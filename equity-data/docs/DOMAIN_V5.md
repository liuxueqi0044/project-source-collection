# V5 竞争领域与证据合同

新增领域记录保存到原 `records` 追加版本体系，来源为 `competition`。主体、母子/品牌关系、竞争关系、事件、目标关联、研究推断、监测入口、快照和审核行为分别使用 `entities`、`entity_links`、`competition_edges`、`competitive_events`、`competitive_event_links`、`analysis_hypotheses`、`watch_endpoints`、`competition_snapshots` 和 `review_actions`。所有竞争 payload 带 `schema_version=competition-v5.0` 与 `evidence_refs`。

## 身份和关系

`put_entity(store, data, reviewer=...)` 必须提供稳定 `entity_id` 和 `name`。现有 SEC 公司使用 `sec:<10位CIK>`，不会因同名、代码、域名相同自动合并。未上市企业及品牌可以使用独立内部 ID；已登记外部唯一标识冲突会拒绝自动写入。证券映射不因创建企业而自动建立。

`put_entity_link` 保存品牌归属、母子公司、旧名称及身份映射。它不把两端主体合并。

`put_relation(store, data, reviewer=...)` 的键为 `edge_id`，包含 `target_entity_id`、`peer_entity_id`、`relation_type`、`product_or_business`、`geography`、`customer_segment`、`overlap_basis`、`evidence_status`、`review_status`、`valid_from/to`、`review_due_at` 与证据。`review_status` 为 candidate、confirmed、rejected、needs_review；`relation_type=unknown` 只能是未确认状态。

确认需要明确业务范围、重叠理由、审核身份和外部原文证据。`source_explicit` 是来源点名；`business_overlap_inference` 必须把证据指派到双方 `entity_id` 或 `evidence_entity_ids`。同业和共同出现不足以确认。软件核验的是证据完整性，不自动证明审核者对段落的语义判断正确。

未知地区或客户保持 `unknown`。未知地区不等于全球、未知客户不等于所有客户。有效期与审核处理时刻独立；今天确认的关系，即使填写较早 `valid_from`，也不会出现在过去可用信息查询中。

`review_relation(store, edge_id, decision, reason, evidence_refs, reviewer)` 追加关系版本及独立审核行为；基于旧版本的审核被拒绝。代理审核使用 `review_method=agent_evidence_review` 或相应 reviewer，并标为 `agent_reviewed/system_inference`，不能报告为人工审核。

## 原文校验

`validate_evidence(store, refs, require_external=False)` 要求每个引用包含 `raw_id`、`sha256`、`url`、`locator`、`excerpt`。有 retrieval ID 时核对该次取得的 raw 和 URL；没有时选择已保存匹配取得并补齐 ID。校验磁盘字节、SHA-256、字节数、原文路径和摘录。

HTML 去除 script/style/noscript/template 后只归一化空白，不删除负号、金额、否定词。`css:`、`xpath:` 校验指定区域，`document:text` 校验全文可见文本，纯文本可使用 `line:`。JSON 目前按保存的 UTF-8 原始文本校验；PDF OCR、图片和无法解码的二进制不属于此摘录校验能力。

`record_id` 可引用已有结构化记录，但该记录必须实际引用对应 raw。配置及审核 JSON 来源 `competition_config/competition_review` 是可审计的内部材料，不能用作确认外部事实的证据。派生结果自己的 JSON 会保存原文，外部事实仍必须通过 evidence_refs 回到外部版本。

## 查询与时间

`records_snapshot(store, dataset, as_of=None, known_at=None, processed_at=None)` 与 `query_competition(store, target_id, ...)` 分别限制公开可用时间、原文取得和处理时间，并递归检查证据、主体版本、快照版本、事件版本及关系版本。原文-only 当前网页保守使用取得时间；引用中自行填写旧发布日期不能证明该版本当时存在。引用已有披露记录时沿用它原有可用时间证据和限制。

事件关联必须保存精确的 `edge_version_id` 和 `event_version_id`。当前关系纠正后，旧关联进入重算缺口；不能拿当前关系拼接旧事件，或因最新证据损坏而悄悄恢复旧关系。历史切片仍可看到当时的旧关系与事件。`core_brief` 只接受 `fact_stage=extracted_fact` 的事件及已确认、范围匹配的关联；候选不算已提取事实。所有字段都不代表金融影响已独立验证。

继承 V4.1 的历史机器状态重放限制仍在：三种 cutoff 是严格过滤条件，但现存 availability constraints 的原始设计不能保证逐字节复原所有过去机器状态。见 `PIT_USAGE_V41.md`。

## 真实试运行资料准备

`configs/competition-trial.json` 是单独的代理审核清单，不是生产公司规则。它引用原试运行池 SBUX、HON、SAP 与六个同行，仅证明配置流程。`scripts/build_competition_trial.py` 从只读日常库按公司和申报类型发现最近已保存年度申报，再把所需原文连同原取得 ID、时间复制到隔离目录；不会把导入变成今天新下载。其余配置 `sources` 留给通用 import 命令真实取得，失败时不得用搜索结果替代。

