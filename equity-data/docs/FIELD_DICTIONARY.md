# 字段字典与时间语义

实现使用 SQLite 的证据表与统一版本表。下面的 23 个业务数据集各有同名只读 SQL 视图，均为 `SELECT * FROM records WHERE dataset=...`，业务字段保存在 `data_json`；视图不是独立物理表。视图展示所有版本，**不自动作历史时间过滤**；研究查询应使用 `Store.query(as_of=..., known_at=...)`。空视图不表示已覆盖。可连接接口、接口已实测、真实数据已保存是不同状态，实际状态见覆盖报告。不存在的业务字段保持 `null` 或未提供，并记录缺失原因，不能用零填补。

## 基础约定

- `company_id` 标识发行公司，如 `sec:0000320193`；SEC CIK 不等于证券或股类 ID。
- `security_id` 标识明确的证券或股类，如 `local:us:aapl-common`。本地 ID 来自显式配置，不宣称全球证券主档已验证。代码不是 ID；更名、代码变更及退市不能另造或删除原证券身份。
- 宏观、政策、交易所日历等不属于某个公司的资料，两个 ID 可空。公司级财务不擅自绑定某个普通股证券。
- 日期为 ISO `YYYY-MM-DD`。`available_at`、`retrieved_at` 和查询时间须带 UTC 偏移，入库规范为 UTC，保留微秒。`published_at` 保留原始日期或带偏移时间及精度。查询的纯日期 `as_of=2024-01-01` 解释为该 UTC 日末；纽约收盘查询应传明确偏移。
- 金额保留来源实际单位；美元、千美元、百万美元不相互混用。`scale=1` 指 API 已提供单位下的数值，不再乘财报页面显示倍率。财务累计数不自动当作单季数；以开始及结束日期判定期间。
- `knowledge_type` 取 `fact_observation`（事实观测）、`company_statement`（公司陈述）、`expert_opinion`（专家意见）、`survey_estimate`（调查估计）、`system_inference`（系统推断）。此分类不代表已核验。
- 原始字节保存在 `raw/<hash前两位>/<SHA256>`；不重新编码、不替换换行。HTTP 库完成传输解压后的响应实体字节是本系统原件边界，不声称保存 TLS 或压缩网络报文。

## 物理表：sources

| 字段 | 类型 | 含义 |
|---|---|---|
| source | TEXT PK | 来源稳定名称；与记录、获取流水关联 |
| metadata_json | JSON TEXT | 来源目录元数据，可含接入方式、授权说明、覆盖、限制；不是原件的历史权利凭证 |
| updated_at | UTC 时间 | 来源目录最后更新时间 |

此表是可更新的运行目录。原始获取当时的元数据保存在不可变的 `raw_retrievals`；来源条款更新不覆盖已保存的获取流水。

## 物理表：raw_blobs

| 字段 | 类型 | 含义 |
|---|---|---|
| raw_id | TEXT PK | `sha256:` 加摘要；内容地址 |
| sha256 | TEXT UNIQUE | 原始字节 SHA-256，十六进制 |
| path | TEXT | 相对 Store 根目录路径，便于移动整个库 |
| byte_size | INTEGER | 字节数 |
| media_type | TEXT | 首次获取的 MIME 类型；各次获取差异见流水元数据 |
| created_at | UTC 时间 | 该内容首次入库所用的获取时间 |

相同字节只存一份；每次成功获取仍产生独立流水。文件完成写入后以原子硬链接发布，支持同时获取同一内容；数据目录须位于支持硬链接的文件系统（本机 Windows NTFS 已实测）。SQLite 触发器拒绝修改或删除此表。磁盘文件不是 WORM 介质，重用缓存和核验命令须验证摘要；有权限的人员仍可能修改文件。

## 物理表：raw_retrievals

| 字段 | 类型 | 含义 |
|---|---|---|
| retrieval_id | UUID TEXT PK | 单次成功获取标识 |
| raw_id | TEXT FK | 指向 raw_blobs |
| source | TEXT FK | 指向 sources |
| url | TEXT | 获取 URL / 文件 URI；HTTP 查询凭证经过脱敏 |
| retrieved_at | UTC 时间 | 实际获取时间，历史下载不倒填 |
| metadata_json | JSON TEXT | HTTP 状态、ETag、Last-Modified、最终 URL、重试次序；或人工授权说明、导入清单 raw_id；或生成库版本 |

失败响应进入质量日志，不作为成功获取流水。缓存命中沿用实际获取时间，不制造新下载。表由触发器保护为只追加。

