# OpenClaw 阶段 B：PersonalMessengerQQ 隔离克隆试验计划

日期：2026-09-15（Asia/Shanghai）  
状态：Hub/WSL 路线已在阶段 4 停止；`NATIVE-PILOT` 已完成一次试验并回退（2026-09-16）  
销售状态：`DO_NOT_SELL`

## 目标与边界

在 Oracle VirtualBox 源 VM `PersonalMessengerQQ` 的独立完整克隆中验证 OpenClaw 官方直装与修复后生产诊断链路。源 VM、源 VDI、QQ 草稿、QQ 聊天、浏览器凭据和原自动回复任务均不读取、不发送、不清除、不修改。

本轮不运行 `TEST-NON-RELEASE` 中的 15-byte 合成 EXE，不提交/推送/发布 OpenClaw 仓库，不启动 Hermes，不修改宿主机 Hyper-V/WSL/Docker/虚拟化/安全设置，不新购服务。

## 已确认基线

- VirtualBox：`7.1.18r173720`。
- 源 VM：`PersonalMessengerQQ`；UUID `7592dfa4-c60c-4818-921e-ced5c626254b`；状态 `poweroff`；无快照。
- 源配置：`C:/Users/xiayu/Documents/Codex/2026-09-09/new-chat/outputs/qq-vm/PersonalMessengerQQ/PersonalMessengerQQ.vbox`。
- 源磁盘：`PersonalMessengerQQ.vdi`；UUID `294cb397-93c3-476f-8a7e-36667d5fe244`；normal/base、动态 VDI、标称 80 GiB、实际约 42.35 GiB。
- 资源：4 vCPU、4608 MiB RAM、EFI、Windows 11 64-bit、NAT；`nested-hw-virt=off`、`hwvirtex=on`。
- 隔离：clipboard/drag-and-drop disabled；源 VM 无运行实例。
- 源 VM 还挂载旧项目 `PMAI_GUEST_LOCAL_R20260911_12.viso`；克隆首次启动前在克隆上卸载，源 VM 不动。
- 宿主 C 盘可用约 332.07 GiB；目标名称和目录当前均不存在。

## 目标对象

- 克隆名：`XQ-OpenClaw-Test-01`。
- 克隆根：`C:/Users/xiayu/Documents/Codex/2026-09-15/agent-a-c-users-xiayu-documents/work/agent-kits-tech/vm/`。
- 预期配置目录：`.../vm/XQ-OpenClaw-Test-01/`。
- 基线快照名：`XQ-OpenClaw-Baseline-Offline-01`。

## 阶段与验收

### 1. 完整克隆

1. 再次确认源关机、目标名/目录无冲突和磁盘空间。
2. 用官方 `VBoxManage clonevm` 执行 `--mode=all --register` 完整克隆，不使用手工 VDI 复制或 linked clone。
3. 核对克隆 VM UUID、磁盘 UUID、配置/磁盘路径与源不同；克隆磁盘 `Parent UUID: base`，且不引用源 VDI。
4. 对比源配置/VDI size、LastWriteTime、UUID 与 VM 状态，确认源保持不变。

停止条件：源意外启动、目标已存在/归属不明、克隆引用源磁盘、空间不足或 clone 命令失败。

### 2. 首次启动前隔离

1. 仅对克隆断开网线（保留 NAT 配置以便稍后恢复）。
2. 仅对克隆卸载旧项目 VISO；保持剪贴板/拖放禁用，不增加共享文件夹。
3. 首次启动后在离线状态观察是否出现 QQ、PersonalMessenger 或旧自动回复窗口/进程。
4. 只检查与旧项目明确相关的 Startup、计划任务、服务、Run 项和进程；不打开 QQ 或聊天，不读取草稿/浏览器/配置中的凭据，不禁用安全软件。
5. 若发现相关自启动，只在克隆中记录并禁用；确认停止后关机。

停止条件：需要读取私人内容才能判断、出现无法安全终止的外部动作、无法登录或无法可靠控制桌面。

### 3. 离线基线快照

1. 在克隆关机、旧项目自启动已确认停止、网络仍断开时创建 `XQ-OpenClaw-Baseline-Offline-01`。
2. 核对快照 UUID、时间和所属 VM；不在源 VM 创建/恢复/删除快照。

### 4. 环境前提

1. 只读获取 guest Windows edition/build/language/architecture、CPU/RAM、C 盘空闲、WebView2、Virtual Machine Platform、WSL 状态和版本。
2. 只读核对宿主 VirtualBox/Hyper-V 后端及 CPU 嵌套虚拟化能力。
3. 如官方路径需要，提出只作用于克隆的最小 CPU/RAM/nested-hw-virt 调整；不修改宿主设置。
4. 只有旧项目安全隔离后才重新接通克隆 NAT。

停止条件：必须大改或重启宿主、宿主/VirtualBox 不支持所需嵌套虚拟化、guest 资源明显不足且无法在克隆内低成本修正。

### 5. 官方直装与诊断链路

1. 采用已核验的官方 OpenClaw 安装器，不使用合成 ZIP；在 guest 内重新核对 size、SHA-256、Authenticode 和时间戳。
2. 记录安装器版本、下载来源，以及安装器动态取得的实际 Gateway/runtime 来源、版本与可得 hash。
3. 完成安装、应用关闭重开、系统重启恢复、生产诊断、一个安全可恢复故障及恢复、卸载/回退。
4. 真实模型对话等用户指定服务后执行。用户只在 VM/OpenClaw 的官方登录或模型认证界面输入密码、API Key、OAuth 或验证码；不通过聊天、命令、日志或截图传递。
5. 分开记录 VirtualBox 控制台/Guest Control/其他通道，不能把 VM 控制台成功描述为普通客户远程交付通过。

