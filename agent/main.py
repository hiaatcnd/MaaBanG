import sys
import os
from pathlib import Path

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    if sys.platform == "win32":
        temp = Path.home() / ".maabang" / "temp"
        temp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = os.environ["TMP"] = str(temp)

    from maa.agent.agent_server import AgentServer
    from maa.toolkit import Toolkit
    import costume_unlock

    AgentServer.custom_action("UnlockDefault3DCostumes")(costume_unlock.UnlockDefault3DCostumes)
    Toolkit.init_option("./")

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
