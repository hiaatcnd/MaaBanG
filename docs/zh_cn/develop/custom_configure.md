# 项目配置

`assets/interface.json` 定义项目、ADB 控制器、资源、任务及选项。修改 UI 参数后同时运行 schema 校验和单元测试。

独立选项使用不同节点的 attach 字段传值，避免多个 pipeline_override 覆盖同一 custom_action_param 对象。

发布依赖见 `tools/release-inputs.json` 和 `tools/release-requirements.txt`；构建入口及发布步骤见 README。
