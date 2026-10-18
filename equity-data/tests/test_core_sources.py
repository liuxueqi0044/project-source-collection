"""Synthetic channel/queue integration: never calls a provider or holdout issuer."""
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from equity_data.adapters.base import Context, persist_observations
from equity_data.adapters.core_sources import AlpacaAdapter, CalendarAdapter, DependencyAdapter, FredAdapter
from equity_data.budgets import Budget, BudgetExceeded
from equity_data.engine import run_cycle, run_pending, status_report
from equity_data.http import HttpClient, redact_url
from equity_data.queue import Queue
from equity_data.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path) as value:
        yield value


@pytest.fixture(autouse=True)
def no_live_credentials(monkeypatch):
    for name in ("FRED_API_KEY","APCA_API_KEY_ID","APCA_API_SECRET_KEY","TEST_FRED_KEY","TEST_ALPACA_KEY","TEST_ALPACA_SECRET"):
        monkeypatch.delenv(name,raising=False)


def config(source,data_types,**extras):
    return {"market":"US","start":"2024-01-01","end":"2024-12-31","sources":[source],"data_types":data_types,
            "scheduler":{"max_task_attempts_per_run":1,"retry_seconds":0},**extras}


class FixtureClient:
    def __init__(self,store,route):
        self.store,self.route,self.calls=store,route,[]

    def fetch(self,url,*,source,params=None,headers=None,**kwargs):
        params=params or {}
        self.calls.append({"url":url,"params":params,"headers":headers})
        value=self.route(url,params)
        content=value if isinstance(value,bytes) else json.dumps(value).encode()
        safe=redact_url(url+("?"+urlencode(params) if params else ""))
        raw=self.store.put_raw(content,source=source,url=safe,retrieved_at=f"2026-01-{len(self.calls):02d}T12:00:00Z")
        return content,raw


def test_calendar_market_days_and_timezones_are_independent(store):
    adapter=CalendarAdapter()
    us=config(adapter.source_id,["calendar_sessions"],start="2024-07-03",end="2024-07-05",calendar={"exchanges":["XNYS"]})
    cn={**us,"market":"CN","calendar":{"exchanges":["XSHG"]}}
    assert run_cycle(store,SimpleNamespace(),us,{adapter.source_id:adapter})["failed"]==0
    assert run_cycle(store,SimpleNamespace(),cn,{adapter.source_id:adapter})["failed"]==0
    rows={r["record_key"]:r for r in store.query("calendar_sessions")}
    assert rows["XNYS|2024-07-04"]["data"]["is_open"] is False
    assert rows["XSHG|2024-07-04"]["data"]["is_open"] is True
    assert rows["XNYS|2024-07-03"]["data"]["timezone"]=="America/New_York"
    assert rows["XSHG|2024-07-03"]["data"]["timezone"]=="Asia/Shanghai"
    assert not store.query("calendar_sessions",as_of="2024-07-05")


def test_calendar_cross_market_exchange_is_blocked(store):
    cfg=config("exchange_calendars",["calendar_sessions"],calendar={"exchanges":["XSHG"]})
    result=run_cycle(store,SimpleNamespace(),cfg,{"exchange_calendars":CalendarAdapter()})
    assert result["blocked"]==1
    assert not store.query("calendar_sessions")
    assert any(g["code"]=="CAPABILITY_UNAVAILABLE" for g in status_report(store)["gaps"])


