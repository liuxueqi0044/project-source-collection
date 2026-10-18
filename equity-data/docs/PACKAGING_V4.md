# V4 交付包

停止采集并等待当前真实验证结束后，从项目目录执行：

```powershell
.venv\Scripts\python.exe scripts/package_v4.py --output ../equity-data-system-v4.zip
```

输出 ZIP 必须位于项目目录外，既有 ZIP 不覆盖。脚本保留 `package_v3.py` 原样，并复用其哈希、SQLite 检查和只读连接工具；没有导入时打包或生产模块迁移。当前 V4 清单为 `DELIVERY_MANIFEST_V4.json`，V3 和更早清单原样保留为历史证据；它们不是当前文件版本的校验清单。

## 包含和排除

代码、公开配置、文档、原始数据、SQLite、V1/V2/V3 历史数据、全部失败证据，以及 V4 live/sec_repeat/unseen 等验证数据均保留。仅排除：

- 虚拟环境、Git、依赖缓存、Python 字节码。
- 私人 local 配置、环境文件（`.env.example` 除外）、秘密/凭据文件、私钥和常见认证配置。
- 锁、SQLite WAL/SHM/journal 暂态旁文件；已提交 WAL 内容进入 SQLite 快照。
- 符号链接和 Windows junction；不沿链接读取外部路径。
- `backups/` 和本次 `data/v4_restore_validation/` 重复副本。这两处的原始 live 数据已在交付中；其本机备份保持不变，避免脱敏 SQLite 使 BACKUP_MANIFEST 中的哈希失效。

扫描时已存在的排除项目逐项列入清单；随后可能出现的 SQLite WAL/SHM 辅助文件遵循下述暂态规则。原件不删除。非 raw 目录下的嵌套 ZIP 等压缩包因无法直接审核其中私配而拒绝发布，需单独处理后再打包。

## 一致性和验证

每个存储目录依次取得已有 `.local-collector.lock` 和 `.machine-worker.lock`，持有至发布结束；不创建或改写锁文件。任何活动采集者阻止打包。逐库检查 SQLite、拒绝仍为 running 的 machine_tasks；仅有无活动任务的陈旧 machine_runs.running 不会阻止交付，也不会被改成成功或删除。

SQLite 使用 backup API 生成临时快照。每个 raw_blobs 引用必须指向包内原文，其 SHA-256、raw ID 和长度必须与目录完全一致；缺失、越界或内容变化会失败。打包后逐个 ZIP 成员重新计算 SHA-256，并验证成员清单与交付清单一致。

独立的 Control.enqueue 不持采集锁，所以另保留源库连接并检查 `PRAGMA data_version`，同时核对源文件清单和内容。若打包期间新请求入队、数据库改变、生成新文件或原文件改动，ZIP 不发布，`.partial-*` 保留作诊断。本地源请求和证据不回滚。最终发布前短暂持有各库的无写入 `BEGIN IMMEDIATE` 事务，并再次检查版本；这样入队不能在最终检查与 ZIP 发布之间提交。事务结束只 rollback，不修改来源 SQL 数据或执行迁移。

唯一允许的排除项存在变化，是本轮已打开、且 `PRAGMA journal_mode` 为 WAL 的源数据库所对应的精确 `-wal` 和 `-shm` 路径。即使只读连接也可能创建这些辅助文件；它们的出现或消失不表示业务数据改变。已提交内容仍由 SQLite 快照保存，真实提交仍由 `data_version` 检查。其他排除项、新锁、私人配置、无对应已打开 WAL 数据库的同名旁文件，以及 `-journal` 的存在变化仍导致失败；源码和原文的清单、哈希检查没有放宽。

首次真实 V4 打包在 ZIP 验证后因上述误报未发布，失败包原样保留。确切差异为 `data/live/local-control.sqlite3-wal` 和 `data/live/local-control.sqlite3-shm`；重现、失败日志与修复边界见 `reports/v4/package-transient-evidence.json` 和 `package-transient-before.txt`。这次修复不修改生产采集代码或本机来源数据库。

脚本无法禁止外部编辑器改写任意项目文件，故同时使用前后哈希和目录核对。调用者仍须等所有采集、报告生成和手工编辑结束。

## 私人配置脱敏

旧 V3 包排除了 local/env 私配文件，但派生的 machine-status JSON、machine_runs CSV 和 SQLite machine_runs.config_json 仍可能含真实 SEC 联系邮箱。联系邮箱并非 API 密钥，交付副本仍应去掉这项私人配置。

V4 从运行配置和被排除的私人配置中学习明确的凭据/联系值。仅在临时交付副本中：

- 将操作配置中的 user_agent、contact_email、密钥、密码、授权头等值替换为标记；保留 `*_env` 环境变量名。
- 同步处理派生 JSON、CSV、日志等文本中的已识别私人值。
- SQLite 只处理 scheduler/config/diagnostic 表的文本；records、raw_blobs、raw_retrievals 和原始文件保持原始内容。脱敏后 VACUUM，避免 SQLite 空闲页残留原值，再运行完整性检查。
- 可执行源码中若发现已识别私人值，停止发布而不悄悄改写冻结源码。

清单记录每个改变的交付路径、SQLite 表/列/行数以及前后哈希。来源文件和来源数据库不变；失败状态、金额、原始证据及时间记录保留。这是针对明确操作配置和已识别私人值的处理，不是对任意伪装凭据的通用检测器。原始公共披露里的联系方式属于原文，不进行全文替换。

包中的私人身份标记不能用作 SEC 请求身份；运行时设置真实 `SEC_USER_AGENT` 或本机私人配置即可。

## 测试范围

`tests/test_package_v4.py` 在临时小项目上创建真实 SQLite 和 ZIP，验证原文哈希、提交的 WAL、私配排除、快照脱敏且原库字节不变、陈旧 run/失败证据保留、双锁拒绝、损坏原文拒绝、独立入队和新增文件使发布失败、既有 ZIP/V3 清单不覆盖。补充真实只读 WAL 连接生成辅助文件的回归，以及 WAL 下新请求、源码/原文内容变化、其他排除项变化仍阻止发布的测试。测试不打包真实工作目录。
