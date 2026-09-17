# Agent 开发

入口为 `agent/main.py`，注册 `UnlockDefault3DCostumes` 自定义动作。`costume_unlock.py` 负责设备流程，`costume_policy.py` 负责可独立测试的业务决策。

开发环境由 `assets/interface.json` 使用 uv 启动 Agent。发布时构建脚本改为包内 `python/python.exe`。Agent 最后一个命令行参数为 UI 传入的 socket identifier；不要手动填固定 socket。

Python 与 UI 原生 MaaFramework 固定为 5.12.2。依赖更新后同步 uv.lock、发布清单和 UI 原生版本，并验证 Agent 握手。

独立包检查：`tools/package_smoke.py`，由构建脚本使用包内 Python 运行；检查资源加载和动作注册，不操作游戏。

源码启动自检：

```powershell
uv run --project agent python tools/check_agent.py
```

该命令按 `assets/interface.json` 的启动命令创建真实 Agent 子进程，验证握手、六个动作的注册及正常退出；不连接模拟器、不执行游戏任务。日志保存在 `debug/agent-check/`。自检在创建客户端之前设置兼容临时目录，因此不能代替 VS Code 或其他 UI 自身的启动验证。

发布包可运行 `MaaBanG.exe --check-agent` 自检。日常使用请从 `MaaBanG.exe` 启动；直接启动 `MFAAvalonia.exe` 会绕过临时目录兼容设置。若自检通过但 UI 仍报错，应查看发生失败的 UI 日志，避免把历史错误当作当前故障。
