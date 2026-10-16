"""Keep the discovered-input implementation consistent across release entrypoints."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_ui
import install


class ReleaseRuntimeTests(unittest.TestCase):
    def test_native_and_python_release_pins_and_export_hashes_match(self):
        spec = json.loads((ROOT / 'tools/release-inputs.json').read_text(encoding='utf-8'))['maafw']
        version = spec['version']
        self.assertGreaterEqual(tuple(map(int, version.split('.'))), (5, 12, 3))
        self.assertTrue(spec['url'].endswith(f'/v{version}/MAA-win-x86_64-v{version}.zip'))
        self.assertRegex(spec['sha256'], r'^[0-9a-f]{64}$')
        project = tomllib.loads((ROOT / 'agent/pyproject.toml').read_text(encoding='utf-8'))
        self.assertIn(f'maafw=={version}', project['project']['dependencies'])
        self.assertIn(f'maafw=={version}', (ROOT / 'agent/requirements.txt').read_text().splitlines())
        lock = tomllib.loads((ROOT / 'agent/uv.lock').read_text(encoding='utf-8'))
        framework = next(package for package in lock['package'] if package['name'] == 'maafw')
        self.assertEqual(framework['version'], version)
        exported = (ROOT / 'tools/release-requirements.txt').read_text(encoding='utf-8')
        entry = re.search(r'^maafw==([^\n]+)((?:\n[ \t]+[^\n]+)*)', exported, re.MULTILINE)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.group(1).rstrip(' \\'), version)
        self.assertEqual(set(re.findall(r'--hash=(sha256:[0-9a-f]+)', entry.group(0))),
                         {wheel['hash'] for wheel in framework['wheels']})
        tooling = (ROOT / 'maatools.config.mts').read_text(encoding='utf-8')
        self.assertIn(f"maaVersion: '{version}'", tooling)

    def test_new_archive_pin_does_not_use_old_cached_download(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            (cache / 'maafw.zip').write_bytes(b'previous runtime')
            data = b'new runtime'
            spec = {'url': 'https://example.invalid/runtime.zip',
                    'sha256': hashlib.sha256(data).hexdigest()}
            def download(url, destination):
                self.assertEqual(url, spec['url'])
                destination.write_bytes(data)
            with patch.object(install.urllib.request, 'urlretrieve', side_effect=download) as fetch:
                archive = install.release_archive('maafw', spec, cache)
                self.assertEqual(archive.read_bytes(), data)
                self.assertEqual(install.release_archive('maafw', spec, cache), archive)
                fetch.assert_called_once()
            self.assertEqual((cache / 'maafw.zip').read_bytes(), b'previous runtime')

    def test_download_checksum_failure_blocks_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = {'url': 'https://example.invalid/runtime.zip', 'sha256': '0' * 64}
            def download(_url, destination):
                destination.write_bytes(b'wrong runtime')
            with patch.object(install.urllib.request, 'urlretrieve', side_effect=download):
                with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                    install.release_archive('maafw', spec, Path(folder))

    def test_runtime_install_replaces_old_ui_dlls_and_plugins(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / 'app'
            native = package / 'runtimes/win-x64/native'
            native.mkdir(parents=True)
            (native / 'MaaToolkit.dll').write_bytes(b'old toolkit')
            (native / 'removed.dll').write_bytes(b'old dependency')
            plugins = package / 'plugins/win-x64'
            plugins.mkdir(parents=True)
            (plugins / 'removed.dll').write_bytes(b'old plugin')
            archive = root / 'runtime.zip'
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('bin/MaaToolkit.dll', b'new toolkit')
                target.writestr('bin/MaaFramework.dll', b'new framework')
                target.writestr('bin/plugins/ADB.dll', b'new plugin')
                target.writestr('share/MaaAgentBinary/agent', b'agent')
            install.install_native_runtime(archive, package)
            self.assertEqual((native / 'MaaToolkit.dll').read_bytes(), b'new toolkit')
            self.assertFalse((native / 'removed.dll').exists())
            self.assertFalse((plugins / 'removed.dll').exists())
            self.assertEqual((plugins / 'ADB.dll').read_bytes(), b'new plugin')

    def test_ui_build_rejects_a_different_native_runtime_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'UI.csproj'
            project.write_text('<Project><ItemGroup><PackageReference '
                               'Include="Maa.Framework.Runtimes" Version="5.12.3" />'
                               '</ItemGroup></Project>', encoding='utf-8')
            build_ui.verify_runtime_pin(project, '5.12.3')
            with self.assertRaisesRegex(ValueError, 'differs from release'):
                build_ui.verify_runtime_pin(project, '5.12.2')


if __name__ == '__main__':
    unittest.main()
