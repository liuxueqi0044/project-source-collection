# 两个 Agent 配置包阶段 A 技术审查

日期：2026-09-15（Asia/Shanghai）  
负责人：Sol  
协作：Terra 子智能体分别审查 OpenClaw 与 Hermes 仓库；Sol 负责上游真实性、制品签名、CI 实况、远程履约架构与最终判断。

## 结论先行

两个包目前都不能作为“已验证、可销售、可由 AI 远程履约”的商品。

| 包 | 上游真实性 | 包装层 | 真实 VM | AI 远程履约 | 阶段 A 决定 |
|---|---|---|---|---|---|
| OpenClaw | 锁定的 `v2026.7.1` 安装器、哈希、签名、许可证均已独立核对通过 | **存在 P0：交付 ZIP 缺诊断策略文件，承诺的诊断器会启动失败** | 未测 | 新 SKU/新链路，未测 | **禁止销售；修 P0 后优先进入阶段 B** |
| Hermes | 官方项目、`v2026.8.27` 标签及官方 Desktop 下载入口真实；但 Desktop EXE 与该标签没有已证明的不可变绑定 | **manifest 为 draft，全零 commit/hash；CI 当前失败；诊断文档参数错误** | 未测 | 仅有 human-assisted 文案，无远程工具/授权/审计闭环 | **禁止销售；不作为第一套 VM 候选** |

推荐顺序：先测 OpenClaw。若目标 VM 不支持 WSL2/嵌套虚拟化，则不要硬改宿主机，改用 Hermes 的“官方脚本 + 精确 commit”作为远程履约链路备选试验；这个备选只验证新服务路径，不代表现有 Hermes 配置包通过。

对“尽量减少逐单介入”的判断是：

- **创始人技术操作在标准订单上接近零，有条件可行**：必须使用受限的 SSH/WinRM/同等命令通道、固定脚本、自动证据和异常升级队列。
- **客户参与不可能为零**：至少需要授权远程会话、处理可能的 UAC/重启，并由客户本人输入 API Key/OAuth/验证码。
- **当前不能承诺任意客户电脑可由本线程直接远程操作**：本次可验证的能力只有本地 shell、SSH/WinRM 客户端和浏览器；Computer Use 枚举到的原生 App 表面为空。RDP、Quick Assist、AnyDesk 或浏览器远程桌面均未实测。
- 若通过 GUI 远程桌面安装，当前 Computer Use 规则会在安装新软件、改系统设置等动作前要求逐次确认，不适合作为“创始人零介入”的默认链路；命令式、可审计、可撤销的远程通道更合适。

## 审查范围与基线

本轮只读，不修改两个仓库，不推送，不触发发布，不运行安装器，不修改宿主机 WSL/Docker/虚拟化设置。

| 仓库 | 审查 checkout | HEAD | 状态 |
|---|---|---|---|
| `liuxueqi0044/xq-openclaw-windows-kit` | `C:/Users/xiayu/Documents/Codex/2026-08-28/github-chatgpt-conversation-6a9128cc-6290-83e8-2/work/openclaw-public-audit` | `6b4fd067f90fbad6ca7cfc0ac89befba9b3f6f62` | `main...origin/main`，干净 |
| `liuxueqi0044/xq-hermes-agent-windows-kit` | `C:/Users/xiayu/Documents/Codex/2026-08-28/new-chat-2/xq-hermes-agent-windows-kit` | `88747d400ddd662c20883ad5227677a3b1a32a57` | `main...origin/main`，干净 |

两个仓库内及其已检查上级均未发现额外 `AGENTS.md`。OpenClaw 只运行了只读产品策略测试和 PowerShell 语法解析；Hermes 运行了只读 CI 策略检查。没有把合成 fixture 通过写成真实安装通过。

## OpenClaw：上游与完整 Agent 体验

### 已独立核验的事实

