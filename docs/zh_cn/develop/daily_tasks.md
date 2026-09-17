# 日常任务

入口配置在 `assets/interface.json`，识别节点在 `assets/resource/pipeline/daily.json`，动作实现在 `agent/daily_tasks.py`。模板来源与 ROI 记录在 `daily_components.json`。坐标使用短边 720 的框架截图。

## 运行

从游戏主页运行；完成登录、更新并关闭活动弹窗。推荐在 UI 分别添加需要的任务。命令行示例：

```powershell
uv run --project agent python tools/run_daily_task.py ClaimHomeGifts --adb "C:/Program Files/Netease/MuMu/nx_main/adb.exe" --address 127.0.0.1:16416
```

可选入口为 `ClaimHomeGifts`、`ClaimHomeMissions`、`ExchangeMichelle`、`DailyFreeRecruit`。报告保存在 `debug/daily/`。

## 流程与边界

- 礼物箱使用一键领取；任务列表使用各分类的全部领取，并识别禁用按钮。未建立邀请关系时跳过，不创建邀请码。
- 贴纸交换遍历成员、表情、服装、背景、其他五类，跳过已达上限的项目。每次交换数量为 1，交换后重新识别列表，避免项目移位。连续两次滚动后的列表一致时结束该分类。
- 交换前验证费用、数量、贴纸余额，以及两次确认页的名称和余额一致；提交后核对成功弹窗和实际余额。余额不足时结束任务并返回主页。
- 免费招募同时确认专属卡池、免费按钮、剩余次数和确认文案。每次提交后处理跳过动画、成员展示、重复成员道具弹窗及结果页，再验证剩余次数减少 1；最多执行三次。
- 所有正常结束分支返回主页。错误或用户停止时保留现场；未确认的消费不自动重试。
- 服装任务仅在每位成员整批解锁后回评级页面领取新达成的默认 3D 收集奖励，不逐件往返，不更换穿搭。

## 验证

`python -m unittest discover -s tests -v` 覆盖免费次数、错误确认拒绝、交换费用校验、结果不明停止、分类遍历、批量解锁后领奖等行为。`tools/check_agent.py` 可验证当前源码 Agent 握手与五个动作注册，不执行游戏操作。独立包构建另行验证内置 Python、原生库和 Agent 通信。

游戏 UI 可能随更新改变；识别失败时先检查错误截图及报告，不应删除确认校验来绕过失败。
