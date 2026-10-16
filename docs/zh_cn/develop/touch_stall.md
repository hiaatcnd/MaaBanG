# 演出触控卡顿诊断与恢复

## 已确认的失败证据

舞台挑战第 21 关 `mind of Prominence / EXPERT` 的 round53 记录中，一次 move 调用总耗时 607.260 ms。同期四次完整截图耗时分别为 13.491、13.768、15.590、14.694 ms，提取出的音符位置持续前进。之后重新确认相位额外等待 497.166 ms，并跳过了 108 个触控事件。

这些记录支持“触控提交／完成链路发生阻塞，画面没有在整个 607 ms 内停止推进”。原始连续像素帧未保留，采样间隔也不能排除较短的画面冻结，不能据此证明模拟器所有线程都正常。

原生日志里的 `MaaControllerPostTouchMove` enter/leave 相隔约 402 ms。leave 时间戳是在日志同步输出之前生成的，因此剩余约 205 ms 可能仍包括日志刷新，不能把它当作独立测得的等待时间。新记录在 Python 层用相邻 `perf_counter` 分别测量 `submit_ms`、`wait_ms` 和 `api_ms`，只在内存中积累，结束后写出。

该次 worker 的原生日志只有 MaaTouch 输入候选，并包含成功初始化及同线程的 `MtouchHelper` 调用，支持实际使用 MaaTouch。主 Agent 的 `controller.info.input_methods` 只表示允许的候选，不用于证明实际后端。

## 框架路径与尚未确定的根因

[`MaaControllerPostTouchMove`](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.2/source/Common/MaaController.cpp) 先记录函数日志，再向异步队列提交任务；真正的输入后端在工作线程执行。[`AsyncRunner::post`](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.2/source/MaaFramework/Base/AsyncRunner.hpp) 涉及队列与状态锁。提交阻塞不能直接解释为设备执行触控耗时。

