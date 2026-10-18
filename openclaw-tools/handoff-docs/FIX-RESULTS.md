# OpenClaw 最小修复与生产诊断器回归结果

日期：2026-09-15（Asia/Shanghai）  
状态：**本地修复通过；可进入指定 VM 试验；仍为 `DO_NOT_SELL`**

## 本轮范围与协作

- 仅处理 OpenClaw ZIP 遗漏 `policy/diagnostic-command-policy.json` 的 P0；Hermes 暂缓。
- Terra 子智能体拥有 `tests/Package/M6.Package.Tests.ps1`，实现生产诊断器打包回归；Sol 拥有构建、白名单、集成、质量门和交接。
- 未运行安装器，未修改宿主机 WSL/Docker/虚拟化，未提交、推送、发布或联络客户。

## 修复结论

P0 已在本地工作树中关闭：

1. `policy/allowed-package-files.json` 显式允许且只允许新增的 `policy/diagnostic-command-policy.json`，未知文件仍默认拒绝。
2. `build/Build-Package.ps1` 将该策略复制到交付根的 `policy/`；Formal 与 Test 使用同一生产诊断器、脱敏模块和策略，避免测试继续借用单行 fixture 诊断脚本。
3. Test 模式的安装器、许可证和内容仍来自合成 fixture，ZIP 名称及 provenance 均明确为 `TEST-NON-RELEASE` / `test-fixture-non-release`，不计真实安装通过。
4. 新回归实际构建并解压两份 ZIP。成功副本在不传 `PolicyPath` 的情况下从解压目录运行生产诊断器并生成只读报告；失败副本删除策略后以非零退出，错误包含“无法读取只读诊断命令策略”，且不生成报告目录。
5. 回归还校验包内生产诊断器、脱敏模块和策略与源码对应文件 SHA-256 完全一致。

## 本地差异

仓库：`C:/Users/xiayu/Documents/Codex/2026-08-28/github-chatgpt-conversation-6a9128cc-6290-83e8-2/work/openclaw-public-audit`  
基线 HEAD：`6b4fd067f90fbad6ca7cfc0ac89befba9b3f6f62`  
状态：3 个已修改、未提交文件；无其他工作树差异。

- `policy/allowed-package-files.json`
- `build/Build-Package.ps1`
- `tests/Package/M6.Package.Tests.ps1`

差异规模：62 行新增、4 行删除。`git diff --check` 通过。

## 测试与证据

| 检查 | 结果 |
|---|---|
| 手工 Test 模式构建与 ZIP 条目检查 | 通过；策略与三份生产诊断文件均在包内 |
| M0 产品策略检查 | 通过 |
| 25 个 PowerShell 脚本静态解析 | 通过 |
| 锁定 Pester 5.7.1：受影响测试 | 19/19 通过，0 跳过 |
| 锁定 Pester 5.7.1：全量测试 | 63/63 通过，0 跳过 |
| 项目 Developer 质量门 | 通过；63/63、覆盖率 89.45%（门槛 85%）、PSScriptAnalyzer 1.25.0 通过且未跳过 |
| 最终生产诊断器包测试（增加 fixture provenance 断言后） | 2/2 通过 |

本地合成测试 ZIP：

`C:/Users/xiayu/Documents/Codex/2026-09-15/agent-a-c-users-xiayu-documents/work/agent-kits-tech/openclaw-fix/manual-build-48d9877ce06745618fc4ab1c69bb8926/XQ-OpenClaw-Windows-x64-v0.1.0-TEST-NON-RELEASE.zip`

SHA-256：`59C43D4D84DBE6F8B2B91AEF8DF4C4921C0C16964F975036D9C4302CAD6D91EE`。该 ZIP 含 15-byte 合成 EXE，不得安装、销售或描述为官方安装验证。

机器可读证据位于：

- `work/agent-kits-tech/openclaw-fix/pester-targeted.xml`
- `work/agent-kits-tech/openclaw-fix/pester-all.xml`
- `work/agent-kits-tech/openclaw-fix/pester-package-final.xml`
- `work/agent-kits-tech/openclaw-fix/developer-test-summary.json`
- `work/agent-kits-tech/openclaw-fix/developer-test-summary.json.report.json`
- `work/agent-kits-tech/openclaw-fix/developer-test-summary.json.coverage.xml`

Developer summary 仍带 `fixture: true`，且 commit SHA 是未修改基线 HEAD；它是本地开发证据，不可提交给 Formal 构建代签。Formal 门要求干净、已提交的当前 HEAD，本轮按“不推送或发布”没有运行。

## 最简远程履约候选

第一台 VM 不预设 SSH/WinRM 或 GUI。收到 VM 信息后优先复用用户已经能用的连接：

| 候选 | 客户/用户首次动作 | AI 可操作性 | 撤权 | 本轮判断 |
|---|---|---|---|---|
| 用户现有 VM 控制台、浏览器控制台或 RDP 会话 | 不新增配置 | 按实际窗口用 Browser/Computer Use；尚未连接目标 VM | 关闭会话或撤销临时凭据 | **首次试验首选** |
| Quick Assist 监督会话 | 客户打开应用、输入 6 位码、允许共享；全控时再允许一次 | 当前 Windows Computer Use 已能枚举原生应用，但尚未验证 Quick Assist 远程画布；helper 登录不能由 AI 代办 | 客户可取消控制或离开会话 | **普通客户低配置候选，需 VM 实测** |
| SSH/WinRM | 已启用时仅交付临时凭据；未启用时配置成本和网络暴露较高 | 命令式、可审计，适合自动化 | 删除临时凭据/账号并验证端口与会话 | **仅在用户已有时优先** |

微软说明 Quick Assist 的 helper 生成 6 位码，客户允许共享及全控，结束时任一方可离开；helper 需要 Microsoft 账户，客户一方无需登录，双方需通过 HTTPS/443 访问相关服务：[Microsoft Support](https://support.microsoft.com/en-US/Windows/Apps/solve-pc-problems-remotely-using-quick-assist)、[Microsoft Learn](https://learn.microsoft.com/en-us/windows/client-management/client-tools/quick-assist)。

当前 Computer Use 规则只在具体高风险 UI 动作前要求确认，例如运行新下载的软件或执行安装；并非每个 GUI 步骤都确认。它也禁止自动操作认证、安全软件和 Windows 安全/隐私设置。因此 Quick Assist 只能列为监督式候选，不能在实测前承诺无人值守或创始人零确认。本轮不建设自有 RMM，也不为了规避确认规则改换通道。

## 仍缺的真实证据

- 指定、可恢复 Windows 11 VM 上的预检、官方安装器复核与安装。
- 安装器实际取得的 Gateway/runtime 来源与版本；不能用外层 EXE hash 代替 runtime 锁。
- 一次真实模型对话、应用关闭重开、系统重启恢复、典型失败恢复、生产诊断、卸载/回滚。
- 一次真实远程连接、客户中止能力、会话结束和权限撤销证明。
- 从干净快照复跑一次的可重复性。
- 提交后的干净 HEAD Formal 质量门与正式构建；本轮没有授权提交、推送或发布。

## 时间与费用

- 本地执行墙钟约 20 分钟，其中约 4 分钟用于锁定测试依赖下载等待；机器等待未计成人工。
- 新增采购：0 元。
- Pester/PSScriptAnalyzer 从公开 PowerShell Gallery 下载到工作目录，未安装到系统。
- 模型/API 实际计费：当前不可见，按未知记录；未启动外部付费模型调用。

