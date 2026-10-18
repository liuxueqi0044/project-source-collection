from __future__ import annotations
import argparse
import copy
import csv
import json
import os
import sys
import tomllib
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .store import Store,now_utc
from .http import HttpClient
from .sec import collect_sec,financial_snapshot
from .providers import collect_alpaca,collect_fred,collect_calendar
from .public_docs import collect_public_documents,import_materials
from .qa import validate
from .research import collect_research


def load_config(path):
    path=Path(path).resolve()
    config=tomllib.loads(path.read_text(encoding='utf-8-sig'))
    base=path.parent.parent if path.parent.name=='configs' else path.parent
    config['_base_dir']=str(base)
    config['_config_dir']=str(path.parent)
    config['_config_path']=str(path)
    config['storage']=str((base/config.get('storage','data/sample')).resolve())
    if config.get('market') not in {'US','CN'}: raise ValueError('market must be US or CN')
    for k in ['start','end']:
        datetime.strptime(config[k],'%Y-%m-%d')
    if config['start']>config['end']: raise ValueError('start > end')
    sec=config.setdefault('sec',{})
    # Contact identity is local configuration, never a required issuer-specific
    # production setting. Environment takes priority over optional local file.
    local_identity=base/'configs'/'machine.local.toml'
    if local_identity.exists() and not sec.get('user_agent'):
        sec['user_agent']=tomllib.loads(local_identity.read_text(encoding='utf-8-sig')).get('sec',{}).get('user_agent','')
    sec['user_agent']=os.environ.get(sec.get('user_agent_env','SEC_USER_AGENT'),sec.get('user_agent',''))
    return config


