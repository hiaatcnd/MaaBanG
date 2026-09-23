"""Check the development Agent handshake without connecting to a game device."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    if os.name == "nt":
        temp = Path.home() / ".maabang" / "temp"
        temp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = os.environ["TMP"] = str(temp)

    from maa.agent_client import AgentClient
    from maa.resource import Resource
    from maa.toolkit import Toolkit

    output = root / "debug" / "agent-check"
    output.mkdir(parents=True, exist_ok=True)
    Toolkit.init_option(str(output))
    config = json.loads((root / "assets/interface.json").read_text(encoding="utf-8"))["agent"]
    executable = shutil.which(config["child_exec"])
    if not executable:
        raise RuntimeError(f"Agent executable not found: {config['child_exec']}")
    resource = Resource()
    client = AgentClient()
    client.set_timeout(15000)
    if not client.bind(resource):
        raise RuntimeError("Agent client bind failed")
    with (output / "agent-output.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [executable, *config["child_args"], client.identifier],
            cwd=root / "assets", stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            if not client.connect():
                raise RuntimeError(f"Agent handshake failed; exit={process.poll()}; see {output}")
            actions = set(client.custom_action_list)
            expected = {"UnlockDefault3DCostumes", "ClaimHomeGifts", "ClaimHomeMissions",
                        "ExchangeMichelle", "DailyFreeRecruit", "AutoLive", "ChartLive",
                        "MineFullCombo", "MineStories", "MineChallenges"}
            if not expected.issubset(actions):
                raise RuntimeError(f"Missing actions: {sorted(expected - actions)}")
            print("Agent handshake passed. Registered actions:", ", ".join(sorted(actions)))
        finally:
            client.disconnect()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        if process.returncode:
            raise RuntimeError(f"Agent exited with {process.returncode}; see {output}")
    print(f"Agent exited normally. Logs: {output}")


if __name__ == "__main__":
    main()