v5.12.2 使用的 MaaUtils commit 为 `0c2556cfcff85eab8c2fa4529d71e37c310a3b78`。[`LoggerUtils.h`](https://github.com/MaaXYZ/MaaUtils/blob/0c2556cfcff85eab8c2fa4529d71e37c310a3b78/include/MaaUtils/LoggerUtils.h) 在全局日志锁内同步输出并刷新。日志 I/O、锁等待、宿主调度和设备输入服务都仍是待验证方向；现有记录没有证明其中某一项是根因。

若继续做日志 A/B，应使用全新子进程：一组正常文件日志，另一组从进程启动起不设置日志目录，两组均关闭 stdout，并验证第二组没有日志文件增长。[`Logger.cpp`](https://github.com/MaaXYZ/MaaUtils/blob/0c2556cfcff85eab8c2fa4529d71e37c310a3b78/source/Logger/Logger.cpp) 的空路径分支在关闭旧文件流之前返回，运行中调用 `set_log_dir("")` 不能可靠关闭已有日志。没有文件输出也仍有格式化及日志锁开销，不能称为完全移除日志。此项 A/B 尚未执行，不能用升级或单元测试代替结论。

## 官方增强触控与版本一致性

[MaaFramework 5.12.3](https://github.com/MaaXYZ/MaaFramework/releases/tag/v5.12.3) 恢复了新版 MuMu 的自动增强触控。[官方设备发现](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaToolkit/AdbDevice/AdbDeviceWin32Finder.cpp) 检查 MuMuManager 版本并返回输入掩码与完整 extras 配置。项目复用这一结果，不重新实现厂商版本探测。

- Python wheel、Toolkit、原生 Framework 和发布清单固定到同一版本；发布检查核对 DLL 哈希与实际加载路径。
- 默认输入使用完整官方发现；具体输入配置保留。代打自动候选去除不支持分触点的 AdbShell，显式选择不支持的方法则开演前失败。
- 普通设备沿用框架的初始化优先级和兼容候选；应用宝虚拟显示不允许退回普通输入。
- 初始化允许的候选不等于实际选中的后端。`playback.json.backend.active_backend` 未获得可靠证据时保持空，诊断同时保留原生日志路径、进程和连接任务 ID。
- 运行时输入失败不切换后端、不重放状态不明的操作；释放触点只尝试一次，清理失败仍保留报告。

## 恢复验证

恢复仅复用本次卡顿、当前相位原点内的完整截图。耗时达到 40 ms 的截图不参与，最后一帧距实际处理时刻必须不超过 100 ms。重新定位后清空旧时间原点的历史；超时或相位不唯一时停止，错过的音符及中断手势不补打。

`tests/fixtures/chart_sync/round53_stall.json` 保存去标识后的录制样本；`test_chart_recovery_recorded.py` 根据记录的截图中点和耗时重建完成时间进行回放。复用四帧后，下一在途帧约 6.89 ms 后即可确认同步；模拟中首个可恢复事件提前约 638 ms，跳过事件从 108 降到 100。这是离线回放结果，不能视为已在游戏内消除卡顿或通过本关。

本次只做连接与截图的本机验证中，5.12.3 官方发现返回增强触控候选；自动候选连接成功，另一次仅允许输入 Extras（掩码 8）的连接也成功，并取得 1280×720 图像。这证明本机增强输入可初始化；验证没有启动游戏或发送触控，仍不代表整曲触控时延和演出结果已验收。

2026-10-15 预览包验收时尚未覆盖现有安装目录；之后本地安装已同步到匹配的原生库和演奏代码。下文预览包记录保留当时的验证范围，不代表新的正式发布包已经验收。

## 2026-10-15 预览包验证边界

前次完整单元测试运行了 679 项，676 项通过，3 项失败涉及当时已缺失的模板：`live/SongPage.png`、`daily/MissionNext.png`、`daily/ThreeStarReveal.png` 和 `daily/TwoStarReveal.png`。清理失败状态修复随后通过了 44 项相关测试。

2026-10-15 接手时先保存工作区状态、差异及原有受控文件哈希，再恢复仍缺失的 151 张受控 PNG。每张图均确认 HEAD 与 index 一致，且只写入不存在的路径；恢复后没有剩余受控文件缺失，原有受控代码未改变。恢复清单及证据保存在本地 `debug/handoff-20261015/restoration.json` 和 `baseline.json`。

资源检查及 JSON Schema 检查通过；构建刷新后的曲库专项 8 项测试通过，编译后的歌曲选择器通过 8 个场景、56 项选择器验证。全量测试与曲库刷新首次并行执行时，曲库测试读到了进程内旧数据与磁盘新数据，产生 1 项不一致；刷新结束后固定文件状态重新运行，最终 **681 项全部通过**（288.969 秒），日志为本地 `debug/handoff-20261015/tests-final.log`。打包后再核对工作区，除了本节验收说明外，代码、资源和曲库均与预览包构建快照一致。

独立预览包 `v0.6.21-touch-preview-20261015` 已构建完成：ZIP CRC、1,878 个文件的清单及解压哈希、287 个源码/资源文件与构建快照的逐字节一致性、运行数据排除及界面转换检查均通过。真实解压后的 `MaaBanG.exe --check-agent` 通过，实际加载 Framework / Toolkit 5.12.3。隔离目录内的整包替换验证通过，测试用户数据不变、过期文件移除、旧包备份保留，更新后 Agent 自检成功。

包 SHA-256：`43bebf133c5566f70201f6733b07e6430e2535ad6b45c20c862e5df9134ca303`。完整包验收记录位于本地 `debug/handoff-20261015/package-verification.json`。构建产生 23 条上游依赖/编译警告、0 个错误，包含既有 NuGet 安全公告；本轮没有调整这些上游依赖。首次构建因虚拟环境缺少 pip 停止，补齐构建环境后在新目录完整重建，失败记录已保留。

本轮未启动游戏、未执行整曲触控或日志 A/B、未打开 UI、未覆盖现有安装、未推送或正式发布。包内文档对应构建快照，本节之后补记的验收结果保存在源码及本地报告中。单元测试和整包验证不证明卡顿根因已消除，也不证明舞台挑战已通过。

## 本次交接保留的改动范围

- 输入与诊断：`playback_controller.py`、`playback_diagnostics.py`，官方能力发现、显式配置保留、触控提交/等待分别计时、失败不重放和清理留证。
- 演出同步：`chart_sync.py`、`chart_worker.py` 及相关录制样本和测试，本次卡顿内已完成截图复用、时效与相位确认、过期手势跳过。
- 入口与准备流程：`chart_live.py`、`direct_chart_live.py`、`online_live.py`、`auto_live.py`、`cp_live.py`、`live_effects.py`、`online_errors.py`，连同既有协力准备和效果设置修改一并保留。
- 依赖与构建：Python 依赖/锁文件、`release-inputs.json`、`release-requirements.txt`、`maatools.config.mts`、`build_ui.py`、`install.py`、`package_smoke.py`，统一 Framework 5.12.3 并校验包内原生库。
- 构建自动刷新 `agent/data/songs_cn.json`、`agent/data/songs_all.json` 和 `docs/data/songs_cn.csv`；刷新前后哈希记录在包验收报告。完整工作区状态和逐文件构建快照保存在本地交接证据目录。

## 缺图来源调查

受支持的会话接口中，最后一份无缺图状态与第一份含 151 张缺图的状态都出现在“支持协力谱面准备开始”对话。接口只给回合时间，保守时间范围为北京时间 2026-10-14 20:45:07 至 21:01:48，不能据此确定实际删除时刻或删除者。

已检查 8 个相关聊天及子代理，没有找到对应的删除命令或文件删除变更。“排查并修复 maabang 演出失败”在第一次检查时已经缺图。当前 NTFS USN 最早保留记录为 2026-10-15 07:02:11，已不覆盖上述时段，无法补充删除事件证据。结论保持未归因；会话 ID、具体工具调用、状态输出及调查限制见本地 `debug/handoff-20261015/deletion-investigation.json`。