## 物理表：records（所有业务逻辑表共用）

| 字段 | 类型 / 单位 | 定义与关联 |
|---|---|---|
| sequence | INTEGER PK | 入库序号，仅作稳定排序，不能当公开时间 |
| record_id | TEXT UNIQUE | 规范化语义与前次版本 ID 组成的 SHA-256 前加 `rec:`；保留 A→B→A 回退事件 |
| dataset | TEXT | 下列逻辑数据集名称 |
| record_key | TEXT | 来源内的业务键；版本间不变，不能简单只用股票代码 |
| data_json | JSON TEXT | 业务载荷，字段定义见后文；JSON 禁止 NaN/Infinity |
| semantic_hash | SHA-256 | 业务内容、身份、公开时间、口径、质量及解析版本的语义摘要 |
| source | TEXT FK | 来源名称；多来源不自动择一或合并 |
| raw_id | TEXT FK | 此版本首次入库引用的原始内容 |
| retrieval_id | UUID TEXT FK | 此版本入库引用的获取流水 |
| company_id | TEXT / null | 稳定公司 ID |
| security_id | TEXT / null | 稳定证券 ID |
| published_at | ISO 日期/时间 / null | 来源声称的发布时间，或证据明确的接收时间；类型由 basis/载荷说明，不承诺均为实时传播时刻 |
| publication_precision | TEXT | `second`、`day`、`unknown` 等；日期不得伪装成精确时钟 |
| publication_timezone | TEXT / null | 原始时区，如 America/New_York |
| available_at | UTC 时间 / null | 可用于公开时点查询的最早时间；须显式证据或保守假设；不使用报告期末 |
| availability_basis | TEXT | 可用时间依据，如 SEC 次日保守假设、ALFRED vintage 次日、当前网页首次观察；未知则 unknown |
| retrieved_at | UTC 时间 | 该版本的实际证据获取时间，从流水继承 |
| period_start | ISO 日期 / null | 被描述会计/观测期间开始日 |
| period_end | ISO 日期 / null | 被描述期间结束日或余额日；不是可用时间 |
| event_date | ISO 日期 / null | 交易日、除权日、公告日期等，按数据集解释 |
| source_locator | TEXT / null | JSON Pointer、CSV 行列、网页段落、表格/页码或原件范围 |
| unit | TEXT / null | 来源单位，如 USD、USD/shares、shares、百分比；组合行情单位须明确 |
| currency | ISO 币种 / null | USD 等；非金额留空 |
| quality | TEXT | unverified、missing、manual_import_unverified、derived_conditional 等；不是固定真假开关 |
| knowledge_type | TEXT | 五类信息性质，见基础约定 |
| parser_version | TEXT | 解析器/计算程序版本 |
| supersedes | TEXT FK / null | 同 dataset/source/key 的前次入库版本；自动链反映数据版本变更，不自行证明财报法律更正或公开时序 |
| created_at | UTC 时间 | 规范化记录入库时间；区别于原件获取和发布 |

`records` 禁止 UPDATE/DELETE。与最近版本语义相同的重复处理复用 record_id，并追加证据连接；金额、时间、口径、解析器等变更保留新版本。未知时点或当前快照的 A→B→A 回退保存第三个版本。明确带发布时点/精度及可用依据的较早历史版本，若完整语义已存，则视为档案回扫并复用；ALFRED 两轮 A(t1)、B(t2) 全量扫描保持两版。此例外不适用于 unknown、first_observed、current_copy、snapshot 等依据。并发写入以 SQLite 写锁保护版本判断；时钟精度相同的变更以入库序号破同分。不同 SEC accession 属于独立披露上下文，不随意声称更正了哪一份原报表。

## 其余物理表

| 表 | 字段 | 含义 |
|---|---|---|
| record_evidence | record_id FK | 规范化记录 |
| record_evidence | retrieval_id FK | 每次支持该记录的成功获取；与 record_id 组成复合主键 |
| record_evidence | raw_id FK | 对应原件；允许同语义数字在多份原件中被重复证实 |
| quality_issues | issue_id UUID PK | 一条质量/失败/缺口事件 |
| quality_issues | code TEXT | 程序可识别问题代码 |
| quality_issues | message TEXT | 人可读说明，不写接口凭证 |
| quality_issues | dataset TEXT/null | 涉及逻辑表 |
| quality_issues | company_id TEXT/null | 涉及公司 |
| quality_issues | security_id TEXT/null | 涉及证券 |
| quality_issues | severity TEXT | warning/error 等严重度 |
| quality_issues | details_json JSON | URL、记录 ID、异常类型、范围等细节 |
| quality_issues | created_at UTC | 检查/失败发生时间 |
| checkpoints | key TEXT PK | 任务、分页、首次观察等状态键 |
| checkpoints | value_json JSON | 下页 token、offset、完成状态或首次观察时间 |
| checkpoints | updated_at UTC | 状态更新时间 |

