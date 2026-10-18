from types import SimpleNamespace

import pytest

from equity_data.adapters.base import BaseAdapter, DependencyMissing
from equity_data.budgets import Budget, BudgetExceeded
from equity_data.engine import run_cycle, run_pending, status_report
from equity_data.queue import Queue
from equity_data.store import Store


class FixtureAdapter(BaseAdapter):
    source_id="fixture"
    scope_kind="company"
    data_types=("documents",)

    def __init__(self, count=1):
        self.count=count
        self.fetch_count=0
        self.parse_count=0
        self.content=b"first"
        self.fail_parse=False
        self.fail_fetch=False
        self.partial=False

    def discover(self,ctx,scope):
        for index in range(self.count):
            ctx.emit({"source_id":self.source_id,"resource_key":scope["company_id"]+":"+str(index),
                      "company_id":scope["company_id"],"kind":"document","data_type":"documents",
                      "url":"https://example.invalid/"+str(index),"metadata":{}})
        return {"enumeration_complete":True,"discovered":self.count}

    def acquire(self,ctx,resource):
        self.fetch_count+=1
        if self.fail_fetch:
            raise RuntimeError("source offline")
        raw=ctx.store.put_raw(self.content,source=self.source_id,url=resource["url"])
        return self.content,raw

    def parse(self,ctx,resource,content,raw):
        self.parse_count+=1
        if self.fail_parse:
            raise ValueError("cannot parse current fields")
        record=ctx.store.put_record("documents",resource["resource_key"],{"text":content.decode()},
                    source=self.source_id,raw_id=raw["raw_id"],company_id=resource["company_id"],
                    available_at=raw["retrieved_at"],availability_basis="first_observed_current_copy")
        return {"status":"partial" if self.partial else "parsed","record_ids":[record],"parsed_count":1,"errors":["unsupported field"] if self.partial else []}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path) as value:
        yield value


def config(*sources):
    return {"market":"US","sources":list(sources or ["fixture"]),"securities":[{"company_id":"sec:001","cik":"001"}],
            "start":"2024-01-01","end":"2025-12-31","data_types":["documents"],
            "scheduler":{"max_task_attempts_per_run":1,"retry_seconds":0}}


def test_added_issuer_via_configuration_no_fixed_company_logic(store):
    adapter=FixtureAdapter()
    cfg=config()
    cfg["securities"].append({"company_id":"sec:777","cik":"777"})
    result=run_cycle(store,SimpleNamespace(),cfg,{"fixture":adapter})
    assert result["succeeded"]==4
    assert {r["company_id"] for r in store.query("documents")}=={"sec:001","sec:777"}


def test_backfill_idempotence_and_update_revision(store):
    adapter=FixtureAdapter()
    client=SimpleNamespace()
    run_cycle(store,client,config(),{"fixture":adapter})
    first=adapter.fetch_count
    repeat=run_cycle(store,client,config(),{"fixture":adapter})
    assert repeat["executed"]==0 and adapter.fetch_count==first
    adapter.content=b"revised"
    run_cycle(store,client,config(),{"fixture":adapter},mode="update")
    assert adapter.fetch_count==2
    assert len(store.query("documents",latest=False))==2
    assert store.query("documents")[0]["data"]["text"]=="revised"


def test_budget_cap_preserves_complete_emitted_manifest_and_resume(store):
    adapter=FixtureAdapter(4)
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},max_tasks=2)
    report=status_report(store)
    assert result["stop_reason"]=="max_tasks"
    assert report["summary"]["resource_count"]==4
    assert report["summary"]["resource_states"]=={"parsed":1,"discovered_not_downloaded":3}
    resumed=run_pending(store,SimpleNamespace(),config(),{"fixture":adapter})
    assert resumed["succeeded"]==3
    assert adapter.fetch_count==4


def test_source_failure_isolated_from_other_source(store):
    broken=FixtureAdapter()
    broken.source_id="broken"
    broken.fail_fetch=True
    good=FixtureAdapter()
    result=run_cycle(store,SimpleNamespace(),config("broken","fixture"),{"broken":broken,"fixture":good})
    assert result["failed"]==1
    assert good.parse_count==1
    assert status_report(store)["summary"]["resource_states"]=={"download_failed":1,"parsed":1}


def test_parse_failure_resume_uses_saved_bytes_no_redownload(store):
    adapter=FixtureAdapter()
    adapter.fail_parse=True
    run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
    assert status_report(store)["resources"][0]["state"]=="downloaded_unparsed"
    adapter.fail_parse=False
    result=run_pending(store,SimpleNamespace(),config(),{"fixture":adapter})
    assert result["succeeded"]==1
    assert adapter.fetch_count==1
    assert not [g for g in status_report(store)["gaps"] if g["code"]=="PARSE_FAILED"]


def test_raw_reprocess_uses_no_network_even_with_old_pending_downloads(store):
    adapter=FixtureAdapter(2)
    run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},max_tasks=2)
    previous=adapter.fetch_count
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},reprocess=True)
    assert result["succeeded"]==1
    assert adapter.fetch_count==previous
    assert any(g["code"]=="RAW_NOT_SAVED" for g in status_report(store)["gaps"])


