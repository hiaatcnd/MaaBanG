# MaaBanG

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的 BanG Dream 手游中国服自动化助手。

## 下载与启动

支持 Windows x64，目前在 MuMu 模拟器上验证。

1. 在 [Releases](https://github.com/hiaatcnd/MaaBanG/releases/latest) 下载 `MaaBanG-v版本号-win-x64.zip`，完整解压到可写目录。
2. 双击 `MaaBanG.exe`。压缩包内置界面、Python、MaaFramework 和 OCR，无需安装 VS Code、Python 或 uv。
3. 启动模拟器及游戏，完成登录、更新，保持横屏 16:9。建议模拟器分辨率设为 1280×720。
4. 在界面选择安卓端、扫描并连接设备。MuMu 可手动指定 ADB 路径，例如 `C:\Program Files\Netease\MuMu\nx_main\adb.exe`；地址按模拟器设置填写，例如 `127.0.0.1:16416`。
5. 资源选择“中国服”，添加需要的任务，配置后运行。日常任务从游戏主页启动。

若系统提示缺少 VC++ 运行库，可使用包内上游提供的依赖安装脚本。请完整解压后运行，不要只复制 exe。

## 已实现：默认 3D 演出服装

- 支持全部 8 队 40 人、指定乐队或指定成员。
- **目标数量是每位角色最终希望达到的数量，不是本次购买件数**。范围 0–999；默认 0 不执行。
- 可先选“仅检查（不消耗道具）”。该模式仍会领取已完成的服装收集奖励。
- 从主页、乐队菜单、角色评级或服装列表启动；先关闭其他弹窗。
- 根据角色评级中的默认 3D 服装进度计算差额，留在服装列表连续解锁，不逐件返回评级。
- 消耗裁缝套装和金币；不足时停止整个任务。确认结果不明时停止，避免重复提交。
- **解锁后选择“不变更”，不换上新服装，也不保存试穿。**
- 每位成员整批解锁后领取已达成的服装收集奖励，全部完成或资源不足停止时返回主页。
- 快速模式遇到“收集任务全部完成”的角色直接跳过，即使目标数量更大也不扫描该角色的全衣柜。

任务报告位于 `debug/costume_unlock/`。更完整的行为、命令行参数与验证记录见[任务说明](docs/zh_cn/develop/costume_unlock.md)。

## 日常任务

以下任务需要自行添加，默认不勾选；请先进入游戏主页并关闭活动弹窗。

| 任务 | 行为 |
| --- | --- |
| 领取主页礼物 | 使用一键领取，直到礼物箱为空 |
| 领取任务奖励 | 依次领取期间限定、限时招募券、通常任务、每月、邀请邦友、EX任务中已达成的奖励 |
| 米歇尔贴纸交换 | 可多选成员、表情、服装、背景、其他，只交换勾选分类中的可交换项目；贴纸不足时停止 |
| 每日免费招募 | 识别每日三次免费演出招募，仅抽取当天剩余免费次数 |

完成后返回主页。贴纸交换的“贴纸交换分类”默认全选，可取消不需要的分类；全部取消时不操作游戏。贴纸交换会实际消耗米歇尔贴纸；每日免费招募不使用星石或招募券。不会购买月度付费奖励或自动建立邀请关系。确认结果不明时停止，避免重复提交。尚不支持自动演奏。

日常报告及错误截图位于 `debug/daily/`，启动和验证说明见[日常任务开发文档](docs/zh_cn/develop/daily_tasks.md)。

## 开发

```powershell
git clone --recurse-submodules https://github.com/hiaatcnd/MaaBanG.git
cd MaaBanG
uv sync --project agent
uv run --project agent python tools/configure.py
```

使用 VS Code MaaFramework Support 加载 `assets/interface.json`。MaaFramework 与 Python Agent 均固定为 **5.12.2**，避免协议版本不匹配。开发界面由 uv 启动 Agent；发布包直接使用内置解释器。

- `assets/interface.json`：任务和界面选项。
- `assets/resource/pipeline/`：识别节点、ROI、流程入口。
- `assets/resource/image/`：裁剪模板；坐标使用框架缩放后的截图坐标。
- `agent/`：Python 自定义动作、角色选择及解锁逻辑。
- `tests/`：数量决策、配置、导航与连续解锁测试。
- `tools/`：校验、直接运行及独立打包工具。

```powershell
uv run --project agent python -m unittest discover -s tests -v
npm ci
npx @nekosu/maa-tools check
```

## 打包与发布

在 Windows x64、Python 3.12 环境执行：

```powershell
python tools/install.py v0.2.0
```

构建脚本从 `tools/release-inputs.json` 下载固定版本并校验 SHA256；Python 依赖使用 `tools/release-requirements.txt` 的版本及哈希。输出为 `dist/` 下的 ZIP 和 SHA256 清单；已有同名构建目录时会拒绝覆盖。

PR 执行资源、单元测试和 Windows 独立包检查。合并通过后，在合并提交上创建 `v*` 标签触发正式发布。首次发布提供 Windows x64。

更新 Agent 依赖后同步锁文件与发布清单：

```powershell
uv lock --project agent
uv export --project agent --frozen --no-dev --no-emit-project --output-file tools/release-requirements.txt
```

## 反馈与致谢

问题请提交 [Issue](https://github.com/hiaatcnd/MaaBanG/issues)，附版本、模拟器、起始页面和相关日志，分享前检查个人信息。

项目从 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) 初始化，参考 [M9A](https://github.com/MAA1999/M9A) 的发布及包检查方式；界面使用 [MFAAvalonia](https://github.com/MaaXYZ/MFAAvalonia)。框架入门见[官方文档](https://maafw.com/docs/1.1-QuickStarted)。

项目代码采用 [MIT](LICENSE)。第三方组件及游戏素材归各自权利人所有，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

常见问题及 Windows Agent 初始化修复说明见 [FAQ](docs/zh_cn/develop/faq.md)。
