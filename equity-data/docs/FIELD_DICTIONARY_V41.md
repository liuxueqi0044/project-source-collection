# V4.1 字段字典补充

本文描述当前实现的数据合同，不声明真实库已完整取得所有资料。主库为 `data.sqlite3`，控制库为 `local-control.sqlite3`；旧业务及队列表见 [V4 字典](FIELD_DICTIONARY_V4.md)。公开时间、原始获取时间、导入时间、解析时间和调度时间分别保存。日期用 ISO 日期，时刻用带时区 ISO 8601；计数为整数，字节为 bytes，耗时为 seconds，新鲜度为 hours，预算费用为配置估算 USD。

## 上下文维度和证据

以下字段位于 `financial_facts.data`。`dimensions` 保留兼容的词法展示，身份比较应读取 `dimension_definitions`。不能删除前缀以后比较本地名。

|字段|定义与关联|
|---|---|
|dimensions[]|原始 axis/member/type，以及 typed 结构的 typed_xml；只作词法展示|
|dimension_definitions[].axis|原始维度轴 QName，保留前缀|
|member|原文成员文本；explicit 为成员 QName，typed 是未验证域值文本|
|type|explicit 或 typed|
|axis_expanded.namespace_uri / local_name|按该元素实际命名空间作用域展开的维度轴身份；无命名空间时 URI 可为空|
|member_expanded.namespace_uri / local_name|explicit 成员身份；typed 不凭文本构造此身份|
|context_element|维度所在 segment/scenario；位置也是上下文身份的一部分|
|source_locator / evidence_excerpt|维度元素在所属原文中的定位和摘录；必须与 context_evidence 指定原文联合使用|
|source_markup / source_markup_kind|原始片段，或明确标注为 DOM 序列化；original_source_fragment 表示保留原片段，不应把序列化结果冒充原字节|
|equivalence_status|explicit 为 expanded_qname_identity；typed 为 unverified_typed_domain_schema_not_loaded|
|typed_xml|typed 域子元素序列化；不代表已加载类型系统或验证域值语义|
|typed_namespace_bindings|原始 typed 片段的作用域命名空间绑定，用于保守复用判断|
|typed_reuse_method|identical_original_fragment_and_namespace_bindings_only；只可复用同片段与绑定，不推断不同 typed 内容语义等价|
|context_id / context_locator / context_evidence|上下文标识、位置和所属原文身份；context_evidence 来自输入原文证据并加 source_locator|
|supporting_raw_ids|实际使用的支持文件 raw IDs；与原始哈希资料关联|
|unit_definition|继续使用 URI＋本地名、分子/分母结构比较单位，详见 V4 字典|

explicit 上下文比较包括实体、期间、位置、展开后的轴和成员。不绑定的前缀、冲突定义、重复展开轴等拒绝输出；不同前缀但 URI/本地名相同可等价，同前缀但 URI 不同不能等价。typed 域未加载 schema，不能确认等价时拒绝合并；不同 context IDs 不因类似文本合并 typed 值。

解析 coverage 中的 `typed_contexts_semantically_unverified` 为 typed 语义仍待核验的上下文数量。`IXBRL_NUMERIC_REJECTED` 是拒绝记录，不能计为已提取事实。本轮 QName 缺陷来自合成复现，字典不声称真实历史样本曾出现该错误。

## 历史初始化主库账本

`initialization_batches` 每行对应目标配置＋来源＋明确分段日期及数据类型/来源选项。`batch_id` 是 specification 的哈希身份，相同批次复用；初始化的逻辑请求 ID 另保存在请求 payload 与状态报告。

|字段|定义|
|---|---|
|batch_id|批次稳定主键，init-batch: 前缀；不是每次运行 ID|
|target_json|证券/公司配置目标，至少有 symbol 或 cik；不从名字猜测身份|
|source_id|来源注册表/适配器 ID|
|start / end|该批次冻结的采集日期范围；不能当成每条财务事实的报告期间|
|specification_json|市场、目标、类型、日期及安全来源选项；决定批次身份|
|state|pending、running、in_progress、blocked、acquisition_initialized、initialized 等当前账本状态|
|acquisition_complete|资料清单可靠枚举、原文取得及任务终结条件满足；不代表独立完整性分母|
|parsing_complete|取得完成后适配器报告支持的解析完成；不保证每个目标字段已独立核验|
|created_at / last_attempt_at / completed_at|创建、最近批次尝试、资料取得完成时刻；completed_at 本身不等于 parsing_complete|
|next_attempt_at|批次最早可继续时间；底层来源/资源重试闸门仍生效|
|attempt_count|该批次执行次数；不是 HTTP 请求量|
|scope_ids_json|实际执行的 machine_scope_checks.scope_id 列表|
|result_json|run_id、network_budget、source_scope_checks、remaining_tasks、stop_reason、new_records 等最近摘要|
|last_error|批次异常描述；配置或运行故障可 blocked，不能当作没有新信息|

