"""Synthetic controlled failures; no assertion of live provider recovery."""
import copy
from types import SimpleNamespace
import pytest
from equity_data.adapters.base import BaseAdapter
from equity_data.engine import run_cycle,run_pending,source_scope_ids,reset_retry_budget
from equity_data.queue import Queue,classify_failure
from equity_data.store import Store

class Source(BaseAdapter):
    data_types=("documents",)
    def __init__(self,source_id="fixture"):
        self.source_id=source_id;self.error=None;self.calls=0;self.parses=0
        self.value=b"one";self.partial=False;self.cached=False;self.fingerprint=None
    def discover(self,ctx,scope):
        ctx.emit({"source_id":self.source_id,"resource_key":"document","kind":"document","data_type":"documents",
            "url":"https://example.invalid/"+self.source_id,"metadata":{}})
        return {"enumeration_complete":True}
    def acquire(self,ctx,resource):
        self.calls+=1
        if self.error:raise RuntimeError(self.error)
        raw=ctx.store.latest_raw(self.source_id,resource["url"]) if self.cached else None
        return self.value,raw or ctx.store.put_raw(self.value,source=self.source_id,url=resource["url"])
    def parse(self,ctx,resource,content,raw):
        self.parses+=1
        return {"status":"partial" if self.partial else "parsed","parsed_count":1}
    def parse_input_fingerprint(self,ctx,resource,raw):return self.fingerprint

def config(*sources):
    return {"market":"US","start":"2024-01-01","end":"2024-01-31","sources":list(sources or ["fixture"]),
        "data_types":["documents"],"scheduler":{"max_task_attempts_per_run":1,"retry_seconds":0}}

@pytest.fixture
def store(tmp_path):
    with Store(tmp_path) as store:yield store

def test_403_multiple_cycles_no_growth_then_real_success_transition(store):
    bad,good=Source("bad"),Source("good");bad.error="HTTP 403 permission denied"
    cfg=config("bad","good");counts=[];checks=[]
    for _ in range(3):
        before=bad.calls
        result=run_cycle(store,SimpleNamespace(),cfg,{"bad":bad,"good":good},mode="update")
        counts.append(bad.calls-before);checks.append(result["source_scope_checks"])
    assert counts==[1,0,0] and good.calls==3
    queue=Queue(store)
    assert len([t for t in queue.tasks() if t["source_id"]=="bad" and t["task_kind"]=="acquire"])==1
    failure=store.db.execute("SELECT * FROM machine_attempts WHERE failure_class='permission'").fetchone()
    assert failure["retry_at"] and failure["error"]
    assert all(next(c for c in row if c["source_id"]=="bad")["last_complete_check_at"] is None for row in checks)
    # Controlled deadline advancement models elapsed cooldown, not provider recovery.
    store.db.execute("UPDATE machine_tasks SET next_attempt_at='2000-01-01T00:00:00Z' WHERE source_id='bad'");store.db.commit()
    store.db.execute("UPDATE machine_request_failures SET next_retry_at='2000-01-01T00:00:00Z' WHERE source_id='bad'");store.db.commit()
    bad.error=None
    recovered=run_cycle(store,SimpleNamespace(),cfg,{"bad":bad,"good":good},mode="update")
    assert bad.calls==2 and next(c for c in recovered["source_scope_checks"] if c["source_id"]=="bad")["last_complete_check_at"]
    assert store.db.execute("SELECT COUNT(*) FROM machine_attempts WHERE failure_class='permission'").fetchone()[0]==1
    assert store.db.execute("SELECT consecutive_failures FROM machine_work_items WHERE source_id='bad' AND resource_id IS NOT NULL").fetchone()[0]==0

@pytest.mark.parametrize("message,kind",[("HTTP 401","authentication"),("HTTP 403","permission"),("HTTP 429","rate_limit"),("ConnectionError reset","transient_network"),("HTTP 503","transient_network")])
def test_failure_classes(message,kind):assert classify_failure(message)==kind

