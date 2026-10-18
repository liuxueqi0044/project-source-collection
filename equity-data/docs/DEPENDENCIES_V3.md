# V3 外部依赖与接通后验收

核查日期：2026-10-09。以下是接入需求，不代表已经开通产品。公开产品说明也不能代替账户实际授权、历史样本和存储处理条款。费用没有取得正式报价的均记为待确认。

| 需求 | 产品候选与接口 | 所需权限与字段 | 历史深度要求 | 接通后验收 |
|---|---|---|---|---|
| 美股未复权行情 | Alpaca Market Data：`GET /v2/stocks/bars` | APCA key/secret；明确 IEX/SIP/OTC feed；OHLCV、交易日/时段、单位、VWAP/成交额是否真实提供 | 至少覆盖配置回测窗口；逐证券检查第一/最后日期，不推断统一起点 | 活跃/不活跃/更名/拆股/停牌各抽样；分页首尾、日期边界、交易所日历缺口、与独立授权数据对照；验证 raw adjustment 与符号拼接设置 |
| 公司行为 | Alpaca `GET /v1/corporate-actions`，或完整授权行动数据库 | 账户许可；公告/除权/生效/支付日、比例、现金币种、原/新证券和更正标记 | 所有样本证券在历史窗口内的行动及后续修订；各类型最早历史待实测 | 分红、拆并股、分拆、并购现金/股票对价、权利分配、退市处置；验证原始价格独立保存及派生重算。供应商新类型必须明确支持或拒绝 |
| 历史证券池/永久标识/退市 | CRSP US Stock Databases（商业/机构授权候选）；许可文件交付和数据字典适配 | 永久证券/公司标识、代码/名称有效期、交易所、股类、上市退市日期、退市原因与回报、分配/股数 | 应覆盖完整研究窗口及窗口前身份衔接；实际版本与字段依订阅确认 | 更名保持同一证券、多股类不合并、退市证券不消失、当时股票池可重建；抽查身份生效日期及退市回报缺失处理 |
| 历史市场预期 | LSEG I/B/E/S Detail/Summary History 内容授权；按实际供货方案选择 API/批量文件 | 指标、目标财期、公司/证券映射、分析师/经纪商标识或授权匿名标识、估计值、币种口径、发布/生效/修订时间、均值中位数分歧及贡献者数 | 明确要求 point-in-time 历史版本；普通当前快照产品不能代替历史文件 | 从公告前截止时点重建估计集合；与供应商历史快照对照；预测撤回/修订、拆股调整、实际值口径及贡献者变化分别测试 |
| 宏观历史版本 | FRED/ALFRED API：series observations、vintage dates | 免费申请 FRED_API_KEY；观测期、realtime_start/end、单位、频率、修订版本 | 每个系列分别查历史起点与 vintage 范围 | 对同一观测期至少两个 vintage 检查修订和历史查询排除未来值；当前 CSV 只算当前快照 |
| 行业统计 | BLS 公开接口；必要时注册 API；EIA API 需 key | 选择明确 series、单位、季调、频率、历史区间；BLS 当前运行网络失败需重试验证 | 依序列和接口限制；不能把近期窗口称完整历史 | 同一时间范围逐期对照官方表，检查分页/年限/单位、修订与缺失。EIA 当前仍为候选 |
| IR/新闻全文和电话会 | 公司官方 IR、明确授权新闻源/文字稿供应商 | 官方入口、全文获取许可、历史版本、事件及发布时间 | 当前 RSS/网页不能替代历史全文库 | 静态/RSS/PDF/动态页面分别抽样；网页删改、附件缺失、只有录音无文字稿分开标记；全文与摘录不同状态 |

Alpaca 官方资料明确区分 IEX 单交易所和 SIP 合并行情；权限与延迟受账户影响：[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)。公司行为接口及新增类型应以所接版本为准：[Corporate actions](https://docs.alpaca.markets/us/v1.1/reference/corporateactions-1)、[2026-05-22 新类型](https://docs.alpaca.markets/us/v1.1/changelog/2026-05-22-corporate-actions-5c87d2b)。

CRSP 官方产品入口：[US Stock Databases](https://indexes.morningstar.com/research-data-products/crsp-us-stock-databases)。产品存在不等于本系统有许可；当前 `licensed_master` 仍是依赖边界，尚无相应真实文件解析验收。

LSEG 官方接口入口：[Estimates API](https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/estimates-API)。历史深度必须按最终购买的内容包确认，不能拿指数级 aggregates 或当前 API 快照充当个股完整历史一致预期。当前 `licensed_estimates` 没有真实授权样本，无法研究相对市场预期的业绩差异。

ALFRED 说明：[FRED/ALFRED](https://fred.stlouisfed.org/docs/api/fred/alfred.html)。当前没有 key 的 CSV 采集不支持重建历史 vintage。

无授权材料、保密经营资料、未公开交易指令不进入自动数据集。人工导入仍须保存授权、性质、样本范围和方法，不能计入自动采集成功率。
