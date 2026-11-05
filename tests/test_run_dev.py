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
    def legacy_environment(self):
        # Reproduce Windows redirected GBK output even on UTF-8 test hosts.
        return {**os.environ, 'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'gbk'}

    def test_launcher_and_descendants_share_utf8_under_legacy_host(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / '中文配置_🎵'
            child = (
                "import os,sys,subprocess;"
                "print('子进程：协力🎵／ピコ',flush=True);"
                "print('子进程诊断：失败🎵',file=sys.stderr,flush=True);"
                "assert sys.flags.utf8_mode==1;"
                "subprocess.run([sys.executable,'-c',"
                "\"print('孙进程：中文🎵')\"],check=True)"
            )
            result = subprocess.run([sys.executable, str(ROOT / 'tools/run_dev.py'),
                                     '--data-dir', str(data_dir), '--', sys.executable, '-c', child],
                                    env=self.legacy_environment(), capture_output=True, check=True)
            output = result.stdout.decode('utf-8')
            self.assertIn(f'开发数据目录：{data_dir.resolve()}', output)
            self.assertIn('子进程：协力🎵／ピコ', output)
            self.assertIn('孙进程：中文🎵', output)
            self.assertIn('子进程诊断：失败🎵', result.stderr.decode('utf-8'))

    def test_argument_errors_are_utf8_before_any_child_starts(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/run_dev.py')],
                                env=self.legacy_environment(), capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('请指定 --app', result.stderr.decode('utf-8'))

    def test_child_environment_overrides_legacy_encoding_without_mutating_parent(self):
        with patch.dict(os.environ, {'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'gbk'}):
            env = development_environment()
            self.assertEqual(env['PYTHONUTF8'], '1')
            self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
            self.assertEqual(os.environ['PYTHONUTF8'], '0')
            self.assertEqual(os.environ['PYTHONIOENCODING'], 'gbk')

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
