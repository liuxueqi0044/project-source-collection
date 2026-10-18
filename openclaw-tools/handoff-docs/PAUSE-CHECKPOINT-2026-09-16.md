# OpenClaw 原生 Windows 试验暂停检查点

日期：2026-09-16（Asia/Shanghai）

## 当前结论

OpenClaw 官方 Windows 原生 CLI/Gateway 路径已在专用克隆中完成安装、服务、故障恢复和生产诊断器回归。DeepSeek 官方 provider 已安装并完成无凭据配置，但尚未完成 API Key 接入和真实模型调用，因此当前不能判定“模型端到端通过”，也不能算作旧 Hub 安装包通过。

## 安全停机状态

- 原虚拟机 `PersonalMessengerQQ`：`poweroff`，未修改、未启动旧项目。
- 测试副本 `XQ-OpenClaw-Test-01`：UUID `5d948da3-2078-45bf-92b4-151de5412753`，当前为 `saved`。
- 正常关机在 45 秒内未结束，因此采用 VirtualBox 保存状态；没有强制断电。
- 共享剪贴板：`disabled`。
- 剪贴板文件传输：`off`。
- 基线快照：`XQ-OpenClaw-Baseline-Offline-01`，UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e`。
- Hermes 未启动；宿主系统配置未改；未推送、未发布。
- DeepSeek 凭据尚未保存进 OpenClaw。聊天中曾暴露的测试密钥应在 DeepSeek 控制台撤销；重置虚拟机不会使云端密钥失效。

## 已完成

- OpenClaw 核心固定为 `2026.9.4`，官方发布提交 `3a9d69db306cd7f081e06254cb89c4bcc14a7107`。
- Node.js 固定为 `26.8.2`，使用私有安装目录，不改宿主机和原虚拟机。
- Gateway 仅监听 `127.0.0.1:18789`；健康检查和只读 RPC 已通过。
- Windows 登录触发的计划任务可重启 Gateway；无人登录时不能自动恢复，已作为生产限制记录。
- 停止 Gateway 的故障注入、诊断器识别、无消息正文诊断包导出和官方启动恢复均已通过。
- DeepSeek 官方插件固定安装为 `@openclaw/deepseek-provider@2026.9.4`：
  - OpenClaw 识别为 `trustedOfficialInstall: true`；
  - npm integrity：`sha512-W8XdQR9ncwQxPY2wjQtMCCxz/x2xnE+ZEjTD2YgefPH5wjD9COZk+BO58sscRCNKjjQU+LwAweyjy7/DQ7KjGQ==`；
  - 本地下载包 SHA-256：`F6D495816D92B19B1F6B92EB47402B7D2EE278B302062CAE8BB8C33632065D9A`；
  - 无额外运行依赖，peer dependency 为 `openclaw >=2026.9.4`。
- 插件白名单已收紧为仅 `deepseek`；`openai` 和 `codex` 条目已停用。
- 默认模型已设为 `deepseek/deepseek-v4-pro`。旧 `deepseek-chat` / `deepseek-reasoner` 不使用。
- 无凭据状态验证正确：DeepSeek auth profile 为空，界面显示“未配置”，没有误报可用。
- 发现并修正了生产自动化竞态：provider 插件首次安装后，必须先完成插件策略写入，再使用较宽容的 `models set` 固定模型，最后重启 Gateway；不能在插件加载前通过严格 `config patch` 同时写模型。

## 尚未完成

1. 在测试副本内登录 DeepSeek 控制台，撤销已暴露的旧密钥并创建新测试密钥。
2. 在同一虚拟机浏览器内复制新密钥，切回 OpenClaw“设置 → 模型”保存；不得把新密钥发到聊天。
3. 验证认证状态、模型目录和 DeepSeek 连接测试。
4. 执行一次不投递到外部渠道的真实对话，并核对模型、响应和最小用量。
5. 完成带凭据状态下的 Gateway 重启、应用关闭/重开和诊断器回归。
6. 导出脱敏证据后卸载/回滚测试环境，并恢复基线快照。
7. 更新最终 `NATIVE-PILOT-RESULTS.md`、`HANDOFF.md`、`PROJECT_STATE.md` 和 `DECISIONS.md`。

## 下次恢复入口

恢复 `XQ-OpenClaw-Test-01` 保存状态后，Edge 当前停在 DeepSeek 官方登录页；另一个标签页保留 OpenClaw 模型设置。用户完成 DeepSeek 登录及密钥保存后，再继续自动化验证。恢复工作前应确认共享剪贴板仍为关闭，避免把测试凭据跨虚拟机暴露。

主要无密钥证据保存在：

- `work/agent-kits-tech/native-pilot/guest-native-deepseek-only.json`
- `work/agent-kits-tech/native-pilot/guest-native-gateway-service-check.json`
- `work/agent-kits-tech/native-pilot/guest-native-fault-recovery.json`
- `work/agent-kits-tech/native-pilot/guest-native-diagnostics-stopped.zip`
- `work/agent-kits-tech/reviews/OPENCLAW-NATIVE-TERRA.md`

