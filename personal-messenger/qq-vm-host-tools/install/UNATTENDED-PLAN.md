# Windows 11 家庭版无人值守安装记录（主要验收完成）

此记录只适用于 `PersonalMessengerQQ`。用户已授权 Windows 许可、家庭版、无产品密钥安装；Windows 安装、Guest Additions 和主要来宾控制验收均已完成，VM 当前关机。

## 固定参数

- 安装介质：`../media/Win11_25H2_Chinese_Simplified_x64_v2.iso`
- 介质 SHA-256：`7408581E67BC455EBAAFB9230E531ABF45B1C8864A22114A1B03893F897102E4`
- 映像索引：`1`（Windows 11 家庭版）
- 语言：`zh-CN`；区域：`zh_CN`；国家：`CN`；时区：`Asia/Shanghai`
- 产品密钥：不传入；安装程序将使用“我没有产品密钥”路径。
- 启动模式：已先经 `--dry-run --start-vm=none` 审查，随后使用启动前内存保护启动专用 VM。
- Guest Additions/TXS：已使用匹配的 `VBoxGuestAdditions.iso` 与 `--no-install-txs`；不会启用共享文件夹、剪贴板或拖放。重启后已实测 `VBoxService`、GuestControl 和版本 `7.1.18 r173720`；两张安装辅助光盘均已移除。WinRE 路径/offset 指向 Recovery 分区 4，但未直接验证 Enabled 状态。

## 独立模板差异

基于 VirtualBox 自带 `win_nt6_unattended.xml` 的副本 `win11_home_zh_cn_no_bypass.xml`：

- 保留已获授权的 `<AcceptEula>true</AcceptEula>`。
- 删除 windowsPE 阶段的完整 `RunAsynchronous` LabConfig 块。
- 删除 specialize 阶段的完整 `RunAsynchronous` LabConfig 块。
- 因此模板不包含 `BypassCPUCheck`、`BypassRAMCheck`、`BypassSecureBootCheck`、`BypassStorageCheck` 或 `BypassTPMCheck`。
- 安装阶段仅使用临时本地管理员 `pmai_setup`，`LogonCount=1`；日后 QQ 普通账户未在本次安装中创建。Guest Additions 后安装脚本清理 AutoAdminLogon、DefaultPassword、DefaultUserName 与 AutoLogonCount；重启后已核对这些值以及 ForceAutoLogon 均不存在。

## 凭据处理（安装后已收敛）

将生成随机的专用来宾凭据，只写入 `aux` 目录中的任务限定密码文件；目录与文件 ACL 仅允许当前 Windows 用户和 `SYSTEM`。VBoxManage 只通过 `--user-password-file` 和 `--admin-password-file` 读取密码，绝不在命令行、日志或此文档中写入明文。

安装后 `install\aux` 已删除。凭据状态只保留 DPAPI 保护的 `administrator.dpapi` 与 `pmai_setup.dpapi`，ACL 仅当前用户 `L\xiayu` 和 `SYSTEM`、不继承；DPAPI 恢复的临时受限密码文件已用于 GuestControl 验收并删除。来宾 `C:\Windows\Panther\unattend.xml` 仍存在，非提升删除被拒绝，未检查其敏感字段。