def test_fred_csv_snapshot_is_idempotent_revisioned_and_not_historical_pit(store):
    payload=[b"observation_date,SYNTHETIC\n2024-01-01,100.00\n2024-02-01,.\n"]
    client=FixtureClient(store,lambda url,params:payload[0])
    cfg=config("fred",["macro_observations"],fred={"allow_public_snapshot":True,"series":[{"id":"SYNTHETIC","unit":"index"}]})
    adapter=FredAdapter()
    run_cycle(store,client,cfg,{"fred":adapter})
    run_cycle(store,client,cfg,{"fred":adapter})
    assert len(client.calls)==1
    run_cycle(store,client,cfg,{"fred":adapter},mode="update")
    assert len(store.query("macro_observations",latest=False))==2
    payload[0]=payload[0].replace(b"100.00",b"101.00")
    run_cycle(store,client,cfg,{"fred":adapter},mode="update")
    assert len(store.query("macro_observations",latest=False))==3
    rows=store.query("macro_observations")
    assert rows[0]["data"]["value"]=="101.00"
    assert rows[1]["data"]["value"] is None and rows[1]["quality"]=="missing"
    assert not store.query("macro_observations",as_of="2024-12-31")
    assert any(g["code"]=="HISTORICAL_VINTAGES_UNAVAILABLE" for g in status_report(store)["gaps"])


def test_changed_source_series_config_generates_new_discovery(store):
    client=FixtureClient(store,lambda url,p:f"observation_date,{p['id']}\n2024-01-01,1\n".encode())
    cfg=config("fred",["macro_observations"],fred={"allow_public_snapshot":True,"series":["SERIES_A"]})
    run_cycle(store,client,cfg,{"fred":FredAdapter()})
    cfg["fred"]["series"].append("SERIES_B")
    run_cycle(store,client,cfg,{"fred":FredAdapter()})
    assert {r["data"]["series_id"] for r in store.query("macro_observations")}=={"SERIES_A","SERIES_B"}


def test_fred_api_metadata_pagination_vintages_and_no_persisted_key(store,monkeypatch):
    secret="fixture-fred-secret-do-not-save"
    monkeypatch.setenv("TEST_FRED_KEY",secret)
    def route(url,params):
        assert params["api_key"]==secret
        if url.endswith("/series"):
            return {"seriess":[{"id":"SYNTHETIC","units":"Millions of Dollars","frequency":"Monthly","seasonal_adjustment":"Not Seasonally Adjusted"}]}
        offset=params["offset"]
        return {"count":2,"observations":[{"date":"2024-01-01","value":"100" if offset==0 else "101", "realtime_start":"2024-02-02" if offset==0 else "2024-03-02","realtime_end":"9999-12-31"}]}
    client=FixtureClient(store,route)
    cfg=config("fred",["macro_observations"],fred={"api_key_env":"TEST_FRED_KEY","series":["SYNTHETIC"],"vintage_end":"2024-12-31"})
    result=run_cycle(store,client,cfg,{"fred":FredAdapter()},max_tasks=3)
    assert result["stop_reason"]=="max_tasks"
    assert len(client.calls)==2
    assert status_report(store)["summary"]["resource_states"]["discovered_not_downloaded"]==1
    run_pending(store,client,cfg,{"fred":FredAdapter()})
    assert [c["params"].get("offset") for c in client.calls]==[None,0,1]
    assert store.query("macro_observations",as_of="2024-02-05")[0]["data"]["value"]=="100"
    assert store.query("macro_observations")[0]["data"]["value"]=="101"
    assert len(store.query("macro_observations",latest=False))==2
    assert secret not in "\n".join(store.db.iterdump())
    assert all("api_key" not in r["metadata"].get("params",{}) for r in Queue(store).resources())


def test_fred_missing_api_dependency_does_not_substitute_csv(store):
    result=run_cycle(store,SimpleNamespace(),config("fred",["macro_observations"],fred={"series":["SYNTHETIC"]}),{"fred":FredAdapter()})
    assert result["blocked"]==1
    assert not Queue(store).resources()


def alpaca_config():
    return config("alpaca",["daily_bars"],securities=[{"company_id":"fixture-company","security_id":"fixture-share-class","symbol":"SYNTHETIC","currency":"USD"}],
                  alpaca={"feed":"iex","key_env":"TEST_ALPACA_KEY","secret_env":"TEST_ALPACA_SECRET"})


