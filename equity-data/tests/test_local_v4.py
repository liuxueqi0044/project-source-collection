"""Behavioral acceptance of persistent local scheduling (no financial test answers)."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import pytest
from equity_data.local_control import Control,base_payload,collector_lock,schedule_slots
from equity_data.local import active_config,drain,freshness_result
from equity_data.local_schedule import spec


def conf(tmp_path):
    return {'market':'US','start':'2024-01-01','end':'2026-10-10','storage':str(tmp_path/'data'),
        '_base_dir':str(tmp_path),'_config_dir':str(tmp_path),'_config_path':str(tmp_path/'live.toml'),
        'universe':{'mode':'configured','symbols':['ONE','TWO']},'sources':['sec_edgar'],
        'data_types':['filings','financial_facts'],
        'local_schedule':{'timezone':'Asia/Shanghai','times':['09:00','20:30','23:30'],'retry_minutes':60,'max_between_slot_retries':2}}


def test_beijing_three_slots_independent_of_host_timezone():
    slots=schedule_slots(['09:00','20:30','23:30'],'Asia/Shanghai',datetime(2026,10,10,tzinfo=timezone.utc),datetime(2026,10,11,tzinfo=timezone.utc))
    assert [s['utc'][11:16] for s in slots]==['01:00','12:30','15:30']
    assert all(s['local'].endswith('+08:00') for s in slots)


def test_missed_slots_coalesce_and_are_not_source_success(tmp_path):
    cfg=conf(tmp_path);control=Control(cfg['storage'])
    control.set_meta('planning_cursor','2026-10-08T00:00:00+00:00')
    result=control.plan(cfg,datetime(2026,10,10,16,tzinfo=timezone.utc))
    assert result['coalesced_missed_slots']==9 and len(result['requests'])==1
    request=control.get(result['requests'][0]);assert request['state']=='pending'
    assert json.loads(request['payload_json'])['window_start_hint']=='2026-10-05'
    assert not control.plan(cfg,datetime(2026,10,10,16,1,tzinfo=timezone.utc))['requests']
    assert control.db.execute('SELECT COUNT(DISTINCT request_id) FROM local_slots').fetchone()[0]==1
    control.close()


def test_pending_and_running_overlap_have_bounded_successor(tmp_path):
    cfg=conf(tmp_path);control=Control(cfg['storage']);payload=base_payload(cfg)
    first=control.enqueue(payload,'one');assert control.enqueue(payload,'two')==first
    control.claim()
    second=control.enqueue(payload,'three');assert second!=first
    assert control.enqueue(payload,'four')==second
    assert control.db.execute('SELECT COUNT(*) FROM local_requests').fetchone()[0]==2
    control.close()


def test_owner_exit_recovery_preserves_history(tmp_path):
    cfg=conf(tmp_path);control=Control(cfg['storage']);payload=base_payload(cfg)
    first=control.enqueue(payload,'one');control.claim();second=control.enqueue(payload,'successor')
    with collector_lock(cfg['storage']) as owned:
        assert owned and control.recover_under_lock()==1
    assert control.get(first)['state']=='interrupted' and control.get(second)['state']=='pending'
    assert control.db.execute("SELECT COUNT(*) FROM local_events WHERE kind='owner_exit_recovery'").fetchone()[0]==1
    control.close()


def test_real_process_death_releases_kernel_lock(tmp_path):
    ready=tmp_path/'ready';script=tmp_path/'owner.py'
    script.write_text('from equity_data.local_control import collector_lock\nfrom pathlib import Path\nimport time,os,json\nwith collector_lock('+repr(str(tmp_path))+') as lock:\n Path('+repr(str(ready))+').write_text(json.dumps({"lock":lock,"pid":os.getpid()}))\n time.sleep(60)\n')
    import os
    child=subprocess.Popen([sys.executable,str(script)],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])})
    try:
        end=time.monotonic()+10
        while not ready.exists() and time.monotonic()<end:time.sleep(.05)
        owner=json.loads(ready.read_text());assert owner['lock']
        with collector_lock(tmp_path) as owned:assert not owned
        # Windows venv launchers can own a child interpreter: kill the actual lock owner.
        import signal
        os.kill(owner['pid'],signal.SIGTERM);child.wait(timeout=10)
        with collector_lock(tmp_path) as owned:assert owned
    finally:
        if child.poll() is None:child.kill();child.wait()


def test_retry_between_slots_is_bounded(tmp_path):
    cfg=conf(tmp_path);control=Control(cfg['storage']);now=datetime(2026,10,10,2,tzinfo=timezone.utc)
    control.set_meta('planning_cursor',now.isoformat())
    from equity_data.store import Store
    from equity_data.queue import Queue
    from equity_data.local_control import build_continuation
    with Store(cfg['storage']) as store:
        queue=Queue(store);queue.scope_ids={'sec_edgar':'scope:retry'}
        queue.enqueue_discovery('sec_edgar',{'market':'US','start':'2024-01-01'})
        continuation=build_continuation(store,{'effective_scope':{'source_scope_ids':queue.scope_ids}})
    identity=control.enqueue(base_payload(cfg),'manual',now);control.claim();control.finish(identity,'partial','evidence',continuation=continuation)
    control.db.execute('UPDATE local_requests SET finished_at=?',(now.isoformat(),));control.db.commit()
    for i in range(1,3):
        plan=control.plan(cfg,now+timedelta(hours=i+1));assert len(plan['requests'])==1
        request=control.claim();control.finish(request['request_id'],'partial','evidence',continuation=continuation)
        control.db.execute('UPDATE local_requests SET finished_at=? WHERE request_id=?',((now+timedelta(hours=i+1)).isoformat(),request['request_id']));control.db.commit()
    assert control.plan(cfg,now+timedelta(hours=5))['requests']==[]
    control.close()


def test_changed_csv_requires_new_request(tmp_path):
    cfg=conf(tmp_path);(tmp_path/'pool.csv').write_text('symbol\nONE\n')
    cfg['universe']={'mode':'configured','csv_file':'pool.csv'};payload=base_payload(cfg)
    assert active_config(cfg,payload)['universe']==cfg['universe']
    (tmp_path/'pool.csv').write_text('symbol\nTWO\n')
    with pytest.raises(ValueError,match='changed'):active_config(cfg,payload)


def test_recent_fetch_does_not_prove_fresh_source_or_parsing():
    now=datetime(2026,10,10,tzinfo=timezone.utc)
    check={'scope_id':'one','source_id':'sec','last_acquisition_success_at':now.isoformat(),
           'last_parse_success_at':None,'progress':{'discovery_complete':True,'acquisition_complete':True,'parsing_complete':False,'upstream_refresh_unconfirmed':[]}}
    result=freshness_result([check],['filings','financial_facts'],{'sec':['filings','financial_facts']},{'default':24},now)
    assert result[0]['freshness']=='satisfied' and result[1]['freshness']=='cannot_confirm'
    check['progress']['upstream_refresh_unconfirmed']=['missing']
    assert freshness_result([check],['filings'],{'sec':['filings']},{},now)[0]['freshness']=='cannot_confirm'
    assert freshness_result([],['filings'],{}, {},now)[0]['freshness']=='cannot_confirm'


def test_restored_database_cannot_claim_freshness_without_live_check():
    now=datetime(2026,10,10,tzinfo=timezone.utc)
    check={'scope_id':'one','source_id':'sec','last_acquisition_success_at':now.isoformat(),'progress':{
        'discovery_complete':True,'acquisition_complete':True,'parsing_complete':True}}
    assert freshness_result([check],['filings'],{'sec':['filings']},{},now,restored=True)[0]['freshness']=='cannot_confirm'


def test_invalid_queued_config_fails_and_next_can_run(tmp_path):
    cfg=conf(tmp_path);control=Control(cfg['storage']);payload=base_payload(cfg);payload['csv_sha256']='bad'
    identity=control.enqueue(payload,'invalid');control.close()
    result=drain(cfg);assert result['completed'][0]['state']=='failed'
    control=Control(cfg['storage']);assert control.get(identity)['error'];control.close()


def test_windows_xml_absolute_paths_explicit_offsets_and_login(tmp_path):
    cfg=conf(tmp_path)
    result=spec(cfg,'DOMAIN\\User',sys.executable,datetime(2026,10,10,tzinfo=timezone.utc))
    root=ET.fromstring(result['xml']);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    daily=root.findall('.//t:CalendarTrigger/t:StartBoundary',ns)
    assert len(daily)==3 and all(d.text.endswith('+08:00') for d in daily)
    assert root.find('.//t:LogonType',ns).text=='InteractiveToken'
    assert root.find('.//t:WakeToRun',ns).text=='false'
    assert root.find('.//t:MultipleInstancesPolicy',ns).text=='IgnoreNew'
    assert root.find('.//t:LogonTrigger',ns) is not None
    assert result['config']==cfg['_config_path'] and Path(result['python']).is_absolute()


def test_windows_schedule_rejects_empty_or_invalid_dispatch_interval(tmp_path):
    cfg=conf(tmp_path);cfg['local_schedule']['times']=[]
    with pytest.raises(ValueError):spec(cfg,'User',sys.executable)
    cfg=conf(tmp_path);cfg['local_schedule']['pulse_minutes']=0
    with pytest.raises(ValueError):spec(cfg,'User',sys.executable)


def test_typed_freshness_cannot_invent_an_undiscovered_type():
    now=datetime(2026,10,10,tzinfo=timezone.utc)
    check={'scope_id':'one','source_id':'sec','last_parse_success_at':now.isoformat(),'progress':{
        'discovery_complete':True,'acquisition_complete':True,'parsing_complete':True,
        'by_data_type':{'financial_facts':{'discovered':1,'acquisition_complete':True,'parse_complete':True,'last_upstream_check_at':now.isoformat()}}}}
    result=freshness_result([check],['financial_facts','guidance'],{'sec':['financial_facts','guidance']},{},now)
    assert result[0]['freshness']=='satisfied' and result[1]['freshness']=='cannot_confirm'


def test_actual_backup_restore_marker_requires_per_source_new_evidence(tmp_path):
    from equity_data.local import restore_state
    now=datetime(2026,10,10,12,tzinfo=timezone.utc)
    boundary=now-timedelta(minutes=5)
    (tmp_path/'RESTORE_STATUS.json').write_text(json.dumps({'state':'restored_requires_live_check','requires_live_check':True,'restored_at':boundary.isoformat()}))
    restored,cutoff=restore_state(tmp_path);assert restored
    checks=[{'scope_id':source,'source_id':source,'last_acquisition_success_at':at.isoformat(),
             'progress':{'discovery_complete':True,'acquisition_complete':True,'parsing_complete':True}}
        for source,at in [('old_source',now-timedelta(hours=1)),('checked_source',now-timedelta(minutes=1))]]
    result=freshness_result(checks,['filings'],{s:['filings'] for s in ['old_source','checked_source']},{},now,restored,cutoff)
    outcomes={s['source_id']:s['freshness'] for s in result[0]['sources']}
    assert outcomes=={'old_source':'cannot_confirm','checked_source':'satisfied'}
