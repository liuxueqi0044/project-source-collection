# r20260912-18 发布状态

- 状态：已冻结、已通过 manifest 校验、已通过 VirtualBox GuestSession 后台安装。
- wheel SHA256：`27737C4B762D77594D65757FBD4ADC7FAE8CB5FAC7AEF1183E2ECAE6360966A8`。
- guest 目标 manifest SHA256：`D3E6BCEEA31441C6411910F9E1C5E2AF83A7C67139100D7CE8FADEB9318645D0`。
- 安装结果：`state=succeeded`、`installer_exit_code=0`、`pip_returncode=0`。
- 安装前置：runtime 被再次核对为未运行；安装过程没有启动 QQ runtime、调用模型、操作联系人或发送消息。
- 主要增量：独立视觉行认证 provider、`ConversationSelectionActuator`、exact-HWND 单行 GDI/PNG 捕获、guest 单击边界、运行配置显式开关与标签一一对应校验、视觉/回复模型隔离。
- 离线验证：`777 passed, 1 skipped`；另有 pytest 配置与 Starlette/httpx 的两个既有 warning。
- 尚未验收：视觉 API 实际响应、contact2 selection-only 单击、新进程 selected/header/direct/group 复核、三联系人只读轮换、真实 QQ → DeepSeek → QQ 闭环。

本发布安装成功不等于联系人选择或自动回复可用。下一步遵循 `VISUAL-CONVERSATION-SELECTION.md`，只做一次 selection-only 实机门。
