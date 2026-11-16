"""
AES-CTR shim shared by the proxy core and the Elyx plugin.

The desktop project used `cryptography` (with a ctypes OpenSSL fallback).
On Android/Chaquopy neither is guaranteed, so this module exposes the same
tiny public surface::

    Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(data)

Backend priority (overridable with TGWS_AES_BACKEND=java|cryptography|pyaes|openssl):

  1. java          - javax.crypto.Cipher, registered by plugin/crypto_backend.py
  2. cryptography  - available on desktop / in the ported test suite
  3. pyaes         - vendored pure-Python AES (always present in the .elyx)
  4. openssl       - ctypes wrapper over system libcrypto (last resort)

All backends implement a 128-bit big-endian counter, matching
`cryptography.hazmat.primitives.ciphers.modes.CTR` semantics used by the
original handshake code.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os

try:
    from cryptography.hazmat.primitives.ciphers import (  # noqa: F401
        Cipher as _CryptoCipher, algorithms as _crypto_algorithms,
        modes as _crypto_modes,
    )
except ImportError:  # pragma: no cover - depends on the runtime
    _CryptoCipher = None
    _crypto_algorithms = None
    _crypto_modes = None

try:
    from vendor.pyaes import AES as _PyAES
except ImportError:  # pragma: no cover
    try:
        from ._pyaes import AES as _PyAES  # type: ignore
    except ImportError:
        _PyAES = None


# ---------------------------------------------------------------------------
# Backend registration / selection
# ---------------------------------------------------------------------------

_JAVA_STREAM_FACTORY = None  # factory(key: bytes, iv: bytes) -> stream.update(data)
_FORCED_BACKEND = os.environ.get('TGWS_AES_BACKEND', '').strip().lower() or None


def register_java_backend(factory) -> None:
    """Register a Java JCE backed CTR factory supplied by the plugin layer.

    ``factory(key, iv)`` must return an object with ``update(bytes) -> bytes``.
    When registered, this backend is preferred on Android.
    """
    global _JAVA_STREAM_FACTORY
    _JAVA_STREAM_FACTORY = factory


def set_backend(name: str | None) -> None:
    """Force a backend (used by tests and by the plugin debug settings)."""
    global _FORCED_BACKEND
    _FORCED_BACKEND = (name or None) and name.strip().lower()


def active_backend() -> str:
    if _FORCED_BACKEND in ('java', 'cryptography', 'pyaes', 'openssl'):
        return _FORCED_BACKEND
    if _JAVA_STREAM_FACTORY is not None:
        return 'java'
    if _CryptoCipher is not None:
        return 'cryptography'
    if _PyAES is not None:
        return 'pyaes'
    return 'openssl'


# ---------------------------------------------------------------------------
# Public algorithm/mode descriptors (API compatible with cryptography)
# ---------------------------------------------------------------------------

class algorithms:
    class AES:
        __slots__ = ('key',)

        def __init__(self, key: bytes):
            if len(key) not in (16, 24, 32):
                raise ValueError("AES key must be 16/24/32 bytes")
            self.key = bytes(key)


class modes:
    class CTR:
        __slots__ = ('iv',)

        def __init__(self, iv: bytes):
            if len(iv) != 16:
                raise ValueError("CTR IV must be 16 bytes")
            self.iv = bytes(iv)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _CryptoStream:
    __slots__ = ('_s',)

    def __init__(self, key: bytes, iv: bytes):
        self._s = _CryptoCipher(_crypto_algorithms.AES(key),
                                _crypto_modes.CTR(iv)).encryptor()

    def update(self, data: bytes) -> bytes:
        return self._s.update(data)


class _PyCtrStream:
    """CTR built on the vendored pure-Python AES block cipher."""

    __slots__ = ('_aes', '_counter', '_keystream', '_offset')

    def __init__(self, key: bytes, iv: bytes):
        self._aes = _PyAES(key)
        self._counter = int.from_bytes(iv, 'big')
        self._keystream = b''
        self._offset = 0

    def update(self, data: bytes) -> bytes:
        if not data:
            return b''
        out = bytearray(len(data))
        pos = 0
        while pos < len(data):
            if self._offset >= len(self._keystream):
                block = self._counter.to_bytes(16, 'big')
                self._keystream = self._aes.encrypt(block)
                self._counter = (self._counter + 1) & ((1 << 128) - 1)
                self._offset = 0
            n = min(len(data) - pos, len(self._keystream) - self._offset)
            ks = self._keystream
            off = self._offset
            chunk = data[pos:pos + n]
            out[pos:pos + n] = bytes(c ^ ks[off + i] for i, c in enumerate(chunk))
            self._offset += n
            pos += n
        return bytes(out)


def _load_libcrypto():
    name = ctypes.util.find_library("crypto")
    candidates = []
    if name:
        candidates.append(name)
    candidates += [
        "libcrypto.so.3", "libcrypto.so.1.1", "libcrypto.so.1.0.0",
        "libcrypto.so", "libc.so", "/opt/lib/libcrypto.so",
        "/opt/lib/libcrypto.so.1.1", "/opt/lib/libcrypto.so.3",
    ]
    last_err = None
    for c in candidates:
        try:
            return ctypes.CDLL(c)
        except OSError as e:
            last_err = e
    raise RuntimeError(
        "libcrypto not found; last error: %r" % last_err
    )


_LIBCRYPTO = None
_EVP_BY_KEY = None


def _init_libcrypto():
    global _LIBCRYPTO, _EVP_BY_KEY
    if _LIBCRYPTO is not None:
        return
    lib = _load_libcrypto()
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_aes_128_ctr.restype = ctypes.c_void_p
    lib.EVP_aes_192_ctr.restype = ctypes.c_void_p
    lib.EVP_aes_256_ctr.restype = ctypes.c_void_p
    lib.EVP_EncryptInit_ex.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_char_p, ctypes.c_char_p,
    ]
    lib.EVP_EncryptInit_ex.restype = ctypes.c_int
    lib.EVP_EncryptUpdate.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
        ctypes.c_char_p, ctypes.c_int,
    ]
    lib.EVP_EncryptUpdate.restype = ctypes.c_int
    _EVP_BY_KEY = {
        16: lib.EVP_aes_128_ctr,
        24: lib.EVP_aes_192_ctr,
        32: lib.EVP_aes_256_ctr,
    }
    _LIBCRYPTO = lib


class _OpenSslCtrStream:
    __slots__ = ('_ctx',)

    def __init__(self, key: bytes, iv: bytes):
        _init_libcrypto()
        ctx = _LIBCRYPTO.EVP_CIPHER_CTX_new()
        if not ctx:
            raise RuntimeError("EVP_CIPHER_CTX_new failed")
        self._ctx = ctx
        evp = _EVP_BY_KEY[len(key)]()
        if _LIBCRYPTO.EVP_EncryptInit_ex(ctx, evp, None, key, iv) != 1:
            _LIBCRYPTO.EVP_CIPHER_CTX_free(ctx)
            self._ctx = None
            raise RuntimeError("EVP_EncryptInit_ex failed")

    def update(self, data: bytes) -> bytes:
        if not data:
            return b''
        outlen = ctypes.c_int(0)
        buf = ctypes.create_string_buffer(len(data) + 16)
        if _LIBCRYPTO.EVP_EncryptUpdate(
                self._ctx, buf, ctypes.byref(outlen), bytes(data), len(data)
        ) != 1:
            raise RuntimeError("EVP_EncryptUpdate failed")
        return buf.raw[:outlen.value]

    def __del__(self):
        ctx = getattr(self, '_ctx', None)
        if ctx and _LIBCRYPTO is not None:
            try:
                _LIBCRYPTO.EVP_CIPHER_CTX_free(ctx)
            except Exception:
                pass
            self._ctx = None


class _JavaCtrStream:
    __slots__ = ('_stream',)

    def __init__(self, key: bytes, iv: bytes):
        if _JAVA_STREAM_FACTORY is None:
            raise RuntimeError("Java AES backend is not registered")
        self._stream = _JAVA_STREAM_FACTORY(key, iv)

    def update(self, data: bytes) -> bytes:
        return self._stream.update(data)


_STREAM_BY_BACKEND = {
    'java': _JavaCtrStream,
    'cryptography': _CryptoStream,
    'pyaes': _PyCtrStream,
    'openssl': _OpenSslCtrStream,
}


class Cipher:
    __slots__ = ('_key', '_iv')

    def __init__(self, algorithm, mode):
        if not isinstance(algorithm, algorithms.AES):
            raise TypeError("only AES is supported")
        if not isinstance(mode, modes.CTR):
            raise TypeError("only CTR mode is supported")
        self._key = algorithm.key
        self._iv = mode.iv

    def _new_stream(self):
        backend = active_backend()
        stream_cls = _STREAM_BY_BACKEND.get(backend)
        if stream_cls is None:
            raise RuntimeError("Unknown AES backend: %r" % backend)
        return stream_cls(self._key, self._iv)

    def encryptor(self):
        return self._new_stream()

    # CTR is symmetric - decryption is encryption with the same keystream.
    decryptor = encryptor


__all__ = [
    'Cipher', 'algorithms', 'modes', 'register_java_backend',
    'set_backend', 'active_backend',
]
