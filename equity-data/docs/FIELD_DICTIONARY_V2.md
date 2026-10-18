# V2 统一字段字典与时间语义

> V3 补充：`known_at` 限制原文及支持证据的获取状态；要重现当时已经解析入库的字段，还必须提供 `processed_at`（CLI `--processed-at`）。该参数同时限制 records.created_at、获取证据、时间约束及核验决定，避免今天重新解析旧原文后把新解析结果带回过去。历史原文隔离重算结果见 machine_reprocess_audits，不直接进入当前事实。数据合同与候选/事实层次见 DATA_CONTRACTS_V3.md。

本文件同时涵盖保留的 v1 来源标准化字段和 V2 任务/通用解析字段。业务载荷按 `parser_version`、`extraction_method` 和 `document_kind` 区分，不能要求每条记录同时具有所有来源字段；以下未实现的供应商/授权导入字段只是接口约定，绝非真实覆盖承诺。V2 专属字段与说明优先于旧来源载荷约定。

`data_json` 允许来源扩展字段。未逐项列出的公司自定义 QName、SEC 原始 `source_fact`、Federal Register 原始详情键、BLS `source_item/catalog`，按照“原字段名、原值、原来源/原件、解析版本”原样保留；不把这些不固定字段冒称统一可比较标准。原文动态对象本身也是字典的一部分，必须结合原件定位查看。

实现使用 SQLite 的证据表与统一版本表。下面的业务数据集各有同名只读 SQL 视图，均为 `SELECT * FROM records WHERE dataset=...`，业务字段保存在 `data_json`；视图不是独立物理表。视图展示所有版本，**不自动作历史时间过滤**；研究查询应使用 `Store.query(as_of=..., known_at=...)`。空视图不表示已覆盖。可连接接口、接口已实测、真实数据已保存是不同状态，实际状态见覆盖报告。不存在的业务字段保持 `null` 或未提供，并记录缺失原因，不能用零填补。

## 基础约定

- `company_id` 标识发行公司，如 `sec:0000320193`；SEC CIK 不等于证券或股类 ID。
- `security_id` 标识明确的证券或股类，如 `local:us:aapl-common`。本地 ID 来自显式配置，不宣称全球证券主档已验证。代码不是 ID；更名、代码变更及退市不能另造或删除原证券身份。
- 宏观、政策、交易所日历等不属于某个公司的资料，两个 ID 可空。公司级财务不擅自绑定某个普通股证券。V2 默认证券 ID 是 CIK+当前代码的本地替代标识，跨代码连续性未知；显式 share_class_id/security_id 另行保留配置证据。
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

Companyfacts 不覆盖全部自定义标签、分部维度与附注明细。V2 另对原始 inline XBRL 解析自定义 QName/context/dimension；叙述候选与严格指引规则见后文。保存原件仍不等于全部财务附注已结构化。

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

V2 已关闭预填答案自动入库。含 `records.data` 的旧公开网页清单产生 `PREFILLED_MANIFEST_REJECTED`；旧库仅留作 legacy 证据，不计入 V2 自动采集准确率。自动解析载荷使用通用 parser，字段定义见本文件后半部分。

人工导入载荷额外包含 `import_manifest_raw_id`（完整 JSONL 原件）和 `availability_evidence`（时间证据文字）。导入清单字段 `rights`、`classification`、`authorization_note` 保存于清单及获取流水；每条必须声明 public 或 authorized，且分类为 public_or_authorized_nonconfidential。专家意见与调查估计不能伪装成事实观测。

## 空值与覆盖声明

证券主档、历史停复牌、完整退市证券池、精确成交额、复杂公司行为、历史一致预期、新闻修订、行业供应链和获授权另类材料不会因为定义了字段就变成已覆盖。缺少历史一致预期时必须明确：**无法研究相对市场预期的业绩差异**。可以在资料与时间证据充分时单独检验实际业绩相对公司此前指引的差异。

本系统样本是链路与边界验证集，不是无偏历史股票池。没有可验证的历史身份与版本时间时，无法可靠重建当时可见状态。

## V2：只追加的公开时间限制证据