def test_alpaca_missing_keys_never_marked_connected(store):
    result=run_cycle(store,SimpleNamespace(),alpaca_config(),{"alpaca":AlpacaAdapter()})
    assert result["blocked"]==1
    assert any(g["status"]=="missing_authorization" for g in status_report(store)["gaps"])
    assert not store.query("daily_bars")


def test_alpaca_saved_single_page_normalizer_and_next_page_queue(store,monkeypatch):
    monkeypatch.setenv("TEST_ALPACA_KEY","fixture-key")
    monkeypatch.setenv("TEST_ALPACA_SECRET","fixture-secret")
    def route(url,params):
        assert url.endswith("/bars")
        assert params["adjustment"]=="raw" and params["asof"]=="-"
        second=bool(params.get("page_token"))
        return {"bars":{"SYNTHETIC":[{"t":"2024-01-03T05:00:00Z" if second else "2024-01-02T05:00:00Z","o":10,"h":12,"l":9,"c":11,"v":100,"vw":10.5,"n":4}]},"next_page_token":None if second else "next"}
    client=FixtureClient(store,route)
    cfg=alpaca_config()
    result=run_cycle(store,client,cfg,{"alpaca":AlpacaAdapter()},max_tasks=2)
    assert result["stop_reason"]=="max_tasks"
    assert len(client.calls)==1 and len(store.query("daily_bars"))==1
    assert status_report(store)["summary"]["resource_states"]=={"parsed":1,"discovered_not_downloaded":1}
    run_pending(store,client,cfg,{"alpaca":AlpacaAdapter()})
    assert len(client.calls)==2 and len(store.query("daily_bars"))==2
    row=store.query("daily_bars")[0]
    assert row["data"]["turnover"] is None and row["data"]["coverage"]=="IEX only"
    assert row["data"]["adjustment"]=="raw"
    assert not store.query("daily_bars",as_of="2024-12-31")
    assert "fixture-secret" not in "\n".join(store.db.iterdump())


def test_alpaca_action_page_does_not_fetch_hidden_bar_page(store,monkeypatch):
    monkeypatch.setenv("TEST_ALPACA_KEY","fixture-key")
    monkeypatch.setenv("TEST_ALPACA_SECRET","fixture-secret")
    client=FixtureClient(store,lambda url,params:{"corporate_actions":{"forward_splits":[{"id":"split1","new_rate":2,"old_rate":1,"effective_date":"2024-03-01"}]},"next_page_token":None})
    cfg=alpaca_config();cfg["data_types"]=["corporate_actions"]
    result=run_cycle(store,client,cfg,{"alpaca":AlpacaAdapter()})
    assert result["failed"]==0
    assert len(client.calls)==1 and client.calls[0]["url"].endswith("/corporate-actions")
    assert store.query("corporate_actions")[0]["data"]["split_ratio"]=="2"
    assert not store.query("daily_bars")


def test_dependency_source_boundary_keeps_datasets_missing(store):
    adapter=DependencyAdapter("forecasts",["analyst_estimates"],"Historical consensus requires a licensed vendor")
    result=run_cycle(store,SimpleNamespace(),config("forecasts",["analyst_estimates"]),{"forecasts":adapter})
    assert result["blocked"]==1 and not store.query("analyst_estimates")
    assert any(g["code"]=="PROVIDER_DEPENDENCY" and g["data_type"]=="analyst_estimates" for g in status_report(store)["gaps"])


class Response:
    status_code=200
    ok=True
    headers={"Content-Type":"text/csv"}
    def __init__(self,url):self.url=url
    def iter_content(self,chunk_size):yield b"observation_date,SYNTHETIC\n2024-01-01,100\n"
    def close(self):pass


