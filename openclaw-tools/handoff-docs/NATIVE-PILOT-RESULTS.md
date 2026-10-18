# OpenClaw 官方 Windows 原生 CLI/Gateway 试验结果

日期：2026-09-16（Asia/Shanghai）  
目标：`XQ-OpenClaw-Test-01`（UUID `5d948da3-2078-45bf-92b4-151de5412753`）  
最终状态：试验链路部分通过，已卸载并恢复离线基线；整体仍为 `DO_NOT_SELL`  
边界：本报告只评价 Windows 原生 CLI/Gateway 候选，不改写旧 Hub/WSL 路线失败，也不代表旧 XQ Hub 安装包通过。

## 总结

OpenClaw `2026.9.4` 在现有 Windows 11 测试副本中可不依赖 WSL 运行：固定供应链、私有 Node/npm 安装、回环 Gateway、DeepSeek 官方 provider、一次真实模型对话、页面关闭重开、Gateway 重启、Windows 重启后登录恢复、故障停止/恢复和官方卸载均形成了真实证据。

但当前还不能销售或承诺低人工远程履约，原因有三项：

1. XQ 生产诊断器虽能脱敏并识别原生 CLI，但其 native adapter 没有正确沿用命名 profile，状态命令探测了错误的默认配置与端口；本项为 `PARTIAL/FAIL`。
2. OpenClaw 官方诊断 ZIP 在声称 payload-free 的情况下仍包含本次合成对话标记；它不能作为对外售后证据包。
3. 真实客户远程接入、客户中止/断开/撤权和从基线第二次完整复跑仍未测试。VirtualBox/Guest Control 只证明内部测试机可控，不能代签远程服务。

## 固定供应链与安装边界

| 项目 | 固定值 / 结果 |
|---|---|
| OpenClaw | `2026.9.4`，release commit `3a9d69db306cd7f081e06254cb89c4bcc14a7107` |
| 固定安装脚本 | SHA-256 `A7B586F471FB02774AE6DF47F1AC640ADDC58FF6C099DA209310D3E2156523CC` |
| OpenClaw npm tarball | SHA-256 `4F1F656770461D4677DEA755B1899CBA12B912B06798C89A59E2F0C18688B761` |
| Node | Windows x64 `26.8.2`，ZIP SHA-256 `CF02F5D0C06C794B84F277177D5CF3743D0924CA49F6641CD435DD7CB6EE9085` |
| DeepSeek provider | `@openclaw/deepseek-provider@2026.9.4`，trusted official install；tarball SHA-256 `F6D495816D92B19B1F6B92EB47402B7D2EE278B302062CAE8BB8C33632065D9A` |
| 隔离路径 | `C:\Users\pmai_setup\OpenClawNativePilot`；profile `xq-native-pilot`；未改用户/机器 PATH |
| Gateway | `127.0.0.1:18789`，只允许回环；Windows 登录触发的 per-user Scheduled Task |
| 模型策略 | allowlist 仅 `deepseek`；默认 `deepseek/deepseek-v4-pro`；fallback 为空；OpenAI/Codex 未启用 |

没有运行滚动远端脚本管道。实际安装使用已在宿主固定和校验的 Node 与精确 npm 包，并限定在测试副本的私有目录。旧 PMAI/OpenClaw 身份、记忆和模型凭据均未复用。

## 验收矩阵

