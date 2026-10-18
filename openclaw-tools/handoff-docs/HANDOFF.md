# HANDOFF：OpenClaw 原生 Windows 试验完成

更新时间：2026-09-16（Asia/Shanghai）  
总状态：`DO_NOT_SELL`  
主结果：[NATIVE-PILOT-RESULTS.md](./NATIVE-PILOT-RESULTS.md)

## 交接结论

OpenClaw 官方 Windows 原生 CLI/Gateway `2026.9.4` 已在专用克隆中完成一次真实链路：固定供应链、隔离安装、回环 Gateway、DeepSeek V4 Pro 真实对话、页面重开、Gateway 重启、Windows 重启后登录恢复、故障停止/恢复、诊断、官方卸载和快照回退。

这条路径证明“无需 WSL 的本地 Gateway + WebChat”技术上可行，但不能代签旧 Windows Hub/WSL 包通过，也还不能销售。阻断项：

1. XQ 生产诊断器的 native adapter 未正确继承命名 profile，读取默认配置并探测错误端口，VM 健康诊断为 `PARTIAL/FAIL`。
2. 官方 OpenClaw 诊断 ZIP 含合成对话标记，违反预期的 payload-free 证据卫生；该 ZIP 标记 `DO NOT SHARE`。
3. 没有真实客户远程连接、中止、断开和撤权证据；VirtualBox/Guest Control 不是客户 transport。
4. 只有一次安装样本，未从基线第二次完整复跑，也未做恢复后 guest 内残留探针。
5. 工作树修复仍未提交，Formal 门和正式制品未跑。

## 已通过

- OpenClaw `2026.9.4`、Node `26.8.2`、DeepSeek provider `2026.9.4` 的来源与完整性固定。
- 私有目录安装，不改用户/机器 PATH，不复用旧 PMAI/OpenClaw state。
- Gateway 仅监听 `127.0.0.1:18789`，健康与只读 RPC 正常。
- DeepSeek allowlist-only，默认 `deepseek/deepseek-v4-pro`，无 fallback。
- 一次真实调用成功，未调用工具、未向外部渠道投递；成本记录为 `USD 0.002553015`。
- Gateway restart、浏览器页面关闭/重开和 Windows 重启后登录恢复通过。
- 故障状态能被检测，Gateway 可用官方命令恢复。
- XQ 生产诊断器的包闭包、缺策略 fail-closed 和脱敏扫描通过。
- 官方 `uninstall --service --state --workspace` 与 npm CLI removal 通过，无任务、端口、进程、state、workspace、package 或 shim 残留。
- 专用克隆已恢复离线基线；源 VM 始终关机。

## 最终机器状态

- 源 VM：`PersonalMessengerQQ`，UUID `7592dfa4-c60c-4818-921e-ced5c626254b`，`poweroff`。
- 测试副本：`XQ-OpenClaw-Test-01`，UUID `5d948da3-2078-45bf-92b4-151de5412753`，`poweroff`。
- 当前快照：`XQ-OpenClaw-Baseline-Offline-01`，UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e`。
- 测试副本 NAT 网线断开；clipboard 和 drag-and-drop disabled；旧 VISO 仍只从克隆卸载。
- DeepSeek 测试密钥已由用户在云端撤销。
- 一张曾显示该密钥的本地截图已移入 Windows 回收站，未纳入交付。
- Hermes 未启动；宿主机配置未改；未提交、推送或发布。

## 下一位执行者的最小任务

### 1. 修生产诊断器 named-profile 支持

仓库：`C:/Users/xiayu/Documents/Codex/2026-08-28/github-chatgpt-conversation-6a9128cc-6290-83e8-2/work/openclaw-public-audit`

当前已有 3 个预期未提交文件：

- `build/Build-Package.ps1`
- `policy/allowed-package-files.json`
- `tests/Package/M6.Package.Tests.ps1`

新增修复应让 `tools/OpenClaw-XQ-Diagnose.ps1` 的 native adapter 在命名 profile 下显式使用正确 state/config/port，并新增“命名 profile + 非默认/已安装端口”的生产包回归。不要把命令退出 0 当作健康通过；断言应覆盖实际配置路径、probe URL 和 RPC 状态。

### 2. 处理官方诊断 ZIP 的消息内容泄漏

证据：`work/agent-kits-tech/native-pilot/guest-native-diagnostics-stopped.zip`，SHA-256 `0230E4E37B690D693FCD6C53B75708D5ED830D2CD296680C7549F28BE59F4938`。

ZIP 未含 API Key，但 `logs/openclaw-sanitized.jsonl` 的 `msg` 含合成对话标记。不要将该 ZIP 发给客户或上游工单；后续若做包装层过滤，必须采用 allowlist 输出并回归证明不含消息正文。

### 3. 重复性与远程履约

- 如继续 VM 复跑，目标仍只能是上述专用克隆和基线快照；首次启动保持断网。
- 从基线完成第二次全链路安装，补恢复后 guest 内无残留验证。
- 另选真实客户 transport，测试客户接入、允许控制、中止、断开和撤权。
- 凭据创建、登录和撤销仍由客户在安全界面完成；不要要求粘贴到聊天。

## 证据入口

- 最终原生试验：[NATIVE-PILOT-RESULTS.md](./NATIVE-PILOT-RESULTS.md)
- 旧 Hub/WSL 结果：[TEST-RESULTS.md](./TEST-RESULTS.md)
- 修复结果：[FIX-RESULTS.md](./FIX-RESULTS.md)
- 计划：[TEST-PLAN.md](./TEST-PLAN.md)
- 当前状态：[PROJECT_STATE.md](./PROJECT_STATE.md)
- 决策：[DECISIONS.md](./DECISIONS.md)
- 暂停检查点：[PAUSE-CHECKPOINT-2026-09-16.md](./PAUSE-CHECKPOINT-2026-09-16.md)（仅历史，不代表最终状态）

主要机器可读证据在 `work/agent-kits-tech/native-pilot/`。其中官方诊断 ZIP 仅用于缺陷证明，不得对外分享；敏感截图已移入回收站。
