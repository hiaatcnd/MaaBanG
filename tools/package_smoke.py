"""Run with the packaged interpreter; no emulator or account resources needed."""
import json
import os
from pathlib import Path
import sys
import subprocess
import hashlib

package = Path(sys.argv[1]).resolve()
os.chdir(package)
assert Path(sys.executable).resolve().is_relative_to(package)
interface = json.loads(Path("interface.json").read_text(encoding="utf-8"))
assert interface["agent"]["child_exec"] == "./python/python.exe"
assert Path("MFAAvalonia.exe").is_file()
assert Path("MaaBanG.exe").is_file()
assert Path(os.environ["TEMP"]).resolve() == Path.home() / ".maabang" / "temp"
assert Path("resource/model/ocr/rec.onnx").is_file()
for library in ("MaaFramework.dll", "MaaAgentClient.dll", "MaaAgentServer.dll"):
    native = package / "runtimes/win-x64/native" / library
    python_native = package / "python/Lib/site-packages/maa/bin" / library
    assert hashlib.sha256(native.read_bytes()).digest() == hashlib.sha256(python_native.read_bytes()).digest(), library
from maa.library import Library
from maa.resource import Resource
from maa.toolkit import Toolkit
import costume_unlock
import costume_policy
import main

assert Library.version().lstrip("v") == "5.12.2", Library.version()
Toolkit.init_option(str(package / "debug"))
resource = Resource()
assert resource.post_bundle(str(package / "resource")).wait().succeeded
assert costume_policy.parse_target("0") == 0
from maa.agent_client import AgentClient

client = AgentClient()
client.set_timeout(15000)
assert client.bind(resource)
process = subprocess.Popen([str(package / interface["agent"]["child_exec"]),
                            *interface["agent"]["child_args"], client.identifier], cwd=package)
try:
    assert client.connect(), "Agent handshake failed"
    assert "UnlockDefault3DCostumes" in client.custom_action_list
finally:
    client.disconnect()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
print("Package smoke passed: embedded Python, Maa 5.12.2, resources + OCR, Agent handshake")