def test_partial_fields_are_not_full_parse_success(store):
    adapter=FixtureAdapter()
    adapter.partial=True
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
    assert result["partial"]==1
    assert status_report(store)["resources"][0]["state"]=="partial_parse"
    assert not result["coverage_complete"]


def test_missing_authorization_blocks_and_reports_dependency(store):
    class Paid(FixtureAdapter):
        def acquire(self,ctx,resource):
            raise DependencyMissing("MISSING_API_KEY environment variable")
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":Paid()})
    assert result["blocked"]==1
    assert status_report(store)["resources"][0]["state"]=="missing_authorization"


def test_http_budget_is_deferred_not_absent_data(store):
    class Limited(FixtureAdapter):
        def acquire(self,ctx,resource):
            raise BudgetExceeded("Request budget exhausted")
    result=run_cycle(store,SimpleNamespace(budget=Budget()),config(),{"fixture":Limited()})
    assert result["deferred"]==1
    assert result["budget"]["requests"]==0
    assert status_report(store)["resources"][0]["state"]=="discovered_not_downloaded"


def test_unknown_adapter_becomes_explicit_gap(store):
    run_cycle(store,SimpleNamespace(),config("not_installed"),{})
    assert status_report(store)["gaps"][0]["code"]=="SOURCE_ADAPTER_UNAVAILABLE"


def test_discovery_incomplete_is_never_claimed_complete(store):
    class Incomplete(FixtureAdapter):
        def discover(self,ctx,scope):
            super().discover(ctx,scope)
            return {"enumeration_complete":False,"discovered":1,"continuation":"next-page"}
    run_cycle(store,SimpleNamespace(),config(),{"fixture":Incomplete()})
    assert any(g["code"]=="ENUMERATION_INCOMPLETE" for g in status_report(store)["gaps"])


def test_run_metadata_redacts_credentials(store):
    cfg=config()
    cfg["api_key"]="do-not-persist"
    cfg["endpoint"]="https://example.invalid/?api_key=secret"
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":FixtureAdapter()},discover_only=True)
    saved=store.db.execute("SELECT config_json FROM machine_runs").fetchone()[0]
    assert "do-not-persist" not in saved and "=secret" not in saved


def test_exact_task_limit_at_complete_queue_is_not_truncation(store):
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":FixtureAdapter()},max_tasks=2)
    assert result["executed"]==2
    assert result["stop_reason"] is None
    assert result["execution_complete"] is True


def test_generation_coverage_drop_keeps_historical_resources(store):
    adapter=FixtureAdapter(3)
    run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
    adapter.count=1
    result=run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},mode="update")
    assert result["coverage_drop"][0]["previous"]==3
    assert result["coverage_drop"][0]["current"]==1
    assert len(Queue(store).resources())==3
    assert any(g["code"]=="COVERAGE_DROP" for g in status_report(store)["gaps"])


def test_source_freshness_threshold_explicit(store):
    run_cycle(store,SimpleNamespace(),config(),{"fixture":FixtureAdapter()})
    source=status_report(store)["source_health"][0]
    assert source["freshness_threshold_hours"]==48
    assert source["freshness_status"]=="recently_acquired"


def test_completed_resume_resolves_budget_gap_history(store):
    adapter=FixtureAdapter(2)
    run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter},max_tasks=2)
    assert any(g["code"]=="RUN_RESOURCE_LIMIT" for g in status_report(store)["gaps"])
    run_pending(store,SimpleNamespace(),config(),{"fixture":adapter})
    assert not any(g["code"]=="RUN_RESOURCE_LIMIT" for g in status_report(store)["gaps"])
    assert any(g["code"]=="RUN_RESOURCE_LIMIT" and g["resolved_at"] for g in Queue(store).gaps(unresolved=False))


def test_keyboard_interrupt_immediate_resume_keeps_raw_checkpoint(store):
    class Interrupted(FixtureAdapter):
        def parse(self,ctx,resource,content,raw):
            raise KeyboardInterrupt()
    adapter=Interrupted()
    with pytest.raises(KeyboardInterrupt):
        run_cycle(store,SimpleNamespace(),config(),{"fixture":adapter})
    report=status_report(store)
    assert report["latest_run"]["state"]=="interrupted"
    assert report["summary"]["task_states"]=={"succeeded":1,"pending":1}
    resumed=FixtureAdapter()
    run_pending(store,SimpleNamespace(),config(),{"fixture":resumed})
    assert resumed.fetch_count==0 and resumed.parse_count==1


def test_invalid_resource_budget_does_not_leave_running_run(store):
    with pytest.raises(ValueError,match="max_tasks"):
        run_cycle(store,SimpleNamespace(),config(),{"fixture":FixtureAdapter()},max_tasks=-1)
    assert status_report(store)["latest_run"]["state"]=="failed"


