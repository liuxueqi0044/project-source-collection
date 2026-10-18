"""One collector, separate resumable analysis queue, bounded optional acquisition."""
from __future__ import annotations
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from ..store import _json, now_utc
from . import jobs
from .domain import load_record, latest_record, put_derived_record

def freeze_settings(config):
    """No database writes/network during the five-minute scheduling pulse."""
    settings=copy.deepcopy(config.get('competition',{}))
    if not settings.get('enabled',False):return None
    import sqlite3
    from pathlib import Path
    from ..store import Store
    database=Path(config['storage'])/'research.sqlite3'
    # Store filename is an implementation contract; derive it without opening a
    # new writable Store. Existing deployments use equity.sqlite3.
    candidates=[p for p in Path(config['storage']).glob('*.sqlite3') if p.name!='local-control.sqlite3']
    if not candidates:return {'settings':settings,'plan':None,'reason':'store_not_initialized'}
    for path in candidates:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone():continue
            store=Store.__new__(Store);store.root=path.parent;store.db=db
            from .watch_planner import plan_watches
            plan=plan_watches(store,config,force=False)
            # Request identity cannot change merely because the pulse clock did.
            plan.pop('planned_at',None);plan.pop('plan_id',None)
            for endpoint in plan.get('all_endpoints',[]):
                for key in ('due','due_reason','last_success_at','next_due_at'):endpoint.pop(key,None)
            plan['endpoints']=copy.deepcopy(plan['all_endpoints']);plan.pop('due_endpoints',None)
            plan['plan_id']='competition-plan:'+hashlib.sha256(_json(plan).encode()).hexdigest()
            return {'settings':settings,'plan':plan}
        finally:db.close()
    return {'settings':settings,'plan':None,'reason':'store_not_initialized'}

def _snapshot(store,row):
    value=copy.deepcopy(row['data']);raw=store.db.execute('SELECT * FROM raw_blobs WHERE raw_id=?',(row['raw_id'],)).fetchone()
    value.update(record_id=row['record_id'],snapshot_version_id=row['record_id'],raw_id=row['raw_id'],
                 sha256=raw['sha256'],retrieval_id=row['retrieval_id'],observed_at=row['retrieved_at'])
    return value

def enqueue_analysis(store,plan):
    from .events import EXTRACTION_VERSION
    from .linker import LINKER_VERSION
    endpoints={e['endpoint_id']:e for e in plan.get('all_endpoints',plan.get('endpoints',[]))}
    identities=[]
    for row in store.query('competition_snapshots'):
        endpoint=endpoints.get(row['data'].get('endpoint_id'))
        if not endpoint:continue
        payload={'snapshot_version_id':row['record_id'],'endpoint':endpoint,
                 'edge_versions':plan.get('edge_versions',[]),
                 'extraction_version':EXTRACTION_VERSION,'linker_version':LINKER_VERSION}
        # Exclude volatile cadence receipts from analytical input identity.
        payload['endpoint']={k:v for k,v in endpoint.items() if k not in ('due','due_reason','last_success_at','next_due_at')}
        identities.append(jobs.enqueue(store,payload))
    return identities