`initialization_events` 是追加流水：`event_id` 为整数主键，`batch_id` 关联批次，`observed_at` 为实际事件时间，`event` 标明开始/结果/失败/显式重试，`details_json` 保存当时证据。源文件、旧结果和失败历史不靠清表重置。

初始化请求 payload 的 `initialization=true`、`market`、`start`、`end`、`targets`、`sources`、`data_types`、`chunk_months` 冻结范围；`initialization_id` 是这些字段的哈希。状态汇总的 `batch_count`、`attempted_batches`、`acquired_batches`、`parsed_batches` 具有同一当前配置批次分母，但仍不是官方资料独立召回率。

## 旧原文验证导入

导入只复制验证通过的 raw_blobs/raw_retrievals 和缺失来源注册，不复制业务 records、任务、水位或成功标志。

|表/字段|定义与关系|
|---|---|
|raw_import_events.import_id|本次缓存转移操作 ID，主键；重复执行可以新增操作事件|
|imported_at|本次导入时刻，绝不替代原始 retrieved_at 或 published_at|
|source_root|原文来源库目录，定位导入出处|
|manifest_sha256|验证输入的原文/获取证据/来源注册清单 SHA-256|
|sources_json|此次导入选择的来源 ID 列表|
|result_json|核验原文/获取数、实际新增数、导入字节、零网络请求、业务记录前后数量及仍需发现/解析的标志|
|raw_import_provenance.import_id + retrieval_id|联合主键；关联一次导入及原 raw_retrievals 证据|
|original_retrieved_at|原始系统取得时间，保留源库值|

原 raw_id/sha256 身份及 retrieval_id/retrieved_at/metadata 保留。新增 blob 保留源库 `created_at` 与非路径元数据；已有同哈希 blob 则保留目标元数据及创建时间，不被导入覆盖。目标表不为去重 blob 另外记录源库创建时间，本轮7份此类差异由保留的源库和逐项导入审计解释；它们没有改变原始获取流水。`result_json.imported_bytes` 是此次验证输入的原文总字节，即使已缓存也计入；实际新增另看 added_blobs/added_retrievals。重复导入新增操作事件与 provenance 边属于合理审计增长，不能算新增业务事实或重新获取原文。`requires_discovery_and_replay=true` 和 `acquisition_complete=false` 明确导入本身未完成初始化。

## 控制库扩展与恢复

|字段|定义|
|---|---|
|local_requests.continuation_json|从本次精确 source scopes 的未完成任务建立的后续工作收据；不是泛化 partial 状态|
|continuation.items[]|source_id、scope_ids、task_ids、kind、failure_class、eligible_after；仅到期且可行动的来源继续|
|continuation.suppressed[]|为何不自动网络重试：解析、缺依赖、不支持、重试额度耗尽或无明确传输重试依据等|
|payload.window_start_hint|同范围请求最早需要补采的日期；合并取最早|
|payload.resume_scope_ids|需要恢复的精确来源范围，并集传递给后继|
|payload._local.recovered_request_ids|异常请求来源链；不改变逻辑采集身份|
|payload._local.retry_parent_id|有限重试的父请求 ID|
|local_events.details_json.category|normal_schedule、missed_schedule_catchup、unfinished_work_resume、limited_failure_retry、manual_refresh、historical_revision_check、historical_initialization；旧未知分类保留|
|details_json.reason|当时精确触发字符串；分类改进不回写历史原因|
|details_json.scheduled_slots|计划时点 UTC/本地时间，关联一次合并工作|
|owner_exit_recovery / recovery_window_transfer|旧请求恢复和转交事件；保存前后窗口、scope IDs、旧启动时间和后继 ID|