`availability_constraints` 不修改原始记录。若同一 immutable accession 的后续文件头证明原来的可用时间下限太早，就追加约束；这不是给旧记录倒填系统获取时间。

| 字段 | 类型 | 含义 |
|---|---|---|
| constraint_id | TEXT PK | dataset/source/key/限制时刻及原件证据的稳定标识 |
| dataset | TEXT | 被限制的逻辑数据集 |
| source | TEXT | 被限制的来源 |
| record_key | TEXT | 同一业务事实/档案的键；与 dataset/source 共同关联 records |
| not_before | UTC 时间 | 新证据要求的信息可用时间下限 |
| learned_at | UTC 时间 | 获得这份限制证据的实际时间，取原件获取时刻 |
| raw_id | TEXT FK | 支持该限制的原始文件，连接 raw_blobs |
| basis | TEXT | 限制理由，例如更晚 SEC header acceptance 所要求的保守下限 |

查询同时考虑 `available_at`、同秒修订歧义以及这些限制的最大 not_before。带 `known_at` 的查询只采用 learned_at 不晚于该 cutoff 的限制，避免把后来知道的证据偷偷塞进“系统当时知道什么”。结果可额外带 `availability_constraints`。约束表受 UPDATE/DELETE 禁止触发器保护。

跨文档事实还需要 `data.supporting_raw_ids` 中每份原件在该记录的 record_evidence 中有连接。缺支持证据时历史 as_of/known_at 查询排除该事实；known_at 早于任一支持文件实际获取时刻也排除。结果额外的 `required_evidence_available_from` 是主原件与支持原件实际获取时刻的最大值，`required_evidence_complete` 表明依赖是否全部已连接。单独 as_of 可表示今天基于已取得全部档案重建公开状态，同时传 known_at 才表示系统当时确已掌握这些完整证据。

## V2：来源注册表和发现载荷

`configs/source_registry.json` 为来源规范，`sources.metadata_json` 保留注册版本的当前运行副本；真实健康证据在 machine_source_health/原件/验收报告中。

| 字段 | 定义 |
|---|---|
| schema_version、reviewed_at、note | 注册表结构版本、审查日期与状态解释 |
| source_id、name | 稳定来源键与显示名称 |
| markets、security_coverage、data_types | 声明市场、证券范围、数据类型；不是实际覆盖行数 |
| adapter | Python `模块:类`；没有实现时只实例化依赖边界 |
| documentation、access | 官方文档地址、API/文件/RSS/网页/人工等接入方式 |
| authentication_authorization_cost | 账户、授权、费用与使用条件文字；凭证不在这里保存 |
| historical_coverage、delisted_coverage、history_versions | 历史起点/覆盖限制、退市能力与版本能力 |
| updates_latency_revisions、publication_time_precision | 更新频率/延迟/修订机制、公开时刻精度 |
| status | candidate/implemented/mock_tested/connected/verified/missing_dependency 等状态；不得互相替代 |
| failure_and_alternatives、dependency_reason | 失败处理、替代来源及尚缺依赖 |

| 数据集 | 字段 | 含义 |
|---|---|---|
| securities | share_class_id | 配置提供的股类标识；未独立核验不宣称证券全球 ID |
| securities | ticker_valid_from、ticker_valid_to、historical_membership_verified | 代码有效期/历史池证据；SEC 当前目录通常 null/false |
| securities | security_mapping_basis、missing_reason、evidence_excerpt | 本地 surrogate 或显式 ID 的依据、缺口、对应源行 |
| source_candidates | url、candidate_type | 待访问来源地址、official_ir/official_company_website 或 candidate 分类 |
| source_candidates | verified_official_by、authority_basis、candidate_only | 哪份公司声明/SEC profile 提供该链接；不表示当前域名所有权已独立核验 |
| source_candidates | current_snapshot_only、evidence_excerpt、source_document_record_id | 当前来源快照、原文摘录、关联文档版本 |
| universe_snapshots | 视图接口；暂无独立自动业务载荷承诺 | 当前池证据实际保存于目录原件、securities、配置原件及运行 scope；空视图不得解释为完整历史池 |

来源候选存在不等于源已接通，必须找到对应资源获取流水。股票池 offset/limit 属于显式资源控制：省略的公司清单进入缺口，不能把入选子集说成完整市场。

