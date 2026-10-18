# V4.1 日常研究库历史初始化

历史初始化与日常增量使用同一个 `data/live` 数据库；不覆盖数据库。股票池仍是试运行范围，不能据此推定为用户投资范围或无偏历史股票池。

## 配置

```toml
[initialization]
enabled = true
start = "2024-01-01"
end = "2026-10-10"
sources = ["sec_edgar"]
data_types = ["filings", "documents", "financial_facts", "guidance", "relations", "operating_metrics", "commitments", "debt_maturities", "risk_disclosures"]
chunk_months = 4

[initialization.budget]
max_tasks = 180
max_requests = 220
max_bytes = 150000000
max_runtime_seconds = 900
max_cost_usd = 0
```

每份请求冻结起止日期，不在运行中把结束日期不断扩展为今天。新增证券通过 `universe.symbols`、`universe.ciks`、`securities` 或股票池 CSV 接入；生产代码没有股票名单或答案。CSV 支持现有通用 `symbol,cik,security_id,official_ir,website` 字段。

先按“目标公司配置、来源、资料类型、来源选项、分段日期”建立持久批次。新公司会增加对应批次，已有完全相同批次保留完成状态。变更来源选项、资料类型、日期范围或身份配置可能建立新批次；仍由原始缓存和业务记录去重避免重复下载/写入。当前 `sec_current` 不能直接用作历史初始化股票池：应先把发现结果固定为配置或 CSV，避免每次发现改变分母。

## 状态解释

| 状态 | 含义 |
|---|---|
| `not_initialized` | 当前配置要求的历史批次未执行，已有少量增量资料不代表初始化完成 |
| `in_progress` | 初始化已开始，仍有预算截断、等待重试、失败或未完成批次 |
| `acquisition_initialized` | 已枚举并取得该范围的适配器资料清单，相关任务已终结；仍有部分解析、明确拒绝或语义缺口 |
| `initialized` | 该范围的适配器资料清单已取得，适配器报告支持的解析全部完成 |

以上状态均不代表独立参考清单覆盖率、全部目标字段准确、历史股票池完整或当前来源最新。`acquisition_complete`、`parsing_complete` 单列。深层语义候选不能计为事实。初始化之后的日常增量检查有独立的来源成功时间和新鲜度状态。

每轮只执行一批受预算约束的任务。预算耗尽保持原分段日期和同一 backfill scope，后续轻量调度检查可续跑。没有完成的任务不能推进初始化完成标志。任务失败使用现有来源/资源退避；耗尽或无可行动任务的批次保持 blocked，不无限轮询。已有原文但解析失败的任务仍不算初始化完成；终态 `partial_parse` 被保留为明确解析缺口。

`initialization_batches` 保存范围、当前状态、下一次可重试时间和最近执行结果；`initialization_events` 追加保存启动、预算结果、失败和显式重试事件。进程在 scope 建立前退出时，仍有固定范围的 running 批次可在重新取得共享锁后恢复。

## 复用旧验收库的原始缓存

先按 `BACKUP_RESTORE_V4.md` 备份日常库，并确保停止新任务领取或等共享采集锁可用，然后执行：

```powershell
.venv\Scripts\python.exe -m equity_data.replay --source data/v4_sec_repeat --target data/live --sources sec_edgar,sec_directory
```

该命令使用本机采集锁和采集引擎锁，读取来源数据库的只读 SQLite 快照，并逐份核验原文 SHA-256、大小及 raw ID。只导入原文 blob 和 retrieval 证据，不复制验收库的研究记录、采集任务、成功水位或完成状态。

原始 `retrieval_id`、`retrieved_at`、`created_at`、原始 metadata 保留；今天的导入动作另记在 `raw_import_events` / `raw_import_provenance`。重复导入不新增原文/获取证据，允许追加一条“执行过导入”的操作记录。ID 冲突、文件损坏、路径越界明确拒绝。

随后运行正常历史初始化。适配器重新枚举官方资料清单，复用经过哈希核验的不可变归档原文，并用当前解析器产生事实。解析时间是实际解析时间；旧原文的系统获取时间保持旧值。动态清单仍会真实检查上游，缓存导入不能被当作一次今日上游检查。

## 程序接口

`equity_data.initialization`：

- `build_initialization_payload(config, start=None, end=None, symbols=None, sources=None)`：冻结明确范围和配置目标。
- `ensure_plan(store, config, payload=None)`：幂等建立批次；调用者持 `collector_lock`。
- `run_initialization_batch(store, config, payload=None)`：一次受预算约束的 `machine.execute(backfill)`，返回原引擎 `run`（含 universe）、批次和初始化状态；调用者持 `collector_lock`。
- `initialization_status(store, config, payload=None)` / `next_pending_plan(...)`：只读；允许 `mode=ro` SQLite 连接，无表时返回未初始化。
- `retry_initialization(...)`：记录显式重新尝试 blocked 批次；不私自重置来源失败重试预算，来源授权/重试预算仍用已有管理命令处理。

`equity_data.replay.import_raw_cache(target_store, source_root, sources=None)`：调用者持本机采集锁，内部再取引擎锁。不得在采集线程未停止时绕过锁直接执行 SQL 复制。

## 验证边界

`tests/test_initialization_v41.py` 使用合成来源验证实际 Queue/engine/Store 的预算中止、同范围续跑、完成后零写入重复运行、新公司复用已完成批次，以及原文导入幂等性、时间保留、损坏/ID冲突拒绝、共享锁和只读状态。测试不算真实 SEC 覆盖证据；真实日常库范围和库存另见本轮验收报告。

## 本轮查询性能修补

对实际日常库的只读快照（10,007 条记录、1,276 条获取证据）在临时内存数据库中测量查询计划与耗时，确认原文解析结束后查找历史字段的查询在扫描整个 `records` 表。`Store` 增加 `records(source, json_extract(data_json,'$.resource_key'))` 索引后，同一查询同样返回 15 行，中位耗时由约 80.13 ms 降至 0.063 ms。获取证据增加 `(raw_id, source, retrieved_at)` 索引，同一查找由约 0.085 ms 降至 0.0024 ms。

这是局部查询测量，不代表整体采集加速比例；网络和文档解析仍各占时间。生产修改只增加这两个索引，没有改原记录、时间、版本或查询语义。回归在旧结构临时数据库重新打开后逐字段核对原文、获取证据、研究记录、证据关系、验证和时间约束完全不变。详情见 `reports/v41/initialization/read-only-query-performance.json`。
