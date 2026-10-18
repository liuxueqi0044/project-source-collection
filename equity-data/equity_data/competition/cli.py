"""Public CLI: import reviewed configuration, refresh, process, query and trace."""
from __future__ import annotations
import argparse
import copy
import json
import sqlite3
from pathlib import Path
from ..cli import load_config
from ..local_control import collector_lock,Control,base_payload
from ..store import Store,now_utc
from .domain import put_derived_record,load_record
from .entities import put_entity,put_entity_link
from .relations import put_relation,review_relation
from .evidence import validate_evidence

def import_bundle(store,bundle,config):
    """Local operator configuration is audit data, never external evidence."""
    from ..http import HttpClient
    from ..budgets import Budget
    from ..adapters.competition_source import CompetitionWebAdapter,validate_url
    from types import SimpleNamespace
    budget=Budget(config.get('competition',{}).get('budget',{'max_requests':40,'max_bytes':30000000,'max_runtime_seconds':300}))
    client=HttpClient(store,user_agent=config.get('sec',{}).get('user_agent') or 'EquityDataCompetition/0.5',**config.get('http',{}));client.budget=budget
    raws={};failures=[]
    for source in bundle.get('sources',[]):
        try:
            content,raw=CompetitionWebAdapter().acquire(SimpleNamespace(client=client,config=config),{'url':source['url'],'metadata':{'endpoint':{**source,'fetch_mode':'http'}}})
            # A repeat review uses the earliest saved identical evidence copy;
            # the new HTTP check remains in retrieval history, not a fake edge revision.
            original=store.db.execute('SELECT retrieval_id,retrieved_at FROM raw_retrievals WHERE raw_id=? AND source=? AND url=? ORDER BY retrieved_at,rowid LIMIT 1',(raw['raw_id'],'competition_web',source['url'])).fetchone()
            if original:raw.update(retrieval_id=original['retrieval_id'],retrieved_at=original['retrieved_at'])
            raws[source['key']]=raw
        except Exception as exc:failures.append({'source_key':source['key'],'error':str(exc)})
    def resolve(value):
        if isinstance(value,list):return [resolve(v) for v in value]
        if not isinstance(value,dict):return value
        if 'source_key' in value:
            raw=raws[value['source_key']]
            return {k:resolve(v) for k,v in value.items() if k!='source_key'}|{k:raw[k] for k in ('raw_id','sha256','retrieval_id','url')}
        return {k:resolve(v) for k,v in value.items()}
    results=[];reviewer=bundle.get('reviewer','configuration_unreviewed')
    for section,writer in [('entities',put_entity),('entity_links',put_entity_link),('relations',put_relation),('endpoints',None)]:
        for original in bundle.get(section,[]):
            try:
                value=resolve(original)
                if writer:identity=writer(store,value,reviewer=reviewer)
                else:
                    from urllib.parse import urlsplit
                    validate_url(value['url'],value.get('allowed_domains',[]))
                    value['evidence_refs']=validate_evidence(store,value.get('evidence_refs',[]),require_external=True)
                    entity=store.db.execute("SELECT record_id FROM records WHERE dataset='entities' AND record_key=? ORDER BY sequence DESC LIMIT 1",(value['entity_id'],)).fetchone()
                    if not entity:raise ValueError('Endpoint entity must be registered')
                    value['entity_version_ids']=[entity[0]]
                    entity_data=load_record(store,entity[0])['data']
                    def host(raw):
                        result=(urlsplit(raw).hostname if '://' in str(raw) else str(raw)).rstrip('.').lower()
                        return result[4:] if result.startswith('www.') else result
                    def within(name,domains):
                        return any(name==domain or name.endswith('.'+domain) for domain in domains)
                    def attributed(refs):
                        return bool(refs) and all(ref.get('entity_id')==value['entity_id'] or value['entity_id'] in ref.get('evidence_entity_ids',[]) for ref in refs)
                    endpoint_host=host(value['url'])
                    allowed={host(domain) for domain in value.get('allowed_domains',[])}
                    requested=value.get('official_status') or value.get('verification_status') or 'unknown'
                    proof=None;authority_errors=[]
                    verified_domains=[];website_refs=[]
                    for website in entity_data.get('websites',[]):
                        if website.get('verification_status') not in {'verified','verified_official'}:continue
                        try:
                            refs=validate_evidence(store,website.get('evidence_refs',[]),require_external=True)
                            if not attributed(refs):raise ValueError('Verified website evidence is not attributed to this entity')
                            verified_domains.append(host(website['url']));website_refs.extend(refs)
                        except ValueError as exc:authority_errors.append(str(exc))
                    if (verified_domains and within(endpoint_host,verified_domains)
                            and all(within(domain,verified_domains) for domain in allowed)
                            and attributed(value['evidence_refs'])):
                        proof={'method':'registered_verified_website','entity_version_id':entity[0],
                               'domains':sorted(set(verified_domains)),'evidence_refs':website_refs,
                               'independent_ownership_verification_claimed':False}
                    review=value.get('authority_review')
                    if proof is None and review:
                        try:
                            if review.get('entity_id')!=value['entity_id']:raise ValueError('Authority review identifies a different entity')
                            if review.get('method') not in {'agent_evidence_review','human_review'}:raise ValueError('Authority review method is not explicit')
                            if not str(review.get('reviewer','')).strip() or len(str(review.get('basis','')).strip())<24:raise ValueError('Authority review requires a named reviewer and substantive ownership basis')
                            if review['method']=='human_review' and not str(review['reviewer']).startswith('human:'):raise ValueError('Human ownership review must have an explicit human: reviewer identity')
                            domains={host(domain) for domain in review.get('domains',[])}
                            if not domains or not within(endpoint_host,domains) or not all(within(domain,domains) for domain in allowed):raise ValueError('Endpoint or redirect domain is outside the reviewed ownership scope')
                            refs=validate_evidence(store,review.get('evidence_refs',[]),require_external=True)
                            if not attributed(refs):raise ValueError('Ownership evidence is not attributed to this entity')
                            if not all(within(host(ref['url']),domains) for ref in refs):raise ValueError('Third-party mentions do not establish official domain ownership')
                            identity_phrase=str(review.get('identity_phrase','')).strip()
                            if not identity_phrase or not any(identity_phrase.casefold() in ref['excerpt'].casefold() for ref in refs):raise ValueError('Ownership review must locate its exact identity phrase in the source')
                            proof={**review,'domains':sorted(domains),'evidence_refs':refs,
                                   'entity_version_id':entity[0],'independent_ownership_verification_claimed':False,
                                   'human_review_claimed':review['method']=='human_review'}
                        except ValueError as exc:authority_errors.append(str(exc))
                    value['requested_official_status']=requested
                    if requested in {'verified_official','official_verified','verified'} and proof:
                        value['official_status']='verified_official'
                        value['authority_validation']={'status':'reviewed','proof':proof,'limits':'Saved source identity and explicit review establish the configured attribution; no independent legal/domain ownership audit is claimed.'}
                        for ref in proof.get('evidence_refs',[]):
                            if ref not in value['evidence_refs']:value['evidence_refs'].append(ref)
                    else:
                        value['official_status']='needs_review'
                        value['authority_validation']={'status':'needs_review','reasons':authority_errors or ['No entity-attributed verified website or explicit scoped ownership review'],'requested_status':requested}
                    identity=put_derived_record(store,'watch_endpoints',value['endpoint_id'],value,reviewer=reviewer,
                        quality='authority_reviewed' if value['authority_validation']['status']=='reviewed' else 'needs_review',knowledge_type='manual_observation' if str(reviewer).startswith('human:') else 'system_inference')
                results.append({'section':section,'record_id':identity})
            except Exception as exc:failures.append({'section':section,'identity':original.get('entity_id') or original.get('edge_id') or original.get('endpoint_id'),'error':str(exc)})
    return {'records':results,'failures':failures,'network_budget':budget.snapshot(),'reviewer':reviewer,'human_review_claimed':reviewer.startswith('human:')}

