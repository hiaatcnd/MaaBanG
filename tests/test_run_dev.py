import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from run_dev import development_environment, ui_executable


class DevelopmentLaunchTests(unittest.TestCase):
    def test_defaults_and_overrides_do_not_modify_parent_environment(self):
        with patch.dict(os.environ, {'MAABANG_DATA_DIR': ''}):
            self.assertEqual(development_environment()['MAABANG_DATA_DIR'], str(ROOT / '.maabang-dev'))
            self.assertEqual(os.environ['MAABANG_DATA_DIR'], '')
        with patch.dict(os.environ, {'MAABANG_DATA_DIR': str(ROOT / 'debug/custom')}):
            self.assertEqual(development_environment()['MAABANG_DATA_DIR'], str(ROOT / 'debug/custom'))
            self.assertEqual(development_environment(ROOT / 'debug/explicit')['MAABANG_DATA_DIR'], str(ROOT / 'debug/explicit'))

    def test_child_process_and_agent_resolve_the_same_isolated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            code = "import os,sys,json;sys.path.insert(0,'agent');from user_data import data_root;print(json.dumps([os.environ['MAABANG_DATA_DIR'],str(data_root())]))"
            result = subprocess.run([sys.executable, str(ROOT / 'tools/run_dev.py'),
                                     '--data-dir', directory, '--', sys.executable, '-c', code],
                                    capture_output=True, text=True, encoding='utf-8', check=True)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), [str(Path(directory).resolve())] * 2)

    def test_ui_uses_package_runtime_without_production_migration_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / 'app').mkdir()
            exe = package / 'app/MFAAvalonia.exe'
            exe.touch()
            for path in (package, package / 'app', package / 'MaaBanG.exe', exe):
                self.assertEqual(ui_executable(path), exe.resolve())
            with self.assertRaises(ValueError):
                ui_executable(package / 'missing')


if __name__ == '__main__':
    unittest.main()
