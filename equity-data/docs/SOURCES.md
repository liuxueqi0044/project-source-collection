# 来源清单与接入边界

核查日期：2026-10-09（Asia/Shanghai）。本表区分“官方文档可行”“适配器已实现”和“本次运行实际取得”。真实下载数量、HTTP 失败、证券/日期覆盖以运行生成的覆盖报告为准；下列候选不等于已经取得授权或完整历史。

## 第一优先级

| 来源 / 数据集 | 接入、账户与费用 | 历史、更新、修订、限制 | 当前实现与剩余核验 |
|---|---|---|---|
| [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)：公司档案、申报索引、结构化财务 | 官方 JSON 与原始申报文件；公众读取无需 API key，自动访问声明真实联系身份；软件通过统一限速/重试传输保存原始字节 | Company Facts 主要覆盖标准分类、整体申报实体的 XBRL；强制 XBRL 起于 2009，不能据此保证每家公司自 2009 完整。提交索引与 XBRL 随披露更新；旧提交需遍历历史文件；更正通过新 accession 与事实版本保留 | 已实现 SEC 适配器。CIK 是申报实体 ID，不能当成证券 ID；证券映射另表。Company Facts 不覆盖全部自定义标签、附注表格及分部维度，完整附注靠原文；当前证券名单不等于历史股票池 |
| [SEC 原始申报检索](https://www.sec.gov/search-filings)：10-K/10-Q/8-K/20-F/6-K、股东、并购、诉讼、管理层事项 | 公共原文，按 accession 取得 primary document 和可取得附件；无订阅费 | 表单范围取决于配置，不是所有事件都有机器可读数值。原始网页及附件保留版权/原发行人信息；全文展示或再分发需另看条款。历史正文可能受访问限制 | 已实现原始文档下载和索引保留；有数量上限时明确列为样本覆盖。被网络拒绝的正文不标为已取得；不得用摘要冒充原文 |
| 公司投资者关系网站：业绩新闻稿、演示资料、电话会、问答、指引、预计财报日期 | 公司原站公开 HTML/PDF；通常浏览无需账户，但不是统一 API，音视频/第三方电话会文本条款独立 | 档案起点与保留年限因公司而异；活动后更新，页面可能被覆盖；发布时间可能只有日期。网页的发布日期不是事件生效日期 | 公共文档/人工审校导入路线。原文、哈希与段落证据保留。指引、分析师预期、实际业绩分别存表；暂不自动推断缺失指引或公司关系 |
| [Alpaca 历史日线](https://docs.alpaca.markets/us/reference/stockbars)、[市场数据 FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)：未复权 OHLCV、基准 ETF | 账户 API key/secret；IEX 与 SIP 权限按账户计划，收费和存储/再分发权限以账户合同为准；本次没有账户，未进行授权数据实测 | `adjustment=raw`，显式 feed；IEX 只代表一个场所，不能当作全市场成交量。日线依交易条件规则聚合，OHLC 与量的纳入条件未必完全相同，不能简单声称全部为 09:30–16:00 常规盘。分页最多 10000 条，限额依账户；退市证券历史、全部年份和历史修改时刻仍待样本核查 | 适配器已实现、mock 验证，无凭证则缺口退出。禁用自动历史代码映射 `asof=-`，由显式证券身份和代码区间管理。成交量单位为股；成交额为空，绝不将 VWAP×成交量当成精确成交额。行情版本只从本次获取时间开始可见 |
| [Alpaca 公司行为](https://docs.alpaca.markets/us/reference/corporateactions-1)：拆并股、现金/股票分红、分拆、并购、赎回、更名、权利分配等 | 同上，`/v1/corporate-actions`，显式 `data_quality=all` 保存不完整候选 | API 起止日期依据 `process_date`，不是除权日；分页最多 1000。官方不保证创建时间，宣布后可能延迟入库；完整历史长度、退市处置、日期完整性待核验 | 适配器保留全字段及事件日期；不把 `process_date`、`declaration_date`、除权日冒充已验证公开时刻。未验证的事件不应直接驱动历史交易或全市场复权 |
| [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars)：休市、常规开闭市、半日市、时区 | 实际复用开源 Python 库，Apache-2.0；无需账户 | 每个市场可用范围由库实现决定；更新依赖包版本，紧急休市可能事后补入。不是官方历史公告档案，也没有单证券停复牌或涨跌幅限制 | 已实现生成 JSON 原材料、包版本及每日开闭状态；美股 XNYS 与 A 股 XSHG 独立。今日生成日历仅有今日获取时间，不能据此断言历史当时已公布；官方页面交叉核验见下一行 |
| [NYSE 交易时段/日历](https://www.nyse.com/trade/hours-calendars)、[Nasdaq 停牌入口](https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts)：时段、停复牌、市场规则 | 官方网页/公开历史查询；历史批量交付、分发权限与费用另核查 | NYSE 时段依具体市场；Nasdaq 页面停牌时间为 ET。当前页面不证明旧规则在何时生效，历史完整范围未验 | 本次只核查入口；证券停牌与规则沿革尚未建立全面自动采集，作为明确缺口。交易限制应分市场、证券类别、生效区间管理 |
| 基准提供方或授权行情商：市场/行业基准 | 可先将明确身份的 ETF 配置为 `is_benchmark=true`，走相同行情适配器；正式指数授权另议 | ETF 价格收益、ETF 含分红收益、指数价格收益、指数总收益均不同；历史成分同样需时点数据 | `benchmark_bars` 独立保存原始价格，标明 `price_only_excludes_cash_distributions`；尚未声称拥有正式行业指数或完整总收益序列 |

公司名称、代码与证券身份：当前 SEC 档案可给出公司现名/曾用名线索，但不会自动给出完整、无歧义、全时期的证券级历史主表。上市/退市日、不同股类/ADR、并购后新实体、旧代码被复用都需原始公告或授权 reference-data 交叉验证。手工精选样本仅证明链路；不能用它进行无偏股票池回测。

公司行为的最小字段映射依据 [Alpaca 官方 SDK 模型](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/data/models/corporate_actions.py)：forward/reverse split 的正有限 `new_rate / old_rate` 映射成 `split_ratio`，原始类型与两项比率同时保留；不合法比率保持未归一化。cash dividend 的 `rate` 保存为每股 `cash_amount`，但当前 API 模型未保证币种字段，币种缺失就保持缺失，不能直接复权。除权前交易日必须通过市场日历另外验证，适配器不会猜成前一个自然日。

## 第二优先级

| 来源 / 数据集 | 接入、授权 | 时间/覆盖与限制 | 当前交付 |
|---|---|---|---|
| [FRED / ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html)：利率、就业、通胀、生产、消费等 | 官方 API 需申请 key；另有公开图表 CSV。各系列可能有第三方版权或使用限制，见 [API 条款](https://fred.stlouisfed.org/docs/api/terms_of_use.html)；不将“免费访问”解释为无限制分发 | 频率与历史起点依系列。ALFRED `realtime_start/end` 记录当时可见窗口；默认查询是今天所知，必须显式设置。日精度不足以重建当天盘中信息集 | 已实现带 key 的 vintage 适配器；无 key 时仅在 `allow_public_snapshot=true` 明确开启当前 CSV 快照。该快照的 `published_at` 未知，仅从获取时间可见，不能做历史宏观修订回测 |
| [FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)、[Real-Time Periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)：宏观修订 | JSON 全分页，`output_type=1`、显式 realtime 范围，`units=lin` 不做额外变换 | 页上限 100000；限速保守服从响应。原始响应保留查询窗口截断的 realtime_end，值用 series、观测日期、vintage_start 追溯。API 元数据目前是获取当日快照，不伪称历史元数据 | vintage 日期以美国东部下一天零点作为保守可见假设，不宣称已验证真实发布时间；已用模拟初值/修订验证时间过滤 |
| 公司原始披露：同行、客户、供应商、关联产品 | SEC 正文、公司新闻稿、已授权材料，保留出处和段落 | 披露常有匿名客户或不完整范围；关系的有效区间与确认程度不能由 LLM 补造 | 关系表/人工导入扩展；公司陈述和系统推断分开；未全面结构化产业链 |
| [EIA Open Data](https://www.eia.gov/opendata/)：能源价格、库存、产量、产能、销量 | 官方开放 API，注册 key，另有批量文件；遵守其版权及重用政策 | 日/周/月/年频率随序列，起点各异；实时、估计、预测分开。最新版序列不自动提供完整 vintage，需要历史发布包与持续快照 | 已核查候选；仅在确定覆盖能源行业后实现专用适配器。其他行业按原始统计机构/协会另选，不假设能源指标适用于所有公司 |
| [BLS API](https://www.bls.gov/developers/)：劳动、物价等原始统计 | 官方公共 API，注册与不注册配额不同，接入时按版本验证 | 系列起点、修订、季调口径不同；数据期不等于发布日期 | 候选，用作 FRED 上游交叉核验；本次未全面采集 |
| [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1)：政策、监管原文 | 政府公共 API，无需 key；按检索词及发布日期查询，再抓文档号对应 JSON 与正文 HTML | 发布日、生效日、提案/最终规则、更正要分别存；政策适用行业需人工确认。站点 HTML 是信息展示版，正式法律版本在 govinfo PDF | 已实现 `research.policy` 有界样本，默认最多 3 篇；本次未自动下载正式 PDF。当前 HTML 版本只从获取时可见；没有时间证据的修改不反推到旧发布日期。不是政策全库 |
| 新闻发行机构与原始通讯社：公司/行业事件 | 已实现配置 URL 的公开 RSS/Atom 抓取；付费全文、转载和保存权分别核查 | 首发、更新、转载日期不同；网页可能覆盖旧稿，历史改稿需持续快照或供应商版本；事件去重不能仅按标题 | 已实现 `research.feeds`；保存 feed 原文、canonical URL、片段 hash、发布时间/更新时间。只取得 feed 片段，未冒称全文；相同链接与内容去重、改稿追加版本，语义事件聚类标记 `needs_review`，不同原站转载仍需人工确认 |
| [LSEG I/B/E/S](https://www.lseg.com/en/training/learning-centre/learning-paths/learning-path-for-data-solutions/learn-about-quantitative-data-solutions/learn-about-data-and-content/learn-about-ibes)：历史预测与一致预期 | 商业授权；明确购买 Point-in-Time 产品、历史日期/退市覆盖、EPS 口径、使用/保存权限和接口交付 | 不同产品/订阅不保证相同时间粒度与覆盖；确认每条预测生效时间、收录时间、分析师样本、汇总/分歧、修订/撤回 | 待授权；无分析师预测实测。**目前无法研究相对市场预期的业绩差异。** 有证据的公司指引与后来实际值可另行比较 |

## A 股独立配置与扩展

当前选择美股。A 股来源、Asia/Shanghai 时区、XSHG 日历、证券规则单独配置；不能将 SEC/Alpaca 或美股规则直接用于 A 股。

| 候选入口 | 计划用途 | 核查状态 / 依赖 |
|---|---|---|
| [巨潮资讯](https://www.cninfo.com.cn/)、[上交所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/)、[北交所公告](https://www.bse.cn/disclosure/announcement.html) | 财报、公告、公开投资者关系记录原文；更正公告独立存储 | 前两个公开入口已核查，北交所页面本次浏览工具无法读取。无已验证自动接口或完整历史覆盖承诺；需检查接口许可、访问频率、正文存储与具体市场差异 |
| [Tushare 文档](https://tushare.pro/document/2) | 证券主表/更名、行情、停复牌、财务、分红、交易日历候选 | 文档列出这些数据类别，但账户 token、积分/收费、字段单位、历史退市/修订覆盖需逐端点核验。A 股适配器尚未实现；禁止假称换 market 即能完整采集 |
| 国家统计局、人民银行、海关统计 | 中国宏观、信贷、进出口 | 用户指定候选，暂未逐接口实测；等待 A 股/行业范围确定，先保留依赖，不绕开登录或验证码 |

## 扩展材料的接入要求

研报、专家访谈、渠道调查、网站/App/消费数据、持仓、卖空、期权、舆情和人工行业知识先走带来源的人工导入。每份材料注明授权依据、公开/可获取时间、公司与证券关联、版本、单位及知识类型：事实观测、公司陈述、专家意见、调查估计或系统推断；调查还需样本范围和方法。未知字段保持空并说明原因。内部泄露数据、保密订单、未公开交易指令不进入本系统研究数据集。

## 开源复用选择

| 项目 | 官方依据与判断 | 本次选择 |
|---|---|---|
| [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) | 已有交易日、节假日、早收盘和时区规则，避免重复造日历 | **实际依赖并使用**；冻结运行版本，结果标为当下规则视图 |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB)、[许可 FAQ](https://docs.openbb.co/odp/python/faqs/license) | 多来源接入框架适合未来扩展；当前版本文档说明 Apache-2.0，第三方数据条款独立 | 已评估，不作为本次核心运行依赖。未来扩源可复用 provider；仍需本项目的原始证据、双时间与修订存储约束 |
| [EdgarTools](https://github.com/dgunning/edgartools/blob/main/docs/quickstart.md) | 提供公司、原文、财务报表和 XBRL 解析能力，需要声明访问身份 | 已评估；后续完整自定义标签、分部与附注解析优先集成它。本次使用官方原始 JSON 加最小适配层，不把聚合报表替代原文 |
| [sec-edgar-downloader](https://github.com/jadchaar/sec-edgar-downloader) | 成熟申报下载库，适合扩大表单下载范围 | 已评估，未安装为核心依赖。当前下载通过统一证据传输保存请求、哈希、获取时间与失败原因；扩量可封装其下载产物进入同一存储 |
| [alpaca-py](https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html) | 官方 SDK 已有行情、公司行为请求模型 | 已评估。当前仅少量 REST 端点，直接统一传输保存精确响应；后续实时流优先复用 SDK，不实现交易功能 |
| [pandas-datareader](https://pandas-datareader.readthedocs.io/en/latest/readers/fred.html) | FRED Reader 便于时间序列研究，但常见按观测日期读取的输出不带本项目所需 vintage 证据 | 不用作历史时点宏观入口；直接官方 ALFRED 参数接口。公开 CSV 仅标示当前快照 |

上述是本项目的工程取舍，不是对库功能的完整审计。自己实现的部分集中在不可变原始资料、标准字段、双时间查询、版本/冲突、检查点和质量日志，不重复实现现成交易日算法。

## 有界 P2 采集配置

`collect_research(store, client, config)` 默认关闭。开启时只访问配置的 feed URL，以及 Federal Register 的有限搜索结果；不自动遍历外站或批量抓取通讯社全文。

```toml
[research]
enabled = true
max_feed_entries = 50

[research.policy]
enabled = true
terms = ["semiconductors"]
max_documents = 3
start = "2024-01-01"
end = "2024-12-31"

# 增加明确选定的公开 feed 后再取消注释；这里没有预装未验证的新闻源。
# [[research.feeds]]
# url = "来源官方公开RSS或Atom地址"
# max_entries = 30
# start = "2024-01-01"
# end = "2024-12-31"
```

新闻发布日期已知时按配置期间过滤；未知时保留未知，不指定猜测时区。截取数量、正文失败和 XML 解析失败都有质量日志。仅 canonical URL 与内容哈希相同才可视为重复快照；相关事件的不同报道不会自动合并。历史版时间无证据的新闻/政策快照，只能回答“今天取得了这版内容”，不能回答“旧日期当时已看到这版内容”。
