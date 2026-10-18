# V2 来源适配、通用解析与扩展说明

本系统把发行人/证券发现、资料枚举、原件获取、解析和质量状态分开。新增一个 SEC 已覆盖的发行人，通过 `universe.symbols`、`universe.ciks`、证券池 CSV 或 `[[securities]]` 配置即可进入同一条链路。发行人的财务数字、公告地址和测试答案不得写入生产解析规则。公司样本只是验收输入。

## 1. 先确定系统当前能做什么

| 能力 | 现有机器行为 | 需要保留的限制 |
|---|---|---|
| 美股当前证券池 | 从 SEC 当前目录解析 symbol → CIK → 本地证券映射；可直接配置 CIK | CIK 是申报主体。当前代码不证明历史上市、股类或跨代码连续性；不构成无偏历史池 |
| SEC 申报发现 | submissions、历史 submissions 页、申报目录、正文与配置范围内附件形成任务图 | 公开档案的覆盖、表单过滤和附件规则必须出现在配置/清单；取得目录不代表附件已下载 |
| 标准财务数字 | Companyfacts 原始观测及逐个 inline XBRL 数值事实 | Companyfacts 不含所有扩展标签；inline 解析没有完整 taxonomy 校验 |
| 公司自定义维度/标签 | 保留 QName、context、单位、显式/类型维度和原值 | 不把相似标签自动视为可比较指标，定义未加载时明确未知 |
| 指引 | 只从带明确目标期间、指标、数值、单位的原文段落提取 | 表格数值指引全部拒绝，等待能解释表头/分部/列/限定条件的专门适配器；多指标混排、歧义期间、缺单位/容差等明确拒绝 |
| 关系、承诺、到期债务和风险 | 按关键词抽取可定位的原文证据候选 | 候选不是已核验的关系/金额；客户姓名、未披露数量均不推断 |
| IR | 使用配置的官方入口、SEC 档案/正文提供的官网或 IR 链接，继续枚举静态 HTML/RSS/PDF 链接 | 不猜 ticker 对应域名；不执行任意 JavaScript 门户；音频链接不算文字稿；当前网页不是历史快照 |
| 新闻 | 全部解析已配置 RSS/Atom 响应条目及分页，保留原条目、两个独立时间和重复候选 | 摘要不是正文；历史 feed 不完整。全文需要 `allow_full_text=true`；已下载响应仍标注文章结构待核验 |
| 政策 | Federal Register 按主题/机构/日期分页，逐条排详情、正文、官方 PDF | 摘要不算全文；HTML 是信息性版本。官方 PDF 保留原件，PDF 本文解释另需验证 |
| 行业统计 | BLS 按 series ID 获取近期数据，支持月、季度、年度/年均期间 | 当前无钥匙 GET 只有最近三年；历史发布和修订版本不在此接口内；配置单位不是独立元数据核验 |
| 宏观 | FRED 公共 CSV 当前值；有钥匙时使用 FRED/ALFRED API 的 vintage 字段 | CSV 无历史 vintage；按日 vintage 使用显式保守可用时间，绝不把观测期当公开时刻 |
| 行情及公司行为 | Alpaca 分页任务接口及原有标准化器 | 无账户/数据 entitlement 时是依赖缺口，不能报告真实接通；IEX/SIP 等覆盖分开 |
| 其他市场/商业预测/研究材料 | 来源接口、依赖登记、授权人工导入 | 无适配实现或授权就保留缺口，不以文档中列网站名代替接通 |

注册表中的 `implemented` 只是存在代码。模拟测试通过、真实响应接通、独立字段核验、资料完整性是四个不同维度，以运行数据库与验收报告为准。

## 2. 代码边界

| 文件 | 职责 |
|---|---|
| `equity_data/universe.py` | 当前目录、显式 CIK、CSV 证券池及身份映射证据 |
| `configs/source_registry.json`、`equity_data/registry.py` | 来源能力/市场/费用/依赖/状态、动态加载适配器 |
| `equity_data/adapters/base.py` | `Context`、统一适配器接口、通用 observation 入库 |
| `equity_data/adapters/sec_source.py` | SEC 查询与资料枚举、原件任务、标准财务 |
| `equity_data/adapters/ir_source.py` | 官方入口证据、静态链接与公开文档 |
| `equity_data/adapters/open_sources.py` | RSS/Atom、Federal Register、BLS |
| `equity_data/adapters/core_sources.py` | 日历、FRED、Alpaca 与明确依赖边界 |
| `equity_data/parsers.py` | 无发行人专属答案的本地通用解析 |
| `equity_data/queue.py`、`equity_data/engine.py` | 持久任务、租约、断点、重试、预算中止、覆盖状态 |
| `equity_data/http.py`、`equity_data/budgets.py` | HTTP、原始响应、限速、缓存、重试与预算 |
| `equity_data/store.py` | 原件 SHA-256、只追加记录、版本关系、历史时点查询 |
| `equity_data/public_docs.py` | 公开文档清单兼容入口及独立授权人工导入 |

