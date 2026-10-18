# V4.1 日常研究库与现有调度

本轮仍为 Windows 单机。复用任务 `EquityData-0a1f6db1282b-Collector`，Asia/Shanghai 每日 09:00、20:30、23:30，以及登录检查与每 5 分钟轻量检查。没有新增任务、云服务或订阅。实际启用情况与真实运行证据见本轮验收报告；Windows Ready 不表示数据已齐备。

## 初始化日常库

`configs/live.toml` 的日常库是 `data/live`。试运行股票池仍为用户确认的 SBUX、HON、SAP，不是投资范围或无偏历史股票池。首次明确历史范围为 **2024-01-01 至 2026-10-10**，初始化渠道为 SEC EDGAR；其他日常渠道仍独立检查。

下列 `equity_data.local` 本机入口及 Windows 管理脚本需要完整的 `[local_schedule]` 配置；`configs/live.toml` 已具备。独立 LIN 验收使用 `configs/acceptance_v41_new_company.toml` 与 `scripts/run_new_company_v41.py`，该脚本调用相同通用初始化器、队列和锁，但验收配置不是本机调度安装配置，也没有将 LIN 加入日常股票池。不要把这个精简验收配置直接用于本机 `initialize/status/tick` 或 Windows 安装入口。

在项目根目录 PowerShell 执行：

```powershell
# 一轮最多180任务、220请求、150MB、900秒；用完预算会保留待办。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml initialize

# 最多领取20个队列请求；初始化按预算批次推进，无就绪请求时提前停止。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml initialize --max-batches 20

# 更新初始化范围，日期在请求创建时固定，不在运行中追着today移动。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml initialize --start 2024-01-01 --end 2026-10-10
```

每个批次按证券配置、来源和日期区间确定身份，当前每段4个月。批次可跨多轮预算执行。已完成公司的相同历史区间会被复用；新公司创建自己的批次，不清空已有库。已取得原文但存在字段解析拒绝时，状态是 `acquisition_initialized`，不是完整字段提取成功。失败、缺权限、无法确认的身份或尚未枚举完整的目录不会计为初始化完成。

初始化与刷新共用持久队列；已有其他请求时，`--max-batches` 的领取额度也可能用于较早的请求。执行后应查看返回的初始化进度和未完成请求，而不是把领取次数当作已完成历史窗口数。

