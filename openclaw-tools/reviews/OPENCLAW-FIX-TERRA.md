# Terra：OpenClaw 生产诊断器打包回归

完成状态：completed  
所有权：`tests/Package/M6.Package.Tests.ps1`  
未修改：`build/`、`policy/`、`tools/`、manifest 或文档。

实现内容：

- 调用真实 `Build-Package.ps1 -Mode Test`，并明确合成 EXE 不计安装验证。
- 双份独立解压 ZIP。
- 验证默认策略文件存在，使旧代码因漏打包策略而失败。
- 验证包内生产诊断脚本、脱敏模块和策略与源码 SHA-256 完全一致。
- 不传 `PolicyPath`，从解压目录执行包内诊断器并生成只读报告。
- 第二副本删除策略后，验证非零退出、错误含“无法读取只读诊断命令策略”，且不生成报告目录。

Terra 本地等价实跑、PowerShell 解析和 `git diff --check` 通过。Terra 环境当时只有 Pester 3.4.0，因此没有伪装成锁定 Pester 5.7.1 通过；Sol 随后下载到工作目录并正式运行锁定回归与质量门。

