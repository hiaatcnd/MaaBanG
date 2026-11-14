# Agent 开发

Agent 入口为 [`agent/main.py`](../../../agent/main.py)，集中注册服装、日常、演出、挖矿、技能练习、Bestdori、唤醒及退出等自定义动作。本页说明共用开发约定；环境准备和构建入口见 [README 开发指南](../../../README.md#开发指南)。

## 模块与任务扩展

| 模块 | 职责与相关说明 |
| --- | --- |
| `costume_unlock.py`、`daily_tasks.py` | 基础截图、输入、停止检查及服装／日常流程，见[服装解锁](costume_unlock.md)、[日常任务](daily_tasks.md) |
| `auto_live.py`、`chart_live.py`、`online_live.py`、`cp_live.py` | 自动演出、谱面准备与结算、联网房间、CP 挑战，见[自动演出](auto_live.md)、[谱面演出](../chart_live.md) |
| `chart_worker.py`、`chart_sync.py`、`chart_timing.py`、`playback_controller.py` | 独立演奏进程、首键与持续校时、手势时序、输入后端，见[同步研究](chart_live_sync_v2.md)、[触控诊断](touch_stall.md) |
| `frame_recognition.py`、`song_navigation.py`、`song_catalog.py`、`chart_store.py` | 同帧识别复用、找歌、歌曲目录与谱面缓存，见[歌曲目录](song_catalog.md)及下文复用边界 |
| `mining*.py`、`skill_practice.py`、`live_presets.py`、`direct_chart_live.py` | 挖矿、同名技能练习、演出预设与指定谱面入口，见[挖矿](../mining.md)、[技能升级](../../../README.md#同名成员技能升级)、[预设验证](live_presets_validation.md)、[直接演出验证](direct_chart_live_validation.md) |
| `bestdori*.py`、`wakeup*.py`、`exit*.py` | 档案工具、设备／应用启动及退出，见[Bestdori](bestdori_profiles.md)、[唤醒](wakeup.md)、[退出](exit.md) |
| `*_policy.py`、`user_data.py`、`task_logging.py` | 可独立测试的业务决策、用户数据目录及界面可见日志 |

公共流程的主要继承链为 `CostumeFlow → DailyFlow → LiveFlow → ChartLiveFlow → OnlineLiveFlow`。修改基础截图、识别、输入或结算逻辑时，要检查使用它的其他任务；唤醒、退出等入口也有各自的宿主侧流程，不能假定所有任务都沿用这条继承链。业务参数解析和数量决策尽量与设备操作分离，可参考 `costume_policy.py`、`daily_policy.py` 和 `chart_policy.py`。

新增或扩展任务时，按以下位置核对：

1. 定义参数、起始页面、完成条件、停止行为及资源不足／未知状态的处理，再实现动作和可独立测试的决策。
2. 在 `agent/main.py` 注册动作。现有演出和挖矿动作使用 `catalog_action()`，在每次任务开始前重新加载歌曲目录；扩展这些任务时保留该边界，新增依赖目录的动作也需考虑刷新时机。
3. 在 `assets/resource/pipeline/` 定义识别及 `custom_action` 入口，在 `assets/interface.json` 配置任务、选项和默认值。修改已有选项时检查旧配置兼容及必要的迁移。
4. 同步 `tools/check_agent.py` 与 `tools/package_smoke.py` 的预期动作清单，包括独立设置类动作；注册存在不代表任务流程已经验证。
5. 补充与变更有关的正常、异常、停止及点击恢复测试，更新功能说明；具体行为和历史验证留在对应专题，保留日期及适用范围。

## 环境、启动与数据隔离

开发环境由 `assets/interface.json` 使用 uv 启动 Agent。发布时构建脚本改为包内 `python/python.exe`。Agent 最后一个命令行参数为 UI 传入的 socket identifier；不要手动填固定 socket。

Python 与 UI 原生 MaaFramework 固定为 5.12.3。依赖更新后同步 uv.lock、发布清单和 UI 原生版本，并验证 Agent 握手。

独立包检查：`tools/package_smoke.py`，由构建脚本使用包内 Python 运行；检查资源加载和动作注册，不操作游戏。

源码启动自检：

```powershell
uv run --project agent python tools/check_agent.py
```

该命令按 `assets/interface.json` 的启动命令创建真实 Agent 子进程，验证握手、预期动作的注册及正常退出；不连接模拟器、不执行游戏任务。注册入口以 `agent/main.py` 为准，源码与包内自检清单应同步维护。日志保存在 `debug/agent-check/`。自检在创建客户端之前设置兼容临时目录，因此不能代替 VS Code 或其他 UI 自身的启动验证。

发布包可运行 `MaaBanG.exe --check-agent` 自检。日常使用请从 `MaaBanG.exe` 启动；直接启动 `MFAAvalonia.exe` 会绕过临时目录兼容设置。若自检通过但 UI 仍报错，应查看发生失败的 UI 日志，避免把历史错误当作当前故障。

源码默认使用项目内 `.maabang-dev/`，正式包默认使用 `%USERPROFILE%\.maabang`，显式设置的 `MAABANG_DATA_DIR` 优先。测试本地包使用 `tools/run_dev.py --app <包目录>` 隔离配置；该入口运行所选包内的代码与资源，修改源码后仍需重建。包即使放在仓库 `install/` 下，直接双击也不会自动使用开发配置。具体命令见 [README 开发配置隔离](../../../README.md#开发配置隔离)。

`run_dev.py` 为开发命令及子进程统一 UTF-8 输出；其他 Windows 调试入口也应保证输出和读取编码一致。开发界面与正式界面不要同时向同一设备执行任务。

## 所有点击都需考虑结果确认和补点

控制器返回点击成功只表示输入已发送。新增或修改任何点击流程时，都要定义操作后的可观察状态，并考虑未生效、页面动画、延迟响应及有限补点；这项要求不限于材料选择。

- 导航、切换标签、返回和关闭弹窗：先检查是否已经到达目标状态。仍在原页面时重新识别按钮、等待画面稳定后有限补点，避免旧坐标作用于已切换的页面。
- 复选框、开关和材料选择：确认当前仍未选中、目标未变、相关预览数值未变，再补点。任一次观察到选中或数值变化后，都不能因后续单帧识别回退而重按。
- 领取、购买、招募和练习提交：先检查结果、余额或次数、确认弹窗等足以判断是否已执行的证据。结果不明确时保留现场，不能仅因超时重复消费；成功后继续处理结果页。
- 在知道业务前后状态的流程层实现补点，不给底层 `tap` 或 `post_click` 无条件套重试。所有恢复都要有次数或时间上限、停止响应及可读日志。
- 测试至少覆盖首次点击无效后恢复、延迟生效不重按、目标或页面变化停止、重试耗尽，以及提交不重复执行。真实截图回放用于验证识别，不能代替实机验证。

同名成员技能升级的材料选择最多点击 3 次（初次加 2 次补点），每次等待最多 8 秒确认选中且经验增加。补点前用两张新截图确认原成员、原材料、同名加成标签、灰色未选框和不变的经验预览；观察到任何选中或预览变化后停止补点。仍按原有规则批量选卡后一次提交。

培养成员列表在头像检测未找到新成员时，以完整卡框作独立补充定位，避免浅色头像被切成短片段而漏检。打开成员、返回列表均最多点击 3 次，每次最多等待 8 秒；补点前重新确认当前页面和原目标，延迟到达目标页后不再点击。完成前保留已确认成员记录，回顶复扫；滚动是否生效按局部图像变化判断，整个复核最多滚动 100 次。清空检查逐小块核对完整列表区域，不能以全屏白色占比判断；裁切的已处理卡仅在内部图像匹配后排除，未知内容保留截图并报错。材料连续 3 次读取仍未确认时也报错，不能记成完成；提交结果不明时始终停止，不重放消费。现场与复核证据保存在 `debug/skill-practice/<时间>/`。

成员点击前的稳定性以两帧中唯一匹配的内部头像和卡片位置为准，不要求加成标签等独立动画静止。滚动结束判断仍核对局部像素变化，仅在两帧完整卡框、位置和内部头像均匹配时排除该卡上方的加成标签区域；未知卡片、卡片位移及其他区域变化不得被一起忽略。

游戏明确显示“没有可以进行技能练习的成员”时，优先在两张新截图中核对该提示、筛选勾选状态、无遮挡且无残留卡片，然后直接完成，无需回顶或继续滚动。空提示文字只允许遮盖已定位的文字小框，其他区域仍须核对；普通白屏或加载中的空白不能作为完成依据。

## 演奏进程与停止边界

`ChartLiveFlow.play_chart()` 创建独立的 `chart_worker.py` 进程，等待 `armed` 就绪标记，再执行当前任务的开演前复核、效果设置及必要的 FC 复核，最后写入 `start` 授权。不能把进程已启动或已就绪当作允许开演。指定谱面直接演出由用户负责选曲和难度，其复核只确认最后准备页及开演方式；不要将其他模式的曲名、难度或费用核验描述为它已有的功能。

演奏进程负责首键同步、演奏期间的截图观察和触控；联网准备的截图交接由 `monitor_online_worker()` 协调。修改监视逻辑时保持现有交接，避免 Agent 与演奏进程额外并发采集而干扰同步。停止或异常退出时，主流程向仍在运行的子进程写入 `stop`，限时等待后必要时终止；清理和诊断应保留原始失败证据。输入失败或状态不明时不切换后端重放，也不补打已错过的手势，详见[触控诊断与恢复](touch_stall.md)。

## 日志与故障诊断

面向用户的进度和结果通过 `task_logging.log()`、`finish()`、`failure()` 输出，报告中的提示可使用 `record_warning()`。UI 按每行开头的小写 `info:`、`success:`、`warn:`、`error:` 识别日志，普通 `print` 输出可能只进入诊断文件。日志辅助函数中不要增加控制器调用或 Maa 回调，以免干扰演奏时序。

排障时按以下顺序核对证据：

1. 确认实际运行的源码提交或安装包版本、加载的代码／资源位置及日志时间；目录名称和旧日志不能证明当前运行版本。
2. 关联同一次运行的 UI／Agent 日志、任务 `report.json`、错误截图；谱面任务还需查看对应尝试的 `worker.log` 和 `playback.json`。报告目录和字段因任务而异，以任务入口及专题说明为准。
3. 记录失败阶段、已完成轮次和直接错误。区分准备、谱面输入、结果确认、结算返回及后续队列任务；演奏进程的 `input_complete` 只表示输入完成。指定谱面直接演出在此结束，不验证结算；其他模式按各自规则确认完成轮次。
4. 演出错误报告保留 `error_type` 和 `traceback`。错误截图可能在异常后重新采集，不能当作异常瞬间的精确画面，也不能用后续识别覆盖原始错误。
5. 分开陈述观察、推测与验证结果。允许的输入候选不等于实际使用的后端；一次超时或耗时下降不能证明根因已消除。性能结论需说明测量范围、样本和条件，参考[触控卡顿证据](touch_stall.md#已确认的失败证据)。

## 日常验证与交付

先按变更范围运行相关回归；识别变化再使用固定截图与实际 OCR／模板回放。涉及公共流程或正式发布时，扩大到相关任务或全量测试，并按变更执行资源、界面 Schema、包内运行环境等检查。仅修改文档时检查内容与链接即可，不为此操作游戏或重建整包。

从仓库根运行，例如：

```powershell
uv run --project agent python -X utf8 -m unittest discover -s tests -p 'test_live_presets.py' -v
uv run --project agent python -X utf8 -m unittest discover -s tests -v
```

第一条是演出预设的定向回归示例；按修改内容替换文件名。资源检查及依赖准备见 [README 检查与数据更新](../../../README.md#检查与数据更新)，Schema 命令与依赖见 [CI 检查配置](../../../.github/workflows/check.yml)。不同验证层次分别报告：

| 验证 | 能说明的范围 |
| --- | --- |
| 业务／流程单测 | 指定输入和替身下的决策、状态转换与恢复行为 |
| 固定截图回放 | 指定图片和模型下的识别及流程；注明实录或构造样本 |
| Agent／包检查 | 握手、动作注册，以及相应包检查覆盖的资源和运行依赖 |
| UI／更新验证 | 所测界面交互、配置迁移、整包替换及回退，不等于游戏任务通过 |
| 实机游戏验证 | 指定设备、版本、模式和场景下的实际结果；注明人工接管及未覆盖步骤 |

验证记录应写明运行命令、源码／资源版本、通过／失败／跳过数量及跳过原因。缺少 OCR 模型、私人截图或对应操作系统时，部分用例会跳过；例如 `test_recent_coop_failures.py`、`test_mission_tabs.py` 中的私人失败图回放。CI 通过不能表述为全部历史截图均已回放，也不能推定游戏问题已修复。当前可执行命令和历史实测记录分开维护，不把旧实验状态当作当前功能保证。

构建会强制刷新曲库并改写相关数据；`tools/install.py`、目录更新与读取同一组文件的回归测试应顺序执行，确认数据稳定后再验证同一源码／资源快照。[既往记录](touch_stall.md#2026-10-15-预览包验证边界)曾出现测试期间刷新目录，导致进程内旧数据与磁盘新数据不一致。

发布步骤沿用 [README 打包与发布](../../../README.md#打包与发布)及 [PR 规范](pull_request_guidelines.md)，整包更新见[更新说明](updater.md)。源码检查、正式包验收、本地安装更新和实机验证分别记录，不能相互替代。原始日志、私人截图、本地配置和缓存留在本地；需要共享的回归素材应先检查并去除个人信息。

## 演出准备的截图与识别复用

`LiveFlow` 家族使用 `FrameRecognitionMixin`。同一张截图上，节点定义及覆盖参数完全相同的 OCR / 模板识别只运行一次，包括确定的未命中。每次查询仍检查停止请求和当前节点定义；框架未能启动识别或抛出异常时不缓存结果。新截图尝试、图像对象替换和输入都会使缓存失效，控制器复用同一图像缓冲区时也不跨截图复用。直接调用控制器的触控或前后台切换前，应调用 `invalidate_frame_recognition()`。

缓存只用于静态 ROI 的纯识别；自定义、组合、引用其他识别结果位置的 ROI 和 `color_filter` 不缓存。缓存命中不会更新框架内部的最近识别位置，因此其他节点不能依赖被缓存节点的历史位置；新增这类依赖时需重新设计复用边界。资源图片在任务内保持不变。

截图通过明确的 `fresh=False` 交接：调用方刚确认页面，且中间没有输入或耗时等待时才复用。自动演出复用准备帧读取次数、火数及开演条件；谱面演出合并相邻准备页检查；挑战演出由读歌名步骤承担页面确认。联网 `await_final()` 的成功帧可作为认歌的第一个样本，第二个稳定样本仍等待后重新截图。谱面下载、演奏进程就绪后的身份/难度/费用核验及点击后的结果核验保留新截图，不能用缓存命中代替多个稳定样本。

`matching_songs()` 在扫描候选歌曲时只做一次全目录解析，保留候选顺序、精确重名及模糊歧义拒绝。仅缓存标题字符串的纯规范化，不缓存可动态更新的目录匹配结果。

### ONLY_UNFC 的一次性证据

“仅避免未 FC 歌曲 FC”会在在线认歌的两个独立样本中同时读取完整星排。仅当歌曲与整排状态一致，才记录绑定当前图像、歌曲、所选难度对应的 selection 和房间尝试对象的证据；确认后一秒内可供首次 FC 决策消费一次。截图尝试、输入和前后台切换均使其失效；慢下载、下载期间截图、难度切换或其他不满足条件的情况回退到完整双帧检查。关闭或始终开启“避免 FC”时不收集此证据。

演奏进程就绪后仍重新核验身份、难度和费用，只把已核验的当前图作为 FC 复核第一样本，等待后再拍第二张。在线 FC 样本都需确认最终页、全目录唯一歌曲及所选难度，最多检查四个样本。效果开关输入后也重新核验；无法确认时不得授权已编译为正常全连的演奏进程。

### 维护与断线检查

房间外的 `snap()`、已确认结果后的结算与断线恢复通过 `room_error()` 分类，维护或账号限制优先抛出 `OnlineFatal`。默认两节点仅关键词不同，合并完整区域 OCR；同帧后续断线查询复用识别结果。不同 ROI、阈值、索引、模型或自定义识别不满足合并条件时，`snap()` 的 `fatal_only` 只检查维护，断线留到需要时独立判断，保持原调用时机。

新截图、输入和配置变化仍重新识别，不降低检查频率、不缩小区域、不改动模型，也不以正常页面外观相近为由跨帧缓存阴性结果。联网准备原本已采用每帧一次的合并判断，这项改动主要减少结算与恢复中同帧的重复工作。

`tests/test_frame_recognition.py`、`test_live_preparation.py`、`test_online_preparation.py`、`test_online_full_combo_reuse.py`、`test_online_error_reuse.py` 和 `test_song_title_matching.py` 覆盖复用、证据失效、停止、截图失败、配置回退、歌曲歧义及禁止错误开演。测试中的真实截图回放只验证识别与流程，不能代替实机验证。

## 离线回放工具

从仓库根运行。先按 [README 开发指南](../../../README.md#开发指南)初始化 `assets/MaaCommonAssets` 子模块，并运行 `uv run --project agent python tools/configure.py` 准备开发资源中的默认模型。工具使用固定截图和离线控制器，不连接游戏设备；报告写入本地 `debug/`，不要提交包含个人路径或私人截图的报告。

通用准备回放使用仓库已有的协力、竞演截图。加载遮挡用例由已有截图局部拼接，属于构造样本。对照组关闭复用，用于比较相同流程的重复截图/识别开销；计时包括框架调用和结果构建，不是模型纯推理，也不含设备传输、网络或真实演奏进程启动。

```powershell
uv run --project agent python -X utf8 tools/benchmark_live_preparation.py --repeats 3 --output debug/benchmarks/preparation.json
```

ONLY_UNFC 工具调用真实 `play_chart()`，以立即就绪、不会演奏的替身进程隔离准备逻辑。以下示例在两个阶段重复使用公开 fixture，只验证固定画面；研究真实阶段差异时用自己保存的两张 1280×720 final/ready 截图，并使模式、难度和火数设置与图片一致。可先加 `--inspect-only` 检查图片及星排。

```powershell
uv run --project agent python -X utf8 tools/benchmark_only_unfc_preparation.py --case tests/fixtures/coop/ready.png tests/fixtures/coop/ready.png --mode coop --difficulty expert --fire 0 --device cpu --repeats 3 --output debug/benchmarks/only-unfc.json
```

守卫工具比较独立检查与合并检查，预热后交错运行至少三轮，检查类别和命中文本是否一致。创建 `debug/` 目录，将以下公开 fixture 清单保存为 `debug/guard-cases.json`。图片相对路径按仓库根解析，`expected_category` 可为 `OL_Fatal`、`OL_Disconnected` 或 `null`：

```json
{
  "cases": [
    {"id": "disconnected", "image": "tests/fixtures/online_recovery/connection_failed.png", "expected_category": "OL_Disconnected", "origin": "recorded fixture"},
    {"id": "matching", "image": "tests/fixtures/online_recovery/matching_timeout.png", "expected_category": null, "origin": "recorded fixture"}
  ]
}
```

```powershell
uv run --project agent python -X utf8 tools/benchmark_online_guards.py --cases debug/guard-cases.json --repeats 3 --output debug/benchmarks/guards.json
```

工具显式使用 CPU，在 Windows 以低优先级子进程运行，整批上限五分钟。后台负载会影响耗时；局部 OCR 减少不能直接等同整段游戏等待减少。维护与账号限制仍缺少确认过的真实正例，模拟文本分类单测不能作为这些弹窗的实图召回率证据。

## OCR 模型对照与并行边界

`tools/ocr_benchmark_cases.json` 引用仓库已有的 13 张截图，包含 44 项检查：9 项歌曲、3 项难度标签集合、25 项文字/数字及7项负例。模型工具分别在独立进程和临时命名模型目录中运行，不替换当前资源；使用相同 ROI/阈值、CPU、完整预热和无结果缓存的框架识别。模型文件按来源逐一记录 SHA256。默认模型仍为 PP-OCRv6 small。

```powershell
uv run --project agent python -X utf8 tools/benchmark_ocr_models.py --models v6_small v5_mobile v6_tiny v4_zh_cn v4_det_v6_rec --repeats 3 --low-priority --output debug/benchmarks/models.json
```

`--inspect-only` 只检查候选模型文件与来源，不运行 OCR。每个模型最多运行五分钟；超时或未完成不等于模型无法加载。应按模型分别报告已完成检查、失败项和耗时，不以少量样本外推总体识别率。

一次本地对照中，公开 44 项检查由 v6 small、v5 mobile 全部通过；v6 tiny 为35/44，v4中文为39/44，v4检测＋v6 small识别/字典为43/44。这是业务检查通过率，包含歌曲规范化和模糊消歧，不是字符准确率。混合组合仍有长日英混排标题被检测器切碎的问题，保留识别器和字典并不能消除检测差异；负例也不能证明维护/断线召回率。额外私人截图观察不计入此公开清单。模型替换还需更广泛的歌曲、关键数字及异常正例验证。

MaaCommonAssets 的 v4 中文候选是由其 README 所列官方 Paddle 模型转换的 ONNX 三件套；框架不能直接加载原始 tar。识别模型须与有序字典配套，检测器能定位多语种文本不代表中文字典支持日语。v6 tiny 的官方语言范围也不含日文，因此不能只按速度选择模型。

并行边界依据固定 MaaFramework 5.12.3 源码：[OCRer 的进程内静态锁](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaFramework/Vision/OCRer.h#L107)覆盖检测识别、纯识别与批量识别的模型调用，多线程或多个 Context/Resource 不能让同进程推理真正并行。[Agent 通信实现](https://github.com/MaaXYZ/MaaFramework/blob/v5.12.3/source/MaaAgentClient/Client/AgentClient.cpp#L2721-L2725)也记录了并发接收响应可能被抢走的问题。单次 CPU 推理自身已有内部线程。独立常驻进程方案还需验证各自模型的资源占用、同帧结果归属及所有安全检查完成后的统一决策；本次未验证其实机收益，也未把现有 Agent Context 改为多线程。