- 官方仓库与发布页存在：[`openclaw/openclaw-windows-node` 的 `v2026.7.1`](https://github.com/openclaw/openclaw-windows-node/releases/tag/v2026.7.1)。标签所指发布提交为 `4206611f2b2e47cd575e1c8aa94e3e7708f6dd96`。
- 官方展开资产页发布的 x64 SHA-256 为 `0fad205697dc92a020be4c5809ecdf2f383258f5a54914922399787e1a4afa1`，见[官方资产清单](https://github.com/openclaw/openclaw-windows-node/releases/expanded_assets/v2026.7.1)。
- 本轮从该官方发布 URL 下载完整 `122,306,488` 字节安装器，实算 SHA-256 与上值完全一致。
- Authenticode 当前验证为 `Valid`；签名主体为 `OpenClaw Foundation`，证书指纹 `74F0719953150F0B55D447D608D6A764A057BB55`，有 Microsoft 时间戳。以上均与包内 `manifest/upstream-lock.json` 一致。
- 精确提交下的 MIT `LICENSE` 和 `THIRD_PARTY_NOTICES.md` 实算哈希分别为 `E2BC86731271165CEAE2C2A80AA0F33FF399831662572ECDAFB7F3249F917AEB` 与 `E5F18325B793BA59E70279FC778CEA919CA2A7E5A75878EA597722517A51D9DE`，与 lock 一致。
- Windows Hub 不是“只有节点、缺 Agent”。[官方 Windows 文档](https://docs.openclaw.ai/platforms/windows)明确说明 Hub 可在首次启动时创建 app-owned WSL Gateway，并提供原生聊天、Control UI、节点能力和本地 MCP；因此锁定路径可以覆盖“本地 Agent + 一次基本对话”。

GitHub API 的匿名限额在审查时已耗尽，因此 `releaseId` / `assetId` 两个内部数值没有通过 API 再核一遍；但官方发布 URL、官方发布 digest、完整文件哈希和签名均已闭环，这个缺口不阻断阶段 B。

### 包装层的阻断问题

1. **P0：ZIP 内诊断器不可运行。** 生产脚本默认读取 `../policy/diagnostic-command-policy.json`（`tools/OpenClaw-XQ-Diagnose.ps1:13`），但 `policy/allowed-package-files.json:4-8` 没有允许该文件，`build/Build-Package.ps1:156-158` 也没有复制它。解压后的诊断脚本会在策略初始化时失败。
2. **M6 没覆盖生产诊断器。** `tests/Fixtures/m6/.../OpenClaw-XQ-Diagnose.ps1` 只是单行 fixture；M6 成功只能证明打包逻辑，不证明实际交付脚本可启动。
3. **真实 VM 与正式发布证据为空。** M9 只有 pending 模板；README 自己也标注最终 ZIP 和中英文 VM 验收未完成。
4. **最后一次 GitHub Actions 成功不是当前 HEAD 的完整证明。** [`Build candidate package #1`](https://github.com/liuxueqi0044/xq-openclaw-windows-kit/actions/runs/33233005641) 在 `918449bde303a301ff5b893e0537f564dda3d3e1` 上成功，当前审查 HEAD 是 `6b4fd067…`。该 run 产出 116 MB artifact，digest 为 `sha256:cc18fa25ae84ea68f93584a9794a760ca9282fa4a78eb1019b1566ea77c795ea`，现已过期；运行本身也没有捕获上面的生产诊断闭包错误。

### 可复用部分

- fail-closed 的上游锁、SHA-256、Authenticode、许可证哈希和 release 身份校验。
- 白名单打包、reparse point/ADS 防护、秘密扫描、确定性 ZIP 与交付哈希。
- 只读诊断的命令 allowlist、超时和脱敏框架。
- 清楚的非官方关系声明、凭据禁收和支持范围。

### 最小修复与估时

- 将诊断策略文件纳入 allowlist 和 package copy，并新增“解压后生产诊断器初始化成功”测试：**0.5 天**。
- M6/M7 改用生产 tools + 合成 EXE，保留真实/合成证据标签：**0.5–1 天**。
- 仓库级秘密扫描与 fixture 显式豁免：**0.5 天**。
- 干净 Windows 11 x64 VM 的安装、对话、重启、失败恢复、卸载与远程会话证据：**1–2 个工作日 + 网络/重启等待**。

## Hermes：上游、Desktop 与版本锁

### 已独立核验的事实

- 官方仓库为 [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)。`v2026.8.27` 是真实 release，版本 `0.20.6`，release commit 为 `5fc308a70719a83cccdbba4c0e39c23f5a8239d5`。
- 该 release 的[官方资产页](https://github.com/NousResearch/hermes-agent/releases/expanded_assets/v2026.8.27)只有 source zip/tar，没有 Windows Desktop 二进制。因此不能把独立网站上的 `Hermes-Setup.exe` 自动归入这个 release。
- [官方 Desktop 页面](https://hermes-agent.nousresearch.com/desktop)当前 Windows 链接是 `https://hermes-assets.nousresearch.com/Hermes-Setup.exe?build=4d55ca91656a`。查询中的 `4d55ca91656a` 确实是 Hermes 仓库提交，但它只是 2026-09 的网站文档提交，并不是 `v2026.8.27` 的 Desktop build 证明。
- 该 URL 返回一个 `7,946,048` 字节、产品版本 `0.0.1` 的 signed bootstrap。实算 SHA-256 为 `CFC818ADF831A748C61A407152A03C7A426EBEE78F499D20A31FAE2B5AC5D827`；Authenticode 为 `Valid`，签名主体 `Nous Research Inc.`，指纹 `73319CF939C2073A5066D185EEACDC82797D8C79`，有 Microsoft 时间戳。
- manifest 里的 `?build=000000000000` 当前也返回相同 `ETag`、长度和 Last-Modified；这说明 query 不能当作不可变版本身份。
- 精确 `v2026.8.27` 源码的 `scripts/install.ps1` 支持 `-Commit` / `-Tag`，而 bootstrap 的[构建逻辑](https://github.com/NousResearch/hermes-agent/blob/5fc308a70719a83cccdbba4c0e39c23f5a8239d5/apps/bootstrap-installer/src-tauri/build.rs)明确写明：commit pin 是 opt-in；默认只跟踪 branch HEAD。对本轮下载的 EXE 做静态字符串检查，没有找到 Hermes 上游完整 commit pin，只找到 `main`/build-pin 机制。故该 EXE 本身不能证明会安装 `0.20.6`。
- [官方 Windows 指南](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/windows-native.md)说明原生 Windows 10/11 无需 WSL，默认不需管理员；但首次安装会下载/配置 Python、uv、Git、Node 等依赖，网络和更新行为必须在 VM 实测。

### 包装层的阻断问题

1. `product-manifest.json` 仍是 `buildMode: draft`；`upstreamCommit`、`desktopBuildCommit`、runtime pin、installer hash、license hash 全零，waiver 为 `DRAFT-NOT-FOR-BUILD`。formal 校验按设计失败。
2. 当前 CI 真失败：[`Validate #1`](https://github.com/liuxueqi0044/xq-hermes-agent-windows-kit/actions/runs/33244828377) 在当前 HEAD `88747d4…` 的 `Validate CI policy` 步骤 exit 1；后续 schema/AST、M1–M10 均为 0 秒未执行。不能把 UI 的总失败解读为所有测试失败，也不能把旧 113/113 文件当当前证据。
3. 失败根因是 `ci/Validate-CI.ps1:32` 的 regex 在 CRLF 工作树把换行吞进 `uses:` 值，误报两个已固定 action 为浮动引用；本地可复现 35 项中 2 项失败。
4. `content/guide/06-diagnose-usage.md:14` 写 `-OutputPath`，脚本实际参数是 `-OutputDirectory`，用户按文档会失败。
5. unsigned waiver 只要求任意非空 `waiverId`，没有审批人、原因、期限与 artifact hash 绑定，能绕过正式签名要求。虽然本轮官网 bootstrap 实际签名有效，也应修紧该制度缺口。
6. `remoteBasicInstall` 只是 human-assisted 范围文案，没有远程通道、客户同意记录、逐步授权、断连、凭据隔离、审计、回滚或 AI 异常升级实现。

### 可复用部分

- 草稿/零值/浮动引用的 formal fail-closed 框架。
- 安全门：路径逃逸、重解析点、ADS、敏感文件名、秘密模式、ZIP 双扫描、Defender 检查。
- Draft Release + 人工环境批准 + 不可覆盖 tag/release 的治理骨架。
- native Windows 官方路径可作为未来低权限远程安装候选。

### 最小修复与估时

- 修复 CI regex，并加 LF/CRLF 回归：**0.5 小时**。
- 更正文档参数及“不联网”承诺：**0.25 小时**。
- 重新做正式上游锁：决定保留 `v2026.8.27` 还是选择新候选，记录 exact commit、license hash、Desktop bootstrap hash/signature；若要可复现 runtime，采用 exact `install.ps1 -Commit <sha>` 或获得与 commit 绑定的官方 signed asset：**0.5–1 天**。
- 将签名 waiver 改成默认禁止、审批/原因/期限/hash 绑定的不可变证据：**0.5–1 天**。
- 真实 Windows 11 x64 VM 安装/重启/对话/恢复/卸载：**1–2 个工作日 + 网络等待**。

截至 2026-09-15，官方 release 列表已有 `v0.21.3 (v2026.9.14)`；这说明上游迭代很快。不能自动把旧 manifest 换成 latest，也不能长期跟踪 `main`。每个拟售版本都必须重新审查并形成独立候选。

## 许可与分发

- OpenClaw Windows Hub 与 Hermes Agent 顶层代码均为 MIT；MIT 允许商业使用和再分发，但要求在软件副本或实质部分保留版权和许可声明。
- OpenClaw 已提供第三方 notices，现有锁也记录该文件；真实交付仍要确认安装器内/包外 notices 完整。
- Hermes 的顶层 MIT 不自动替所有打包依赖重新许可，第三方依赖 notices 仍需逐项核对。
- XQ 仓库自身标为保留权利；仓库所有者可决定销售自己的内容，但不能把上游商标、二进制和依赖重新声明为 XQ 权利。
- **推荐远程服务默认不转售/镜像官方二进制**：在客户机器从官方 URL 下载，验证已批准的 hash 与签名，再安装。这样降低分发许可、过期制品、存储和售后成本。若离线 SKU 需要捆绑，则必须保留 license/notices 和不可变来源记录。

## AI 远程安装与售后：可行架构

### 当前能力事实

| 能力 | 本轮证据 | 判断 |
|---|---|---|
| 当前宿主本地 shell | PowerShell 可用，`ssh.exe`、`winrs.exe` 已安装 | 可用于脚本化连接，但目标端和认证尚未知 |
| 浏览器控制 | 已读取登录态私有 GitHub CI | 可用于网页型工单/下载/可能的网页远程桌面；远程 canvas 操作未验证 |
| 原生桌面 App 控制 | Computer Use `getState()` 返回 `apps: []` | 本线程不能把 RDP/Quick Assist 当已具备能力 |
| 测试 VM 直接控制 | VM 信息尚未提供 | 未验证；需 SSH/WinRM/可控浏览器会话或把 VM 作为当前执行环境 |
| 客户电脑远程控制 | 两包都没有真实 transport 与证据 | 未验证；不能销售承诺 |
| 无人值守售后 | 原 scope 排除，且安全/撤销/留存均未设计 | 首版明确禁止 |

OpenAI 的公开 API 只把 Computer Use 定义为控制一个提供给模型的计算机环境，并不替本项目提供任意客户电脑的传输层或授权链；当前实际工具表面才是本项目判断依据。[官方 API 参考](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses)

### 让创始人标准单接近零介入的最小服务系统

1. **订单预检**：客户自填 OS edition/build、架构、RAM/磁盘、网络/代理、是否可重启、WSL/虚拟化状态、语言、模型供应商；不收密钥。
2. **路由决策**：只接受落在已验证矩阵的订单；超出范围自动拒绝或进入人工报价，不能边做边扩大承诺。
3. **临时控制通道**：优先 SSH/WinRM/受限 RMM 命令 API，使用一次性账号/证书、主机指纹固定、最小权限、可审计命令 allowlist；客户可随时断开。不要默认常驻无人值守权限。
4. **标准安装器编排**：下载官方文件 → 校验 URL/size/hash/签名 → 安装 → 校验精确版本 → 启动。所有有副作用步骤都有预检、幂等检查、超时和回滚动作。
5. **客户专属步骤**：API Key/OAuth/验证码只在客户屏幕由客户本人输入；自动化不得读剪贴板、截图或完整配置，不得把秘密写进工单。
6. **自动验收**：一次真实模型对话、重启后可用、诊断无阻断、日志脱敏、卸载/回退检查；自动生成订单级证据和时间分解。
7. **会话收尾**：撤销一次性权限，确认无常驻服务/计划任务/远程账号残留，向客户展示结果。
8. **售后漏斗**：先让客户运行签名/固定哈希的只读诊断；支持 Agent 按 error code 给出 allowlisted 修复；同一阻断连续失败或超出矩阵才升级人工。

### 仍不可消除的参与

- 客户：远程授权、UAC/重启后的登录、模型凭据/OAuth/验证码输入。
- 经营方：首次建立远程通道、处理工具确认、安全审计和异常升级。要让“用户本人”接近零介入，需要把异常升级交给另一个明确的人/团队；AI 不能把未知异常率写成零。
- GUI 路径：当前 Computer Use 的安装/系统设置动作需要 action-time confirmation，因此会制造逐单点击；除非阶段 B 证明另一套可控、安全的 transport，不应以 GUI 作为默认大规模履约方式。

## 阶段 B 建议

### 第一套：OpenClaw

前置：修复 P0，或明确将“官方直装链路试验”和“XQ 包验收”拆成两条证据，绝不互相代签。

建议先在支持 WSL2 的干净 Windows 11 x64 VM 上测试官方 locked installer；完整记录 baseline、候选 hash、签名、安装/启动、一次真实对话、重启、一个典型失败、诊断、卸载/回退、会话撤销、AI/客户/用户时间。若 WSL2 或嵌套虚拟化不可用，立即停止，不改宿主机。

### 第二套：Hermes

必须先补 formal upstream lock。不要仅双击当前官网 bootstrap 就声称安装了 `v0.20.6`；优先用精确提交下的官方 `install.ps1` 并传 `-Commit 5fc308a70719a83cccdbba4c0e39c23f5a8239d5`，或者获得可证明与 commit 绑定的 signed release asset。若改选更新版本，重新生成候选和证据。

### 阶段 B 尚缺信息

- 指定 VM 名称、Windows edition/build/语言、x64/ARM64、CPU/RAM/磁盘。
- VM 是否支持快照/恢复，干净基线名称与可丢弃范围。
- 网络/代理/地域限制，WSL2/嵌套虚拟化状态。
- 连接方式：本地控制、SSH、WinRM、浏览器远程桌面或其他；认证须走安全输入，不把密码/密钥贴进聊天。
- 准备用于一次真实对话的模型服务和调用预算；没有预算就标未测，不用 mock 代签。

## 成本与边界

- 本轮新增采购：`0 元`。
- 既有总预算：`500 元`，未重置。
- 模型/API 实际成本：未知；不得写成零。
- 没有运行任何安装器，没有发布、上架、联系客户或修改远端仓库。

