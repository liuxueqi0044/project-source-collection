"""V5 bounded competition transport/planning regression tests (synthetic only)."""
import hashlib
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from equity_data.adapters.base import Context
from equity_data.adapters.competition_source import CompetitionWebAdapter, GuardedSession, _PinnedAdapter, endpoint_resource, validate_url
from equity_data.budgets import Budget
from equity_data.competition.watch_planner import plan_watches
from equity_data.http import HttpClient
from equity_data.queue import Queue
from equity_data.store import Store


def endpoint(**values):
    return {"endpoint_id": "endpoint:private", "entity_id": "entity:private", "url": "https://example.test/news",
            "channel": "web", "allowed_domains": ["example.test"], "official_status": "verified_official",
            "evidence_refs": [{"raw_id": "evidence"}], "auth_context": "public", **values}


def dns(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]


class Response:
    def __init__(self, url, body=b"<html><main>Example source statement.</main></html>", status=200, **headers):
        self.url, self.content, self.status_code = url, body, status
        self.headers = {"Content-Type": "text/html", **headers}
        self.ok = status < 400
        self.closed = False
    def iter_content(self, chunk_size=65536):
        yield self.content
    def close(self):
        self.closed = True
    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError("HTTP " + str(self.status_code), response=self)


class Transport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses[url]
    def close(self):
        pass


@pytest.mark.parametrize("url,domains", [
    ("http://127.0.0.1/", ["127.0.0.1"]), ("http://[::1]/", ["::1"]),
    ("http://169.254.169.254/latest", ["169.254.169.254"]), ("http://localhost/", ["localhost"]),
    ("file:///tmp/secret", ["example.test"]), ("https://example.test.evil.test/", ["example.test"]),
    ("https://name:secret@example.test/", ["example.test"]), ("https://example.test:8080/", ["example.test"]),
])
def test_forbidden_urls(url, domains):
    with pytest.raises(ValueError):
        validate_url(url, domains)