checkpoints 是可变运行状态，不是历史研究证据。失败日志重复出现是不同运行事件，不算业务记录重复。

## 查询结果额外字段及防前视规则

`Store.query` 将 `data_json` 解码为 `data`，追加 `effective_available_at` 与 `point_in_time_status`。其余字段保留。

- `as_of` 过滤信息可公开使用时间；未知可用时间的记录排除。网页标注旧日期不足以证明今天这个副本当年存在，因此当前可变网页资料从首次获取才可见。
- `known_at` 过滤本系统实际已取得的证据。单独使用 as_of 表示今天基于保存证据重建公共状态；同时使用 known_at 才回答系统当时已持有哪些证据。
- 同一个来源/业务键多版本声明完全相同公开时间，缺乏版本时间证据：`effective_available_at=max(available_at,retrieved_at)`，状态 `acquisition_only_ambiguous_revision`。历史查询在获取前关闭这些不确定版本；首次与再次获取之间仍可读首次版本。
- 若历史 known_at 尚未包括后来的歧义版本，返回当时已有版本，忠实复现当时知识。后来的矛盾证据可让今天的历史重建更保守。
- 普通记录状态 `source_evidenced` 只表示具有调用者提供的时间依据，其中仍可能是明示的保守假设；未知为 `unknown_public_time`。应同时检查 availability_basis 和 quality。
- 默认 latest 在每个 `(dataset,source,record_key)` 内按有效可用时间、获取时间、序号选择。`latest=False` 保留全部符合条件版本。不同来源的冲突同时返回，不悄悄选赢家。
- 没有获取过的历史版本无法恢复。Companyfacts 当前 API 的原始历史表现、网页修改历史、日历紧急休市历史通知不能由当前下载自动认证。

## 业务载荷：身份与日历

以下业务字段未特别写单位时，文本/布尔/日期按列名解释。未连通的数据集列的是导入约定，不代表已经采集。

| 数据集及键 | 载荷字段 | 说明 |
|---|---|---|
| company_profiles；company_id | cik, name, sic, sic_description, entity_type | SEC 发行人代码、名称、SIC 行业分类、机构类型 |
| 同上 | fiscal_year_end, state_of_incorporation, website, investor_website | MMDD 财年截止、注册地、官网、IR 站 |
| 同上 | current_tickers, current_exchanges | 当前代码及交易所数组，不当作历史主档 |
| 同上 | snapshot_only, history_complete, temporal_caveat | 快照标记、历史完整度及限制 |
| securities；security_id | company_id, security_id, configured_symbol, symbol, name | 显式公司证券对应，配置代码与已由来源证实代码分别保存 |
| 同上 | exchange, configured_exchange, currency, security_type | 来源交易所、配置交易所、币种及证券类型；配置值不冒充独立验证 |
| 同上 | classification_basis, current_symbol_confirmed, security_mapping_basis, snapshot_only | 分类和身份依据 |
| 同上 | listing_date, delisting_date, shares_outstanding | 上市/退市日、股本（股）；未知为空，股本需另有观测日期 |
| identity_history；主体+类型+有效期 | identity_type, name, symbol, old_name, new_name, old_symbol, new_symbol | 公司名/证券代码变更；字段按已保存证据选用 |
| 同上 | valid_from_reported, valid_to_reported, valid_from, valid_to, effective_date | 来源报告的有效期及明确生效日，未知不补造；valid_to 约定应在导入说明写明开/闭边界 |
| 同上 | validity_basis, original_publication_unknown | SEC formerNames 不证明原始公告时刻 |
| calendar_sessions；exchange+date | exchange, date, is_session | 交易所、日期、是否开市；休市日也保存 |
| 同上 | open, close, break_start, break_end | UTC 时段时间，休市为空；不是价格字段 |
| 同上 | timezone, calendar_package, calendar_version, coverage | 交易所时区、exchange_calendars 版本及常规时段覆盖 |
| 同上 | historical_announcement_time, security_specific_halts_included | 历史公告时刻通常未知，个股停牌不包含 |
| security_status；security_id+event ID | status, reason, start_at, end_at, restrictions, venue | 停/复牌、限制类型、起止时刻和场所；须外部证据导入 |
| market_rules；market+rule ID+effective_from | market, exchange, rule_id, rule_text, effective_from, effective_to, trading_hours, lot_size, tick_size, price_limits, settlement_cycle | 生效期内规则；缺少规则不可按另一市场套用；暂为导入约定 |