HON 公司名单明确 Building Automation，但地区/客户未知。SAP 年报里的高管薪酬对标名单不被视为竞争证据；ERP 关系采用双方产品材料的有标识代理推断。Dunkin 与 Inspire 分开建档并保存品牌归属。该清单没有人工标签，不能当成独立人工验收集或全市场分母。

## 官方入口归属审核

通用 `competition.cli import` 不接受“任意一段外部摘录＋verified 标记”作为官方入口证明。满足以下之一才保留 `verified_official`：

1. 已登记实体的 `websites` 为 verified，证据明确指派该主体；入口和每个 allowed domain 都属于该已登记站点域，入口证据同样指派该主体。
2. 显式 `authority_review` 包含 method、reviewer、entity_id、domains、basis、identity_phrase 和 evidence_refs。证据必须来自被审核域、定位身份措辞且指派同一主体；入口及允许的重定向域不得越界。

第二路径记录的是可追溯的代理或人工归属审核，不声称独立完成法律主体/域名所有权审计。代理结果保存为 system_inference，不冒充人工审核。缺证明时仍保存原始请求状态，但官方状态降为 `needs_review` 并给出原因，规划器不将它当作已接通官方入口。

Dunkin 最初 about 页真实返回地域限制说明，不能用其标题补成咖啡业务证据。保留旧失败后，单独获取公开新闻列表枚举出的文章及其公司介绍。Dutch Bros/Oracle 的公开 SEC 原文可支持业务档案，不能证明被403阻断的 IR/新闻渠道已健康。

## 人工/代理事件审核入口

```powershell
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition.events_review --config configs/competition-validation.toml --input review.json --out reports/v5/event-review-result.json
```

配置路径按实际运行配置替换。该入口共用采集锁，无网络，不生成交易或利润影响。输入必须给出现有 event_id、fields、fact_evidence_refs、scope_evidence、reviewer、reason；method 明确为 `agent_evidence_review` 或 `human_review`，人工 reviewer 使用 `human:` 前缀。已有事件原文及审核版本都保留，过时版本审核会拒绝。

原始事件事实和范围证据分开保存。action/object 必须能在事实摘录中逐字定位；计划/完成状态必须有对应来源词句，不能把将来计划审成完成。业务、地区、客户的每个非未知范围须有该事件原文证据；仅公司档案所在国家不等于事件发生国家。非逐字范围映射必须保存源措辞及明确审核理由，局部范围不能解释成全球或全部业务。更改发生/公开日期需要带年份的完整原文日期；不自动补年份或时区。

此入口不接受金额、单位、币种、收入/利润影响等人工预填字段；数字核验仍使用专门的财务/价格解析合同。审核完成的事件可标记 extracted_fact，但同时强制 `automatic_extraction_success=false`、`review.counts_as_automatic_extraction=false`，且 extraction_method 为 agent_review 或 human_review。统计自动提取准确率、召回率和成功率必须排除这些审核记录。审核后使用当前关系版本重算该事件关联，追加新版关联并保留此前关联；不重新下载原文，也不依赖已完成采集任务重新执行。

## 将审核配置迁入日常库

```powershell
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition.transfer --source data/competition-validation --config configs/live.toml --dry-run --out reports/v5/config-transfer-inventory.json
.\.venv\Scripts\python.exe -X utf8 -m equity_data.competition.transfer --source data/competition-validation --config configs/live.toml --out reports/v5/live-config-transfer.json
```

源库始终只读；dry-run 只检查源库当前四类配置和全部嵌套证据，不创建目标库。正式迁移共用日常 collector_lock；采集正在执行时拒绝写入，稍后重试。只迁移 entities、entity_links、competition_edges、watch_endpoints 当前版本，以及它们实际引用的原文与获取证据。未知、拒绝、待核验状态保留，不会自动提升。目标中额外存在的配置不删除；同键变更追加新版本。

每份原文校验 SHA-256 和字节数，保留原 raw_id、retrieval_id、retrieved_at、metadata；已有身份冲突或损坏原文拒绝。来源注册表缺项补充，已有不同注册信息不覆盖，并记入收据。配置重新经过通用 import_bundle 核验，产生本次处理时间；源 record_id、created_at 与目标新版本映射保存在收据，不能把过去审核时间当作目标库过去已经完成处理的证明。证据所指源研究 record_id 不复制为目标记录，而改为 source_record_provenance；其来源定位和原文仍保留，目标历史过滤按原文获取时间保守处理。

没有事件、快照、财务事实、分析任务、成功水位或覆盖状态随迁移复制，因此“配置导入”不代表采集或初始化完成。导入后须按需 refresh/process 检查来源和生成分析任务。重复迁移同一配置不增加业务记录、原文或原始获取记录。个别配置核验失败时返回 partial 和逐项错误；已完成项保留，修复后安全续跑。此过程不是跨所有业务写入的一次总事务，收据的成功映射与失败列表是实际结果。命令退出码 1 表示 failed 或 partial，不能当作全部迁移完成。
