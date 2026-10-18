# OpenClaw 官方 Windows 原生 CLI/Gateway 候选独立技术核查

核查时间：2026-09-16（Asia/Shanghai）。范围仅为公开官方一手资料、Git 远端和 npm Registry 的只读核验；未运行安装器、未改动仓库/VM/outputs，未读取任何既有 PMAI/QQ 或其他本机密钥。

## 结论

**候选：有条件通过用于“全新、终端优先的原生 Windows CLI/Gateway”试点；不应把它当作 Windows Hub 的等价替代，也不应直接批准生产机原地升级。**

* 固定候选为稳定版 `openclaw@2026.9.4`，Git tag `v2026.9.4`（发布页在核查时标为 Latest，非 beta）。tag 对象为 `8bec206f3c1f787e1e9c45cfd34d3de2a78c7b8e`，peeled target commit 为 `3a9d69db306cd7f081e06254cb89c4bcc14a7107`。
* 官方 release manifest 明确将该 target 标为 `releaseProfile: stable`，并绑定 package `openclaw-2026.9.4.tgz`、`sourceSha`/`targetSha` 为同一 commit；其自身 SHA-256 是 `c1007fb64bcabe05eadd95f4abd24a18ed803e146b5d82ac4db83b286161622f`。
* 该 manifest 的 Windows 交叉检查中，`installer fresh` 和 `packaged fresh` 成功；**`packaged upgrade` 失败，但被列为 advisory**。这不是“升级已验证”的证据。因此只批准干净环境试点；既有用户状态的升级要先备份、隔离演练并有回退路径。
* 原生 CLI/Gateway 路径确实不要求 WSL：官方 Windows 文档把 PowerShell 安装器列为 “Native Windows CLI and Gateway”，原生托管启动为 per-user Scheduled Task（不可建任务时用 Startup-folder fallback）。WSL2 是“最 Linux-compatible”的另一条路径，而非该 CLI 的先决条件。

## 已固定的供应链标识与完整性

| 项目 | 固定值 / 核验结果 |
| --- | --- |
| Release / source | `v2026.9.4` → commit `3a9d69db306cd7f081e06254cb89c4bcc14a7107` |
| 不可变 `install.ps1` | `https://raw.githubusercontent.com/openclaw/openclaw/3a9d69db306cd7f081e06254cb89c4bcc14a7107/scripts/install.ps1` |
| `install.ps1` SHA-256 | `a7b586f471fb02774ae6df47f1ac640addc58ff6c099da209310d3e2156523cc`（94,423 bytes，核查时实测） |
| npm 包 | `openclaw@2026.9.4`；bin：`openclaw.mjs`；`engines.node`: `>=24.16.0 <25 || >=26.1.0` |
| 固定 npm tarball | `https://registry.npmjs.org/openclaw/-/openclaw-2026.9.4.tgz` |
| tarball SHA-256 | `4f1f656770461d4677dea755b1899cba12b912b06798c89a59e2f0c18688b761`（实测；与 release manifest 一致） |
| Registry SHA-1 | `c377db97052ebbbc7ccc1ea9f466d38d12292c07`（实测匹配 `dist.shasum`） |
| Registry SRI | `sha512-lTQpEEe1Xm3u2PCHaPEr+vP8paGk1vLdHuzdItsNToaLI6hAqRVvgJYg+GxukJhETJp4tPy/S1Gftl4KuB8n7A==`（实测匹配 `dist.integrity`） |
| Registry 发布身份/证明 | metadata 显示 npm trusted publisher `GitHub Actions`（OIDC），并提供 SLSA v1 provenance attestation；同时有 npm signatures。这里验证的是 metadata、tarball 哈希与官方 release manifest 的一致性，未在本机执行 npm 生命周期脚本。 |

可审计来源：

