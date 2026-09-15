# 常见问题

## Release 是否需要安装 Python？

不需要。下载 Windows x64 ZIP，完整解压后运行 MFAAvalonia.exe。不要直接在压缩包内运行，也不要单独移动 exe。

## MuMu 无法连接？

先启动模拟器并开启 ADB。端口以模拟器设置为准，本项目开发时使用 127.0.0.1:16416。ADB 路径可指向 MuMu 的 nx_main/adb.exe。游戏应为横屏 16:9。

## 目标数量 x 是新增件数吗？

是每位角色希望达到的总数量。达到目标会跳过；0 不执行。收集任务全部完成的角色也会快速跳过，不扫描全衣柜。

## 会更换当前穿搭吗？

不会。解锁成功后点击“不变更”，退出试穿时不保存更改。

## Protocol version mismatch

开发环境请统一 Maa Support LoadedVer 与 Python maafw 为 5.12.2。Release 已统一版本，不要混用其他包里的原生 DLL 或 Agent。

## Agent 启动失败 / Bad file descriptor

首次发布准备时，本机由自动化命令启动的 AgentClient 出现过 ZeroMQ 的 Bad file descriptor，独立界面日志也记录了该错误；干净 Windows CI 的同版本独立包已通过 Agent 握手。具体本机原因尚未确认。

遇到此错误请保留日志、记录 Windows 与模拟器版本，通过项目 Issue 反馈。请区分该原生初始化错误与上述协议不匹配；不要通过重复执行消耗任务来排查。

## OCR 模型缺失

发布包应包含 resource/model/ocr 下的 det.onnx、rec.onnx 与 keys.txt；缺失时重新完整解压。源码开发执行子模块初始化后运行 tools/configure.py。

## 反馈渠道

请提交到 [MaaBanG Issues](https://github.com/hiaatcnd/MaaBanG/issues)。分享日志和截图前检查个人信息。报告在 debug/costume_unlock，UI 日志在 logs。
