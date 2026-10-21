"""Build an installable ZIP with an explicit allowlist (no keys/tests/caches)."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

FILES = ('__init__.py', 'main.py', 'client.py', 'cache.py', 'limits.py', 'output.py',
         'metadata.yaml', '_conf_schema.json', 'requirements.txt', 'README.md', 'LICENSE',
         'CHANGELOG.md', 'RELEASING.md')


def build(root=None, output_dir=None, expected_version=None):
    root = Path(root or Path(__file__).resolve().parent)
    metadata = (root / 'metadata.yaml').read_text(encoding='utf-8-sig')
    def field(name):
        match = re.search(rf'^{name}:\s*([^\r\n#]+)', metadata, re.MULTILINE)
        if not match:
            raise ValueError(f'Missing metadata field: {name}')
        return match.group(1).strip().strip('\'"')
    name, version = field('name'), field('version')
    if not re.fullmatch(r'astrbot_plugin_[a-z0-9_]+', name):
        raise ValueError('Invalid plugin name')
    if not re.fullmatch(r'v?\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version):
        raise ValueError('Invalid version')
    version = 'v' + version.removeprefix('v')
    if expected_version and expected_version != version:
        raise ValueError('Git tag does not match metadata version')
    json.loads((root / '_conf_schema.json').read_text(encoding='utf-8-sig'))
    out = Path(output_dir) if output_dir else root.parent / 'dist'
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'{name}-{version}.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
        for filename in FILES:
            info = ZipInfo(f'{name}/{filename}', date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, (root / filename).read_bytes())
    with ZipFile(archive) as z:
        if z.testzip() or len(z.namelist()) != len(FILES):
            raise ValueError('Archive verification failed')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir')
    parser.add_argument('--expected-version')
    args = parser.parse_args()
    print(build(output_dir=args.output_dir, expected_version=args.expected_version))
