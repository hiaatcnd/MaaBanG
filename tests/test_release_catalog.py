"""A failed catalog refresh must prevent every release flavor from being built."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import install


class ReleaseCatalogTests(unittest.TestCase):
    def test_refresh_failure_stops_before_creating_package(self):
        for version in ('v0.6.3','v0.6.3-preview1','v0.6.3-ci'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                (root/'tools').mkdir()
                (root/'tools/release-inputs.json').write_text(json.dumps({}),encoding='utf8')
                with patch.object(install,'ROOT',root),patch.object(install.subprocess,'run',
                        side_effect=subprocess.CalledProcessError(1,'catalog refresh')) as run:
                    with self.assertRaises(subprocess.CalledProcessError):install.build(version)
                    run.assert_called_once_with([sys.executable,str(root/'tools/update_song_catalog.py')],cwd=root,check=True)
                self.assertFalse((root/'install'/f'MaaBanG-{version}-win-x64').exists())
                self.assertFalse((root/'dist').exists())

if __name__=='__main__':unittest.main()
