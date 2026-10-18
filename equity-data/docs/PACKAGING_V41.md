# V4.1 交付打包

新增 `scripts/package_v41.py` 复用已经验证的 V4 打包引擎。不修改 `package_v4.py`，不自动打包；导入脚本不会产生 ZIP 或清单。

默认输出为项目目录之外的 `../equity-data-system-v4-1.zip`，本轮权威清单为 `DELIVERY_MANIFEST_V41.json`。V3、V4 清单作为历史证据收入包内，源文件保持不变。已有任意目标 ZIP 一律拒绝覆盖，包括既有 V3、V4 和 V4.1 包。

## 操作

先等待当前采集结束，暂时停用**既有**本项目 Windows 任务并停止产生新请求。记录原任务启用状态，打包成功或失败都应在 finally 路径恢复原状态。只在打包期间暂停，不重复注册任务。

```powershell
.venv\Scripts\python.exe scripts/package_v41.py
```

需要另存时使用尚不存在的目标文件：

```powershell
.venv\Scripts\python.exe scripts/package_v41.py --output ../equity-data-system-v4-1-review.zip
```

打包期间不要写项目内日志、报告或配置。可把 stdout 保存到项目外目录，避免库存变化导致验证失败。失败时会保留本次 `.partial-*` 证据，不覆盖历史包。

## 验证和隐私边界

沿用 V4 的两层现有执行锁、SQLite backup 与 quick_check、原文目录 hash/size 验证、全部 ZIP 内容 hash 核验、并发数据库版本检查和最终发布前写锁。源数据库不迁移、不清空，也不改业务事实或原文。

本机私配、密钥、环境、锁、SQLite 临时文件及 `backups/` 整目录不打包；备份目录包含本轮修改前的私人配置快照，因此继续排除。被排除的本地备份和私配原件仍保留在电脑上。日常库、历史验收库、原文及失败证据沿用现有保存规则。

新增隐私扫描仅扩展到操作字段：

- `initialization_batches.specification_json`、`result_json`、`last_error`；
- `initialization_events.details_json`；
- `raw_import_events.sources_json`、`result_json`。

`source_scope` 已过滤配置秘密并移除 SEC User-Agent，但配置结果和错误诊断仍按防御性规则检查。扫描在交付数据库副本上进行；已识别密钥/联系信息脱敏后 VACUUM，避免空闲页残留。

`raw_import_events.source_root`、`raw_import_provenance` 原始获取时间和 ID、原文哈希、业务 `records` 均保持原值。导入证据不是今天重新下载；打包不改变它们的时间含义。

V4 引擎实例由 V4.1 脚本独立加载，只调整清单文件名、清单元信息和上述操作列策略。元信息在原引擎生成哈希和 ZIP 核验**之前**加入；没有发布后再修改 ZIP 的步骤。

## 回归证据

`tests/test_package_v41.py` 在临时小项目上验证：默认名称、新旧清单共存、旧清单原字节保留、已有包拒绝覆盖、新操作表脱敏、原文/事实/导入来源时间保留，以及原 V4 模块和文件不受修改。

`reports/v4_1/packaging-wrapper-regression.xml` 记录新入口与原 V4 打包回归共 26 项通过。该回归只生成临时测试 ZIP；真正项目包由交付步骤单独执行并核验。
