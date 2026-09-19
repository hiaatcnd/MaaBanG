# 启动性能

打开 MaaBanG 后自动刷新设备列表并连接模拟器。界面先显示，耗时的设备发现放在后台执行；用户无需为了连接再点击一次刷新。扫描和连接期间窗口应保持响应。

## 原因与修复

v0.3.1 的一次本机日志中，界面初始化约 2 秒，接着刷新设备耗时 11.38 秒，连接与 Agent 初始化又用了约 5 秒。MFAAvalonia v2.16.1 的 `RootView.LoadUI` 在 UI 线程调用 `TryReadAdbDeviceFromConfig()`，设备扫描路径同步执行 `AdbDevice.Find()`，期间窗口无法处理消息。

`tools/patches/mfaa-background-startup-connection.patch` 修改普通启动分支：

1. 在 UI 线程恢复已保存设备，供刷新时优先匹配原设备。
2. 使用 `await Task.Run(...)` 在后台刷新设备列表，沿用上游将设备列表更新派发回 UI 线程的实现。
3. 等待刷新完成后，回到原来的自动连接流程，初始化控制器及 Agent。

明确配置的自动运行与定时任务保持上游行为。此修复改善窗口响应，不保证扫描设备和连接本身更快。没有可用设备时仍沿用上游的提示和失败处理。

## 构建

开发打包需安装 .NET 10 SDK，发布包用户不需要安装 SDK。`tools/build_ui.py` 下载并校验固定版本源码、应用补丁、编译 `MFAAvalonia.Core.dll`。可用 `MAABANG_DOTNET` 指定 dotnet 路径。`tools/install.py` 将编译后的 UI 核心放入发布包，其余上游运行组件保持不变。

源码 ZIP、补丁和构建脚本随包放在 `docs/upstream-ui/`；完整构建入口在本项目 `tools/` 下。修改后的 MFAAvalonia 组件沿用 GPL-3.0。

## 验证

分别检查空配置和已保存设备的普通启动：主窗口出现后持续响应；日志依次记录 `Background startup device refresh started`、`Background startup device refresh completed` 和连接结果；无需手动点击便能启动 Agent 并建立连接。多开时检查保存的设备是否保持选中。测试配置不勾选游戏任务，避免启动测试执行游戏操作。

异常及实测耗时记录到 debug/，不能把 Agent 握手检查等同于 UI 响应测试。