## V2：统一自动解析结果

适用于 parser 输出的 financial_facts、guidance、relations、operating_metrics、commitments、debt_maturities、risk_disclosures。外层共用 records 全部字段。

| 字段 | 类型/单位 | 含义 |
|---|---|---|
| evidence_excerpt | TEXT | 同一已保存原文版本中的文本摘录；数值和所用期间/单位必须可定位 |
| content_sha256、source_content_sha256 | SHA-256 | parser 输入字节哈希、入库器确认的来源字节哈希；都不是文章标题摘要 |
| parser_version | TEXT | 通用解析实现版本，当前 `generic-document-v2.0.3` |
| source_document_type | TEXT/null | 来源表单/文档类型；不推断发布精度 |
| resource_key | TEXT | durable inventory 的来源内键 |
| extraction_mode | TEXT | `automatic`；授权人工导入不使用自动成功状态 |
| source_url | TEXT/null | 原始资料地址 |
| original_published_at、source_revision_at | ISO 日期/时间/null | 来源声称的发布和更新时刻；是否有充分版本证据由外层 availability_basis 说明 |
| extraction_method | TEXT | inline_xbrl_local_no_taxonomy_network、explicit_guidance_span_rules 或 narrative_evidence_candidate |

### financial_facts：inline XBRL 数字

此载荷与 Companyfacts 的 taxonomy/concept/source_fact 载荷独立。默认不合并来自两种结构的同名值。

| 字段 | 类型/单位 | 含义 |
|---|---|---|
| concept_qname、original_metric_name | TEXT | 大小写保留的来源 QName，例如 `issuer:CustomMetric` |
| concept_namespace | URI/null | 可从当前文档解析出的 QName 命名空间；不联网补 taxonomy |
| value | 十进制字符串/null | 解析后已经应用来源 scale/sign 的数值；nil 保持 null |
| value_status | TEXT | parsed 或 source_nil |
| context_id、unit_id | TEXT | 原件中 contextRef/unitRef 的 ID |
| context_locator、unit_locator | XPath TEXT | 原 context/unit 在同一字节版本中的位置 |
| context_evidence、unit_evidence | OBJECT | 各含 raw_id/sha256/source_url/source_locator；可明确指向同 accession 的另一份已保存原文 |
| supporting_raw_ids | ARRAY | 实际使用的跨文档 context/unit 依赖原件；通过 record_evidence 另行关联，并非随意附上全库文件 |
| entity | OBJECT/null | identifier、scheme；来源 context 中的主体标识 |
| period_type | TEXT | instant 或 duration，日期保存在外层 period_start/period_end |
| dimensions | ARRAY | 每项 axis/member/type；显式和 typed 维度分开保存 |
| dimensions[].axis、member、type | TEXT | 来源维度 QName、成员 QName/文本、explicit/typed；不按名字猜业务/地区类别 |
| dimensions[].typed_xml | XML TEXT | typed 维度子树原表示；仅 typed 成员有此字段 |
| unit_definition | OBJECT | label、numerator、denominator、currency |
| unit_definition.label | TEXT | 原 measure 或组合单位，例如 `iso4217:USD/xbrli:shares` |
| unit_definition.numerator、denominator | ARRAY | 原件分子/分母 measure；简单单位 denominator=[] |
| unit_definition.currency | ISO/null | 明确 iso4217 measure 才能识别币种 |
| scale | INTEGER | 原 ix scale 指数；value 已应用 10^scale，消费端不得再次乘 |
| sign | TEXT/null | 原 ix sign，`-` 表示取负；不是财务展示符号猜测 |
| decimals、precision | TEXT/null | 源准确度属性；不是自动 rounding/calculation 校验通过标志 |
| format | QName/null | 原 inline transformation 名；仅支持明确列出的本地转换子集 |
| original_value_text | TEXT | 原标签文本，便于对账；数值拼接排除 ix:exclude |
| evidence_excerpt_kind | TEXT/未提供 | 空 nil/fixed-zero 的原始标签证据为 original_tag_markup；非空标签默认可见原文文本 |
| is_custom_concept | BOOLEAN | 按来源概念前缀标识扩展标签候选；语义仍以 QName/命名空间为准 |
| comparable_metric | null | 通用解析器不做无证据的跨公司指标映射 |
| definition | null | 当前未加载 taxonomy 的定义 |
| definition_status | TEXT | taxonomy_not_loaded |