| 测试项 | 状态 | 证据与说明 |
|---|---|---|
| 官方来源、版本和完整性 | PASS | tag/commit、release manifest、npm metadata/SRI/shasum、脚本和 tarball hash 已闭环 |
| Windows 原生安装 | PASS | OpenClaw `2026.9.4` + Node `26.8.2`，私有 prefix，不改 PATH |
| Gateway 启动与健康 | PASS | health/RPC 退出 0；仅监听 `127.0.0.1:18789` |
| 本地用户入口 | PASS | Control UI/WebChat 可打开；页面关闭后重新打开可自动重连 |
| DeepSeek 官方 provider | PASS | 官方插件 `2026.9.4` loaded/activated，模型可用，配置有效 |
| 一次真实文字对话 | PASS | 实际 provider/model 为 `deepseek/deepseek-v4-pro`；只返回指定合成标记；未调用工具、未外部投递、未 fallback |
| Gateway 重启后恢复 | PASS | PID 变化，凭据和模型保留，健康/RPC 恢复，仍为 loopback-only |
| Windows 重启后恢复 | PASS（有限） | 用户登录后 Scheduled Task 自动启动；凭据、模型和插件保留。无人登录时不启动，这是明确限制 |
| 最小故障与恢复 | PASS | 停止 Gateway 后 health/status/stability 正确失败；官方 start 后三项恢复，监听仍为回环 |
| 官方诊断 ZIP | **FAIL—证据卫生** | 未发现 API Key，但 `logs/openclaw-sanitized.jsonl` 的 `msg` 仍包含合成对话标记；不得对外发送 |
| XQ 生产诊断器包闭包 | PASS | 三文件 hash 与构建包一致；默认相对策略路径可运行；删除策略后退出 1 且不生成报告 |
| XQ 生产诊断器脱敏 | PASS | 报告未发现 API Key 模式或对话标记；用户路径已替换 |
| XQ 原生健康诊断语义 | **PARTIAL/FAIL** | CLI/Node/npm 识别成功，但 `doctor/status` 未正确沿用 `xq-native-pilot`：读取默认 `.openclaw`、选择端口 `44281`，RPC 为 `ECONNREFUSED`，不能记全绿 |
| 官方卸载 | PASS | dry-run、`--service --state --workspace` 实际卸载均退出 0；任务、端口、profile state、workspace 和进程均消失 |
| CLI removal | PASS | 同一私有 npm owner/prefix 移除 OpenClaw，330 packages removed；CLI/package/shim 均消失 |
| 快照回退 | PASS（hypervisor） | 克隆关机后恢复指定快照；当前快照 UUID、关机状态、断网和隔离设置复核正确 |
| 回退后 guest 内残留检查 | NOT TESTED | 为保持离线基线未再次启动来宾；由 VirtualBox 快照恢复保证丢弃试验增量，但未做一次登录后内部探针 |
| 从基线第二次完整复跑 | NOT TESTED | 当前只有一次真实安装样本 |
| DeepSeek 插件单独 uninstall | NOT TESTED | 整套 `--state` 删除已移除插件状态，但不能代签独立插件生命周期 |
| 真实客户远程连接/中止/撤权 | NOT TESTED | 未测试 Quick Assist、RDP、SSH 或 WinRM；本地 VirtualBox 控制不等同客户远控 |
| 旧 Hub/WSL 路线 | FAIL/STOP（不变） | 当前环境不支持嵌套虚拟化；原生路线不能把旧 Hub 包改写为通过 |

## 真实模型调用

一次本地 Gateway-backed 调用成功，未向消息渠道投递：

- provider：`deepseek`
- model：`deepseek-v4-pro`
- fallback：未使用
- 工具调用：0
- input/output/total tokens：`5849 / 10 / 5859`
- OpenClaw 记录的调用成本：`USD 0.002553015`
- 端到端响应时间：约 `5681 ms`

用户已在 DeepSeek 控制台撤销本次测试密钥。密钥本身不在最终报告或文本证据中；一张确认曾显示密钥的本地截图已移入 Windows 回收站，不纳入交付。

## 诊断器结论

### 官方 OpenClaw 诊断导出

故障状态下导出成功，ZIP SHA-256 为 `0230E4E37B690D693FCD6C53B75708D5ED830D2CD296680C7549F28BE59F4938`。扫描未发现 API Key，但发现一次合成对话标记，位置为 `logs/openclaw-sanitized.jsonl` 的 `msg`。因此该 ZIP 仅保留为缺陷证据，标记为 **DO NOT SHARE**。

### XQ 生产诊断器

