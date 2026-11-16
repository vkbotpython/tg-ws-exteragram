"""Tests for the Elyx bridge layer (AES backends, config, logbus, runtime)."""
import asyncio
import logging
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from wsproxy import _aes
from vendor.pyaes import AES
from plugin import config_bridge, telegram_proxy
from plugin.logbus import LogBus
from plugin.runtime import ProxyRuntime


class _FakePlugin:
    def __init__(self, **values):
        self._settings = dict(values)
        self.id = 'tg_ws_bridge'

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value, **kwargs):
        self._settings[key] = value


class AesBackendTest(unittest.TestCase):
    VECTORS = [
        # NIST SP 800-38A, AES-128 CTR, big-endian counter.
        ('2b7e151628aed2a6abf7158809cf4f3c',
         'f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff',
         '6bc1bee22e409f96e93d7e117393172a',
         '874d6191b620e3261bef6864990db6ce'),
    ]

    def test_vendored_aes_known_vectors(self):
        vectors = [
            ('000102030405060708090a0b0c0d0e0f',
             '00112233445566778899aabbccddeeff',
             '69c4e0d86a7b0430d8cdb78070b4c55a'),
            ('000102030405060708090a0b0c0d0e0f1011121314151617',
             '00112233445566778899aabbccddeeff',
             'dda97ca4864cdfe06eaf70a0ec0d7191'),
            ('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f',
             '00112233445566778899aabbccddeeff',
             '8ea2b7ca516745bfeafc49904b496089'),
        ]
        for key, plain, cipher in vectors:
            aes = AES(bytes.fromhex(key))
            self.assertEqual(aes.encrypt(bytes.fromhex(plain)).hex(), cipher)
            self.assertEqual(aes.decrypt(bytes.fromhex(cipher)).hex(), plain)

    def test_pyaes_ctr_matches_nist_vector(self):
        _aes.set_backend('pyaes')
        key, iv, plain, cipher = self.VECTORS[0]
        got = _aes.Cipher(
            _aes.algorithms.AES(bytes.fromhex(key)),
            _aes.modes.CTR(bytes.fromhex(iv)),
        ).encryptor().update(bytes.fromhex(plain))
        self.assertEqual(got.hex(), cipher)

    @unittest.skipUnless(_aes._CryptoCipher is not None, 'no cryptography')
    def test_pyaes_and_cryptography_agree(self):
        data = os.urandom(4096)
        key, iv = os.urandom(32), os.urandom(16)
        outs = {}
        for name in ('pyaes', 'cryptography'):
            _aes.set_backend(name)
            outs[name] = _aes.Cipher(
                _aes.algorithms.AES(key),
                _aes.modes.CTR(iv),
            ).encryptor().update(data)
        self.assertEqual(outs['pyaes'], outs['cryptography'])

    def test_invalid_key_and_iv(self):
        with self.assertRaises(ValueError):
            _aes.algorithms.AES(b'x' * 15)
        with self.assertRaises(ValueError):
            _aes.modes.CTR(b'x' * 15)

    def tearDown(self):
        _aes.set_backend(None)


class ConfigBridgeTest(unittest.TestCase):
    def test_secret_generation_and_validation(self):
        plugin = _FakePlugin()
        secret = config_bridge.ensure_secret(plugin)
        self.assertEqual(len(secret), 32)
        self.assertTrue(config_bridge.valid_secret(secret))
        self.assertEqual(plugin._settings['secret'], secret)
        self.assertFalse(config_bridge.valid_secret('zz' * 16))

    def test_apply_config_maps_all_fields(self):
        plugin = _FakePlugin(
            secret='ab' * 16,
            listen_host='127.0.0.1',
            listen_port=2443,
            pool_size=7,
            buf_kb=64,
            dc_ips='2:1.2.3.4; 4:5.6.7.8',
            fake_tls_domain='example.com',
            force_test_dc=True,
            fallback_cfproxy=False,
            cfproxy_worker_domains='w1.example, w2.example',
            h2_enabled=False,
            disable_secure=True,
            offline=True,
        )
        cfg, errors = config_bridge.apply_config(plugin)
        self.assertEqual(errors, [])
        self.assertEqual(cfg.port, 2443)
        self.assertEqual(cfg.pool_size, 7)
        self.assertEqual(cfg.buffer_size, 64 * 1024)
        self.assertEqual(cfg.dc_redirects, {2: '1.2.3.4', 4: '5.6.7.8'})
        self.assertEqual(cfg.fake_tls_domain, 'example.com')
        self.assertTrue(cfg.force_test_dc)
        self.assertFalse(cfg.fallback_cfproxy)
        self.assertEqual(cfg.cfproxy_worker_domains,
                         ['w1.example', 'w2.example'])
        self.assertTrue(cfg.disable_secure)
        self.assertEqual(os.environ['TGWS_OFFLINE'], '1')

    def test_bad_dc_entry_is_reported_without_crash(self):
        plugin = _FakePlugin(dc_ips='not-a-dc')
        cfg, errors = config_bridge.apply_config(plugin)
        self.assertTrue(errors)

    def test_deeplink_dd_and_ee(self):
        plugin = _FakePlugin(secret='ab' * 16, listen_port=1443)
        cfg, _ = config_bridge.apply_config(plugin)
        link = config_bridge.build_deeplink(cfg)
        self.assertTrue(link.startswith('tg://proxy?server=127.0.0.1&port=1443'))
        self.assertTrue(link.endswith('&secret=dd' + 'ab' * 16))
        cfg.fake_tls_domain = 'example.com'
        ee = config_bridge.build_deeplink(cfg)
        self.assertIn('&secret=ee' + 'ab' * 16 + b'example.com'.hex(), ee)
        self.assertNotIn('ab' * 16, config_bridge.safe_link(cfg))


class LogBusTest(unittest.TestCase):
    def test_ring_buffer_and_levels(self):
        bus = LogBus(capacity=20)
        handler = bus._handler
        handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        logger = logging.getLogger('logbus-test')
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info('hello world')
            logger.debug('detail line')
            self.assertEqual(len(bus.records()), 2)
            self.assertEqual(len(bus.records(min_level=logging.INFO)), 1)
            bus.set_capacity(1)  # clamped to the 50-record minimum
            self.assertEqual(bus.capacity, 50)
            for i in range(60):
                logger.debug('line %d', i)
            self.assertEqual(len(bus.records()), 50)
            bus.clear()
            self.assertEqual(bus.text(), '')
        finally:
            logger.removeHandler(handler)


class RuntimeTest(unittest.TestCase):
    def test_status_snapshot_is_plain_types(self):
        runtime = ProxyRuntime('test-owner')
        status = runtime.status()
        self.assertIn('running', status)
        self.assertIn('summary', status)
        self.assertFalse(status['running'])

    def test_stop_before_start_is_safe(self):
        runtime = ProxyRuntime('test-owner-2')
        self.assertTrue(runtime.stop(timeout=0.2))


class ProxyControllerTest(unittest.TestCase):
    def test_dump_and_restore_are_safe_off_device(self):
        controller = telegram_proxy.ProxyController(_FakePlugin())
        self.assertIsInstance(controller.dump(), str)
        self.assertFalse(controller.restore())


if __name__ == '__main__':
    unittest.main()
