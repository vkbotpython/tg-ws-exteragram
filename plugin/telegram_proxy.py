"""Wire the local bridge into exteraGram's MTProto proxy settings.

The exact proxy API differs between Telegram forks and builds, so this module
is deliberately introspective: it tries a set of known class/field/method
names, records what it finds, and always keeps the ``tg://proxy`` deep link as
a reliable fallback that the user can confirm with one tap.

No networking or UI work happens on the caller's thread here.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import elyx_api as sdk

log = logging.getLogger('tg-ws-bridge.proxy')

_CM_CLASSES = (
    'org.telegram.messenger.ConnectionsManager',
)
_SHARED_CLASSES = (
    'org.telegram.messenger.SharedConfig',
)
_ADDR_FIELDS = ('proxyAddress', 'proxyIp', 'proxyHost', 'mtprotoProxyAddress')
_PORT_FIELDS = ('proxyPort', 'mtprotoProxyPort')
_SECRET_FIELDS = ('proxySecret', 'mtprotoProxySecret')
_ENABLED_FIELDS = ('proxyEnabled', 'isProxyEnabled', 'useProxy')

_SETTER_NAMES = ('setProxySettings', 'setProxy', 'setProxyInfo',
                 'setCurrentProxy', 'setMtprotoProxy')


def _java_class(candidates):
    for name in candidates:
        cls = sdk.find_class(name)
        if cls is not None:
            return cls, name
    return None, None


def _instance(cls):
    """Try the usual singleton accessors on a Java class."""
    if cls is None:
        return None
    for name in ('getInstance', 'getInstance0'):
        try:
            fn = getattr(cls, name)
            try:
                return fn()
            except TypeError:
                return fn(0)
        except Exception:
            continue
    return None


def _field_names(obj) -> List[str]:
    if obj is None:
        return []
    try:
        return [str(f.getName()) for f in obj.getDeclaredFields()]
    except Exception:
        return []


def _method_names(obj) -> List[str]:
    if obj is None:
        return []
    try:
        return [str(m.getName()) for m in obj.getDeclaredMethods()]
    except Exception:
        return []


def _read_field(obj, names):
    for name in names:
        try:
            value = sdk.get_private_field(obj, name)
            if value is not None:
                return name, value
        except Exception:
            continue
    return None, None


def _write_field(obj, names, value) -> bool:
    for name in names:
        try:
            if sdk.set_private_field(obj, name, value):
                return True
        except Exception:
            continue
    return False


class ProxyController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._snapshot: Optional[Dict[str, object]] = None
        self._applied = False
        self._last_error: Optional[str] = None
        self._last_method: Optional[str] = None

    # -- introspection -----------------------------------------------------

    def dump(self) -> str:
        """Human-readable report for the debug UI's 'Dump proxy config'."""
        lines: List[str] = []
        cm_cls, cm_name = _java_class(_CM_CLASSES)
        sc_cls, sc_name = _java_class(_SHARED_CLASSES)
        cm = _instance(cm_cls)
        sc = _instance(sc_cls) or sc_cls
        lines.append('ConnectionsManager class: %s' % (cm_name or 'not found'))
        lines.append('SharedConfig class: %s' % (sc_name or 'not found'))
        lines.append('CM fields: %s' % (', '.join(_field_names(cm)) or '-'))
        lines.append('CM proxy setters: %s' % (
            ', '.join(m for m in _method_names(cm) if 'roxy' in m) or '-'))
        lines.append('SC fields: %s' % (', '.join(
            f for f in _field_names(sc) if 'roxy' in f.lower()) or '-'))
        for label, names in (('address', _ADDR_FIELDS),
                             ('port', _PORT_FIELDS),
                             ('secret', _SECRET_FIELDS)):
            holder, value = _read_field(sc, names)
            if holder is None:
                holder, value = _read_field(cm, names)
            lines.append('current %s: %s = %s' % (
                label, holder or '-', '<set>' if value else '-'))
        lines.append('last method: %s' % (self._last_method or '-'))
        lines.append('last error: %s' % (self._last_error or '-'))
        return '\n'.join(lines)

    # -- apply / restore ---------------------------------------------------

    def apply(self, config) -> bool:
        """Try to set the proxy programmatically. Returns False -> use link."""
        self._last_error = None
        cm_cls, _ = _java_class(_CM_CLASSES)
        sc_cls, _ = _java_class(_SHARED_CLASSES)
        if cm_cls is None and sc_cls is None:
            self._last_error = 'ConnectionsManager/SharedConfig not found'
            return False

        cm = _instance(cm_cls)
        sc = _instance(sc_cls) or sc_cls
        self._remember(cm, sc)

        addr = str(config.host)
        if addr == '0.0.0.0':
            addr = '127.0.0.1'
        port = int(config.port)
        secret = str(config.secret)

        # 1) Direct fields on SharedConfig / ConnectionsManager.
        ok_addr = _write_field(sc, _ADDR_FIELDS, addr) or _write_field(cm, _ADDR_FIELDS, addr)
        ok_port = _write_field(sc, _PORT_FIELDS, port) or _write_field(cm, _PORT_FIELDS, port)
        ok_secret = _write_field(sc, _SECRET_FIELDS, secret) or _write_field(cm, _SECRET_FIELDS, secret)
        for name in _ENABLED_FIELDS:
            _write_field(sc, (name,), True)
            _write_field(cm, (name,), True)

        # 2) A setter, if this build exposes one. Try the common signatures.
        setter_ok = False
        for obj in (cm, sc):
            if obj is None or setter_ok:
                continue
            for name in _SETTER_NAMES:
                try:
                    fn = getattr(obj, name)
                except Exception:
                    continue
                candidates = (
                    (addr, port, secret),
                    (addr, port, secret, True),
                    (secret, addr, port),
                    (port, addr, secret),
                )
                for args in candidates:
                    try:
                        fn(*args)
                        setter_ok = True
                        self._last_method = '%s%r' % (name, args[:3])
                        break
                    except Exception:
                        continue
                if setter_ok:
                    break

        if not (ok_addr and ok_port and ok_secret) and not setter_ok:
            self._last_error = 'no usable proxy fields or setter found'
            return False
        self._applied = True
        self._last_method = self._last_method or 'direct fields'
        log.info('MTProto proxy applied via %s', self._last_method)
        return True

    def _remember(self, cm, sc) -> None:
        if self._snapshot is not None:
            return
        snapshot: Dict[str, object] = {}
        for holder in (sc, cm):
            for label, names in (('address', _ADDR_FIELDS),
                                 ('port', _PORT_FIELDS),
                                 ('secret', _SECRET_FIELDS)):
                field, value = _read_field(holder, names)
                if field is not None:
                    snapshot[label] = value
                    snapshot[label + '_field'] = field
        self._snapshot = snapshot or None

    def restore(self) -> bool:
        if not self._snapshot or not self._applied:
            return False
        cm_cls, _ = _java_class(_CM_CLASSES)
        sc_cls, _ = _java_class(_SHARED_CLASSES)
        cm = _instance(cm_cls)
        sc = _instance(sc_cls) or sc_cls
        snapshot = self._snapshot
        for label, names in (('address', _ADDR_FIELDS),
                             ('port', _PORT_FIELDS),
                             ('secret', _SECRET_FIELDS)):
            if label not in snapshot:
                continue
            field = str(snapshot.get(label + '_field') or names[0])
            for holder in (sc, cm):
                try:
                    sdk.set_private_field(holder, field, snapshot[label])
                    break
                except Exception:
                    continue
        self._applied = False
        self._snapshot = None
        log.info('Previous MTProto proxy settings restored')
        return True

    # -- deep link ---------------------------------------------------------

    def open_link(self, link: str) -> bool:
        """Open tg://proxy in the client. Must be called from a UI callback."""
        def _open():
            try:
                from android.content import Intent
                from android.net import Uri
                activity = sdk.get_last_fragment()
                activity = activity.getParentActivity() if activity else sdk.get_context()
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(link))
                if activity is not None:
                    activity.startActivity(intent)
                return True
            except Exception as exc:
                log.warning('cannot open tg://proxy link: %r', exc)
                return False

        try:
            return sdk.run_on_ui_thread(_open)
        except Exception as exc:
            self._last_error = repr(exc)
            return False
