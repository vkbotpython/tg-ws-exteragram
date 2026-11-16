"""Minimal, dependency-free AES block cipher (pyaes-compatible surface).

This is used only when neither Java JCE nor `cryptography` is available.  It
implements AES-128/192/256 encryption and decryption; CTR is built on top in
`wsproxy._aes` so the counter increment is guaranteed to be big-endian,
exactly like `cryptography`'s CTR mode.

The implementation is intentionally small and table-free: S-boxes are derived
at import time from the GF(2^8) inverse, so there are no large constants that
could be mistyped.
"""
from __future__ import annotations

__all__ = ['AES', 'new', 'MODE_ECB', 'MODE_CTR']


def _gf_mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gf_inv(a: int) -> int:
    if a == 0:
        return 0
    result, base, exp = 1, a, 254
    while exp:
        if exp & 1:
            result = _gf_mul(result, base)
        base = _gf_mul(base, base)
        exp >>= 1
    return result


def _build_tables():
    sbox = []
    for i in range(256):
        inv = _gf_inv(i)
        s = inv
        for _ in range(4):
            s = ((s << 1) | (s >> 7)) & 0xFF
            s ^= inv
        sbox.append(s ^ 0x63)
    inv_sbox = [0] * 256
    for i, v in enumerate(sbox):
        inv_sbox[v] = i
    rcon = [0]  # index 0 unused; round 1 uses 0x01
    value = 1
    for _ in range(14):
        rcon.append(value)
        value = _gf_mul(value, 2)
    return sbox, inv_sbox, rcon


_SBOX, _INV_SBOX, _RCON = _build_tables()

MODE_ECB = 1
MODE_CTR = 6


def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _expand_key(key: bytes):
    nk = len(key) // 4
    nr = nk + 6
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [bytes(b for word in words[4 * r:4 * r + 4] for b in word)
            for r in range(nr + 1)]


def _add_round_key(state, rk):
    for i in range(16):
        state[i] ^= rk[i]


def _shift_rows(state):
    old = state[:]
    for r in range(4):
        for c in range(4):
            state[4 * c + r] = old[4 * ((c + r) % 4) + r]


def _inv_shift_rows(state):
    old = state[:]
    for r in range(4):
        for c in range(4):
            state[4 * c + r] = old[4 * ((c - r) % 4) + r]


def _mix_columns(state):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = state[i:i + 4]
        t = a0 ^ a1 ^ a2 ^ a3
        state[i] = a0 ^ t ^ _xtime(a0 ^ a1)
        state[i + 1] = a1 ^ t ^ _xtime(a1 ^ a2)
        state[i + 2] = a2 ^ t ^ _xtime(a2 ^ a3)
        state[i + 3] = a3 ^ t ^ _xtime(a3 ^ a0)


def _inv_mix_columns(state):
    for c in range(4):
        i = 4 * c
        a0, a1, a2, a3 = state[i:i + 4]
        state[i] = _gf_mul(a0, 14) ^ _gf_mul(a1, 11) ^ _gf_mul(a2, 13) ^ _gf_mul(a3, 9)
        state[i + 1] = _gf_mul(a0, 9) ^ _gf_mul(a1, 14) ^ _gf_mul(a2, 11) ^ _gf_mul(a3, 13)
        state[i + 2] = _gf_mul(a0, 13) ^ _gf_mul(a1, 9) ^ _gf_mul(a2, 14) ^ _gf_mul(a3, 11)
        state[i + 3] = _gf_mul(a0, 11) ^ _gf_mul(a1, 13) ^ _gf_mul(a2, 9) ^ _gf_mul(a3, 14)


class AES:
    """AES block cipher. ``encrypt``/``decrypt`` consume exactly 16 bytes."""

    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ValueError("AES key must be 16/24/32 bytes")
        self._round_keys = _expand_key(bytes(key))
        self._rounds = len(self._round_keys) - 1

    def encrypt(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        state = list(block)
        rk = self._round_keys
        _add_round_key(state, rk[0])
        for r in range(1, self._rounds):
            state = [_SBOX[b] for b in state]
            _shift_rows(state)
            _mix_columns(state)
            _add_round_key(state, rk[r])
        state = [_SBOX[b] for b in state]
        _shift_rows(state)
        _add_round_key(state, rk[self._rounds])
        return bytes(state)

    def decrypt(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("AES block must be 16 bytes")
        state = list(block)
        rk = self._round_keys
        _add_round_key(state, rk[self._rounds])
        for r in range(self._rounds - 1, 0, -1):
            _inv_shift_rows(state)
            state = [_INV_SBOX[b] for b in state]
            _add_round_key(state, rk[r])
            _inv_mix_columns(state)
        _inv_shift_rows(state)
        state = [_INV_SBOX[b] for b in state]
        _add_round_key(state, rk[0])
        return bytes(state)


def new(key: bytes, mode=MODE_ECB, **kwargs):
    """Small pyaes-style helper. Only ECB is used by callers of this module."""
    cipher = AES(key)
    if mode == MODE_ECB:
        return _EcbWrapper(cipher)
    if mode == MODE_CTR:
        return _CtrWrapper(cipher, kwargs.get('initial_value', b'\x00' * 16))
    raise ValueError("unsupported mode")


class _EcbWrapper:
    def __init__(self, cipher):
        self.cipher = cipher

    def encrypt(self, data):
        return b''.join(self.cipher.encrypt(data[i:i + 16])
                        for i in range(0, len(data), 16))

    def decrypt(self, data):
        return b''.join(self.cipher.decrypt(data[i:i + 16])
                        for i in range(0, len(data), 16))


class _CtrWrapper:
    def __init__(self, cipher, iv):
        self.cipher = cipher
        self.counter = int.from_bytes(iv, 'big')

    def _crypt(self, data):
        out = bytearray()
        for i in range(0, len(data), 16):
            ks = self.cipher.encrypt(self.counter.to_bytes(16, 'big'))
            self.counter = (self.counter + 1) & ((1 << 128) - 1)
            chunk = data[i:i + 16]
            out.extend(c ^ ks[j] for j, c in enumerate(chunk))
        return bytes(out)

    encrypt = _crypt
    decrypt = _crypt
