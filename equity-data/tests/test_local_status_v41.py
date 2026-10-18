import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from equity_data.local_control import Control,base_payload
from equity_data.local_status import runtime_status, research_state, status_markdown,reassess_refresh
from equity_data.store import Store


def config(root):
    return {'storage':str(root),'market':'US','start':'2024-01-01','end':'2024-04-30',
        'universe':{'mode':'configured','symbols':['ONE']},'sources':['sec_edgar'],
        'data_types':['filings','documents','financial_facts'],
        'local_schedule':{'times':['09:00','20:30','23:30'],'timezone':'Asia/Shanghai'}}


def test_rolling_refresh_cannot_hide_absent_or_incomplete_history():
    fresh={'required_freshness_satisfied':True}
    assert research_state({'state':'not_initialized'},fresh)=='not_initialized'
    assert research_state({'state':'in_progress','acquisition_complete':False},fresh)=='initialization_incomplete'
    acquired={'state':'acquisition_initialized','acquisition_complete':True,'parsing_complete':False}
    assert research_state(acquired,{'required_freshness_satisfied':False})=='initialized_update_incomplete'
    assert research_state(acquired,fresh)=='initialized_update_checked_with_reported_gaps'


def test_status_has_no_schema_side_effect_and_next_plan_uses_shanghai(tmp_path):
    cfg=config(tmp_path)
    with Store(tmp_path):pass
    c=Control(tmp_path);c.close()
    result=runtime_status(cfg,datetime(2026,10,10,9,tzinfo=timezone.utc))
    assert result['next_normal_schedule']['local']=='2026-10-10T20:30:00+08:00'
    assert result['research_library_state']=='not_initialized'
    assert result['inventory']['raw']['blobs']==0
    with sqlite3.connect(tmp_path/'data.sqlite3') as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='initialization_batches'").fetchone() is None
    assert '库存' in status_markdown(result)


def test_status_preserves_legacy_trigger_without_inventing_login(tmp_path):
    cfg=config(tmp_path);control=Control(tmp_path)
    identity=control.enqueue({'symbols':['ONE']},'scheduled_slots_or_login_catchup')
    control.close()
    result=runtime_status(cfg,datetime(2026,10,10,9,tzinfo=timezone.utc))
    request=result['recent_requests'][0]
    assert request['request_id']==identity
    assert request['events'][0]['details']['reason']=='scheduled_slots_or_login_catchup'
    assert result.get('natural_login_verified') is not True


def test_actual_initialized_partial_keeps_quality_and_refresh_gaps(tmp_path):
    from equity_data.initialization import ensure_plan
    cfg=config(tmp_path)
    with Store(tmp_path) as store:
        ensure_plan(store,cfg)
        store.db.execute("UPDATE initialization_batches SET state='acquisition_initialized',acquisition_complete=1,parsing_complete=0,attempt_count=1")
        store.db.commit()
    (tmp_path/'latest-decision-report.json').write_text(json.dumps({'request_id':'old','required_freshness_satisfied':False}),encoding='utf-8')
    result=runtime_status(cfg)
    assert result['initialization']['acquisition_complete'] is True
    assert result['initialization']['parsing_complete'] is False
    assert result['research_library_state']=='initialized_update_incomplete'
    assert result['latest_collection']['request_id']=='old'


def test_refresh_is_reassessed_for_current_scope_clock_and_not_bootstrap(tmp_path):
    import copy
    cfg=config(tmp_path);now=datetime(2026,10,10,9,tzinfo=timezone.utc)
    check={'source_id':'sec_edgar','scope_id':'same','last_acquisition_success_at':now.isoformat(),
        'last_parse_success_at':now.isoformat(),'progress':{'discovery_complete':True,
        'acquisition_complete':True,'parsing_complete':True,'upstream_refresh_unconfirmed':[]}}
    report={'requested':base_payload(cfg),'current_scope_checks':[check],'required_freshness_satisfied':True}
    inventory={'by_company_and_type':[{'dataset':d,'stored_versions':1} for d in cfg['data_types']]}
    assert reassess_refresh(cfg,report,inventory,now)['required_freshness_satisfied']
    other=copy.deepcopy(report);other['requested']['universe']['symbols']=['OTHER']
    assert not reassess_refresh(cfg,other,inventory,now)['required_freshness_satisfied']
    old=copy.deepcopy(report);old['current_scope_checks'][0].update(last_acquisition_success_at='2020-01-01T00:00:00+00:00',last_parse_success_at='2020-01-01T00:00:00+00:00')
    assert not reassess_refresh(cfg,old,inventory,now)['required_freshness_satisfied']
    bootstrap=copy.deepcopy(report);bootstrap['requested']['initialization']=True
    assert not reassess_refresh(cfg,bootstrap,inventory,now)['required_freshness_satisfied']


def test_initialize_retry_is_queued_with_explicit_reason_not_silently_applied(tmp_path,monkeypatch,capsys):
    from equity_data import local
    cfg=config(tmp_path)
    monkeypatch.setattr(local,'load_config',lambda _:cfg)
    monkeypatch.setattr(local,'drain',lambda *a,**k:{'status':'queued_owner_active'})
    monkeypatch.setattr(local,'status',lambda _: {})
    local.main(['initialize','--retry-blocked','--reason','network_configuration_fixed'])
    control=Control(tmp_path)
    request=control.db.execute('SELECT * FROM local_requests').fetchone();payload=json.loads(request['payload_json'])
    assert payload['initialization'] is True
    assert payload['_local']['retry_initialization']['reason']=='network_configuration_fixed'
    assert request['state']=='pending'
    assert control.request_trigger_summary(request['request_id'])['categories']==['historical_initialization']
    control.close()
