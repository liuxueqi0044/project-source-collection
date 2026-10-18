"""Behavioral negatives for evidence, changes and bounded competition scope."""
import copy

import pytest

from equity_data.competition.change_detector import compare_snapshots, normalize_snapshot, numeric_field
from equity_data.competition.discovery import discover_candidates
from equity_data.competition.events import extract_events
from equity_data.competition.linker import link_event


def snapshot(text, endpoint=None, label="a"):
    endpoint = endpoint or {"format": "html"}
    result = normalize_snapshot(text.encode(), endpoint)
    result.update(raw_id="raw:" + label, sha256=result["raw_content_hash"], retrieval_id="retrieval:" + label,
                  url="https://example.test/news", observed_at="2026-10-10T10:00:00+00:00",
                  endpoint_id="endpoint:test", entity_id="entity:peer")
    return result


def structured_endpoint():
    return {"format": "html", "entity_id": "entity:peer", "entity_verified": True,
            "event_rules": [{"rule_id": "contracts-v1", "event_type": "commercial_agreement",
                "pattern": r"(?P<action>signed) contract (?P<contract_id>[A-Z]-\d+) for (?P<object>[^,]+), business (?P<business_scope>[^,]+), region (?P<geography>[^,]+), customers (?P<customer_segment>[^,.]+)"}]}


def edge():
    return {"record_id": "edge:version:1", "data": {
        "edge_id": "edge:1", "target_entity_id": "entity:target", "peer_entity_id": "entity:peer",
        "product_or_business": "batteries", "geography": "Europe", "customer_segment": "utilities",
        "review_status": "confirmed", "valid_from": "2025-01-01", "valid_to": None,
        "evidence_refs": [{"raw_id": "raw:relation", "excerpt": "competition"}]}}


def test_mixed_html_retains_text_and_signs_while_script_and_navigation_changes_are_noise():
    a = snapshot("<main><p>Loss (3).</p><div>EPS −2.50</div><table><tr><td>Sales</td><td>+17%</td></tr></table></main><script>x=1</script><nav>A</nav>")
    b = snapshot("<main><p>Loss (3).</p><div>EPS −2.50</div><table><tr><td>Sales</td><td>+17%</td></tr></table></main><script>x=2</script><nav>B</nav>", label="b")
    assert all(text in a["body_text"] for text in ("Loss (3).", "EPS −2.50", "Sales +17%"))
    assert compare_snapshots(a, b, {})["status"] == "noise_only"
    assert extract_events(b, {})["events"] == []


@pytest.mark.parametrize("text,value", [("(USD 3.25)", "-3.25"), ("USD -3.25", "-3.25"), ("−3.25", "-3.25"), ("+3.25 million", "3250000.00")])
def test_signed_numbers_are_not_silently_positive(text, value):
    assert numeric_field(text)["value"] == value


@pytest.mark.parametrize("text", ["$3", "(3", "-(-3)", "3 to 5", "USD 3 EUR", "1,23"])
def test_ambiguous_numeric_text_is_rejected(text):
    assert numeric_field(text)["value"] is None


def test_baseline_price_then_same_context_change_retains_both_raw_versions():
    endpoint = {"format": "html", "entity_id": "entity:peer", "entity_verified": True,
        "endpoint_id": "price", "selectors": {"fields": {"price": {"selector": ".price", "kind": "price", "currency": "USD", "unit": "per device"}}},
        "observation_context": {"product": "Device Q", "business_scope": "devices", "geography": "US", "customer_segment": "retail",
            "specification": "128 GB", "quantity": "1", "tax_inclusion": "exclusive", "payment_period": "one-time", "price_type": "list", "login_status": "anonymous"}}
    a = snapshot('<main><span class="price">USD 10</span></main>', endpoint)
    b = snapshot('<main><span class="price">USD 12</span></main>', endpoint, "b")
    b["observed_at"] = "2026-10-10T11:00:00+00:00"
    assert compare_snapshots(None, a, endpoint)["status"] == "baseline"
    assert extract_events(a, endpoint)["events"] == []
    events = extract_events(b, endpoint, a)["events"]
    assert len(events) == 1
    result = events[0]
    assert (result["old_value"], result["new_value"], result["currency"]) == ("10", "12", "USD")
    assert result["event_time"] is None and result["event_precision"] == "observation_interval"
    assert [ref["raw_id"] for ref in result["evidence_refs"]] == ["raw:a", "raw:b"]
    assert result["observation_interval"]["after"] == a["observed_at"]
    changed = copy.deepcopy(b)
    changed["observation_context"]["tax_inclusion"] = "inclusive"
    assert not extract_events(changed, endpoint, a)["events"]
    assert "changed_price_context:tax_inclusion" in extract_events(changed, endpoint, a)["candidates"][0]["candidate_reasons"]


