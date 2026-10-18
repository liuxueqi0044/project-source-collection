# SEC 文档集依赖：V3

同一申报可能把 `ix:nonFraction` 数字与其 `contextRef`、`unitRef` 声明分开放在主文件和附件中。V2 可以使用已到达的同申报支持文件，但支持文件后到时不会自动重解析先到的数字文件。V3 在本地 SQLite 持久记录这些依赖，并按支持原文版本变化生成离线重解析任务。

## 修复前证据

`reports/v3/dependency-before.json`、`dependency-before-tests.txt` 及 `dependency-before-data/` 保存修复前的失败。输入是合成申报，数字文件在前、声明文件在后；先执行到数字文件，再关闭数据库连接，重新打开后续跑。支持文件下载并完成解析后，财务事实仍为零，预期的 `9000` 没有输出。输入只用于回归测试，不在生产规则或股票池中。

`scripts/reproduce_dependencies_v3.py --phase before|after` 使用同一复现流程，保存生产代码哈希、任务、原文数据库、未解决缺口及运行结果。脚本拒绝覆盖已有证据文件。

## 处理流程

1. SEC 申报索引枚举主文件和配置允许的附件；默认附加枚举来源清单明确列出的 `XML`、`EX-101.INS` 文档集支持候选。它们是否含有上下文需要读取原文确认，不代表已完成财务解析。
2. 数字文件解析时，从原文读取上下文和单位引用；按公司 ID、申报 accession 筛选本地已保存的支持文件。不同申报和不同公司不能借用同名 `c1`、`u1`。
3. 所有支持原文先核验 SHA-256；只把包含所需声明 ID 的 HTML、XHTML 或 XML 作为候选。上下文语义冲突仍由解析器拒绝，不能仅因 ID 存在就视为有效数字。
4. 持久保存依赖、缺少的声明 ID、支持原文哈希以及已经处理的支持集合指纹。缺失时输出 `SEC_DOCUMENT_SUPPORT_MISSING`，原数字的解析拒绝也继续保留。
5. 同申报文件取得并解析完成后，检查受影响的最新原文依赖。支持集合变化时，通过 `Queue.enqueue_reprocess` 为确切原文和获取记录创建重解析任务，不重新请求数字文件。
6. 相同支持集合不会反复生成任务。支持发生 A→B→A 变化时，新的消费阶段可以再次调度；多次收到同一未消费变化通知会合并为一个任务。
7. 任务和依赖均保存到 SQLite，重启后可继续。资源预算仍有效，预算耗尽时重解析任务保持待办，不能报告覆盖完成。

主文件先到、支持文件先到、支持下载失败后恢复、声明被修订或移除等情况均作为独立回归场景。数值记录的 `supporting_raw_ids` 及 `record_evidence` 指向实际使用的原文版本和获取时间。

支持声明消失后，旧数字会被拒绝并产生“当前未输出”状态；支持恢复时追加新的解析决定，不复活旧的被拒绝记录。新决定保留原公开时间字段，但 `available_at` 采用 `first_observed_parser_field_recovery` 的保守恢复时点（至少为实际恢复处理时刻）；重复解析保持该时间下限，不能把恢复决定回填成原申报日即已可用。

## 数据合同

`sec_document_dependencies` 是运行依赖表，不是股票事实表，也不是历史发布时间证明。

| 字段 | 定义 |
| --- | --- |
| resource_id | 需要上下文或单位的数字文件，对应 machine_inventory |
| raw_id / retrieval_id | 这次依赖属于哪份原文及哪次取得记录 |
| company_id / accession | 唯一允许使用的公司、申报文档集范围 |
| references_json | 原文实际出现的 contextRef、unitRef ID 集合 |
| consumed_fingerprint | 最近一次处理的支持集合内容指纹 |
| supporting_raw_ids_json | 相关支持原文内容地址；最终事实可只使用其中一部分 |
| missing_json | 本地仍未取得声明的 ID 集合 |
| state | missing_support 或 declarations_available；后者不代表数字已核验 |
| parsed_at | 系统处理依赖的时间，不是公开发布时间 |

每份文件解析结果 `coverage.document_set_dependency` 返回上述引用、缺失、支持集合和指纹。`SEC_DOCUMENT_SUPPORT_MISSING` 按确切资源与原文关闭，旧失败仍保留 `resolved_at`。

## 配置

```toml
[sec]
attachment_types = ["EX-*"]
support_document_types = ["XML", "EX-101.INS"]
```

可以显式设 `support_document_types = []` 关闭额外支持候选。由于权限、文件清单缺失、用户排除或下载失败造成的依赖缺口仍会报告，系统不会自行猜测 URL 或借用其他公司的声明。默认范围并不涵盖所有 SEC 二进制、图片、完整 taxonomy 网络或任意文档格式。

## 边界和证据性质

- 本文的到达顺序、断网恢复与更正场景属于合成集成测试；不能宣称实际连续生产运行中已经观察到了这些事件。
- 文档原始数字、支持声明、获取时间与公开时间分别留存。文件后到不等于当年未公开；同 accession 字节更正也不能自动沿用原文件的精确公开时刻。
- 历史版本批量重解析由引擎的历史处理模式完成；自动依赖唤醒只针对资料的最新原文，避免改变最新库存指针。历史审计记录保持独立。
- `declarations_available` 只证明找到候选声明。定义有效性、源内冲突、单位、数值转换和字段准确性仍需解析与独立核验。
- 本功能修复的是已枚举文档的解析依赖完整性，不能证明整个监管渠道、所有文件类型或全部投研资料已经完整覆盖。
