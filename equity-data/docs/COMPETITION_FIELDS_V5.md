# 竞争模块数据合同

业务数据继续存于不可变 `records` 及对应数据集视图。所有记录共有：record_id、record_key、source、raw_id、retrieval_id、company_id/security_id、published_at及精度/时区、available_at及依据、retrieved_at、created_at、period_start/end、parser_version、supersedes、quality、knowledge_type。共有字段定义沿用原字段字典。

|数据集|主要字段与含义|
|---|---|
|entities|entity_id稳定企业/品牌身份；entity_type；name；aliases带来源；external_ids；company_id独立映射；websites及审核证据；role为target/peer|
|entity_links|link_id；from/to_entity_id；brand_of/subsidiary_of/formerly_named/identity_mapping；有效期；审核、主体版本和原文证据|
|competition_edges|edge_id；target/peer_entity_id；relation_type；product_or_business、geography、customer_segment；overlap_basis；evidence_status；review_status/review_kind；valid_from/to、review_due_at；entity_version_ids|
|watch_endpoints|endpoint_id、entity_id、url、allowed_domains；接入方式、选择器、格式、语言/币种；official_status与authority_review；入口证据；配置版本；枚举深度和数量上限|
|competition_snapshots|endpoint/entity/resource_key；raw_id、source_content_sha256；body_text/body_hash；原始字段、标题、发布时间精度；items；issues；valid；semantic_stage=raw_normalized_snapshot|
|competitive_events|event_id与event_identity依据；actor_entity_id；event_type、action、object；业务/地区/客户；status与期间；old/new_value、unit、currency、period、价格条件；fact_stage；verification_status；snapshot_version_id；evidence_refs|
|competitive_event_links|event_version_id与edge_version_id；target/peer_entity_id；matched_scope、scope_checks、mismatch_or_unknown；review_status；relevance_reason；原文证据；不含财务影响断言|
|analysis_hypotheses|独立推断载荷、目标、支持版本与证据、假设及其它解释；默认流程不自动生成财务影响假说|
|review_actions|审核前后版本、决定、理由、审核者和审核证据；不是来源事实|

`evidence_refs` 每项必须包含 raw_id、sha256、url、locator、excerpt；验证后补 retrieval_id、source、retrieved_at、hash_verified、excerpt_match。可带 entity_id/evidence_entity_ids说明证据适用主体，record_id说明有已核验披露时钟的来源记录。原文摘录只做空白归一化，不能仅凭词语共现证明数字或关系。

|运行表|作用|
|---|---|
|competition_jobs|不可变input_json的任务身份哈希；state、attempts、各阶段时间、next_attempt_at、result_json、last_error|
|competition_job_history|逐次queued/started/completed/retry/owner_interrupted历史，不通过清空失败美化状态|
|competition_endpoint_state|入口配置指纹、尝试/完整成功检查/处理时间、下次有资格执行时间、最后原文及快照、连续失败|
|competition_runs|每次实际触发原因、冻结计划、开始完成时间、采集与分析分开结果|
|competition_cycles|按准确scope保存跨预算轮次的起点和未完成计划，避免续跑重建发现任务|

没有证券代码不影响企业身份；company_id不能由名称/别名静默合并。日期不明为null或unknown，地区不明不等于global。源文件更改保持原文版本和快照版本；无明确项目/合同身份的同类报道只标重复候选，不强行合并为同一业务事件。