def process_analysis(store,*,max_jobs=100,max_seconds=60,allowed_ids=None):
    from .events import extract_events
    from .linker import link_event
    from .change_detector import compare_snapshots
    jobs.ensure_schema(store);recovered=jobs.recover(store)
    began=time.monotonic();results=[]
    for _ in range(max_jobs):
        if time.monotonic()-began>=max_seconds:break
        job=jobs.claim(store,allowed_ids)
        if not job:break
        try:
            payload=job['payload'];row=load_record(store,payload['snapshot_version_id'])
            if row is None:raise ValueError('Saved snapshot version is missing')
            snapshot=_snapshot(store,row);endpoint=payload['endpoint']
            previous=store.db.execute('SELECT record_id FROM records WHERE dataset=? AND source=? AND record_key=? AND sequence<? ORDER BY sequence DESC LIMIT 1',
                ('competition_snapshots',row['source'],row['record_key'],row['sequence'])).fetchone()
            before=_snapshot(store,load_record(store,previous[0])) if previous else None
            delta=compare_snapshots(before,snapshot,endpoint)
            reused=[]
            if delta['status'] in ('no_change','noise_only'):
                for previous_event in store.query('competitive_events'):
                    evidence_snapshot=load_record(store,previous_event['data'].get('snapshot_version_id',''))
                    if (evidence_snapshot and evidence_snapshot['record_key']==row['record_key']
                        and evidence_snapshot['data'].get('body_hash')==snapshot.get('body_hash')
                        and evidence_snapshot['data'].get('publication_time')==snapshot.get('publication_time')):
                        reused.append(previous_event)
            extracted={'events':[],'candidates':[],'issues':['Unchanged business text; prior event versions reused.']} if reused else extract_events(snapshot,endpoint,before=before)
            events=[];links=[];rejected=[]
            for event in extracted['events']+extracted['candidates']+[{**r['data'],'_reuse_record_id':r['record_id']} for r in reused]:
                try:
                    value=dict(event,snapshot_version_id=row['record_id'])
                    if before and event.get('event_type')=='pricing_terms_change':value['before_snapshot_version_id']=before['record_id']
                    reviewed=latest_record(store,'competitive_events',event['event_id'])
                    if event.get('_reuse_record_id'):
                        record_id=event['_reuse_record_id']
                    elif reviewed and reviewed['data'].get('extraction_method') in ('agent_review','human_review') and reviewed['data'].get('snapshot_version_id')==row['record_id']:
                        record_id=reviewed['record_id']
                    else:
                        record_id=put_derived_record(store,'competitive_events',event['event_id'],value,
                            quality='source_claimed_needs_verification' if event['fact_stage']=='extracted_fact' else 'candidate',knowledge_type='company_statement')
                    events.append(record_id)
                    for link in link_event(load_record(store,record_id),payload['edge_versions']):
                        key='link:'+hashlib.sha256(_json([event['event_id'],link['edge_id']]).encode()).hexdigest()
                        links.append(put_derived_record(store,'competitive_event_links',key,link,quality=link['review_status']))
                except (ValueError,TypeError) as exc:
                    rejected.append({'event_id':event.get('event_id'),'reason':str(exc)})
            result={'snapshot_version_id':row['record_id'],'comparison':delta,'events':events,'links':links,
                    'extracted_fact_count':sum(load_record(store,r)['data'].get('fact_stage')=='extracted_fact' and load_record(store,r)['data'].get('extraction_method') not in ('agent_review','human_review') for r in events),
                    'reviewed_fact_count':sum(load_record(store,r)['data'].get('fact_stage')=='extracted_fact' and load_record(store,r)['data'].get('extraction_method') in ('agent_review','human_review') for r in events),
                    'candidate_count':sum(load_record(store,r)['data'].get('fact_stage')=='candidate' for r in events),
                    'proposed_fact_count':len(extracted['events']),'proposed_candidate_count':len(extracted['candidates']),
                    'issues':extracted['issues'],'persistence_rejections':rejected,'reused_event_versions':len(reused),
                    'semantic_coverage_complete':False,'basis':'Rule processing completion is not independently measured semantic recall.'}
            jobs.finish(store,job,result)
            with store.db:store.db.execute('UPDATE competition_endpoint_state SET last_processed_at=? WHERE endpoint_id=?',(now_utc(),endpoint['endpoint_id']))
            results.append({'job_id':job['job_id'],'state':'completed',**result})
        except Exception as exc:
            from ..engine import _error
            error=_error(exc);state=jobs.finish(store,job,error=error)
            results.append({'job_id':job['job_id'],'state':state,'error':error})
    return {'processed':results,'recovered':recovered,'elapsed_seconds':round(time.monotonic()-began,3),'queue':jobs.status(store)['jobs']}