## 3. 适配器契约

```python
class ExampleAdapter(BaseAdapter):
    source_id = "example_official"
    scope_kind = "company"  # 或 "global"
    data_types = ("documents", "financial_facts")

    def discover(self, ctx, scope):
        # 使用源提供的 ID 和已发现的地址，不写发行人答案。
        ctx.emit({
            "source_id": self.source_id,
            "resource_key": "listing:" + scope["company_id"],
            "kind": "listing",
            "data_type": "documents",
            "url": source_disclosed_url,
            "company_id": scope["company_id"],
            "metadata": {"refresh": True, "start": scope["start"], "end": scope["end"]},
        })
        return {"enumeration_complete": True, "discovered": 1}

    def parse(self, ctx, resource, content, raw):
        # 列表/索引可 ctx.emit() 下一页、正文和附件。
        # 正文可调用 parse_document()，随后 persist_observations()。
        # 尚未披露/无法解析/缺权限应分别报告，不能统一返回空成功。
        ...
```

示例中的 `source_disclosed_url` 是源发现结果，省略号表示适配器作者必须实现，不能原样运行。

| 方法/对象 | 输入与返回 | 规则 |
|---|---|---|
| `discover(ctx, scope)` | 返回 `enumeration_complete`、`discovered`、可选 `gaps` | 先发资源清单。结果只说明本次枚举阶段，子页/下载仍可能未完成 |
| `backfill(ctx, scope)` | 默认调用 discover | 相同范围的稳定 generation 去重；不得用隐藏样本数量限制代替历史枚举 |
| `incremental(ctx, scope)` | 默认调用 discover，新 generation | 重新检查列表/原件变更；修订旧期间时须确保日期配置覆盖修订对象 |
| `acquire(ctx, resource)` | `(原始 bytes, 已持久 raw 字典)` | 默认 `ctx.client.fetch()`，认证只能在这里读取环境变量；不得把密钥放 metadata |
| `parse(ctx, resource, content, raw)` | `status`、`record_ids`/`parsed_count`、`errors`、`coverage` | `parsed/partial/not_disclosed/not_applicable/source_conflict` 各自具有含义。解析失败前原件已打检查点 |
| `health(ctx)` | 依赖、实现能力、限制 | 默认不联网；没有原始获取证据不得声称 connected/verified |
| `ctx.emit(resource)` | 返回稳定 resource ID | 发下一页、附件和正文；同来源相同 key 幂等；不直接改队列表 |
| `ctx.gap(code, message, ...)` | 来源、公司、数据类型、状态、details | 每项缺口须给出原因和可执行依赖/续跑线索 |

`resource` 必需字段为 `source_id/resource_key/kind/data_type`，其余为 `url/company_id/security_id/metadata`。`resource_key` 体现真实业务/来源身份，不能包含获取时间或最终财务值来规避版本比较。`metadata.params` 只保存不含凭证的请求参数；时间证据、父资源、原文定位和 immutable archive 声明也在 metadata 中。

`DependencyMissing` 表示凭证/权利等不可执行依赖；`UnsupportedCapability` 表示该来源/类型当前无法实现。HTTP 或解析失败交给调度记录，不能捕获后假报完整。`BudgetExceeded` 交回 engine，未执行任务进入 deferred/pending，而非 not_disclosed。

## 4. 通用 parser 的实际边界

`parse_document(content: bytes, media_type=..., document_type=..., company_id=..., source_url=..., context_documents=None)` 返回 `parser_version/observations/issues/coverage/status`。解析不联网，也不下载 taxonomy。开源 lxml 用于 HTML，本地 pypdf 用于 PDF 文本；原始字节保留不依赖解析成功。

