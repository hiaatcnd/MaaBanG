# 常见问题

## Release 是否需要安装 Python？

不需要。下载 Windows x64 ZIP，完整解压后运行 MaaBanG.exe。不要直接在压缩包内运行，也不要单独移动 exe。请使用 MaaBanG.exe 入口，它会为界面和 Agent 设置兼容的临时目录。

## MuMu 无法连接？

先启动模拟器并开启 ADB。端口以模拟器设置为准，本项目开发时使用 127.0.0.1:16416。ADB 路径可指向 MuMu 的 nx_main/adb.exe。游戏应为横屏 16:9。

## 目标数量是新增件数吗？

是每位角色希望达到的总数量。达到目标会跳过；0 不解锁，只返回主页。收集任务全部完成的角色也会快速跳过，不扫描全衣柜。

## 会更换当前穿搭吗？

不会。解锁成功后点击“不变更”，退出试穿时不保存更改。

## Protocol version mismatch

开发环境请统一 Maa Support LoadedVer 与 Python maafw 为 5.12.2。Release 已统一版本，不要混用其他包里的原生 DLL 或 Agent。

## Agent 启动失败 / Bad file descriptor

本机已复现 libzmq 4.3.5 在 Windows AppData 临时目录下的通信初始化错误：同一套原生库使用默认目录失败，改用用户目录下的 `.maabang/temp` 后 Agent 握手成功。

发布入口 MaaBanG.exe 会在启动界面前设置子进程的 TEMP/TMP，Python Agent 也会使用该目录。只影响 MaaBanG 进程树，不修改系统环境变量。仅在 Agent 脚本内修改还不够，因为 UI 会先创建 MaaAgentClient。请从 MaaBanG.exe 启动。

此问题与协议版本不匹配不同；底层行为参考 [ZeroMQ 修复说明](https://github.com/zeromq/libzmq/pull/4734)。若仍失败，附 logs 中的 UI 日志及 debug 日志反馈，不要重复运行消耗任务排查。

## OCR 模型缺失

发布包应包含 resource/model/ocr 下的 det.onnx、rec.onnx 与 keys.txt；缺失时重新完整解压。源码开发执行子模块初始化后运行 tools/configure.py。

## 反馈渠道

请提交到 [MaaBanG Issues](https://github.com/hiaatcnd/MaaBanG/issues)。分享日志和截图前检查个人信息。报告在 debug/costume_unlock，UI 日志在 logs。

## MuMu 已连接但实时画面为空

本机增强截图方式 EmulatorExtras 曾返回空画面，而普通 ADB 截图可获取 1280×720 图像。先确保模拟器窗口已展开；如仍为空，在 UI 的截图方式设置中切换普通 ADB 截图方式后重连。