## 业务载荷：行情与公司行为

| 数据集 | 载荷字段 | 单位/说明 |
|---|---|---|
| daily_bars / benchmark_bars | symbol, date / session_date, bar_timestamp | 配置代码、本地交易日及提供商时间戳；以稳定 security_id 关联 |
| 同上 | open, high, low, close | 原始未复权、每股计价币种金额 |
| 同上 | volume, volume_unit | 成交股数；不得把手当股 |
| 同上 | vwap, trade_count | 来源加权均价、符合来源交易条件的笔数 |
| 同上 | turnover, turnover_missing_reason | 精确成交额；Alpaca bar 无该字段时留空，不能用 VWAP×volume 冒充精确成交额 |
| 同上 | feed, adjustment, price_unit, session, session_timezone, coverage | IEX/SIP 等覆盖与交易聚合规则；不混合成所谓全市场行情 |
| 同上 | return_basis, symbol_mapping, historical_revision_timestamp | 价格/分红口径、代码映射是否禁用、该返回版本历史修订时间（未知可空） |
| corporate_actions | action_type, action_id / id | split / cash_dividend 等事件类型及来源 ID；Alpaca 原始 vendor 类型保留，复杂类型不会自动用于复权 |
| 同上 | announcement_date, declaration_date, ex_date, record_date, payment_date, effective_date, process_date | 公告、宣告、除权、登记、支付、生效、来源处理日分别保存；process_date 不是公告日 |
| 同上 | split_ratio, new_shares, old_shares | 拆并股比率=new/old，反向拆股可小于 1 |
| 同上 | cash_amount / amount, currency | 每股现金分红及币种；按来源单位，非总派现金额 |
| 同上 | previous_session_date, dividend_share_basis | 现金复权必须明确前一交易日；同日拆股分红必须明确 post_split 每股口径 |
| 同上 | vendor_group, query_date_basis, announcement_time_verified, date_precision, source_creation_time_guaranteed | 来源原始分组、接口日期筛选口径及时间证据等级 |
| 同上 | vendor_action_type, new_rate, old_rate, split_ratio_basis, normalization_status | Alpaca 原始类型/比率保留；仅合法正数 forward_splits/reverse_splits 转为 split 及十进制字符串 new/old；未知类型原样保留并拒绝复权 |
| 同上 | rate, cash_amount_unit | Alpaca cash_dividends 的 rate 原值与每股现金口径；缺币种或 previous_session_date 不推测 |
| 同上 | rights_ratio, subscription_price, consideration, successor_security_id, disposal_terms | 送配股/并购/分拆/退市处置扩展字段，需要人工核实，当前简单复权算法拒绝处理 |
| adjusted_bars | 原始行情字段 | 新逻辑数据集保存派生 OHLC 和 split-adjusted volume，原始 daily_bars 不修改 |
| 同上 | price_factor, volume_factor | 截至指定时点的向后乘法因子；拆股价格乘 old/new，成交股数乘 new/old |
| 同上 | raw_record_id, corporate_action_record_ids | 输入行情和应用公司行为的记录 ID |
| 同上 | adjustment_method, calculation_version | split 或 total_return；算法版本 backward-adjustment/1.0 |
| 同上 | adjustment_as_of, adjustment_reference_date | 明确查询时间及参考日期 |
| 同上 | action_source | 用户显式选择的公司行为来源；未选择时同日同类型多来源事件拒绝计算 |
| 同上 | amount_adjustment, completeness | 成交额保持原始；公司行为完整性不由算术函数认证 |

相同最新输入清单、输出和算法版本重复计算，复用首次计算可用时间及规范化记录；保留独立运行获取流水。输入或输出改变后再次回退仍是新版本。没有符合时点的原始行情时返回 no_data，不生成空的派生原件。