每条 observation 带 `dataset/record_key/data/source_locator/evidence_excerpt/period_start/period_end/event_date/unit/currency/knowledge_type/quality`。数值使用十进制字符串，避免浮点舍入。哈希与 parser 版本同时写入 data，入库时还连接 raw_id 与 retrieval_id。

### inline XBRL

支持 `ix:nonFraction`，读取 `contextRef/unitRef/name/scale/sign/decimals/precision`，保留 instant/duration、entity identifier、显式 dimension/member、typed dimension 原 XML、分子/分母单位、QName 原名与可解析命名空间。`comparable_metric=null`、`definition_status=taxonomy_not_loaded` 是主动保留的语义边界。

可处理 canonical decimal、官方 XBRL transformation 命名空间中的 dot/comma-decimal、dash-zero，以及 2020/2022 注册版本明确规定的 fixed-zero（任意输入文本映射到零，仅在源明确指定该变换时使用）。未知变换命名空间/变换名、缺 context 或 unit、异常倍率/精度、同一文件内重复 context ID、fraction 等会拒绝输出该数值并报告 `IXBRL_NUMERIC_REJECTED`。同语义 context 和 unit 出现不同值，输出冲突候选；尚未做 XBRL rounding-consistency 或完整 calculation-linkbase 校验。

原始数字从标签文本拼接，`ix:exclude` 不算数值。`scale` 确实作用于数值，`sign` 明确改变符号。nil 保持 null；不把破折号无条件转零。空 nil/fixed-zero 以原始标签片段作为证据。当前不自动解释非数字 taxonomy 定义、扫描 OCR 或任意附注表格。

同一 inline XBRL document set 可以把 context/unit 放在主文档、数字放在附件。`context_documents` 接受已保存原文的 `{content: bytes, raw_id, sha256, source_url}` 数组（url 为兼容别名）。SEC adapter 只从**同 accession** 已保存 HTML 里选择补充，无隐式联网。parser 重新核验所有摘要，只共享 context/unit，不重复输出补充文件中的数字；不同文件中同 ID 相同定义可复用，冲突则拒绝。每条数字保存 context_evidence/unit_evidence 的原件 ID、摘要、URL、定位，supporting_raw_ids 记录实际依赖；SEC 入库另将这些原件连接 record_evidence。原件没下载仍然是缺口，不能跨 accession 借用上下文。

### 指引

通用规则识别 revenue、sales、net income、operating income、free cash flow、gross/operating margin、EPS、capital expenditures 等**原始指标名称**。同一段落，或明确 Outlook 目标期间标题与该段落的组合证据，必须同时给出期间、单位和可解释金额。多个期间、多个指标、缺单位、不能解释的附加数字或容差都返回问题，不能只抓一个数字假报成功。

v2.0.3 起，通用段落规则**拒绝所有来自 HTML 表格的数值指引**，包括简单表格、嵌套段落、嵌套表、合并单元格和表格外标题。问题为 `GUIDANCE_TABLE_CONTEXT_UNSUPPORTED`，保存完整表格原文/定位与该行片段。原因是展开单行会丢失其他行的 million/billion 倍率、segment 范围、列标题、近似符号和条件，不能仅凭该行的货币符号保证口径完整。只有未来能同时核验这些要素的独立表格适配器才能输出数值。表格的期间/分部标题也不会继续影响表格结束后的普通段落。

此边界来自一轮真实泛化检验发现的错误：旧 v2.0.2 曾把表格单元格读作基础货币点值，遗漏表头倍率/分部/限定条件。原始失败与旧解析记录须保留并标为不可用于研究；不能把修复后的重复验证冒充首次未见样本全部通过。新的最终泛化测试必须采用另一组未参与调试的证券。

示例：`For fiscal 2028, revenue is expected to be USD 30 billion, plus or minus 2%.` 会保存 point=30000000000、tolerance=2 percent 和原始 fiscal 2028 标签。不会擅自把 fiscal 2028 换算为日历年。明确 quarter ending 日期才写入 period_end。不会自动把 point±tolerance 派生为源直接披露的上下界。单独 `$` 保留 `unit=dollar_symbol/currency=null`，不凭市场猜 USD；`USD/US$` 可明确识别。

