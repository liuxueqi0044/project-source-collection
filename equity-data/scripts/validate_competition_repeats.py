"""Operational repeat evidence, not an independent semantic reference set."""
import argparse, contextlib, io, json, sqlite3, time
from pathlib import Path
from equity_data.competition.cli import main, read_store
from equity_data.cli import load_config
from equity_data.store import now_utc

def counts(config):
    with read_store(config) as store:
        return {'records':dict(store.db.execute('SELECT dataset,COUNT(*) FROM records GROUP BY dataset')),
                'raw_blobs':store.db.execute('SELECT COUNT(*) FROM raw_blobs').fetchone()[0],
                'retrievals':store.db.execute('SELECT COUNT(*) FROM raw_retrievals').fetchone()[0]}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='configs/competition-validation.toml');parser.add_argument('--out',default='reports/v5/repeat-evidence.json');args=parser.parse_args()
    config=load_config(args.config);steps=[]
    for command in (['refresh'],['refresh','--force'],['process'],['refresh']):
        before=counts(config);started=now_utc();clock=time.monotonic();capture=io.StringIO()
        with contextlib.redirect_stdout(capture):main(['--config',args.config]+command)
        result=json.loads(capture.getvalue());request_id=result['request_id']
        path=Path(config['storage'])/'local-runs'/request_id/'decision-report.json'
        report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        after=counts(config);names=set(before['records'])|set(after['records'])
        steps.append({'command':command,'started_at':started,'elapsed_seconds':time.monotonic()-clock,
            'request_id':request_id,'report_path':str(path),'metrics':report.get('metrics'),
            'axes':{k:report.get(k) for k in ['task_execution_complete','acquisition_complete','analysis_processing_complete','parsing_complete','source_checks_complete','coverage_complete']},
            'record_version_delta':{k:after['records'].get(k,0)-before['records'].get(k,0) for k in sorted(names)},
            'raw_blob_delta':after['raw_blobs']-before['raw_blobs'],'retrieval_delta':after['retrievals']-before['retrievals']})
        Path(args.out).write_text(json.dumps({'steps':steps,'limitation':'Live pages may change. New raw/snapshot versions are not automatically duplicate business events.'},indent=2),encoding='utf-8')
        print(json.dumps(steps[-1]))
