"""Replay retained real-source bytes without network and compare normalized rows."""
import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from equity_data.cli import load_config,run_collect
from equity_data.store import Store


def main():
    config=load_config(BASE/'configs/us.local.toml')
    args=argparse.Namespace(command='backfill',sources=None,start=None,end=None,offline=True)
    with Store(config['storage']) as store:
        before=dict(store.db.execute('SELECT dataset,COUNT(*) FROM records GROUP BY dataset').fetchall())
        replay=run_collect(store,config,args)
        after=dict(store.db.execute('SELECT dataset,COUNT(*) FROM records GROUP BY dataset').fetchall())
        differences={k:after.get(k,0)-before.get(k,0) for k in set(before)|set(after) if before.get(k,0)!=after.get(k,0)}
        errors=[s for s,r in replay['sources'].items() if r.get('status')=='failed' or r.get('failures')]
        result={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'passed' if not differences and not errors else 'failed',
                'network':'disabled','before':before,'after':after,'added_records':differences,'failed_sources':errors,
                'note':'Telemetry and generated calendar acquisition events may append; normalized versions must not duplicate.'}
        out=BASE/'reports';out.mkdir(exist_ok=True)
        (out/'idempotence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
