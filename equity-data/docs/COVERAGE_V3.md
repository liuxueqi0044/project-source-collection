# V3 独立参考覆盖核验

## 两套清单

采集队列列出机器已发现的资料；参考清单列出由另一段程序或审核人员直接对照原文建立的应有资料/目标字段。后者不能读取生产解析结果来生成期望答案。两者相比较才有覆盖分母。原文中没有发现目标，不等于公司未披露。

`scripts/build_reference_v3.py` 只读取 raw_blobs/raw_retrievals，重新读取 SEC submissions 列表和 SGML 文件目录。原文 iXBRL 按固定哈希顺序抽取，使用 BeautifulSoup 与独立 Decimal 检查程序重新解释数值、期间、单位和维度；不调用生产解析器，也不从已入库财务记录挑样本。

这属于**独立实现、共享官方来源**，不是独立供应商。尚未下载的历史列表页、未取得的申报目录可能使参考清单不完整；报告明确披露这一点。iXBRL 抽样也不覆盖全部未标记表格、指引或关系。其他类型可按下述合同人工建立原文参考清单，人工答案仅用于验收，不进入自动事实数据集。

## 操作

```powershell
python scripts/build_reference_v3.py --storage data/v3_holdout --start 2024-01-01 --end 2024-04-30 --out reports/v3/reference.json
python -m equity_data --config configs/v3_holdout.toml coverage --reference reports/v3/reference.json --out reports/v3/coverage
```

参考清单应在评分前冻结并保存 SHA256。要检验新代码的泛化，先冻结生产代码，再选择未调试股票/文档；如据样本修复代码，该样本改为开发样本，需重新选择最终留出样本。

## 参考合同

顶层：`schema_version=coverage-reference-1`、`reference_provenance.method`、`uses_collector_outputs=false`、`scope`、`limitations`、`documents`、`fields`。

每项有唯一 `reference_id`。`evidence` 含 `raw_id, sha256, locator, url`，哈希必须对应实际保存字节。支持文件放 `support_evidence`，也须通过哈希检查。

资料参考含 `source_id, company_id, data_type, url`；来源原文清单中的定位是证据。字段参考含 `source_id, company_id, dataset, selector, expected, tolerances`。selector 是字段路径（例如 `data.concept_qname`、`data.context_id`）与原始标识；expected 可核验 `data.value`、`unit`、`period_end`、`data.dimensions` 等。数值容差必须由审核者明确给出，不在核验失败后放宽。`reference_status=unverified` 表示参考程序自身不支持变换或原文有歧义。

事实状态：`extracted_correct`、`extracted_mismatch`、`explicitly_rejected`、`candidate_only`、`silent_omission`、`reference_unverified`。拒绝必须对应同一原文版本及目标定位；其他位置的一条错误不能掩盖本字段漏采。候选片段、人工导入和参考未核实均不计自动正确提取。`target_locator_unresolved` 表示同类事实存在但目标位置未匹配（可能遗漏或独立 HTML 解析定位差异），不能算成功；`manual_only` 和 `extraction_provenance_unknown` 也不计自动成功。重复概念/context/unit 的另一个出现位置不能替代选定目标。

分母为所列参考目标数量。没有参考目标时百分比为 null；不能解释成 0% 或 100%。资料有旧原文但最新更新失败时，downloaded 与当前状态同时保留。字段对照按原文版本核对，而不是让不同日期的数值互相替代。

## 采集结束后的报告口径更正

V3 真实采集和数值解析结束后，发现资料 `success_numerator` 原按 `parsed/partial_parse/downloaded_unparsed` 状态相加；新 generation 把库存状态重置为 `discovered_not_downloaded` 时，已有原文还在，却被从成功分子扣掉。反过来，只有 `parsed` 标签而没有文件也可能计成功。修复前代码与失败复现保存在 `reports/v3/coverage-before-reporting-fix.py`、`coverage-reporting-fix-before.txt`。原采集冻结核验保存在 `freeze-before-reporting-fix.json`；本修正只改变报告计算，没有重采、重解析或修改数据库，也不声称所有交付代码仍与首次冻结相同。

资料总计及逐来源/公司/类型分组统一采用 `success_basis=downloaded_has_saved_raw`：匹配资料有原文指针、存在对应原文文件且 SHA256 正确，才计入 `downloaded` 和 `success_numerator`。`success_rate=success_numerator/reference_denominator`；没有参考项仍为 null。文件检查按 raw_id 缓存，只读，不向数据库写入修复或核验结论。

`outcomes` 和 `workflow_state_counts` 保留当前工作流状态，不参与原文已取得分子的推断；每份资料返回 `saved_originals` 的文件验证状态。已有原文且当前更新待办/失败，仍算已有原文，但 `latest_requested_version_downloaded=null`，不能说新版本已经下载。来源是否还有尚未发现的更新、当前请求是否最终完成和字段解析是否完整，需要另看任务与版本证据。状态标成 `parsed`、但原文文件未保存、遗失或哈希错误时，原文取得分子为零。

## 时间和运行状态

机器报告新增 `company_type_freshness`，按来源/公司/资料类型列出最旧及最新获取、来源声称的发布日期范围、未知发布日期数量。获取新文件不会刷新其他公司的状态。

`availability_to_acquisition_max_seconds` 可能是多年历史回填时差、保守可用时间假设，不是服务延迟 SLA。只有连续监控中独立确认真实发布时间与首次到达时刻，才能测量更新延迟；没有这类证据时 `disclosure_update_delay_seconds=null`。

任务结束、解析结束、字段正确、参考范围覆盖完成必须分开报告。任何一个都不能推出全市场、全部资料或全部历史版本已覆盖。