def read_store(config):
    """Read-only CLI must not create/migrate the database or fetch a URL."""
    root=Path(config['storage'])
    for path in root.glob('*.sqlite3'):
        if path.name=='local-control.sqlite3':continue
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone():
            store=Store.__new__(Store);store.root=root;store.db=db;return store
        db.close()
    raise ValueError('No initialized research database')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='configs/live.toml');parser.add_argument('--out')
    commands=parser.add_subparsers(dest='command',required=True)
    imp=commands.add_parser('import');imp.add_argument('file')
    refresh=commands.add_parser('refresh');refresh.add_argument('--targets');refresh.add_argument('--force',action='store_true')
    commands.add_parser('process');commands.add_parser('status');commands.add_parser('plan')
    query=commands.add_parser('query');query.add_argument('target');query.add_argument('--as-of');query.add_argument('--known-at');query.add_argument('--processed-at')
    trace=commands.add_parser('trace');trace.add_argument('record_id')
    review=commands.add_parser('review');review.add_argument('edge_id');review.add_argument('decision',choices=['confirmed','rejected','needs_review']);review.add_argument('--reason',required=True);review.add_argument('--reviewer',required=True);review.add_argument('--evidence',required=True)
    discover=commands.add_parser('discover');discover.add_argument('--targets')
    args=parser.parse_args(argv);config=load_config(args.config)
    if args.command in ('status','plan','query','trace'):
        with read_store(config) as store:
            if args.command=='status':
                from .reports import inventory
                result=inventory(store,config)
            elif args.command=='plan':
                from .watch_planner import plan_watches
                result=plan_watches(store,config)
            elif args.command=='query':
                from .queries import query_competition
                result=query_competition(store,args.target,as_of=args.as_of,known_at=args.known_at,processed_at=args.processed_at)
            else:
                result=load_record(store,args.record_id)
                if result:
                    result['validated_evidence']=validate_evidence(store,result['data'].get('evidence_refs',[]));result['raw']=dict(store.db.execute('SELECT * FROM raw_blobs WHERE raw_id=?',(result['raw_id'],)).fetchone())
    elif args.command in ('refresh','process'):
        from ..local import drain
        if not config.get('competition',{}).get('enabled',False):raise ValueError('Enable [competition] in the chosen config first')
        if getattr(args,'targets',None):config['competition']['target_ids']=args.targets.split(',')
        payload=base_payload(config);payload.update(competition_only=True,analysis_only=args.command=='process',force_competition=getattr(args,'force',False))
        control=Control(config['storage'])
        try:identity=control.enqueue(payload,'manual_decision_refresh' if args.command=='refresh' else 'unfinished_work_resume')
        finally:control.close()
        result={'request_id':identity,'execution':drain(config,max_jobs=1)}
    else:
        with collector_lock(config['storage']) as owned:
            if not owned:raise RuntimeError('Collector is active; retry this configuration/review command after it finishes')
            with Store(config['storage']) as store:
                if args.command=='import':result=import_bundle(store,json.loads(Path(args.file).read_text(encoding='utf-8-sig')),config)
                elif args.command=='review':result={'record_id':review_relation(store,args.edge_id,args.decision,args.reason,json.loads(Path(args.evidence).read_text(encoding='utf-8-sig')),args.reviewer)}
                else:
                    from .discovery import discover_candidates
                    from .runner import _snapshot
                    from .change_detector import normalize_snapshot
                    targets=args.targets.split(',') if args.targets else config.get('competition',{}).get('target_ids',[])
                    rows=[r for r in store.query('documents') if not targets or r.get('company_id') in targets]
                    maximum=int(config.get('competition',{}).get('max_discovery_documents',100));docs=[]
                    for row in sorted(rows,key=lambda r:r['sequence'],reverse=True)[:maximum]:
                        raw=store.db.execute('SELECT * FROM raw_blobs WHERE raw_id=?',(row['raw_id'],)).fetchone()
                        retrieval=store.db.execute('SELECT url FROM raw_retrievals WHERE retrieval_id=?',(row['retrieval_id'],)).fetchone()
                        normalized=normalize_snapshot((store.root/raw['path']).read_bytes(),{'format':'html'})
                        normalized.update(entity_id=row['company_id'],company_id=row['company_id'],raw_id=row['raw_id'],sha256=raw['sha256'],retrieval_id=row['retrieval_id'],url=retrieval[0],observed_at=row['retrieved_at'],record_id=row['record_id'])
                        docs.append(normalized)
                    result=discover_candidates(docs,store.query('entities'))
                    result['documents_scanned']=len(docs);result['unscanned_documents']=max(0,len(rows)-len(docs))
                    result['persisted']=[];result['rejected']=[]
                    for candidate in result['candidates']:
                        try:result['persisted'].append(put_relation(store,{**candidate,'edge_id':candidate['candidate_id']},reviewer='automatic_candidate_discovery'))
                        except ValueError as exc:result['rejected'].append({'candidate_id':candidate['candidate_id'],'error':str(exc)})
    text=json.dumps(result,ensure_ascii=False,indent=2,default=str)
    if args.out:
        target=Path(args.out);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
        if args.command=='query':
            from .reports import brief_markdown
            target.with_suffix('.md').write_text(brief_markdown(result),encoding='utf-8')
    print(text)
    return 0