class RunHttpClient(HttpClient):
    def __init__(self,*args,offline=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.offline=offline

    def fetch(self,url,*,source,headers=None,params=None,refresh=True):
        if self.offline:
            from requests import Request
            from .http import redact_url
            full=redact_url(Request('GET',url,params=params).prepare().url)
            if not self.store.latest_raw(source,full):
                raise RuntimeError('Offline raw evidence missing: '+full)
            refresh=False
        return super().fetch(url,source=source,headers=headers,params=params,refresh=refresh)


def run_collect(store,config,args):
    conf=copy.deepcopy(config)
    if args.command=='update':
        zone='America/New_York' if conf['market']=='US' else 'Asia/Shanghai'
        end=datetime.now(ZoneInfo(zone)).date()
        start=end-timedelta(days=conf.get('update_lookback_days',14))
        conf.update(start=start.isoformat(),end=end.isoformat())
        for sec in conf.get('securities',[]): sec.update(start=conf['start'],end=conf['end'])
    for key in ['start','end']:
        value=getattr(args,key,None)
        if value:
            datetime.strptime(value,'%Y-%m-%d')
            conf[key]=value
            for sec in conf.get('securities',[]): sec[key]=value
    if conf['start']>conf['end']: raise ValueError('start > end')
    client=RunHttpClient(store,user_agent=conf['sec'].get('user_agent') or 'EquityDataResearch/0.1',offline=args.offline,**conf.get('http',{}))
    requested=args.sources.split(',') if args.sources else ['sec','public','calendar','alpaca','fred','research']
    mapping={'sec':collect_sec,'public':collect_public_documents,'calendar':collect_calendar,'alpaca':collect_alpaca,'fred':collect_fred,'research':collect_research}
    result={'started_at':now_utc(),'mode':args.command,'market':conf['market'],'start':conf['start'],'end':conf['end'],'sources':{}}
    for source in requested:
        if source not in mapping: raise ValueError('Unknown source: '+source)
        if conf['market']=='CN' and source!='calendar':
            result['sources'][source]={'status':'skipped','reason':'CN adapters not yet implemented; no US rules or providers substituted'}
            continue
        print('Collecting '+source+' ...',file=sys.stderr,flush=True)
        try:
            local=copy.deepcopy(conf)
            if source=='fred' and args.command=='update':
                # New releases revise old observation periods; rescan the configured
                # macro history instead of only the equity filing lookback window.
                local['start']=config.get('fred',{}).get('observation_start',config['start'])
            if source=='calendar':
                local['start']=min([conf['start']]+[s.get('start',conf['start']) for s in conf.get('securities',[])])
                local['end']=max([conf['end']]+[s.get('end',conf['end']) for s in conf.get('securities',[])])
            result['sources'][source]=mapping[source](store,client,local)
        except Exception as exc:
            store.issue('COLLECTOR_FAILED',str(exc),severity='error',details={'source':source})
            result['sources'][source]={'status':'failed','reason':str(exc)}
    result['completed_at']=now_utc()
    result['status']='partial' if any(s.get('status') not in {'ok','disabled'} for s in result['sources'].values()) else 'ok'
    directory=store.root/'runs'; directory.mkdir(exist_ok=True)
    filename=result['started_at'].replace(':','').replace('.','')+'.json'
    (directory/filename).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (store.root/'latest_run.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def export(store,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    tables=[r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cursor=store.db.execute('SELECT * FROM '+table)
        with (out/(table+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow([v[0] for v in cursor.description]);writer.writerows(cursor)
    for dataset, in store.db.execute('SELECT DISTINCT dataset FROM records'):
        with (out/(dataset+'.jsonl')).open('w',encoding='utf-8') as f:
            for row in store.query(dataset,latest=False): f.write(json.dumps(row,ensure_ascii=False)+'\n')
    return {'status':'ok','output':str(out),'tables':tables}


def main(argv=None):
    p=argparse.ArgumentParser(description='Evidence-first equity research data collector')
    p.add_argument('--config',default='configs/machine.toml')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ['discover','backfill','update','run','reprocess','watch']:
        run=sub.add_parser(name);run.add_argument('--sources');run.add_argument('--start');run.add_argument('--end');run.add_argument('--offline',action='store_true')
        run.add_argument('--symbols',help='Comma-separated symbols resolved by the source directory, no code edits')
        run.add_argument('--data-types',help='Comma-separated required data types')
        run.add_argument('--resume-scope-ids',help='Explicit known source scope IDs whose persisted work should also resume')
        run.add_argument('--max-tasks',type=int);run.add_argument('--full-history',action='store_true')
        if name=='watch':
            run.add_argument('--interval',type=float);run.add_argument('--cycles',type=int)
        if name=='reprocess':
            run.add_argument('--reprocess-versions',choices=['latest','all'],default='latest')
            run.add_argument('--raw-ids',help='Comma-separated saved raw IDs to reparse')
    q=sub.add_parser('query');q.add_argument('dataset');q.add_argument('--as-of');q.add_argument('--known-at');q.add_argument('--processed-at');q.add_argument('--company-id');q.add_argument('--security-id');q.add_argument('--all-versions',action='store_true');q.add_argument('--include-rejected',action='store_true');q.add_argument('--limit',type=int,default=20)
    f=sub.add_parser('financial-snapshot');f.add_argument('--company-id',required=True);f.add_argument('--as-of',required=True);f.add_argument('--known-at');f.add_argument('--concept');f.add_argument('--limit',type=int,default=20)
    e=sub.add_parser('export');e.add_argument('--out',default='exports')
    c=sub.add_parser('coverage');c.add_argument('--reference',required=True);c.add_argument('--out',default='reports/reference-coverage')
    ra=sub.add_parser('reprocess-audits');ra.add_argument('--raw-id');ra.add_argument('--resource-id');ra.add_argument('--limit',type=int,default=20)
    retry=sub.add_parser('retry-failed');retry.add_argument('--sources');retry.add_argument('--resource-ids');retry.add_argument('--scope-ids');retry.add_argument('--reason',default='explicit_operator_retry')
    sub.add_parser('validate');sub.add_parser('report');sub.add_parser('status');sub.add_parser('sources')
    t=sub.add_parser('trace');t.add_argument('record_id')
    i=sub.add_parser('import-jsonl');i.add_argument('file')
    a=sub.add_parser('adjust');a.add_argument('--security-id',required=True);a.add_argument('--as-of',required=True);a.add_argument('--known-at');a.add_argument('--method',choices=['split','total_return'],default='split');a.add_argument('--action-source')
    args=p.parse_args(argv)
    try:
        config=load_config(args.config)
        with Store(config['storage']) as store:
            if args.command in {'discover','backfill','update','run','reprocess','watch'}:
                from .machine import execute,watch
                from .local_control import collector_lock
                with collector_lock(config['storage']) as acquired:
                    if not acquired:
                        raise RuntimeError('Another collector owns this storage. Use equity_data.local refresh to queue a request.')
                    result=watch(store,config,args) if args.command=='watch' else execute(store,config,args)
            elif args.command=='retry-failed':
                from .engine import reset_retry_budget
                from .local_control import collector_lock
                def csv_values(value):return [v.strip() for v in value.split(',') if v.strip()] if value else None
                if not any((args.sources,args.resource_ids,args.scope_ids)):
                    raise ValueError('Select at least one --sources, --resource-ids or --scope-ids retry boundary')
                with collector_lock(config['storage']) as acquired:
                    if not acquired:raise RuntimeError('Collector active; retry-budget reset must wait for the owner')
                    result=reset_retry_budget(store,source_ids=csv_values(args.sources),resource_ids=csv_values(args.resource_ids),scope_ids=csv_values(args.scope_ids),reason=args.reason)
            elif args.command=='query':
                rows=store.query(args.dataset,as_of=args.as_of,known_at=args.known_at,processed_at=args.processed_at,company_id=args.company_id,security_id=args.security_id,latest=not args.all_versions,include_rejected=args.include_rejected)
                result={'total':len(rows),'rows':rows[:args.limit]}
            elif args.command=='financial-snapshot':
                result=financial_snapshot(store,args.company_id,args.as_of,args.known_at)
                if args.concept: result['records']=[r for r in result['records'] if r['data'].get('concept')==args.concept]
                result['total']=len(result['records']);result['records']=result['records'][:args.limit]
            elif args.command=='export': result=export(store,Path(config['_base_dir'])/args.out)
            elif args.command=='coverage':
                from .coverage import write_coverage
                result=write_coverage(store,Path(config['_base_dir'])/args.reference,Path(config['_base_dir'])/args.out)
            elif args.command=='reprocess-audits':
                exists=store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='machine_reprocess_audits'").fetchone()
                rows=[]
                if exists:
                    filters=[];values=[]
                    for name in ('raw_id','resource_id'):
                        value=getattr(args,name)
                        if value:filters.append(name+'=?');values.append(value)
                    sql='SELECT * FROM machine_reprocess_audits'+(' WHERE '+' AND '.join(filters) if filters else '')+' ORDER BY parsed_at DESC LIMIT ?'
                    values.append(max(0,args.limit))
                    for row in store.db.execute(sql,values):
                        item=dict(row)
                        for key in list(item):
                            if key.endswith('_json'):item[key[:-5]]=json.loads(item.pop(key))
                        rows.append(item)
                result={'rows':rows,'interpretation':'Historical reparse audits are reconstructed parser outputs, not promoted current research facts.'}
            elif args.command=='trace':
                record=store.db.execute('SELECT * FROM records WHERE record_id=?',(args.record_id,)).fetchone()
                if not record: raise ValueError('record_id not found')
                result=dict(record);result['data']=json.loads(result.pop('data_json'))
                evidence=[dict(r) for r in store.db.execute('SELECT r.source,r.url,r.retrieved_at,b.path,b.sha256,b.media_type FROM record_evidence e JOIN raw_retrievals r USING(retrieval_id) JOIN raw_blobs b ON e.raw_id=b.raw_id WHERE e.record_id=?',(args.record_id,))]
                for row in evidence:row['path']=str(store.root/row['path'])
                result['evidence']=evidence
            elif args.command=='validate':
                result=validate(store,config)
                (store.root/'quality_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                result={k:v for k,v in result.items() if k!='field_coverage'}
            elif args.command=='report':
                from .machine_reports import generate_machine_reports
                result=generate_machine_reports(store,Path(config['_base_dir'])/config.get('reports','reports/machine'))
            elif args.command=='status':
                from .engine import status_report
                result=status_report(store)
            elif args.command=='sources':
                from .registry import capability_matrix
                result=capability_matrix(store)
            elif args.command=='import-jsonl': result=import_materials(store,args.file)
            elif args.command=='adjust':
                from .adjustments import materialize_adjustments
                result=materialize_adjustments(store,args.security_id,method=args.method,as_of=args.as_of,known_at=args.known_at,action_source=args.action_source)
            print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
            return 1 if result.get('status')=='failed' else 0
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        print(json.dumps({'status':'failed','reason':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