def _next_due(config,endpoint,success,failures):
    now=datetime.now(timezone.utc)
    if not success:
        # Exhausted endpoints wait for explicit reset/configuration change.
        if failures>=int(config.get('competition',{}).get('max_endpoint_failures',5)):return '9999-12-31T00:00:00Z'
        return (now+timedelta(minutes=min(1440,60*2**(failures-1)))).isoformat().replace('+00:00','Z')
    if endpoint.get('cadence')=='watch1daily':return (now+timedelta(days=1)).isoformat().replace('+00:00','Z')
    settings=config.get('local_schedule',{});zone=ZoneInfo(settings.get('timezone','Asia/Shanghai'));local=now.astimezone(zone)
    candidates=[]
    for day in (0,1):
        for clock in settings.get('times',['09:00','20:30','23:30']):
            h,m=map(int,clock.split(':'));point=(local+timedelta(days=day)).replace(hour=h,minute=m,second=0,microsecond=0)
            if point>local:candidates.append(point)
    return min(candidates).astimezone(timezone.utc).isoformat().replace('+00:00','Z')

def endpoint_acquisition_receipts(store,config,plan,due,resources,state,stamp,acquisition):
    """Advance scoped checks only on exact, current acquisition receipts.

    A successful entry page and incomplete discovered children are separate
    outcomes. Identical bytes retrieved from another URL cannot refresh a URL.
    """
    snapshots=store.query('competition_snapshots')
    receipts=[]
    stopped=bool(acquisition.get('stop_reason'))
    scope_ids=list(acquisition.get('effective_scope',{}).get('source_scope_ids',{}).values())
    demanded={r[0] for r in store.db.execute('SELECT DISTINCT t.resource_id FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.scope_id IN ('+','.join('?' for _ in scope_ids)+') AND d.requested_at>=? AND t.resource_id IS NOT NULL',scope_ids+[stamp])} if scope_ids else None
    for endpoint in due:
        matching=[r for r in resources if r['source_id']=='competition_web'
            and r.get('metadata',{}).get('endpoint',{}).get('endpoint_id')==endpoint['endpoint_id']
            and r.get('metadata',{}).get('plan_id')==plan['plan_id']
            and (demanded is None or r['resource_id'] in demanded)]
        entries=[r for r in matching if r.get('url')==endpoint['url']]
        def fresh(resource):
            if resource.get('refresh_state')!='succeeded' or resource.get('upstream_state')!='confirmed_at_response':return False
            if not resource.get('last_refresh_success_at') or resource['last_refresh_success_at']<stamp:return False
            return bool(store.db.execute('SELECT 1 FROM raw_retrievals WHERE retrieval_id=? AND raw_id=? AND source=? AND url=? AND retrieved_at>=?',
                (resource.get('retrieval_id'),resource.get('raw_id'),'competition_web',resource['url'],stamp)).fetchone())
        def normalized(resource):
            return resource.get('parse_state')=='parsed' and any(row['data'].get('resource_key')==resource['resource_key'] and row['raw_id']==resource.get('raw_id') and row['data'].get('valid') for row in snapshots)
        entry_fresh=bool(entries and all(fresh(r) for r in entries))
        entry_normalized=bool(entries and all(normalized(r) for r in entries))
        current_ids={r['resource_key'] for r in entries}
        latest=next((r for r in reversed(snapshots) if r['data'].get('resource_key') in current_ids and any(r['raw_id']==entry.get('raw_id') for entry in entries)),None)
        unfinished=[{'resource_key':r['resource_key'],'url':r.get('url'),'raw_saved':bool(r.get('raw_id')),
            'checked_this_round':fresh(r),'parse_state':r.get('parse_state'),'last_error':r.get('last_error')}
            for r in matching if not fresh(r) or not normalized(r)]
        success=bool(entry_fresh and entry_normalized and not unfinished)
        old=state.get(endpoint['endpoint_id'],{})
        # Resource/time budgets retain pending work without consuming the finite
        # failure allowance. Unsupported sources and real failures back off.
        deferred=bool(stopped and not success)
        network_incomplete=not entry_fresh or any(not fresh(r) for r in matching)
        failures=0 if success or not network_incomplete else old.get('consecutive_failures',0)+(0 if deferred else 1)
        next_due=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace('+00:00','Z') if deferred else _next_due(config,endpoint,success or not network_incomplete,failures)
        error=None if success else ('Acquisition budget interrupted; unfinished scope retained' if deferred else 'Network acquisition incomplete; inspect endpoint receipt' if network_incomplete else 'Normalization or enumeration incomplete; raw checked; no extra network retry')
        receipt={'endpoint_id':endpoint['endpoint_id'],'entry_checked_this_round':entry_fresh,
            'entry_normalization_complete':entry_normalized,'enumerated_resources':len(matching),
            'resources_checked_this_round':sum(fresh(r) for r in matching),'resources_normalized':sum(normalized(r) for r in matching),
            'scope_check_complete':success,'budget_deferred':deferred,'needs_network_retry':network_incomplete or deferred,'unfinished':unfinished,
            'enumeration_basis':'Configured entry plus links actually enumerated; not complete website coverage',
            'next_due_at':next_due,'consecutive_failures':failures}
        receipts.append(receipt)
        for endpoint_id in endpoint.get('endpoint_ids',[endpoint['endpoint_id']]):
            with store.db:store.db.execute('''INSERT INTO competition_endpoint_state(endpoint_id,config_hash,last_attempt_at,last_success_at,next_due_at,last_raw_id,last_snapshot_id,last_error,consecutive_failures)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(endpoint_id) DO UPDATE SET config_hash=excluded.config_hash,last_attempt_at=excluded.last_attempt_at,last_success_at=COALESCE(excluded.last_success_at,competition_endpoint_state.last_success_at),next_due_at=excluded.next_due_at,last_raw_id=COALESCE(excluded.last_raw_id,competition_endpoint_state.last_raw_id),last_snapshot_id=COALESCE(excluded.last_snapshot_id,competition_endpoint_state.last_snapshot_id),last_error=excluded.last_error,consecutive_failures=excluded.consecutive_failures''',
                (endpoint_id,endpoint.get('config_hash'),stamp,now_utc() if success else None,next_due,latest['raw_id'] if latest else None,latest['record_id'] if latest else None,error,failures))
    return receipts

