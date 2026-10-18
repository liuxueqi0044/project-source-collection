# Personal Messenger AI V5 实施状态

本文把“代码已经写入、离线检查已经通过”和“真实 QQ/Windows 仍待验证”分开记录。QQ 已完成一次受监督的真实私聊发送（向“哎.爱”发送“你好呀”，root 截图验收），但程序 receipt 仍为 `visual-check`，完整 DeepSeek 自动回复未启用。产品范围为全部一对一私聊，含临时会话，排除群聊；测试账号获授权自由真实收发而无需逐次确认，仍沿用原 persona。历史“未真实发送”或“仅好友私聊”的文字应按其记录时间理解，不是当前状态。

| 阶段 | 当前状态 | 说明 |
|---|---|---|
| B0 契约与边界 | 已写入，已做离线检查 | WebUI 只通过 `LiveHubFacade` 调用 runtime；生产模式必须显式注入真实 facade。 |
| B1 虚拟机与 QQ 环境 | 安装完成，UIA 发送能力待验 | Windows/Core 26200、Guest Additions 7.1.18、Python 3.13.15、官方 QQ 9.9.33 和项目包已实际安装；runtime-setup-status.json 实读 complete，包含依赖、DPAPI smoke、CLI help 和 QQ 签名检查。QQ 已完成扫码登录；selected structural probe 实际返回 438 个节点。Composer 只有 TextPattern、没有 ValuePattern；send 按钮有 InvokePattern，但尚未通过写入读回、身份和幂等门槛。登录/modal heuristic 的 false/unknown 仍不能作为实际退出结论。 |
| B2 多联系人注册与观察 | 已实现，离线通过 | `runtime/` 与 `adapters/qq/vm_driver/` 的多联系人观察、游标和方向投影已由 3/5 联系人验收覆盖；真实 UIA 绑定待验。 |
| B3 多目标执行与账本 | 已实现，离线通过 | VM bridge 的 prepare/commit/verify、operation 关联、重复发送防护和多目标 fixture 已覆盖；真实 QQ 发送待验。 |
| B4 记忆、规则与规划 | 已实现，离线通过 | 复用 Memory/M7/M8，以联系人上下文和不同 reply 验收；guest 合成 DeepSeek 生成已通过，真实 QQ runtime 仍待验。 |
| B5 编排、恢复与分段 | 已实现，离线通过 | 真实共享 runtime 覆盖并发、暂停、三段、人工接管与 ignore；CLI 生命周期离线复验已通过。 |
| B2 多联系人观察与绑定 | 实机待验 | 至少绑定 A/B/C；身份证据不充分、绑定过期或有缺口时必须保持暂停。 |
| B3 发送账本与执行器 | 实机待验 | 需要用测试联系人验证 operation_id、提交超时、重复调用和 UNCERTAIN 隔离。 |
| B4 记忆、规则与 DeepSeek | 原人设 RulePack 已在 guest 激活；真实 QQ runtime 仍待验 | `rulepack-activation-status.json` 报告 `personal-default` 已 active，source hash 为 `b63c24aaa58bb30c4d05cd4297b562f1164ee932b6b61224c6c4035f65c2d6a3`、version 为 `vb63c24aaa58b`。v4 guest 合成生成实测通过；生产类型/好友校验接线已实现并由离线测试覆盖，但尚未部署新 wheel。未启动 runtime WebUI，未进行真实 QQ 发送；guest composer/UIA 路径和多联系人实机验收仍待验。 |
| B5 编排与恢复 | 仅有离线契约检查 | 需要验证重启恢复、全局/联系人暂停、到期任务恢复和多联系人公平调度。 |
| B6 控制 WebUI | Projection 与 AtomicRuntimeControls 已完成，真实 SQLite/Starlette 离线契约检查通过；真实 driver/API/VM 仍待验 | `RuntimeWebUIProjection` 从 RuntimeState、Hub、M10 和可选规则/driver 投影生成常用页面；生产入口缺真实 facade 时直接报错，不回退 Fake。 |
| B7 端到端交付 | 尚未完成 | QQ 已登录但候选列表尚未形成稳定身份绑定；仍需要真实 A/B/C 消息、至少 5 个配置联系人调度检查，以及验收矩阵记录。 |

## 真实 runtime 与 WebUI 接口

## 当前代码复验与部署边界

Root 在本轮 selector 与会话类型防护合并后独立运行完整测试，结果为 `541 passed, 1 skipped`，另有两个既有 warning；随后 DeepSeek 修复后的 LLM 专项测试为 `21 passed`。历史冻结的 534 项验收记录保持不变。生产入口现在要求认证后的 `direct` 会话类型与 `friendship_verified=true`，并拒绝 `group`、`unknown`、未认证 participant signature 及 client version 不一致；尚未将该接线部署为新 wheel。

DeepSeek-only schema 修复与本轮 identity wheel 已在 guest 安装并校验；provider hash match，身份/类型接线 wheel 已完成安装状态记录。v4 合成生成已通过。原人设 RulePack 已激活，但生产 runtime/WebUI、自动回复和真实 QQ 发送继续关闭；已有真实私聊记录仍属于临时历史验收，不能替代本次恢复后的验收。

生产代码应构造 `LiveHubFacade(runtime)`。runtime 必须实现：

```python
def webui_page(name: str, entity_id: str | None = None) -> dict[str, Any]: ...
def webui_command(name: str, payload: dict[str, Any]) -> dict[str, Any]: ...
```

常用页面名称为 `inbox`、`conversation`、`reviews`、`contacts`、`contact`、`rules`、`pacing`、`adapters`、`incidents`、`audit`、`settings`。联系人快照应提供 `contact_id`、`display_name`、`binding_status`、`binding_expires_at`、`last_observed_at`、`plan_status`、`pause_status`、`health`、`uncertain_operations` 和递增 `revision`。

暂停命令为 `pause`、`resume`、`pause_contact`、`resume_contact`。不确定发送只能按具体 `operation_id` 和 `expected_revision` 进入“确认已查看，保持不自动重发”的人工处理记录；这个动作不把发送结果改成 VERIFIED，也不批量清除联系人下的其他 operation。

当前已用三个真实 RuntimeState 联系人和 SQLite HubStore 验证联系人列表、单联系人暂停、版本冲突，以及 uncertain operation 的查看记录。该记录只写 WebUI review 表，保留 Hub `send_operations.status=uncertain`，不推进 M10。

离线演示必须显式传入 `FakeHubFacade()`（或 `create_app(..., demo=True)`）。演示联系人会标注 `demo-only`，不能当作真实 QQ 状态。

## 操作注意事项

- DeepSeek API key 只在客体的密钥存储或运行环境中配置，不要把 key 发到聊天，也不要写入源码、fixture、截图或普通日志。
- QQ 绑定必须从当前可见目标会话建立并记录身份依据；昵称单独不足以唯一识别联系人。无法区分时保持该联系人暂停。
- 全局暂停停止所有自动发送；联系人暂停只影响该联系人。绑定过期、健康异常、身份缺口都要在页面可见，并阻止该联系人继续自动发送。
- `UNCERTAIN` 表示无法证明这一次操作是否已经发送。人工“确认已查看”只记录处理，不制造发送成功证据，也不提供自动重发按钮。
- 本轮已完成 QQ 客户端扫码登录、原人设 RulePack guest 激活和一次 guest 合成 DeepSeek 生成验收，但没有启动真实 runtime WebUI 或执行 QQ 发送；群聊明确排除，首版范围为全部 QQ 好友私聊。新 private structural probe 尚未产生成功报告。
