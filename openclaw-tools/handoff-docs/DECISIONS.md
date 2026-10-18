# DECISIONS

## D-001：阶段 A 不解除销售门

决定：两个包均保持 `DO_NOT_SELL`。  
理由：OpenClaw 的诊断器 P0 已在本地修复并回归通过，但尚无真实 VM、runtime、重启、远程会话和 Formal 构建证据；Hermes 暂缓且仍是全零/draft、CI 失败。

## D-002：OpenClaw 为第一套 VM 候选

决定：阶段 B 默认先测 OpenClaw。  
理由：其 locked installer、官方 digest、SHA-256、签名和许可链已闭环；剩余首要缺口集中且可最小修复。  
条件：若 VM 缺 WSL2/嵌套虚拟化，报告环境不匹配和最低成本下一步，不修改宿主机，也不自动启动 Hermes 全套修复。

## D-003：远程安装是新 SKU/新验收链

决定：不通过修改现有说明把自助包测试算作远程服务通过。  
要求：独立的授权、临时连接、命令 allowlist、凭据隔离、会话撤销、日志留存、回滚和异常升级证据。

## D-004：标准交付优先官方直下，不默认重分发

决定：远程服务优先在客户机从官方 URL 下载，核对已批准 hash/签名后安装。  
理由：降低第三方再分发、制品过期、存储和支持成本。离线包若捆绑二进制，另做 license/notices 与交付 hash 门禁。

## D-005：Hermes 不用 mutable Desktop URL 代替 runtime pin

决定：当前官网 `Hermes-Setup.exe` 的签名有效，但不能据此声称安装 `v0.20.6`。  
要求：使用 exact `install.ps1 -Commit <sha>`，或取得与 commit 绑定的 signed release asset；manifest 重新 formalize 后再测。

## D-006：首版不保留无人值守权限

决定：每单使用一次性、可撤销会话；结束时证明远程权限已撤销。  
理由：最小化客户信任、隐私和售后安全风险；当前两包也均未覆盖无人值守服务。

## D-007：区分创始人介入与客户必要参与

决定：业务目标定义为“标准订单创始人技术操作接近零”，不是“无人参与”。  
仍需客户本人完成：授权、必要的 UAC/重启登录、API Key/OAuth/验证码输入。异常先升级高级 AI 复核；超出授权或无法安全恢复时暂停、回退或退出，不默认要求创始人或另一个人工团队修电脑。

## D-008：Test 包必须携带生产诊断器及其真实依赖

决定：Formal 与 Test 构建都从仓库生产 `tools/` 和 `policy/diagnostic-command-policy.json` 取诊断组件；Test 的安装器与其余供应链输入保持合成 fixture。  
理由：这样回归能暴露“源码存在但交付 ZIP 漏文件”，同时不把 fixture 包冒充真实安装或 release。

## D-009：远程通道按用户已有条件选择

决定：第一台 VM 优先使用用户已有的控制台、浏览器控制台、RDP、SSH 或 WinRM，不要求先安装另一套服务器。普通客户路径把 Quick Assist 列为监督式低配置候选，但必须实测 AI 对远程画布的控制、动作确认和撤权。  
理由：SSH/WinRM 适合已配置环境，但不是普通客户的先验唯一方案；本轮不建设自有 RMM，也不通过换工具规避适用确认规则。

## D-010：原生 CLI/Gateway 是独立候选，不替代 Hub 商品

决定：OpenClaw `2026.9.4` Windows 原生 CLI/Gateway 可继续作为“本地 Gateway + WebChat”候选，但不把它写成 Windows Hub/WSL、托盘、Windows node 或 MCP 商品的等价通过。  
理由：原生链路已真实完成一次安装、模型调用、重启、故障恢复和卸载；旧 Hub 路线仍受嵌套虚拟化阻断，产品体验和能力边界也不同。

## D-011：官方诊断 ZIP 不进入客户售后包

决定：`guest-native-diagnostics-stopped.zip` 标记 `DO NOT SHARE`，只保留为缺陷证据。  
理由：虽然 API Key 扫描为 0，ZIP 的 sanitized log 仍保留合成对话标记，不能满足“不含消息正文”的售后证据标准。若未来使用，必须增加包装层 allowlist 过滤并独立回归。

## D-012：生产诊断器 named-profile 为新阻断

决定：XQ 生产诊断器的包闭包、策略 fail-closed 和脱敏保持通过，但原生健康诊断整体降为 `PARTIAL/FAIL`。在修复前，不以命令退出 0 宣称 Gateway 健康。  
理由：native adapter 没有正确沿用 `xq-native-pilot`，状态命令读取默认配置并探测错误端口，RPC 实际失败。下一修复必须覆盖命名 profile、配置路径、服务端口和 RPC 断言。

## D-013：凭据生命周期仍由客户本人完成

决定：模型服务登录、API Key 创建/保存和云端撤销继续由客户在安全界面完成；标准化目标是“客户只做登录/授权/凭据，AI 完成其余流程”，不承诺无人参与。  
理由：本轮证明智能体可完成大部分技术步骤，但凭据复制粘贴出现障碍，且安全策略不允许把长期密钥当作聊天参数。曾显示密钥的本地截图已移入回收站，测试密钥已撤销。

## D-014：一次成功不解除重复性与远程门

决定：保持 `DO_NOT_SELL`，直到至少完成一次从离线基线的完整复跑、修复生产诊断器 named-profile、验证一种真实客户远程通道及撤权，并通过提交后 Formal 门。  
理由：当前只有一个 VM 样本，且 VirtualBox/Guest Control 是内部控制面，不是普通客户远程履约证据。
