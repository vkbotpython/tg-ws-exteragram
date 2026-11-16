"""Translation layer: plugin settings -> wsproxy.proxy_config."""
from __future__ import annotations

import os
import secrets
from typing import Dict, List, Tuple

from wsproxy.config import proxy_config, coerce_domain_list, parse_dc_ip_list

DEFAULTS: Dict[str, object] = {
    'enabled': True,
    'listen_host': '127.0.0.1',
    'listen_port': 1443,
    'secret': '',
    'pool_size': 4,
    'buf_kb': 256,
    'dc_ips': '2:149.154.167.220, 4:149.154.167.220',
    'fake_tls_domain': '',
    'force_test_dc': False,
    'proxy_protocol': False,
    'fallback_cfproxy': True,
    'cfproxy_domains': '',
    'cfproxy_worker_domains': '',
    'h2_enabled': True,
    'disable_secure': False,
    'auto_apply_proxy': True,
    'restore_proxy_on_unload': True,
    'log_level': 'INFO',
    'log_buffer': 500,
    'status_autorefresh': False,
    'offline': False,
}


def setting(plugin, key: str, default=None):
    if default is None:
        default = DEFAULTS.get(key)
    try:
        value = plugin.get_setting(key, default)
    except Exception:
        value = default
    return default if value is None else value


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def valid_secret(value: str) -> bool:
    if not isinstance(value, str) or len(value.strip()) != 32:
        return False
    try:
        bytes.fromhex(value.strip())
        return True
    except ValueError:
        return False


def new_secret() -> str:
    return secrets.token_hex(16)


def ensure_secret(plugin) -> str:
    """Load the secret from settings, generating and persisting one if absent."""
    value = str(setting(plugin, 'secret', '') or '').strip()
    if not valid_secret(value):
        value = new_secret()
        try:
            plugin.set_setting('secret', value)
        except Exception:
            pass
    return value


def parse_dc_ips(text: str) -> Tuple[Dict[int, str], List[str]]:
    entries = [e.strip() for e in str(text or '').replace(';', ',').split(',')
               if e.strip()]
    try:
        return parse_dc_ip_list(entries), []
    except ValueError as exc:
        return {}, [str(exc)]


def apply_config(plugin, *, quiet: bool = False) -> Tuple[object, List[str]]:
    """Populate the global proxy_config from plugin settings.

    Returns ``(proxy_config, errors)``. Never raises: a malformed setting must
    not prevent the proxy from starting with the remaining sane values.
    """
    errors: List[str] = []

    secret = ensure_secret(plugin)
    if not valid_secret(secret):  # defensive: ensure_secret should fix this
        secret = new_secret()
        errors.append('invalid secret regenerated')

    dc_redirects, dc_errors = parse_dc_ips(setting(plugin, 'dc_ips'))
    errors.extend(dc_errors)

    port = _as_int(setting(plugin, 'listen_port'), 1443)
    if not 1 <= port <= 65535:
        errors.append('invalid listen port; using 1443')
        port = 1443
    pool_size = max(0, _as_int(setting(plugin, 'pool_size'), 4))
    buf_kb = max(4, _as_int(setting(plugin, 'buf_kb'), 256))

    proxy_config.host = str(setting(plugin, 'listen_host') or '127.0.0.1').strip()
    proxy_config.port = port
    proxy_config.secret = secret.strip()
    if dc_redirects:
        proxy_config.dc_redirects = dc_redirects
    proxy_config.pool_size = pool_size
    proxy_config.buffer_size = buf_kb * 1024
    proxy_config.fake_tls_domain = str(
        setting(plugin, 'fake_tls_domain') or '').strip()
    proxy_config.force_test_dc = _as_bool(setting(plugin, 'force_test_dc'))
    proxy_config.proxy_protocol = _as_bool(setting(plugin, 'proxy_protocol'))
    proxy_config.fallback_cfproxy = _as_bool(setting(plugin, 'fallback_cfproxy'))
    proxy_config.cfproxy_user_domains = coerce_domain_list(
        setting(plugin, 'cfproxy_domains'))
    proxy_config.cfproxy_worker_domains = coerce_domain_list(
        setting(plugin, 'cfproxy_worker_domains'))
    proxy_config.cfproxy_h2_media = _as_bool(setting(plugin, 'h2_enabled'))
    proxy_config.disable_secure = _as_bool(setting(plugin, 'disable_secure'))

    offline = _as_bool(setting(plugin, 'offline'))
    os.environ['TGWS_OFFLINE'] = '1' if offline else '0'

    return proxy_config, errors


def build_deeplink(config=None) -> str:
    """tg://proxy link for the current configuration (dd / ee secret form)."""
    cfg = config or proxy_config
    host = cfg.host
    if host == '0.0.0.0':
        host = '127.0.0.1'
    base = 'tg://proxy?server=%s&port=%d' % (host, cfg.port)
    if cfg.fake_tls_domain:
        domain_hex = cfg.fake_tls_domain.encode('ascii', errors='ignore').hex()
        return base + '&secret=ee%s%s' % (cfg.secret, domain_hex)
    return base + '&secret=dd%s' % cfg.secret


def safe_link(config=None) -> str:
    """Censored variant for logs: keep the host/port, hide the secret."""
    link = build_deeplink(config)
    return link.split('&secret=')[0] + '&secret=<redacted>'


def describe(config=None) -> str:
    cfg = config or proxy_config
    return ('host=%s port=%d pool=%d buf=%dKiB dc=%s fake_tls=%s '
            'cf=%s workers=%s h2=%s secure=%s' % (
                cfg.host, cfg.port, cfg.pool_size, cfg.buffer_size // 1024,
                ','.join('%d:%s' % (k, v) for k, v in sorted(
                    cfg.dc_redirects.items(), key=lambda i: i[0])),
                cfg.fake_tls_domain or '-',
                cfg.fallback_cfproxy,
                ','.join(cfg.cfproxy_worker_domains) or '-',
                cfg.cfproxy_h2_media,
                not cfg.disable_secure,
            ))
