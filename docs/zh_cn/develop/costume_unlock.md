# 默认 3D 演出服装解锁任务

## 使用

Release 用户请先按仓库 README 解压启动，以下为源码开发方式。

1. 安装 uv，并在仓库根目录运行 `uv sync --project agent`。Python 3.12 和依赖由 uv 管理。
2. 在 Maa Support 或通用 UI 中加载 `assets/interface.json`，连接安卓设备，资源选择“中国服”。框架截图需为横屏 1280×720。
3. 选择“解锁默认3D演出服装”，输入每位角色的目标数量（0–999，0 不执行）。
   “服装执行范围”默认为全部成员；选择“指定乐队”后选择一支乐队，或选择“指定成员”后选择一位成员。任务仅处理所选范围，目标数量对其中每位角色生效。
4. 执行模式选“解锁服装”，或先使用“仅检查（不消耗道具）”。仅检查仍会按需求领取已完成的收集奖励。
5. 从游戏主页、乐队菜单、角色评级或服装列表启动。账号登录、数据下载请先完成。

## 行为

- 在所选范围内，按游戏列表顺序处理角色；默认范围为当前支持的 8 队 40 人。
- 打开角色任务的“收集”，领取已完成的服装收集奖励，读取下一档“默认3D演出服装”的实际进度。
- **快速模式：若收集任务全部完成，直接跳过该角色，不统计服装列表。** 即使目标数量较大，也不对这类角色继续补齐。这是用户选定的速度优先行为。
- 达到目标数量的角色跳过。其余角色按列表顺序寻找未持有服装，确认“默认配色”、裁缝套装图标、套装与金币数量。
- 每位角色只读取一次评级进度，计算缺少的件数，然后留在服装页连续解锁。每次识别到“解锁完毕”并关闭弹窗后，成功数加 1、剩余数减 1；不会逐件返回评级，也不会逐件把列表滚回顶部。达到目标后再退出服装页切换角色。确认结果不明或读数不稳定时停止，不重复提交解锁。
- 裁缝套装或金币不足时停止整个任务；没有可用裁缝套装解锁的服装时记录该角色并继续下一位。
- 不购买星石、商店服装或道具，不切换到 Live2D，不解锁额外配色，不保存服装预览为穿搭。
- 用户点击停止后，流程在下一次设备操作前终止。

报告写入运行目录的 `debug/costume_unlock/report-*.json`，列出各角色的原数量、结束数量和停止原因。快速跳过时状态为 `collection_completed`，数量为 `null`（未读取）。

`purchases` 记录每次确认时的费用与成功弹窗确认状态（`success_confirmed`）。`after_basis: initial_progress_plus_success_dialogs` 表示结束数量由初始评级进度加成功次数计算，没有再次读取评级页面。出现错误时同时保存最近的截图。

## 文件

- `assets/resource/pipeline/costume_unlock.json`：任务入口、识别模板及 ROI。
- `assets/resource/image/unlock/`：评级、收集、确认、锁定状态和乐队标签素材。
- `agent/costume_unlock.py`：界面状态检查、OCR、遍历、解锁和报告。
- `agent/costume_policy.py`：数值解析、资源判断和角色顺序。
- `unlock_components.json`：截图组件来源及裁剪坐标。完整截图留在本机 debug 目录。

本地验证：`uv run --project agent python -m unittest discover -s tests -v`。

## 命令行运行

在仓库根目录用 PowerShell 执行，以下示例只检查 目标数量为 60：

```powershell
uv run --project agent python tools/run_costume_task.py 60 --inspect-only --adb "C:\Program Files\Netease\MuMu\nx_main\adb.exe" --address 127.0.0.1:16416
```

把 `60` 改成自己的目标数量；删除 `--inspect-only` 即实际解锁。按 Ctrl+C 停止。
指定范围时，在命令末尾添加 `--band Roselia`（该乐队五人）或 `--member "市谷有咲"`（仅此成员），两者不能同时使用。不传则为全部成员。乐队支持显示名称或内部标识（如 `poppin_party`、`roselia`），成员使用列表里的姓名，`CHU2` 也可用于选择 `CHU²`。
此入口直接调用相同的任务代码，不依赖 Agent 进程通信。

UI 与 Python Agent 必须使用兼容的 MaaFramework 协议。当前开发环境统一为 **5.12.2**（Python 依赖及 maa-tools 均已锁定），Maa Support 的 LoadedVer 也应为 5.12.2。此前 UI 5.12.2 与 Python 5.13.0 混用会报 `Protocol version mismatch`（协议 7 / 8）；遇到此错误先核对两侧版本。本机独立包的 ZeroMQ 初始化错误及 Windows CI 验证结果见 [FAQ](faq.md)。

## 开发验证状态

2026-09-16，MuMu 中国服实机验证：

- 输入 目标数量为 1、仅检查：8 队 40 人全部遍历成功，按需领取已完成的服装收集奖励，无额外服装解锁。
- 经用户授权实际解锁一件默认服装，确认进度增加 1，消耗 200 裁缝套装和 10000 金币。
- 补齐“解锁完毕 → 不变更”的处理。早期单件实机测试返回评级复核过增量；当前连续解锁实现改为按成功弹窗累计，不再逐件返回评级。
- 开发用双分类统计曾实机验证：57 件服装 + 9 件发型/饰品 = 66，与评级进度一致。该统计方法仅保留作开发校验，正式任务的快速模式不会调用。
- 快速模式实机验证通过：羽泽鸫的收集任务全部完成，直接返回角色列表，没有进入服装列表统计。
- 25 项自动测试、JSON Schema、Maa 资源检查通过。道具/金币不足边界使用自动测试验证，没有为了测试而耗尽账号资源。

测试原始记录保存在本机 `debug/probe-all-report.json`、`debug/test-one-before.png`、`debug/test-one-after.png`、`debug/test-one-resources-after.png` 及 `debug/grid-count-log.txt`。Agent 临时目录修复见 FAQ。
