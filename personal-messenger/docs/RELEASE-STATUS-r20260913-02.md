# r20260913-02 发布状态

## 结论

`r20260913-02` 是当前已冻结、已安装并通过离线验证的 guest release。它包含受控的一次性真实回复入口，但尚未对任何联系人执行该入口，也没有启动常驻 supervisor。

本 release 的 one-shot 边界为：显式指定一个 direct binding、最多生成和发送一个 segment、同一 source message 只允许一个 durable attempt、失败或结果不确定时不自动重试、COMMIT 后必须由不同 PID/epoch 的全新 worker VERIFY，随后 worker 也必须确认退出。

## 发布与安装证据

- release：`outputs/guest-local-releases/r20260913-02`
- guest 安装目标：`C:\PMAI\app\releases\r20260913-02`
- wheel SHA256：`2D4C3B4180C4BBCA3F9B11EDB8F88CA9CC756DFF3C00BD86C354344377F6D6BF`
- manifest SHA256：`9673E610A0DEB76837885CBC141036EB8B9E6F99C8B3CC9DE8C3CD5988308EB2`
- manifest 文件数：`20`
- guest one-shot 脚本 SHA256：`323D321A344767E41E8C2EF9AE8499772FC4674B14A37DA68A6E09605171541C`
- 安装诊断：`../qq-vm/install/diagnostics/stage-install-guest-release-v2.json`
- canonical 配置检查：`../qq-vm/install/diagnostics/runtime-config-check-r20260913-02-1fdcbad45f3e4715a37b608913a31c7b.json`
- 最新停机状态：`../qq-vm/install/diagnostics/runtime-status-host-6bee2cb536404f2686cbc01c2bf9ad09.json`

安装器 exit `0`，配置检查 exit `0`。最新状态是 `graceful_stopped`：runtime 不存活，worker 不存活且 exit `0`，Web 不可达。

`r20260913-01` 是审计修复前冻结的样本，没有安装，也不得作为可执行 release 使用。

## one-shot 安全契约

- guest 先以 exclusive create 持久化 attempt intent，再访问 QQ 或 provider；attempt id 不能复用。
- canonical 配置先投影为只含目标 binding、contact、session evidence、visual label、migration 和 bootstrap provenance 的单 binding 配置，再次验证目标唯一且为 direct。
- runtime assembly 禁止恢复其他会话的共享 pending state；视觉选择刷新不自动重试。
- observation 必须精确绑定目标会话；没有新 inbound 时不调用回复 provider，也不进入发送动作。
- event、job、plan 和 pacing segment 均按目标 artifact 精确领取；本 one-shot 的发送 claim 为 non-recoverable，不会被通用恢复逻辑重新排队。
- 回复 segment 上限为 `1`。发送不确定、超时、进程退出不明确或验证不完整均停止，不能自动再发。
- verified 结果要求 COMMIT 与 VERIFY 的 PID/epoch 均不同，旧 worker 已退役，fresh worker 的 HEALTH epoch 与 VERIFY epoch 相同，且 fresh worker 最终也以确定 exit code 退役。
- host 超时、启动等待失败或状态不明确时，只终止本次精确进程树；报告仅允许安全 ID、hash、状态和布尔证明，不转存消息正文、联系人标签或 API key。

## 审计与测试

独立复核最初给出 NO-GO，指出五项问题：全 binding 装载、视觉刷新重试、due claim 可恢复、host 不确定失败未杀进程树、worker stop 未证明退出。上述五项均已修复，同一复核者重审结论为 **GO**。

- one-shot/host/worker/pacing 等定向组合：`150 passed`
- 完整测试：`867 passed, 1 skipped, 2 warnings`
- 新增 one-shot 相关代码的定向静态检查：通过
- Python 编译检查与 PowerShell parser 检查：通过

两个 warning 是既有环境提示。全仓库静态检查仍会报告旧版 bridge/worker/run script 的历史问题；`git diff --check` 也会命中既有 generated obj 尾随空白，本次没有为了清理这些无关基线而扩大改动。

## 下一道门

仅在用户明确指定 `session-contact-1`、`session-contact-2` 或 `session-contact-3` 后，才运行一次 host one-shot。每次使用新的 attempt id，不做自动重试：

```powershell
python ..\qq-vm\install\run_one_shot_deepseek_reply_host.py `
  --release-id r20260913-02 `
  --binding-id session-contact-N `
  --attempt-id <new-uuid> `
  --max-wait-seconds 90
```

若目标当前没有新的未处理 inbound，合法终态是 `no_new_inbound`，且 provider/action 都必须为 false。若视觉选择执行了一次动作但 fresh observation 尚未完成，合法安全终态是失败/不确定并停止；不得用相同 attempt id 或自动循环重试。真实 one-shot 通过前，不启动常驻 supervisor。
