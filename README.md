# tg_ws_bridge

[![tests](https://github.com/avencores/tg_ws_bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/avencores/tg_ws_bridge/actions/workflows/tests.yml)

**Local MTProto → WebSocket bridge as an [exteraGram / AyuGram](https://exteragram.org) Elyx plugin.**

A port of [Flowseal/tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy) (MIT) to Python 3.11 / Chaquopy. It accepts obfuscated Telegram sessions on `127.0.0.1:1443`, extracts the DC ID from the initialization packet and relays traffic over WebSocket/TLS to `kwsN.web.telegram.org`, with Cloudflare proxy / Worker / HTTP2 / TCP fallbacks.

Everything needed for debugging — logs, live statistics and a proxy-config dump — is available from the plugin settings. **No adb required.**

## Features

- Full protocol parity with the original proxy engine (ported `proxy/*` → `wsproxy/`)
- Automatic proxy activation via reflection, with a `tg://proxy` deep-link fallback
- AES-CTR with a Java JCE → `cryptography` → pure-Python → OpenSSL backend chain
- Bundled pure-Python wheels; no compiler or native modules on the device
- Background asyncio loop in its own thread with safe stop/restart and reload protection
- In-plugin log viewer (ring buffer + rotating log file) and status/statistics screen
- Optional Fake TLS (`ee`-secret) masking, custom DC redirects, PROXY protocol support

## Install

1. Grab `tg_ws_bridge-*.elyx` from [Releases](../../releases), or build it yourself (below).
2. Send the file to Saved Messages in exteraGram and open it → **Install Plugin**.
3. Open the plugin settings. A 32-hex secret is generated automatically.
4. Leave **Enabled** on. With **Apply proxy automatically** enabled the plugin tries reflection first; if the build's proxy API is unknown it opens `tg://proxy` for confirmation.
5. Open **Status and statistics**: `RUNNING`, growing `total` / `up` / `down`, and `DCx -> WS pool hit` in **Logs** confirm the relay works.

Use **Open tg://proxy** or **Copy tg://proxy link** if you prefer to connect manually.

## Build from source

Canonical (ElyxBuilder):

```bash
elyb build -nf
# builds/tg_ws_bridge-1.0.0.elyx
```

Dependency-free fallback:

```bash
python scripts/build_elyx.py                    # builds/tg_ws_bridge-1.0.0.elyx
python -c "from scripts.build_elyx import verify; verify()"
```

To refresh bundled wheels (run on a PC, not on the device):

```bash
pip download --only-binary=:all: --platform any --python-version 3.11 \
  --implementation cp --abi none --dest wheels \
  "httpx[http2]==0.28.1" certifi sniffio
```

## Repository layout

```
refmap.yml          Elyx component map (main / metainfo / strings / assets / wheels)
metainfo.yml        id, sdk_version >=1.4.5.3, requirements
meta.yml            compatibility copy for older Elyx validators
main.py             TgWsBridgePlugin(BasePlugin)
plugin/             SDK compatibility, runtime, config bridge, proxy hook,
                    log bus, debug UI, crypto backend
wsproxy/            ported proxy core (protocol logic unchanged)
vendor/pyaes/       pure-Python AES fallback (NIST-vector tested)
wheels/             certifi, httpx, httpcore, h11, h2, hpack, hyperframe,
                    anyio, sniffio, idna (all py3-none-any)
strings/ locales/   en/ru strings (YAML for Elyx, JSON for compatibility)
assets/cacert.pem   bundled CA bundle for Chaquopy
docs/PLAN.ru.md     original Russian implementation plan
tests_port/         ported core tests + plugin-layer tests
scripts/            build/verify helpers
```

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests_port -q
```

Current result: **230 passed, 415 subtests passed** (Python 3.11+).
CI runs the suite on Python 3.11, 3.12 and 3.13.

### AES backend

Selected automatically; overridable with `TGWS_AES_BACKEND=java|cryptography|pyaes|openssl`:

1. `java` — `javax.crypto.Cipher("AES/CTR/NoPadding")` (preferred on Android)
2. `cryptography` — desktop / tests
3. `pyaes` — vendored pure Python (NIST-vector verified)
4. `openssl` — ctypes libcrypto fallback

All backends use a 128-bit big-endian counter, matching the original CTR mode.

## Notes and limitations

- The exact `ConnectionsManager.setProxySettings(...)` signature differs between exteraGram builds. Use **Dump proxy configuration** in the debug UI to inspect the target build; the `tg://proxy` fallback always works.
- Doze / process death can stop the listener. The plugin restarts it on `START`/`RESUME`; change the port if 1443 is taken.
- HTTP/2 media needs the bundled wheels; without them the bridge falls back to WS/CF/TCP automatically.
- Fake TLS needs TLS 1.3 in the platform `ssl`; otherwise random ApplicationData record sizes are used.
- Do not publish your proxy secret — treats it like a password.

## License

MIT — see [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). The original `tg-ws-proxy` source tree was removed after the port; attribution and the full original license text are retained in those files. Bundled `certifi` data is MPL-2.0 (license included in its wheel).
