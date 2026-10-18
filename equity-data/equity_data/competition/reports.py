"""Evidence bundles and explicit coverage limits for people and research agents."""
import json
from pathlib import Path
from collections import Counter
from .queries import query_competition
from . import jobs
from .events import CAPABILITY_MATRIX

def inventory(store,config):
    datasets=('entities','entity_links','competition_edges','watch_endpoints','competition_snapshots','competitive_events','competitive_event_links','analysis_hypotheses')
    result={}
    for name in datasets:
        rows=store.query(name)
        result[name]={'current_records':len(rows),'stored_versions':store.db.execute('SELECT COUNT(*) FROM records WHERE dataset=?',(name,)).fetchone()[0],
                      'stages':dict(Counter(r['data'].get('fact_stage',r['data'].get('semantic_stage','not_applicable')) for r in rows)),
                      'review_states':dict(Counter(r['data'].get('review_status','not_applicable') for r in rows))}
    return {'configured_enabled':config.get('competition',{}).get('enabled',False),'target_ids':config.get('competition',{}).get('target_ids',[]),
            'datasets':result,'runtime':jobs.status(store),'semantic_capabilities':CAPABILITY_MATRIX,
            'coverage_complete':False,'independent_denominator':'See reports/v5 validation manifests; no all-web disclosure or full event-recall claim',
            'remaining_dependencies':['Unconfigured dynamic pages need a reviewed browser adapter; none installed.',
                'Search providers, paid news/full transcripts and commercial expectations remain external dependencies.',
                'Entity and relationship evidence review is not automated independent business verification.',
                'No financial impact, buy/sell conclusion or order is generated.']}

def brief_markdown(bundle):
    lines=['# 竞争情报证据简报','',f"目标主体：{bundle['target_entity_id']}",'',
           f"关系 {len(bundle['relations'])}；关联事件 {len(bundle['events'])}；符合已确认范围的关联 {len(bundle['core_brief'])}；待核验 {len(bundle['pending_review'])}。",'',
           '这里的事实为来源陈述；竞争影响需另行研究。候选片段不计为完成的结构化事实。','']
    for item in bundle['core_brief']:
        event=item['event']['data'];edge=item['edge']['data']
        lines += [f"- {event.get('actor_entity_id')}：{event.get('event_type')} / {event.get('status')} / {event.get('object')}",
                  f"  适用范围：{edge.get('product_or_business')}；{edge.get('geography')}；{edge.get('customer_segment')}。",
                  f"  原文证据：{item['event']['record_id']}；关系版本：{item['edge']['record_id']}。"]
    if not bundle['core_brief']:lines+=['当前没有满足全部身份、关系范围和时间证据要求的核心关联；请查看 JSON 中的候选、范围未知和证据缺口。']
    lines+=['','## 待核验与局限','']
    lines += ['- '+str(value) for value in bundle.get('gaps',[])+bundle.get('time_limitations',[])]
    return '\n'.join(lines)+'\n'

def write_run_report(store,config,run,path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    def save(name,value):(path/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    save('run.json',run);save('status.json',inventory(store,config))
    for target in config.get('competition',{}).get('target_ids',[]):
        bundle=query_competition(store,target);name=target.replace(':','_').replace('/','_')
        save(name+'.json',bundle);(path/(name+'.md')).write_text(brief_markdown(bundle),encoding='utf-8')

def decision_report(store,config,payload,run,before,request,output):
    from ..local import storage_counts,write_json
    from ..store import now_utc
    comp=run.get('competition',{});acq=comp.get('acquisition');analysis=comp.get('analysis',{})
    counts=analysis.get('queue',{})
    analysis_complete=not any(counts.get(k,0) for k in ('pending','running','retry_wait','exhausted')) and not comp.get('error') and not comp.get('paused')
    acquisition_complete=None if comp.get('analysis_only') else bool(acq and acq.get('task_execution_complete') and not comp.get('budget_gap'))
    if not comp.get('analysis_only') and not comp.get('due_endpoints') and not comp.get('error'):
        acquisition_complete=True  # no network work requested; no freshness claim
    source_complete=None if acq is None else bool(comp.get('endpoint_receipts') and all(r['scope_check_complete'] for r in comp['endpoint_receipts']))
    after=storage_counts(store)
    result={'request_id':request['request_id'],'started_at':request['started_at'],'finished_at':now_utc(),
        'trigger':request.get('trigger'),'requested':payload,'information_scope':'competition_only',
        'task_execution_complete':bool(analysis_complete and acquisition_complete is not False),
        'acquisition_complete':acquisition_complete,'analysis_processing_complete':analysis_complete,
        'parsing_complete':acq.get('parsing_complete') if acq is not None else None,'source_checks_complete':source_complete,'required_freshness_satisfied':None,
        'coverage_complete':False,'coverage_basis':'Analysis completion and configured source checks do not establish independent material/semantic completeness.',
        'competition':comp,'metrics':(acq or {}).get('network_budget',{'requests':0,'bytes':0}),
        'storage_before':before,'storage_after':after,'storage_growth':{k:after[k]-before[k] for k in after},
        'freshness_note':'null means not checked in this request. Prior receipts remain in endpoint state; unchanged download is not evidence of latest upstream content.'}
    write_json(Path(output)/'decision-report.json',result)
    (Path(output)/'decision-report.md').write_text('# 竞争信息刷新证据\n\n'+
        f"任务执行完成：{result['task_execution_complete']}；采集完成：{acquisition_complete}；分析处理完成：{analysis_complete}；本轮来源检查：{source_complete}。\n\n"+
        'None/null 表示本轮未检查，不能视为最新。语义覆盖完整：False。详情、候选和未完成事项见 competition/ 证据包。\n',encoding='utf-8')
    return result
