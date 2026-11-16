"""Build and verify the distributable .elyx archive.

The canonical tool is `elyb build -nf`. This is a dependency-free fallback
that creates a plain zip with refmap.yml at the archive root.

Usage:
    python scripts/build_elyx.py [output.elyx]
    python -c "from scripts.build_elyx import verify; verify()"
"""
from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only these paths belong in the on-device package. Dev files, CI, docs and
# tests stay in the repository but are never shipped to the plugin loader.
INCLUDE_FILES = (
    'refmap.yml', 'metainfo.yml', 'meta.yml', 'main.py', 'NOTICE.md',
)
INCLUDE_DIRS = (
    'plugin', 'wsproxy', 'vendor', 'wheels', 'strings', 'locales', 'assets',
)
SKIP_NAMES = {'__pycache__', '.DS_Store', 'Thumbs.db'}


def iter_files(root=ROOT):
    for name in INCLUDE_FILES:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            yield path, name.replace('\\', '/')
    for dirname in INCLUDE_DIRS:
        base = os.path.join(root, dirname)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
            for filename in sorted(filenames):
                if filename in SKIP_NAMES or filename.endswith(('.pyc', '.pyo')):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, root).replace('\\', '/')
                yield full, rel


def build(output=None):
    output = output or os.path.join(ROOT, 'builds', 'tg_ws_bridge-1.0.0.elyx')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
        for full, rel in sorted(iter_files(), key=lambda item: item[1]):
            zf.write(full, rel)
    return output


def verify():
    """Structural check used by CI; raises SystemExit on failure."""
    import yaml

    errors = []
    refmap_path = os.path.join(ROOT, 'refmap.yml')
    try:
        with open(refmap_path, encoding='utf-8') as fh:
            refmap = yaml.safe_load(fh) or {}
    except Exception as exc:
        errors.append('cannot parse refmap.yml: %r' % exc)
        refmap = {}

    for key in ('main', 'metainfo', 'strings', 'assets', 'wheels'):
        value = refmap.get(key)
        if not value:
            errors.append('refmap.yml missing %r' % key)
        elif not os.path.exists(os.path.join(ROOT, str(value))):
            errors.append('refmap.yml %r points to missing path: %s' % (key, value))

    meta_path = os.path.join(ROOT, str(refmap.get('metainfo', 'metainfo.yml')))
    if os.path.isfile(meta_path):
        with open(meta_path, encoding='utf-8') as fh:
            meta = yaml.safe_load(fh) or {}
        if not meta.get('id'):
            errors.append('metainfo.yml missing id')
        if not str(meta.get('sdk_version', '')).startswith('>='):
            errors.append('metainfo.yml sdk_version should be a minimum constraint')

    wheels = [f for _, f in iter_files() if f.startswith('wheels/')]
    if not wheels:
        errors.append('no wheels found in package')
    for name in wheels:
        if not name.endswith('-none-any.whl'):
            errors.append('non portable wheel: %s' % name)

    if errors:
        for err in errors:
            print('ERROR:', err)
        raise SystemExit(1)
    print('Elyx package verified: %d files, %d wheels' % (
        len(list(iter_files())), len(wheels)))


def main():
    output = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print('wrote', output)


if __name__ == '__main__':
    main()
