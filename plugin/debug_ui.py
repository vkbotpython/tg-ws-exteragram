"""Debug screens: log viewer and engine status/statistics.

Everything here is UI-only and must be entered through ``run_on_ui_thread``.
The text is built on the calling thread so the UI thread only creates views.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import elyx_api as sdk

log = logging.getLogger('tg-ws-bridge.ui')

_MAX_DIALOG_CHARS = 6000


def notify(text: str, kind: str = 'info') -> None:
    fn = {
        'success': sdk.BulletinHelper.show_success,
        'error': sdk.BulletinHelper.show_error,
    }.get(kind, sdk.BulletinHelper.show_info)
    try:
        fn(str(text))
    except Exception:
        log.info('%s: %s', kind, text)


def _activity():
    fragment = sdk.get_last_fragment()
    if fragment is not None:
        try:
            activity = fragment.getParentActivity()
            if activity is not None:
                return activity
        except Exception:
            pass
    return sdk.get_context()


def _text_view(text: str, monospace: bool = True):
    """Build a scrollable TextView; returns None off-device."""
    try:
        from android.widget import TextView, ScrollView
        from android.view import ViewGroup
        from android.util import TypedValue
        from android.graphics import Color

        tv = TextView(_activity())
        tv.setText(text)
        tv.setTextSize(TypedValue.COMPLEX_UNIT_DIP, 11)
        if monospace:
            try:
                from android.graphics import Typeface
                tv.setTypeface(Typeface.MONOSPACE)
            except Exception:
                pass
        try:
            tv.setTextColor(Color.rgb(220, 220, 220))
        except Exception:
            pass
        scroll = ScrollView(_activity())
        scroll.addView(tv, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT))
        try:
            scroll.setBackgroundColor(Color.rgb(24, 24, 26))
        except Exception:
            pass
        return scroll
    except Exception as exc:
        log.debug('cannot build native TextView (%r)', exc)
        return None


def _builder(title: str, body: str):
    builder = sdk.AlertDialogBuilder(_activity())
    try:
        builder.set_title(title)
    except Exception:
        pass
    view = _text_view(body)
    if view is not None:
        try:
            builder.set_view(view)
        except Exception:
            builder.set_message(body[:_MAX_DIALOG_CHARS])
    else:
        try:
            builder.set_message(body[:_MAX_DIALOG_CHARS])
        except Exception:
            pass
    return builder


def show_logs(plugin, bus, limit: int = 300, level: str = 'INFO') -> None:
    """Show the ring buffer with refresh / copy / clear / level controls."""
    def _render():
        minimum = getattr(logging, level.upper(), logging.INFO)
        body = bus.text(limit=limit, min_level=minimum) or '(no log records)'
        builder = _builder('Bridge logs (%s, last %d)' % (level, limit), body)

        def _refresh(b, _w):
            try:
                b.dismiss()
            except Exception:
                pass
            sdk.run_on_ui_thread(lambda: show_logs(plugin, bus, limit, level))

        def _copy(b, _w):
            sdk.copy_to_clipboard(body)
            try:
                b.dismiss()
            except Exception:
                pass
            notify('Logs copied to clipboard', 'success')

        def _clear(b, _w):
            bus.clear()
            try:
                b.dismiss()
            except Exception:
                pass
            sdk.run_on_ui_thread(lambda: show_logs(plugin, bus, limit, level))

        def _toggle(b, _w):
            next_level = 'DEBUG' if level.upper() != 'DEBUG' else 'INFO'
            try:
                b.dismiss()
            except Exception:
                pass
            try:
                plugin.set_setting('log_level', next_level)
            except Exception:
                pass
            bus.set_level(next_level)
            sdk.run_on_ui_thread(lambda: show_logs(plugin, bus, limit, next_level))

        builder.set_positive_button('Refresh', _refresh)
        builder.set_negative_button('Close', lambda b, _w: b.dismiss())
        builder.set_neutral_button('Copy', _copy) if hasattr(builder, 'set_neutral_button') else None
        builder.set_items(['Clear logs', 'Toggle DEBUG/INFO'],
                          lambda b, i: (_clear(b, i) if i == 0 else _toggle(b, i)))
        builder.set_cancelable(True)
        builder.show()

    sdk.run_on_ui_thread(_render)


def status_text(status: dict) -> str:
    def hb(n):
        for unit in ('B', 'KB', 'MB', 'GB'):
            if abs(n) < 1024:
                return '%.1f%s' % (n, unit)
            n /= 1024
        return '%.1fTB' % n

    pool_total = status['pool_hits'] + status['pool_misses']
    cf_total = status['cf_pool_hits'] + status['cf_pool_misses']
    return '\n'.join([
        'Engine:        %s' % ('RUNNING' if status['running'] else 'STOPPED'),
        'Uptime:        %.1fs' % status['uptime'],
        '',
        'Connections:   total=%d active=%d' % (
            status['connections_total'], status['connections_active']),
        'Routes:        ws=%d h2=%d tcp_fb=%d cf=%d front=%d masked=%d' % (
            status['connections_ws'], status['connections_h2'],
            status['connections_tcp_fallback'], status['connections_cfproxy'],
            status['connections_fronting'], status['connections_masked']),
        'Bad handshakes:%d   WS errors: %d' % (
            status['connections_bad'], status['ws_errors']),
        'Traffic:       up=%s down=%s' % (
            hb(status['bytes_up']), hb(status['bytes_down'])),
        'WS pool hits:  %s' % (
            '%d/%d' % (status['pool_hits'], pool_total) if pool_total else 'n/a'),
        'CF pool hits:  %s' % (
            '%d/%d' % (status['cf_pool_hits'], cf_total) if cf_total else 'n/a'),
        '',
        'Last error:    %s' % status['last_error'],
        '',
        status['summary'],
    ])


def show_status(plugin, runtime, controller=None, refresh=None) -> None:
    status = runtime.status()
    body = status_text(status)

    def _render():
        builder = _builder('Bridge status & statistics', body)

        def _refresh(b, _w):
            try:
                b.dismiss()
            except Exception:
                pass
            if refresh is not None:
                refresh()
            sdk.run_on_ui_thread(lambda: show_status(
                plugin, runtime, controller, refresh))

        def _copy(b, _w):
            sdk.copy_to_clipboard(body)
            try:
                b.dismiss()
            except Exception:
                pass
            notify('Status copied to clipboard', 'success')

        def _dump(b, _w):
            text = controller.dump() if controller is not None else 'no controller'
            try:
                b.dismiss()
            except Exception:
                pass
            def _show_dump():
                dump_builder = _builder('Proxy configuration dump', text)
                dump_builder.set_negative_button('Close',
                                                 lambda bb, _w: bb.dismiss())
                dump_builder.show()
            sdk.run_on_ui_thread(_show_dump)

        builder.set_positive_button('Refresh', _refresh)
        builder.set_negative_button('Close', lambda b, _w: b.dismiss())
        if hasattr(builder, 'set_neutral_button'):
            builder.set_neutral_button('Copy', _copy)
        builder.set_items(['Dump proxy config'], lambda b, i: _dump(b, i))
        builder.set_cancelable(True)
        builder.show()

    sdk.run_on_ui_thread(_render)
