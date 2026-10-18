# SEC V4：原始申报、支持文件与重复处理

此升级保持公司无关的规则。原始发现问题、源码快照及最终修复后证据分别保存在 `reports/v4/sec-original-xml-before.json`、`sec-source-before-v4.py`、`sec-original-xml-after-final.json`。较早的 `sec-original-xml-after.json` 保留为开发中间阶段证据。三个合成申报类型使用不同文件名；没有在生产代码内设置证券、文档 URL 或答案。真实补获的原始 XML 及其官方清单声明另存 `sec-original-xml-real.json`。

## 原始 XML 与展示文件

Submissions 的 `primaryDocument` 可能指向 XSL 展示路径，申报 header 清单的 `TYPE` 等于本申报表单类型的文件仍是应采集的原始文件。适配器获取所有这样的清单文件，即使它们未命中 `attachment_types` 或 `support_document_types`。展示路径和原始文件各有独立 URL、原文哈希及任务；获取其中之一不等于获得另一个。清单中的其他文件仍服从明确配置范围。

原始 XML 保存成功不代表所有 XML 表单已完成语义提取。所有权、持仓及其他 XML 文档的字段合同和解析覆盖仍应单独核验，不能以 XML 下载计为结构化事实成功。

## 原文复用与修订检查

SEC accession 档案默认复用已保存原文，保留原来的获取时间。新发现 accession 或未获取文件仍需要网络请求。Submissions 和 Companyfacts 等可变 API 快照在新一轮更新中重新请求。

`--full-history` 或配置 `revision_refresh = true` 会刷新 accession 档案，以检查同一 URL 的源文件变化。`watch` 的周期性 full-history 轮次使用相同机制。普通缓存轮次的状态必须显示来源未重新检查，不能推进“最后向来源核验时间”。如果源站悄悄修订档案，发现延迟受修订检查周期影响。

## 解析缓存的边界

仅完整已保存输入一致时，engine 可以复用上一次解析结果。SEC 提供的指纹包含原文、公司/证券、文档类型、报告期间、有效时间证据、支持原文集合、适配器与解析器版本及源代码哈希。Companyfacts 指纹还包含日期范围及已保存 header 证据。发现/索引节点不缓存解析结果，因为每轮仍须枚举子资源。新增或变化的解析结果不因旧版本曾经出现过而直接复用。

该指纹仅对已经调度的解析任务生效。同一 generation 的已完成 `backfill` 保持幂等，并不会因为安装了新版解析器自动唤醒全部旧任务。升级解析器后应执行 `update` 或显式 `reprocess`；后者保证真正重解析，不能用零执行的重复回填冒充新解析器已验收。

同一申报中只索引该公司和 accession 的最新原文记录。对原文内容的哈希校验、context/unit ID 解析在一次进程内复用；文件大小/修改时间/元数据发生变化会重新读取并核验哈希。支持声明原文集合变化会唤醒该申报受影响的已保存文件，依赖记录与任务仍保存在数据库。原文 A→B→缺失→B 的回归确认恢复不会因旧 B 缓存而沿用被撤回事实。

## 完整固定范围验证

`scripts/validate_sec_repeat_v4.py --clone` 使用只读 SQLite backup 和 raw 文件复制建立 `data/v4_sec_repeat`，不修改 `data/v3_holdout`。随后分别运行命名轮次：

```powershell
python scripts/validate_sec_repeat_v4.py --label 01-complete --command backfill
python scripts/validate_sec_repeat_v4.py --label 02-repeat --command backfill
python scripts/validate_sec_repeat_v4.py --label 03-incremental --command update
python scripts/validate_sec_repeat_v4.py --label 04-revision-check --command update --full-history
```

固定范围为 SBUX、HON、SAP，2024-01-01 至 2024-04-30，SEC 渠道和 V3 配置的所有数据类型及表单/附件范围。使用真实证券池解析，因此每轮证券目录请求应单独列示，不能与 SEC 档案重复下载混为一谈。资源控制中断时使用命名 `--command run` 续跑，报告未完成任务，不得把截断当作全范围完成。

每轮报告保留运行时长、实际请求/字节/费用预算、原始文件和获取记录增量、业务记录数量与身份哈希、任务/资源状态及代码快照。此固定证券池属于开发验证；它不是最终冻结后的未调试泛化样本。

冷重解析仍有性能限制：只读 `EXPLAIN QUERY PLAN` 确认，旧字段核验会扫描 `records` 并查询 JSON 中的 resource key；写入事实时按原文/来源查获取记录也未使用覆盖该查询的完整索引。本轮未更改这些底层持久化查询。支持原文扫描优化和解析缓存不能据此被表述成冷重解析已足够快；实际耗时以逐轮报告为准。合成声明扫描基准仅证明那一类重复扫描减少，不代表全市场吞吐能力。
