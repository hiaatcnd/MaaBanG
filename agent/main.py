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
    import daily_tasks
    from auto_live import AutoLive
    from chart_live import ChartLive
    from direct_chart_live import DirectChartLive
    from live_presets import LivePresets
    from update_songs import UpdateSongCatalog
    import mining

    from song_catalog import reload_catalog

    def catalog_action(name, action):
        class WithCurrentCatalog(action):
            def run(self, context, argv):
                try:
                    reload_catalog()
                except Exception as exc:
                    print(f'[歌曲列表] {exc}', flush=True)
                    return False
                return super().run(context, argv)
        AgentServer.custom_action(name)(WithCurrentCatalog)

    AgentServer.custom_action("UnlockDefault3DCostumes")(costume_unlock.UnlockDefault3DCostumes)
    catalog_action("AutoLive", AutoLive)
    catalog_action("ChartLive", ChartLive)
    catalog_action("DirectChartLive", DirectChartLive)
    AgentServer.custom_action("LivePresets")(LivePresets)
    AgentServer.custom_action("UpdateSongCatalog")(UpdateSongCatalog)
    for name in ("MineFullCombo", "MineStories", "MineChallenges"):
        catalog_action(name, getattr(mining, name))
    for name in ("ClaimHomeGifts", "ClaimHomeMissions", "ExchangeMichelle", "DailyFreeRecruit"):
        AgentServer.custom_action(name)(getattr(daily_tasks, name))
    Toolkit.init_option("./")

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
