# 来源、开源复用与外部依赖

`configs/source_registry.json` 是可管理的来源合同，登记市场、数据类型、接入、认证/授权、费用、历史长度、修订、退市、发布时间、失败处理和替代来源。`sources` 命令增加运行健康。implemented、mock_tested、connected、verified不能互相替代；当前来源是否成功和哪些字段经过抽查，由运行报告及独立审计证据决定。

## 官方接口及边界

| 来源 | 官方文档 | 本系统接入与关键限制 |
| --- | --- | --- |
| SEC | [API说明](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | CIK申报历史、files分页、Companyfacts及归档正文。标准API主要实体整体概念；自定义及维度从iXBRL取。联系邮箱+限速，无API key。filed与接受时间不等于独立证实的精确公开传播时间。 |
| Alpaca | [市场数据说明](https://docs.alpaca.markets/us/docs/market-data-faq) | 需key/secret及feed权限。IEX单所与SIP合并不可互换。分页原价和公司行为接口已有模拟测试，但本次没有真实凭据或行情。|
| FRED/ALFRED | [实时版本说明](https://fred.stlouisfed.org/docs/api/fred/alfred.html)、[观测值接口](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) | 公开CSV当前值已接入；API元数据及vintage分页等待key实测。API日精度采用保守可见边界，CSV不当历史修订。各底层系列授权仍须分别检查。 |
| Federal Register | [API文档](https://www.federalregister.gov/developers/documentation/api/v1) | 按主题/机构/出版日分页，详情和全文分别入任务。列表摘要不等于正文，HTML研究版本和正式法律PDF有区别。 |
| 官方RSS | [美联储feed目录](https://www.federalreserve.gov/feeds/feeds.htm) | 本次用官方press feed验证条目管道；保存原始XML、公开/修改字段和重复候选，摘录不是新闻全文或完整历史库。 |
| BLS | [v2文档](https://www.bls.gov/developers/api_signature_v2.htm) | 无key单序列近期GET，最近三年以外列历史缺口。本次访问403，真实状态仍是下载失败。 |

## 开源优先的取舍

| 轮子 | 决定 |
| --- | --- |
| [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars) | 已复用日历、交易所和时区；当前库规则不能证明历史停牌或紧急休市公告。 |
| [requests](https://requests.readthedocs.io/)、[SQLite](https://www.sqlite.org/) | 已复用HTTP和持久事务。项目增加任务状态、来源证据、版本和时间规则。 |
| [lxml](https://lxml.de/)、[Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)、[pypdf](https://pypdf.readthedocs.io/) | 已复用HTML/XML/PDF解析。项目不重复造底层解析器，只做语义映射、证据和拒绝规则；无PDF OCR。 |
| [edgartools](https://github.com/dgunning/edgartools) | 考察过便利SEC接口；现有工程采用透明官方端点，以保留每个资源的任务、原文、获取时间和解析版本。 |
| [Arelle](https://github.com/Arelle/Arelle) | 完整taxonomy、XBRL校验的后续适配候选；本次局部iXBRL与同申报跨文件引用不等同完整XBRL验证。 |
| [OpenBB](https://github.com/openbq-org/OpenBB) | 多供应商扩展候选；聚合接口不能自行证明历史身份、存储许可或PIT正确性，当前未接入。 |

## 外部依赖清单

1. 授权行情和公司行为：Alpaca或替代商；实测历史长度、退市、币种、成交额及并购/分拆处置。原价与复权派生分开。
2. 完整历史证券主档、停牌及生效规则：当前名单和ID映射都不能代替有效期、股类、上市/退市证据。
3. 历史分析师预期：如 [LSEG I/B/E/S](https://www.lseg.com/en/data-analytics/financial-data/company-data/ibes-estimates)，需要合同、schema、时间戳、修订和处理许可。当前明确**无法研究相对市场预期的业绩差异**；公司前期指引差异可另行研究。
4. FRED API key：免费注册依赖；接口路径已有模拟测试，尚未实测ALFRED历史版本。
5. 动态IR、电话会全文、完整新闻历史：需逐站适配/许可。当前网页不证明历史版本，公开音频不自动当文字稿。
6. 行业扩展：例如 [EIA](https://www.eia.gov/opendata/documentation.php) 需要key和序列/schema适配，目前候选；不能用BLS部分统计冒充完整产业链指标。
7. 研究、专家访谈、渠道调查和另类数据：授权人工JSONL入口，保存样本方法和信息性质；未建自动合同资料获取。保密、泄露、未授权内容拒绝导入。
8. A股：独立市场/日历配置已保留；巨潮、交易所披露、行情和证券状态尚未实现，不能套美股源。

本次无需采购即可运行公开渠道。估计请求费预算不会购买服务，订阅费和授权必须另核实。缺少依赖不阻塞其他可执行任务。最终覆盖以机器报告的资源、日期、字段和缺口为准。