窗口转交、恢复事件和旧请求终结在一个 SQLite 事务内完成。股票池、来源、类型或初始化范围不同的请求不能合并。

## 统一状态和库存接口

`python -m equity_data.local --config configs/live.toml status --out reports/current-live-status` 生成读取当时的 JSON/Markdown。结果只读数据；历史 `decision-report.json` 仍是当次结果，不随之后时钟重写。

|字段|定义|
|---|---|
|observed_at / configured_pool / configured_securities / enabled_sources|当前观察时间、配置范围及启用来源；启用并不等于真实接通或成功|
|last_recorded_plan_slot / next_normal_schedule|最近记录计划时点、按配置时区计算的下一正常时点；不承诺关机/休眠时实际执行|
|recent_requests[].events / payload|每轮原始触发、恢复证据和冻结请求；started_at/finished_at 为实际执行时刻|
|latest_collection|最新报告的初始化、执行/解析/时效、metrics、storage_growth、原文状态轴与缺口摘要|
|source_health|来源聚合尝试/成功/错误计数和时间；不代表所有公司所有历史范围成功|
|source_scope_checks|精确来源、池、日期、类型范围水位和分阶段进度；应连同 scope 阅读|
|initialization|当前配置的初始化状态、范围、目标、批次数与明细|
|current_refresh_assessment|按当前时间、当前精确配置和恢复边界重新判断刷新时效；初始化报告不能替代近期刷新|
|research_library_state|not_initialized、initialization_incomplete、initialized_update_incomplete 或 initialized_update_checked_with_reported_gaps；必须同时查看解析缺口|
|unresolved_gaps|未解决缺口的来源、公司、类型、code/status/message、首次/最近出现时间及次数|
|inventory.by_company_and_type|company_id＋dataset 分组，保存版本/逻辑键、该数据集 period_start/end 库存范围、公开范围、原文取得范围及最近解析时刻；财务事实期间不等于申报报告期|
|stored_versions / stored_keys|追加保存版本数；不同 source/record_key 计为逻辑键（financial_periods 的键口径见下项）；均非独立资料分母|
|inventory.financial_periods|按公司、period_start、period_end、unit、currency 分组的 financial_facts 保存版本和不同 record_key 数|
|inventory.semantic_layers / quality|按数据集及 fact_stage/quality 的版本库存；候选不得计为已提取事实|
|inventory.raw.blobs / bytes|原文 blob 总数和总字节数，不是本轮网络流量|

`first_evidence_acquired_at` / `latest_evidence_acquired_at` 源于 records.retrieved_at；`latest_parsed_at` 源于 records.created_at，不因原文导入而伪造旧解析。库存包含历史版本，不默认按某个历史可见时点过滤；研究查询仍需结合公开、取得和解析时间条件。某数据集有记录不证明当前池中的每家公司都有足够记录。

`earliest_period_start` / `latest_period_end` 是组内对应字段的最小/最大值。状态 Markdown 当前将这两列显示为“报告期起点/终点”，但对 `financial_facts`，它们是数字上下文的期间范围，可能包含比较期和未来到期等日期；不得将 `MAX(financial_facts.period_end)` 当作最新已取得财报。`filings.period_end` 则取原始 SEC submissions 的 `reportDate` 或 null。独立覆盖输出 `period-inventory.json` 按公司分别保存定期申报的 form/reportDate/filingDate/acceptanceDateTime、已有主文状态及所有财务上下文期间；年度/季度报告范围为 10-K、10-Q、20-F 及更正，其他表单的 reportDate 不自动成为定期财报期。

`current_refresh_assessment.scope_matches_current_configuration` 要求报告的股票池、来源和类型与当前配置完全一致。子范围刷新可能已完成自己的检查，却不能替代整个配置的必要信息时效；应读取该 request_id 的范围报告。共享锁忙时刷新请求可保持 pending，进程退出成功或等待窗口结束均不等于刷新成功。操作与完整 `[local_schedule]` 配置要求见 [本机操作说明](LOCAL_OPERATIONS_V41.md)。

`metrics.requests/bytes/elapsed_seconds` 与 `storage_growth` 分开：网络重复检查可能合理新增 retrieval 和任务流水，不应新增重复业务事实。所有已解析事实仍保留质量状态；自定义指标不凭名称强行变成统一指标。
