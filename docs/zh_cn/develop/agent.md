# Agent 开发

入口为 `agent/main.py`，注册 `UnlockDefault3DCostumes` 自定义动作。`costume_unlock.py` 负责设备流程，`costume_policy.py` 负责可独立测试的业务决策。

开发环境由 `assets/interface.json` 使用 uv 启动 Agent。发布时构建脚本改为包内 `python/python.exe`。Agent 最后一个命令行参数为 UI 传入的 socket identifier；不要手动填固定 socket。

Python 与 UI 原生 MaaFramework 固定为 5.12.2。依赖更新后同步 uv.lock、发布清单和 UI 原生版本，并验证 Agent 握手。

独立包检查：`tools/package_smoke.py`，由构建脚本使用包内 Python 运行；检查资源加载和动作注册，不操作游戏。