def test_backoff_is_capped_and_survives_new_generation(store):
    source=Source();source.error="ConnectionError";cfg=config();cfg["scheduler"].update(retry_seconds=10,retry_max_seconds=15)
    for _ in range(3):
        run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")
        store.db.execute("UPDATE machine_tasks SET next_attempt_at='2000-01-01T00:00:00Z' WHERE state='failed'");store.db.commit()
        store.db.execute("UPDATE machine_request_failures SET next_retry_at='2000-01-01T00:00:00Z'");store.db.commit()
    from datetime import datetime
    rows=store.db.execute("SELECT finished_at,retry_at FROM machine_attempts WHERE failure_class='transient_network'").fetchall()
    delays=[(datetime.fromisoformat(r[1].replace('Z','+00:00'))-datetime.fromisoformat(r[0].replace('Z','+00:00'))).total_seconds() for r in rows]
    assert len(delays)==3 and 9.9<delays[0]<10.1 and all(14.9<d<15.1 for d in delays[1:])

def test_failed_refresh_and_rediscovery_preserve_saved_parse_axes(store):
    source=Source();cfg=config()
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":source})
    old=Queue(store).resources()[0]
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update",max_tasks=1)
    pending=Queue(store).resources()[0]
    assert pending["state"]=="parsed" and pending["raw_state"]=="saved" and pending["refresh_state"]=="pending"
    source.error="HTTP 403"
    run_pending(store,SimpleNamespace(),cfg,{"fixture":source})
    failed=Queue(store).resources()[0]
    assert failed["raw_id"]==old["raw_id"] and failed["parse_state"]=="parsed" and failed["refresh_state"]=="failed"
    assert failed["upstream_state"]=="unknown_after_failure"

def test_scope_isolation_and_explicit_old_scope_resume(store):
    class Dated(Source):
        def discover(self,ctx,scope):
            ctx.emit({"source_id":self.source_id,"resource_key":scope["start"],"kind":"document","data_type":"documents",
                "url":"https://example.invalid/"+scope["start"],"metadata":{"requested_start":scope["start"]}})
            return {"enumeration_complete":True}
        def parse(self,ctx,resource,content,raw):
            assert ctx.config["start"]==resource["metadata"]["requested_start"]
            return super().parse(ctx,resource,content,raw)
    source=Dated();old=config()
    first=run_cycle(store,SimpleNamespace(),old,{"fixture":source},max_tasks=1)
    old_id=first["effective_scope"]["source_scope_ids"]["fixture"]
    new={**old,"start":"2024-02-01","end":"2024-02-29"}
    run_cycle(store,SimpleNamespace(),new,{"fixture":source})
    assert source.calls==1
    unfinished=[t for t in Queue(store).tasks() if t["state"]!="succeeded"]
    assert len(unfinished)==1 and unfinished[0]["resource"]["metadata"]["requested_start"]==old["start"]
    resumed=run_pending(store,SimpleNamespace(),{**new,"_resume_scope_ids":[old_id]},{"fixture":source})
    assert source.calls==2 and resumed["execution_complete"]
    assert set(resumed["effective_scope"]["resumed_scope_ids"])=={old_id}

def test_partial_parse_blocks_complete_but_preserves_acquisition_axis(store):
    source=Source();source.partial=True
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":source})
    check=result["source_scope_checks"][0]
    assert check["progress"]["acquisition_complete"] and not check["progress"]["parsing_complete"]
    assert check["last_acquisition_success_at"] and check["last_complete_check_at"] is None

def test_cached_current_response_cannot_advance_upstream_clock(store):
    source=Source();cfg=config()
    first=run_cycle(store,SimpleNamespace(),cfg,{"fixture":source})["source_scope_checks"][0]
    source.cached=True
    check=run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")["source_scope_checks"][0]
    assert check["last_complete_check_at"]==first["last_complete_check_at"]
    assert not check["progress"]["complete_check"] and check["progress"]["upstream_refresh_unconfirmed"]

def test_parse_cache_only_latest_input_and_explicit_reprocess_bypasses(store):
    source=Source();source.fingerprint="A";cfg=config()
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":source})
    result=run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")
    assert source.parses==1 and result["parse_cache_hits"]==1
    source.fingerprint="B";run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")
    source.fingerprint="A";run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")
    assert source.parses==3
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},reprocess=True)
    assert source.parses==4

