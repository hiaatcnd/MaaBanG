"""Exercise the compiled Windows updater against disposable installations."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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
        cls.stub_binaries = {}
        stub_source = Path(__file__).parent / 'fixtures/updater/adb_lock_stub.cs'
        for architecture in ('x86', 'x64'):
            output = cls.bin / architecture / 'adb.exe'
            output.parent.mkdir()
            subprocess.run([str(compiler), '/nologo', f'/platform:{architecture}',
                            f'/out:{output}', str(stub_source)], check=True)
            cls.stub_binaries[architecture] = output

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
        self.old_files = {p.relative_to(self.target): p.read_bytes()
                          for p in self.target.rglob('*') if p.is_file()}
        self.new = self.base / 'MaaBanG-v0.6.2-win-x64'
        self.package(self.new, 'v0.6.2')
        self.archive = self.base / 'update.zip'
        self.stub_count = 0

    def lock_process(self, *, architecture='x64', cwd=None, arguments=None,
                     name='adb.exe', listen=False, ignore_kill=False, advertised_port=None):
        """Hold only a disposable cwd; the fixture never connects to an ADB server."""
        self.stub_count += 1
        folder = self.base / f'process-{self.stub_count}'
        folder.mkdir()
        executable = folder / name
        shutil.copy2(self.stub_binaries[architecture], executable)
        ready, stop = folder / 'ready.txt', folder / 'stop.txt'
        kill_marker = folder / 'kill-request.txt'
        connect_marker = folder / 'connected.txt'
        cwd = cwd or self.target / 'app'
        port = 0
        if listen:
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
        if arguments is None:
            arguments = ['-L', f'tcp:{advertised_port or port}', 'fork-server', 'server', '--reply-fd', '600']
        environment = dict(self.env, MAABANG_TEST_STUB_READY=str(ready),
                           MAABANG_TEST_STUB_STOP=str(stop),
                           MAABANG_TEST_STUB_KILL_MARKER=str(kill_marker),
                           MAABANG_TEST_STUB_CONNECT_MARKER=str(connect_marker),
                           MAABANG_TEST_STUB_IGNORE_KILL=str(int(ignore_kill)),
                           MAABANG_TEST_STUB_PORT=str(port))
        process = subprocess.Popen([str(executable), *arguments], cwd=cwd,
                                   env=environment, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)

        def cleanup():
            try:
                stop.write_text('exit', encoding='utf-8')
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                process.stderr.close()

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.025)
        if not ready.is_file():
            error = process.stderr.read().decode('utf-8', errors='replace') if process.poll() is not None else ''
            self.fail(f'Lock fixture did not become ready: {process.poll()} {error}')
        pointer_size, observed_cwd = ready.read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(int(pointer_size), 4 if architecture == 'x86' else 8)
        # Windows may expand the inherited TEMP directory's 8.3 alias in the
        # child process. Compare the directory identity, not its spelling.
        self.assertTrue(Path(observed_cwd).samefile(cwd), (observed_cwd, str(cwd)))
        process.kill_marker = kill_marker
        process.connect_marker = connect_marker
        process.stub_port = port
        return process

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
                                capture_output=True, encoding='utf-8-sig', timeout=45)
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

    def assert_blocked_install_preserved(self, work):
        self.assertEqual({p.relative_to(self.target): p.read_bytes()
                          for p in self.target.rglob('*') if p.is_file()}, self.old_files)
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.1')
        self.assertTrue((self.target / 'app/obsolete.dll').is_file())
        self.assertTrue((work / 'next/app/interface.json').is_file())
        self.assertFalse((work / 'previous').exists())
        self.assertFalse((work / 'failed').exists())
        self.assertEqual({p.relative_to(work / 'next'): p.read_bytes()
                          for p in (work / 'next').rglob('*') if p.is_file()},
                         {p.relative_to(self.new): p.read_bytes()
                          for p in self.new.rglob('*') if p.is_file()})
        self.assertEqual({p.name: p.read_bytes() for p in self.shared.iterdir()}, self.before)
        self.assertEqual((work / 'status.txt').read_text(encoding='utf-8-sig'), 'rolled-back')

    def assert_releases_cwd_server(self, architecture, *, unrelated=False):
        work = self.prepare()
        server = self.lock_process(architecture=architecture, listen=True)
        others = []
        if unrelated:
            # Prefix similarity must not turn a sibling installation into a match.
            other_app = self.base / (self.target.name + '-other') / 'app'
            other_app.mkdir(parents=True)
            others.append(self.lock_process(cwd=other_app, listen=True))
            others.append(self.lock_process(cwd=self.shared, arguments=['devices']))
        self.apply(work)
        self.assertEqual(server.wait(timeout=5), 0, 'The server must exit through its own protocol handler')
        self.assertEqual(server.kill_marker.read_text(), '0009host:kill')
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.2')
        self.assertTrue((work / 'previous/app/obsolete.dll').is_file())
        self.assertEqual((work / 'status.txt').read_text(encoding='utf-8-sig'), 'complete')
        self.assertEqual({p.name: p.read_bytes() for p in self.shared.iterdir()}, self.before)
        for other in others:
            self.assertIsNone(other.poll(), 'Unrelated ADB processes must remain alive')
            self.assertFalse(other.kill_marker.exists())
            self.assertFalse(other.connect_marker.exists())

    def test_x86_cwd_server_exits_without_stopping_other_adb(self):
        self.assert_releases_cwd_server('x86', unrelated=True)

    def test_x64_cwd_server_exits_through_its_exact_endpoint(self):
        self.assert_releases_cwd_server('x64')

    def test_short_installation_alias_releases_long_cwd_server(self):
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        short_path = kernel.GetShortPathNameW
        short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        short_path.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        length = short_path(str(self.target), buffer, len(buffer))
        self.assertGreater(length, 0, ctypes.get_last_error())
        self.assertLess(length, len(buffer))
        long_target = self.target.resolve()
        alias = Path(buffer.value)
        if alias.name.lower() == long_target.name.lower():
            self.skipTest('This volume does not provide an 8.3 alias for the installation')
        self.assertTrue(alias.samefile(long_target))
        # Exercise the actual executable and its .NET runtime configuration:
        # a reflection host may use different path compatibility switches.
        self.target = alias
        work = self.prepare()
        server = self.lock_process(cwd=long_target / 'app', listen=True)
        self.apply(work)
        self.assertEqual(server.wait(timeout=5), 0)
        self.assertEqual(server.kill_marker.read_text(), '0009host:kill')
        self.assertEqual(json.loads((long_target / 'app/interface.json').read_text())['version'], 'v0.6.2')
        self.assertTrue((work / 'previous/app/obsolete.dll').is_file())
        self.assertEqual((work / 'status.txt').read_text(encoding='utf-8-sig'), 'complete')
        self.assertEqual({p.name: p.read_bytes() for p in self.shared.iterdir()}, self.before)

    def test_non_adb_and_adb_client_blockers_are_not_stopped(self):
        work = self.prepare()
        non_adb = self.lock_process(name='other-program.exe', listen=True)
        adb_client = self.lock_process(arguments=['devices'], listen=True)
        self.apply(work, ok=False)
        self.assert_blocked_install_preserved(work)
        for process in (non_adb, adb_client):
            self.assertIsNone(process.poll())
            self.assertFalse(process.kill_marker.exists())
            self.assertFalse(process.connect_marker.exists())

    def test_advertised_endpoint_owned_by_another_process_is_not_contacted(self):
        work = self.prepare()
        listener = self.lock_process(cwd=self.shared, listen=True)
        blocker = self.lock_process(advertised_port=listener.stub_port)
        self.apply(work, ok=False)
        self.assert_blocked_install_preserved(work)
        for process in (listener, blocker):
            self.assertIsNone(process.poll())
            self.assertFalse(process.kill_marker.exists())
            self.assertFalse(process.connect_marker.exists())

    def test_server_that_refuses_exit_preserves_old_install_and_stage(self):
        work = self.prepare()
        server = self.lock_process(listen=True, ignore_kill=True)
        self.apply(work, ok=False)
        self.assertEqual(server.kill_marker.read_text(), '0009host:kill')
        self.assertIsNone(server.poll(), 'A refusal to exit must not escalate to process termination')
        self.assert_blocked_install_preserved(work)

    def test_startup_failure_after_server_exit_still_rolls_back(self):
        self.package(self.new, 'v0.6.2', fail=True)
        work = self.prepare()
        server = self.lock_process(listen=True)
        self.apply(work, ok=False)
        self.assertEqual(server.wait(timeout=5), 0)
        self.assertEqual(server.kill_marker.read_text(), '0009host:kill')
        self.assertEqual(json.loads((self.target / 'app/interface.json').read_text())['version'], 'v0.6.1')
        self.assertTrue((self.target / 'app/obsolete.dll').is_file())
        self.assertTrue((work / 'failed/app/interface.json').is_file())
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
