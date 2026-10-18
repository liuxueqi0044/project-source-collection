# V4：持续采集、任务合并与范围检查账本

本轮保持单机执行。`execute(store, config, args)` 仍是入口，SQLite 保留原文、历次获取、旧记录、任务尝试和每轮需求；原引擎的操作系统文件锁仍限制每个数据目录一个采集执行者。

## 工作项与失败恢复

`machine_work_items` 将同一来源、资源身份、URL、参数和资料类型的获取任务合并为一个工作项。更新 generation 是新需求，存入 `machine_task_demands`，不再复制失败任务。保留资源身份是为了避免同一 URL 的不同公司或语义上下文被错误合并。任务的所有执行记录保存在 `machine_attempts`，原任务的 `generation` 仅指最近需求，不是完整历史。

同一 HTTP URL/参数对应不同解析日期窗口时，资源任务仍分别保留，但 `machine_request_failures` 按来源+URL+参数共享网络失败冷却和上限。一个窗口的 403 不会因新滚动窗口资源键改变而产生又一次请求。请求成功后解除该网络闸门；解析失败仍以各自解析上下文计算预算。此表也在状态 JSON 的 request_failure_gates 中公开。

认证、权限、限流、暂时网络、解析、缺依赖、不支持和预算不足分别分类。失败不会因为新 update generation 绕过冷却。失败达到本轮执行次数上限后保持未完成，下轮只在到期后再试。获取/发现工作项达到资源级连续失败上限（默认 5）后进入 exhausted，新轮次不再请求。获取后的解析失败通常通过已保存原文离线重试；新的更新轮次也可检查上游更正。成功会清零连续失败计数、解除相应未完成缺口，旧失败尝试不删除。

```toml
[scheduler]
max_task_attempts_per_run = 1
max_resource_attempts = 5
retry_seconds = 60
retry_max_seconds = 3600
authorization_retry_seconds = 86400
rate_limit_retry_seconds = 60
```

普通错误按连续失败次数指数退避并封顶。权限、认证和缺依赖默认至少 3600 秒；其配置的间隔也作为本类最小封顶值。限流默认 60 秒起步。HTTP 客户端内部仍独立执行已有的有限重试及 Retry-After 处理；队列的封顶冷却在这些请求全部失败之后生效。`permission_retry_seconds` 不是有效参数。IR 遍历深度属于可续跑资源限制，增加 `ir.max_depth` 不改变范围身份并可立即续跑；预算和遍历限额不消耗失败预算。

解决权限或服务故障后，可显式调用 `engine.reset_retry_budget(store, source_ids=[...], resource_ids=None, scope_ids=None, reason='explicit_operator_retry')`。函数持有引擎锁，仅重置 failed/blocked/retry_wait/exhausted 的已登记工作项，返回 reset_count/resets；不重置正在运行或成功的任务。重置增加 retry_epoch、记录 machine_retry_resets，旧 machine_attempts 的 epoch、失败和时间全部保留；下轮同范围 run/update 才真正执行。状态报告导出这些重置记录。命令行接入见 CLI `retry-failed` 帮助。

## 精确范围与补采

来源范围包含市场、原始配置股票池、有效起止日期、数据类型和来源选项；预算、存储位置、联系邮箱等不进入身份。新滚动日期是新范围。同一资源可关联多个范围，但执行不会自动扫描旧库所有 pending/failed 任务。

Python 调度入口可传 `args.resume_scope_ids`（字符串或字符串列表），或 `config['resume_scope_ids']`。只有明确指定且来源仍在本轮 sources 里的旧范围被纳入共同预算。未知 scope 或未选来源会报错。恢复旧任务时使用其保存的日期与来源范围；新增子资源也属于该旧范围。旧范围未完成资料不能被新窗口的成功状态替代。

新增 Python 参数 `args.data_types`（逗号字符串或列表）覆盖 `config['data_types']`。`full_history=True` 或 `revision_refresh=True` 设置 `_refresh_archives`，使 SEC 已归档原文重新检查；普通增量优先复用不可变申报原文，同时继续刷新申报清单。命令行支持哪些参数，以主 CLI 的帮助输出为准。

现有 V3 库中的无 scope 旧任务保留作证据，不自动纳入 V4 当前采集。对既有库重新 backfill 会登记 V4 的需求和范围；生产 live 目录与实验目录分开。

## 查询接口与账本

- `engine.source_scope(config, source_id)` 返回规范化范围。
- `engine.source_scope_ids(config, adapters)` 返回来源到 scope ID 的映射。
- `engine.scope_status(store)` 或 `Queue(store).scope_checks()` 返回全部范围检查；`scope_status(store, config, adapters)` 返回配置对应范围。
- `execute` 返回 `effective_scope`（market/start/end/requested_data_types/source_scope_ids/resumed_scope_ids）和 `source_scope_checks`。

