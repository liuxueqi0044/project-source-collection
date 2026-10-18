"""Synthetic state-transition tests; these do not certify live channel coverage."""
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from equity_data import machine
from equity_data.adapters.base import BaseAdapter, persist_observations
from equity_data.engine import run_cycle, run_pending
from equity_data.queue import Queue, StaleTaskOwner, exclusive_worker
from equity_data.store import Store


class ObservedScope(BaseAdapter):
    source_id="fixture"
    scope_kind="company"
    data_types=("documents",)

    def __init__(self):
        self.scopes=[]
        self.security_dates=[]

    def discover(self,ctx,scope):
        self.scopes.append(scope)
        self.security_dates.append([(s["start"],s["end"]) for s in ctx.config["securities"]])
        return {"enumeration_complete":True,"discovered":0}


def config():
    return {"market":"US","start":"2024-01-01","end":"2024-12-31","sources":["fixture"],
            "securities":[{"company_id":"sec:0000009999","cik":"9999","symbol":"SYNTHETIC",
                           "start":"2020-01-01","end":"2020-01-31"}],"data_types":["documents"],
            "sec":{},"http":{},"budget":{},"scheduler":{"max_task_attempts_per_run":1,"retry_seconds":0}}


def args(command="backfill",**values):
    return SimpleNamespace(**dict({"command":command,"sources":None,"symbols":None,"start":None,"end":None,
                                  "offline":True,"max_tasks":None,"full_history":False,"interval":1,"cycles":2},**values))


@pytest.mark.parametrize("command,start,end",[("backfill","2025-02-01","2025-02-28"),
                                             ("update","2025-02-01","2025-02-28"),("update",None,None)])
def test_effective_dates_reach_scope_and_security_consuming_adapters(tmp_path,command,start,end):
    conf=config()
    unchanged=copy.deepcopy(conf)
    adapter=ObservedScope()
    with Store(tmp_path) as store,patch.object(machine,"build_adapters",return_value={"fixture":adapter}):
        machine.execute(store,conf,args(command,start=start,end=end))
    today=datetime.now(ZoneInfo("America/New_York")).date()
    expected=(start or (today-timedelta(days=14)).isoformat(),end or today.isoformat())
    assert (adapter.scopes[0]["start"],adapter.scopes[0]["end"])==expected
    assert adapter.security_dates==[[expected]]
    assert conf==unchanged


def test_ordinary_backfill_retains_security_date_default(tmp_path):
    adapter=ObservedScope()
    with Store(tmp_path) as store,patch.object(machine,"build_adapters",return_value={"fixture":adapter}):
        machine.execute(store,config(),args())
    assert (adapter.scopes[0]["start"],adapter.scopes[0]["end"])==("2020-01-01","2020-01-31")


def test_one_bound_cli_override_validates_combined_security_interval(tmp_path):
    with Store(tmp_path) as store,patch.object(machine,"build_adapters",return_value={"fixture":ObservedScope()}):
        with pytest.raises(ValueError,match="Security effective start"):
            machine.execute(store,config(),args(start="2024-02-01"))


def test_directory_network_failure_leaves_independent_global_channel_running(tmp_path):
    issuer=ObservedScope()
    global_source=ObservedScope()
    global_source.source_id="global"
    global_source.scope_kind="global"
    conf=config()
    conf["sources"].append("global")
    with Store(tmp_path) as store,patch.object(machine,"build_adapters",return_value={"fixture":issuer,"global":global_source}), \
         patch.object(machine,"resolve_universe",side_effect=TimeoutError("temporary network outage")):
        result=machine.execute(store,conf,args())
        assert len(global_source.scopes)==1 and not issuer.scopes
        assert result["succeeded"]==1
        assert any(g["code"]=="UNIVERSE_TEMPORARILY_UNAVAILABLE" for g in Queue(store).gaps())


def test_same_pool_cached_identity_only_never_resolves_new_symbol(tmp_path):
    adapter=ObservedScope()
    conf=config()
    resolved={"securities":copy.deepcopy(conf["securities"]),"gaps":[]}
    with Store(tmp_path) as store,patch.object(machine,"build_adapters",return_value={"fixture":adapter}):
        with patch.object(machine,"resolve_universe",return_value=resolved):
            machine.execute(store,conf,args())
        with patch.object(machine,"resolve_universe",side_effect=TimeoutError("offline")):
            machine.execute(store,conf,args("update"))
            assert len(adapter.scopes)==2
            assert adapter.scopes[-1]["security"]["identity_cache_stale"]
            machine.execute(store,conf,args("update",symbols="OTHER_UNRESOLVED"))
            assert len(adapter.scopes)==2
        assert any(g["code"]=="UNIVERSE_CACHED_STALE" for g in Queue(store).gaps())


