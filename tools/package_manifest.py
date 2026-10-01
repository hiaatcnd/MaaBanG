"""Inventory the complete, immutable release payload for the whole-package updater."""
import hashlib
import json
from pathlib import Path


def write_manifest(root, version):
    root = Path(root)
    files = {
        file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(root.rglob('*'))
        if file.is_file() and file != root / 'package-manifest.json'
    }
    manifest = {'format': 1, 'name': 'MaaBanG', 'version': version,
                'platform': 'win-x64', 'files': files}
    (root / 'package-manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_updater(output):
    import os
    import subprocess
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:exe', '/codepage:65001',
                    '/reference:System.IO.Compression.dll',
                    '/reference:System.IO.Compression.FileSystem.dll',
                    '/reference:System.Web.Extensions.dll', '/reference:System.Windows.Forms.dll',
                    f'/out:{output}', str(Path(__file__).with_name('MaaBanGUpdater.cs'))], check=True)
    # The fixed-runtime helper must support deeply nested installed dependencies.
    Path(str(output) + '.config').write_text('''<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8" /></startup>
  <runtime><AppContextSwitchOverrides value="Switch.System.IO.UseLegacyPathHandling=false;Switch.System.IO.BlockLongPaths=false" /></runtime>
</configuration>
''', encoding='utf-8')
