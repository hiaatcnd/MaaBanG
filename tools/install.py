"""Build a standalone Windows x64 package from verified upstream archives."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(version):
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[\w.-]+)?", version):
        raise ValueError("Expected a version such as v0.1.0")
    manifest = json.loads((ROOT / "tools/release-inputs.json").read_text())
    cache = ROOT / "deps/release-cache"
    cache.mkdir(parents=True, exist_ok=True)
    package = ROOT / "install" / f"MaaBanG-{version}-win-x64"
    if package.exists():
        raise FileExistsError(f"Use a fresh output directory: {package}")
    package.mkdir(parents=True)
    for name, spec in manifest.items():
        archive = cache / f"{name}.zip"
        if not archive.exists():
            urllib.request.urlretrieve(spec["url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["sha256"]:
            raise ValueError(f"Checksum mismatch: {archive}")
        dest = package if name == "mfaa" else package / "python" if name == "python" else cache / "framework"
        with zipfile.ZipFile(archive) as source:
            source.extractall(dest)
    shutil.rmtree(package / "runtimes")
    framework = cache / "framework"
    shutil.copytree(framework / "bin", package / "runtimes/win-x64/native",
                    ignore=shutil.ignore_patterns("plugins", "*MaaPiCli*", "*.node"))
    shutil.copytree(framework / "bin/plugins", package / "plugins/win-x64", dirs_exist_ok=True)
    shutil.copytree(framework / "share/MaaAgentBinary", package / "libs/MaaAgentBinary", dirs_exist_ok=True)
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
    interface["agent"] = {"child_exec": "./python/python.exe", "child_args": ["./agent/main.py"]}
    (package / "interface.json").write_text(json.dumps(interface, ensure_ascii=False, indent=4), encoding="utf-8")
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, package)
    shutil.copytree(ROOT / "docs", package / "docs")
    subprocess.run([str(package / "python/python.exe"), str(ROOT / "tools/package_smoke.py"), str(package)], check=True, timeout=90)
    # Smoke logs are build diagnostics, not user configuration.
    if (package / "debug").exists():
        shutil.rmtree(package / "debug")
    for bytecode in package.rglob("__pycache__"):
        shutil.rmtree(bytecode)
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(output / package.name), "zip", package.parent, package.name))
    (output / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    print(f"Built {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    build(parser.parse_args().version)
