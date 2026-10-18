"""Controlled local-process interruption + missed-slot recovery; real public RSS.

Uses new experiment storage only. Does not log off, reboot or terminate the
production collector. Separate from a naturally observed power outage.
"""
from __future__ import annotations
from datetime import timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from equity_data.local_control import Control,base_payload,stamp,utcnow
from equity_data.cli import load_config
from equity_data.local import tick,write_json


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--name',default='v4_local_recovery');args=parser.parse_args()
    if not args.name.replace('_','').isalnum():raise ValueError('Experiment name must use letters, digits and underscores')
    report=ROOT/'reports/v4'/args.name;report.mkdir(parents=True,exist_ok=False)
    storage=ROOT/'data'/args.name
    if storage.exists():raise FileExistsError('Fresh experiment directory required')
    config_path=ROOT/'configs'/(args.name+'.toml')
    text='''market = "US"
storage = "data/v4_local_recovery"
start = "2024-01-01"
end = "2026-10-10"
sources = ["public_news"]
data_types = ["news", "documents"]
[universe]
mode = "configured"
symbols = []
[http]
requests_per_second = 1
timeout = 20
max_attempts = 2
[budget]
max_tasks = 25
max_requests = 10
max_bytes = 5000000
max_runtime_seconds = 120
[local_schedule]
timezone = "Asia/Shanghai"
times = ["09:00", "20:30", "23:30"]
pulse_minutes = 5
max_between_slot_retries = 2
[local_freshness]
required_types = ["news"]
news = 24
[[news.feeds]]
url = "https://www.federalreserve.gov/feeds/press_all.xml"
allow_full_text = false
'''
    config_path.write_text(text.replace('data/v4_local_recovery','data/'+args.name),encoding='utf-8');cfg=load_config(config_path)
    ready=report/'owner-ready.json';owner=report/'owner.py'
    code='''import os,time,json,sys
from pathlib import Path
sys.path.insert(0,ROOT)
from equity_data.cli import load_config
from equity_data.local_control import Control,collector_lock,base_payload,stamp,utcnow
from datetime import timedelta
cfg=load_config(CONFIG)
with collector_lock(cfg['storage']) as locked:
 assert locked
 control=Control(cfg['storage'])
 control.set_meta('planning_cursor',stamp(utcnow()-timedelta(days=3)))
 identity=control.enqueue(base_payload(cfg),'controlled_process_interruption')
 request=control.claim()
 Path(READY).write_text(json.dumps({'pid':os.getpid(),'at':stamp(),'request':request}))
 time.sleep(120)
'''
    owner.write_text('ROOT='+repr(str(ROOT))+'\nCONFIG='+repr(str(config_path))+'\nREADY='+repr(str(ready))+'\n'+code,encoding='utf-8')
    child=subprocess.Popen([sys.executable,str(owner)],cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+15
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.05)
        evidence=json.loads(ready.read_text());pid=evidence['pid']
        control=Control(storage)
        queued1=control.enqueue(base_payload(cfg),'overlapping_manual_trigger_1')
        queued2=control.enqueue(base_payload(cfg),'overlapping_manual_trigger_2');control.close()
        os.kill(pid,signal.SIGTERM);child.wait(timeout=10)
        recovered=tick(cfg)
        control=Control(storage)
        try:
            requests=[dict(r) for r in control.db.execute('SELECT * FROM local_requests ORDER BY created_at')]
            events=[dict(r) for r in control.db.execute('SELECT * FROM local_events ORDER BY observed_at')]
            slots=[dict(r) for r in control.db.execute('SELECT * FROM local_slots ORDER BY slot_utc')]
        finally:control.close()
        latest=storage/'latest-decision-report.json';actual=json.loads(latest.read_text()) if latest.exists() else {}
        result={'experiment':'Actual local OS process killed; attempted real RSS recovery; simulated missed-clock interval',
            'naturally_observed_reboot':False,'production_task_untouched':True,'started_owner':evidence,
            'overlap_same_request':queued1==queued2,'actual_owner_pid_terminated':pid,
            'restart':recovered,'requests':requests,'events':events,'slots':slots,
            'pass_lock_recovered':recovered['execution']['status']=='drained' and recovered['execution']['recovered_after_exit']==1,
            'pass_missed_slots_merged':len({s['request_id'] for s in slots})==1 and len(slots)>=3,
            'actual_network_requests':actual.get('metrics',{}).get('requests',0),
            'pass_recovery_collection':actual.get('source_checks_complete',False),
            'finished_at':stamp()}
        write_json(report/'results.json',result)
        print(json.dumps({k:v for k,v in result.items() if k.startswith('pass_') or k in {'overlap_same_request','finished_at'}}))
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)


if __name__=='__main__':main()
