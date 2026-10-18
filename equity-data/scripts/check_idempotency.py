"""Repeat queued backfill and raw-only reprocessing; retain exact count deltas."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from equity_data.cli import load_config
from equity_data.machine import execute
from equity_data.store import Store

p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--out',required=True);args=p.parse_args()
config=load_config(args.config)
config['budget']['max_tasks']=5000
config['budget']['max_runtime_seconds']=900
runargs=argparse.Namespace(command='backfill',sources='sec_edgar',symbols=None,start=None,end=None,offline=True,max_tasks=5000,full_history=False)
with Store(config['storage']) as store:
    def counts():
        return {table:store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('records','raw_blobs','raw_retrievals','record_validations')}
    warm=execute(store,config,runargs)
    before=counts();repeat=execute(store,config,runargs);after=counts()
    runargs.command='reprocess'
    normalization=execute(store,config,runargs)
    before_parse=counts();reparse=execute(store,config,runargs);after_parse=counts()
    report={'scope':'SEC only; no network; one warm-up backfill and one warm-up reparse explicitly excluded from repeat comparison',
            'backfill':{'before':before,'after':after,'delta':{k:after[k]-before[k] for k in before},'run':repeat},
            'reprocess':{'before':before_parse,'after':after_parse,'delta':{k:after_parse[k]-before_parse[k] for k in before_parse},'run':reparse},
            'warm_up':{'backfill':warm,'reprocess':normalization}}
    report['passed']=all(v==0 for section in ('backfill','reprocess') for v in report[section]['delta'].values())
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'backfill_delta':report['backfill']['delta'],'reprocess_delta':report['reprocess']['delta']}))