VM 中运行的是构建包解压出的生产脚本、脱敏模块和策略：

- `OpenClaw-XQ-Diagnose.ps1`：`3D3156C517026022CB712C33811EBAEA79689AD4F11D61BAC99200CE8BB0585C`
- `XQ.Redaction.psm1`：`221B8E6E63759A2F37597EB037CE02A379113D52CFD68BCFA1D0F6C3310BED91`
- `diagnostic-command-policy.json`：`DC325439CBAEFC7FA79C0C15E20E0AD5D70880E57294CBF315BB48CC0432C52D`

包闭包、缺失策略 fail-closed 和脱敏均通过；但 named-profile 诊断语义失败。后续最小修复应让 native adapter 显式携带当前 profile，或以受支持方式把 state/config/port 传给每个只读命令，并新增“命名 profile + 非默认端口”的回归。

## 卸载、回退与最终状态

官方卸载先 dry-run，再移除 service/state/workspace；随后用安装所有者的私有 npm prefix 移除 CLI。卸载后验证为：任务 0、18789 listener 0、相关进程 0、profile state 不存在、workspace 不存在、OpenClaw package/CLI/shim 不存在。私有 Node staging 当时仍存在，随后已通过恢复离线基线整体丢弃。

最终状态：

- 源 `PersonalMessengerQQ`：`poweroff`，未启动、未改源磁盘或 QQ 草稿。
- 测试副本：`poweroff`。
- 当前快照：`XQ-OpenClaw-Baseline-Offline-01`，UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e`。
- NAT 配置保留但虚拟网线断开；clipboard/drag-and-drop disabled。
- Hermes 未启动；宿主机配置未改；没有提交、推送或发布。

## 自动履约判断

本次证明：在已有、受控、可恢复的 Windows VM 中，用户完成安全登录和模型凭据操作后，智能体可以独立完成精确版本安装、配置、真实调用、重启、故障恢复、诊断、卸载和快照回退。

仍不能证明“普通客户订单几乎无需创始人介入”。本轮用户必须参与 Windows 登录、DeepSeek 登录/密钥创建与保存、重启后登录和云端密钥撤销；复制粘贴还出现过明显障碍。更关键的是，真实客户 transport 与撤权没有测试，生产诊断器仍有 named-profile 缺陷。可实现的近期目标应是“客户只做登录/授权/凭据，AI 完成其余标准流程”，而不是完全无人参与。

## 证据路径

主要机器可读证据位于 `work/agent-kits-tech/native-pilot/`：

- `guest-native-package-official-spec.json`
- `guest-native-gateway-service-check.json`
- `guest-native-deepseek-readiness.json`
- `guest-native-deepseek-conversation.json`
- `guest-native-gateway-restart.json`
- `guest-native-fault-recovery.json`
- `guest-native-diagnostics-stopped.zip`（**DO NOT SHARE**）
- `guest-production-diagnostic-regression.json`
- `guest-production-diagnostic-report.txt`
- `guest-native-uninstall-validation.json`
- `guest-native-shutdown-request.json`

本报告、`HANDOFF.md`、`PROJECT_STATE.md` 和 `DECISIONS.md` 是交接入口。原 `TEST-RESULTS.md` 保留为旧 Hub/WSL 路线结果，不改写。

## 下一步

1. 修复 XQ 生产诊断器对命名 profile/非默认端口的传递，并加生产包回归。
2. 将官方 ZIP 的 payload 泄漏登记为上游/包装层阻断：对外支持包不得直接采用该 ZIP，除非增加二次 allowlist 过滤并证明无消息正文。
3. 在同一离线基线再做一次完整复跑，验证重复性与恢复后 guest 内无残留。
4. 选择一种真实客户通道（优先客户已有方式；否则 Quick Assist 监督式候选），测试接入、客户中止、会话断开和权限撤销。
5. 完成提交后的干净 HEAD Formal 门和正式制品验证；在此之前继续 `DO_NOT_SELL`。
