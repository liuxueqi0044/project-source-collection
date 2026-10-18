"""Read-only delivery receipt; checks database evidence rather than process exit code."""
import hashlib,json,sqlite3
from pathlib import Path
from equity_data.cli import load_config
from equity_data.competition.cli import read_store
from equity_data.competition.reports import inventory
from equity_data.store import now_utc

root=Path(__file__).resolve().parents[1]
read=lambda p:json.loads((root/p).read_text(encoding='utf-8-sig'))
frozen=read('reports/v5/production-freeze.json')
changed=[p for p,h in frozen['files'].items() if hashlib.sha256((root/p).read_bytes()).hexdigest()!=h]
config=load_config(str(root/'configs/live.toml'))
background=read('reports/v5/live-background-request.json')
folder=Path(config['storage'])/'local-runs'/background['request_id']
report=json.loads((folder/'decision-report.json').read_text(encoding='utf-8'))
pointer=Path(config['storage'])/'latest-decision-report.json'
with read_store(config) as store:
    evidence=inventory(store,config)
    legacy=dict(store.db.execute("SELECT dataset,COUNT(*) FROM records WHERE dataset IN ('financial_facts','filings','documents') GROUP BY dataset"))
    duplicate_keys=store.db.execute('SELECT COUNT(*) FROM (SELECT record_id,COUNT(*) n FROM records GROUP BY record_id HAVING n>1)').fetchone()[0]
    evidence['financial_facts_by_company']=[dict(r) for r in store.db.execute("SELECT company_id,COUNT(*) AS stored_versions FROM records WHERE dataset='financial_facts' GROUP BY company_id")]
control=sqlite3.connect((Path(config['storage'])/'local-control.sqlite3').as_uri()+'?mode=ro',uri=True);control.row_factory=sqlite3.Row
request=dict(control.execute('SELECT * FROM local_requests WHERE request_id=?',(background['request_id'],)).fetchone());control.close()
request.pop('payload_json',None)
result={'observed_at':now_utc(),'production_freeze_changes':changed,'background_request':request,
    'background_report':{k:report.get(k) for k in ('started_at','finished_at','trigger','task_execution_complete','acquisition_complete','analysis_processing_complete','parsing_complete','source_checks_complete','coverage_complete','metrics','storage_growth')},
    'endpoint_receipts':report['competition']['endpoint_receipts'],
    'financial_report_pointer_unchanged':hashlib.sha256(pointer.read_bytes()).hexdigest()==background['financial_report_pointer_sha256'],
    'legacy_stored_versions':legacy,'duplicate_record_ids':duplicate_keys,'competition_inventory':evidence,
    'not_natural_schedule_observation':True,'capabilities_not_implied':['all-web completeness','semantic recall pass','historical webpage version coverage','financial impact conclusion']}
path=root/'reports/v5/delivery-audit.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'production_changes':changed,'legacy_versions':legacy,'background_state':request['state'],'pointer_unchanged':result['financial_report_pointer_unchanged'],'output':str(path)}))
