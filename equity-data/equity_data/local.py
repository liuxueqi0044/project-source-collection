"""Windows/local durable dispatcher and evidence-only decision refresh.

The OS task invokes tick frequently; network work runs only for a due slot,
an explicit request, a bounded recovery attempt, or a weekly revision scan.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .cli import load_config
from .local_control import Control, base_payload, collector_lock, encode, stamp, utcnow, build_continuation
from .store import Store


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    temporary.replace(path)


def active_config(config,payload):
    conf=copy.deepcopy(config)
    for key in ('universe','securities','sources','data_types'):
        if key in payload:conf[key]=copy.deepcopy(payload[key])
    if payload.get('competition_frozen'):
        conf['competition']=copy.deepcopy(payload['competition_frozen']['settings'])
        conf['_competition_frozen']=copy.deepcopy(payload['competition_frozen'].get('plan'))
    elif config.get('competition',{}).get('enabled',False):
        # Requests queued before activation retain their old scope.
        conf['competition']={'enabled':False}
    if payload.get('csv_sha256')!=base_payload(conf).get('csv_sha256'):
        raise ValueError('Stock-pool CSV changed after request; submit a new refresh for the changed pool')
    return conf


def resume_scopes(store,conf):
    """Resume only previously requested unfinished work for this pool and source options."""
    from .engine import source_scope
    from .queue import Queue
    matches=[]
    for row in Queue(store).scope_checks():
        if row['source_id'] not in conf.get('sources',[]):continue
        if row.get('progress',{}).get('complete_check'):continue
        if not store.db.execute("SELECT 1 FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.scope_id=? AND t.state!='succeeded' LIMIT 1",(row['scope_id'],)).fetchone():continue
        expected=source_scope(conf,row['source_id'])
        actual=row['scope']
        def securities_without_dates(value):
            return sorted([{k:v for k,v in security.items() if k not in {'start','end'}} for security in value or []],key=encode)
        expected=dict(expected,securities=securities_without_dates(expected.get('securities')))
        actual=dict(actual,securities=securities_without_dates(actual.get('securities')))
        keys=('market','universe','securities','data_types','source_options')
        if all(actual.get(k)==expected.get(k) for k in keys):matches.append(row)
    # Date windows are not silently discarded when the rolling window moves on.
    matches.sort(key=lambda r:r.get('last_attempt_at') or '')
    return [r['scope_id'] for r in matches]


def storage_counts(store):
    return {'records':store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],
            'record_sequence':store.db.execute('SELECT COALESCE(MAX(sequence),0) FROM records').fetchone()[0],
            'raw_blobs':store.db.execute('SELECT COUNT(*) FROM raw_blobs').fetchone()[0],
            'raw_bytes':store.db.execute('SELECT COALESCE(SUM(byte_size),0) FROM raw_blobs').fetchone()[0],
            'retrievals':store.db.execute('SELECT COUNT(*) FROM raw_retrievals').fetchone()[0],
            'database_bytes':sum(p.stat().st_size for p in store.root.glob('*.sqlite3*'))}


def record_availability(store,companies):
    """All stored versions, grouped by evidence stage; no recall denominator claim."""
    from collections import defaultdict,Counter
    groups=defaultdict(lambda:{'stored_versions':0,'stages':Counter(),'qualities':Counter(),'latest_publication':None,'latest_period_end':None,'latest_acquisition':None})
    clause='company_id IS NULL'
    params=sorted(c for c in companies if c)
    if params:clause+=' OR company_id IN ('+','.join('?' for _ in params)+')'
    for row in store.db.execute('SELECT dataset,data_json,quality,published_at,period_end,retrieved_at FROM records WHERE '+clause,params):
        item=groups[row['dataset']];data=json.loads(row['data_json'])
        stage=data.get('fact_stage') or ('candidate' if data.get('extraction_method')=='narrative_evidence_candidate' else 'record_without_semantic_contract')
        item['stored_versions']+=1;item['stages'][stage]+=1;item['qualities'][row['quality']]+=1
        for key,field in [('latest_publication','published_at'),('latest_period_end','period_end'),('latest_acquisition','retrieved_at')]:
            if row[field] and (not item[key] or row[field]>item[key]):item[key]=row[field]
    return dict(groups)


def restore_state(root):
    path=Path(root)/'RESTORE_STATUS.json'
    if not path.exists():return False,None
    marker=json.loads(path.read_text(encoding='utf-8'))
    return bool(marker.get('requires_live_check',marker.get('restored_requires_live_check',False))),marker.get('restored_at')


def freshness_result(checks,data_types,source_types,thresholds,now=None,restored=False,restore_boundary=None):
    """Freshness is a source check, not a fetch timestamp or field recall score."""
    now=now or utcnow();result=[]
    raw_types={'documents','filings','news','policy_documents','company_profiles','securities','identity_history','event_calendar','calendar_sessions'}
    for dtype in data_types:
        candidates=[]
        for check in checks:
            if dtype not in source_types.get(check['source_id'],[]):continue
            progress=check.get('progress',{})
            type_progress=progress.get('by_data_type',{}).get(dtype)
            phase='acquisition' if dtype in raw_types else 'parse'
            checked=check.get('last_acquisition_success_at' if phase=='acquisition' else 'last_parse_success_at')
            complete=bool(progress.get('discovery_complete') and progress.get('acquisition_complete') and
                          (phase=='acquisition' or progress.get('parsing_complete')) and not progress.get('upstream_refresh_unconfirmed'))
            if progress.get('by_data_type') is not None:
                complete=bool(type_progress and type_progress.get('discovered',0)>0 and progress.get('discovery_complete') and
                    type_progress.get('acquisition_complete') and (phase=='acquisition' or type_progress.get('parse_complete')) and not progress.get('upstream_refresh_unconfirmed'))
                checked=(type_progress or {}).get('last_upstream_check_at') if complete else checked
            age=(now-datetime.fromisoformat(checked.replace('Z','+00:00'))).total_seconds()/3600 if checked else None
            maximum=float(thresholds.get(dtype,thresholds.get('default',48)))
            restore_pending=restored and not (checked and restore_boundary and
                datetime.fromisoformat(checked.replace('Z','+00:00'))>=datetime.fromisoformat(restore_boundary.replace('Z','+00:00')))
            fresh=complete and age is not None and 0<=age<=maximum and not restore_pending
            candidates.append({'source_id':check['source_id'],'scope_id':check['scope_id'],
                'source_check_complete':complete,'last_successful_check':checked,'age_hours':round(age,3) if age is not None else None,
                'threshold_hours':maximum,'freshness':'satisfied' if fresh else 'cannot_confirm',
                'basis':phase+' and complete source discovery; not independent target-field completeness',
                'phase_progress':progress,'type_progress':type_progress,'restored_requires_live_check':restore_pending})
        result.append({'data_type':dtype,'freshness':'satisfied' if candidates and any(c['freshness']=='satisfied' for c in candidates) else 'cannot_confirm',
                       'sources':candidates,'missing_reason':None if candidates else 'No selected implemented/evidenced source covers this type'})
    return result


def decision_report(store,conf,payload,run,before,request,output):
    from .registry import get_registry
    from .queue import Queue
    after=storage_counts(store);queue=Queue(store)
    scope_ids=set(run.get('effective_scope',{}).get('source_scope_ids',{}).values())
    checks=[c for c in queue.scope_checks() if c['scope_id'] in scope_ids]
    source_types={s['source_id']:s['data_types'] for s in get_registry()['sources']}
    restored,restore_boundary=restore_state(store.root)
    # Each source/type must have its own post-restore check evidence. A request
    # to some other source must not unlock inherited freshness for this one.
    freshness=freshness_result(checks,payload['data_types'],source_types,conf.get('local_freshness',{}),restored=restored,restore_boundary=restore_boundary)
    rows=store.db.execute('''SELECT r.*,b.sha256,b.path AS raw_path FROM records r JOIN raw_blobs b USING(raw_id)
        WHERE r.sequence>? ORDER BY r.sequence''',(before['record_sequence'],))
    new_by_type={};new_versions=0;timestamp_examples=[]
    with (output/'new-records.jsonl').open('w',encoding='utf-8') as stream:
        for row in rows:
            record=dict(row);record['data']=json.loads(record.pop('data_json'))
            new_by_type[record['dataset']]=new_by_type.get(record['dataset'],0)+1
            new_versions+=bool(record['supersedes'])
            if len(timestamp_examples)<40:timestamp_examples.append({k:record[k] for k in ('record_id','dataset','company_id','security_id','source','published_at','publication_precision','period_start','period_end','retrieved_at','source_locator','raw_id','sha256','quality','knowledge_type','parser_version')})
            stream.write(encode(record)+'\n')
    companies={s.get('company_id') for s in (run.get('universe') or {}).get('securities',[])}
    availability=record_availability(store,companies)
    for item in freshness:
        item['stored_evidence']=availability.get(item['data_type'],{'stored_versions':0,'stages':{}})
        item['content_available']=item['stored_evidence']['stored_versions']>0
        item['interpretation']='Source-check freshness and stored content availability are separate; neither establishes independently verified field completeness.'
    resources=[r for r in queue.resources() if r['source_id'] in payload['sources'] and (not r.get('company_id') or not companies or r['company_id'] in companies)]
    from collections import Counter
    axes={key:dict(Counter(r.get(key,'unknown') for r in resources)) for key in ('raw_state','refresh_state','parse_state','upstream_state')}
    gaps=[g for g in queue.gaps() if g['source_id'] in payload['sources']+['registry','sec_directory','scheduler'] and (not g.get('company_id') or not companies or g['company_id'] in companies)]
    write_json(output/'coverage-gaps.json',gaps)
    checks_complete=bool(checks) and all(c.get('progress',{}).get('complete_check') for c in checks)
    required=conf.get('local_freshness',{}).get('required_types',payload['data_types'])
    all_fresh=bool(required) and all(any(f['data_type']==dtype and f['freshness']=='satisfied' and f['content_available'] for f in freshness) for dtype in required)
    from .initialization import initialization_status
    from .local_status import research_state
    init={'state':'not_requested','note':'Competition-only request does not initialize SEC financial history.'} if payload.get('competition_only') else initialization_status(store,conf,payload=payload if payload.get('initialization') else None)
    result={'request_id':request['request_id'],'started_at':request['started_at'],'finished_at':stamp(),
        'trigger':request.get('trigger'), 'initialization':init,
        'requested':payload,'effective_scope':run.get('effective_scope'),'run_id':run.get('run_id'),
        'task_execution_complete':run.get('task_execution_complete',False),
        'parsing_complete':run.get('parsing_complete',False),'source_checks_complete':checks_complete,
        'coverage_complete':False,'coverage_basis':'Discovery/parse state is separate from independently referenced material and field recall; see reference-coverage reports.',
        'required_freshness_satisfied':all_fresh,'freshness':freshness,'current_scope_checks':checks,
        'resumed_scope_checks':[c for c in run.get('source_scope_checks',[]) if c['scope_id'] not in scope_ids],
        'new_records_by_dataset':new_by_type,'new_record_versions':new_versions,
        'record_timestamp_examples':timestamp_examples,'resource_state_axes':axes,
        'gaps_count':len(gaps),'gaps_file':str(output/'coverage-gaps.json'),
        'new_records_file':str(output/'new-records.jsonl'),'metrics':run.get('network_budget'),
        'stored_evidence_by_dataset':availability,
        'storage_before':before,'storage_after':after,'storage_growth':{k:after[k]-before[k] for k in after},
        'uncompleted':[{c['source_id']:c.get('progress',{})} for c in checks if not c.get('progress',{}).get('complete_check')],
        'semantic_layers':{'raw':'saved file + hash','candidate':'data.fact_stage=candidate; not an extracted fact',
            'fact':'field evidence + unit/period/entity; quality may still be unverified','unified_metric':'only explicit semantic mapping; no name-based company extension mapping'},
        'limitations':['Recent download does not establish newest upstream version.','Successful parsing does not measure silent omissions without an independent reference.',
            'No trading recommendation or order is generated.']}
    result['research_library_state']=research_state(init,result)
    if payload.get('initialization'):
        result['initialization_batch']=run.get('initialization_batch')
        result['freshness_note']='Historical initialization establishes retained acquisition and parsing progress, not a completed current incremental refresh.'
    write_json(output/'decision-report.json',result)
    lines=['# 决策前刷新证据',f"请求：{result['request_id']}",f"完成时间（UTC）：{result['finished_at']}",
        f"任务执行完成：{result['task_execution_complete']}；解析完成：{result['parsing_complete']}；来源检查完整：{checks_complete}",
        f"必要信息时效已满足：{all_fresh}。研究库状态：{result['research_library_state']}；初始化：{init.get('state')}。资料和字段的完整覆盖仍须独立参考清单核验。",'',
        '|信息类型|来源时效检查|','|---|---|']
    lines += [f"|{f['data_type']}|{f['freshness']}|" for f in freshness]
    lines+=['',f'缺口 {len(gaps)} 项，见 coverage-gaps.json。详细检查时间、期间、发布时间、获取时间及四层语义状态见 decision-report.json。',
            '新增及更正记录的完整证据见 new-records.jsonl；旧版本保留于数据库。']
    (output/'decision-report.md').write_text('\n\n'.join(lines[:6])+'\n'+'\n'.join(lines[6:]),encoding='utf-8')
    return result


def run_request(control,config,request):
    from .machine import execute
    payload=json.loads(request['payload_json']);conf=active_config(config,payload)
    request=dict(request,trigger=control.request_trigger_summary(request['request_id']))
    output=Path(conf['storage'])/'local-runs'/request['request_id'];output.mkdir(parents=True,exist_ok=True)
    with (output/'run.log').open('a',encoding='utf-8',buffering=1) as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        print(encode({'event':'start','request_id':request['request_id'],'at':stamp(),'pid':__import__('os').getpid(),'trigger':request['trigger']}))
        try:
            with Store(conf['storage']) as store:
                before=storage_counts(store)
                if payload.get('competition_only'):
                    from .competition.runner import run_competition
                    competition=run_competition(store,conf,frozen=conf.get('_competition_frozen'),
                        trigger=','.join(request['trigger'].get('categories',[])) or 'manual_refresh',
                        analysis_only=payload.get('analysis_only',False),force=payload.get('force_competition',False))
                    run={'effective_scope':{},'task_execution_complete':True,'competition':competition,
                         'network_budget':(competition.get('acquisition') or {}).get('network_budget',{'requests':0,'bytes':0})}
                elif payload.get('initialization'):
                    from .initialization import run_initialization_batch,retry_initialization
                    retry=payload.get('_local',{}).get('retry_initialization')
                    if retry:
                        retry_initialization(store,conf,payload,reason=retry['reason'])
                        payload=copy.deepcopy(payload)
                        payload['_local'].pop('retry_initialization',None)
                    batch=run_initialization_batch(store,conf,payload)
                    run=batch.get('run') or {'effective_scope':{},'task_execution_complete':True,'network_budget':{'requests':0,'bytes':0}}
                    run['initialization_batch']=batch.get('batch')
                else:
                    end=utcnow().astimezone(ZoneInfo('America/New_York' if conf['market']=='US' else 'Asia/Shanghai')).date()
                    start=conf['start'] if payload.get('full_history') else (end-timedelta(days=conf.get('update_lookback_days',14))).isoformat()
                    if payload.get('window_start_hint'):start=min(start,payload['window_start_hint'])
                    start=max(config['start'],start)
                    resume=sorted(set(resume_scopes(store,conf)+payload.get('resume_scope_ids',[])))
                    args=SimpleNamespace(command='update',start=start,end=end.isoformat(),sources=None,symbols=None,
                        data_types=payload['data_types'],full_history=payload.get('full_history',False),offline=False,
                        resume_scope_ids=resume,max_tasks=None)
                    run=execute(store,conf,args)
                    if conf.get('competition',{}).get('enabled',False):
                        from .competition.runner import run_competition
                        try:
                            run['competition']=run_competition(store,conf,frozen=conf.get('_competition_frozen'),
                                trigger=','.join(request['trigger'].get('categories',[])) or 'scheduled_collection',used_budget=run.get('network_budget'))
                        except Exception as exc:
                            # Optional analysis/source failure cannot undo the
                            # already committed financial acquisition receipts.
                            from .engine import _error
                            run['competition']={'enabled':True,'state':'failed','error':_error(exc)}
                write_json(output/'engine-run.json',run)
                if payload.get('competition_only'):
                    from .competition.reports import decision_report as competition_decision_report
                    report=competition_decision_report(store,conf,payload,run,before,request,output)
                else:report=decision_report(store,conf,payload,run,before,request,output)
                if run.get('competition') is not None:
                    from .competition.reports import write_run_report
                    report['competition']=run['competition']
                    write_run_report(store,conf,run['competition'],output/'competition')
                    write_json(output/'decision-report.json',report)
                    write_json(Path(conf['storage'])/'latest-competition-report.json',{'request_id':request['request_id'],'competition':run['competition'],'result_path':str(output/'decision-report.json')})
                finished=report['task_execution_complete'] if payload.get('competition_only') else report['initialization'].get('acquisition_complete') if payload.get('initialization') else report['source_checks_complete']
                state='completed' if finished else 'partial'
                continuation=None if payload.get('initialization') or payload.get('competition_only') else build_continuation(store,run)
                control.finish(request['request_id'],state,output/'decision-report.json',continuation=continuation)
                if payload.get('initialization'):
                    from .initialization import next_pending_plan
                    if next_pending_plan(store,conf,payload=payload):
                        control.enqueue(payload,'initialization_continuation')
                if not payload.get('competition_only'):write_json(Path(conf['storage'])/'latest-decision-report.json',report)
                print(encode({'event':'finish','state':state,'at':stamp(),'metrics':run.get('network_budget')}))
                return control.get(request['request_id'])
        except Exception as exc:
            from .engine import _error
            traceback.print_exc();error=_error(exc)
            write_json(output/'failure.json',{'at':stamp(),'error':error,'request_id':request['request_id']})
            control.finish(request['request_id'],'failed',output/'failure.json',error)
            return control.get(request['request_id'])


def drain(config,*,max_jobs=None):
    with collector_lock(config['storage']) as acquired:
        if not acquired:return {'status':'queued_owner_active','completed':[]}
        control=Control(config['storage'])
        try:
            recovered=control.recover_under_lock();completed=[]
            for _ in range(max_jobs or config.get('local_schedule',{}).get('max_jobs_per_activation',1)):
                if control.meta('paused',False):break
                request=control.claim()
                if not request:break
                try:completed.append(run_request(control,config,request))
                except Exception as exc:
                    from .engine import _error
                    path=Path(config['storage'])/'local-runs'/request['request_id']/'failure.json'
                    write_json(path,{'at':stamp(),'error':_error(exc),'stage':'request_configuration'})
                    control.finish(request['request_id'],'failed',path,_error(exc))
                    completed.append(control.get(request['request_id']))
            return {'status':'drained','recovered_after_exit':recovered,'completed':completed}
        finally:control.close()


def tick(config):
    control=Control(config['storage'])
    try:
        plan=control.plan(config)
        settings=config['local_schedule'];now=utcnow()
        if not control.meta('revision_planned_at'):control.set_meta('revision_planned_at',stamp(now))
        last=datetime.fromisoformat(control.meta('revision_planned_at'))
        if not plan['paused'] and now-last>=timedelta(days=settings.get('revision_every_days',7)):
            payload=base_payload(config);payload['full_history']=True
            identity=control.enqueue(payload,'low_frequency_history_revision_check')
            control.set_meta('revision_planned_at',stamp(now));plan['requests'].append(identity)
        control.set_meta('last_dispatcher_tick',{'at':stamp(),'pid':__import__('os').getpid(),'plan':plan})
    finally:control.close()
    initialization=plan_initialization(config)
    analysis=plan_competition_analysis(config)
    return {'plan':plan,'initialization_plan':initialization,'competition_analysis_plan':analysis,'execution':drain(config)}


def plan_competition_analysis(config):
    """Queue ready analysis or evidenced, gated competition-only continuation."""
    if not config.get('competition',{}).get('enabled',False):return {'enabled':False}
    with collector_lock(config['storage']) as owned:
        if not owned:return {'status':'owner_active'}
        control=Control(config['storage'])
        try:
            if control.meta('paused',False):return {'status':'paused'}
            if control.meta('competition_paused',False):return {'status':'competition_paused'}
            from .competition import jobs
            with Store(config['storage']) as store:
                jobs.ensure_schema(store)
                ready=store.db.execute("SELECT job_id FROM competition_jobs WHERE state='running' OR (state IN ('pending','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=?)) LIMIT 1",(stamp().replace('+00:00','Z'),)).fetchone()
                payload=base_payload(config)
                retry=None
                if not ready:
                    active_ids=sorted({identity for endpoint in ((payload.get('competition_frozen') or {}).get('plan') or {}).get('all_endpoints',[]) for identity in endpoint.get('endpoint_ids',[endpoint['endpoint_id']])})
                    if active_ids:
                        retry=store.db.execute("SELECT * FROM competition_endpoint_state WHERE endpoint_id IN ("+','.join('?' for _ in active_ids)+") AND (last_error LIKE 'Acquisition budget interrupted%' OR last_error LIKE 'Network acquisition incomplete%') AND next_due_at<=? AND consecutive_failures<? ORDER BY next_due_at LIMIT 1",active_ids+[stamp().replace('+00:00','Z'),int(config['competition'].get('max_endpoint_failures',5))]).fetchone()
                    if not retry:return {'status':'no_ready_analysis_or_gated_retry'}
                payload.update(competition_only=True,analysis_only=bool(ready))
                reason='unfinished_work_resume' if ready or 'budget' in retry['last_error'].lower() else 'limited_failure_retry'
                identity=control.enqueue(payload,reason,details={'kind':'competition_analysis_only' if ready else 'competition_endpoint_continuation','network':not bool(ready),'endpoint_id':retry['endpoint_id'] if retry else None})
                return {'status':'queued','request_id':identity,'reason':reason,'analysis_only':bool(ready)}
        finally:control.close()


def plan_initialization(config):
    """Only queue a needed historical batch; the pulse itself does no network work."""
    if not config.get('initialization',{}).get('auto_on_pool_change',False):return {'enabled':False}
    with collector_lock(config['storage']) as owned:
        if not owned:return {'status':'owner_active'}
        control=Control(config['storage'])
        try:
            if control.meta('paused',False):return {'status':'paused'}
            from .initialization import build_initialization_payload,ensure_plan,next_pending_plan
            payload={**base_payload(config),**build_initialization_payload(config)}
            with Store(config['storage']) as store:
                ensure_plan(store,config,payload)
                next_batch=next_pending_plan(store,config,payload=payload)
                if next_batch:
                    identity=control.enqueue(payload,'initialization_unfinished_work')
                    return {'status':'queued','request_id':identity,'batch':next_batch}
                return {'status':'no_ready_historical_work'}
        finally:control.close()


def status(config):
    from .local_status import runtime_status
    return runtime_status(config)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='configs/live.toml')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('tick','pause','resume'):commands.add_parser(name)
    status_parser=commands.add_parser('status');status_parser.add_argument('--out')
    initialize=commands.add_parser('initialize')
    initialize.add_argument('--symbols');initialize.add_argument('--sources')
    initialize.add_argument('--start');initialize.add_argument('--end')
    initialize.add_argument('--max-batches',type=int,default=1)
    initialize.add_argument('--retry-blocked',action='store_true')
    initialize.add_argument('--reason',default='explicit_operator_initialization_retry')
    refresh=commands.add_parser('refresh')
    refresh.add_argument('--symbols');refresh.add_argument('--sources');refresh.add_argument('--data-types')
    refresh.add_argument('--full-history',action='store_true');refresh.add_argument('--wait-seconds',type=float,default=0)
    args=parser.parse_args(argv);config=load_config(args.config)
    if args.command=='tick':result=tick(config)
    elif args.command=='status':
        result=status(config)
        if args.out:
            from .local_status import status_markdown
            target=Path(args.out);write_json(target.with_suffix('.json'),result)
            target.with_suffix('.md').write_text(status_markdown(result),encoding='utf-8')
    elif args.command in {'pause','resume'}:
        control=Control(config['storage'])
        try:control.set_meta('paused',args.command=='pause')
        finally:control.close()
        result={'paused':args.command=='pause','in_flight_policy':'An already running request finishes; no further claim while paused.'}
    elif args.command=='initialize':
        from .initialization import build_initialization_payload
        if args.max_batches<1:parser.error('--max-batches must be positive')
        symbols=[s.strip().upper() for s in args.symbols.split(',') if s.strip()] if args.symbols else None
        sources=[s.strip() for s in args.sources.split(',') if s.strip()] if args.sources else None
        payload={**base_payload(config),**build_initialization_payload(config,start=args.start,end=args.end,symbols=symbols,sources=sources)}
        if args.retry_blocked:payload['_local']={'retry_initialization':{'reason':args.reason}}
        control=Control(config['storage'])
        try:identity=control.enqueue(payload,'manual_history_initialization')
        finally:control.close()
        execution=drain(config,max_jobs=args.max_batches)
        result={'request_id':identity,'execution':execution,'status':status(config),
                'note':'Each batch has its own configured budget. Ready unfinished batches stay queued for the existing Windows dispatcher.'}
    else:
        payload=base_payload(config)
        if args.symbols:
            symbols=sorted({s.strip().upper() for s in args.symbols.split(',') if s.strip()})
            payload.update(symbols=symbols,universe={'mode':'configured','symbols':symbols},securities=[],csv_sha256=None)
        for key in ('sources','data_types'):
            if getattr(args,key):payload[key]=sorted({s.strip() for s in getattr(args,key).split(',') if s.strip()})
        payload['full_history']=args.full_history
        control=Control(config['storage'])
        try:identity=control.enqueue(payload,'manual_decision_refresh')
        finally:control.close()
        execution=drain(config)
        deadline=time.monotonic()+max(0,args.wait_seconds)
        while True:
            control=Control(config['storage'])
            try:request=control.get(identity)
            finally:control.close()
            if request['state'] not in {'pending','running'} or time.monotonic()>=deadline:break
            time.sleep(min(2,max(0,deadline-time.monotonic())));execution=drain(config)
        result={'request':request,'execution':execution}
    if sys.stdout:print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        # pythonw has no standard streams; retain early configuration/import failures.
        fallback=Path(__file__).resolve().parents[1]/'logs';fallback.mkdir(exist_ok=True)
        with (fallback/'local-startup-errors.log').open('a',encoding='utf-8') as stream:
            stream.write(stamp()+'\n');traceback.print_exc(file=stream)
        if sys.stderr:traceback.print_exc()
        raise SystemExit(1)