def run_competition(store,config,*,frozen=None,trigger='manual_refresh',analysis_only=False,force=False,used_budget=None):
    settings=config.get('competition',{})
    if not settings.get('enabled',False):return {'enabled':False}
    from ..local_control import Control
    control=Control(store.root)
    try:
        if control.meta('competition_paused',False):return {'enabled':True,'paused':True,'state':'paused'}
    finally:control.close()
    jobs.ensure_schema(store)
    from .watch_planner import plan_watches
    plan=copy.deepcopy(frozen or plan_watches(store,config,force=force))
    if plan is None:return {'enabled':True,'state':'not_configured','gaps':['No saved monitoring plan']}
    # Frozen entity/edge/endpoint identities remain unchanged; cadence gates are
    # operational and checked again after waiting for the single-writer lock.
    state={r['endpoint_id']:dict(r) for r in store.db.execute('SELECT * FROM competition_endpoint_state')}
    stamp=now_utc();due=[]
    for endpoint in plan.get('all_endpoints',plan.get('endpoints',[])):
        saved=state.get(endpoint['endpoint_id'],{})
        if force or saved.get('config_hash')!=endpoint.get('config_hash') or not saved.get('next_due_at') or saved['next_due_at']<=stamp:due.append(endpoint)
    plan['endpoints']=[] if analysis_only else due
    identity='competition-run:'+str(uuid.uuid4());began=time.monotonic()
    with store.db:store.db.execute('INSERT INTO competition_runs(run_id,started_at,trigger,plan_json) VALUES(?,?,?,?)',(identity,stamp,trigger,_json(plan)))
    acquisition=None;budget_gap=None;endpoint_receipts=[]
    cap={'max_requests':60,'max_bytes':30000000,'max_runtime_seconds':180,'max_tasks':80,'max_cost_usd':0,**settings.get('budget',{})}
    if used_budget:
        limits=config.get('budget',{})
        for key,used,default in [('max_requests','requests',250),('max_bytes','bytes',250000000),('max_runtime_seconds','elapsed_seconds',900)]:
            cap[key]=min(cap[key],max(0,limits.get(key,default)-used_budget.get(used,0)))
    if due and not analysis_only:
        if any(cap[key]<=0 for key in ('max_requests','max_bytes','max_runtime_seconds')):budget_gap='Parent or competition budget exhausted; due endpoints retained'
        else:
            from ..machine import execute
            conf=copy.deepcopy(config);conf.update(sources=['competition_web'],data_types=['competition_snapshots'],universe={'mode':'configured','symbols':[]},securities=[],budget=cap,_competition_plan=plan)
            conf['competition_web']={'plan_id':plan['plan_id'],'endpoint_versions':[e['endpoint_version_id'] for e in plan.get('all_endpoints',[])],
                                    'edge_versions':[r['record_id'] for r in plan.get('edge_versions',[])]}
            from ..engine import source_scope_ids
            scope_id=source_scope_ids(conf,{'competition_web':None})['competition_web']
            cycle=store.db.execute('SELECT * FROM competition_cycles WHERE scope_id=?',(scope_id,)).fetchone()
            resume=bool(cycle and cycle['state']=='unfinished')
            boundary=cycle['cycle_started_at'] if resume else stamp
            if resume:
                cycle_plan=json.loads(cycle['plan_json']);conf['_competition_plan']=cycle_plan
                due=cycle_plan['endpoints']
            else:
                with store.db:store.db.execute('INSERT INTO competition_cycles VALUES(?,?,?,?,?) ON CONFLICT(scope_id) DO UPDATE SET cycle_started_at=excluded.cycle_started_at,plan_json=excluded.plan_json,state=excluded.state,last_attempt_at=excluded.last_attempt_at',(scope_id,stamp,_json(plan),'unfinished',stamp))
            args=SimpleNamespace(command='run' if resume else 'update',start=conf['start'],end=conf['end'],sources=None,symbols=None,data_types=None,full_history=True,offline=False,resume_scope_ids=[],max_tasks=cap['max_tasks'])
            acquisition=execute(store,conf,args)
            from ..queue import Queue
            resources=Queue(store).resources()
            endpoint_receipts=endpoint_acquisition_receipts(store,config,plan,due,resources,state,boundary,acquisition)
            remaining=acquisition.get('remaining_tasks',{})
            cycle_state='unfinished' if acquisition.get('stop_reason') and any(remaining.get(key,0) for key in ('pending','deferred','running')) else ('complete' if acquisition.get('task_execution_complete') else 'finished_with_gaps')
            with store.db:store.db.execute('UPDATE competition_cycles SET state=?,last_attempt_at=? WHERE scope_id=?',(cycle_state,stamp,scope_id))
            acquisition['competition_cycle']={'scope_id':scope_id,'cycle_started_at':boundary,'resumed_without_rediscovery':resume}
    job_ids=enqueue_analysis(store,plan)
    analysis=process_analysis(store,max_jobs=int(settings.get('max_analysis_jobs',100)),max_seconds=max(0,min(float(settings.get('max_analysis_seconds',60)),float(cap['max_runtime_seconds'])-(time.monotonic()-began))))
    result={'enabled':True,'run_id':identity,'trigger':trigger,'plan_id':plan['plan_id'],'started_at':stamp,'finished_at':now_utc(),
            'due_endpoints':len(due),'analysis_only':analysis_only,'acquisition':acquisition,'analysis':analysis,'budget_gap':budget_gap,'endpoint_receipts':endpoint_receipts,
            'gaps':plan.get('gaps',[]),'excluded':plan.get('excluded',[]),'coverage_complete':False,
            'coverage_basis':'Configured endpoint inventory only; open web and semantic completeness unverified',
            'elapsed_seconds':round(time.monotonic()-began,3)}
    with store.db:store.db.execute('UPDATE competition_runs SET finished_at=?,result_json=? WHERE run_id=?',(result['finished_at'],_json(result),identity))
    return result