def test_failed_or_missing_fields_cannot_be_product_exit_or_price_change():
    endpoint = {"format": "html", "selectors": {"body": "main", "fields": {"price": {"selector": ".price", "kind": "price", "currency": "USD"}}}}
    a = snapshot('<main><span class="price">10</span></main>', endpoint)
    for bad in (snapshot("Forbidden", {**endpoint, "response_status": 403}), snapshot("<main></main>", endpoint)):
        assert compare_snapshots(a, bad, endpoint)["status"] == "rejected"
        assert extract_events(bad, endpoint, a)["events"] == []
    missing = snapshot("<main>temporarily unavailable</main>", endpoint)
    assert extract_events(missing, endpoint, a)["events"] == []


def test_parser_or_selector_change_requires_reparse_not_business_event():
    a = snapshot("<main>Product text</main>", {"selectors": {"body": "main"}})
    b = snapshot("<main>Product text</main>", {"selectors": {"body": "main"}, "selector_version": "2"})
    assert compare_snapshots(a, b, {})["status"] == "rejected"


def test_missing_scope_is_candidate_not_fact_and_plans_do_not_become_completion():
    s = snapshot("<main>The company plans to open a new factory in 2028.</main>")
    result = extract_events(s, {"entity_id": "entity:peer", "entity_verified": True})
    assert result["events"] == []
    assert result["candidates"][0]["status"] == "planned"
    assert result["candidates"][0]["geography"] == "unknown"
    denied = snapshot("<main>The company has not launched Product R.</main>")
    assert extract_events(denied, {})["events"] == []
    assert extract_events(denied, {})["candidates"] == []


def test_explicit_identifiers_support_reprints_but_keep_two_same_day_contracts():
    endpoint = structured_endpoint()
    raw = "<main>We signed contract P-123 for cells, business batteries, region Europe, customers utilities. We signed contract P-124 for cells, business batteries, region Europe, customers utilities.</main>"
    first = snapshot(raw, endpoint)
    first["publication_time"] = "2026-10-10"
    first["publication_precision"] = "day"
    events = extract_events(first, endpoint)["events"]
    assert len(events) == 2 and events[0]["event_id"] != events[1]["event_id"]
    reprint = snapshot(raw, endpoint, "reprint")
    reprint["url"] = "https://reprint.example.test/press"
    reprint["endpoint_id"] = "reprint"
    assert [e["event_id"] for e in extract_events(reprint, endpoint)["events"]] == [e["event_id"] for e in events]
    events[0]["record_id"] = "event:version:1"
    link = link_event(events[0], [edge()])[0]
    assert link["core_eligible"] and link["review_status"] == "matched" and link["mismatch_or_unknown"] == []
    assert link["edge_version_id"] == "edge:version:1" and link["event_version_id"] == "event:version:1"


def test_same_title_without_explicit_identifier_is_not_cross_source_merged():
    text = "<main>We signed a contract to deliver parts.</main>"
    a, b = snapshot(text), snapshot(text, label="b")
    b["url"] = "https://other.example.test/story"
    ea, eb = extract_events(a, {})["candidates"][0], extract_events(b, {})["candidates"][0]
    assert ea["event_id"] != eb["event_id"]
    assert not ea["event_identity"]["cross_source_merge_allowed"]