* Release：<https://github.com/openclaw/openclaw/releases/tag/v2026.9.4>
* 固定源码树：<https://github.com/openclaw/openclaw/tree/3a9d69db306cd7f081e06254cb89c4bcc14a7107>
* 固定安装器：<https://github.com/openclaw/openclaw/blob/3a9d69db306cd7f081e06254cb89c4bcc14a7107/scripts/install.ps1>
* 固定 release manifest：<https://github.com/openclaw/openclaw/releases/download/v2026.9.4/openclaw-2026.9.4-release-manifest.json>
* npm version metadata：<https://registry.npmjs.org/openclaw/2026.9.4>
* npm attestation endpoint：<https://registry.npmjs.org/-/npm/v1/attestations/openclaw@2026.9.4>

**固定安装建议（仅供后续受控执行，不在本次执行）：** 不使用 `https://openclaw.ai/install.ps1` 的滚动内容，也不接受脚本默认 `latest`。先取得上面的 commit-pinned 原文件、按 SHA-256 验证，再以 `-Tag 2026.9.4` 调用。脚本自己的 npm 途径会执行 `npm install -g ...`，并允许 OpenClaw 生命周期脚本；固定版本不等于无代码执行。

## 平台与运行时要求

* **PowerShell：5+**。固定安装器在启动时显式拒绝低于 5 的宿主。Windows PowerShell 5.1 可满足最低项；PowerShell 7 亦可用。
* **Node：仅 Node `24.16.0+` 且 `<25`，或 `26.1.0+`。** 推荐固定 `Node 26.8.2`（官方提供 Windows x64/ARM64 zip/MSI）；Node 22、23、25 不在此候选支持范围。安装器还实际探测 `node:sqlite` 能力，不只看版本号。
* **npm：** 包没有声明独立的 npm engine。官方包说明：npm 12 或 npm 11.16+ 使用 `--allow-scripts=openclaw`；npm 11.15 及更早版本须省略该参数。安装器会按 npm 能力计算 lifecycle allow 参数。
* **Windows 版本/架构：** OpenClaw 对“原生 CLI/Gateway”没有发布明确的最低 Windows build，不能把 Hub 的要求偷换为 CLI 要求。可确认的是 Node 24+/26 的官方 Windows 分发只有 x64/ARM64（已没有 x86 预编译包）。Windows Hub 则单独声称 Windows 10 20H2+ / Windows 11。故候选准入应写为“受支持的 x64/ARM64 Windows 10/11，PowerShell 5+，且先在目标 build 验证”；对 Windows Server 或更老版本暂无 OpenClaw 官方版本下限证明。

来源：<https://docs.openclaw.ai/install/installer>、<https://www.npmjs.com/package/openclaw/v/2026.9.4>、<https://nodejs.org/en/download/archive/v26.8.0>、<https://nodejs.org/en/blog/migrations/v22-to-v24>、<https://docs.openclaw.ai/platforms/windows>。

## 安装、升级与引导的副作用

### `install.ps1`（默认 npm 方法）