### 6. 重复性与收尾

1. 第一次链路成功后，经用户允许恢复专用克隆的离线基线快照复跑一次。
2. 记录客户参与、创始人介入、AI 活跃时间、复制/下载/重启等待和增量费用。
3. 明确保留或恢复的克隆状态；不删除源 VM、克隆、快照或未交付证据。

## 时间预算

- 准备和环境路径判断：最多 45 分钟 AI 活跃操作；克隆复制等待单列。
- 首次 VM 试验：最多 3 小时墙钟、90 分钟 AI 活跃操作。
- 超时或遇根本障碍时停止并交接，不自动启动 Hermes 或更换大型虚拟化平台。

## 执行落点

- 阶段 1：完成，独立完整克隆已注册。
- 阶段 2：完成，离线登录后已知旧项目标识未自启动；唯一 PMAI 任务没有触发器。
- 阶段 3：完成，快照 `XQ-OpenClaw-Baseline-Offline-01`，UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e`。
- 阶段 4：**FAIL/STOP**。VirtualBox 宿主报告 `nested HW virtualization: no`，来宾看不到 VT/SLAT/VM Monitor Mode；未修改宿主配置。
- 阶段 5–6：未执行。没有运行安装器、没有模型认证、没有恢复/复跑。详见 `TEST-RESULTS.md`。

## NATIVE-PILOT：Windows 原生 CLI + Gateway 独立路线

本节不改写前述 Hub/WSL 失败结论，也不把原生路线结果计入旧 Hub 安装包。唯一目标仍为 `XQ-OpenClaw-Test-01`；源 VM、宿主虚拟化设置和 Hermes 均不在操作范围。

### 前置门

1. 从 OpenClaw 官方文档、官方 GitHub release/tag、固定 tag 下的 `scripts/install.ps1` 和 npm registry 锁定一个非 beta 的精确 CLI/Gateway 版本。
2. 下载但不直接执行 `https://openclaw.ai/install.ps1`；将其与固定 tag 的脚本逐字节比较并记录 SHA-256。
3. 下载固定版本 npm tarball，校验 registry `dist.integrity`/`shasum`，记录 tarball SHA-256/SHA-512、包名、版本和 Node engines。
4. 静态审查 PATH、全局 npm、Node/Git bootstrap、onboarding、upgrade/doctor、配置目录、计划任务/Startup 回退、监听地址和认证副作用。
5. 只在确认该版本确实支持 native Windows、无需 WSL、可固定版本且不需要大规模源码改造后进入 VM。

停止条件：官方脚本与固定 tag 不一致、npm 身份/完整性无法闭环、候选需要 WSL/宿主变更、只能滚动 latest、或安装会不可控地复用旧配置/密钥。

### 测试副本安装顺序

1. 核对 clone UUID、关机状态、当前快照 UUID、空差分盘、旧 VISO 已卸载；不重复克隆或扫描私人内容。
2. 启动时先保持网线断开，通过限界 Guest Control 检查 Node/npm/Git/PowerShell、PATH、与本次相关的 OpenClaw 配置/命令/任务和端口占用；不读取配置值或密钥。
3. 若基线仍安全，接回 clone NAT。安装文件先在宿主固定、校验，再通过受控方式传入；不运行远端文本管道。
4. 使用独立 native pilot 配置/profile 路径（仅在该精确版本证实支持时），固定 npm 版本，跳过 onboarding；记录用户、权限、文件、PATH、全局包和网络副作用。
5. 验证 `openclaw --version`、`doctor`、本地 Gateway 启动与 `gateway status --json`；Gateway 仅监听回环且保留认证。
6. 有模型条件时，由用户在 OpenClaw 官方安全界面或来宾终端内自行输入凭据；聊天和日志不接收密钥。完成一次普通文本对话并记录入口（CLI 或本地 WebChat）。
7. 分开验证应用/进程重开与 Windows 重启后行为；验证一个最小可恢复故障、原生诊断、停止/卸载和基线回退。
8. 远程客户 transport 仅在安装主链路通过且时间足够时测试；VirtualBox/Guest Control 不代签远程履约。

### 预期证据

- 新增 `NATIVE-PILOT-RESULTS.md`，状态逐项为 PASS/FAIL/NOT TESTED/NOT APPLICABLE。
- 保存固定脚本、npm 元数据、tarball及哈希到 `work/agent-kits-tech/native-pilot/`，不放凭据。
- 更新 `HANDOFF.md`、`PROJECT_STATE.md` 和 `DECISIONS.md`；保留原 `TEST-RESULTS.md` 不改写。

### NATIVE-PILOT 实际收尾

- 版本/来源、私有安装、loopback Gateway、DeepSeek 真实对话、应用重开、Gateway 重启、Windows 重启后登录恢复、故障恢复、官方卸载和快照恢复已执行。
- 官方诊断 ZIP 因包含合成对话标记判为证据卫生失败，不得对外分享。
- XQ 生产诊断器包闭包、缺策略 fail-closed 和脱敏通过；named-profile/端口语义判为 `PARTIAL/FAIL`。
- 从基线第二次完整复跑、恢复后 guest 内残留探针、真实客户远程连接/中止/撤权均为 `NOT TESTED`。
- 最终结果见 `NATIVE-PILOT-RESULTS.md`；整体继续 `DO_NOT_SELL`。
