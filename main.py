# -*- coding: utf-8 -*-
"""tg_ws_bridge - local MTProto WebSocket bridge for exteraGram / AyuGram.

Port of Flowseal's tg-ws-proxy (MIT) into an Elyx plugin. The proxy engine
runs on a private asyncio loop in a background thread; all client-facing work
(settings, proxy activation, debug UI) stays on the SDK's threads.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import List

# The Elyx loader may import main.py from a different CWD; make the sibling
# packages (plugin/, wsproxy/, vendor/) resolvable explicitly.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugin import elyx_api as sdk
from plugin import config_bridge, crypto_backend, debug_ui
from plugin.logbus import get_bus, reset_bus
from plugin.runtime import get_runtime, release_runtime
from plugin.telegram_proxy import ProxyController

BasePlugin = sdk.BasePlugin
log = logging.getLogger('tg-ws-bridge.plugin')


class _StatusTimer:
    """Optional periodic status snapshot; opt-in via 'status_autorefresh'."""

    def __init__(self, plugin, runtime, interval: float = 5.0):
        self.plugin = plugin
        self.runtime = runtime
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='tg-ws-bridge-status')
        self._thread.start()

    def _run(self):
        from plugin.debug_ui import status_text
        while not self._stop.wait(self.interval):
            try:
                if not config_bridge.setting(self.plugin, 'status_autorefresh'):
                    continue
                text = status_text(self.runtime.status())
                try:
                    self.plugin.set_setting('status_snapshot', text,
                                            reload_settings=False)
                except TypeError:
                    self.plugin.set_setting('status_snapshot', text)
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class TgWsBridgePlugin(BasePlugin):
    """Main plugin class discovered by the Elyx loader."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.id = getattr(self, 'id', 'tg_ws_bridge') or 'tg_ws_bridge'
        self.logger = getattr(self, 'logger', None) or log
        self.runtime = get_runtime(self.id)
        self.controller = ProxyController(self)
        self.bus = None
        self._timer = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_plugin_load(self) -> None:
        try:
            crypto_backend.install_java_backend()
            capacity = config_bridge._as_int(
                config_bridge.setting(self, 'log_buffer'), 500)
            level = str(config_bridge.setting(self, 'log_level') or 'INFO')
            self.bus = get_bus(capacity=capacity, min_level=level)
            self.bus.install()

            cfg, errors = config_bridge.apply_config(self)
            for err in errors:
                self.logger.warning('config: %s', err)
            self.logger.info('config applied: %s', config_bridge.describe(cfg))
            self.logger.info('connect link: %s', config_bridge.safe_link(cfg))

            if config_bridge.setting(self, 'enabled'):
                if self.runtime.start():
                    self.logger.info('bridge started')
                    if config_bridge.setting(self, 'auto_apply_proxy'):
                        self._apply_proxy_async(cfg)
                else:
                    self.logger.error('bridge failed to start')
                    debug_ui.notify('Bridge failed to start', 'error')

            self._timer = _StatusTimer(self, self.runtime)
            self._timer.start()
        except Exception as exc:
            self.logger.exception('on_plugin_load failed: %s', exc)
            debug_ui.notify('tg_ws_bridge load error: %s' % exc, 'error')

    def on_plugin_unload(self) -> None:
        # Everything must be undone here: Elyx reloads in-process and leaves
        # dead threads / occupied ports otherwise.
        try:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            if config_bridge.setting(self, 'restore_proxy_on_unload'):
                try:
                    self.controller.restore()
                except Exception:
                    pass
            if self.runtime is not None:
                self.runtime.stop()
            if self.bus is not None:
                self.bus.close()
            reset_bus()
            release_runtime(self.id)
        except Exception as exc:
            try:
                self.logger.exception('on_plugin_unload failed: %s', exc)
            except Exception:
                pass

    def on_app_event(self, event) -> None:
        """START / STOP / PAUSE / RESUME from the client lifecycle."""
        try:
            name = getattr(event, 'name', None) or str(event)
            name = str(name).upper()
        except Exception:
            return
        enabled = bool(config_bridge.setting(self, 'enabled'))
        if 'STOP' in name and 'PAUSE' not in name:
            self.runtime.stop()
        elif ('START' in name or 'RESUME' in name) and enabled:
            if not self.runtime.is_running():
                config_bridge.apply_config(self)
                self.runtime.start()

    def on_settings_change(self, key: str, value) -> None:
        """React to setting changes without forcing a full settings reload."""
        if key in ('listen_port', 'listen_host', 'secret', 'pool_size',
                   'buf_kb', 'dc_ips', 'fake_tls_domain', 'force_test_dc',
                   'proxy_protocol', 'fallback_cfproxy', 'cfproxy_domains',
                   'cfproxy_worker_domains', 'h2_enabled', 'disable_secure',
                   'offline'):
            if self.runtime.is_running() and config_bridge.setting(self, 'enabled'):
                self._restart_async()
        elif key == 'enabled':
            if value and not self.runtime.is_running():
                config_bridge.apply_config(self)
                self.runtime.start()
            elif not value:
                self.runtime.stop()
        elif key == 'log_level':
            if self.bus is not None:
                self.bus.set_level(str(value))
        elif key == 'log_buffer':
            if self.bus is not None:
                self.bus.set_capacity(config_bridge._as_int(value, 500))

    # ------------------------------------------------------------------
    # Proxy integration
    # ------------------------------------------------------------------

    def _apply_proxy_async(self, cfg=None):
        def _work():
            config = cfg or config_bridge.proxy_config
            ok = False
            try:
                ok = self.controller.apply(config)
            except Exception as exc:
                self.logger.warning('proxy reflection failed: %r', exc)
            if not ok:
                link = config_bridge.build_deeplink(config)
                self.logger.info('opening deeplink fallback: %s',
                                 config_bridge.safe_link(config))
                debug_ui.notify('Confirm the proxy connection in the dialog',
                                'info')
                self.controller.open_link(link)

        sdk.run_on_queue(_work)

    def _restart_async(self):
        def _work():
            cfg, errors = config_bridge.apply_config(self)
            for err in errors:
                self.logger.warning('config: %s', err)
            self.runtime.restart()
            self.logger.info('bridge restarted: %s', config_bridge.describe(cfg))

        sdk.run_on_queue(_work)

    # ------------------------------------------------------------------
    # Settings screen
    # ------------------------------------------------------------------

    def create_settings(self) -> List[object]:
        s = sdk
        regen = lambda b, w: self._regenerate_secret()
        start = lambda b, w: self._action_start()
        stop = lambda b, w: self._action_stop()
        restart = lambda b, w: self._action_restart()
        open_link = lambda b, w: self._action_open_link()
        copy_link = lambda b, w: self._action_copy_link()
        show_logs = lambda b, w: self._action_logs()
        show_status = lambda b, w: self._action_status()
        dump = lambda b, w: self._action_dump()
        default_secret = config_bridge.ensure_secret(self)

        return [
            s.Header(text='tg_ws_bridge - WebSocket MTProto bridge'),
            s.Switch(key='enabled', text='Enabled',
                     subtext='Run the local proxy on app start',
                     default=config_bridge.setting(self, 'enabled')),
            s.Input(key='listen_host', text='Listen host',
                    default=config_bridge.setting(self, 'listen_host')),
            s.Input(key='listen_port', text='Listen port',
                    default=config_bridge.setting(self, 'listen_port')),
            s.Input(key='secret', text='Secret (32 hex)',
                    subtext='MTProxy secret; keep it private',
                    default=default_secret),
            s.Text(text='Generate a new secret',
                   subtext='Invalidates the current tg://proxy link',
                   on_click=regen),
            s.Text(text='Start / Stop / Restart',
                   subtext='Control the bridge without reloading the plugin',
                   on_click=self._action_controls),
            s.Divider(),

            s.Header(text='Transport'),
            s.Input(key='pool_size', text='WS pool size per DC',
                    default=config_bridge.setting(self, 'pool_size')),
            s.Input(key='buf_kb', text='Socket buffer (KiB)',
                    default=config_bridge.setting(self, 'buf_kb')),
            s.Input(key='dc_ips', text='DC redirects (DC:IP, ...)',
                    default=config_bridge.setting(self, 'dc_ips')),
            s.Input(key='fake_tls_domain', text='Fake TLS domain (optional)',
                    subtext='Enables ee-secret masking',
                    default=config_bridge.setting(self, 'fake_tls_domain')),
            s.Switch(key='force_test_dc', text='Force test DC',
                     default=config_bridge.setting(self, 'force_test_dc')),
            s.Switch(key='proxy_protocol', text='Accept PROXY protocol v1',
                     default=config_bridge.setting(self, 'proxy_protocol')),
            s.Divider(),

            s.Header(text='Fallbacks'),
            s.Switch(key='fallback_cfproxy', text='Cloudflare proxy fallback',
                     default=config_bridge.setting(self, 'fallback_cfproxy')),
            s.Input(key='cfproxy_domains', text='CF proxy domains (CSV)',
                    default=config_bridge.setting(self, 'cfproxy_domains')),
            s.Input(key='cfproxy_worker_domains', text='CF Worker domains (CSV)',
                    default=config_bridge.setting(self, 'cfproxy_worker_domains')),
            s.Switch(key='h2_enabled', text='HTTP/2 media (CF)',
                     default=config_bridge.setting(self, 'h2_enabled')),
            s.Switch(key='disable_secure', text='Use port 80 (no TLS)',
                     default=config_bridge.setting(self, 'disable_secure')),
            s.Switch(key='offline', text='Offline mode',
                     subtext='Use bundled CF domains, no GitHub refresh',
                     default=config_bridge.setting(self, 'offline')),
            s.Divider(),

            s.Header(text='Connection'),
            s.Switch(key='auto_apply_proxy', text='Apply proxy automatically',
                     default=config_bridge.setting(self, 'auto_apply_proxy')),
            s.Switch(key='restore_proxy_on_unload',
                     text='Restore previous proxy on unload',
                     default=config_bridge.setting(self, 'restore_proxy_on_unload')),
            s.Text(text='Open tg://proxy',
                   subtext='Fallback when reflection cannot set the proxy',
                   on_click=open_link),
            s.Text(text='Copy tg://proxy link', on_click=copy_link),
            s.Divider(),

            s.Header(text='Debug (no adb)'),
            s.Selector(key='log_level', text='Log level',
                       items=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       default=config_bridge.setting(self, 'log_level')),
            s.Input(key='log_buffer', text='Log buffer size',
                    default=config_bridge.setting(self, 'log_buffer')),
            s.Switch(key='status_autorefresh', text='Auto-refresh status',
                     default=config_bridge.setting(self, 'status_autorefresh', False)),
            s.Text(text='Logs', on_click=show_logs),
            s.Text(text='Status and statistics', on_click=show_status),
            s.Text(text='Dump proxy configuration', on_click=dump),
        ]

    # ------------------------------------------------------------------
    # Setting actions
    # ------------------------------------------------------------------

    def _regenerate_secret(self):
        value = config_bridge.new_secret()
        try:
            self.set_setting('secret', value)
        except Exception:
            pass
        self._restart_async()
        debug_ui.notify('New secret generated', 'success')

    def _action_start(self):
        if not self.runtime.is_running():
            cfg, errors = config_bridge.apply_config(self)
            self.runtime.start()
            if config_bridge.setting(self, 'auto_apply_proxy'):
                self._apply_proxy_async(cfg)
        debug_ui.notify('Bridge started' if self.runtime.is_running()
                        else 'Bridge failed to start',
                        'success' if self.runtime.is_running() else 'error')

    def _action_stop(self):
        self.runtime.stop()
        debug_ui.notify('Bridge stopped', 'info')

    def _action_restart(self):
        self._restart_async()
        debug_ui.notify('Bridge restarting...', 'info')

    def _action_controls(self):
        def _show():
            builder = sdk.AlertDialogBuilder(sdk.get_last_fragment()
                                              and sdk.get_last_fragment().getParentActivity())
            builder.set_title('Bridge control')
            builder.set_items(
                ['Start', 'Stop', 'Restart'],
                lambda b, i: (self._action_start(), self._action_stop(),
                              self._action_restart())[i] if i < 3 else None)
            builder.set_negative_button('Close', lambda b, w: b.dismiss())
            builder.show()
        sdk.run_on_ui_thread(_show)

    def _action_open_link(self):
        cfg, _ = config_bridge.apply_config(self)
        self.controller.open_link(config_bridge.build_deeplink(cfg))

    def _action_copy_link(self):
        cfg, _ = config_bridge.apply_config(self)
        link = config_bridge.build_deeplink(cfg)
        sdk.copy_to_clipboard(link)
        debug_ui.notify('Link copied', 'success')

    def _action_logs(self):
        if self.bus is not None:
            debug_ui.show_logs(
                self, self.bus,
                limit=config_bridge._as_int(
                    config_bridge.setting(self, 'log_buffer'), 500),
                level=str(config_bridge.setting(self, 'log_level') or 'INFO'))

    def _action_status(self):
        debug_ui.show_status(self, self.runtime, self.controller,
                             refresh=lambda: config_bridge.apply_config(self))

    def _action_dump(self):
        text = self.controller.dump()
        def _show():
            builder = sdk.AlertDialogBuilder(
                sdk.get_last_fragment() and sdk.get_last_fragment().getParentActivity())
            builder.set_title('Proxy configuration dump')
            builder.set_message(text[:6000])
            builder.set_negative_button('Close', lambda b, w: b.dismiss())
            builder.show()
        sdk.run_on_ui_thread(_show)
