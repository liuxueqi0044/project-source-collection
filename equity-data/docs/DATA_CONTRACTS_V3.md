# V3 语义数据合同

合同版本：`financial-semantic-v1`。生产实现：`equity_data/contracts.py`；文档解析版本：`generic-document-v3.0.1`。

本合同不把片段召回当成事实提取，不把结构化结果当成已核验结果。公司参数和来源网址不决定解析规则；没有公司名称、最终数值或文档 URL 例外表。

## 四层数据

| 层级 | 保存形式 | 可计为结构化事实 | 规则 |
|---|---|---|---|
| 原文 | 现有原文文件、SHA-256、raw_id、获取记录 | 否 | 原始字节不因解析器升级而改变 |
| 候选片段 | `data.fact_stage=candidate` | 否 | 只证明原文存在相应文字，不证明关系、金额和期间已解析 |
| 已提取事实 | `data.fact_stage=extracted_fact` | 是，但独立核验状态另列 | XBRL 标签/上下文/单位，或受支持的指引跨度与表格；未知语义保留原因 |
| 统一指标 | `normalized_metric` | 需单独语义映射证据 | 当前文档解析器不自动生成；不得按自定义标签名称强行对齐 |

每个已支持观察记录附加 `data.semantic_contract`，现有顶层字段继续兼容。`quality=parsed_unverified` 不因为获得数据合同就升级为 verified。

## 公共字段

| 字段 | 含义 |
|---|---|
| `version` / `family` / `stage` | 合同版本、语义类别、候选或事实层级 |
| `applicable_entity` | XBRL 明示的实体标识；非 XBRL 默认仅指文档主体，标识未知，需来源适配器保留公司关联 |
| `applicable_period` | XBRL 期间或原文指引期间标签；没有明确财政日历就不推造截止日 |
| `unit` / `currency` | 金额、每股、百分比等；单独 `$` 保留 dollar_symbol，不猜 USD |
| `accounting_basis` | 原文明示的 GAAP/non-GAAP，或未知；标准 taxonomy 的名称本身不是会计口径证据 |
| `dimensions` | 原文 XBRL explicit/typed dimensions；未识别的叙事维度不补全 |
| `unknown_reasons` | 每个尚未确定语义字段的缺失原因 |
| `evidence` | 原文哈希、原文定位、解析版本；证据摘录仍在原有字段 |
| `normalized_metric` / `normalization_status` | 当前均为空 / not_mapped；未来映射必须给出定义等价性证据 |
| `verification_status` | 与顶层 quality 对应；从提取流程中独立 |

公开时间、可用时间、获取时间和修订关系仍由适配器与存储层提供。文档解析器不从报告期生成发布时间。

SEC Companyfacts 的整份快照会因无关事实变化而改变哈希。为避免把整份快照的新哈希误当成每条旧事实的修订，Companyfacts 合同使用精确定义的 `content_sha256_reference = "records.raw_id -> raw_blobs.sha256"`。原文哈希仍由记录关联原文取得，不能写成任意不明文件引用。文档解析结果继续直接带原文哈希。

## 分类别合同与当前能力

| 类别 | 事实应具备的语义 | V3 实际状态 |
|---|---|---|
| 财务指标 financial_facts | 原始 QName、数值/源 nil、上下文期间、实体、单位、维度、精度、口径及标签定义 | 受支持 iXBRL 数值可提取；完整 taxonomy、跨公司等价性与口径还未建立；未知显式记录 |
| 指引 guidance | 原始指标、目标期间、点值/上下界、容差、单位、适用主体、口径、条件、原文金额 | 明示段落及有限矩形表格可以提取；负数与括号负数保留；其他结构明确拒绝 |
| 经营指标 operating_metrics | 原始名称、定义、值、单位、期间、实体、产品/地区等维度 | 叙事匹配仍为候选；自定义 iXBRL 以原始财务事实保存，不能据标签名冒充已完成经营语义分类 |
| 客户/供应商 relations | 主体、关系类型、对方身份/明确匿名、适用期、集中度及确认级别 | 当前叙事仅候选；不会把“one customer”解析成客户身份，也不确认具体供应关系 |
| 债务 debt_maturities | 债务人、工具、本金、币种、到期日/区间、利率条款和口径 | 当前叙事仅候选；原始 iXBRL 值另存 financial_facts，未自动拼成工具级债务时间表 |
| 承诺 commitments | 义务主体、承诺类型、金额、币种、支付期间、可取消性、适用范围和条件 | 当前叙事仅候选；不把一句 purchase commitments 当成金额结构完成 |