本轮大批量历史运行曾在批次边界安全暂停后，通过 `scripts/run_bootstrap_v41.py --requests-per-second 5 --max-batches 100` 调用相同生产队列、锁和初始化器继续执行。该手动进程覆盖请求速率，不修改每日任务或 `configs/live.toml` 的每秒2次设置；请求、任务、字节、运行时间预算仍按每批执行。它仅供明确的 SEC 初始化操作，运行前应确认队列中的工作范围。SEC [官方访问说明](https://www.sec.gov/about/developer-resources)在本轮核查时要求每用户总量不超过每秒10次；多进程或其他设备不能分别各用该额度。本项目继续保持单采集执行者。

`auto_on_pool_change=true` 时，现有任务的轻量检查只会为有可执行的历史待办排队。预算耗尽可继续；到期网络重试遵守来源失败闸门。没有 scope 且无预算原因的异常结果会阻塞并留证，不允许每5分钟无限请求。普通增量的有限失败重试与历史初始化账本分开；IR 入口缺失、深层语义或单纯部分解析，不会让正常来源每小时全部重跑。

修复网络、权限或配置后再明确恢复：

```powershell
# 若来源已耗尽重试额度，先开启新的重试周期，保留旧失败历史。
.venv\Scripts\python.exe -m equity_data --config configs/live.toml retry-failed --sources sec_edgar --reason network_configuration_fixed
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml initialize --retry-blocked --reason network_configuration_fixed --max-batches 20
```

`--retry-blocked` 在共享锁内重置所选初始化范围的阻塞账本。它不绕过供应商权限，也不伪造原文或成功进度。

## 修改股票池

直接修改 `configs/live.toml` 的 `[universe].symbols`。也可改为 CSV：

```toml
[universe]
mode = "configured"
symbols = []
ciks = []
csv_file = "trial_pool.csv"
```

`configs/trial_pool.csv` 是可直接使用的试运行示例。CSV 路径相对于配置目录，至少提供 symbol 或 cik。更名、同名或不同股类需要来源证实的稳定身份，不能只凭名字猜测。

改池后用同一 `initialize` 命令；现有 Windows 任务也会发现新配置的未初始化批次。若新公司应覆盖更晚历史，先更新 `[initialization].end`，或显式传 `--end`，再初始化。仅临时验证某公司可使用 `initialize --symbols NEW_SYMBOL --start ... --end ...`；这不会永久修改日常股票池。不要在已有请求执行中修改 CSV；已入队请求记录 CSV 哈希，发现变化会明确失败并要求以新配置重新入队，避免混淆范围。

## 原文复用与证据时间

```powershell
.venv\Scripts\python.exe -m equity_data.replay --source data/v4_sec_repeat --target data/live --sources sec_edgar,sec_directory
```

此入口验证来源库原文的哈希、大小与ID，在共享锁和引擎锁内追加原文及原获取流水。新增 blob 保留源库的 `created_at` 和非路径元数据；目标库已有相同哈希 blob 时保留目标元数据，不覆盖其已有创建时间。获取流水的原 `retrieval_id`、`retrieved_at` 和元数据完整保留。本轮7份去重 blob 的源/目标创建时间差异由源库和逐项审计保留，目标表没有另存一份源 blob 创建时间。导入时间单独记入 `raw_import_events`，不是新的公开或获取时间。它不复制旧研究记录、不复制成功水位、不证明初始化完成；后续正常发现与解析会建立新解析版本。重复导入可以增加审计事件，但不能增加重复原文和原获取记录。不得直接拼接或覆盖 SQLite 文件。

## 刷新、查询和统一状态

```powershell
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh --symbols SBUX,HON,SAP --sources sec_edgar --data-types filings,documents,financial_facts
# 可选：在排队状态下追加等待；这不是整次采集的运行时间上限。
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml refresh --wait-seconds 60
.venv\Scripts\python.exe -m equity_data.local --config configs/live.toml status --out reports/current-live-status
.venv\Scripts\python.exe -m equity_data --config configs/live.toml query financial_facts --limit 20
.venv\Scripts\python.exe -m equity_data --config configs/live.toml trace RECORD_ID
.venv\Scripts\python.exe -m equity_data --config configs/live.toml export --out exports/live
```

共享锁忙或队列已有请求时，默认 `--wait-seconds 0` 可能返回 `pending` / `queued_owner_active`。命令退出码0不表示所请求的刷新已经完成；等待结束仍可能有待办。应沿返回的 `request.request_id` 检查 `data/live/local-runs/<request_id>/decision-report.json`、请求状态、实际范围和各数据类型时效，报告尚未生成时保持未完成。

SEC 子范围刷新只检查显式选定的来源和类型，不等于整个日常库已刷新。统一状态要求报告与当前配置的股票池、来源和类型精确一致；当前必要信息还含 `calendar_sessions`，因此子范围报告不能把全配置状态改成新鲜。检查子范围结果请读取该请求的报告；要刷新整个当前股票池及已配置渠道，使用不缩小范围的第一条 `refresh` 命令。

状态输出同时包含股票池、渠道、初始化范围与各批次、来源及精确范围水位、触发事件、最近启动/结束、原文/解析/新鲜度、质量、未完成事项、请求与存储变化、下一正常计划。库存按公司、资料类型、期间列出，统计的是保存版本，不能代替独立覆盖率分母。`status --out` 写 JSON 与便于阅读的 Markdown；状态读取不创建初始化表。

当前状态表的“报告期起点/终点”是对应数据集 `period_start` / `period_end` 的库存范围。财务事实的结束日可能是比较期或未来到期、承诺等上下文日期，**其最大值不是最新财报期间**。最新定期财报请看独立覆盖的 `period-inventory.json`：按 10-K、10-Q、20-F 及更正的原始 SEC `reportDate`，分别列明已发现与已有主文的报告期、申报日期、接收时间及解析状态。

`not_initialized`、`initialization_incomplete`、`initialized_update_incomplete` 分别表示尚未初始化、历史仍未取得完整、历史已取得但当前更新不能确认。初始化取得完成仍可以有解析缺口。统一状态会按当前时间重新检查时效并验证最新报告是否对应当前配置，不沿用另一股票池或旧时间的布尔结论。

每份 `data/live/local-runs/<request_id>/decision-report.json` 是该次实际请求的历史结果。`trigger` 保留原始原因和当时记录的分类，合并或恢复链单独留存。新版本不会改写旧版 12:41、13:49、14:54 等运行的解释。最新状态展示与历史结果是两件事。

原文、候选、提取事实、统一指标分开。供应商、客户、债务、承诺等只有候选时，不作为完整结构化事实。历史查询须使用公开可用时间、实际取得时间和解析时间限制；今天导入旧原文不等于当年系统已持有。

严格历史查询请先阅读 [三种时间过滤及剩余限制](PIT_USAGE_V41.md)：`--as-of` 约束公开可用时间，`--known-at` 只约束原文取得时间，`--processed-at` 才约束解析结果的生成时间。`financial-snapshot` 尚无 processed 入口，不用于重建历史已解析机器状态。即使三者同时使用，后来解释旧原文产生的时间约束仍可能保守排除旧结果，不能承诺逐字重放历史机器状态；直接 SQL 和普通导出也不会自动应用这些过滤。

## 管理与备份

```powershell
.\scripts\manage_local.ps1 -Action Status -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Run -Config configs/live.toml
.\scripts\manage_local.ps1 -Action Pause -Config configs/live.toml
# Pause 不终止正在执行的请求；等其结束后再备份，锁忙则保留暂停并稍后重试。
.venv\Scripts\python.exe -m equity_data.backup backup --storage data/live --destination backups/my-live-snapshot
.venv\Scripts\python.exe -m equity_data.backup verify --backup backups/my-live-snapshot
.\scripts\manage_local.ps1 -Action Resume -Config configs/live.toml
.venv\Scripts\python.exe -m equity_data.backup restore --backup backups/my-live-snapshot --destination data/my-restored-live
```

`Pause` 停止领取新请求并停用本项目 Windows 任务；已经运行的请求继续完成。备份前确认当前采集已结束，或在锁忙拒绝后重试，不应把暂停命令返回当作采集进程已经终止。完成维护后使用 `Resume` 恢复领取和既有任务。

仅在希望卸载自动调度时执行以下入口；它验证任务归属与动作，只清理本项目任务、保留数据库及原文，也不会强行终止已运行的采集请求：

```powershell
.\scripts\manage_local.ps1 -Action Uninstall -Config configs/live.toml
```

已有采集者会持有操作系统锁；异常进程退出后锁自然释放。补采旧请求的最早日期和恢复scope在同一事务中交给同范围后继，再终结旧请求；不同股票池/来源/类型不混合。备份和导入也遵守锁，忙时拒绝而非与写库并发。

恢复到新目录，不覆盖原库。恢复标记保留原获取/解析时间，要求各来源重新真实检查后才能宣称当前新鲜度。Windows 任务不会自动改为恢复副本。未登录、关机或休眠不采集，恢复登录后检查遗漏；锁屏且保持登录可运行。本轮未自然发生的登录、休眠、新披露或更正，仍列为待观察。
