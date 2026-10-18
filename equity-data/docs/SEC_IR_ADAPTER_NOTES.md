# SEC 与官方 IR 适配器的实际边界

本文解释生产代码中的通用采集机制。真实接通、失败、待下载和字段准确性，应以本次数据库与验收报告为准；这里的接口说明不构成渠道完整覆盖声明。

## 股票池解析与证券身份

`resolve_universe(store, client, config)` 支持配置中的 `universe.symbols`、`universe.ciks`、`universe.csv_file`、`universe.mode="sec_current"`，以及兼容已有的 `securities` 配置。

- 代码解析使用 SEC 当前目录；主目录失败时尝试另一个 SEC 官方目录。响应原文保存为 `sec_directory` 来源。
- CIK 规范化为十位数字，公司 ID 为 `sec:CIK`。CIK 可以单独使用，不要求当前上市代码仍然存在。
- 证券类别有用户明确提供的 `security_id`、`share_class_id`、FIGI 或 ISIN 时保留该映射，并注明尚未独立核验。
- 无证券类别标识时，当前代码产生公司加当前代码的本地上市证券代用 ID；该 ID 可以跨交易场所变更保持一致，但**不证明更名前后为同一股份类别**。仅配置 CIK 时，证券 ID 可以为空。
- 当前列表不是历史股票池。上市日期、退市日期、代码有效区间、币种和证券类型无法由当前 SEC 列表确认的字段保持未知。
- `universe.limit` 与 `universe.offset` 是明确的资源选择边界；省略的身份及续接位置记录在 `UNIVERSE_RESOURCE_LIMIT` 中。

## SEC 采集图

生产适配器为 `SecAdapter`，来源 ID 为 `sec_edgar`。需要真实联系身份的 `sec.user_agent` 或其环境变量；不需要 SEC API 密钥。

```text
公司 CIK + 披露日期窗口
  → submissions 当前页
  → 与窗口相交的全部 archive pages
  → 全部匹配表格类型的 filing metadata
  → 各 filing 的 header/index + 已知 primary document
  → header 中匹配 attachment_types 的全部附件
  → companyfacts，按 filed 日筛选
```

没有内置的“每家公司只取若干份报告”限制。整体请求数、任务数、字节和运行时间由调度预算控制；未执行的资源继续保留在任务清单中。

默认附件匹配 `EX-*`。用户缩窄类型时，header 解析结果列出未选附件及原因。header 失败不会阻断已由 submissions 确认的主文档下载；但附件完整性仍未知。缺失主文档名时尝试从 header 的表格类型定位，不能确定则报告缺口。

财务表格中的自定义字段不预填。`companyfacts` 保留 SEC 标准标签及单位、精确期间、accession；原文解析另外保留自定义标签、维度和语义候选。两条通道的字段覆盖应分别统计。

SEC API 的官方说明明确：公司申报历史分页需读取 `filings.files`；标准 XBRL API 仅聚合非自定义标签与整家公司语境。[SEC EDGAR API 文档](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

## 文档集合中的上下文与单位

部分 Inline XBRL 申报把数值放在附件、把 context/unit 放在主文件。SEC 适配器向通用解析器提供 `context_documents`：

```python
{
    "content": b"原始支持文档字节",
    "raw_id": "sha256:...",
    "sha256": "...",
    "source_url": "已获取的 SEC 文件 URL"
}
```

支持文档必须是**同一公司、同一 accession、已经保存的 HTML 主文件或附件**。适配器核对 SHA-256；不会为了补上下文隐式访问网络，也不会把多个文件拼成伪造原文。

数值只从当前输入文档抽取。实际用到的跨文档 context/unit 在每条结果的 `context_evidence`、`unit_evidence` 与 `supporting_raw_ids` 中保存，并连接到 `record_evidence`。缺少支持证据的历史查询不把结果当作完整证据；`known_at` 不能早于必要支持原文的获取时间。

这不是完整 Arelle 校验器。未支持的 transformation、冲突定义、冲突值、舍入一致性和未加载 taxonomy 的限制都必须保留为解析状态，不得据此声称全部财务字段已核验。

## SEC 的时间规则

- 报告期与 `filed`、header acceptance、系统获取时间分别保存。
- header acceptance 不等于独立观测到的公开传播时刻。默认历史可用性采用纽约时间下 filed 日与已知 acceptance 日较晚者的次日零点。
- 后到 header 若证明原有按 filed 日计算的可用边界过早，会追加 `availability_constraints`，限制同 accession 的已有财务记录；原记录不覆盖、不删除。
- 同一 accession 的数值变化没有可靠修订发布时间时，仍保留旧版并采用保守的获取证据限制。
- 更正表格 `/A` 可识别；原 accession 的确切更正对应关系不能仅凭表格类型推断。

## 官方 IR 入口与遍历

生产适配器为 `OfficialIRAdapter`，来源 ID 为 `official_ir`。它可以从下列证据寻找入口：

1. 配置中明确指定的官方入口。
2. SEC 公司档案中的 `investor_website` 或 `website`。
3. 已保存的 `source_candidates`。
4. 已下载 SEC 原文中，同一短段落内的公司网站声明和链接／文字 URL。

不会按代码拼接公司域名，也不会把搜索结果页面当作官方 IR 原文。历史申报中提到的域名作为有出处的候选保存；当下域名所有权未独立核验。没有入口时报告 `IR_ENTRYPOINT_UNRESOLVED`。

调度器根据 `discovery_dependencies=("sec_edgar",)` 在新 SEC 证据出现时重新运行 IR 发现。已有静态 HTML、RSS/Atom、next/archive 分页和文件链接成为独立资源任务。

- `ir.max_depth` 控制遍历深度。超限链接仍进入资源清单，保留 `IR_TRAVERSAL_LIMIT`；增大深度后可以续跑。
- 未支持的 JavaScript 列表报告能力缺口，不能推断“来源未披露”。
- 外部 HTML 来源未确认时在覆盖结果中列明；链接的 PDF 与明确的 webcast 服务可以保存原始资源。
- webcast 落地页不等于电话会文字稿。未获取音频和全文时明确保留 `transcript_obtained=false`。
- IR 网页即使显示旧发布日期，也只证明当前下载的副本。公开日期标签独立保留，历史可用边界仍为该内容版本的首次系统获取时间。

## 如何理解完成状态

`parsed` 表示对应资源的解析任务已执行，不表示网站所有资料都已枚举，也不表示原文所有业务语义都已结构化。

要分别查看：任务是否完成、原文是否保存、结构化字段是否解析、语义是否待核验、日期证据是否充分、来源是否覆盖该问题。未经确认的客户名称、指南条件、全文缺失、历史证券池及授权数据依赖均不能靠解析成功率消除。