现金分红向后因子为 `1-D/P_previous`；同日拆股时先把前收盘转换至拆股后股数口径。事件必须显式提供 ex_date，支付日、process_date、effective_date 或通用 event_date 不能自动顶替。缺币种、币种变化、未知可用时间、缺前一交易日证据、分红大于等于参考价、复杂事件拒绝计算。同日同类型多来源记录先要求选定 action_source，避免 IR 与数据商各报一次十拆一被错误连乘为百拆一。`total_return` 是常见现金分红价格调整口径，不是含税、资金再投资执行、交易成本、FX、退市处置的组合收益模拟。没有公司行为记录不等于已证实期间没有公司行为。

## 业务载荷：财务、全文与事件

| 数据集 | 载荷字段 | 单位/说明 |
|---|---|---|
| financial_facts | taxonomy, concept, label, description | XBRL 分类、标签及来源定义；覆盖利润、资产负债、现金流、股本等所有 API 返回标准概念 |
| 同上 | value, unit, scale, scale_basis, currency | 原始数字、单位、倍率依据与币种；不混合美元/百万美元或股/每股 |
| 同上 | accession, form, filed | SEC 披露编号、表单、披露日期 |
| 同上 | start, end, fy, fp, frame | 精确期间与 SEC 上下文；fp=Q2 不意味着三个月单季数据 |
| 同上 | period_type, duration_days, quarter_or_cumulative | instant/duration、天数、累计或单季限制说明 |
| 同上 | consolidation_scope, accounting_basis | SEC entire_filing_entity，US-GAAP / IFRS / taxonomy_specific |
| 同上 | is_amendment, amends_accession, revision_link_basis | 识别 /A；未验证被更正 accession 时保持空 |
| 同上 | time_evidence_accession, time_evidence_location, source_fact | 按 accession 连接 filings/documents 原文受理时间；财务事实保留稳定的 filing day 日精度时间及完整来源观测对象，避免新增header造成虚假数值修订 |
| filings；accession | accession, form, filing_date, report_date | 公告编号/类别/披露日/报告日 |
| 同上 | acceptance_datetime_raw, accepted_local_clock_header | API 原始时钟与文件头本地接收时钟；均不冒充已观察的传播秒点 |
| 同上 | items, primary_document, primary_url, is_xbrl, is_inline_xbrl | 8-K 事项、主文件名/URL、XBRL 标志 |
| 同上 | is_amendment, amends_accession, revision_relation | 更正识别及关联限制 |
| 同上 | document_record_ids, original_downloaded, time_evidence_raw_id, time_caveat | 全文连接、是否获取、时间证据及限制 |
| documents | title, document_type, document_kind, url, content_sha256 | 原文类型、标题、位置与摘要；文件内容通过 raw_id 读取 |
| 同上 | historical_version_verified, original_published_date, licensing_status | 当前副本是否有历史版本证明、页面旧日期、使用权状态 |
| 同上 | accession, filename, form, document_type, description | SEC 文件专属属性 |
| 同上 | accepted_local_clock, document_manifest, publication_time_kind | SEC 文件头时钟、附件目录（type/filename/description/sequence）及时间含义 |
| 同上 | full_text_preserved, structured_guidance_extracted, links_images_downloaded, time_evidence_raw_id | 全文字节保留；链接图片/结构化指引未完成不能假称完整抓取 |
| guidance | metric, period_start, period_end, low, high, point, currency, unit, accounting_basis, guidance_status, prior_guidance_record_id, conditions | 公司收入/利润/EPS 等指引；保留区间、条件、撤回/修订与前版，独立于分析师预测及实际数 |
| analyst_estimates | metric, target_period, analyst, broker, estimate, consensus_mean, consensus_median, dispersion, contributor_count, estimate_timestamp, revision_of, accounting_basis, currency, unit | 预测历史时点与参与者；无授权历史库则为空，禁止用实际数/公司指引替代 |
| earnings_actuals | metric, target_period, value, currency, unit, accounting_basis, reported_or_adjusted, filing_record_id | 已公布实际业绩与报表出处；与财务事实可关联但不凭空生成 |
| event_calendar | event_type, event_date, status, form, accession | 事件预期/实际分开；SEC 采集产生 actual_sec_filing 事件 |
| 同上 | expected_earnings_date, actual_earnings_date, is_earnings_announcement_date, prior_event_record_id | 预计及实际财报日和修订；SEC filing date 不自动充当业绩公布日 |

财務事实业务键包含公司、taxonomy、concept、unit、accession、start、end、fy、fp、frame，因此保留比较期及不同披露上下文。各 accession 的比较期数字可能不同，通用 latest 不跨 accession 强行合并。消费端必须先固定会计口径、期间、标签及历史 as_of，再审查可比披露上下文。