同语义 context/unit 的冲突值带 `quality=source_conflict_requires_review`，问题为 IXBRL_CONFLICTING_FACTS。尚未验证报告舍入误差的一致性，不能自动选某一个值。

context_documents 仅由适配器传入同一 accession 已保存的支持文档；parser 校验 content/raw_id/sha256 一致。跨文件同 ID 定义相同可复用，定义冲突拒绝。支持原文的期间/单位变更也会改变数字记录语义及依赖原件，不会无告警沿用旧 context。覆盖结果 inline_xbrl.context_documents_supplied 为支持文档数，supporting_raw_ids 为实际使用的原件集合。

### guidance：自动指引

| 字段 | 类型/单位 | 定义 |
|---|---|---|
| original_metric_name | TEXT | 本段实际披露的指标标签，保留 GAAP/non-GAAP 等原前缀 |
| comparable_metric、definition | null | 没有可靠统一语义映射/定义时为空 |
| target_period | OBJECT | label/year/fiscal_year/period_end/calendar_mapping |
| target_period.label | TEXT | 原文目标期间，例如 Q2 fiscal 2028 |
| target_period.year | INTEGER | 目标标签里的年份；不单独作为会计期间结束日 |
| target_period.fiscal_year | INTEGER/null | 明确 fiscal/FY 时的财年；其余不猜 |
| target_period.period_end | ISO 日期/null | 原文明确 quarter/year ending 日期才赋值 |
| target_period.calendar_mapping | TEXT | explicit_date 或 unknown_fiscal_calendar |
| point、low、high | 十进制字符串/null | 点值或明确区间；point±tolerance 不擅自填为源披露上下界 |
| tolerance | OBJECT/null | value/unit/basis；相对 percent 或绝对 basis_points/金额 |
| tolerance.value | 十进制字符串 | 容差原数值；明确百万/十亿金额按倍率转为基础货币单位 |
| tolerance.unit | TEXT | percent、basis_points、明确金额单位 |
| tolerance.basis | TEXT | relative_to_point 或 absolute |
| value_qualifier | TEXT | 原文约束，如 approximately、at least；未出现为 stated |
| source_amounts | ARRAY | 每项 value/unit/currency/source_scale/source_text，记录规则实际使用的金额证据 |
| conditions_text | TEXT | 原片段全句，保留未结构化条件，不把条件删除后假称无条件预测 |
| dimensions | ARRAY | 当前通常空；不能凭行业想象补造产品或地域 |
| accounting_basis | TEXT | 明确 GAAP、non-GAAP 或 unspecified |
| automatic | BOOLEAN | 此入口的自动提取标记；不适用于旧预填清单和人工导入 |
| unit、currency | TEXT/null | USD/EUR/GBP、货币/share、percent；只有 `$` 时 dollar_symbol 且 currency=null |
| definition_status | TEXT | source_label_only，不宣称该指标已跨公司标准化 |

GUIDANCE_REJECTED 或 GUIDANCE_METRIC_UNSUPPORTED 保留源片段和原因，不回退到旧配置值。只找到关键词不能被计作字段解析成功。

v2.0.3 的 `GUIDANCE_TABLE_CONTEXT_UNSUPPORTED` 表示表格指引未获得可靠的表头单位/倍率、分部维度、列关联和限定条件联合解释，**不输出数值**。其 issue.details 包含 `evidence_excerpt`（完整表格，可含外部 Outlook 标题）、`row_locator`、`row_excerpt`、`required_adapter=evidenced_table_guidance`，issue.source_locator 指向原表格/标题。此缺口不是“公司未披露”。旧 v2.0.2 的表格指引必须按失败验收保留并排除研究使用，不得用原始记录不变作为沿用错误值的理由。

### 关系、经营指标、承诺、到期债务和风险候选

