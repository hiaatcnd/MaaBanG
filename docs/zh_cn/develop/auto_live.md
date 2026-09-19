# 清火自动演出

配置入口为 `AutoLive`，资源节点前缀为 `LV_`，模板来自实机短边 720 截图，裁剪坐标见 `live_components.json`。`agent/auto_live.py` 负责设备流程，`live_policy.py` 负责次数、火数和配置校验。

## 用户配置

| 配置 | 选项与行为 |
| --- | --- |
| 演出模式 | 自由演出 / 巡回演出（仅自由巡演） |
| 演出歌曲 | Bestdori 中国服可用目录，无需收藏，程序在游戏内自动按乐队和 EXPERT 等级查找；同名歌曲区分乐队 |
| 演出难度 | 随歌曲联动，只显示中国服已开放的难度；旧配置中不可用的 Special 回退 Expert |
| 每首消耗火数 | 0、1、2、3 |
| 火不足策略 | 停止 / 降低火数，最低 0 |
| 最大演出次数 | 留空不限；填写 1–999。自由一首或巡演三首均计一轮 |

不限次数仍受每日自动演出额度限制。巡演在开始前检查至少还有 3 次；每首开始前再次核对剩余次数和自动开关。停止策略下，在开始巡演之前还要求持有火数不少于每首火数乘 3，避免因已知火不足而停在一轮中间。降低策略按每首当时的剩余火数决定，不购买补充道具。

## 流程

1. 从主页进入演出。选歌前切到 Expert，避免残留的 Special 筛选隐藏没有该难度的歌曲。自由巡演为三个位置分别按乐队筛选全部歌曲，选择同一首歌和难度；每次选歌都验证选中项，开演前再次核对。同名歌曲还核对选歌页的演奏乐队，无法确认则不选择。
2. 读取自动演出次数和持有火数，执行策略。明确选择 0–3 火，验证单选状态及开始按钮上的扣减预览。
3. 确认“自动演出 开”后只提交一次开始。每 10 秒检查演出结果，等待可随用户停止中断；10 分钟未确认结束则报错，不重试消费。
4. 每日首次演出后可能立即弹出“每日演出报酬完成”，包括巡演第一首与第二首之间。优先确认关闭该弹窗，再核对下一首曲序及额度减 1，避免将弹窗背后的准备页误判为可操作状态。最终结算逐页处理奖励、成绩、经验、评级升级、每日演出奖励、跨日登录奖励、活动奖励与演出后对话。确认返回导航页面后才累计一轮。
5. 达到上限、自动次数不足或火不足时正常停止并返回主页。识别失败或用户停止时保留现场，不重启在途演出。

程序不调整编队、分数设置或设备音画选项；使用用户当前游戏配置。开始时应关闭无关活动弹窗，不能从已开始但未结束的巡演中间恢复。

## 开发与验收

```powershell
# 只完成选歌并读取准备页，不开演
uv run --project agent python tools/run_live_task.py --prepare-only --song EXIST --difficulty special --adb "C:/Program Files/Netease/MuMu/nx_main/adb.exe"

# 自由演出最多一次，每首 2 火
uv run --project agent python tools/run_live_task.py --song EXIST --fire 2 --max-rounds 1 --adb "C:/Program Files/Netease/MuMu/nx_main/adb.exe"

# 自由巡演；同一首选三次
uv run --project agent python tools/run_live_task.py --mode tour --song "SAVIOR OF SONG" --difficulty expert --fire 3 --shortage lower --adb "C:/Program Files/Netease/MuMu/nx_main/adb.exe"
```

动作报告位于 `debug/auto_live/`，包含设置、实际难度、每首提交与结果状态、火数和自动次数读数。结果未确认时保留 `submitted`，不会把它计为完成。准备模式会改变游戏选歌与难度，但不会点击开演。