候选的 `unknown_reasons` 会列出未解析的必需语义。合同描述未来事实应具备什么，并不宣称每一类别都已完成自动结构化。

## 表格指引支持边界

支持矩形表格：第一列为单一已支持指标，随后为一列或多列预测；`th` 列头提供明确期间、币种及 million/thousand/billion 倍率。单预测列可使用紧邻的指引标题提供期间，单元格也可自带金额单位。每条结果保存标题、列头、指标单元格、数值单元格的原文定位和原文文本。

已支持：负数、括号负数、跨零区间、金额/百分比、EPS 每股、明确点值/区间/容差、近似限定词、不同期间列顺序，以及链接到同文档唯一 ID 的受限假设脚注。脚注必须以 Assumes/Assuming/Subject to/Based on 开始，且不能改变单位、倍率或排除口径；脚注全文保留在证据组件中。

`at least` 等不等式保存为单侧 low/high，point 为空，`value_semantics` 标明上下界，`bound_inclusivity` 标明包含与否；约数保留 approximate_point 和原限定词。列头 GAAP/non-GAAP 会传入事实口径；与行口径冲突时拒绝。标题含未解析地区、主体或范围时拒绝，不把该范围默认为公司整体。

拒绝并报告：合并单元格、嵌套表、分部范围不明、列期间歧义、冲突币种/倍率、无法找到的脚注、可能改变数值口径的脚注、无单位、未支持指标。尚未支持任意表格、PDF 表格语义、OCR、完整脚注推理。矩形表格规则没有覆盖所有真实发行人。

V3.0.1 还拒绝含显式隐藏元素/行、隐藏样式及可能影响该表格的内嵌 CSS 隐藏选择器的表格。不尝试把隐藏负号剥离后宣称数值已可靠解释。百分比或基点不能与金额币种/倍率组合；loss/negative/deficit 等语义负值用词目前明确拒绝，不猜正负。历史、实际结果或无关正文打断指引标题继承，并保留中断证据，避免把后文实际数值套入旧指引期间。

XBRL 的隐藏事实另按明确标签、context 和 unit 解析，不能因不可见而删除。HTML 文本清单只应用显式静态可见性，不是浏览器计算后的完整视觉结果；外部 CSS、脚本渲染和未知复杂样式未全面支持。

内部可把证据明确提供的单位合成解析跨度，但输出的 `evidence_excerpt` 和 `evidence_components` 始终来自原始单元格与标题，不用内部合成句冒充原文。`source_amounts` 是解析工作字段；核验时应使用 `original_value_cell` 和逐组件证据。

## HTML 可见文本清单

`parse_document` 新增 `text_blocks`：每项含 `source_locator`、`text`、`kind`。混合 p/div/table、正文容器直接文本和子元素尾文本均进入清单，不重复把子段落算进父容器。多段容器文本使用 XPath 后缀 `/text-run:N` 表示原文顺序中的第 N 个文字段。该清单是解析器选取结果，不能用自己当作独立覆盖率分母。

生产覆盖计数新增 `coverage.fact_stage_counts`，仅说明当前输出各层数量。字段覆盖率必须来自原文优先建立的独立参考清单；`guidance_candidates` 是规则触发计数，不是文档全部应有指引字段数。
