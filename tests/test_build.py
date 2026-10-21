import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from astrbot_plugin_deepseek_search.build import FILES, build


class BuildTests(unittest.TestCase):
    def test_version_checksum_repeatability_and_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            # A renamed checkout with a future version must not use hard-coded names.
            root = Path(temp) / 'astrbot_deepseek_search'
            root.mkdir()
            source = Path(__file__).resolve().parents[1]
            for filename in FILES:
                shutil.copyfile(source / filename, root / filename)
            metadata = root / 'metadata.yaml'
            metadata.write_text(re.sub(r'^version:.*$', 'version: v9.8.7',
                                       metadata.read_text(encoding='utf-8'), flags=re.MULTILINE),
                                encoding='utf-8')
            archive = build(root=root, expected_version='v9.8.7')
            first = archive.read_bytes()
            self.assertEqual(archive.name, 'astrbot_plugin_deepseek_search-v9.8.7.zip')
            self.assertEqual(build(root=root).read_bytes(), first)
            self.assertEqual(archive.with_suffix('.zip.sha256').read_text().split()[0], hashlib.sha256(first).hexdigest())
            with ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()), {f'astrbot_plugin_deepseek_search/{f}' for f in FILES})
                for name in z.namelist():
                    self.assertNotIn('tests/', name)
                readme = z.read('astrbot_plugin_deepseek_search/README.md').decode('utf-8-sig')
                for link in re.findall(r'\]\(([^)]+)\)', readme):
                    if not link.startswith(('https://', 'http://', '#')):
                        self.assertIn(f'astrbot_plugin_deepseek_search/{link}', z.namelist())
            with self.assertRaisesRegex(ValueError, 'tag'):
                build(root=root, expected_version='v9.9.9')

    def test_public_readme_covers_schema_and_local_links(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / 'README.md').read_text(encoding='utf-8-sig')
        schema = json.loads((root / '_conf_schema.json').read_text(encoding='utf-8'))
        for key in schema:
            self.assertIn(f'`{key}`', text)
        for link in re.findall(r'\]\(([^)]+)\)', text):
            if not link.startswith(('https://', 'http://', '#')):
                self.assertTrue((root / link).exists(), link)
