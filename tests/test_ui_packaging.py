"""Keep every patched UI project in the packaged application."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import install


class UiPackagingTests(unittest.TestCase):
    def test_patched_ui_assemblies_replace_upstream_copies(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            ui_build = work / 'ui-build'
            ui_build.mkdir()
            payload = {
                'MFAAvalonia.Core.dll': b'patched core',
                'SukiUI.dll': b'patched sukiui',
            }
            for name, data in payload.items():
                (ui_build / name).write_bytes(data)
            libs = work / 'package' / 'libs'
            libs.mkdir(parents=True)
            # The upstream archive already ships unpatched copies.
            (libs / 'SukiUI.dll').write_bytes(b'upstream sukiui')
            (libs / 'MFAAvalonia.Core.dll').write_bytes(b'upstream core')

            install.copy_ui_assemblies(ui_build / 'MFAAvalonia.Core.dll', work / 'package')

            for name, data in payload.items():
                self.assertEqual((libs / name).read_bytes(), data, name)

    def test_every_patched_project_is_copied(self):
        # A patch that touches a separate UI project must not be silently lost.
        self.assertIn('MFAAvalonia.Core.dll', install.UI_PATCHED_ASSEMBLIES)
        self.assertIn('SukiUI.dll', install.UI_PATCHED_ASSEMBLIES)

    def test_missing_patched_assembly_fails_loudly(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            ui_build = work / 'ui-build'
            ui_build.mkdir()
            (ui_build / 'MFAAvalonia.Core.dll').write_bytes(b'patched core')
            package = work / 'package'
            (package / 'libs').mkdir(parents=True)

            with self.assertRaises(FileNotFoundError):
                install.copy_ui_assemblies(ui_build / 'MFAAvalonia.Core.dll', package)


if __name__ == '__main__':
    unittest.main()