def test_exact_scope_tracks_budget_unresolved_company_and_missing_pages(store):
    source=Source();cfg=config()
    limited=run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},max_tasks=1)["source_scope_checks"][0]
    assert limited["last_complete_check_at"] is None and limited["current_status"]=="budget_limited"
    class Empty(Source):scope_kind="company"
    empty=run_cycle(store,SimpleNamespace(),{**cfg,"securities":[]},{"fixture":Empty()})["source_scope_checks"][0]
    assert not empty["progress"]["discovery_complete"] and empty["last_complete_check_at"] is None
    class Paged(Source):
        def discover(self,ctx,scope):
            super().discover(ctx,scope);return {"enumeration_complete":False,"next_page":"missing"}
    gap=run_cycle(store,SimpleNamespace(),cfg,{"fixture":Paged()},mode="update")["source_scope_checks"][0]
    assert not gap["progress"]["discovery_complete"] and not gap["progress"]["complete_check"]

def test_scope_hash_ignores_budget_but_tracks_pool_and_dates():
    cfg=config();base=source_scope_ids(cfg,{})
    assert source_scope_ids({**cfg,"budget":{"max_tasks":1}}, {})==base
    assert source_scope_ids({**cfg,"end":"2024-02-01"},{})!=base
    assert source_scope_ids({**cfg,"universe":{"symbols":["NEW"]}},{})!=base

def test_new_update_can_fetch_correction_after_old_parse_failed(store):
    class Broken(Source):
        def parse(self,ctx,resource,content,raw):
            if content==b"one":raise ValueError("malformed current response")
            return super().parse(ctx,resource,content,raw)
    source=Broken();cfg=config()
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":source})
    source.value=b"corrected"
    recovered=run_cycle(store,SimpleNamespace(),cfg,{"fixture":source},mode="update")
    assert source.calls==2 and recovered["failed"]==0
    assert Queue(store).resources()[0]["parse_state"]=="parsed"

def test_exhausted_budget_persists_across_generations_and_explicit_reset_recovers(store):
    bad,good=Source("bad"),Source("good");bad.error="HTTP 403"
    cfg=config("bad","good");cfg["scheduler"].update(max_resource_attempts=2,authorization_retry_seconds=0)
    for _ in range(4):run_cycle(store,SimpleNamespace(),cfg,{"bad":bad,"good":good},mode="update")
    assert bad.calls==2 and good.calls==4
    task=next(t for t in Queue(store).tasks() if t["source_id"]=="bad" and t["resource_id"])
    assert task["state"]=="exhausted" and task["next_attempt_at"] is None
    work=store.db.execute("SELECT * FROM machine_work_items WHERE task_id=?",(task["task_id"],)).fetchone()
    assert work["consecutive_failures"]==2 and work["exhausted_at"]
    reset=reset_retry_budget(store,source_ids=["bad"])
    assert reset["reset_count"]==1 and reset["resets"][0]["retry_epoch"]==2
    bad.error=None
    run_cycle(store,SimpleNamespace(),cfg,{"bad":bad,"good":good},mode="update")
    assert bad.calls==3
    attempts=store.db.execute("SELECT state,retry_epoch FROM machine_attempts WHERE task_id=? ORDER BY started_at",(task["task_id"],)).fetchall()
    assert [tuple(r) for r in attempts]==[("failed",1),("exhausted",1),("succeeded",2)]
    assert store.db.execute("SELECT COUNT(*) FROM machine_retry_resets").fetchone()[0]==1

def test_failed_same_http_request_is_gated_across_different_date_resource_keys(store):
    class Rolling(Source):
        def discover(self,ctx,scope):
            ctx.emit({"source_id":self.source_id,"resource_key":"submissions:"+scope["start"],"kind":"document","data_type":"documents",
                "url":"https://example.invalid/current.json","metadata":{"start":scope["start"]}})
            return {"enumeration_complete":True}
    source=Rolling();source.error="HTTP 403";cfg=config()
    for start in ("2024-01-01","2024-01-02","2024-01-03"):
        run_cycle(store,SimpleNamespace(),{**cfg,"start":start},{"fixture":source},mode="update")
    assert source.calls==1
    assert store.db.execute("SELECT COUNT(*) FROM machine_request_failures WHERE consecutive_failures=1").fetchone()[0]==1
    assert len(Queue(store).resources())==3  # distinct parsing windows remain explicit
    assert all(t["state"] in {"failed","retry_wait"} for t in Queue(store).tasks() if t["resource_id"])
