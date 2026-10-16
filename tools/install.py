"""Build a standalone Windows x64 package from verified upstream archives."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from build_ui import build_ui
from package_manifest import build_updater, write_manifest
from song_interface import install_bindings

ROOT = Path(__file__).resolve().parents[1]


def release_archive(name, spec, cache):
    """A dependency upgrade must not reuse an archive from the previous pin."""
    archive = cache / f"{name}-{spec['sha256']}.zip"
    if not archive.exists():
        legacy = cache / f"{name}.zip"
        if legacy.exists() and hashlib.sha256(legacy.read_bytes()).hexdigest() == spec["sha256"]:
            shutil.copy2(legacy, archive)
        else:
            urllib.request.urlretrieve(spec["url"], archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["sha256"]:
        raise ValueError(f"Checksum mismatch: {archive}")
    return archive


def install_native_runtime(archive, package):
    # A fresh extraction prevents removed DLLs/plugins from surviving an upgrade
    # in the shared release cache. The destination belongs to this new package.
    with tempfile.TemporaryDirectory(prefix="maabang-framework-") as folder:
        framework = Path(folder)
        with zipfile.ZipFile(archive) as source:
            source.extractall(framework)
        shutil.rmtree(package / "runtimes")
        shutil.copytree(framework / "bin", package / "runtimes/win-x64/native",
                        ignore=shutil.ignore_patterns("plugins", "*MaaPiCli*", "*.node"))
        plugins = package / "plugins/win-x64"
        if plugins.exists():
            shutil.rmtree(plugins)
        shutil.copytree(framework / "bin/plugins", plugins)
        shutil.copytree(framework / "share/MaaAgentBinary", package / "libs/MaaAgentBinary", dirs_exist_ok=True)


def build(version):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[\w.-]+)?", version):
        raise ValueError("Expected a version such as v0.1.0")
    manifest = json.loads((ROOT / "tools/release-inputs.json").read_text())
    cache = ROOT / "deps/release-cache"
    cache.mkdir(parents=True, exist_ok=True)
    release = ROOT / "install" / f"MaaBanG-{version}-win-x64"
    if release.exists():
        raise FileExistsError(f"Use a fresh output directory: {release}")
    # Every release (including prereleases and CI previews) must fetch current
    # CN choices, all-server recognition metadata and their generated UI lists.
    # Never silently ship the previous snapshot when the refresh fails.
    subprocess.run([sys.executable, str(ROOT / "tools/update_song_catalog.py")],
                   cwd=ROOT, check=True)
    package = release / "app"
    package.mkdir(parents=True)
    for name, spec in manifest.items():
        archive = release_archive(name, spec, cache)
        if name == "maafw":
            install_native_runtime(archive, package)
            continue
        dest = package if name == "mfaa" else package / "python"
        with zipfile.ZipFile(archive) as source:
            source.extractall(dest)
    # Keep upstream managed dependencies and use our verified Framework/Toolkit
    # runtime pin for both the UI and Python wheel.
    shutil.copy2(build_ui(), package / 'libs/MFAAvalonia.Core.dll')
    shutil.copytree(ROOT / "assets/resource", package / "resource")
    if not (package / "resource/model/ocr").exists():
        shutil.copytree(ROOT / "assets/MaaCommonAssets/OCR/ppocr_v6/small", package / "resource/model/ocr")
    shutil.copytree(ROOT / "agent", package / "agent",
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"))
    subprocess.run([sys.executable, "-m", "pip", "install", "--require-hashes",
                    "--only-binary=:all:", "--target", str(package / "python/Lib/site-packages"),
                    "-r", str(ROOT / "tools/release-requirements.txt")], check=True)
    (package / "python/python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\n../agent\nimport site\n", encoding="utf-8")
    interface = json.loads((ROOT / "assets/interface.json").read_text(encoding="utf-8"))
    interface["version"] = version
    interface["icon"] = "MaaBanG.ico"
    shutil.copy2(ROOT / "assets/branding/MaaBanG.ico", package / "MaaBanG.ico")
    interface["agent"] = {"child_exec": "./python/python.exe", "child_args": ["./agent/main.py"]}
    (package / "interface.json").write_text(json.dumps(interface, ensure_ascii=False, indent=4), encoding="utf-8")
    install_bindings(package)
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, release)
    readme_images = release / 'assets/branding'
    readme_images.mkdir(parents=True)
    shutil.copy2(ROOT / 'assets/branding/MaaBanG.png', readme_images)
    shutil.copytree(ROOT / "docs", release / "docs")
    source_bundle = release / 'docs/upstream-ui'
    source_bundle.mkdir()
    shutil.copy2(cache / 'mfaa-source.zip', source_bundle)
    for patch in (ROOT / 'tools/patches').glob('mfaa-*.patch'):
        shutil.copy2(patch, source_bundle)
    shutil.copy2(ROOT / 'tools/build_ui.py', source_bundle)
    shutil.copy2(ROOT / 'tools/MaaBanGUpdate.cs', source_bundle)
    shutil.copy2(ROOT / 'tools/MaaBanGSongCatalog.cs', source_bundle)
    (package / "tools").mkdir()
    build_updater(package / 'tools/MaaBanGUpdater.exe')
    shutil.copy2(ROOT / "tools/package_smoke.py", package / "tools/package_smoke.py")
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll",
                    f"/win32icon:{ROOT / 'assets/branding/MaaBanG.ico'}",
                    f"/out:{release / 'MaaBanG.exe'}", str(ROOT / "tools/MaaBanGLauncher.cs")], check=True)
    subprocess.run([str(release / "MaaBanG.exe"), "--check-agent"], check=True, timeout=90)
    # Smoke logs are build diagnostics, not user configuration.
    if (package / "debug").exists():
        shutil.rmtree(package / "debug")
    for bytecode in package.rglob("__pycache__"):
        shutil.rmtree(bytecode)
    write_manifest(release, version)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(output / release.name), "zip", release.parent, release.name))
    (output / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    build(parser.parse_args().version)
