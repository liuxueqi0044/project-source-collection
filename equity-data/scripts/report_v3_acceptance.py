"""Assemble evidence, preserving separate software/coverage/semantic verdicts."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import hashlib,json,sqlite3,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/v3'
def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))

def freeze_changes(freeze):
    # Include new and removed production files, not only keys in the old list.
    paths=sorted((ROOT/'equity_data').rglob('*.py'))+[ROOT/'configs/source_registry.json',ROOT/'pyproject.toml']
    current={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
    original=freeze['production_hashes']
    return sorted(name for name in set(original)|set(current) if original.get(name)!=current.get(name))

def regression_evidence(freeze):
    paths={key:'reports/v3/'+value for key,value in {
        'parser_before':'parser-before/results.json','parser_after':'parser-release/results.json',
        'sign_before':'parser-before-additional/results.json','sign_after':'parser-release-additional/results.json',
        'edge_before':'parser-edge-before/results.json','edge_after':'parser-edge-after/results.json',
        'dependency_before':'dependency-before.json','dependency_after':'dependency-after.json',
        'runtime_before':'runtime-before.json','runtime_after':'runtime-after.json'}.items()}
    loaded={key:read(path) for key,path in paths.items()}
    before={case['case']:case for key in ['parser_before','sign_before','edge_before'] for case in loaded[key]['cases']}
    after={case['case']:case for key in ['parser_after','sign_after','edge_after'] for case in loaded[key]['cases']}
    def same_case(name):
        b,a=before.get(name,{}),after.get(name,{})
        return bool(b and a and b.get('raw_sha256')==a.get('raw_sha256') and
                    b.get('expected_points')==a.get('expected_points') and
                    b.get('actual_points')!=b.get('expected_points') and
                    a.get('passed') is True and a.get('actual_points')==a.get('expected_points'))
    sign_names=['negative_ascii','negative_parentheses','negative_currency_parentheses','negative_before_currency','negative_before_dollar']
    b,a=loaded['dependency_before'],loaded['dependency_after']
    rawset=lambda result:{(r.get('url'),r.get('raw_id')) for r in result.get('resources',[]) if r.get('raw_id')}
    facts=a.get('facts',[])
    dependency=bool(b.get('correct') is False and a.get('correct') is True and b.get('expected_value')==a.get('expected_value') and
                    rawset(b)==rawset(a) and rawset(a) and len(facts)==1 and
                    facts[0].get('data',{}).get('value')==a.get('expected_value') and
                    facts[0].get('data',{}).get('supporting_raw_ids') and a.get('repeat_run',{}).get('executed')==0)
    runtime_before={c['case']:c for c in loaded['runtime_before']['date_precedence']}
    runtime_after={c['case']:c for c in loaded['runtime_after']['date_precedence']}
    date_names=['backfill_cli_overrides','update_effective_dates','update_cli_overrides']
    date_checks={name:bool(runtime_before.get(name,{}).get('passed') is False and runtime_after.get(name,{}).get('passed') is True and
                         runtime_after[name].get('observed',{}).get('scope_dates')==runtime_after[name].get('expected_scope_dates'))
                 for name in date_names}
    gates={'signed_guidance':all(same_case(name) for name in sign_names),'mixed_html_text':same_case('mixed_dom'),
           'late_support_reparse':dependency,'date_precedence':all(date_checks.values())}
    hash_differences={}
    for key in ['dependency_after','runtime_after']:
        diffs=[name for name,digest in loaded[key].get('production_sha256',{}).items()
               if freeze['production_hashes'].get(name.replace('\\','/'))!=digest]
        if diffs:hash_differences[key]=diffs
    edge_cases={name:same_case(name) for name in ['hidden_minus','hidden_row','percentage_monetary_scale','semantic_loss','stale_heading']}
    return {'passed':all(gates.values()),'gates':gates,'additional_safety_cases':edge_cases,'date_case_checks':date_checks,'source_files':paths,
            'after_artifact_code_differences_from_final_freeze':hash_differences,
            'evidence_scope':'Synthetic before/after reproductions; development-time artifacts may precede final freeze. Final software suite separately covers the final production code; none is live-event evidence.'}

def document_acquisition_summary(summary):
    result=dict(summary)
    denominator=summary.get('reference_denominator',0)
    states=summary.get('outcomes',{})
    result.update(success_semantics='Original acquisition is counted from saved raw evidence independently from current workflow state; it does not certify that the newest upstream version is held or parsed.',
                  original_downloaded=summary.get('downloaded',0),
                  original_download_rate=summary.get('downloaded',0)/denominator if denominator else None,
                  fully_parsed_resources=states.get('parsed',0),partial_parse_resources=states.get('partial_parse',0),
                  downloaded_unparsed_resources=states.get('downloaded_unparsed',0),
                  fully_parsed_resource_rate=states.get('parsed',0)/denominator if denominator else None,
                  parser_state_caveat='These are current workflow states. Re-enumeration can reset them while prior raw and parsed facts remain. They do not establish all-version or semantic-field parsing completion.')
    return result
def main():
    freeze=read('reports/v3/production-freeze.json')
    changes=freeze_changes(freeze)
    software_path='reports/v3/software-tests-post-reporting-fix.xml'
    xml=ET.parse(ROOT/software_path).getroot()
    suites=[xml] if xml.tag=='testsuite' else list(xml.iter('testsuite'))
    software={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ['tests','failures','errors','skipped']}
    coverage=read('reports/v3/holdout-coverage-corrected/reference-coverage.json')
    reference_manifest=read('reports/v3/holdout-reference-final.json')
    excluded_documents=reference_manifest.get('source_declared_outside_configured_scope')
    excluded_count=len(excluded_documents) if excluded_documents is not None else None
    numeric=read('reports/v3/holdout-audit/independent-field-audit.json')
    narrative=read('reports/v3/narrative-reference-comparison.json')
    public=read('reports/v3/public_channels/continuous-real-evidence.json')
    idempotency_path='reports/v3/bounded-idempotency/experiment.json'
    idempotency=read(idempotency_path)
    freeze_before_reporting_fix=read('reports/v3/freeze-before-reporting-fix.json')
    channel_path='reports/v3/channel-evidence.json'
    channel_evidence=read(channel_path) if (ROOT/channel_path).exists() else None
    network_recoveries=[{'source_id':source['source_id'],'matched_failure_observations':len(source.get('network_recovery_evidence',[])),
                        'distinct_recovered_urls':len({item['url'] for item in source.get('network_recovery_evidence',[])})}
                       for source in (channel_evidence or {}).get('sources',[]) if source.get('real_network_failure_recovery_observed')]
    regressions=regression_evidence(freeze)
    numeric_outcomes=numeric.get('field_outcomes',{})
    raw_outcomes=numeric.get('raw_integrity',{}).get('outcomes',{})
    temporal_outcomes=numeric.get('temporal',{}).get('outcomes',{})
    repeat_runs=idempotency.get('public_repeats',[])+idempotency.get('sec_repeats',[])
    idempotence_passed=bool(idempotency.get('bounded_passed') is True and len(repeat_runs)==4 and all(
        part.get('delta') and all(value==0 for value in part['delta'].values()) and part.get('zero_network_requests') is True
        for part in repeat_runs))
    acceptance_gates={'software':software['tests']>0 and software['failures']==0 and software['errors']==0,
        'frozen_production_unchanged':not changes,'four_regressions':regressions['passed'],
        'additional_parser_safety_regressions':all(regressions['additional_safety_cases'].values()),
        'bounded_offline_idempotence':idempotence_passed,
        'full_SEC_backfill_idempotence':False,
        'numeric_sample_has_verified_passes_and_no_known_failures':numeric_outcomes.get('pass',0)>0 and numeric_outcomes.get('fail',0)==0,
        'raw_first_field_targets_have_verified_matches_and_no_known_mismatch':coverage['fields_summary'].get('outcomes',{}).get('extracted_correct',0)>0 and coverage['fields_summary'].get('outcomes',{}).get('extracted_mismatch',0)==0,
        'raw_integrity':raw_outcomes.get('pass',0)>0 and raw_outcomes.get('fail',0)==0,
        'temporal_boundary_checks':temporal_outcomes.get('pass',0)>0 and temporal_outcomes.get('fail',0)==0}
    failed_gates=[key for key,passed in acceptance_gates.items() if not passed]
    documents_summary=document_acquisition_summary(coverage['documents_summary'])
    narrative_summary=narrative['summary']
    narrative_total=narrative['denominator']
    if sum(narrative_summary.values())!=narrative_total:
        raise ValueError('Narrative audit outcomes do not reconcile to the frozen target denominator')
    verdict=('Bounded regression, idempotence, raw integrity and sampled numeric checks passed with the listed unresolved/unverified scope; semantic completeness and long-running production reliability are NOT accepted.'
             if not failed_gates else 'Acceptance checks failed or lack required evidence: '+', '.join(failed_gates)+'. No successful bounded acceptance is claimed.')
    silent_targets=[row['target_id'] for row in narrative.get('results',[]) if row.get('classification')=='silent_omission']
    db=sqlite3.connect((ROOT/'data/v3_holdout/data.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    latest=dict(db.execute('SELECT * FROM machine_runs ORDER BY started_at DESC LIMIT 1').fetchone())
    stats={'tasks':dict(db.execute('SELECT state,COUNT(*) FROM machine_tasks GROUP BY state').fetchall()),
           'resources':dict(db.execute('SELECT state,COUNT(*) FROM machine_inventory GROUP BY state').fetchall()),
           'records':db.execute('SELECT COUNT(*) FROM records').fetchone()[0],
           'raw':dict(db.execute('SELECT COUNT(*) AS blobs, SUM(byte_size) AS bytes FROM raw_blobs').fetchone()),
           'latest_run_summary':json.loads(latest['summary_json'])}
    run_history=[]
    for row in db.execute('SELECT run_id,mode,started_at,finished_at,state,summary_json FROM machine_runs ORDER BY started_at'):
        row=dict(row);summary=json.loads(row.pop('summary_json') or '{}')
        run_history.append({**row,'stop_reason':summary.get('stop_reason'),'executed':summary.get('executed'),
                            'remaining_tasks':summary.get('remaining_tasks'),'execution_complete':summary.get('execution_complete')})
    budget_resumes=[]
    for index,run in enumerate(run_history):
        if run['stop_reason']:
            following=run_history[index+1:]
            resume=next((candidate for candidate in following if candidate['mode']=='resume'),None)
            budget_resumes.append({'budget_stopped_run':run,'later_resume_run':resume,
                'interpretation':'Real configured budget stop and subsequent resume invocation; this is NOT a network outage recovery. Final inventory separately shows unfinished work.'})
    db.close()
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'software':software,'production_changed_after_freeze':changes,
            'sampling':{k:v for k,v in freeze.items() if k!='production_hashes'},'real_holdout_stats':stats,
            'post_collection_reporting_fix':{'before_fix_freeze_verification':freeze_before_reporting_fix,
                'current_changes':changes,'collector_and_parser_changed':any(name!='equity_data/coverage.py' for name in changes),
                'interpretation':'Acquisition and raw parsing finished on the original frozen code. The coverage reporting module was then corrected against an observed workflow-state/raw-evidence mismatch; this final release is not entirely identical to the initial freeze.'},
            'numeric_accuracy':numeric['field_outcomes'],'raw_first_field_coverage':coverage['fields_summary'],
            'reference_document_coverage':documents_summary,'narrative_coverage':{'denominator':narrative_total,'outcomes':narrative_summary},
            'document_reference_scope':{'configured_scope':reference_manifest.get('scope'),
                'source_declared_outside_configured_scope_count':excluded_count,
                'entire_filing_package_coverage_claimed':False,
                'caveat':'Reference document denominator is configured scope only. Saved HTML does not prove linked GRAPHIC/JSON/ZIP assets or the whole filing package were archived.'},
            'idempotency':{'bounded_passed':idempotence_passed,'scope':idempotency['scope'],
                'public_backfill_deltas':[p['delta'] for p in idempotency.get('public_repeats',[])],
                'sec_sampled_reparse_deltas':[p['delta'] for p in idempotency.get('sec_repeats',[])],
                'sec_raw_ids':idempotency.get('sec_selected_raw_ids',[]),
                'sec_full_backfill':idempotency['sec_full_inventory_backfill_idempotence']},
            'public_watch':{k:public.get(k) for k in ['watch_wall_seconds','watch_cycles','http_requests','response_bytes','wall_observation_window_seconds']},
            'four_regression_evidence':regressions,'acceptance_gates':acceptance_gates,'failed_acceptance_gates':failed_gates,
            'real_budget_stop_and_resume':budget_resumes,
            'real_http_recovery':{'channel_evidence_available':channel_evidence is not None,'sources':network_recoveries,
                                  'scope':'Same URL/source/storage failure then later HTTP200; matched failure counts are not unique outage episodes or new disclosures.'},
            'known_limitations':{'numeric_unverified':numeric_outcomes.get('unverified',0),'numeric_sample_size':sum(numeric_outcomes.values()),
                                'software_skipped':software['skipped'],'independent_reference_is_whole_source_truth':False},
            'verdict':verdict}
    input_paths=['reports/v3/production-freeze.json',software_path,'reports/v3/freeze-before-reporting-fix.json',
        'reports/v3/holdout-reference-final.json','reports/v3/holdout-coverage-corrected/reference-coverage.json','reports/v3/holdout-audit/independent-field-audit.json',
        'reports/v3/narrative-reference-comparison.json','reports/v3/public_channels/continuous-real-evidence.json',
        idempotency_path,*regressions['source_files'].values()]
    if channel_evidence is not None:input_paths.append(channel_path)
    result['evidence_input_sha256']={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in input_paths}
    (OUT/'acceptance-v3.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    def j(value):return json.dumps(value,ensure_ascii=False)
    lines=['# V3 验收：修复、覆盖与真实缺口','',
        ('四项指定回归的前后证据通过校验。' if regressions['passed'] else '四项指定回归尚有证据检查未通过，不能声明全部修复完成。')+
        '**没有把本系统验收为全市场完整数据库，也没有把深层语义提取验收为完成。** 数字核验、资料覆盖和持续运行分别报告。','',
        '- 有限验收门控：'+j(acceptance_gates),'- 未通过门控：'+j(failed_gates)+'。未通过时不作有限验收成功结论。','',
        '## 四项修复与失败证据','',
        '| 问题 | 修复前 | 修复后 | 证据 |','|---|---|---|---|',
        '| 负数/括号负数指引 | 保留 3 种静默变正及 2 种拒绝案例 | '+('明确负号/括号回归通过；歧义拒绝另列' if regressions['gates']['signed_guidance'] else '前后证据校验未通过')+' | parser-before、parser-before-additional、parser-release、parser-release-additional |',
        '| 混合 p/div/table 正文 | 非 p 的正文/尾文本被遗漏 | '+('混合正文同原文回归通过；额外隐藏表格风险明确拒绝' if regressions['gates']['mixed_html_text'] else '前后证据校验未通过')+' | parser-before、parser-release、parser-edge-before/after |',
        '| 支持文件后到 | 重启后取得支持原文仍为 0 条事实 | '+('自动唤醒离线重解析证据通过；变更/移除/恢复另有软件测试' if regressions['gates']['late_support_reparse'] else '前后证据校验未通过')+' | dependency-before.json、dependency-after.json、原始测试数据库 |',
        '| 证券旧日期覆盖更新 | CLI/update 仍使用旧证券日期 | '+('CLI > 更新日期 > 普通回填证券默认的回归通过' if regressions['gates']['date_precedence'] else '前后证据校验未通过')+' | runtime-before.json、runtime-after.json |','',
        '这些回归使用明确标记的合成原文，证明软件行为，不冒充真实公司准确性。冻结前追加的隐藏表格/单位/语义负值/标题继承案例：'+j(regressions['additional_safety_cases'])+'；明确拒绝不算成功提取。','',
        '## 软件与冻结泛化','',f'- 软件：{software["tests"]} 项；失败 {software["failures"]}、错误 {software["errors"]}、跳过 {software["skipped"]}。',
        '- 冻结后生产文件变化：'+j(changes)+ '。采集和原始数字解析完成时仍与冻结一致；随后发现覆盖报告把队列状态误当原文取得状态，单独修正报告模块并回归。这份最终发布不能称为全部代码仍冻结。',
        '- 开发期修复后证据与最终冻结的代码差异：'+j(regressions['after_artifact_code_differences_from_final_freeze'])+'。这些记录保持原样；最终代码另由完整软件回归覆盖，不把早期复现称为冻结后真实运行。',
        '- 冻结后从预声明结构组按固定 SHA256 排序抽取：'+j(freeze['selected'])+'。这组真实采集与字段检查对应采集时的冻结版本；报告修复后的版本没有另抽一组全新证券，不能扩张为最终全部代码的留出验收。',
        '- 本组是小范围结构覆盖抽样，不是无偏股票池；原文结构及字段抽样见 production-freeze.json 和 holdout-reference-final.json。','',
        '## 独立资料与字段覆盖','',
        '参考清单由另一路代码直接读取官方原文列表/附件目录建立；同一来源可能自身漏项，缺少的历史索引和目录不在完整性保证内。原始 numeric targets 先选取、再核对生产记录；没有从已输出记录反推分母。','',
        '- 资料参考（原文取得、完整解析状态和部分解析分别列出）：'+j(documents_summary),
        '修正后的 success_rate 以已保存原文证据计算，不能解释为最新源版本或解析完成率。队列重新枚举会改变工作流状态，旧原文与旧解析仍保留；原先把工作流状态当下载率的失败报告也保留。','',
        f'- 官方清单已列明但在本次配置范围外的资源数：{excluded_count}（null/None 表示未统计）。这些 GRAPHIC、JSON、ZIP 等资源保留在参考清单的排除数组，未计入配置内分母。原文取得率仅指配置内资料，不代表完整申报包；保存 HTML 不等于其外链图表或其他附件也已归档。','',
        '- 原文优先的数值目标：'+j(coverage['fields_summary']),
        '- 另一路已输出财务数字独立抽查：'+j(numeric['field_outcomes'])+'；这是抽查准确性，不能当召回率。',
        f'- 真实语义目标：{narrative_total} 个；结果：'+j(narrative_summary)+'。完整合同须逐字段核验；候选和未核验结构化输出均不算完成。','',
        '静默语义遗漏目标 ID：'+j(silent_targets)+'。具体段落、指引拒绝原因见语义审计。债务、承诺等候选不算结构化事实；孤立的 XBRL 金额也不算已组装的工具级合同。原文和这些失败均保留，生产解析器未根据留出证券调整。','',
        f'语义参考为模型对原文的人工式复核，构建路径独立于生产输出，不是独立人类专家或第二供应商。{narrative_total} 是目的抽样语义目标数，不能外推全库准确率；同一段落可能对应多个目标，不能按独立文档数解读。','',
        '## 运行状态和持续采集','',
        '- 留出任务状态：'+j(stats['tasks']),'- 留出资料状态：'+j(stats['resources']),
        '- 保存记录版本/原文：'+j({'records':stats['records'],**stats['raw']}),
        '- 幂等验证：'+j(result['idempotency']),
        '- 全量 SEC 重复回填实验未完成：测试脚本切换来源集合后合法生成不同 generation，人工中止其耗时预热。待办保留在样本库，未删除或伪装完成。公共来源回填与三篇 SEC 主文离线重解析的有界检查不能代替全量 SEC 证明。',
        '- 真实预算中止与后续 resume 记录：'+j(budget_resumes)+'。这证明预算停止与续跑过程，不冒充网络中断恢复；未完成任务以上方最终库存为准。',
        '- 真实 HTTP 失败后同 URL 成功获取：'+(j(network_recoveries) if channel_evidence is not None else '渠道关联证据尚未生成，未知')+'。失败日志与后续 200 响应按来源、URL、存储和时间关联；不是新内容检测，也不是长时间稳定性证明。BLS 403 仍单列未恢复。',
        f'- 公共来源 {public.get("watch_cycles")} 轮真实 watch：{public.get("watch_wall_seconds")} 秒；回填与 watch 合计 {public.get("http_requests")} 次 HTTP 请求。真实新披露观察标记 {public.get("real_new_disclosure_observed_during_watch")}、源内容更正观察标记 {public.get("real_source_content_revision_observed_during_watch")}；新增获取流水不算新增内容。',
        '- 合成状态测试另验证新增、修订、断网、顺序改变和重复执行；真实本地子进程突然退出后已保存原文零重下载恢复。上述不等于数日持续服务验收。','',
        '## 未解决问题','',
        f'1. 深层经营指标、关系、债务和承诺仍有候选与未知；本次 {len(silent_targets)} 个语义目标没有产生字段级缺口，需要下一轮通用语义解析与召回改进。',
        '2. BLS 真实 403 未恢复；同一资源的旧失败任务跨 generation 累积，实际请求数 1→2→3。失败任务合并及永久错误重试策略尚待修复。其他来源继续执行已获真实证明。',
        '3. 有限矩形表格已支持；复杂合并表头、PDF/OCR、动态网页、未知样式/脚注仍会拒绝或存在未覆盖范围。拒绝不算完整提取。',
        '4. 授权行情/公司行为、历史证券池、历史一致预期仍缺账号/数据许可与真实验收。没有历史一致预期，不能研究相对市场预期的业绩差异。',
        '5. 参考清单不是全网站真值；IR、新闻全文和更早版本仍可能不可枚举。当前网页/宏观快照不能还原过去版本，未知公开时间仍排除或明确限制。',
        '6. 历史重解析审计尚不自动提升为研究事实；公开可见状态用 as_of，原文取得状态用 known_at，解析已发生状态用 processed_at。审查系统当时实际掌握的结果时应限制处理时间；仅取得旧原文不代表当时已完成解析。运行采用单机单执行者，未验证海量并行。',
        '7. 公共渠道新闻全文未启用的配置缺口，不等于已证实需要付费授权。HTTP 403 证据是请求/状态/失败日志，未保存的错误响应正文不能宣称已归档。','',
        '8. 独立目录发现 1 个未枚举原始 XML 地址；同一申报的 XSL 展示地址已保存，但没有假定二者字节等价。详情见 reference-amendment.json。官方 IR 本轮仍有 403 及未完成重试。',
        '9. 完整 SEC 重复回填尚未完成；重新枚举会重置库存工作流状态。覆盖报告已修正以原文证据单独计数，队列与旧解析状态的展示仍需要继续改进。','',
        '## 交付索引','',
        '- [运行说明](../../README.md) · [数据合同](../../docs/DATA_CONTRACTS_V3.md) · [字段增量](../../docs/FIELD_DICTIONARY_V3.md)',
        '- [参考覆盖报告](holdout-coverage-corrected/reference-coverage.md) · [原文语义审计](narrative-reference-notes.md)',
        '- [渠道矩阵](CHANNELS_V3.md) · [公共渠道真实证据](public_channels/PUBLIC_CHANNELS_V3.md) · [外部依赖](../../docs/DEPENDENCIES_V3.md)',
        '- [留出机器状态](holdout/machine-report.md) · [数值审计](holdout-audit/independent-field-audit.md) · [完整验收JSON](acceptance-v3.json)','',
        ('有限回归与抽样验收门控通过，仍保留上述未知、失败和抽样局限。' if not failed_gates else '有限验收存在未通过项：'+j(failed_gates)+'；不得声明所有验收项通过，单项结论以上述证据为准。')+
        '“所有研究信息已完整结构化、长期自动运行无异常”本轮未通过验收。']
    (OUT/'ACCEPTANCE_V3.md').write_text('\n'.join(lines),encoding='utf-8')
    print(j({'software':software,'code_unchanged':not changes,'acceptance_gates':acceptance_gates,'failed_gates':failed_gates,
             'documents':documents_summary,'fields':coverage['fields_summary'],'numeric':numeric['field_outcomes'],'narrative':narrative['summary']}))
    return 2 if failed_gates else 0

if __name__=='__main__':raise SystemExit(main())
