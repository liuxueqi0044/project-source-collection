"""Read-only research-library status; scheduler exit codes are not data coverage."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from .local_control import schedule_slots, stamp, utcnow, base_payload


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def library_inventory(db):
    rows = [dict(r) for r in db.execute('''SELECT company_id,dataset,
        COUNT(*) AS stored_versions,COUNT(DISTINCT source||':'||record_key) AS stored_keys,
        MIN(period_start) AS earliest_period_start,MIN(period_end) AS earliest_period_end,
        MAX(period_end) AS latest_period_end,MIN(published_at) AS earliest_publication,
        MAX(published_at) AS latest_publication,MIN(retrieved_at) AS first_evidence_acquired_at,
        MAX(retrieved_at) AS latest_evidence_acquired_at,MAX(created_at) AS latest_parsed_at
        FROM records GROUP BY company_id,dataset ORDER BY company_id,dataset''')]
    financial_periods = [dict(r) for r in db.execute('''SELECT company_id,period_start,period_end,
        unit,currency,COUNT(*) AS stored_versions,COUNT(DISTINCT record_key) AS stored_keys
        FROM records WHERE dataset='financial_facts'
        GROUP BY company_id,period_start,period_end,unit,currency
        ORDER BY company_id,period_end,period_start,unit''')]
    layers = Counter(); qualities = Counter()
    for row in db.execute('SELECT dataset,data_json,quality FROM records'):
        data = json.loads(row['data_json'])
        stage = data.get('fact_stage') or ('candidate' if data.get('extraction_method') == 'narrative_evidence_candidate' else 'record_without_semantic_contract')
        layers[(row['dataset'],stage)] += 1
        qualities[(row['dataset'],row['quality'])] += 1
    raw = dict(db.execute('SELECT COUNT(*) AS blobs,COALESCE(SUM(byte_size),0) AS bytes FROM raw_blobs').fetchone())
    return {'by_company_and_type':rows,'financial_periods':financial_periods,'raw':raw,
        'semantic_layers':[{'dataset':d,'stage':s,'stored_versions':n} for (d,s),n in sorted(layers.items())],
        'quality':[{'dataset':d,'quality':q,'stored_versions':n} for (d,q),n in sorted(qualities.items())],
        'count_basis':'All append-only stored versions/keys. Not independent completeness, comparable metrics, or a point-in-time filtered answer.'}


def research_state(initialization, report):
    """A refreshed rolling window cannot stand in for historical initialization."""
    state = initialization.get('state','not_initialized')
    if state == 'not_initialized': return 'not_initialized'
    if not initialization.get('acquisition_complete',False): return 'initialization_incomplete'
    if not report or not report.get('required_freshness_satisfied',False): return 'initialized_update_incomplete'
    return 'initialized_update_checked_with_reported_gaps'


def reassess_refresh(config, report, inventory, now):
    """Reevaluate current clock/scope; a saved boolean is historical evidence only."""
    from .local import freshness_result,restore_state
    from .registry import get_registry
    desired=base_payload(config); requested=(report or {}).get('requested',{})
    keys=('universe','securities','sources','data_types','symbols','csv_sha256')
    matches=bool(report) and not requested.get('initialization') and not requested.get('competition_only') and all(requested.get(k)==desired.get(k) for k in keys)
    checks=(report or {}).get('current_scope_checks',[])
    restored,boundary=restore_state(config['storage'])
    source_types={s['source_id']:s['data_types'] for s in get_registry()['sources']}
    required=config.get('local_freshness',{}).get('required_types',config.get('data_types',[]))
    freshness=freshness_result(checks,required,source_types,config.get('local_freshness',{}),now=now,restored=restored,restore_boundary=boundary)
    counts=Counter()
    for row in inventory.get('by_company_and_type',[]):counts[row['dataset']]+=row['stored_versions']
    for item in freshness:item['content_available']=counts[item['data_type']]>0
    satisfied=bool(matches and required and all(f['freshness']=='satisfied' and f['content_available'] for f in freshness))
    return {'assessed_at':stamp(now),'scope_matches_current_configuration':matches,
        'latest_report_is_historical_initialization':bool(requested.get('initialization')),
        'required_freshness_satisfied':satisfied,'freshness':freshness,
        'basis':'Recomputed against the current clock, exact configured pool/source/type scope, current inventory and restore boundary. A historical initialization report cannot serve as the current refresh.'}


def runtime_status(config, now=None):
    now = now or utcnow(); root = Path(config['storage'])
    settings = config['local_schedule']; control_path = root/'local-control.sqlite3'
    result = {'observed_at':stamp(now),'storage':str(root),'configured_pool':config.get('universe',{}),
        'configured_securities':config.get('securities',[]),'enabled_sources':config.get('sources',[]),
        'schedule':settings,'paused':False,'recent_requests':[],'request_states':{},
        'latest_report':str(root/'latest-decision-report.json'),
        'windows_behavior':'Uses the existing InteractiveToken task. Logged-on/locked works; off, logged-out or asleep does not. Due slots coalesce after return.'}
    if control_path.exists():
        with closing(readonly(control_path)) as db:
            meta={r['key']:json.loads(r['value_json']) for r in db.execute('SELECT * FROM local_meta')}
            result.update(paused=meta.get('paused',False),last_dispatcher_tick=meta.get('last_dispatcher_tick'),
                request_states=dict(db.execute('SELECT state,COUNT(*) FROM local_requests GROUP BY state')),
                recent_requests=[dict(r) for r in db.execute('SELECT * FROM local_requests ORDER BY created_at DESC LIMIT 12')])
            slot=db.execute('SELECT * FROM local_slots ORDER BY slot_utc DESC LIMIT 1').fetchone()
            result['last_recorded_plan_slot']=dict(slot) if slot else None
            for request in result['recent_requests']:
                events=[dict(r) for r in db.execute('SELECT observed_at,kind,details_json FROM local_events WHERE request_id=? ORDER BY observed_at',(request['request_id'],))]
                for event in events:event['details']=json.loads(event.pop('details_json'))
                request['events']=events
                # Initial trigger and any merged/recovery triggers stay separately visible.
                request['payload']=json.loads(request.pop('payload_json'))
    slots=schedule_slots(settings['times'],settings.get('timezone','Asia/Shanghai'),now,now+timedelta(days=2))
    result['next_normal_schedule']=slots[0] if slots else None
    report_path=root/'latest-decision-report.json';report=None
    if report_path.exists():
        report=json.loads(report_path.read_text(encoding='utf-8'))
        keys=('request_id','started_at','finished_at','trigger','requested','effective_scope','initialization',
              'task_execution_complete','source_checks_complete','parsing_complete','required_freshness_satisfied',
              'freshness','metrics','storage_growth','resource_state_axes','gaps_count','uncompleted','semantic_layers')
        result['latest_collection']={k:report[k] for k in keys if k in report}
    data_path=root/'data.sqlite3'
    result['initialization']={'state':'not_initialized','acquisition_complete':False,'parsing_complete':False}
    if data_path.exists():
        with closing(readonly(data_path)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'records' in tables:result['inventory']=library_inventory(db)
            if config.get('competition',{}).get('enabled',False):
                from .competition.jobs import status as competition_status
                result['competition']={'enabled':True,'target_ids':config['competition'].get('target_ids',[]),
                    'runtime':competition_status(SimpleNamespace(db=db,root=root)),
                    'report_path':str(root/'latest-competition-report.json'),
                    'semantic_coverage_complete':False,'note':'Separate acquisition, analysis and scope-review evidence; does not replace financial freshness.'}
            if 'machine_source_health' in tables:
                result['source_health']=[dict(r) for r in db.execute('SELECT source_id,status,last_attempt_at,last_success_at,last_error_at,last_error,attempts,failures FROM machine_source_health') if r['source_id'] in config.get('sources',[])]
            if 'machine_scope_checks' in tables:
                result['source_scope_checks']=[]
                wanted=set(config.get('universe',{}).get('symbols',[]))
                for row in db.execute('SELECT * FROM machine_scope_checks ORDER BY last_attempt_at DESC'):
                    row=dict(row);scope=json.loads(row.pop('scope_json'));pool=set(scope.get('universe',{}).get('symbols',[]))
                    if row['source_id'] not in config.get('sources',[]):continue
                    if wanted and pool and not pool.issubset(wanted):continue
                    row['scope']=scope;row['progress']=json.loads(row.pop('progress_json'));result['source_scope_checks'].append(row)
                result['scope_time_caveat']='Success times apply only to the displayed source/pool/date/type scope; never infer whole-pool success from one issuer or one historical interval.'
            if 'machine_gaps' in tables:
                result['unresolved_gaps']=[dict(r) for r in db.execute('SELECT source_id,company_id,data_type,code,status,message,first_seen_at,last_seen_at,occurrences FROM machine_gaps WHERE resolved_at IS NULL')]
            from .initialization import initialization_status
            result['initialization']=initialization_status(SimpleNamespace(db=db,root=root),config)
    result['current_refresh_assessment']=reassess_refresh(config,report,result.get('inventory',{}),now)
    result['research_library_state']=research_state(result['initialization'],result['current_refresh_assessment'])
    result['coverage_warning']='Ready/exit-code/task-complete do not establish complete acquisition, parsing, freshness, or verified field coverage.'
    return result


def status_markdown(result):
    pool=result['configured_pool']; rows=['# 本机研究库运行状态','',
        '观察时间：'+result['observed_at'], '股票池：'+json.dumps(pool,ensure_ascii=False),
        '启用渠道：'+', '.join(result['enabled_sources']),
        '研究库状态：'+result['research_library_state'],
        '初始化状态：'+result['initialization'].get('state','unknown'),
        '下一正常计划：'+str(result.get('next_normal_schedule',{}).get('local')),
        '', '|公司 ID|资料类型|保存版本|报告期起点|报告期终点|最近公开时间|', '|---|---|---:|---|---|---|']
    for r in result.get('inventory',{}).get('by_company_and_type',[]):
        rows.append('|'+ '|'.join(str(r.get(k) or '未知/不适用') for k in ['company_id','dataset','stored_versions','earliest_period_start','latest_period_end','latest_publication'])+'|')
    rows+=['','这些数量是库内版本库存，不是独立覆盖率分母。深层语义候选不计为完整事实。',
        '按来源/范围的检查时间、触发历史、指标、原文/解析/质量状态和缺口见同名 JSON。',
        '自然登录/睡眠恢复、新披露与更正的证据仅在实际发生后记录。']
    if result.get('competition'):
        rows+=['','竞争模块已配置启用；当前目标：'+', '.join(result['competition']['target_ids']),
            '采集/分析队列和失败见同名JSON的competition字段；语义覆盖仍未通过完整验收。']
    return '\n'.join(rows)+'\n'