def test_watch_transient_failure_appends_evidence_then_recovers(tmp_path):
    with Store(tmp_path) as store,patch.object(machine,"execute",side_effect=[TimeoutError("offline"),{"execution_complete":True}]) as execute, \
         patch.object(machine.time,"sleep") as sleep:
        result=machine.watch(store,config(),args("watch"))
        assert result["status"]=="completed" and result["failed_cycles"]==1
        assert execute.call_count==2 and sleep.call_args.args[0]==5
        events=[json.loads(line) for line in (store.root/"watch_events.jsonl").read_text().splitlines()]
        assert events[0]["result"]["status"]=="cycle_failed" and events[1]["consecutive_failures"]==0
        assert Queue(store).gaps(unresolved=False)[0]["resolved_at"]


def test_watch_failure_limit_is_bounded_and_configuration_does_not_retry(tmp_path):
    conf=config()
    conf["scheduler"]["max_consecutive_cycle_failures"]=2
    with Store(tmp_path) as store,patch.object(machine,"execute",side_effect=TimeoutError("offline")) as execute,patch.object(machine.time,"sleep"):
        result=machine.watch(store,conf,args("watch",cycles=10))
        assert result["status"]=="failed" and execute.call_count==2
    with Store(tmp_path) as store,patch.object(machine,"execute",side_effect=ValueError("invalid config")) as execute:
        with pytest.raises(ValueError):machine.watch(store,conf,args("watch",cycles=10))
        assert execute.call_count==1


def test_os_lock_blocks_second_worker_even_if_database_lease_expired(tmp_path):
    with Store(tmp_path) as store,exclusive_worker(store):
        with pytest.raises(RuntimeError,match="Another acquisition worker"):
            run_cycle(store,SimpleNamespace(),config(),{"fixture":ObservedScope()})
    with Store(tmp_path) as store:
        assert run_cycle(store,SimpleNamespace(),config(),{"fixture":ObservedScope()})["execution_complete"]


def test_recovered_task_rejects_old_worker_finish_and_checkpoint(tmp_path):
    with Store(tmp_path) as store:
        queue=Queue(store)
        queue.emit({"source_id":"fixture","resource_key":"doc","kind":"document","data_type":"documents"})
        old=queue.claim(queue.start_run("backfill","first"))
        queue.db.execute("UPDATE machine_runs SET lease_until='2000-01-01T00:00:00Z'")
        queue.db.execute("UPDATE machine_tasks SET lease_until='2000-01-01T00:00:00Z'")
        queue.db.commit()
        assert queue.recover_expired()==1
        newer=queue.claim(queue.start_run("resume","second"))
        with pytest.raises(StaleTaskOwner):queue.finish(old,{"status":"parsed"})
        with pytest.raises(StaleTaskOwner):queue.fail(old,"late failure")
        raw=store.put_raw(b"evidence",source="fixture",url="https://example.invalid/doc")
        with pytest.raises(StaleTaskOwner):queue.acquisition_checkpoint(old,raw)
        queue.acquisition_checkpoint(newer,raw)
        queue.finish(newer,{"status":"parsed"},resource_state="parsed")


class RevisionAdapter(ObservedScope):
    def __init__(self):
        super().__init__()
        self.content=b"original"
        self.fetch_count=0
        self.parser_version="fixture-v1"
        self.seen=[]

    def discover(self,ctx,scope):
        ctx.emit({"source_id":self.source_id,"resource_key":"mutable-document","company_id":scope["company_id"],
                  "kind":"document","data_type":"documents","url":"https://example.invalid/doc","metadata":{}})
        return {"enumeration_complete":True,"discovered":1}

    def acquire(self,ctx,resource):
        self.fetch_count+=1
        return self.content,ctx.store.put_raw(self.content,source=self.source_id,url=resource["url"])

    def parse(self,ctx,resource,content,raw):
        self.seen.append(content)
        result={"parser_version":self.parser_version,"observations":[{"dataset":"observations","record_key":"text",
                "data":{"text":content.decode()},"evidence_excerpt":content.decode(),"source_locator":"body"}],"issues":[]}
        return persist_observations(ctx,resource,result,raw)


