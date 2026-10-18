# V4 本机备份与恢复

命令实现：`equity_data/backup.py`。从项目根执行，使用项目虚拟环境的 Python。命令只读来源数据库；不初始化 Store、不迁移来源库、不发起网络获取、不修改 Windows 计划任务或配置。

```powershell
python -m equity_data.backup backup --storage data/live --destination backups/live-20261010-001
python -m equity_data.backup verify --backup backups/live-20261010-001
python -m equity_data.backup restore --backup backups/live-20261010-001 --destination data/restored-20261010-001
```

备份目的目录必须不存在，并且位于来源 storage 之外。恢复目的目录只能是新目录或空目录，不能位于备份目录内。重复命令不会覆盖已有备份或生产数据。失败后保留目的目录、已产生的文件和错误状态；重试须换一个新的目的目录。

## 快照边界与锁

命令按采集器相同顺序取得 `.local-collector.lock`、`.machine-worker.lock` 两个操作系统锁。任何一个正被占用时，命令返回 `busy`，退出码为 **3**，不会继续拼接主库和控制库的混合采集快照。正常完成退出 **0**，其他错误退出 **1**。不通过删除锁文件强行解锁；采集结束后用新目录重试。

成功取得锁后，使用 SQLite online backup API 保存 `data.sqlite3`，以及实际存在的 `local-control.sqlite3`、`control.sqlite3`。每个数据库都执行来源和副本的 `PRAGMA quick_check`。来源仍可被其他进程只读访问，已提交的 WAL 内容由 SQLite backup 纳入；不直接复制处于活动状态的主库文件或 WAL。

**每个 SQLite 快照是原子的，但多个数据库不是一个跨库事务。** 采集和解析工作在共享锁期间不能运行；控制层允许请求入队独立于采集锁，因此控制库快照可能包含主库快照之后才排队的请求。恢复后先检查这些历史队列，重复请求依赖现有任务去重和幂等性安全重放。不要让不遵循上述锁的外部程序写入这些数据库，也不要把本工具解释为任意多写者系统的事务备份。

## 备份内容与核验

- 主库和存在的两个控制库，保留数据库行、时间、修订关系及不可变触发器。
- 主库 `raw_blobs` 引用的全部原文，逐文件核验数据库登记的 SHA-256 和字节数。
- `BACKUP_MANIFEST.json`：每个文件的路径、哈希、大小、原文引用、快照边界及备份时间。
- `BACKUP_MANIFEST.sha256`：清单字节的 SHA-256；清单不自我哈希。
- `BACKUP_STATUS.json`：`creating`、`complete`、`busy` 或 `failed`，失败文件不自动删除。

当前 Store 的原文目录为 `raw/<哈希前两位>/<哈希>`。备份允许安全的 `raw/` 相对路径，不修改库中路径；绝对路径、越界路径、符号链接会拒绝，须先单独修复旧库的存储问题。缺少原文或原文哈希不符时整次备份不能完成，不能用缺失替代品伪装完整快照。

核验同时检查清单哈希、完成标记、所有入包文件的哈希/大小、SQLite 完整性，以及清单原文引用与主库 `raw_blobs` 的一一对应。副本以 immutable 只读模式检查，避免读取额外 WAL 文件；恢复只复制清单列出的文件并再次检查其哈希。哈希用于检出损坏，**不是数字签名或来源真实性认证**，备份目录仍应按原文授权条件妥善保管。

排除未引用散文件、缓存、配置、密钥、锁文件、WAL/SHM 旁文件和 Windows 计划任务。SQLite backup 已纳入已提交 WAL 事务。不存在的控制库不会凭空建立。

## 恢复后的时效

恢复原样保留公开时间、获取时间、处理时间、旧水位和历史运行记录，不产生新的获取记录，不把恢复时间写成获取时间。

恢复目的目录中的 `RESTORE_STATUS.json` 为 `restored_requires_live_check`，并写明：

```json
{
  "requires_live_check": true,
  "historical_watermarks_are_current_freshness": false,
  "original_timestamps_preserved": true,
  "new_fetches_created": 0,
  "automatic_schedule_changed": false
}
```

恢复完成仅证明快照文件和引用自洽。需要显式检查恢复的请求/任务队列，再用恢复目录执行真实来源检查和增量更新；成功网络检查的独立证据才可说明当前时效。备份命令不会清除该标记，也不会自动启动恢复目录的计划任务。恢复失败则写 `restore_failed`，保留不完整目录供排查，不能投入采集。

## 验证范围

`tests/test_backup_v4.py` 使用临时合成 Store 和控制库：覆盖活动 WAL 的已提交数据、全表原值和修订时间、原文、不可变触发器、两个操作系统锁的忙状态、释放后续跑、现有目录保护、空库、缺文件、文件/清单/完成标记损坏、独立 SQLite 完整性检查、清单越界、原文引用错配和排除额外旁文件。

测试结果见 `reports/v4/backup-tests.txt`。这些是软件恢复测试，不代替真实运行目录的备份恢复演练，也不证明恢复后的远端来源仍然新鲜。
