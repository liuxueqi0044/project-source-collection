# QQ VM 部署交接记录

状态记录：2026-09-10。本文只记录部署边界和下一步，不包含密码、API key、DPAPI 内容或任何 secret 值。

## 当前已确认

- Windows guest 已安装；本轮 5 个公开部署文件已完成只读验证。
- 通过官方 VBox Keyboard COM `PutScancode` 已正常登录 `pmai_setup`，键盘路径已恢复。
- 用户指定的新管理员密码已通过 GUI 隐藏字段修改成功，界面确认“你的密码已更改”；本文不记录密码值，也无需用户再次手动操作。
- root 以 pending 新 credential 执行 `CreateSession`，`wait1/status100` 已验证成功；`ReplaceFileW` 原子 promote 已将新 DPAPI 状态提升为 current，旧凭据标记为 `obsolete-20260909T170533Z`。旧凭据值和新凭据值均不记录。
- `controlvm shutdown` 在不使用 force 的情况下约 16 秒正常 poweroff 成功；未使用强制关机。
- 关机后已建立 `IDE0:0` 光驱映射。原 `PMAI_DEPLOY.viso` 因 UDF 不支持导致启动 aborted；仅移除并以 `iso+joliet` 修复后恢复正常启动，未换槽。
- 上述旧 `PMAI_DEPLOY.viso` SHA-256 为 `2DDFDC0642A4A0D6F9EA2722D4BA22CBC42535994F4790F54C0559CA4A5DED10`；历史 `PMAI_RUNTIME.viso` SHA-256 为 `9C002C6B22E0CDC1B318BEBA68F9372A8EC3D50720FBF21E4F20FDB524514C93`。当前介质为下文的 `PMAI_RUNTIME_KEY.viso`。
- 5GB/4 CPU 是前一阶段历史配置；该配置在 Windows 更新后因 free `11.94/11.89GiB < 12GiB` 被宿主保护启动检查阻止。现已降至 4608MB，仍保留 6GiB 宿主保留 + 1GiB 虚拟化余量，并已实际保护启动成功。
- Guest Additions 7.1.18 与 CreateSession 已有实证，当前 COM 进程状态仍未认证；public VISO 介质状态以实际确认记录为准。
- 原因已定位为 guest `app2` 文件存在空 DACL；host 同构复现并由 root 读取确认。对单文件写入无继承 flags 的显式 ACE（当前用户 M、SYSTEM F、Administrators F）后，host `ReadAllText` 恢复，证明需要逐文件修复，不能只修目录继承。
- 第一次 guest repair app 阶段已执行到成功阶段，但 Python 因 Windows PowerShell 5.1 native stderr 与 `$ErrorActionPreference=Stop` 组合提前 throw；修正版脚本 SHA-256 为 `FF2F8216C610C9B902CAF521CE8D3A68255164C4F3CC020A2BF8306F7AB58998`，已在 host 以 stderr exit 5 验证。独立修复介质为 `PMAI_REPAIR_V2`，SHA-256 为 `D07F2162B6F13776F847C4A289FAF6F767DEF526F2858A7A9646CAF335D7EE8E`，GUID 为 `e4dfc6cf-01d7-482d-86d8-1faf89e63b66`。
- 后续部署脚本 `guest_deploy_prerequisites_v2.ps1` SHA-256 为 `57082FDCD1AE5B6337B2BBD2CA9319A7F4E7226BF07DAC54EC8A7053CA622AD5`；root 已审查，host Windows PowerShell 5.1 函数测试可读文件。上述均不等于 guest Python repair 或 runtime 依赖已完成。
- repair-process 最终证据显示本轮 Python 文件 3727 个 repaired、2678 个 owner changed、`bootstrap_read=true`、`pyproject_hash=true`、Python 3.13.15、exit 0；权限修复仅限专用安装文件范围，目录未递归改动。futurev2 host review 与测试已通过。
- `PMAI_RUNTIME_KEY.viso` 已实际挂载 3 个公开文件，独立 GUID 为 `5f85a65e-a23f-442b-933b-4bef9144ad01`；实测 SHA-256 为 `085CCCA6AB194066AA3027F11E84D046C82A9173E5CAF59F28B22C26F5787024`。旧 host intake 在用户粘贴并确认后未产生预期状态，不能描述为用户未提交；该流程已弃用。随后经专用子进程 stdin 向 guest 隐藏 getpass 输入，返回 exit 0 / typed=true。qqbot 的 DPAPI get_secret 检查仅输出 configured=True；未记录秘密值。
- 实际读取 `C:\PMAI\data\runtime-setup-status.json` 得到 `complete`，包含 QQ 签名检查。旧 console 的 qq-metadata 行是未更新的画面，不能据此认定仍等待。
- 宿主对官方 `https://api.deepseek.com/models` 的认证探测返回 HTTP 200，configuredModelAvailable=true（deepseek-v4-flash）。这只证明密钥及模型查询可用，没有调用聊天生成或发送 QQ。
- QQ 已完成手机扫码登录，root 已查看登录后的截图；可见列表仅作为测试候选，尚未完成稳定身份绑定或 UIA/contact/selector/capability 验证。旧 intake PID 16216 经进程名、创建时间和命令来源核对后已停止，不再等待或重复输入 key。

- selected structural probe 已实际返回 438 个节点，但 `ComposerGroupTextPattern` 无可用 Value/sendInvoke 能力；登录 modal heuristic 为 false/unknown，不能视为 UIA 发送或登录验收。此前 v3 合成生成 HTTP 400 已由 DeepSeek-only schema 修复解决；更新 wheel 已安装且 provider hash match，v4 guest 合成生成实测通过（schema_valid=true，usage 895/286/1181）。整体自动回复尚未开启。

## 尚未完成

guest 内 Python 文件级 repair、runtime setup 和一次合成 DeepSeek 生成已完成；真实 alias 的 DPAPI 读取与宿主 API 认证已通过。QQ 登录/UIA 交互、selector/binding/capability 验证、身份类型绑定生产接线、guest composer 路径、RulePack 激活、运行时 WebUI 启动、多联系人验收及真实 QQ 消息发送仍未完成。不能把合成生成当作端到端 QQ 自动回复成功证据。

官方 COM 进程旧版五参数调用与本机六参数签名不兼容；修复后 stdin 和无 stdinProbe 路径仍分别出现 status100/timeout，因此不能声称 GuestProcess 已可正常执行。CreateSession 认证路径已有实证成功，不能把进程兼容性问题倒推为认证失败。runtime setup 的 complete 与 key 检查已有独立截图证据，后续不应重复安装或录入密钥。

## 建议的下一步顺序

1. 继续保留 4608MB 启动与 guest repair 的 status/截图证据；不要把 owner changed 数量扩展解释为目录或全盘修复。
2. 对已登录 QQ 获取实际 UIA/contact、selector/capability 证据；可见列表只作为候选，不能替代身份绑定。
3. 完成规则配置、guest 运行时 DeepSeek 聊天调用和多联系人真实发送验收；当前均不得标记完成。

所有后续状态更新都应区分 Windows 已装、公开介质已核验、guest 实际部署、QQ/UIA 认证、DeepSeek 调用和真实发送；前一阶段证据不能替代后一阶段验收。