@pytest.mark.parametrize("limits",[{"max_requests":0},{"max_cost_usd":0,"request_cost_usd":{"fred":1}}])
def test_request_and_cost_budget_defers_before_network(store,monkeypatch,limits):
    client=HttpClient(store)
    client.budget=Budget(limits)
    calls=[]
    monkeypatch.setattr(client.session,"get",lambda url,**kwargs:calls.append(url) or Response(url))
    cfg=config("fred",["macro_observations"],fred={"allow_public_snapshot":True,"series":["SYNTHETIC"]})
    result=run_cycle(store,client,cfg,{"fred":FredAdapter()})
    assert result["deferred"]==1 and not calls
    assert status_report(store)["resources"][0]["state"]=="discovered_not_downloaded"
    client.budget=Budget({"max_requests":1})
    assert run_pending(store,client,cfg,{"fred":FredAdapter()})["succeeded"]==1
    assert len(calls)==1


def test_raw_byte_budget_does_not_accept_partial_original(store,monkeypatch):
    client=HttpClient(store)
    client.budget=Budget({"max_bytes":1})
    monkeypatch.setattr(client.session,"get",lambda url,**kwargs:Response(url))
    cfg=config("fred",["macro_observations"],fred={"allow_public_snapshot":True,"series":["SYNTHETIC"]})
    result=run_cycle(store,client,cfg,{"fred":FredAdapter()})
    assert result["deferred"]==1
    assert store.db.execute("SELECT COUNT(*) FROM raw_blobs").fetchone()[0]==0
    assert not store.query("macro_observations")


def observation():
    return {"dataset":"guidance","record_key":"revenue","data":{"value":"5"},"source_locator":"paragraph 1","evidence_excerpt":"Revenue guidance is $5 million for fiscal 2025.","unit":"USD million","period_end":"2025-12-31"}


def test_disappearing_automatic_field_gets_parser_status_tombstone(store):
    queue=Queue(store)
    ctx=Context(store,SimpleNamespace(),{},queue,"test")
    resource={"source_id":"ir","resource_key":"release","url":"https://example.invalid/release","company_id":"fixture-company","metadata":{}}
    original=store.put_raw(b"Revenue guidance is $5 million for fiscal 2025.",source="ir",url=resource["url"],retrieved_at="2026-01-01T00:00:00Z")
    persist_observations(ctx,resource,{"parser_version":"fixture-v1","observations":[observation()],"issues":[]},original)
    updated=store.put_raw(b"Revenue guidance is temporarily unavailable.",source="ir",url=resource["url"],retrieved_at="2026-01-02T00:00:00Z")
    result=persist_observations(ctx,resource,{"parser_version":"fixture-v1","observations":[],"issues":[]},updated)
    current=store.query("guidance")[0]
    assert current["data"]["status"]=="not_emitted_by_current_parser" and current["data"]["value"] is None
    assert current["knowledge_type"]=="system_inference" and not current["data"]["is_observation"]
    assert store.query("guidance",as_of="2026-01-01")[0]["data"]["value"]=="5"
    assert result["status"]=="partial"
    assert result["parsed_count"]==0
    persist_observations(ctx,resource,{"parser_version":"fixture-v1","observations":[],"issues":[]},updated)
    assert len(store.query("guidance",latest=False))==2


def test_missing_field_evidence_never_counted_automatic_parse_success(store):
    queue=Queue(store)
    ctx=Context(store,SimpleNamespace(),{},queue,"test")
    resource={"source_id":"ir","resource_key":"release","url":"https://example.invalid/release","metadata":{}}
    raw=store.put_raw(b"content",source="ir",url=resource["url"])
    malformed=observation();malformed.pop("evidence_excerpt")
    result=persist_observations(ctx,resource,{"parser_version":"fixture-v1","observations":[malformed],"issues":[]},raw)
    assert result["status"]=="partial" and result["parsed_count"]==0
    assert not store.query("guidance")
    assert any(g["code"]=="PARSER_EVIDENCE_MISSING" for g in queue.gaps())
