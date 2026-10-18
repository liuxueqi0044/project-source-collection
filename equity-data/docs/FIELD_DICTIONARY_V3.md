# V3 字段字典增量

V2 的物理表、原文、记录、单位和时间字段继续有效，见 FIELD_DICTIONARY_V2.md。业务提取合同见 DATA_CONTRACTS_V3.md；本文只列本轮新增。

## 记录与查询

`data.fact_stage` 为 candidate/extracted_fact/normalized_metric。统一指标须有独立语义映射证据；本轮不凭自定义 QName 自动映射。`data.semantic_contract` 的语义、未知原因和证据字段见合同说明。

`Store.query(processed_at=...)` / CLI `query --processed-at` 限制 `records.created_at`，并限制获取、支持原件、核验决定及已知时间约束。`known_at` 仍表示原文取得状态，不独自代表解析发生状态。结果 `system_time_semantics` 明示使用了哪种查询方式。`as_of` 的公开时间过滤独立保留。

## machine_resource_versions

新增 `resource_json`：该次获取时资料元数据的不可变快照。旧版本迁移后可以为 null，不能把当前元数据伪装成旧版本的元数据。`raw_id` 和 `retrieval_id` 仍标识精确原文及获取事件。

## machine_reprocess_audits

| 字段 | 定义 |
|---|---|
| audit_id | 历史重算审计稳定标识，重复相同结果去重 |
| resource_id / raw_id / retrieval_id | 资料、原文字节、该次获取的精确标识 |
| run_id / parsed_at | 本次重算任务和实际解析时间 |
| parser_versions_json | 本次涉及的解析器版本 |
| result_json | 解析状态、缺口及返回结果 |
| records_json | 隔离数据库中重建的字段记录，不自动提升为当前事实 |
| effects_json | 重建期间的校验、时间约束等副作用及历史元数据是否真实保存 |
| interpretation | 明示重建审计不能当作系统过去已经知道的信息 |

通过 `reprocess-audits` 查询，或 `export` 导出。最新原文仍按普通追加版本流程重算，历史原文在隔离数据库中处理。

## sec_document_dependencies

| 字段 | 定义 |
|---|---|
| resource_id / raw_id / retrieval_id | 依赖方文档及其精确原文版本 |
| company_id / accession | 支持文件允许关联的公司与申报范围 |
| references_json | context/unit 引用 ID 清单 |
| consumed_fingerprint | 上次解析已使用的支持声明指纹 |
| supporting_raw_ids_json | 实际使用的支持原件；仍须单独验证哈希 |
| missing_json / state | 缺失声明及待支持/已满足状态 |
| parsed_at | 依赖方实际解析时间 |

同公司不同申报、不同公司相同引用 ID 不得互借。新的支持指纹自动生成重处理任务，相同指纹不循环唤醒。

## 运行与报告

`task_execution_complete` 仅表示所选任务执行完成；`parsing_complete` 表示当前资源解析状态完整；`coverage_complete` 不由前两项推出。

`watch_events.jsonl` 逐轮追加实际运行时间、成功或失败、连续失败次数和结果。退出后文件和任务队列保留。

`company_type_freshness` 按来源/公司/类型汇总真实 raw_retrievals 的时间，另列 latest_parse_completion。离线重解析不会刷新获取时间。

独立参考覆盖的 schema、目标定位、期望、容差、未知分母和状态定义见 COVERAGE_V3.md。其清单是审核材料，不是生产公司配置，也不是自动事实来源。
