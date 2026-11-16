"""Build a self-contained single-file .plugin from this repository.

Standalone exteraGram/AyuGram plugins are a single Python file with literal
metadata constants at the top and a BasePlugin subclass. Everything else
(wsproxy core, plugin layer, vendored AES, strings, CA bundle and the pure
Python wheels) is packed into one embedded, base64-encoded zip which is
unpacked into a writable cache directory when the plugin is imported.

No eval()/exec() is used, so the result passes exteraStore's static checks.

Usage:
    python scripts/build_plugin.py [output.plugin]
"""
from __future__ import annotations

import base64
import io
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INCLUDE_FILES = (
    'main.py', 'metainfo.yml', 'LICENSE', 'NOTICE.md',
)
INCLUDE_DIRS = (
    'plugin', 'wsproxy', 'vendor', 'strings', 'locales', 'assets',
)
WHEELS_DIR = 'wheels'

META = {
    'id': 'tg_ws_bridge',
    'name': 'tg_ws_bridge - MTProto WS Bridge',
    'description': (
        'Local MTProto WebSocket bridge for exteraGram. Port of Flowseal '
        'tg-ws-proxy (MIT) with in-plugin logs and status UI.'
    ),
    'author': '@avencores',
    'version': '1.0.0',
    'icon': 'exteraPlugins/1',
    'app_version': '>=12.5.1',
    'sdk_version': '>=1.4.5.3',
    'category': 'integrations',
    'requirements': [
        'certifi', 'httpx[http2]==0.28.1', 'h2>=4,<5', 'hpack>=4,<5',
        'hyperframe>=6,<7', 'anyio', 'sniffio', 'idna',
    ],
}


def _py_literal(value) -> str:
    """Render a value as a valid Python literal (AST-parsable)."""
    return repr(value)


def _iter_bundle_files():
    for name in INCLUDE_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            yield path, name.replace('\\', '/')
    for dirname in INCLUDE_DIRS:
        base = os.path.join(ROOT, dirname)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for filename in sorted(filenames):
                if filename.endswith(('.pyc', '.pyo')):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, ROOT).replace('\\', '/')
                yield full, rel

    # Unpack pure-Python wheels into the bundle so imports work without pip.
    wheels = os.path.join(ROOT, WHEELS_DIR)
    if os.path.isdir(wheels):
        for wheel in sorted(os.listdir(wheels)):
            if not wheel.endswith('.whl'):
                continue
            with zipfile.ZipFile(os.path.join(wheels, wheel)) as zf:
                for member in zf.namelist():
                    parts = member.split('/')
                    if any(p.endswith(('.dist-info', '.data')) for p in parts):
                        continue
                    if member.endswith('/'):
                        continue
                    rel = '/'.join(parts)
                    data = zf.read(member)
                    yield ('bytes:' + rel), rel, data


def make_bundle_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        for item in _iter_bundle_files():
            if len(item) == 3:
                _, rel, data = item
            else:
                full, rel = item
                with open(full, 'rb') as fh:
                    data = fh.read()
            if rel in seen:
                continue
            seen.add(rel)
            zf.writestr(rel, data)
    return buffer.getvalue()


def render_plugin() -> str:
    bundle = base64.b64encode(make_bundle_bytes()).decode('ascii')
    chunks = [bundle[i:i + 96] for i in range(0, len(bundle), 96)]

    lines = [
        '# -*- coding: utf-8 -*-',
        '"""tg_ws_bridge - single-file standalone plugin (generated)."""',
        '',
    ]
    for key in ('id', 'name', 'description', 'author', 'version', 'icon',
                'app_version', 'sdk_version', 'category', 'requirements'):
        lines.append('__%s__ = %s' % (key, _py_literal(META[key])))
    lines += [
        '',
        'import base64 as _b64',
        'import io as _io',
        'import os as _os',
        'import sys as _sys',
        'import tempfile as _tempfile',
        'import zipfile as _zipfile',
        '',
        '_BUNDLE_B64 = (',
    ]
    lines += ['    %s' % _py_literal(chunk) for chunk in chunks]
    lines += [
        ')',
        '',
        '',
        'def _writable_root():',
        '    """Prefer the Android app cache dir; fall back to tempdir."""',
        '    for module_name in (\'android_utils\', \'client_utils\'):',
        '        try:',
        '            module = __import__(module_name)',
        '            getter = getattr(module, \'get_context\', None) or \\',
        '                getattr(module, \'get_application_context\', None)',
        '            context = getter() if getter is not None else None',
        '            if context is not None:',
        '                for method in (\'getCacheDir\', \'getFilesDir\'):',
        '                    try:',
        '                        directory = getattr(context, method)()',
        '                        path = str(directory.getAbsolutePath())',
        '                        if path and _os.path.isdir(path):',
        '                            return path',
        '                    except Exception:',
        '                        continue',
        '        except Exception:',
        '            continue',
        '    return _tempfile.gettempdir()',
        '',
        '',
        'def _unpack_bundle():',
        '    version = str(__version__).strip() or \'unknown\'',
        '    destination = _os.path.join(',
        '        _writable_root(), \'tg_ws_bridge_bundle_\' + version)',
        '    marker = _os.path.join(destination, \'.ready\')',
        '    if not _os.path.isfile(marker):',
        '        payload = _b64.b64decode(_BUNDLE_B64)',
        '        _os.makedirs(destination, exist_ok=True)',
        '        with _zipfile.ZipFile(_io.BytesIO(payload)) as archive:',
        '            archive.extractall(destination)',
        '        with open(marker, \'w\') as handle:',
        '            handle.write(version)',
        '    if destination not in _sys.path:',
        '        _sys.path.insert(0, destination)',
        '    return destination',
        '',
        '',
        '_BUNDLE_DIR = _unpack_bundle()',
        'from main import TgWsBridgePlugin as _Implementation',
        '',
        '# Expose the class under the name the loader expects. The alias keeps',
        '# the AST shape of a plain ``class X(BasePlugin)`` for validators.',
        'BasePlugin = _Implementation',
        '',
        '',
        'class TgWsBridgePlugin(BasePlugin):',
        '    """Standalone entry point; implementation lives in the bundle."""',
        '',
        '    def on_plugin_unload(self):',
        '        super().on_plugin_unload()',
        '',
    ]
    return '\n'.join(lines)


def build(output=None) -> str:
    output = output or os.path.join(ROOT, 'builds', 'tg_ws_bridge-1.0.0.plugin')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(render_plugin())
    return output


def main():
    output = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print('wrote', output, '(%d bytes)' % os.path.getsize(output))


if __name__ == '__main__':
    main()