1. 默认 `-Tag` 是 `latest`，所以必须显式传 `2026.9.4`。它先确保 Node，再确保 Git；即使 npm 安装，依赖需要 Git 时也会检查/补齐 Git。
2. 无可用支持 Node 时，依次尝试 `winget install OpenJS.NodeJS.LTS`、Chocolatey (`choco upgrade nodejs-lts -y --install-if-not-installed`) 与 Scoop；均不可用时下载“当时最新 Node 26” zip 到 `%LOCALAPPDATA%\OpenClaw\deps\portable-node`，并将其目录加入当前进程和**用户** PATH。该 fallback 是滚动的，因而不符合固定依赖要求；受控部署应先自行安装/固定 Node 26.8.2。
3. Git 不可用时可下载 user-local MinGit 到 `%LOCALAPPDATA%\OpenClaw\deps\portable-git`，并把其所需路径加入用户 PATH。安装器会改写当前进程 PATH，且可能持久追加 User `Path`；不写 PowerShell `$PROFILE`。
4. 用可写临时目录运行 npm：`npm install -g <fixed spec>`（脚本额外传 freshness/lifecycle 参数）。这会修改 npm **全局包目录**、npm cache，并运行包生命周期代码。安装完成后，若 `openclaw.cmd` 未可发现，会将 npm global bin（典型候选 `%APPDATA%\npm` 或 npm prefix）追加到 User `Path`。
5. 新装默认进入 `openclaw onboard`；`-NoOnboard` 才跳过。引导会创建/更新 OpenClaw 状态，配置模型，并可能检测现有 Claude Code/Codex CLI 登录或 API key、用真实模型 completion 验证。因此不能在不希望该探测行为的共享用户配置上做默认引导。
6. 脚本本身**不新建** Gateway 服务；但若发现服务已经 loaded，会先 `openclaw gateway install --force`，再 restart/status。升级或 git 安装随后跑 `openclaw doctor --fix --non-interactive`，它可以迁移状态/配置；所以升级不是只替换一个 npm 包。
7. `-NodeOnly -NodePrefix <绝对私有目录>` 会下载 Node zip、校验 Node 官方 `SHASUMS256.txt`、不安装 OpenClaw、也不改 process/user/machine PATH。它仍下载“当时最新 Node 26”，不等于版本冻结。

### 状态、HOME/profile、服务和网络

* 默认 HOME 解析源可由 `OPENCLAW_HOME` 覆盖，否则为 `HOME` / `USERPROFILE` / `os.homedir()`；默认 state 是 `%USERPROFILE%\.openclaw`，配置为 `%USERPROFILE%\.openclaw\openclaw.json`，默认 workspace 是 `%USERPROFILE%\.openclaw\workspace`。可由 `OPENCLAW_STATE_DIR`、`OPENCLAW_CONFIG_PATH`、`OPENCLAW_WORKSPACE_DIR` 改写。状态包含 config、SQLite 会话/认证状态、provider/channel credentials、日志和 agent workspace，属于敏感持久数据。
* Native Windows managed startup 是 per-user Scheduled Task：`OpenClaw Gateway`（profile 时 `OpenClaw Gateway (<profile>)`）。任务通过 state dir 内 `gateway.cmd` + 隐藏 `gateway.vbs` 启动；若 Task Scheduler 拒绝创建，退化为 per-user Startup-folder 登录项。没有 Windows Service/管理员服务承诺。`openclaw gateway install`、`onboard --install-daemon`、`configure` 或 `doctor` 可触发这条路径。
* Gateway 默认单一 HTTP+WebSocket 端口 `18789`，默认 bind=`loopback`（`127.0.0.1`）；端口优先级是 CLI `--port` > `OPENCLAW_GATEWAY_PORT` > config > 18789。它承载 Control UI、HTTP APIs、WS RPC 和 plugin routes。
* 默认要求认证；onboarding 默认生成 token。非 loopback（LAN/tailnet/custom）必须使用 token/password 或可信反向代理认证。`auth.mode: none` 是显式且仅应限制在可信 loopback 的例外，绝不能配合 `0.0.0.0`。建议维持 loopback，远程管理走 SSH tunnel/Tailscale Serve。
* 网络副作用包括：安装阶段 OpenClaw/npm、npm Registry 和依赖；没有 Node 时还可能访问 Node.js、winget/Chocolatey/Scoop/MinGit 来源；引导阶段访问已选模型供应商并进行真实 completion。包说明还称运行时默认每日做一次版本检查（可设 `update.checkOnStart: false`）；匿名 feature statistics 默认 opt-in。

来源：固定脚本（特别是参数/Node 检查、`Install-OpenClaw`、PATH、`Refresh-GatewayServiceIfLoaded`、`Run-Doctor` 与 `Main`）：<https://github.com/openclaw/openclaw/blob/3a9d69db306cd7f081e06254cb89c4bcc14a7107/scripts/install.ps1>；状态：<https://docs.openclaw.ai/help/faq/where-things-live-on-disk>；环境：<https://docs.openclaw.ai/help/environment>；Windows service：<https://docs.openclaw.ai/platforms/windows>；Gateway：<https://docs.openclaw.ai/gateway>、<https://docs.openclaw.ai/gateway/config-gateway>、<https://docs.openclaw.ai/gateway/security/network-exposure>。

