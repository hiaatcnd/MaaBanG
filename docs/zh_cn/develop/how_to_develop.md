# 开发入门

环境安装、目录说明和校验命令见[项目 README](../../../README.md#开发)。当前业务流程见[服装解锁开发说明](costume_unlock.md)。

新增任务时，先用 Maa Support Crop Tool 获取框架截图，裁剪稳定图标到 `assets/resource/image/`，将 ROI 写入 pipeline 识别节点；动态数字使用 OCR。ROI 是截图上的 x、y、宽、高，应使用框架归一化后的图像坐标。

先验证识别，再添加点击动作和页面转换。涉及资源消耗时必须识别费用、确认结果，并处理资源不足及停止操作。
