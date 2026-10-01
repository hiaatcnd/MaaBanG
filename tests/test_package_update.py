"""Exercise the compiled Windows updater against disposable installations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from package_manifest import build_updater, write_manifest


@unittest.skipUnless(os.name == 'nt', 'Windows package updater')
class PackageUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.bin = Path(cls.build.name)
        cls.updater = cls.bin / 'MaaBanGUpdater.exe'
        build_updater(cls.updater)
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        for code in (0, 1):
            source = cls.bin / f'launcher{code}.cs'
            source.write_text('''using System; using System.IO; using System.Diagnostics;
class TestLauncher { static int Main(string[] args) {
if (args.Length > 0 && args[0] == "--wait") {
Console.WriteLine(Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks);
System.Threading.Thread.Sleep(2000); return 0; }
if (args.Length == 0) File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "restarted.txt"), "ok");
return ''' + str(code) + '; }}')
            subprocess.run([str(compiler), '/nologo', f'/out:{cls.bin / f"launcher{code}.exe"}', str(source)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='maabang update 中文 ')
        def cleanup():
            self.temp.name = '\\\\?\\' + self.temp.name
            self.temp.cleanup()
        self.addCleanup(cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / 'MaaBanG-existing'
        self.shared = self.base / 'shared'
        self.shared.mkdir()
        (self.shared / 'config.json').write_bytes(b'{"selected":"my-device","tasks":[1,2]}')
        (self.shared / 'chart.json').write_bytes(b'[{"type":"Single"}]')
        self.before = {p.name: p.read_bytes() for p in self.shared.iterdir()}
        self.env = dict(os.environ, MAABANG_DATA_DIR=str(self.shared))
        self.package(self.target, 'v0.6.1')
        (self.target / 'app/obsolete.dll').write_bytes(b'old dependency')
        self.new = self.base / 'MaaBanG-v0.6.2-win-x64'
        self.package(self.new, 'v0.6.2')
        self.archive = self.base / 'update.zip'

    def package(self, root, version, fail=False):
        for relative in ('MaaBanG.exe', 'app/interface.json', 'app/MFAAvalonia.exe',
                         'app/libs/MFAAvalonia.Core.dll', 'app/python/python.exe',
                         'app/agent/main.py', 'app/agent/user_data.py', 'app/tools/package_smoke.py',
                         'app/tools/MaaBanGUpdater.exe', 'app/tools/MaaBanGUpdater.exe.config',
                         'app/resource/model/ocr/rec.onnx'):
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(version)
        shutil.copy2(self.bin / f'launcher{int(fail)}.exe', root / 'MaaBanG.exe')
        (root / 'app/interface.json').write_text(json.dumps({'name': 'MaaBanG', 'version': version}))
        write_manifest(root, version)

    def zip(self):
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in self.new.rglob('*'):
                if file.is_file():
                    archive.write(file, file.relative_to(self.base).as_posix())

    def run_updater(self, *args, ok=True):
        result = subprocess.run([str(self.updater), *map(str, args)], env=self.env,
                                capture_output=True, encoding='utf-8-sig', timeout=30)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return result

    def prepare(self):
        self.zip()
        result = self.run_updater('--prepare', self.archive, self.target)
        return Path(result.stdout.strip())

    def apply(self, work, ok=True, pid=0, ticks=0, restart=False):
        args = ['--apply', work, pid, ticks]
        if not restart:
            args.append('--no-restart')
        return self.run_updater(*args, ok=ok)

    def test_full_replacement_preserves_data_and_old_backup(self):
        work = self.prepare()
        self.assertTrue((self.target / 'app/obsolete.dll').exists())
        self.apply(work)
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.2')
        self.assertFalse((self.target / 'app/obsolete.dll').exists())
        self.assertTrue((work / 'previous/app/obsolete.dll').exists())
        for relative in ('MaaBanG.exe', 'app/python/python.exe', 'app/libs/MFAAvalonia.Core.dll'):
            self.assertEqual((self.target / relative).read_bytes(), (self.new / relative).read_bytes())
        self.assertEqual({p.name: p.read_bytes() for p in self.shared.iterdir()}, self.before)
        self.assertEqual((work / 'status.txt').read_text(encoding='utf-8-sig'), 'complete')

    def test_failed_startup_check_rolls_back_whole_package(self):
        self.package(self.new, 'v0.6.2', fail=True)
        work = self.prepare()
        self.apply(work, ok=False)
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.1')
        self.assertTrue((self.target / 'app/obsolete.dll').exists())
        self.assertTrue((work / 'failed/app/interface.json').exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.shared.iterdir()}, self.before)
        self.assertEqual((work / 'status.txt').read_text(encoding='utf-8-sig'), 'rolled-back')

    def test_corrupt_or_incomplete_download_does_not_touch_install(self):
        (self.new / 'app/python/python.exe').write_bytes(b'corrupt')
        self.zip()
        self.run_updater('--prepare', self.archive, self.target, ok=False)
        self.assertTrue((self.target / 'app/obsolete.dll').exists())

    def test_missing_manifest_is_rejected(self):
        (self.new / 'package-manifest.json').unlink()
        self.zip()
        self.run_updater('--prepare', self.archive, self.target, ok=False)

    def test_missing_required_file_is_rejected_even_with_valid_inventory(self):
        (self.new / 'app/python/python.exe').unlink()
        write_manifest(self.new, 'v0.6.2')
        self.zip()
        self.run_updater('--prepare', self.archive, self.target, ok=False)

    def test_wrong_product_is_rejected(self):
        (self.new / 'app/interface.json').write_text('{"name":"Other","version":"v0.6.2"}')
        write_manifest(self.new, 'v0.6.2')
        self.zip()
        self.run_updater('--prepare', self.archive, self.target, ok=False)

    def test_long_dependency_paths_are_supported(self):
        import hashlib
        relative = Path('app/python/Lib/site-packages') / ('nested' * 15) / ('dependency' * 9 + '.py')
        payload = b'long path payload'
        manifest_path = self.new / 'package-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'][relative.as_posix()] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        self.zip()
        with zipfile.ZipFile(self.archive, 'a') as archive:
            archive.writestr(self.new.name + '/' + relative.as_posix(), payload)
        result = self.run_updater('--prepare', self.archive, self.target)
        work = Path(result.stdout.strip())
        self.apply(work)
        self.assertEqual(Path('\\\\?\\' + str(self.target / relative)).read_bytes(), payload)

    def test_zip_traversal_and_windows_aliases_are_rejected(self):
        for relative in ('../escape.txt', '/escape.txt', 'app/../../escape.txt',
                         'app/CON.txt', 'app/extra:stream', 'app/file.', 'app\\escape.txt'):
            with self.subTest(relative=relative):
                self.zip()
                with zipfile.ZipFile(self.archive, 'a') as archive:
                    archive.writestr(self.new.name + '/' + relative, 'bad')
                self.run_updater('--prepare', self.archive, self.target, ok=False)
        self.assertFalse((self.base / 'escape.txt').exists())

    def test_unlisted_payload_is_rejected(self):
        (self.new / 'extra.exe').write_bytes(b'extra')
        self.zip()
        self.run_updater('--prepare', self.archive, self.target, ok=False)

    def test_modified_stage_is_rejected_before_swap(self):
        work = self.prepare()
        (work / 'next/app/agent/main.py').write_text('changed after prepare')
        self.apply(work, ok=False)
        self.assertTrue((self.target / 'app/obsolete.dll').exists())

    def test_changed_install_is_rejected_before_swap(self):
        work = self.prepare()
        self.package(self.target, 'v0.6.3')
        self.apply(work, ok=False)
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.3')

    def test_user_data_inside_install_is_rejected(self):
        self.zip()
        self.env['MAABANG_DATA_DIR'] = str(self.target / 'user-data')
        self.run_updater('--prepare', self.archive, self.target, ok=False)

    def test_waits_for_old_process_then_restarts_launcher(self):
        work = self.prepare()
        with subprocess.Popen([str(self.target / 'MaaBanG.exe'), '--wait'], stdout=subprocess.PIPE, text=True) as old:
            ticks = int(old.stdout.readline().strip())
            self.apply(work, pid=old.pid, ticks=ticks, restart=True)
            self.assertIsNotNone(old.poll())
        import time
        for _ in range(40):
            if (self.target / 'restarted.txt').exists():
                break
            time.sleep(.05)
        self.assertTrue((self.target / 'restarted.txt').exists())


if __name__ == '__main__':
    unittest.main()
