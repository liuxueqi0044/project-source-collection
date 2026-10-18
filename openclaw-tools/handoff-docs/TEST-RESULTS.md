# OpenClaw 阶段 B：PersonalMessengerQQ 隔离克隆试验结果

执行时间：2026-09-15 22:34 至 2026-09-16 01:20（Asia/Shanghai）  
最终状态：**在环境前提门停止，未运行安装器**  
停止代码：`ENV-NESTED-VIRT-UNAVAILABLE`  
销售状态：`DO_NOT_SELL`  
Hermes：暂缓，未启动

## 结论

独立完整克隆、旧项目离线自启动核查和离线基线快照均已完成。测试副本没有出现 QQ 或旧 PersonalMessenger/PMAI 运行时自动启动，源 VDI 和 QQ 草稿没有被打开或改写。

OpenClaw 本地 Windows Hub 路径不能在这台 VirtualBox 副本中继续：VirtualBox 7.1.18 的宿主能力报告明确为 `Processor supports nested HW virtualization: no`，来宾也看不到虚拟化、SLAT 或 VM Monitor Mode。Microsoft 说明在 VM 内运行 WSL2 必须启用嵌套虚拟化；OpenClaw Windows Hub 的本地快速路径会创建 app-owned `OpenClawGateway` WSL distro。因此任务书的停止条件已触发，未用无效参数强行安装，也未修改/重启宿主机。

这次结果证明智能体可以在用户完成一次安全的 Windows 登录后，通过 VirtualBox 控制台、`VBoxManage` 与 Guest Control 完成克隆隔离、只读诊断、正常关机和快照；它**不证明普通客户远程安装、撤权或售后闭环可自动完成**。

## 源机、克隆与快照

| 对象 | 结果 |
|---|---|
| 源 VM | `PersonalMessengerQQ`，UUID `7592dfa4-c60c-4818-921e-ced5c626254b`，最终 `poweroff` |
| 源配置 | `C:/Users/xiayu/Documents/Codex/2026-09-09/new-chat/outputs/qq-vm/PersonalMessengerQQ/PersonalMessengerQQ.vbox` |
| 源 VDI | UUID `294cb397-93c3-476f-8a7e-36667d5fe244`；长度 `45,477,789,696` 字节；LastWriteTimeUtc `2026-09-15T10:37:57.7742073Z`，克隆后未变化 |
| 测试副本 | `XQ-OpenClaw-Test-01`，UUID `5d948da3-2078-45bf-92b4-151de5412753`，最终 `poweroff` |
| 克隆目录 | `C:/Users/xiayu/Documents/Codex/2026-09-15/agent-a-c-users-xiayu-documents/work/agent-kits-tech/vm/XQ-OpenClaw-Test-01/` |
| 克隆基盘 | `XQ-OpenClaw-Test-01.vdi`，UUID `7fb735e1-71e8-454c-9944-98f2c554e9d4`，`Parent UUID: base`，不引用源 VDI |
| 基线快照 | `XQ-OpenClaw-Baseline-Offline-01`，UUID `706f7c12-5d62-46b6-b5e2-7e317f726d7e` |
| 快照差分盘 | `Snapshots/{e069c4b0-f1a0-4c08-a8bd-393dac640343}.vdi`，父 UUID 为克隆基盘 UUID；创建时约 2 MiB |

完整克隆命令使用 `VBoxManage clonevm ... --mode=all --register`，耗时约 66.5 秒。源 VM 无快照，因此没有链接克隆或快照依赖。

### 源配置文件级偏差

VirtualBox 在执行官方克隆命令时重写了源 `.vbox` 文件。对重写前的 `.vbox-prev` 与当前文件做结构化比较，排除整个 `GuestProperties` 节点后 XML 完全相同；实际只移除了六个由 Guest Additions/VirtualBox 维护的瞬态只读属性（登录用户计数、Guest Additions GUI focus、宿主 VBox 版本/修订）。源 UUID、硬件、SATA/IDE、源 VDI、NAT/MAC、剪贴板、拖放和嵌套虚拟化设置均未改变。

因此源机的**语义配置和磁盘状态保持不变**，但“源配置文件一个字节都不变”没有满足。没有手工覆盖或恢复源 `.vbox`，以免在源机上继续写入。当前源 `.vbox` SHA-256 为 `01011D372BF2879958B17AA165EB0196A98456E3545A89261E720B97C903DE6B`，LastWriteTimeUtc 为 `2026-09-15T14:36:05.9701598Z`。

## 旧项目隔离结果

首次启动前只对克隆执行：断开唯一 NAT 网卡的虚拟网线、卸载旧 `PMAI_GUEST_LOCAL_R20260911_12.viso`、保持剪贴板/拖放禁用、保持无共享文件夹。源 VM 的 NAT 和 VISO 未动。

