# PROJECT_STATE

更新时间：2026-09-16（Asia/Shanghai）

- 当前阶段：OpenClaw 官方 Windows 原生 CLI/Gateway 试验已完成一次真实链路，已卸载并恢复离线基线；Hermes 继续暂缓。
- 总体状态：`DO_NOT_SELL`。原生技术路径可行，但生产诊断 named-profile 失败、官方诊断 ZIP 含对话标记、真实客户远控/撤权和第二次复跑未完成，Formal 构建未完成。
- 原生候选：OpenClaw `2026.9.4`（commit `3a9d69db306cd7f081e06254cb89c4bcc14a7107`）、Node `26.8.2`、DeepSeek provider `2026.9.4`。
- 原生链路：私有目录安装、loopback-only Gateway、DeepSeek V4 Pro 一次真实调用、页面重开、Gateway 重启、Windows 重启后登录恢复、故障恢复和官方卸载均通过。
- 真实调用：provider/model 为 `deepseek/deepseek-v4-pro`，无 fallback、无工具、无外部投递；5859 total tokens；记录成本 `USD 0.002553015`。
- 凭据：测试密钥已由用户在 DeepSeek 云端撤销；文本证据未发现密钥模式；一张曾显示密钥的截图已移入 Windows 回收站。
- 官方诊断 ZIP：API Key 扫描为 0，但日志 `msg` 含合成对话标记，标记 `DO NOT SHARE`。
- XQ 生产诊断器：包闭包、缺策略 fail-closed 和脱敏通过；native adapter 未正确沿用 `xq-native-pilot`，读取默认 `.openclaw` 并探测错误端口，状态为 `PARTIAL/FAIL`。
- 卸载：官方 service/state/workspace 删除与 npm CLI removal 均通过；任务、端口、相关进程、state、workspace、package 和 shim 均为 0/不存在。
- 回退：专用克隆已恢复 `XQ-OpenClaw-Baseline-Offline-01`（UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e`）；当前 `poweroff`、NAT 网线断开、clipboard/drag-and-drop disabled。恢复后未再次启动 guest 做内部残留探针。
- 源 VM：`PersonalMessengerQQ`（UUID `7592dfa4-c60c-4818-921e-ced5c626254b`）最终 `poweroff`；源磁盘和 QQ 草稿未打开或修改。
- 旧 Hub/WSL 路线：`ENV-NESTED-VIRT-UNAVAILABLE` 结论不变；原生路径不代签旧包装层通过。
- 重复性：仅一次真实安装；从基线第二次完整复跑 `NOT TESTED`。
- 远程履约：Quick Assist/RDP/SSH/WinRM 接入、中止、断开和撤权均 `NOT TESTED`；VirtualBox/Guest Control 只算内部执行能力。
- 客户必要参与：Windows 登录、模型服务登录/API Key 创建与保存、重启后登录和云端撤销；尚不能承诺零客户参与或零创始人例外。
- OpenClaw 产品仓库 HEAD：`6b4fd067f90fbad6ca7cfc0ac89befba9b3f6f62`；原 3 个最小修复仍未提交：`build/Build-Package.ps1`、`policy/allowed-package-files.json`、`tests/Package/M6.Package.Tests.ps1`。named-profile 新缺陷尚未修。
- Hermes HEAD：`88747d400ddd662c20883ad5227677a3b1a32a57`，未启动本轮工作。
- 未提交、未推送、未发布；宿主机虚拟化/WSL/Docker/安全设置未改；新增采购 0 元。
- 主结果：`outputs/agent-kits-tech/NATIVE-PILOT-RESULTS.md`；交接：`outputs/agent-kits-tech/HANDOFF.md`。原 `TEST-RESULTS.md` 保留旧 Hub/WSL 结论。
