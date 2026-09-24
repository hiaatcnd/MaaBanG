<p align="center">
  <img src="assets/branding/MaaBanG.png" alt="MaaBanG 图标" width="160" height="160">
</p>

<h1 align="center">MaaBanG</h1>

<p align="center">
  基于 MaaFramework 的 BanG Dream 手游中国服自动化助手
</p>

<p align="center">
  <a href="https://github.com/hiaatcnd/MaaBanG/releases/latest">下载软件</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#功能与配置">功能说明</a> ·
  <a href="docs/zh_cn/develop/faq.md">常见问题</a> ·
  <a href="https://github.com/hiaatcnd/MaaBanG/issues">反馈问题</a>
</p>

---

## 功能一览

| 功能 | 支持内容 |
| --- | --- |
| Maa代打演出（实验性） | 按谱面操作、正态随机偏差、可选道具补火；自由演出、自选／课题巡演、团队联网演出 |
| 清火自动演出 | 自由演出、自由巡演；从已解锁歌曲按乐队选歌，配置难度、火数和最大演出次数 |
| 挖矿 | 自由演出未 FC 谱面、成员未读小故事／回忆小故事、主舞台／特别舞台挑战；配置演出数、材料解锁与练习星级 |
| 解锁3D演出服饰 | 全部 8 队 40 人，或指定乐队、成员；按目标数量解锁，领奖后不换装 |
| 主页礼物 | 一键领取，直到礼物箱为空 |
| 任务奖励 | 领取期间限定、限时招募券、通常任务、每月、邀请邦友、EX 任务中已达成的奖励 |
| 米歇尔贴纸交换 | 按成员、表情、服装、背景、其他分类选择交换项目 |
| 每日免费招募 | 领取当天剩余的每日免费演出招募次数 |

**所有任务正常完成后返回游戏主页。** 无奖励可领、资源不足、次数用尽或没有待处理项目时也会返回；遇到未知弹窗或无法确认的结果时停止并保留现场。

## 快速开始

### 1. 下载与解压