def test_numeric_correction_is_reparsed_with_same_explicit_identity_and_new_evidence():
    endpoint = {"entity_id": "entity:peer", "entity_verified": True,
        "event_rules": [{"event_type": "financial_update", "pattern": r"(?P<action>revises) (?P<event_identifier>OUTLOOK-\d+) (?P<object>EPS), business (?P<business_scope>software), region (?P<geography>US), customers (?P<customer_segment>enterprise), (?P<period>FY2027), (?P<unit>per share), (?P<currency>USD) (?P<amount>[−+-]?\d+\.\d+)"}]}
    template = "<main>Company revises OUTLOOK-7 EPS, business software, region US, customers enterprise, FY2027, per share, USD %s.</main>"
    a, b = snapshot(template % "−3.25", endpoint), snapshot(template % "−4.25", endpoint, "b")
    ea, eb = extract_events(a, endpoint)["events"][0], extract_events(b, endpoint)["events"][0]
    assert ea["event_id"] == eb["event_id"]
    assert (ea["new_value"], eb["new_value"]) == ("-3.25", "-4.25")
    assert ea["evidence_refs"][0]["raw_id"] != eb["evidence_refs"][0]["raw_id"]
    malformed = snapshot(template % "unknown", endpoint, "c")
    assert not extract_events(malformed, endpoint)["events"]


@pytest.mark.parametrize("dimension,new_value,reason", [("geography", "Asia", "mismatch:geography"), ("business_scope", "aircraft", "mismatch:business"), ("customer_segment", "unknown", "unknown:customer_segment")])
def test_partial_competition_does_not_expand_to_entire_company(dimension, new_value, reason):
    endpoint = structured_endpoint()
    s = snapshot("<main>We signed contract P-123 for cells, business batteries, region Europe, customers utilities.</main>", endpoint)
    evt = extract_events(s, endpoint)["events"][0]
    evt.update(publication_time="2026-10-10", **{dimension: new_value})
    result = link_event(evt, [edge()])[0]
    assert not result["core_eligible"] and reason in result["mismatch_or_unknown"]


def test_relation_validity_and_missing_publication_are_not_waived_by_recent_retrieval():
    evt = {"actor_entity_id": "entity:peer", "event_id": "e", "business_scope": "batteries", "geography": "Europe", "customer_segment": "utilities", "fact_stage": "extracted_fact", "evidence_refs": [{}], "first_observed_at": "2026-10-10"}
    assert "unknown:event_time_and_publication_time" in link_event(evt, [edge()])[0]["mismatch_or_unknown"]
    evt["publication_time"] = "2024-01-01"
    assert "mismatch:relation_validity" in link_event(evt, [edge()])[0]["mismatch_or_unknown"]


def test_feed_entries_keep_their_own_publication_dates_and_article_extent():
    endpoint = {"format": "rss", "entity_id": "entity:peer", "entity_verified": True}
    s = snapshot('<rss><channel><item><title>Company launches Product One</title><pubDate>Fri, 09 Oct 2026 10:00:00 GMT</pubDate><link>https://example.test/one</link></item><item><title>Company launches Product Two</title><pubDate>Sat, 10 Oct 2026 10:00:00 GMT</pubDate><link>https://example.test/two</link></item></channel></rss>', endpoint)
    events = extract_events(s, endpoint)["candidates"]
    assert len(events) == 2
    assert events[0]["publication_time"] != events[1]["publication_time"]
    assert all(e["context"]["evidence_extent"] == "feed_entry_only" for e in events)
    assert events[0]["evidence_refs"][0]["url"] == s["url"]


def test_same_industry_and_cooccurrence_only_produce_bounded_candidates():
    doc = snapshot("<main>Company A and Company B attended a trade show. Company C is a competitor.</main>")
    entities = [{"entity_id": "A", "legal_name": "Company A"}, {"entity_id": "B", "legal_name": "Company B"}, {"entity_id": "C", "legal_name": "Company C"}]
    candidates = discover_candidates([doc], entities, target_id="A")["candidates"]
    assert len(candidates) == 2 and all(c["review_status"] == "candidate" for c in candidates)
    assert {c["overlap_basis"] for c in candidates} == {"cooccurrence_only", "explicit_competition_language"}
    limited = discover_candidates([doc], entities, target_id="A", budget=1)
    assert limited["truncated"] and len(limited["candidates"]) == 1