用户只在 VirtualBox 测试副本窗口内输入 Windows 密码；密码没有进入聊天、命令、日志或文件。登录后两次桌面观察均未见 QQ、OpenClaw 或 PMAI 窗口。限界的 Guest Control 只读探针得到：

- `QQ.exe`：0。
- `C:\PMAI\`/OpenClaw 相关进程：0。
- 相关服务：0。
- HKCU/HKLM Run 与 RunOnce 命中：0。
- Startup 文件命中：0。
- 发现一个 `PMAI-QQ-Metadata-*` 计划任务：`Ready`、启用、动作位于 `C:\PMAI\`，但 `trigger_count=0`，没有登录、开机、时间或事件触发器，只能手动调用。
- 离线状态：启用网卡数 0、IPv4 默认路由数 0。

判定：在已知 PersonalMessenger/PMAI/OpenClaw 标识范围内，旧项目不会自动运行。未打开 QQ、未读取聊天/草稿/浏览器或项目凭据，也未禁用安全软件。原始最小化 JSON 在 `work/agent-kits-tech/evidence/guest-preflight.json` 和 `guest-admin-preflight.json`。

## 环境检查

| 项目 | 实测 | 判定 |
|---|---|---|
| 来宾 OS | Windows 11 家庭版，10.0.26200，x64 | 支持 Windows Hub 外层应用 |
| 来宾资源 | 4 vCPU，4,811,812,864 字节 RAM，C 盘约 49.1 GB 可用 | 磁盘可用；内存偏紧但不是当前硬阻碍 |
| 来宾虚拟化可见性 | `VirtualizationFirmwareEnabled=false`、`SLAT=false`、`VMMonitorMode=false` | **FAIL** |
| OpenClawGateway distro | 未发现 | 尚未安装 |
| 宿主资源 | 24 逻辑处理器，约 32 GB RAM，检查时约 9 GB 可用 | 足以运行现有 VM |
| 宿主虚拟化 | `HypervisorPresent=True`；VirtualBox 报 HW virtualization=yes、nested paging=yes、nested HW virtualization=no | **FAIL：当前 VirtualBox 后端不能向来宾暴露嵌套虚拟化** |
| 来宾 WSL/VMP 功能状态 | 非提升 Guest Control 无法读取，记为 unknown | 不影响停止判断；即使已启用，也缺来宾 CPU 虚拟化能力 |

没有执行 `--nested-hw-virt=on`：Oracle 文档说明宿主不存在的硬件虚拟化能力不会暴露给来宾，而当前 `VBoxManage list hostinfo` 已明确报告 nested HW virtualization=no。仅把配置位改为 on 不能形成可用 WSL2 证据，继续启动/安装只会扩大副本差异。

依据：

- [OpenClaw Windows 文档](https://docs.openclaw.ai/platforms/windows)：Windows Hub 的本地快速设置会创建 app-owned `OpenClawGateway` WSL distro。
- [Microsoft WSL FAQ](https://learn.microsoft.com/en-us/windows/wsl/faq)：VM 内运行 WSL2 需要启用嵌套虚拟化。
- [Oracle VirtualBox 7.1 用户指南](https://docs.oracle.com/en/virtualization/virtualbox/7.1/user/EN-VBOX-7-1-USER.pdf)：嵌套虚拟化只能暴露宿主已有的硬件虚拟化功能。

## 安装候选与测试矩阵

本轮没有下载新文件。复用此前从[官方 v2026.7.1 release](https://github.com/openclaw/openclaw-windows-node/releases/tag/v2026.7.1)完整下载并核验的 `OpenClawCompanion-Setup-x64-v2026.7.1.exe`：

- 大小：`122,306,488` 字节。
- SHA-256：`0FAD205697DC92A020BE4C5809ECDF2F383258F5A54914922399787E1A4AFA1B`，与官方 GitHub digest 一致。
- Authenticode：`Valid`；签名主体 `OpenClaw Foundation`；证书指纹 `74F0719953150F0B55D447D608D6A764A057BB55`；Microsoft 时间戳。
- 生产诊断文件当前哈希：`OpenClaw-XQ-Diagnose.ps1` = `3D3156C517026022CB712C33811EBAEA79689AD4F11D61BAC99200CE8BB0585C`；`XQ.Redaction.psm1` = `221B8E6E63759A2F37597EB037CE02A379113D52CFD68BCFA1D0F6C3310BED91`；`allowed-package-files.json` = `FD9FE66210E6A2D8BD40CE0CD6B595845CFEB8408419F7957460E02C80F961D7`。

| 测试项 | 状态 | 说明 |
|---|---|---|
| 独立完整克隆 | PASS | 新 VM/磁盘 UUID，clone VDI 为独立 base |
| 源 VDI/QQ 数据保护 | PASS | 源 VDI 未写；QQ 未打开；草稿未读/未发/未清 |
| 源 `.vbox` 文件级不变 | DEVIATION | VirtualBox 克隆时仅清理六个瞬态 GuestProperties；语义配置不变 |
| 首次离线启动与旧项目自启动 | PASS（限界） | 已知标识无进程/服务/启动项；唯一任务无触发器 |
| 离线基线快照 | PASS | UUID `706f7c12-...`，属于测试副本 |
| OpenClaw 外层安装器真实性 | PASS（宿主侧） | 大小、SHA-256、签名已复核 |
| WSL2/嵌套虚拟化前提 | **FAIL** | 当前 VirtualBox 报 nested HW virtualization=no |
| 来宾内安装器复核与安装 | NOT RUN | 被环境停止门阻断 |
| 实际 Gateway/runtime 来源、版本与 hash | NOT TESTED | 未安装 |
| 真实模型对话 | NOT TESTED | 未安装；也未请求模型凭据 |
| 应用关闭/重开、系统重启恢复 | NOT TESTED | 未安装 |
| 生产诊断器 VM 回归 | NOT TESTED | 本地 63/63 与 89.45% 仍是开发证据，不代替 VM 证据 |
| 典型故障与恢复 | NOT TESTED | 未安装 |
| 卸载/快照恢复复跑 | NOT TESTED | 未产生安装状态；基线快照已保留 |
| Quick Assist/RDP/SSH/WinRM 远程链路 | NOT TESTED | 本轮仅本机 VirtualBox 控制台与 Guest Control |
| 客户中止、会话断开与撤权 | NOT TESTED | 没有建立远程客户会话 |

严禁安装的 `XQ-OpenClaw-Windows-x64-v0.1.0-TEST-NON-RELEASE.zip` 未进入 VM、未运行。仓库差异未提交、未推送、未发布。

## 自动履约与人工介入

- 已证明：在预先存在、已授权的 VirtualBox 测试环境里，用户完成一次 Windows 登录后，智能体可以独立完成绝大多数克隆隔离、后台检查、正常关机和快照工作。
- 用户本轮必需动作：在测试副本的 VirtualBox 窗口输入一次 Windows 密码并停在桌面。没有要求用户把密码贴入聊天。
- 创始人/用户额外介入：没有 UAC、OpenClaw 登录、模型认证或故障处理介入。
- 不能外推：内部 VM 的 DPAPI Guest Control 凭据不适用于客户机器；尚未证明普通客户能通过 Quick Assist 等方式一次接入、由智能体完成安装、并在结束后可靠撤权。
- 若进入下一台合格环境，预计仍至少需要客户在安全界面完成 Windows 登录、必要的 UAC 确认，以及模型服务登录/API Key/OAuth；密钥不得进入聊天。

## 尚缺条件与最低成本下一步

1. 提供一台能向 Windows 来宾暴露嵌套虚拟化的测试环境，首选支持 nested virtualization 的 Hyper-V/云 VM，或直接使用可安全回退的物理 Windows 测试机。不要为这次测试关闭宿主 Hyper-V/VMP/WSL 或重启当前宿主。
2. 在新环境确认 Windows 来宾内 `VirtualizationFirmwareEnabled/SLAT/VMMonitorMode` 可用后，恢复阶段 B 的官方直装、实际 Gateway/runtime 锁、重启、诊断、故障、卸载与恢复测试。
3. 真实对话时再由用户在 OpenClaw 官方界面提供一个明确授权的模型服务/免费额度；本轮未读取旧 QQ 项目的任何模型密钥。
4. 另行测试一种真实客户 transport（优先客户现有方式，其次 Quick Assist），包括客户接入、允许控制、中止、断开和撤权。VirtualBox 本地控制不能代签远程履约。
5. 当前 OpenClaw 文档还提供 native PowerShell/远程 Gateway 路径，但改走它会改变已审查的 Windows Hub 本地 WSL 试验目标；如选择该路线，应先重新锁定 runtime/版本和安全边界，不在本轮临时改道。

当前保留状态：源 VM 关机；测试副本关机；测试副本当前指向离线基线快照后的空差分盘；网线仍断开；旧 VISO 仍只从克隆卸载。没有删除任何 VM、快照或证据。

## 时间与费用

- 阶段 B 墙钟约 2 小时 45 分，包含用户安全登录、探针编码修正、一次宿主关机/恢复中断和 VM 等待。
- AI 活跃操作估算约 50 分钟，未超过 90 分钟上限；完整克隆等待约 66.5 秒；Guest Control 查询累计约 4 分钟。
- 新增下载：0；新增采购：0 元；模型/API 调用：0 元。