| 逻辑数据集 | semantic_type | 当前边界 |
|---|---|---|
| relations | customer_concentration、supply_chain | 客户集中度/供应商/竞争表述证据，未自动完成实体关系确认 |
| operating_metrics | segment_disclosure | 分部、地区、产品线相关原文候选，未自动填分部数值 |
| commitments | commitments | 采购、产能、租赁、云服务等承诺表述；金额尚未从任意表格结构化 |
| debt_maturities | debt_maturities | 债务/本金到期表述；不是完整到期债务时间序列 |
| risk_disclosures | contingencies、risk_statement | 或有事项/风险原文；没有完成版本间“风险变化”解释 |

以上共用字段：`text` 为原文；`counterparty_name=null`、`counterparty_status=not_resolved` 表示对手方未解析；`numeric_values=null` 表示未完成数字结构化；`dimensions=[]` 保留扩展空间；`verification_status=requires_review`；`is_structured_numeric_fact=false`；`change_analysis=not_performed`。质量均为 `evidence_candidate_requires_review`，信息性质为 company_statement。不得把候选数用于计算数值准确率或已核验关系数。

### 当前版本无法输出的占位状态

通用入库器为之前成功、当前源版本缺字段/不支持的自动结果追加状态载荷：`status=not_emitted_by_current_parser`、`value=null`、`prior_record_id`（前版连接）、`resource_key`、`source_content_sha256`、`reason`、`extraction_mode=automatic`、`is_observation=false`。其外层 quality 为 current_field_unavailable、knowledge_type 为 system_inference；这是机器的解析状态，不是新的公司数字/撤回声明。

## V2：开放来源专属字段

### news 和文章 documents

| 字段 | 定义 |
|---|---|
| title、content | Feed 中的标题和 excerpt/content；content_scope=feed_entry_only |
| publication、updated | Feed 原始时间字符串；与解析后的两个时钟一起保留 |
| source_published_at、publication_precision、publication_timezone | 单独解析的发布时间；无时区的具体时钟保持未知 |
| source_updated_at、updated_precision、updated_timezone | 单独解析的修改时间，不拿它冒充首发 |
| source_url、source_name、feed_url | Feed 提供的原来源及当前 Feed 地址；原转载关系不擅自认证 |
| canonical_url | 去 fragment/已知追踪参数的 URL，保留实际地址关联 |
| original_feed_item_xml | 单条 Feed XML 表示；整个原始响应仍通过 raw_id 保留精确字节 |
| item_content_sha256 | 本条标题/内容规范化摘要，不替代原响应 SHA-256 |
| canonical_duplicate_candidate、content_duplicate_candidate、title_duplicate_candidate | URL、内容、标题候选分组哈希，仅提议重复，不自动合并不同事件 |
| duplicate_status、event_cluster_status | candidate_only_not_merged、needs_review |
| company_association_candidates | company_id/matched_name/method/status 数组；只按完整配置公司名匹配，不能把短 ticker 子串当事实关系 |
| historical_version_time_verified | false；当前 Feed 不证明历史版本 |
| republication_status | feed_source_field_only 或 unknown；不推断“谁最早发布” |
| article_full_text_retrieved | Feed 条目始终 false，摘要不算全文 |
| full_text_permission | configured_allow 或 missing_authorization |
| documents.news_record_key、authorization_note | 已明确允许文章请求时，关联 Feed 条目和权限说明 |
| documents.document_type | news_article_response |
| documents.article_structure_verified、full_text_status | false、response_acquired_requires_article_structure_review：响应留存，但正文/导航范围未独立核验 |

新闻业务键含 Feed 身份与 canonical URL，同 URL 不同 Feed 的证据不互相覆盖。旧发布时间不在范围、但更新时间在范围的条目仍会进入修订检查。

### policy_documents：Federal Register

详情载荷保留 API 原始所有字段，包括 document_number/title/type/abstract/agencies/citation/publication_date/effective_on/body_html_url/full_text_xml_url/pdf_url、源提供的修订关联。`metadata_only=true`、`full_text_status=separate_inventory_resources` 明确它只是元数据。