`machine_scope_checks.scope_json` 经接口解码为 `scope`，`progress_json` 解码为 `progress`。每条包括：

| 字段 | 含义 |
| --- | --- |
| last_attempt_at | 最近尝试这个确切范围的时间，失败也更新 |
| last_discovery_success_at | 发现遍历关闭且动态原文已有有效获取检查的保守时间 |
| last_acquisition_success_at | 此范围已发现资料的原文获取检查完成时间，不要求数字全部解析 |
| last_parse_success_at | 此范围当前原文解析结果确认完成时间；实际每份解析时间见 inventory.last_parsed_at |
| last_complete_check_at | 发现、获取、当前解析均完成且无分页/预算/上游刷新未知的检查时间 |
| current_status | complete_check / incomplete / budget_limited / not_rechecked / checking |
| progress | 各阶段布尔值、任务状态、资料数量、解析状态、类型与未确认刷新资料列表 |

发现/获取/全检查时间使用动态资料确认时间的保守最小值；重新解析或读取旧缓存不能把它改为现在。`last_*` 保留历史成功，**必须同时查看 current_status/progress**，不能把上次成功误读成当前成功。`machine_scope_runs` 保留逐轮检查结果。`complete_check` 只针对适配器可枚举的配置范围，不证明网站全量披露、字段召回率或财务准确性。

`progress.by_data_type[type]` 分别给出 `discovered`、`raw_saved`、`acquisition_complete`、`parse_complete`、`parse_states` 与 `last_upstream_check_at`。资料级 data_type 不等于所有其中可能出现的语义字段：例如 documents 原文已保存，不意味着 guidance 数字已获取。无该类型条目不能借来源总成功补成已获取。未解决公司身份、分页、预算、403 和部分解析不能推进全检查。

## 原文、刷新、解析和最新性分别记录

`machine_inventory` 与导出 CSV 包含：

| 轴 | 字段 | 例子 |
| --- | --- | --- |
| 已有原文 | raw_state | missing / saved |
| 本轮刷新 | refresh_state | not_checked / pending / running / succeeded / cached / failed / deferred |
| 当前保存原文解析 | parse_state | unknown / downloaded_unparsed / parsed / partial_parse / not_disclosed / not_applicable / source_conflict |
| 上游确认 | upstream_state | unknown / confirmed_at_response / cached_not_rechecked / unknown_after_failure |

旧原文已解析但本轮 403 时，raw_state 仍为 saved、parse_state 仍为 parsed，refresh_state 为 failed，上游状态为 unknown_after_failure。本地库生成日历只能证明当前库规则的生成状态，不证明交易所公告被检查；日历适配器原有知识限制仍保留。

## 原文解析缓存

适配器可以实现 `parse_input_fingerprint(ctx, resource, raw)`。只接受最新适用的解析指纹相同且 raw 相同的结果复用；不能在历史缓存中找到 A 就跨过 A→B→A 的实际状态恢复。指纹应包括解析代码版本、元数据和相关支持文件版本。清单/索引枚举节点不跳过解析，避免遗漏子任务。显式 reprocess 仍实际执行解析。缓存后也调用依赖通知钩子，维护支持文件后到的重解析关系。

指纹检查只针对实际被调度的任务。同一 generation 的已完成 backfill 保持零执行，升级解析器不会凭空唤醒其全部旧任务；应执行 update 或显式 reprocess 才处理新解析版本。不能把零执行的重复 backfill 报告成已用新解析器重新验证。

历史版本重解析继续使用 V3 隔离审计，不提升成当前研究事实。其含义是用当前解析器和保存环境重建解析，不承诺重建当时可获得的支持文件集合；审计的公开时间和获取时间不得改写。

## 验证证据与限制

`reports/v4/runtime/before.json` 保存 V3 三轮同地址 403 的 1/2/3 次调用，以及已有 raw 状态被重新枚举抹除；`after.json` 保存 V4 的 1/0/0 与保留 parsed。测试在受控适配器中推进冷却期限后返回成功，验证真实的成功状态转换及保留失败历史；它不是 BLS 真实网络恢复。V3 实际 BLS 403 的历史证据依然有效，不能宣称其已获授权或恢复。

故障来源与正常来源分别调度；请求数、字节数、运行时间和费用仍受同一轮预算限制。独立参考清单的覆盖报告与这些执行账本分开，测试通过不代表资料完整。
