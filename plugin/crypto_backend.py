"""AES backend selection for the proxy core.

On Android the preferred CTR implementation is Java JCE (fast, hardware
accelerated on most devices).  If JCE cannot be initialised the core falls back
to `cryptography` or the vendored pure-Python AES automatically.
"""
from __future__ import annotations

import logging

from wsproxy import _aes

log = logging.getLogger('tg-ws-bridge.crypto')


def _to_bytes(value) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    try:
        return bytes(bytearray(value))
    except Exception:
        return bytes(value)


class _JavaCtrStream:
    __slots__ = ('_cipher',)

    def __init__(self, cipher):
        self._cipher = cipher

    def update(self, data: bytes) -> bytes:
        if not data:
            return b''
        return _to_bytes(self._cipher.doUpdate(bytearray(data)))


def install_java_backend() -> bool:
    """Register javax.crypto as the AES-CTR provider. Returns True on success."""
    try:
        from javax.crypto import Cipher as JCipher  # type: ignore
        from javax.crypto.spec import IvParameterSpec  # type: ignore
        from javax.crypto.spec import SecretKeySpec  # type: ignore
    except Exception as exc:
        log.debug('Java JCE unavailable (%r); using fallback backend', exc)
        return False

    def factory(key: bytes, iv: bytes):
        cipher = JCipher.getInstance('AES/CTR/NoPadding')
        key_spec = SecretKeySpec(bytearray(key), 'AES')
        iv_spec = IvParameterSpec(bytearray(iv))
        cipher.init(JCipher.ENCRYPT_MODE, key_spec, iv_spec)
        return _JavaCtrStream(cipher)

    try:
        # Fail fast if JCE is present but refuses the transform.
        probe = factory(b'\x00' * 16, b'\x00' * 16)
        probe.update(b'\x00' * 16)
    except Exception as exc:
        log.warning('Java JCE AES/CTR probe failed (%r); using fallback', exc)
        return False

    _aes.register_java_backend(factory)
    log.info('AES backend: Java JCE (AES/CTR/NoPadding)')
    return True


def backend_name() -> str:
    return _aes.active_backend()


def force(name: str) -> None:
    _aes.set_backend(name)