正文/PDF 载荷字段：`document_number/title/url/format_field`（来源 URL 字段）、`content_sha256/metadata_raw_id`（正文与详情原件连接）、`full_text_status=original_response_acquired`、`historical_version_time_verified=false`、`publication_date/effective_date`、`legal_edition`（官方 govinfo PDF 或 informational_rendition）、`text`（HTML/XML 简单抽取或 null）、`text_extraction_status`（extracted_unverified 或 not_performed_original_pdf_retained）。当前 PDF 原件已取得不表示 OCR/条款结构化完成。

按主题/机构查询的相关性只是源检索候选；不能由某条 policy 字段存在推断它已被证明适用于某公司。

### industry_metrics：BLS

| 字段 | 类型/单位 | 定义 |
|---|---|---|
| series_id、original_series_name | TEXT/null | BLS ID、可选配置名称；不是公司业务指标 |
| value、original_value | 十进制字符串/null、来源原值 | 无有限数字时保持 null 并报告缺口，不补零 |
| year、period、period_name | TEXT | BLS 年份与 M01..M12/M13/A01/Q01..Q04 期间代码和源名称 |
| frequency | TEXT | monthly/quarterly/annual/annual_average；精确起止日期写外层 period_start/period_end |
| footnotes、source_item、catalog | ARRAY/OBJECT/null | 完整源脚注、观测对象、元数据；保留 preliminary 等状态 |
| unit、unit_evidence_status | TEXT/null | 配置单位及 configured_unverified，或 unknown；没有独立核验就不能提升为已证实 |
| source_published_at、source_revision_at | null | 当前 GET 不提供可靠历史发布时间/修订时钟 |
| vintage_status、historical_release_time_verified | TEXT/BOOLEAN | current_api_snapshot_only、false |
| seasonal_adjustment | TEXT | 配置季调信息或 unknown，不能只凭系列名称猜 |
| knowledge_type | TEXT | fact_observation；其来源/单位核验质量另看 quality |

单系列 GET 最近三年之外日期明确 BLS_RECENT_GET_HISTORY_LIMIT。取得当前值只能从实际取得时刻查询，不可靠重建当年的经济数据 vintage。

### calendar_sessions 和 IR documents 的 V2 名称差异

V2 CalendarAdapter 使用 `is_open`（v1 为 is_session）、`market`、`library_version`、`knowledge_scope=current_library_rules`；open/close/break_* 仍为带时区时间。生成库快照的 knowledge_type=system_inference，不冒充交易所历史通知。

IR documents 新字段：`resource_key`、`original_published_at/source_revision_at`、`current_copy_only=true/historical_copy_verified=false`、`authority_evidence`（入口的来源、引用或配置证据）；列表可含 `enumerated_links`，webcast landing page 为 `transcript_obtained=false/audio_downloaded=false`。源日期可解释为旧发布，但 current copy 仅从首次观察可用。

## V2：持久采集机器物理表

下列表用于运行状态；除 resource_versions 引用的原件及业务 records 外，这些运行状态表可更新。它们不能替代不可变研究证据。所有 `_json` 为 JSON TEXT；接口读取通常解码并去掉 `_json` 后缀。

### machine_inventory：应获取资源

| 字段 | 定义 |
|---|---|
| resource_id TEXT PK | source_id+resource_key 的稳定摘要 |
| source_id TEXT | 来源注册键 |
| resource_key TEXT | 来源内稳定资源业务键；与 source_id 组成 UNIQUE |
| kind TEXT | submissions/filing_index/news_feed/policy_body 等适配器分支 |
| data_type TEXT | 这项资源服务的数据类型，不保证已取得所有该类字段 |
| url TEXT/null | 请求地址或本地库 URI |
| company_id TEXT/null | 公司范围，全球宏观等为空 |
| security_id TEXT/null | 有证据证券范围，不用 ticker 代替 |
| metadata_json JSON | 无凭证请求参数、父源证据、期间、表单、分页/刷新规则等 |
| state TEXT | 资源当前获取/解析状态，枚举见下文 |
| first_discovered_at UTC | 首次发现时刻 |
| last_discovered_at UTC | 最近一次重新发现时刻 |
| last_attempt_at UTC/null | 最后执行尝试时刻 |
| last_success_at UTC/null | 最后成功获取/处理时刻；不能解释为所有字段已核验 |
| raw_id TEXT/null | 最近已保存原件，可连接 raw_blobs |
| retrieval_id TEXT/null | 对应成功获取流水 |
| last_error TEXT/null | 当前最新错误摘要 |