class DependentFixture(FixtureAdapter):
    source_id="dependent"
    discovery_dependencies=("fixture",)

    def __init__(self):
        super().__init__()
        self.discovery_calls=0

    def discover(self,ctx,scope):
        self.discovery_calls+=1
        if not ctx.store.query("documents",company_id=scope["company_id"]):
            return {"enumeration_complete":False,"discovered":0}
        return super().discover(ctx,scope)


def test_dependent_discovery_wakes_in_first_backfill_without_update(store):
    upstream,dependent=FixtureAdapter(),DependentFixture()
    cfg=config("fixture","dependent")
    result=run_cycle(store,SimpleNamespace(),cfg,{"fixture":upstream,"dependent":dependent})
    assert result["execution_complete"]
    assert dependent.discovery_calls==2 and dependent.fetch_count==1
    discovery=[t for t in Queue(store).tasks() if t["source_id"]=="dependent" and t["task_kind"]=="discover"]
    assert len(discovery)==1 and discovery[0]["attempt_count"]==2
    assert discovery[0]["result"]["dependency_evidence"]
    assert not any(g["code"]=="ENUMERATION_INCOMPLETE" for g in Queue(store).gaps())
    assert run_cycle(store,SimpleNamespace(),cfg,{"fixture":upstream,"dependent":dependent})["executed"]==0


def test_dependency_changes_coalesce_and_cap_retains_pending_refresh(store):
    upstream,dependent=FixtureAdapter(3),DependentFixture()
    cfg=config("fixture","dependent")
    result=run_cycle(store,SimpleNamespace(),cfg,{"fixture":upstream,"dependent":dependent},max_tasks=5)
    assert result["stop_reason"]=="max_tasks"
    assert upstream.parse_count==3 and dependent.discovery_calls==1
    roots=[t for t in Queue(store).tasks() if t["source_id"]=="dependent"]
    assert len(roots)==1 and roots[0]["state"]=="pending"
    resumed=run_pending(store,SimpleNamespace(),cfg,{"fixture":upstream,"dependent":dependent})
    assert resumed["execution_complete"] and dependent.discovery_calls==2
    assert dependent.fetch_count==1


def test_dependent_failure_does_not_loop_on_unchanged_evidence(store):
    class BrokenDependent(DependentFixture):
        def discover(self,ctx,scope):
            self.discovery_calls+=1
            raise ValueError("dependency parser unavailable")
    upstream,dependent=FixtureAdapter(),BrokenDependent()
    result=run_cycle(store,SimpleNamespace(),config("fixture","dependent"),{"fixture":upstream,"dependent":dependent})
    assert dependent.discovery_calls==2
    assert result["failed"]==2 and result["executed"]==4


def test_absent_dependency_does_not_block_configured_independent_discovery(store):
    class Direct(DependentFixture):
        def discover(self,ctx,scope):
            return FixtureAdapter.discover(self,ctx,scope)
    dependent=Direct()
    result=run_cycle(store,SimpleNamespace(),config("dependent"),{"dependent":dependent})
    assert result["execution_complete"] and dependent.fetch_count==1


def test_dependency_refresh_filters_company_and_preserves_generation(store):
    upstream,dependent=FixtureAdapter(),DependentFixture()
    cfg=config("fixture","dependent")
    cfg["securities"].append({"company_id":"sec:002","cik":"002"})
    run_cycle(store,SimpleNamespace(),cfg,{"fixture":upstream,"dependent":dependent})
    roots=[t for t in Queue(store).tasks() if t["source_id"]=="dependent" and t["task_kind"]=="discover"]
    assert len(roots)==2 and all(t["attempt_count"]==2 for t in roots)
    assert all("fixture" in t["result"]["dependency_evidence"] for t in roots)


def test_official_ir_integration_discovers_first_cycle_sec_profile(store):
    from equity_data.adapters.ir_source import OfficialIRAdapter
    class ProfileSource(FixtureAdapter):
        source_id="sec_edgar"
        def parse(self,ctx,resource,content,raw):
            record=ctx.store.put_record("company_profiles",resource["company_id"],{"investor_website":"https://investors.synthetic.example.test/"},
                source=self.source_id,raw_id=raw["raw_id"],company_id=resource["company_id"])
            return {"status":"parsed","record_ids":[record],"parsed_count":1}
    class Pages:
        def __init__(self):self.calls=[]
        def fetch(self,url,*,source,**kwargs):
            self.calls.append(url)
            content=b"<html><head><title>Investor Relations</title></head><body>Official investor relations.</body></html>"
            return content,store.put_raw(content,source=source,url=url,media_type="text/html")
    client=Pages()
    result=run_cycle(store,client,config("sec_edgar","official_ir"),{"sec_edgar":ProfileSource(),"official_ir":OfficialIRAdapter()})
    assert "https://investors.synthetic.example.test/" in client.calls
    assert not any(g["code"] in {"IR_ENTRYPOINT_UNRESOLVED","ENUMERATION_INCOMPLETE"} for g in Queue(store).gaps())
    roots=[t for t in Queue(store).tasks() if t["source_id"]=="official_ir" and t["task_kind"]=="discover"]
    assert roots[0]["attempt_count"]==2
    assert result["failed"]==0