def test_dns_mixed_public_private_fails_closed():
    answers = dns() + [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.1.1.1", 443))]
    with patch("socket.getaddrinfo", return_value=answers), pytest.raises(ValueError, match="PRIVATE_ADDRESS"):
        validate_url("https://example.test/", ["example.test"], resolve=True)


def test_validated_ip_pinned_tls_uses_original_hostname():
    adapter = _PinnedAdapter({("example.test", 443): "8.8.8.8"})
    captured = {}
    adapter.poolmanager.connection_from_host = lambda **kwargs: captured.update(kwargs) or "pool"
    request = requests.Request("GET", "https://example.test/data").prepare()
    assert adapter.get_connection_with_tls_context(request, True) == "pool"
    assert captured["host"] == "8.8.8.8"
    assert captured["pool_kwargs"]["assert_hostname"] == "example.test"
    assert captured["pool_kwargs"]["server_hostname"] == "example.test"


def test_redirect_charged_and_raw_preserved(tmp_path):
    with Store(tmp_path) as store, patch("socket.getaddrinfo", side_effect=dns):
        client = HttpClient(store, max_attempts=1)
        client.interval = 0
        client.budget = Budget({"max_requests": 5, "max_bytes": 10000})
        transport = Transport({"https://example.test/news": Response("https://example.test/news", b"redirect", 302, Location="/release"),
                               "https://example.test/release": Response("https://example.test/release")})
        client.session = GuardedSession(client, endpoint(), transport=transport)
        content, raw = client.fetch("https://example.test/news", source="competition_web")
        assert len(transport.calls) == 2
        assert client.budget.requests == 2
        assert store.db.execute("SELECT count(*) FROM raw_retrievals").fetchone()[0] == 2
        assert store.latest_raw("competition_web", "https://example.test/news")["metadata"]["final_url"] == "https://example.test/release"
        assert all(call[1]["allow_redirects"] is False for call in transport.calls)
        assert all(call[1]["headers"]["Host"] == "example.test" for call in transport.calls)


def test_redirect_outside_allowlist_never_requested(tmp_path):
    with Store(tmp_path) as store, patch("socket.getaddrinfo", side_effect=dns):
        client = HttpClient(store, max_attempts=1)
        transport = Transport({"https://example.test/news": Response("https://example.test/news", b"", 302, Location="https://evil.test/a")})
        client.session = GuardedSession(client, endpoint(), transport=transport)
        with pytest.raises(RuntimeError):
            client.fetch("https://example.test/news", source="competition_web")
        assert len(transport.calls) == 1


def test_permission_failure_no_redirect_or_retry_bypass(tmp_path):
    with Store(tmp_path) as store, patch("socket.getaddrinfo", side_effect=dns):
        client = HttpClient(store, max_attempts=3)
        transport = Transport({"https://example.test/news": Response("https://example.test/news", b"Forbidden", 403)})
        client.session = GuardedSession(client, endpoint(), transport=transport)
        with pytest.raises(RuntimeError, match="403"):
            client.fetch("https://example.test/news", source="competition_web")
        assert len(transport.calls) == 1
        assert store.db.execute("SELECT count(*) FROM raw_retrievals").fetchone()[0] == 0


def context(store, endpoints):
    return Context(store, None, {"_generation": "test", "_competition_plan": {"plan_id": "test-plan", "endpoints": endpoints}}, Queue(store), "test-run")


def test_snapshot_unchanged_refresh_is_idempotent_and_no_financial_tombstone(tmp_path):
    with Store(tmp_path) as store:
        adapter, ep = CompetitionWebAdapter(), endpoint()
        ctx = context(store, [ep])
        adapter.discover(ctx, {})
        resource = endpoint_resource(ep, "test-plan")
        body = b"<html><main><p>Company announces a new product for 2026.</p></main></html>"
        raw = store.put_raw(body, source="competition_web", url=ep["url"], retrieved_at="2026-01-01T00:00:00Z")
        finance_raw = store.put_raw(body, source="sec_edgar", url="https://www.sec.gov/example")
        old = store.put_record("financial_facts", "legacy", {"value": 123, "resource_key": resource["resource_key"], "extraction_mode": "automatic"}, source="sec_edgar", raw_id=finance_raw["raw_id"])
        first = adapter.parse(ctx, resource, body, raw)
        raw2 = store.put_raw(body, source="competition_web", url=ep["url"], retrieved_at="2026-01-02T00:00:00Z")
        second = adapter.parse(ctx, resource, body, raw2)
        assert first["record_ids"] == second["record_ids"]
        assert len(store.query("competition_snapshots", latest=False)) == 1
        assert store.query("financial_facts")[0]["record_id"] == old
        assert store.db.execute("SELECT count(*) FROM record_validations").fetchone()[0] == 0
        assert second["events_complete"] is False
        assert store.query("competition_snapshots")[0]["available_at"].startswith("2026-01-01")


def test_rss_bounded_links_report_omitted_original_not_claim_full(tmp_path):
    with Store(tmp_path) as store:
        adapter, ep = CompetitionWebAdapter(), endpoint(channel="rss", max_documents=1)
        ctx = context(store, [ep]); adapter.discover(ctx, {})
        body = b'<rss><channel><title>Issuer news</title><item><title>Product A release</title><link>https://example.test/a</link><description>Company announces A.</description></item><item><title>Product B release</title><link>https://example.test/b</link><description>Company announces B.</description></item></channel></rss>'
        raw = store.put_raw(body, source="competition_web", url=ep["url"])
        result = adapter.parse(ctx, endpoint_resource(ep, "test-plan"), body, raw)
        assert result["children_discovered"] == 1
        assert result["status"] == "partial"
        errors = [g for g in ctx.queue.gaps() if g["code"] == "COMPETITION_ENUMERATION_LIMIT"]
        assert len(errors) == 1 and "https://example.test/b" in str(errors)
        assert len(ctx.queue.resources()) == 2


def row(key, data):
    return {"record_id": "version:" + key, "data": data}


def planner_rows(peers=2):
    entities = [row("target-a", {"entity_id": "target-a", "role": "target"}), row("target-b", {"entity_id": "target-b", "role": "target"})]
    entities += [row("peer-" + str(i), {"entity_id": "peer-" + str(i), "entity_type": "private_company"}) for i in range(peers)]
    edges = [row("edge-" + str(i), {"edge_id": "edge-" + str(i), "target_entity_id": "target-a", "peer_entity_id": "peer-" + str(i),
             "relation_type": "direct_competition", "review_status": "confirmed", "product_or_business": "widgets", "geography": "US", "customer_segment": "retail"}) for i in range(peers)]
    endpoints = [row("ep-" + str(i), endpoint(endpoint_id="ep-" + str(i), entity_id="peer-" + str(i))) for i in range(peers)]
    return {"entities": entities, "competition_edges": edges, "watch_endpoints": endpoints}


def test_planner_deduplicates_peer_for_targets_one_hop(tmp_path):
    with Store(tmp_path) as store:
        rows = planner_rows()
        rows["competition_edges"].append(row("edge-b", {**rows["competition_edges"][0]["data"], "edge_id": "edge-b", "target_entity_id": "target-b"}))
        with patch("equity_data.competition.watch_planner._records", side_effect=lambda s, d: rows[d]):
            plan = plan_watches(store, {"competition": {"target_ids": ["target-a", "target-b"]}}, now="2026-10-10T01:00:00Z")
        assert len(plan["endpoints"]) == 2
        assert plan["endpoints"][0]["target_ids"] == ["target-a", "target-b"]
        assert len(plan["endpoints"][0]["edge_version_ids"]) == 2
        assert plan["unique_peer_count"] == 2


def test_planner_caps_unknown_scope_candidate_expired_and_future(tmp_path):
    with Store(tmp_path) as store:
        rows = planner_rows(20)
        rows["competition_edges"][0]["data"]["product_or_business"] = "unknown"
        rows["competition_edges"][1]["data"]["review_status"] = "candidate"
        rows["competition_edges"][2]["data"]["valid_to"] = "2026-10-09"
        rows["competition_edges"][3]["data"]["valid_from"] = "2027-01-01"
        with patch("equity_data.competition.watch_planner._records", side_effect=lambda s, d: rows[d]):
            plan = plan_watches(store, {"competition": {"target_ids": ["target-a"]}}, now="2026-10-10T09:00:00Z")
        assert len(plan["endpoints"]) == 15
        assert sum(ep["cadence"] == "core3daily" for ep in plan["endpoints"]) == 5
        assert {e["reason"] for e in plan["excluded"]} >= {"unknown_business_scope_requires_review", "relationship_not_confirmed", "relationship_expired", "relationship_not_yet_valid", "per_target_watch_cap"}


def test_planner_future_due_does_not_unconditionally_fetch(tmp_path):
    with Store(tmp_path) as store:
        rows = planner_rows(1)
        store.db.execute("CREATE TABLE competition_endpoint_state(endpoint_id TEXT PRIMARY KEY, config_hash TEXT, next_due_at TEXT,last_success_at TEXT)")
        with patch("equity_data.competition.watch_planner._records", side_effect=lambda s, d: rows[d]):
            first = plan_watches(store, {"competition": {}}, now="2026-10-10T09:00:00Z")
            ep = first["endpoints"][0]
            store.db.execute("INSERT INTO competition_endpoint_state VALUES(?,?,?,?)", (ep["endpoint_id"], ep["config_hash"], "2026-10-10T12:30:00Z", "2026-10-10T09:00:00Z"))
            plan = plan_watches(store, {"competition": {}}, now="2026-10-10T09:05:00Z")
            forced = plan_watches(store, {"competition": {}}, now="2026-10-10T09:05:00Z", force=True)
        assert plan["endpoints"] == [] and len(plan["all_endpoints"]) == 1
        assert len(forced["endpoints"]) == 1

def test_unknown_region_or_customers_remain_watch_not_core(tmp_path):
    with Store(tmp_path) as store:
        rows = planner_rows(1)
        rows["competition_edges"][0]["data"]["geography"] = "unknown"
        rows["competition_edges"][0]["data"]["customer_segment"] = "unknown"
        with patch("equity_data.competition.watch_planner._records", side_effect=lambda s, d: rows[d]):
            plan = plan_watches(store, {"competition": {"target_ids": ["target-a"]}})
        assert len(plan["endpoints"]) == 1 and plan["endpoints"][0]["cadence"] == "watch1daily"
        assert any(g["code"] == "UNKNOWN_SCOPE_REQUIRES_REVIEW" for g in plan["gaps"])


def test_redirect_budget_stops_before_second_network_request(tmp_path):
    from equity_data.budgets import BudgetExceeded
    with Store(tmp_path) as store, patch("socket.getaddrinfo", side_effect=dns):
        client = HttpClient(store, max_attempts=1); client.interval = 0
        client.budget = Budget({"max_requests": 1})
        transport = Transport({"https://example.test/news": Response("https://example.test/news", b"", 302, Location="/release")})
        client.session = GuardedSession(client, endpoint(), transport=transport)
        with pytest.raises(BudgetExceeded):
            client.fetch("https://example.test/news", source="competition_web")
        assert len(transport.calls) == 1 and client.budget.requests == 1


class FixtureClient:
    def __init__(self, store, fixtures):
        self.store, self.fixtures, self.calls = store, fixtures, []
    def fetch(self, url, **kwargs):
        self.calls.append(url)
        value = self.fixtures[url]
        if isinstance(value, Exception):
            raise value
        return value, self.store.put_raw(value, source=kwargs['source'], url=url)


def test_entry_success_child_failure_keeps_scope_incomplete_then_resumes(tmp_path):
    from equity_data.engine import run_cycle
    from equity_data.competition.jobs import ensure_schema
    from equity_data.competition.runner import endpoint_acquisition_receipts
    from equity_data.store import now_utc
    ep = endpoint(channel='rss', max_documents=5, config_hash='same')
    plan = {'plan_id':'fixture-plan','endpoints':[ep]}
    config = {'market':'US','start':'2026-01-01','end':'2026-10-10','sources':['competition_web'],
              'data_types':['competition_snapshots'],'securities':[], '_competition_plan':plan,
              'scheduler':{'retry_seconds':0,'authorization_retry_seconds':0,'max_task_attempts_per_run':1}}
    fixtures = {ep['url']: b'<rss><channel><title>News</title><item><title>A</title><description>Company announces product A.</description><link>https://example.test/a</link></item></channel></rss>',
                'https://example.test/a': RuntimeError('HTTP 403 permission denied')}
    with Store(tmp_path) as store, patch('socket.getaddrinfo', side_effect=dns):
        ensure_schema(store)
        client = FixtureClient(store, fixtures)
        stamp = now_utc()
        result = run_cycle(store, client, config, {'competition_web':CompetitionWebAdapter()}, mode='update')
        receipt = endpoint_acquisition_receipts(store, config, plan, [ep], Queue(store).resources(), {}, stamp, result)[0]
        assert receipt['entry_checked_this_round'] and receipt['entry_normalization_complete']
        assert receipt['enumerated_resources'] == 2 and not receipt['scope_check_complete']
        assert store.db.execute('SELECT last_success_at FROM competition_endpoint_state').fetchone()[0] is None
        fixtures['https://example.test/a'] = b'<html><main><p>Company announces product A.</p></main></html>'
        stamp = now_utc()
        result2 = run_cycle(store, client, config, {'competition_web':CompetitionWebAdapter()}, mode='update')
        receipt2 = endpoint_acquisition_receipts(store, config, plan, [ep], Queue(store).resources(), {}, stamp, result2)[0]
        assert receipt2['scope_check_complete'] and receipt2['resources_checked_this_round'] == 2
        before = store.db.execute('SELECT count(*) FROM records').fetchone()[0]
        result3 = run_cycle(store, client, config, {'competition_web':CompetitionWebAdapter()}, mode='update')
        assert len(client.calls) == 6
        assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == before


def test_retrieving_identical_bytes_elsewhere_cannot_refresh_failed_endpoint(tmp_path):
    from equity_data.competition.jobs import ensure_schema
    from equity_data.competition.runner import endpoint_acquisition_receipts
    from equity_data.store import now_utc
    with Store(tmp_path) as store:
        ensure_schema(store)
        ep = endpoint(config_hash='same'); ctx = context(store, [ep]); adapter = CompetitionWebAdapter()
        adapter.discover(ctx,{})
        resource = endpoint_resource(ep,'test-plan')
        body=b'<html><main><p>Source statement.</p></main></html>'
        raw=store.put_raw(body,source='competition_web',url=ep['url'],retrieved_at='2025-01-01T00:00:00Z')
        adapter.parse(ctx,resource,body,raw)
        stamp=now_utc()
        store.put_raw(body,source='competition_web',url='https://example.test/other')
        receipt=endpoint_acquisition_receipts(store,{}, {'plan_id':'test-plan'},[ep],Queue(store).resources(),{},stamp,{})[0]
        assert not receipt['entry_checked_this_round'] and not receipt['scope_check_complete']

def test_doh_public_cache_avoids_fake_system_dns_and_private_answers(tmp_path):
    from equity_data.adapters.competition_source import public_resolution
    class DnsTransport(Transport):
        def mount(self, *args): pass
    with Store(tmp_path) as store:
        client=HttpClient(store,max_attempts=1);client.interval=0;client.budget=Budget({'max_requests':5})
        url='https://cloudflare-dns.com/dns-query'
        transport=DnsTransport({url:Response(url,json.dumps({'Status':0,'Answer':[{'type':1,'TTL':30,'data':'8.8.8.8'}]}).encode())})
        ep=endpoint(transport={'dns_mode':'cloudflare_doh'})
        with patch('requests.Session',return_value=transport),patch('socket.getaddrinfo',side_effect=AssertionError('system DNS must not be used')):
            assert public_resolution(client,ep,ep['url'])[1]==['8.8.8.8']
            assert public_resolution(client,ep,ep['url'])[1]==['8.8.8.8']
        assert len(transport.calls)==1 and client.budget.requests==1
        assert store.db.execute("SELECT count(*) FROM raw_retrievals WHERE source='competition_dns'").fetchone()[0]==1
        client._competition_dns_cache={}
        bad=DnsTransport({url:Response(url,json.dumps({'Status':0,'Answer':[{'type':1,'TTL':30,'data':'10.0.0.1'}]}).encode())})
        with patch('requests.Session',return_value=bad),pytest.raises(ValueError,match='PRIVATE_ADDRESS'):
            public_resolution(client,ep,ep['url'])
        assert len(bad.calls)==1


def test_offline_raw_replay_does_not_resolve_dns(tmp_path):
    from equity_data.cli import RunHttpClient
    with Store(tmp_path) as store:
        ep=endpoint();body=b'<html><main>Preserved text</main></html>'
        raw=store.put_raw(body,source='competition_web',url=ep['url'])
        client=RunHttpClient(store,offline=True)
        with patch('socket.getaddrinfo',side_effect=AssertionError('offline DNS prohibited')):
            content,replayed=CompetitionWebAdapter().acquire(SimpleNamespace(client=client),endpoint_resource(ep,'offline'))
        assert content==body and replayed['retrieval_id']==raw['retrieval_id']
