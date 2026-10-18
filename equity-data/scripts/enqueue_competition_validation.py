"""Enqueue one bounded refresh for the existing Windows task to execute."""
import hashlib,json
from pathlib import Path
from equity_data.cli import load_config
from equity_data.local_control import Control,base_payload
from equity_data.store import now_utc

config=load_config('configs/live.toml')
payload=base_payload(config);payload.update(competition_only=True,force_competition=True)
control=Control(config['storage'])
try:
    if control.db.execute("SELECT COUNT(*) FROM local_requests WHERE state IN ('pending','running')").fetchone()[0]:
        raise RuntimeError('Wait for current queued work before the controlled background validation')
    request=control.enqueue(payload,'manual_decision_refresh')
finally:control.close()
pointer=Path(config['storage'])/'latest-decision-report.json'
receipt={'enqueued_at':now_utc(),'request_id':request,'trigger':'manual_decision_refresh',
         'executor':'Existing Windows Scheduled Task; enqueue command exits before execution',
         'financial_report_pointer_sha256':hashlib.sha256(pointer.read_bytes()).hexdigest() if pointer.exists() else None}
Path('reports/v5/live-background-request.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(json.dumps(receipt))