在 [Releases](https://github.com/hiaatcnd/MaaBanG/releases/latest) 下载 `MaaBanG-v版本号-win-x64.zip`，**完整解压**到可写目录，再双击根目录的 **`MaaBanG.exe`**。

- 支持 **Windows x64**，目前在 **MuMu 模拟器**上验证。
- 发布包内置界面、Python、MaaFramework 和 OCR，无需安装 VS Code、Python、uv 或 .NET SDK。
- 保留完整目录结构；更新时解压完整新包，不要只复制启动程序。
- 配置和谱面缓存保存在用户目录 `%USERPROFILE%\.maabang`，换包或升级后继续复用。首次启动会优先迁移当前包的配置，并合并当前目录旁其他 MaaBanG 包中的谱面缓存；原文件保留，已有共享配置不会被旧包覆盖。
- `config/` 保存任务、设备和实例设置，`cache/charts/` 保存谱面。需要独立配置时，可在启动前通过 `MAABANG_DATA_DIR` 指定另一个数据目录。请退出正在运行的旧版本后再启动新版。
- 若提示缺少 VC++ 运行库，可使用 `app/` 内上游提供的依赖安装脚本。

### 2. 连接模拟器

启动模拟器和游戏，完成登录及更新，保持 **横屏 16:9**，建议分辨率为 **1280 × 720**。

在 MaaBanG 中选择“安卓端”，刷新设备列表并选择目标，资源选择“中国服”。MuMu 也可手动填写连接信息：

| 设置 | 示例 |
| --- | --- |
| ADB 程序 | `C:\Program Files\Netease\MuMu\nx_main\adb.exe` |
| ADB 地址 | `127.0.0.1:16416` |

地址和路径以你自己的模拟器设置为准，多开时请确认选中了正确的实例。

> **启动时会自动刷新设备列表并连接模拟器。** 从 v0.3.2 起，启动优化将耗时的设备扫描放到后台，窗口可在扫描、连接期间继续响应；上次保存的设备会优先用于匹配。首次使用、多开或切换模拟器时，请确认自动选择的目标是否正确。技术说明见[启动性能](docs/zh_cn/develop/startup.md)。

### 3. 配置并运行

1. 进入游戏主页，关闭公告、活动等弹窗。
2. 添加需要的任务并检查选项；日常任务默认不勾选。
3. 两种演出任务均无需收藏目标歌曲；歌曲须已在游戏内解锁。
4. 点击开始，等待任务完成并返回主页。

服装任务还支持从乐队菜单、角色评级或服装列表启动，其他任务请从主页启动。

## 功能与配置

### Maa代打演出（实验性）

新增独立的“Maa代打演出”任务，可选择中国服歌曲和实际开放的难度，无需预先收藏。自由巡演的三首歌曲和难度分别配置，课题巡演自动读取固定歌曲并使用分别指定的难度。提供六个非零随机偏差档位、每首 0–3 火、可选回复道具补火和最大演出次数。

团队演出由游戏随机选曲；选择 SPECIAL 时，歌曲没有对应难度则降为 EXPERT。识别最终歌曲和难度后，优先读取共享缓存，缺失时只下载该歌曲的对应谱面，不再提前下载整个曲库。谱面准备超过 12 秒、下载或校验失败时退出房间并停止任务；准备期间持续检查房间状态，校验完成后才提交准备。掉房自动重进，房间 3 分钟未开演也退出重进，只有成功结算才计次。协力演出暂缓开放。

任务在首次演出前自动调整速度、皮肤和演出画面，连续演出复用设置，并在演奏中持续校时；不保证全连，可能出现 MISS。v0.4.0 起提供此实验性功能，使用 MuMu 安卓15 的快速截图接口，首次使用谱面需联网。要求 16:9 横屏，支持当前已确认的 2560×1440；内部统一缩放至 1280×720 识别，无需降低模拟器分辨率。

[Maa代打演出使用说明](docs/zh_cn/chart_live.md)

### 挖矿

提供三个独立任务：扫描歌曲星星并以极小偏差补 Full Combo；筛选未读成员小故事、跳过阅读并处理奖励；使用推荐编组逐个推进主舞台或特别舞台。自由演出可多选挖矿难度；两类挖矿演出均可选每首 0–3 火、火不足停止或使用回复道具，最大演出数按实际尝试计数。故事材料解锁和练习满级分别配置，默认关闭；练习可限制成员星级，并启用游戏的自动特训以提升等级上限。

[挖矿配置与运行说明](docs/zh_cn/mining.md)

### 清火自动演出

直接选择歌曲及实际演出难度。程序在游戏内根据该曲所属乐队和 EXPERT 等级自动缩小查找范围，无需手动配置筛选，也无需收藏。

| 选项 | 说明 |
| --- | --- |
| 演出模式 | 自由演出，或巡回演出中的自由巡演；自由巡演会选择同一首歌三次 |
| 演出歌曲 | 使用 Bestdori 中国服离线目录，同名歌曲按乐队区分 |
| 演出难度 | 随歌曲变化，只提供目录中中国服已开放的难度；旧配置中不可用的 Special 回退至 Expert |
| 每首消耗火数 | 0–3 火 |
| 火不足策略 | 停止，或降低到剩余火数，最低为 0；停止策略下，自由巡演需有足够完成三首的火数 |
| 最大演出次数 | 留空表示不限，或填写 1–999；自由演出一首计一次，自由巡演三首计一次 |

程序使用游戏内自动演出，每首开始前确认自动模式及剩余额度。按当前任务规则，每日自动演出上限为 10 首，自由巡演每轮需要 3 次；额度不足时不开始新一轮。最大演出次数留空时，任务仍会在自动额度不足或火不足策略要求停止时结束。

演出期间每 10 秒检查一次状态，结算后处理奖励、评级升级和演出后对话。不会补充火、购买自动次数或切换到手动打歌。

已逐首核对当前中国服自由演出列表的 736 首歌曲识别，并实测同名歌曲消歧；这不代表逐曲演奏或全部难度验证。游戏更新后的文字或目录差异仍可能导致找歌失败。[全曲识别核对报告](docs/zh_cn/develop/free_song_recognition_audit.md)

[自动演出说明](docs/zh_cn/develop/auto_live.md) · [中国服歌曲目录](docs/data/songs_cn.csv) · [目录来源与筛选规则](docs/zh_cn/develop/song_catalog.md)

### 解锁3D演出服饰

可选择全部角色、指定乐队或指定成员，并设置每位角色的目标数量。

**目标数量指每位角色最终希望达到的数量。** 例如当前已解锁 5 件、目标为 8 件，本次会再解锁 3 件。范围为 0–999，默认 0，不解锁，只返回主页。

- **仅检查模式**不消耗解锁道具，但仍会领取已完成的服装收集奖励。
- 根据角色评级中的默认 3D 服装进度计算差额，留在服装列表连续解锁，不逐件返回评级页面。
- 解锁消耗**裁缝套装和金币**，不足时停止整个任务；每位成员处理完成后领取已达成的收集奖励。
- **解锁后选择“不变更”，不换上新服装，也不保存试穿。**
- 快速模式遇到“收集任务全部完成”的角色会直接跳过，即使目标数量更大，也不会扫描该角色的全部衣柜。

[服装解锁说明](docs/zh_cn/develop/costume_unlock.md)

### 奖励、交换与免费招募

主页礼物和任务奖励只领取已达成的奖励，不会购买月度付费奖励或自动建立邀请关系。

**米歇尔贴纸交换**支持多选成员、表情、服装、背景、其他，默认全选。只处理勾选分类中的可交换项目，并实际消耗贴纸；贴纸不足时停止。可交换项目位于列表顶部，当前分类已无可交换项目时不再向下翻页。全部取消选择时只返回主页。

**每日免费招募**只使用当天剩余的每日三次免费演出招募次数，不使用星石或招募券。

[日常任务说明](docs/zh_cn/develop/daily_tasks.md)

## 文件位置与问题反馈

发布包根目录的唯一启动入口为 `MaaBanG.exe`，界面和运行依赖位于 `app/`。

| 内容 | 发布包中的位置 |
| --- | --- |
| 用户配置 | `app/config/` |
| 界面日志 | `app/logs/` |
| 自动演出报告与错误截图 | `app/debug/auto_live/` |
| 服装解锁报告与错误截图 | `app/debug/costume_unlock/` |
| 日常任务报告与错误截图 | `app/debug/daily/` |
| 启动器错误记录 | 根目录 `launcher-error.log`，启动失败时生成 |

开发环境的任务报告位于项目根目录的 `debug/`。

遇到问题可先查看 [FAQ](docs/zh_cn/develop/faq.md)。提交 [Issue](https://github.com/hiaatcnd/MaaBanG/issues) 时，请附软件版本、模拟器版本、起始页面、复现步骤及相关日志；分享前检查其中的个人信息。

## 开发指南

### 准备环境

```powershell
git clone --recurse-submodules https://github.com/hiaatcnd/MaaBanG.git
cd MaaBanG
uv sync --project agent
uv run --project agent python tools/configure.py
```

使用 VS Code 的 **MaaFramework Support** 插件加载 `assets/interface.json`。MaaFramework 与 Python Agent 均固定为 **5.12.2**；开发界面由 uv 启动 Agent，发布包使用内置解释器。

### 项目结构

| 路径 | 用途 |
| --- | --- |
| `assets/interface.json` | 任务入口、界面选项与配置 |
| `assets/resource/pipeline/` | 识别节点、ROI 和流程 |
| `assets/resource/image/` | 游戏识别模板，坐标基于框架缩放后的截图 |
| `assets/branding/` | 最终版程序图标与来源说明 |
| `agent/` | Python 自定义动作和任务逻辑 |
| `agent/data/songs_cn.json` | 中国服离线歌曲目录 |
| `tests/` | 配置、数量决策、导航及任务流程测试 |
| `tools/` | 校验、目录更新、任务运行与打包工具 |
| `docs/zh_cn/develop/` | 功能行为、验证记录与开发说明 |

### 检查与数据更新

运行测试和资源检查，需要 Python 开发环境及 Node.js/npm：

```powershell
uv run --project agent python -m unittest discover -s tests -v
npm ci
npx @nekosu/maa-tools check
```

更新歌曲目录和联动界面选项：

```powershell
python tools/update_song_catalog.py
```

更新 Agent 依赖后，同步锁文件及发布清单：

```powershell
uv lock --project agent
uv export --project agent --frozen --no-dev --no-emit-project --output-file tools/release-requirements.txt
```

### 打包与发布

构建环境为 **Windows x64、Python 3.12、Git 和 .NET 10 SDK**。使用尚未构建过的版本号，例如：

```powershell
python tools/install.py v0.4.0-dev1
```

- 上游运行组件按 `tools/release-inputs.json` 下载并校验 SHA256，Python 依赖按发布清单固定版本及哈希。
- `tools/build_ui.py` 获取固定版本的 MFAAvalonia 源码，应用启动优化补丁并编译界面核心。
- 构建时运行包内 Agent 检查，输出 `dist/` 下的 ZIP 和 SHA256 清单；已有同名构建目录时拒绝覆盖。
- 修改通过 PR 提交，资源、单元测试和 Windows 打包检查通过后合并；在合并提交上创建 `v*` 标签触发正式发布。

[启动优化与构建细节](docs/zh_cn/develop/startup.md)

## 致谢与许可

MaaBanG 基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework)，使用 [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia) 提供界面。项目由 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) 模板初始化，参考 [M9A](https://github.com/MAA1999/M9A) 的发布和包检查方式，歌曲元数据来自 [Bestdori](https://bestdori.com/info/songs)。框架入门见 [MaaFramework 官方文档](https://maafw.com/docs/1.1-QuickStarted)。

本项目为非官方社区工具。MaaBanG 项目代码采用 [MIT](LICENSE)；修改后的 MFAAvalonia 组件沿用 GPL-3.0。第三方组件、角色图标及游戏素材保留各自的许可与权利归属，详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
