"""Build the narrowly patched MFAAvalonia core from hash-pinned upstream source."""
import hashlib
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = '4f11c8122de4f43eafc818a368c9956e3b06249c'  # v2.16.1
SOURCE_SHA256 = '087ff4dde522feebaf20e6946392d9f2179d8507aebf12bb2c2f5371885c5e50'


def build_ui():
    cache = ROOT / 'deps/release-cache'
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / 'mfaa-source.zip'
    if not archive.exists():
        urllib.request.urlretrieve(
            f'https://codeload.github.com/MaaXYZ/MFAAvalonia/zip/{REVISION}', archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('MFAAvalonia source checksum mismatch')
    # Re-extract pristine source each time, so patches never accumulate.
    with zipfile.ZipFile(archive) as source:
        source.extractall(cache / 'ui-source')
    source_dir = cache / 'ui-source' / f'MFAAvalonia-{REVISION}'
    patch = ROOT / 'tools/patches/mfaa-background-startup-connection.patch'
    # Normalize patch bytes even when a Windows editor has used CRLF.
    patch_bytes = patch.read_text(encoding='utf-8').encode('utf-8')
    subprocess.run(['git', 'apply', '--check', '-'], input=patch_bytes, cwd=source_dir, check=True)
    subprocess.run(['git', 'apply', '-'], input=patch_bytes, cwd=source_dir, check=True)
    output = cache / 'ui-build'
    dotnet = os.environ.get('MAABANG_DOTNET', 'dotnet')
    subprocess.run([dotnet, 'build', str(source_dir / 'MFAAvalonia/MFAAvalonia.csproj'),
                    '-c', 'Release', '-r', 'win-x64', '-o', str(output),
                    '-p:InformationalVersion=2.16.1-maabang'], check=True)
    core = output / 'MFAAvalonia.Core.dll'
    if not core.is_file():
        raise FileNotFoundError(core)
    return core


if __name__ == '__main__':
    print(build_ui())