def test_all_raw_versions_reparse_isolated_and_repeat_idempotent(tmp_path):
    adapter=RevisionAdapter()
    with Store(tmp_path) as store:
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
        original_record=store.query("observations")[0]
        adapter.content=b"corrected"
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},mode="update")
        original_versions=[tuple(row) for row in store.db.execute("SELECT raw_id,retrieval_id,acquired_at FROM machine_resource_versions ORDER BY rowid")]
        pointer=(Queue(store).resources()[0]["raw_id"],Queue(store).resources()[0]["retrieval_id"])
        adapter.parser_version="fixture-v2"
        adapter.seen=[]
        result=run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},reprocess=True,reprocess_versions="all")
        assert result["succeeded"]==2 and adapter.seen==[b"original",b"corrected"] and adapter.fetch_count==2
        assert store.query("observations")[0]["data"]["text"]=="corrected"
        assert store.query("observations",known_at=original_record["retrieved_at"])[0]["data"]["text"]=="original"
        assert (Queue(store).resources()[0]["raw_id"],Queue(store).resources()[0]["retrieval_id"])==pointer
        assert [tuple(row) for row in store.db.execute("SELECT raw_id,retrieval_id,acquired_at FROM machine_resource_versions ORDER BY rowid")]==original_versions
        audit=store.db.execute("SELECT * FROM machine_reprocess_audits").fetchone()
        assert audit and json.loads(audit["records_json"])[0]["parser_version"]=="fixture-v2"
        assert json.loads(audit["effects_json"])["production_records_modified"] is False
        before=store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},reprocess=True,reprocess_versions="all")
        assert store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]==before
        assert store.db.execute("SELECT COUNT(*) FROM machine_reprocess_audits").fetchone()[0]==1


def test_raw_id_selection_does_not_reparse_latest_or_fetch(tmp_path):
    adapter=RevisionAdapter()
    with Store(tmp_path) as store:
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
        older=Queue(store).resources()[0]["raw_id"]
        adapter.content=b"new"
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},mode="update")
        adapter.seen=[]
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},reprocess=True,reprocess_versions="all",raw_ids=[older])
        assert adapter.seen==[b"original"] and adapter.fetch_count==2
        assert store.query("observations")[0]["data"]["text"]=="new"


def test_restart_uses_persisted_original_without_network(tmp_path):
    adapter=RevisionAdapter()
    with Store(tmp_path) as store:
        with patch.object(adapter,"parse",side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
    with Store(tmp_path) as store:
        result=run_pending(store,SimpleNamespace(),config(),{"fixture":adapter})
        assert result["execution_complete"] and adapter.fetch_count==1


def test_abrupt_process_exit_releases_os_lock_and_recovers_checkpoint(tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path
    child="""
import os, sys, json
from equity_data.store import Store
from equity_data.queue import Queue, exclusive_worker
from equity_data.engine import _configure_scopes
with Store(sys.argv[1]) as store, exclusive_worker(store):
    queue=Queue(store)
    _configure_scopes(queue,json.loads(sys.argv[2]),{})
    queue.emit({'source_id':'fixture','resource_key':'mutable-document','company_id':'sec:0000009999',
                'kind':'document','data_type':'documents','url':'https://example.invalid/doc','metadata':{}},'crash-probe')
    run=queue.start_run('backfill','crash-probe',lease_seconds=0.01)
    task=queue.claim(run,lease_seconds=0.01)
    raw=store.put_raw(b'original',source='fixture',url='https://example.invalid/doc')
    queue.acquisition_checkpoint(task,raw)
    os._exit(7)
"""
    result=subprocess.run([sys.executable,"-c",child,str(tmp_path),json.dumps(config())],cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=10)
    assert result.returncode==7,result.stderr.decode(errors="replace")
    time.sleep(0.03)
    adapter=RevisionAdapter()
    with Store(tmp_path) as store:
        recovered=run_pending(store,SimpleNamespace(),config(),{"fixture":adapter})
        assert recovered["execution_complete"]
        assert adapter.fetch_count==0 and adapter.seen==[b"original"]
        assert store.db.execute("SELECT COUNT(*) FROM machine_attempts WHERE state='interrupted'").fetchone()[0]==1