### machine_tasks：持久任务

| 字段 | 定义 |
|---|---|
| task_id TEXT PK | 来源、任务类型、资源、generation 的稳定摘要 |
| source_id TEXT | 来源 |
| task_kind TEXT | discover/acquire/reprocess |
| resource_id TEXT FK/null | 连接 inventory；discovery 可无具体资源 |
| generation TEXT | 回填范围稳定标识或增量周期标识；预算不改变逻辑范围 |
| scope_json JSON | 市场、证券池、日期、类型等发现范围 |
| resource_json JSON/null | 执行时的资源参数快照 |
| state TEXT | pending/running/succeeded/failed/retry_wait/blocked/deferred |
| attempt_count INTEGER | 真实尝试次数，默认零 |
| created_at UTC | 任务建立时刻 |
| updated_at UTC | 状态最后修改时刻 |
| next_attempt_at UTC/null | 退避结束时刻 |
| owner_run_id TEXT/null | 领取任务的运行 ID |
| lease_until UTC/null | 当前 worker 租约截止，不抢占仍有效的执行者 |
| raw_id TEXT/null | 获取检查点，解析失败重试可直接读取此原件 |
| retrieval_id TEXT/null | 获取检查点对应流水 |
| result_json JSON/null | parser/discovery 结果、coverage、错误等 |
| last_error TEXT/null | 最新失败信息 |

### machine_attempts：单次尝试

| 字段 | 定义 |
|---|---|
| attempt_id TEXT PK | 尝试 UUID |
| task_id TEXT FK | 对应任务 |
| run_id TEXT | 发起运行 ID |
| started_at UTC | 尝试开始 |
| finished_at UTC/null | 结束；未结束为空 |
| state TEXT | running/succeeded/failed/interrupted 等执行状态 |
| stage TEXT | start/acquire/parse 等失败定位阶段 |
| error TEXT/null | 错误文字；不含认证秘密 |
| raw_id TEXT/null | 当次已获得原件检查点 |
| result_json JSON/null | 当次处理结果 |

### machine_runs：运行/预算边界

| 字段 | 定义 |
|---|---|
| run_id TEXT PK | 运行 UUID |
| mode TEXT | backfill/update/resume/reprocess 等模式 |
| generation TEXT | 该次任务生成周期 |
| started_at UTC | 开始时刻 |
| finished_at UTC/null | 完成/停止时刻 |
| heartbeat_at UTC | 最近心跳 |
| lease_until UTC | 运行租约截止 |
| state TEXT | running/completed/interrupted/limited 等运行状态，按实际 engine 结果 |
| config_json JSON | 去除凭证后的运行配置快照 |
| summary_json JSON/null | 请求数、字节、成本预算、任务数、时长、剩余工作等运行汇总 |

### machine_gaps：缺口清单

| 字段 | 定义 |
|---|---|
| gap_id TEXT PK | 来源/公司/类型/code/details 稳定摘要，重复发现累加 |
| source_id TEXT | 来源或 registry |
| company_id TEXT/null | 涉及公司 |
| data_type TEXT/null | 涉及类型 |
| code TEXT | 程序可识别原因 |
| status TEXT | capability_gap/missing_authorization/partial_parse/resource_limited 等 |
| message TEXT | 人可读解释 |
| details_json JSON | 未覆盖日期/公司/页、失败 URL、原件、依赖、续跑参数等 |
| first_seen_at UTC | 首次见到缺口 |
| last_seen_at UTC | 最近见到 |
| occurrences INTEGER | 累计出现次数 |
| last_run_id TEXT/null | 最近运行 |
| resolved_at UTC/null | 明确解决时刻，未解决为空；不因别的任务成功自动消失 |

### machine_source_health：来源健康

