# V4 字段字典补充

V4.1 新增上下文维度展开、历史初始化、原文导入与统一状态字段，见 [V4.1 补充字典](FIELD_DICTIONARY_V41.md)。本文件保留 V4 的历史定义。

原始业务记录沿用 [完整 V1 字典](FIELD_DICTIONARY.md)、[V2 通用库存字典](FIELD_DICTIONARY_V2.md) 和 [V3 合同及时间字段](FIELD_DICTIONARY_V3.md)。所有时间使用带偏移的 ISO 8601；业务期间、公开时间、获取时间、解析时间分别保存。主库为 `data.sqlite3`，本机请求控制库为 `local-control.sqlite3`。

## 原文单位语义

`financial_facts.data.unit_definition`：expanded_numerator/expanded_denominator 是含 namespace_uri、local_name 的数组，分子分母分别保留重复因子；source_measures 保存 lexical_qname、展开身份、作用位置/分子分母。identity_method 是 `expanded_qname_multisets_no_cancellation_v1`。只有 URI＋本地名及结构一致才等价；不通过删前缀比较。展示用 unit 不承担唯一语义判断。金额单位、币种、scale/decimals 和事实期间仍取原文，不由股票代码推断。

## 主库任务和来源范围

|表 / 新字段|定义与关联|
|---|---|
|machine_inventory.resource_id|资源稳定 ID，来源＋资源键；并非一次运行 ID|
|raw_state|原文 saved/missing；重新发现不抹掉已有原文|
|refresh_state|该资源刷新任务是否成功、失败、待办或仅缓存|
|parse_state|当前原文解析进度，parsed、partial_parse、downloaded_unparsed 等|
|upstream_state|最新上游版本确认状态；旧原文存在不能推断最新|
|last_refresh_attempt_at / last_refresh_success_at / last_parsed_at|分别是刷新尝试、可靠刷新证据、解析的时钟|
|failure_class / refresh_generation|失败类别及刷新轮次，不能代替资料版本|
|machine_work_items.work_id / task_id|同一逻辑工作与 canonical task 关联；跨轮次复用，不清除失败流水|
|request_id|source＋URL/参数的请求身份；跨日期解析范围仍共享失败闸门|
|consecutive_failures / next_retry_at / exhausted_at / retry_epoch|本次重试预算、下次重试时间、上限触发时间及预算世代|
|machine_request_failures|同地址请求失败类别、次数、冷却和耗尽状态；不同解析范围不复制网络失败重试|
|machine_retry_resets|显式重置的追加证据、原因和关联范围；不删除原 machine_attempts|
|machine_attempts.failure_class / retry_at / retry_epoch|每次真实执行的失败分类、退避时间和重试世代|
|machine_task_demands|task_id＋generation＋scope_id 的需求边；既保留每轮需求又避免重复业务任务|
|machine_scope_checks.scope_id / source_id / scope_json|精确来源、股票池、日期及数据类型和来源选项；路径/预算不是业务范围身份|
|last_attempt_at|范围最近一次尝试|
|last_discovery_success_at|完整枚举可靠完成的检查时间|
|last_acquisition_success_at|该范围原文取得且刷新可靠完成时间；不等同解析成功|
|last_parse_success_at|当前原文解析完成时间；不能反推当时已公开|
|last_complete_check_at|范围完整检查的时间；预算、分页未完、失败、未知证券及解析缺口不能推进|
|last_run_id / current_status / progress_json|关联本轮、当前状态；含task_states、资源分母、raw取得、parse分项、未确认刷新、by_data_type|
|machine_scope_runs|run_id＋scope_id 的每轮状态流水，含 started_at/finished_at/status/progress_json|
|machine_parse_cache|resource_id＋raw_id＋input_fingerprint 的解析缓存，保留 parsed_at/result_json；依赖版本或解析版本变更失效|

资源状态的具体枚举以运行数据及 `docs/RUNTIME_V4.md` 为准。旧库升级添加列及关系表，原始资料、业务记录和失败流水不删除。

## 本机控制库

|表|字段定义|
|---|---|
|local_meta|key/value_json：paused、planning_cursor、last_dispatcher_tick、revision_planned_at、自动重试计数。planning_cursor 只记已枚举时点，绝非来源成功水位|
|local_requests|request_id；scope_key 为股票池/来源/类型/历史模式的去重哈希；payload_json 保存明确请求。state 为 pending/running/completed/partial/failed/interrupted；created_at/latest_trigger_at/started_at/finished_at 是调度时间；trigger_count 为合并触发次数；result_path/error 指向结果或失败|
|local_events|event_id、request_id、observed_at、kind、details_json：追加触发/合并/中断恢复证据|
|local_slots|slot_utc 为唯一计划时点，local_time 保留配置时区表示；request_id 关联合并后的工作；observed_at 为实际发现遗漏的时间|

两把 OS 锁分别控制本机采集生命周期和内层任务执行；文件内容不证明锁所有权。请求入队可以与采集并发，采集/解析只有一个执行者。

## 刷新报告

effective_scope 和 requested 分别为执行窗口与用户请求。task_execution_complete、parsing_complete、source_checks_complete、coverage_complete 分开表达，required_freshness_satisfied 还要求必要类型有实际已存资料。freshness 每项列来源、范围、最近成功检查、年龄小时和阈值；content_available 与 stored_evidence 显示实际存储版本数、层次、质量和最新期间/发布时间/获取时间。

stored_versions 是资料库存描述，不是独立覆盖分母，也不是无重复财务事实数。new_record_versions 指 supersedes 非空的新业务版本；新的 HTTP retrieval、任务需求边和运行流水是合理的审计增长，不能算重复业务事实。metrics 的 requests 含重试、bytes 为网络字节、elapsed_seconds 为运行秒数、estimated_cost_usd 仅为配置估算。

原文为 raw_blobs/raw_retrievals；语义层在 data.fact_stage、data.semantic_contract：candidate 为候选片段，extracted_fact 为完成支持字段解析但可能仍未独立核验的事实，normalized_metric 必须有明确映射证据。不得把 candidate、原文标题或自定义 XBRL 名称当作标准化指标完成。
