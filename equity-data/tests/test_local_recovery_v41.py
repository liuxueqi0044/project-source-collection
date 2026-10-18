"""Recovery scope transfer and evidence-based extra work, using synthetic inputs."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from equity_data.local_control import (Control, base_payload, build_continuation,
    collector_lock, merge_recovery_payload, scope_key)
from equity_data.queue import Queue
from equity_data.store import Store


def payload(**changes):
    return {"symbols":["ONE"],"universe":{"mode":"configured","symbols":["ONE"]},
        "sources":["sec_edgar"],"data_types":["documents","financial_facts"],
        "full_history":False,**changes}


def config(root):
    return {"market":"US","start":"2020-01-01","storage":str(root),
        "universe":{"mode":"configured","symbols":["ONE"]},"sources":["sec_edgar","official_ir"],
        "data_types":["documents","financial_facts"],"local_schedule":{"times":["09:00","20:30","23:30"],
        "timezone":"Asia/Shanghai","retry_minutes":60,"max_between_slot_retries":2,"pulse_minutes":5}}


@pytest.mark.parametrize("later_hint,earliest",[(None,"2020-02-01"),("2023-01-01","2020-02-01"),("2019-02-01","2019-02-01")])
def test_successor_retains_earliest_window_and_scope_evidence(tmp_path,later_hint,earliest):
    c=Control(tmp_path)
    first=c.enqueue(payload(window_start_hint="2020-02-01",resume_scope_ids=["old"]),"missed_schedule_catchup");c.claim()
    incoming=payload(resume_scope_ids=["new"])
    if later_hint:incoming["window_start_hint"]=later_hint
    second=c.enqueue(incoming,"manual_decision_refresh")
    with collector_lock(tmp_path) as owned:
        assert owned and c.recover_under_lock()==1
    result=json.loads(c.get(second)["payload_json"])
    assert result["window_start_hint"]==earliest
    assert result["resume_scope_ids"]==["new","old"]
    assert result["_local"]["recovered_request_ids"]==[first]
    assert c.get(first)["state"]=="interrupted"
    assert c.request_trigger_summary(second)["categories"]==["manual_refresh","unfinished_work_resume"]
    c.close()


def test_no_successor_resumes_same_request_without_requiring_source_scope(tmp_path):
    c=Control(tmp_path)
    first=c.enqueue(payload(window_start_hint="2020-02-01"),"missed_schedule_catchup");c.claim()
    assert not (tmp_path/"equity.sqlite3").exists()
    with collector_lock(tmp_path):assert c.recover_under_lock()==1
    row=c.get(first)
    assert row["state"]=="pending" and json.loads(row["payload_json"])["window_start_hint"]=="2020-02-01"
    assert json.loads(row["payload_json"])["_local"]["recovered_request_ids"]==[first]
    c.close()


def test_consecutive_crashes_keep_original_window_and_lineage(tmp_path):
    c=Control(tmp_path);ids=[]
    first=c.enqueue(payload(window_start_hint="2020-02-01"),"missed_schedule_catchup");ids.append(first)
    for _ in range(3):
        c.claim();new=c.enqueue(payload(),"manual_decision_refresh");ids.append(new)
        with collector_lock(tmp_path):assert c.recover_under_lock()==1
        value=json.loads(c.get(new)["payload_json"])
        assert value["window_start_hint"]=="2020-02-01"
    assert set(value["_local"]["recovered_request_ids"])==set(ids[:-1])
    assert dict(c.db.execute("SELECT state,COUNT(*) FROM local_requests GROUP BY state"))=={"interrupted":3,"pending":1}
    c.close()


@pytest.mark.parametrize("change",[{"symbols":["TWO"]},{"sources":["fred"]},{"data_types":["guidance"]},
    {"initialization":True,"initialization_id":"other","history_start":"2022-01-01"}])
def test_distinct_scopes_are_not_recovery_successors(tmp_path,change):
    c=Control(tmp_path);old=c.enqueue(payload(window_start_hint="2020-02-01"),"catchup");c.claim()
    new=c.enqueue(payload(**change),"manual")
    with collector_lock(tmp_path):assert c.recover_under_lock()==1
    assert c.get(old)["state"]=="pending" and "window_start_hint" not in json.loads(c.get(new)["payload_json"])
    with pytest.raises(ValueError):merge_recovery_payload(payload(),payload(**change))
    c.close()


def test_recovery_transfer_and_interruption_roll_back_as_one_transaction(tmp_path):
    c=Control(tmp_path);old=c.enqueue(payload(window_start_hint="2020-02-01"),"catchup");c.claim()
    new=c.enqueue(payload(),"manual")
    c.db.execute("CREATE TRIGGER fail_recovery_event BEFORE INSERT ON local_events WHEN NEW.kind='owner_exit_recovery' BEGIN SELECT RAISE(ABORT,'injected transaction failure'); END");c.db.commit()
    with collector_lock(tmp_path),pytest.raises(sqlite3.IntegrityError):c.recover_under_lock()
    assert c.get(old)["state"]=="running"
    assert "window_start_hint" not in json.loads(c.get(new)["payload_json"])
    assert c.db.execute("SELECT COUNT(*) FROM local_events WHERE kind='recovery_window_transfer'").fetchone()[0]==0
    c.db.execute("DROP TRIGGER fail_recovery_event");c.db.commit()
    with collector_lock(tmp_path):c.recover_under_lock()
    assert json.loads(c.get(new)["payload_json"])["window_start_hint"]=="2020-02-01"
    c.close()


def test_metadata_does_not_fork_same_scope_pending_request(tmp_path):
    c=Control(tmp_path);one=c.enqueue(payload(),"manual")
    two=c.enqueue(payload(_local={"recovered_request_ids":["history"]}),"unfinished_work_resume")
    assert one==two and scope_key(payload())==scope_key(payload(_local={"retry_parent_id":"history"}))
    c.close()


def task(q,source,state="pending",failure_class=None,retry_at=None,raw_id=None):
    q.scope_ids={source:"scope:"+source}
    identity=q.enqueue_discovery(source,{"symbol":"ONE"})
    q.db.execute("UPDATE machine_tasks SET state=?,raw_id=?,next_attempt_at=? WHERE task_id=?",(state,raw_id,retry_at,identity))
    q.db.execute("UPDATE machine_work_items SET failure_class=?,next_retry_at=? WHERE task_id=?",(failure_class,retry_at,identity));q.db.commit()
    return identity


def test_continuation_is_scoped_task_evidence_not_partial_semantics(tmp_path):
    with Store(tmp_path) as store:
        q=Queue(store)
        pending=task(q,"sec_edgar")
        task(q,"official_ir","succeeded")  # Missing entry point produces no acquired tasks.
        task(q,"fred","retry_wait","rate_limit","2026-10-10T06:00:00Z")
        task(q,"parse_only","failed","parse",raw_id="raw:existing")
        task(q,"unsupported","blocked","unsupported")
        task(q,"unrelated")
        result=build_continuation(store,{"effective_scope":{"source_scope_ids":{s:"scope:"+s for s in ["sec_edgar","official_ir","fred","parse_only","unsupported"]}}})
    assert {i["source_id"] for i in result["items"]}=={"sec_edgar","fred"}
    assert {i["source_id"] for i in result["suppressed"]}=={"parse_only","unsupported"}
    assert next(i for i in result["items"] if i["source_id"]=="fred")["eligible_after"]=="2026-10-10T06:00:00Z"
    assert next(i for i in result["items"] if i["source_id"]=="sec_edgar")["task_ids"]==[pending]


def test_only_due_retry_sources_are_scheduled_with_correct_scope_and_reason(tmp_path):
    now=datetime(2026,10,10,2,tzinfo=timezone.utc);cfg=config(tmp_path);c=Control(tmp_path)
    c.set_meta("planning_cursor",now.isoformat())
    original=c.enqueue(base_payload(cfg),"manual_decision_refresh",now);c.claim()
    with Store(tmp_path) as store:
        q=Queue(store);task(q,"sec_edgar","retry_wait","transient_network",(now+timedelta(minutes=45)).isoformat())
        task(q,"fred","retry_wait","permission",(now+timedelta(days=1)).isoformat());task(q,"official_ir","succeeded")
        followup=build_continuation(store,{"effective_scope":{"source_scope_ids":{s:"scope:"+s for s in ["sec_edgar","fred","official_ir"]}}})
    c.finish(original,"partial","unused",continuation=followup)
    c.db.execute("UPDATE local_requests SET finished_at=?",(now.isoformat(),));c.db.commit()
    result=c.plan(cfg,now+timedelta(hours=1));assert len(result["requests"])==1
    row=c.get(result["requests"][0]);body=json.loads(row["payload_json"])
    assert body["sources"]==["sec_edgar"] and body["resume_scope_ids"]==["scope:sec_edgar"]
    assert c.request_trigger_summary(row["request_id"])["categories"]==["limited_failure_retry"]
    c.close()


def test_partial_ir_or_unverified_fields_do_not_repeat_good_sources(tmp_path):
    now=datetime(2026,10,10,2,tzinfo=timezone.utc);cfg=config(tmp_path);c=Control(tmp_path)
    c.set_meta("planning_cursor",now.isoformat());request=c.enqueue(base_payload(cfg),"manual",now);c.claim()
    c.finish(request,"partial","no-report",continuation={"items":[],"suppressed":[]})
    c.db.execute("UPDATE local_requests SET finished_at=?",(now.isoformat(),));c.db.commit()
    for hour in [2,3,4,5]:assert not c.plan(cfg,now+timedelta(hours=hour))["requests"]
    assert c.meta("automatic_retries_since_slot",0)==0
    c.close()


def test_initialization_batches_are_not_retried_by_general_schedule(tmp_path):
    now=datetime(2026,10,10,2,tzinfo=timezone.utc);cfg=config(tmp_path);c=Control(tmp_path)
    c.set_meta("planning_cursor",now.isoformat());request=c.enqueue({**base_payload(cfg),"initialization":True,"initialization_id":"id"},"historical_initialization",now);c.claim()
    c.finish(request,"partial","no-report",continuation={"items":[{"source_id":"sec_edgar","scope_ids":["scope"],"kind":"unfinished_work_resume"}]})
    c.db.execute("UPDATE local_requests SET finished_at=?",(now.isoformat(),));c.db.commit()
    assert not c.plan(cfg,now+timedelta(hours=2))["requests"]
    c.close()


def test_normal_and_missed_plan_reasons_are_recorded_without_rewriting_legacy(tmp_path):
    now=datetime(2026,10,10,1,1,tzinfo=timezone.utc);cfg=config(tmp_path);c=Control(tmp_path)
    c.set_meta("planning_cursor",(now-timedelta(minutes=3)).isoformat())
    normal=c.plan(cfg,now)["requests"][0]
    assert c.request_trigger_summary(normal)["categories"]==["normal_schedule"]
    c.claim();c.finish(normal,"completed","evidence")
    later=now+timedelta(days=1,hours=4)
    missed=c.plan(cfg,later)["requests"][0]
    assert c.request_trigger_summary(missed)["categories"]==["missed_schedule_catchup"]
    assert len(c.request_trigger_summary(missed)["events"][0]["scheduled_slots"])==3
    c.close()


def test_initialization_trigger_categories_keep_exact_reason_and_existing_history(tmp_path):
    c=Control(tmp_path)
    original=c.enqueue(payload(),"manual_history_initialization")
    c.enqueue(payload(),"initialization_unfinished_work")
    c.enqueue(payload(),"initialization_continuation")
    summary=c.request_trigger_summary(original)
    assert summary["categories"]==["historical_initialization","unfinished_work_resume"]
    assert [event["reason"] for event in summary["events"]]==["manual_history_initialization","initialization_unfinished_work","initialization_continuation"]
    # An earlier process may already have stored the old classification: do not
    # rewrite that historical category just because the current map is better.
    legacy=c.enqueue(payload(symbols=["OTHER"]),"manual_history_initialization")
    event=c.db.execute("SELECT event_id,details_json FROM local_events WHERE request_id=?",(legacy,)).fetchone()
    details=json.loads(event["details_json"]);details["category"]="legacy_or_unspecified"
    c.db.execute("UPDATE local_events SET details_json=? WHERE event_id=?",(json.dumps(details),event["event_id"]));c.db.commit()
    assert c.request_trigger_summary(legacy)["categories"]==["legacy_or_unspecified"]
    c.close()
