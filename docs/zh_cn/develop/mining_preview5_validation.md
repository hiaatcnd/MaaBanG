# preview5：补齐活动新歌目录

2026-09-23 的 10:34 失败发生于团队最终确认页。报告路径为 `install/MaaBanG-v0.4.0-mining-preview4-win-x64/app/debug/chart_live/20260923-103037/report.json`，曲名为「かぽーんっと極楽☆ 湯〜とぴあ！」。

包内目录获取于 9 月 19 日，未包含 9 月 22 日中国服开放的 773 号歌曲。此前识别到完整曲名也无法绑定谱面；不是文字误识别或偏差设置问题。

重新获取 Bestdori 目录并按中国服当前开放时间生成，当前可选歌曲由 734 增为 735。773 的 EASY／NORMAL／HARD／EXPERT 谱面下载及校验全部通过，音符数分别为 135／227／532／812；无 SPECIAL。真实失败截图经生产读取函数唯一识别为 773，无需新增 OCR 别名或模糊匹配。

173 项回归测试通过。此次修复保留挖矿 preview4 功能，未启动额外演出。截图识别和谱面校验不等于这首歌已完整实机演奏。

## 后续 3 火实机验收

用户要求重试一把后，通过 preview5 的实际 Agent 入口运行团队 EX、中等偏差、3 火、最多成功 1 次。随机再次抽到 773，正常首键同步并演奏：809 PERFECT、3 GREAT、0 GOOD／BAD／MISS，812 全连，火数 25→22，未补火、未重试。

原任务在“达成报酬一览”弹窗超时，报告仍准确保留完成 1 次，不能将该次任务标为全自动通过。定位到 MaaFramework 缩放截图 OCR 漏掉“一览”的“一”，补充精确标题形式“达成报酬览”后，生产弹窗函数实机返回成功，随后结算恢复自动返回主页，未再开演。修复已同步本预览包。

完整原始记录保存在 `debug/team-three-fire-validation/20260923-120745`，成绩截图为 `judgment_attempt1.png`；框架截图与恢复证据位于 `debug/achievement-popup/framework.png`、`debug/online-capture/final-three-fire-state.png`。
