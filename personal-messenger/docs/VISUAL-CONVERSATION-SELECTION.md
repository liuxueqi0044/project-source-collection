# QQ 视觉联系人选择边界

## 当前结论

QQ 9.9.33 的会话行没有可用的 UIA `Name` 或 `SelectionItemPattern`，而
`ControlFromPoint` 也不会命中承载会话行的可访问性子树。因此，坐标点不能再由
UIA hit-test 证明属于目标联系人。视觉方案只补这个缺口，不替代现有消息读取、
记忆、规划、策略、去重、prepare/commit/verify 或发送回执。

源码已增加独立的 `ConversationSelectionActuator`：

1. 现有 current-session locator 先唯一定位已登记的 UIA 行。
2. guest 只截取该单行矩形；尺寸上限为 `1024x256`，完整 QQ 窗口和聊天面板不能进入模型请求。
3. 专用 `deepseek-v4-flash-vision-exp` 只回答该行的主要联系人标签是否精确等于本地配置标签；输出 schema 没有坐标、回复正文或工具调用字段。
4. 只有 `exact_label`、标签规范化后精确相等且置信度不低于 `0.98` 才可继续。
5. 模型返回后重新截图；几何或 PNG digest 变化即拒绝，避免使用过期视觉判断。
6. 执行器在 guest 内对行中心发送一次单击，且只接受仍属于已认证 QQ HWND/PID 的点。
7. 执行器只返回 `not_needed / action_attempted / rejected`。`action_attempted` 不是成功证明；动作进程必须退役，由不重叠的新 worker 再确认 selected token、header identity 和 `group_marker_count=0`。

视觉 provider 与原回复 provider 是两个对象。启用视觉选择时，主回复模型不能配置成视觉模型；同一 API key 只作为两个独立客户端的凭据，不会进入请求日志或 worker 状态。

## 配置与构建入口

运行配置只有显式存在 `visual_selection` 才启用。标签必须与全部已选择 binding 一一对应：

```json
{
  "model": "deepseek-v4-flash",
  "visual_selection": {
    "model": "deepseek-v4-flash-vision-exp",
    "labels": {
      "session-contact-1": "本地精确标签",
      "session-contact-2": "本地精确标签"
    },
    "min_confidence": 0.98,
    "timeout_seconds": 8
  }
}
```

guest 配置构建器用重复的 `--visual-label INDEX=LABEL` 参数生成该段；只给部分联系人标签、重复索引、控制字符、非视觉模型、视觉模型同时作为回复模型，或未给 worker 退役预留四秒，都会在启动前拒绝。

## 验证状态与下一道门

截至 2026-09-12，selection-only 实机门已经通过。r20/r21 的单行 PNG 为纯黑，诊断字段证明模型安全拒绝且没有动作；r22 将行捕获改为经 QQ HWND/PID 和五点边界认证的 screen DC 后，图片恢复为非黑内容。r23 又补齐动作异常的 `action_attempted` 语义、两代 worker 的正向退役证明、严格四阶段/不同 PID/不同 epoch 验证，以及 selection-only 不访问 composer selector 的约束。

r23 对三个 binding 各执行一次独立串行 one-shot，均满足：视觉 `exact_label`、置信度 `0.98`、一次单击、第一 worker 退役、全新第二 worker 确认 selected token、header identity、direct 类型和 group marker 0、第二 worker 退役。attempt IDs：

- contact1：`e478d0cb-8d6d-4dd5-a698-153826627abf`
- contact2：`55ed4be1-db8a-4a14-9d87-43311c94dfd1`
- contact3：`39a33b92-316d-4152-8a22-ee3ffbcfe5b7`

视觉配置已晋升到 guest canonical config。晋升时发现 Windows `os.open` 未带 `O_BINARY`，把原配置的 CRLF 备份成 CRCRLF；当前源码已改为 binary exclusive write，并循环处理 partial write。畸变备份保留取证，另生成字节完全一致的精确备份：晋升前/精确备份 SHA256 都是 `c9b6763fc40f428f6f32987c3691ff8917c8ef629a66efa714f667ffc5fe52b5`，当前 canonical SHA256 为 `87a6de61967b234013c99dbbd25ab72e25fe65668e4f76646310c290e9cd02b6`。

完整离线套件为 `823 passed, 1 skipped`。`r20260912-26` 已冻结并通过 guest 后台安装，wheel SHA256 为 `D1F5BEC65ECB365E401ABCF1FE043D3DB6E88BE9CF686DC79F7946E4FF042322`，guest 目标 manifest SHA256 为 `A17C5D43513ABCD17EAB75B4754939969D31B8E65D28128F8076A55F12A0854D`；canonical `--check` exit 0。

最新状态仍为 `graceful_stopped`，runtime/worker 均不存活，Web 不可达。视觉选择阶段没有读取或生成回复、没有进入 composer、没有点击发送按钮，receipt 仍为 0。下一道门是受控的一次真实 QQ → DeepSeek → QQ 回复：必须显式指定一个 binding、限制最多一次发送、保留 prepare/commit/verify 证据，并在发送结果不确定时停止而非盲目重试；在该 one-shot 入口完成审计前不得启动常驻 supervisor。

## 2026-09-13 one-shot 集成状态

上述 one-shot 入口现已实现、通过独立审计并随 `r20260913-02` 安装，但尚未执行真实回复。one-shot 会把 canonical 配置投影成只含显式目标 binding 的 scoped 配置，并关闭 selection refresh 自动重试：如果目标尚未选中，视觉动作至多执行一次，动作 worker 立即退役；fresh worker 必须重新观察并完成身份确认。任何 observation 不完整或结果不确定都停止，不会为“完成选择”自动开始第二轮。

完整测试更新为 `867 passed, 1 skipped, 2 warnings`。最新 runtime 状态仍为 `graceful_stopped`；没有 provider 调用、composer 写入、发送动作或新增 receipt。只有在用户明确选择 `session-contact-1/2/3` 之一后，才能运行一次新的受控 attempt。常驻 supervisor 继续禁止启动。