| 字段 | 定义 |
|---|---|
| source_id TEXT PK | 来源键 |
| last_attempt_at UTC/null | 最近任务尝试 |
| last_success_at UTC/null | 最近执行成功 |
| last_error_at UTC/null | 最近错误时刻 |
| last_error TEXT/null | 最近错误描述 |
| status TEXT | 当前来源运行状态，与 registry 静态实现状态分开 |
| attempts INTEGER | 尝试数 |
| successes INTEGER | 执行成功数，不能作为真实数值准确率 |
| failures INTEGER | 失败数 |
| capabilities_json JSON | health() 声明的依赖、实现能力、限制与当前检查结果 |

### machine_resource_versions：原件解析版本检查点

| 字段 | 定义 |
|---|---|
| version_id TEXT PK | 资源与获取流水的稳定版本键 |
| resource_id TEXT FK | inventory 资源 |
| raw_id TEXT FK | raw_blobs 原件 |
| retrieval_id TEXT FK | raw_retrievals 获取流水；与资源组成 UNIQUE |
| acquired_at UTC | 原件实际获取时刻 |
| parse_state TEXT | downloaded_unparsed/parsed/partial_parse 等解析状态 |
| parsed_at UTC/null | 最近解析处理时刻 |
| parse_result_json JSON/null | 输出、错误、coverage 的解析结果；解析器版本可通过输出 records.parser_version 追溯，不保证每个适配器在此重复同名字段 |

### machine_discovery_dependencies：跨来源发现依赖

用于先运行 IR、随后 SEC 新获取资料才提供官网入口等情形。记录上游证据指纹与下游已消费指纹；下游发现不能把“上游暂时没有入口”当永久完成，也不应在上游完全没变时无限重排同一发现任务。

| 字段 | 定义 |
|---|---|
| task_id TEXT FK | 下游 discovery 任务；与 upstream_source_id 组成复合主键 |
| upstream_source_id TEXT | 其依赖的上游来源 |
| requested_fingerprint TEXT | 最新已观察到、要求下游处理的上游证据指纹 |
| consumed_fingerprint TEXT/null | 下游上次成功消费的指纹；尚未消费为空 |
| observed_at UTC | 观察到本次上游指纹的时刻 |
| consumed_at UTC/null | 下游完成消费的时刻 |

## 状态不能混用

| 状态 | 含义 |
|---|---|
| discovered_not_downloaded | 已枚举资源，尚未成功取得正文；包括预算后剩余任务 |
| download_failed | 这次下载失败，不意味着来源没有披露 |
| downloaded_unparsed | 原件已保存，尚未解析或解析任务失败 |
| partial_parse | 部分字段/格式拒绝或无法识别；不能计作全部字段成功 |
| parsed | 支持的解析步骤完成；coverage.document_semantically_complete 仍可能 false |
| not_disclosed | 必须有来源证据支持“未披露”，不可由下载失败推出 |
| not_applicable | 有明确适用范围依据 |
| missing_authorization | 缺账户/权限/授权，不能用摘要或别的来源冒充 |
| source_conflict | 来源或重复事实冲突，消费者必须看到冲突证据 |

任务 pending/deferred/blocked、资源状态、字段质量、来源实现/接通/核验是不同维度。报表至少分开统计软件测试、实际原件获取、字段准确性、资料完整性、跨公司通用性，禁止只用总条数作为验收。

## record_validations：追加式字段核验结论

原始摘录不覆盖或删除；核验与解析值分开保存。

| 字段 | 定义 |
| --- | --- |
| validation_id TEXT PK | 审计结论标识 |
| record_id TEXT FK | 被核验的原始标准化记录版本 |
| verdict TEXT | rejected、verified、needs_review；仅针对这一版本及审计范围 |
| reason TEXT | 核验依据、失败原因、范围和限制 |
| raw_id TEXT FK | 结论使用的原文证据 |
| validated_at UTC | 系统实际作出该核验结论的时间，不伪装成历史公开时间 |

普通 query 默认排除 rejected；`--include-rejected --all-versions` 是审计入口，并返回 validation 对象。`known_at` 只应用截止时刻已经知道的审计结论，以保留历史系统知识状态。同份原文在新解析器中不能再输出的字段，会失效并拒绝作为当前事实；新的单位缺口不能静默沿用旧值。仅解析版本升级、数值语义未改变不应被误判为源数据自身修改。