Companyfacts 不覆盖全部自定义标签、分部维度与附注明细。原始 10-K/10-Q/附件用于后续专门解析；保存原件不等于全部财务附注已结构化。

## 业务载荷：研究扩展

| 数据集 | 载荷字段 | 定义/约束 |
|---|---|---|
| relations | relation_type, subject_company_id, object_company_id, product, valid_from, valid_to, confidence, confirmation_status, evidence_quote | 竞争/客户/供应商/产品关系；必须有证据位置、适用期；系统推断单独分类 |
| industry_metrics | metric, value, unit, geography, product, industry, period_start, period_end, methodology, sample_scope | 价格、销量、库存、产能、订单、交期等；指标语义按行业明确 |
| macro_observations | series_id, observation_date, value, value_type, units | 宏观系列、观察日期、十进制字符串值及来源单位；`.` 缺失变 null |
| 同上 | frequency, seasonal_adjustment, realtime_start | 频率、季调、ALFRED vintage 起始日 |
| 同上 | metadata_as_of, realtime_end_location, availability_assumption | 系列元数据为当前快照；vintage end 在原件保留避免查询窗口截断被误当最终修订结束日；次日可用假设 |
| 同上 | missing_reason, unit_basis, vintage_status, historical_revision_timestamp | 缺失原因、配置单位未独立核对、公开 CSV 仅当前快照、历史修订时间未知 |
| policy_documents | title, issuing_body, document_number, jurisdiction, publication_date, effective_from, effective_to, revision_of, status | 政策全文与修订、生效区间；原件经 raw_id 保留 |
| 同上 Federal Register 接口 | abstract, document_type, effective_date, agencies, citation, html_url, pdf_url, body_html_url | 摘要、法规类型、生效日期原字段、机构数组、引注、不同原件入口 |
| 同上 | correction_of, correction_relationship_status, metadata_raw_id, full_text_raw_id, full_text_sha256, full_text_status | 更正关联（未验证保持未知）、元数据与全文字节的独立证据连接、全文获取状态 |
| 同上 | historical_version_time_verified, industry_relevance, legal_edition | 当前 HTML 信息版不冒充当时正式版本或正式 PDF；行业适用性需复核 |
| news | title, publisher, original_url, published_at_source, updated_at_source, event_id, canonical_article_id, reprint_of, content_hash | 原始报道、更新时间、转载和事件去重键；当前未完成自动新闻聚类，人工字段不能冒充已验证 |
| 同上 RSS/Atom 接口 | canonical_url, content, source_name, source_url, content_sha256, source_published_at, publication_precision, publication_timezone | 标准化 URL、feed 条目文本及摘要哈希、来源信息、原始发布时间和精度 |
| 同上 | updated_at, updated_precision, updated_timezone, content_scope, article_full_text_retrieved | feed 更新时间及精度；content_scope=feed_entry_only，不冒充已获取正文 |
| 同上 | event_cluster_id, event_cluster_status, historical_version_time_verified, duplicate_detection | 聚类 ID 未完成可为空/needs_review；重复检测方法及历史版本未验证标识 |
| research_materials | title, author, material_type, observation_date, rights, authorization_note, sample_scope, methodology, method, input_record_ids, model, prompt_hash, extraction_confidence | 授权研究/专家/调查/另类数据/人工知识；调查必须样本和方法，推断必须方法和输入；LLM 字段用于追溯，不补造数字 |

公开网页清单产出的各业务载荷额外包含 `extraction_method=curated_manifest_with_regex_evidence_guard` 和 `historical_version_verified=false`，说明人工清单提取经过原文关键词检查但没有历史副本认证。

人工导入载荷额外包含 `import_manifest_raw_id`（完整 JSONL 原件）和 `availability_evidence`（时间证据文字）。导入清单字段 `rights`、`classification`、`authorization_note` 保存于清单及获取流水；每条必须声明 public 或 authorized，且分类为 public_or_authorized_nonconfidential。专家意见与调查估计不能伪装成事实观测。

## 空值与覆盖声明

证券主档、历史停复牌、完整退市证券池、精确成交额、复杂公司行为、历史一致预期、新闻修订、行业供应链和获授权另类材料不会因为定义了字段就变成已覆盖。缺少历史一致预期时必须明确：**无法研究相对市场预期的业绩差异**。可以在资料与时间证据充分时单独检验实际业绩相对公司此前指引的差异。

本系统样本是链路与边界验证集，不是无偏历史股票池。没有可验证的历史身份与版本时间时，无法可靠重建当时可见状态。