源数字、million/billion、币种、目标年或容差改变，必须重新解析为新值或拒绝。源字段在新版本缺失/不可解析时，通用入库器为先前自动输出写入 `not_emitted_by_current_parser` 状态，旧值不能继续伪装为当前结果。这是解析可用性状态，**不是公司撤回原披露的事实判断**。

### 叙述与公司特有字段

客户集中、供应商、承诺、债务到期、或有事项、风险和分部表述先保存原文证据候选。`counterparty_name=null/numeric_values=null` 不代表源没有信息，只代表没有完成可验证的实体/数字结构化。`semantic_type` 表明候选类别，`verification_status=requires_review`。不得把它计入已核验关系或已核验财务数准确率。

候选按语义 datasets 保存：客户/供应链 → relations，承诺 → commitments，到期债务 → debt_maturities，或有事项/风险 → risk_disclosures，分部 → operating_metrics。拥有这些候选不等于承诺金额、债务期限表或经营指标已完全结构化；需要按 quality 和 extraction_method 区分。

PDF 仅有提取文本时才能形成片段；`page:N/text-block:M` 保留页码。无文本页报告 `PDF_NO_TEXT`，不虚构 OCR 结果。当前 PDF 文本块/多指标表格可能拒绝数值，应由文档格式适配器逐步扩展。

## 5. 新增来源、市场与数据类型

1. 先核查官方文档、真实响应、认证/存储授权、历史/退市覆盖、分页/限速、修订与时间精度。记录一个真实原始响应才能进入真实接通验收。
2. 在独立 adapter 模块实现上述方法。清单页全部枚举；页/文件数量预算由执行器控制。遇来源结果上限应细分日期或保留未完成范围，不能丢尾页。
3. 在来源注册表或配置 `source_plugins` 加 `source_id/markets/data_types/adapter` 以及来源限制、身份认证、费用、历史和修订说明。`adapter` 格式是 `包.模块:类名`。未知市场不可借用 US 规则。
4. 为新市场分别实现稳定身份、交易日历、时区、生效规则、金额和证券单位、监管与行情来源。只有日历能跑不等于该市场全采集已经支持。
5. 新数据类型先确定语义、键、单位、时点规则和 raw 定位；再扩展 Store 的 DATASETS 视图、import 白名单（如适用）、注册表、报告和字段字典。动态公司字段保留原名/定义/维度，语义证据不足不得映射统一可比指标。
6. 使用通用 synthetic fixtures 覆盖分页、失败隔离、预算截断、增量修订、重复执行、缺字段和金额/单位/期间变异。冻结代码后再以未参与调试证券/文档作真实独立核验。

插件配置示意（Python 包必须已在运行环境中安装）：

```toml
[[source_plugins]]
source_id = "my_official_source"
markets = ["US"]
data_types = ["documents"]
adapter = "my_package.source:MyOfficialAdapter"
dependency_reason = "按该源合同配置凭证"
```

字段字典见 [FIELD_DICTIONARY_V2.md](FIELD_DICTIONARY_V2.md)。添加来源不需要改引擎；需要改变解析语义时升级 parser_version，使用离线重新解析，并保留旧结果/原件。

## 6. 授权人工入口

`public_docs.collect_public_documents` 对含 `records.data` 的旧清单输出 `PREFILLED_MANIFEST_REJECTED`，原件仍留存；关键词相符不再使预填答案成为自动结果。应改用独立 `import-materials` JSONL：每条必须给 rights、classification、knowledge_type、来源/定位、原文文件、授权说明，调查另须 sample_scope/methodology，推断另须 method/input_record_ids。

人工结果为 `manual_import_unverified`，不得计入自动抽取成功数。只有原文证据能支持的字段才可导入；未知时刻保持 unknown。专家意见、调查估计、系统推断和公司陈述分别标识。泄露/保密材料不进入该入口。

## 7. 如何判断完成

应同时检查 inventory（应获取资源）、tasks/attempts（执行）、resource_versions（原件/解析版本）、records（输出）、gaps（缺口）、source_health（健康）。单个任务 succeeded 只说明这项工作执行过，不能推导所有文章、所有附件或全部字段都已完整。

官方规范参考：[SEC APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[Inline XBRL 1.1](https://specifications.xbrl.org/work-product-index-inline-xbrl-inline-xbrl-1.1.html)、[Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1)、[BLS API v2](https://www.bls.gov/developers/api_signature_v2.htm)。实现是这些协议的明确子集；不能以规范存在推导当前代码已支持全部规范。
