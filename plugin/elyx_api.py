"""Compatibility layer around the exteraGram / AyuGram Elyx SDK.

The SDK is only available inside the client.  Every import here is optional:
on a desktop Python 3.11 host the module installs small inert shims so the
plugin logic (config mapping, runtime lifecycle, log bus) can be unit-tested
without an Android device.

Nothing in this module uses eval/exec - exteraStore's automated review rejects
those patterns.
"""
from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger('tg-ws-bridge.elyx')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import(specs, attr):
    """Return the first successfully imported ``attr`` from ``specs``."""
    for module_name, name in specs:
        try:
            module = __import__(module_name, fromlist=[name])
            value = getattr(module, name)
            if value is not None:
                return value
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# BasePlugin (with a test shim)
# ---------------------------------------------------------------------------

class _FallbackBasePlugin:
    """Minimal stand-in used only outside the client."""

    def __init__(self, *args, **kwargs):
        self.id = getattr(self, 'id', 'tg_ws_bridge')
        self.logger = logging.getLogger('tg-ws-bridge')
        self._settings = {}

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value, **kwargs):
        self._settings[key] = value

    def add_menu_item(self, data):
        return getattr(data, 'item_id', None)

    def remove_menu_item(self, item_id):
        pass

    def run_on_ui_thread(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def get_context(self):
        return None


BasePlugin = _import([
    ('base_plugin', 'BasePlugin'),
    ('elyx', 'BasePlugin'),
], 'BasePlugin') or _FallbackBasePlugin

_IS_DEVICE = BasePlugin is not _FallbackBasePlugin


def is_device() -> bool:
    return _IS_DEVICE


# ---------------------------------------------------------------------------
# Menu helpers (only needed for a debug entry point)
# ---------------------------------------------------------------------------

class _MenuItemType:
    MESSAGE_CONTEXT_MENU = 1
    CHAT_ACTION_MENU = 2
    PROFILE_ACTION_MENU = 3
    MAIN_DRAWER_MENU = 4


MenuItemType = _import([
    ('base_plugin', 'MenuItemType'),
    ('elyx', 'MenuItemType'),
], 'MenuItemType') or _MenuItemType


class _MenuItemData:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


MenuItemData = _import([
    ('base_plugin', 'MenuItemData'),
    ('elyx', 'MenuItemData'),
], 'MenuItemData') or _MenuItemData


# ---------------------------------------------------------------------------
# Settings row types
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _row(name):
    return _import([
        ('ui.settings', name),
        ('elyx.ui', name),
    ], name) or type(name, (_Row,), {})


Header = _row('Header')
Text = _row('Text')
Switch = _row('Switch')
Input = _row('Input')
Selector = _row('Selector')
Divider = _row('Divider')
Custom = _row('Custom')


# ---------------------------------------------------------------------------
# Threading / UI helpers
# ---------------------------------------------------------------------------

def run_on_ui_thread(fn, *args, **kwargs):
    """Execute ``fn`` on Android's main thread (direct call off-device)."""
    helper = _import([
        ('android_utils', 'run_on_ui_thread'),
        ('client_utils', 'run_on_ui_thread'),
        ('elyx', 'run_on_ui_thread'),
    ], 'run_on_ui_thread')
    if helper is not None:
        try:
            return helper(lambda: fn(*args, **kwargs))
        except TypeError:
            return helper(fn, *args, **kwargs)
    # Last resort: post to the main thread via Android's Handler.
    try:
        from android.os import Handler, Looper
        return Handler(Looper.getMainLooper()).post(
            _Runnable(lambda: fn(*args, **kwargs)))
    except Exception:
        return fn(*args, **kwargs)


try:
    from java import dynamic_proxy as _dynamic_proxy
    from java.lang import Runnable as _JavaRunnable

    class _Runnable(_dynamic_proxy(_JavaRunnable)):
        def __init__(self, callback):
            super().__init__()
            self.callback = callback

        def run(self):
            self.callback()
except Exception:  # pragma: no cover - off device
    class _Runnable:  # type: ignore
        def __init__(self, callback):
            self.callback = callback

        def run(self):
            self.callback()


def run_on_queue(fn, queue=None, delay=None):
    helper = _import([('client_utils', 'run_on_queue')], 'run_on_queue')
    if helper is not None:
        try:
            return helper(fn, queue, delay=delay) if delay is not None else helper(fn, queue)
        except TypeError:
            return helper(fn)
    threading.Thread(target=fn, daemon=True).start()


def get_last_fragment():
    return _import([('client_utils', 'get_last_fragment'),
                    ('android_utils', 'get_last_fragment')],
                   'get_last_fragment')()


def get_context():
    fns = [
        _import([('android_utils', 'get_context'),
                 ('client_utils', 'get_application_context')], 'get_context'),
        _import([('client_utils', 'get_application_context')], 'get_application_context'),
    ]
    for fn in fns:
        if fn is not None:
            try:
                ctx = fn()
                if ctx is not None:
                    return ctx
            except Exception:
                pass
    return None


def copy_to_clipboard(text: str) -> bool:
    """Copy text using whatever clipboard helper the SDK exposes."""
    fns = [
        _import([('android_utils', 'copy_to_clipboard'),
                 ('client_utils', 'copy_to_clipboard'),
                 ('elyx', 'copy_to_clipboard')], 'copy_to_clipboard'),
    ]
    for fn in fns:
        if fn is not None:
            try:
                fn(str(text))
                return True
            except Exception:
                continue
    try:
        from android.content import Context
        from android import mActivity
        cm = mActivity.getSystemService(Context.CLIPBOARD_SERVICE)
        from android.content import ClipData
        cm.setPrimaryClip(ClipData.newPlainText('tg_ws_bridge', str(text)))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Notifications / dialogs
# ---------------------------------------------------------------------------

class _BulletinHelper:
    @staticmethod
    def _show(kind, text, **kwargs):
        log.info('[%s] %s', kind, text)

    @classmethod
    def show_success(cls, text, **kwargs):
        cls._show('success', text, **kwargs)

    @classmethod
    def show_error(cls, text, **kwargs):
        cls._show('error', text, **kwargs)

    @classmethod
    def show_info(cls, text, **kwargs):
        cls._show('info', text, **kwargs)


BulletinHelper = _import([('ui.bulletin', 'BulletinHelper'),
                          ('elyx', 'BulletinHelper')],
                         'BulletinHelper') or _BulletinHelper


class AlertDialogBuilder:
    """Thin wrapper that prefers the SDK builder and degrades to a logger."""

    def __init__(self, activity=None):
        self._impl = None
        builder = _import([('ui.alert', 'AlertDialogBuilder'),
                           ('elyx', 'AlertDialogBuilder')],
                          'AlertDialogBuilder')
        if builder is not None:
            try:
                self._impl = builder(activity) if activity is not None else builder()
            except Exception:
                self._impl = None

    def __getattr__(self, name):
        impl = self.__dict__.get('_impl')
        if impl is not None and hasattr(impl, name):
            return getattr(impl, name)
        if name.startswith('set_') or name in ('show', 'dismiss', 'set_items',
                                                'set_title', 'set_message'):
            return lambda *a, **k: log.debug('dialog %s (no SDK builder)', name)
        raise AttributeError(name)

    # SDK snippets use snake_case; a few builds use camelCase.
    def set_title(self, value):
        return self.__getattr__('set_title')(value)

    def set_message(self, value):
        return self.__getattr__('set_message')(value)

    def set_positive_button(self, label, callback):
        return self.__getattr__('set_positive_button')(label, callback)

    def set_negative_button(self, label, callback):
        return self.__getattr__('set_negative_button')(label, callback)

    def set_items(self, items, callback):
        return self.__getattr__('set_items')(items, callback)

    def set_cancelable(self, value):
        return self.__getattr__('set_cancelable')(value)

    def show(self):
        return self.__getattr__('show')()


# ---------------------------------------------------------------------------
# Reflection helpers
# ---------------------------------------------------------------------------

hook_utils = None
try:
    import hook_utils as _hook_utils  # type: ignore
    hook_utils = _hook_utils
except Exception:
    pass


def find_class(name):
    if hook_utils is not None:
        try:
            return hook_utils.find_class(name)
        except Exception:
            pass
    try:
        from jnius import autoclass  # type: ignore
        return autoclass(name)
    except Exception:
        return None


def get_private_field(obj, name):
    if hook_utils is not None:
        return hook_utils.get_private_field(obj, name)
    try:
        return getattr(obj, name)
    except Exception:
        return None


def set_private_field(obj, name, value):
    if hook_utils is not None:
        return hook_utils.set_private_field(obj, name, value)
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------

def t(key: str, default: str = '') -> str:
    try:
        from elyx import strings  # type: ignore
        value = strings.get(key)
        if value:
            return str(value)
    except Exception:
        pass
    return default or key