## 用户进入一次模型对话的明确路径

先决条件是已完成模型配置/认证；首次默认引导本身会做一次真实 completion。

* **完全不启动 Gateway 的 CLI 本地会话：** `openclaw chat`（等同 `openclaw tui --local`）→ 输入消息 → Enter。它直接使用 embedded agent runtime；Gateway-only 能力不可用。
* **CLI + native Gateway：** 一个终端运行 `openclaw gateway`（Ctrl+C 结束）或先 `openclaw gateway install` 后使用受管任务；另一终端运行 `openclaw tui` → 输入消息 → Enter。裸 `openclaw` 会依配置状态自动进入 onboarding、Gateway TUI 或 local TUI，故不是可预测的生产入口。
* **网页 Control UI：** Gateway 运行后执行 `openclaw dashboard`。它用短时一次性 owner-pairing URL 打开浏览器，已配对浏览器获得持久 administrator device credential；`openclaw dashboard --json` 返回应优先打开的 `browserUrl`。首次 onboarding 成功也会自动打开 dashboard。这个 Web UI 是管理面（含 chat/config/exec approvals），不是无认证的普通网页。

来源：<https://docs.openclaw.ai/web/tui>、<https://docs.openclaw.ai/cli/dashboard>、<https://docs.openclaw.ai/start/getting-started>。

## 与 Windows Hub 商品承诺的差异

| 能力 | 固定 CLI/Gateway 2026.9.4 | Windows Hub 所承诺 |
| --- | --- | --- |
| 是否原生 Windows | 是；PowerShell + Node + npm 的 native CLI/Gateway，不需 WSL | Hub 应用是 native WinUI，但“Set up locally”默认会 provision **app-owned `OpenClawGateway` WSL distro** |
| 交互 | terminal TUI、浏览器 Control UI | 原生 chat 窗口、tray、setup、Settings、Command Center |
| 持续运行 | Scheduled Task / Startup-folder fallback，需 CLI 操作安装 | tray status、launch-at-login、连接管理 |
| Windows 特有能力 | 本次候选仅核实 CLI/Gateway；不等价包含桌面 node/MCP | Windows node（screen/camera/notification/device/talk/受控 `system.run`）和 local loopback MCP server |
| 远程连接 | CLI 可用 Gateway URL/token；安全基线 loopback + SSH/Tailscale | 可配置 local、WSL、remote 或 SSH-tunneled Gateway，并处理 pairing |

因此，若“Windows Hub 商品承诺”包含托盘、原生聊天、Command Center、Windows node 或本地 MCP，则**CLI 候选不足以履约**。若目标只是原生 Windows 上的可控 Gateway + TUI/Web chat，则此候选满足“无需 WSL”的技术路径，但仍有上述升级风险。

Hub 来源：<https://docs.openclaw.ai/platforms/windows>。

## 受控试点门槛与停止理由

建议只在干净、无敏感既有 state 的 x64/ARM64 Windows 10/11 测试用户上推进；预装固定 Node 26.8.2，使用 commit-pinned 安装脚本且验证 SHA-256，传 `-Tag 2026.9.4 -NoOnboard`，再在获授权后独立执行最小化引导。保持 `gateway.bind=loopback`、token auth、不开 LAN/public bind，并先记录/备份 `.openclaw`。

本审查在 30 分钟内完成且证据闭环，故无时间停止理由。唯一实质保留为 release manifest 已披露的 Windows packaged-upgrade advisory failure，以及 OpenClaw 未为原生 CLI 单独声明最低 Windows build；两者均不影响“干净环境、原生、无需 WSL”的结论，但阻止把它标为无条件生产升级候选。
