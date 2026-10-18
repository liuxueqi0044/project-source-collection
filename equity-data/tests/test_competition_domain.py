"""Controlled synthetic documents: identity, raw integrity, reviews and PIT closure."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from equity_data.store import Store
from equity_data.competition.domain import load_record, put_derived_record
from equity_data.competition.entities import put_entity, put_entity_link
from equity_data.competition.evidence import EvidenceError, validate_evidence
from equity_data.competition.relations import put_relation, review_relation
from equity_data.competition.queries import query_competition, records_snapshot


@contextmanager
def at(stamp):
    with patch("equity_data.store.now_utc", return_value=stamp), patch("equity_data.competition.domain.now_utc", return_value=stamp):
        yield


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path) as result:
        yield result


def raw_ref(store, content=b"<html><p id='claim'>Alpha and Beta compete in European coffee.</p><p id='other'>Nothing else.</p></html>", *, source="synthetic_official", url="https://fixture.invalid/report", time="2024-01-01T10:00:00Z", excerpt="Alpha and Beta compete in European coffee.", locator="css:#claim", **extra):
    raw = store.put_raw(content, source=source, url=url, media_type="text/html", retrieved_at=time)
    return {**{key: raw[key] for key in ("raw_id", "sha256", "url", "retrieval_id")}, "locator": locator, "excerpt": excerpt, **extra}


def entities(store):
    with at("2024-01-02T10:00:00Z"):
        a = put_entity(store, {"entity_id": "entity:alpha", "name": "Same Name", "entity_type": "company", "role": "target"})
        b = put_entity(store, {"entity_id": "entity:beta", "name": "Same Name", "entity_type": "private_company"})
    return a, b


def relation(ref, **extra):
    return {"edge_id": "edge:coffee", "target_entity_id": "entity:alpha", "peer_entity_id": "entity:beta", "product_or_business": "coffee", "geography": "Europe", "customer_segment": "retail", "overlap_basis": "The saved paragraph names this business overlap.", "evidence_status": "source_explicit", "review_status": "confirmed", "evidence_refs": [ref], **extra}


def test_exact_raw_retrieval_locator_and_excerpt_integrity(store):
    ref = raw_ref(store)
    validated = validate_evidence(store, [ref], require_external=True)[0]
    assert validated["hash_verified"] and validated["source"] == "synthetic_official"
    for change in ({"sha256": "0" * 64}, {"url": "https://other.invalid"}, {"retrieval_id": "unknown"}, {"excerpt": "undisclosed financial answer"}, {"locator": "css:#other"}):
        with pytest.raises(EvidenceError):
            validate_evidence(store, [{**ref, **change}])


def test_script_content_is_not_evidence_and_unicode_signs_survive(store):
    ref = raw_ref(store, b"<p id='claim'>Loss guidance is ($2.50), not +$2.50.</p><script>Profit guaranteed 100</script>", excerpt="Loss guidance is ($2.50), not +$2.50.")
    assert validate_evidence(store, [ref])[0]["excerpt"] == ref["excerpt"]
    with pytest.raises(EvidenceError):
        validate_evidence(store, [{**ref, "locator": "document:text", "excerpt": "Profit guaranteed 100"}])


def test_config_and_manual_review_are_not_external_fact_evidence(store):
    ref = raw_ref(store, source="competition_config")
    assert validate_evidence(store, [ref])[0]["evidence_nature"] == "internal_review_or_configuration"
    with pytest.raises(EvidenceError):
        validate_evidence(store, [ref], require_external=True)
    entities(store)
    with pytest.raises(EvidenceError):
        put_relation(store, relation(ref), reviewer="operator")


def test_hash_checked_again_not_cached_trust(store):
    ref = raw_ref(store)
    path = store.root / store.db.execute("select path from raw_blobs where raw_id=?", (ref["raw_id"],)).fetchone()[0]
    path.write_bytes(b"corrupt")
    with pytest.raises(EvidenceError, match="SHA-256"):
        validate_evidence(store, [ref])


def test_same_name_and_ticker_do_not_merge_private_entities(store):
    a, b = entities(store)
    assert a != b
    assert len(records_snapshot(store, "entities")) == 2
    with pytest.raises(ValueError, match="stable entity_id"):
        put_entity(store, {"name": "Same Name", "ticker": "SAME"})


def test_sec_mapping_and_external_id_collisions_fail_closed(store):
    record = put_entity(store, {"entity_id": "sec:0000123456", "name": "SEC company", "cik": "123456", "external_ids": {"lei": "synthetic-lei"}})
    assert load_record(store, record)["company_id"] == "sec:0000123456"
    with pytest.raises(ValueError, match="10-digit"):
        put_entity(store, {"entity_id": "sec:123456", "name": "bad"})
    with pytest.raises(ValueError, match="collision"):
        put_entity(store, {"entity_id": "entity:other", "name": "different", "external_ids": {"lei": "synthetic-lei"}})


def test_confirmed_scope_requires_external_evidence_and_overlap(store):
    entities(store)
    ref = raw_ref(store)
    for change in ({"product_or_business": "unknown"}, {"overlap_basis": "unknown"}, {"evidence_status": "unverified"}):
        with pytest.raises(ValueError):
            put_relation(store, relation(ref, **change), reviewer="operator")
    edge = put_relation(store, relation(ref, geography=None, customer_segment="unknown"), reviewer="operator")
    assert load_record(store, edge)["data"]["geography"] == "unknown"


def test_overlap_inference_needs_evidence_for_each_entity(store):
    entities(store)
    ref = raw_ref(store, entity_id="entity:alpha")
    with pytest.raises(ValueError, match="both entities"):
        put_relation(store, relation(ref, evidence_status="business_overlap_inference"), reviewer="operator")
    ref["evidence_entity_ids"] = ["entity:alpha", "entity:beta"]
    assert put_relation(store, relation(ref, evidence_status="business_overlap_inference"), reviewer="operator").startswith("rec:")


def test_idempotent_review_creates_no_business_or_raw_duplicates(store):
    entities(store)
    ref = raw_ref(store)
    with at("2024-01-03T10:00:00Z"):
        first = put_relation(store, relation(ref), reviewer="operator")
    counts = tuple(store.db.execute("select (select count(*) from records),(select count(*) from raw_retrievals)").fetchone())
    with at("2024-02-03T10:00:00Z"):
        assert put_relation(store, relation(ref), reviewer="operator") == first
    assert tuple(store.db.execute("select (select count(*) from records),(select count(*) from raw_retrievals)").fetchone()) == counts


def test_review_version_history_preserved_and_stale_review_rejected(store):
    entities(store)
    ref = raw_ref(store)
    with at("2024-01-03T10:00:00Z"):
        first = put_relation(store, relation(ref), reviewer="operator")
    with at("2024-01-05T10:00:00Z"):
        second = review_relation(store, "edge:coffee", "rejected", "Evidence scope is disputed", [ref], "reviewer-two")
    assert load_record(store, second)["supersedes"] == first
    assert len(store.query("competition_edges", latest=False)) == 2
    assert len(store.query("review_actions")) == 1
    with pytest.raises(ValueError, match="stale"):
        review_relation(store, first, "confirmed", "old review", [ref], "operator")
    assert records_snapshot(store, "competition_edges", as_of="2024-01-04", known_at="2024-01-04", processed_at="2024-01-04")[0]["record_id"] == first


def test_entity_parent_link_does_not_merge_brand_with_parent(store):
    entities(store)
    ref = raw_ref(store)
    link = put_entity_link(store, {"link_id": "ownership:1", "from_entity_id": "entity:beta", "to_entity_id": "entity:alpha", "link_type": "brand_of", "review_status": "confirmed", "evidence_refs": [ref]}, reviewer="operator")
    assert load_record(store, link)["dataset"] == "entity_links"
    assert len(records_snapshot(store, "entities")) == 2


def event_and_link(store, edge, ref):
    event = put_derived_record(store, "competitive_events", "event:1", {"event_id": "event:1", "actor_entity_id": "entity:beta", "event_type": "product_launch", "status": "announced", "fact_stage": "extracted_fact", "business_scope": "coffee", "geography": "Europe", "customer_segment": "retail", "evidence_refs": [ref]})
    link = put_derived_record(store, "competitive_event_links", "link:1", {"link_id": "link:1", "target_entity_id": "entity:alpha", "event_id": "event:1", "event_version_id": event, "edge_version_id": edge, "review_status": "matched", "mismatch_or_unknown": [], "evidence_refs": [ref]})
    return event, link


def test_historical_bundle_does_not_use_current_relation_for_old_event(store):
    entities(store)
    ref = raw_ref(store)
    with at("2024-01-03T10:00:00Z"):
        edge = put_relation(store, relation(ref), reviewer="operator")
    with at("2024-01-04T10:00:00Z"):
        event_and_link(store, edge, ref)
    with at("2024-01-05T10:00:00Z"):
        review_relation(store, "edge:coffee", "rejected", "scope correction", [ref], "operator")
    past = query_competition(store, "entity:alpha", as_of="2024-01-04", known_at="2024-01-04", processed_at="2024-01-04")
    assert len(past["core_brief"]) == 1
    current = query_competition(store, "entity:alpha")
    assert current["core_brief"] == []
    assert any("recompute" in gap["reason"] for gap in current["gaps"])


def test_later_confirmation_cannot_be_backdated_by_valid_from(store):
    entities(store)
    ref = raw_ref(store)
    with at("2024-05-03T10:00:00Z"):
        put_relation(store, relation(ref, valid_from="2020-01-01"), reviewer="operator")
    assert records_snapshot(store, "competition_edges", as_of="2024-01-31") == []


def test_raw_reference_claimed_publication_cannot_fake_historical_copy(store):
    ref = raw_ref(store, time="2024-05-01T10:00:00Z", publication_time="2020-01-01T00:00:00Z")
    with at("2024-05-02T10:00:00Z"):
        put_derived_record(store, "competitive_events", "event:1", {"event_id": "event:1", "evidence_refs": [ref]}, available_at="2020-01-01T00:00:00Z", availability_basis="claimed_source_date")
    assert records_snapshot(store, "competitive_events", as_of="2024-01-31") == []


def test_corrupt_dependency_removes_core_without_resurrecting_previous_edge(store):
    entities(store)
    ref = raw_ref(store)
    with at("2024-01-03T10:00:00Z"):
        edge = put_relation(store, relation(ref), reviewer="operator")
        event_and_link(store, edge, ref)
    path = store.root / store.db.execute("select path from raw_blobs where raw_id=?", (ref["raw_id"],)).fetchone()[0]
    path.write_bytes(b"broken")
    bundle = query_competition(store, "entity:alpha")
    assert not bundle["core_brief"] and not bundle["relations"]
    assert any("validation_failed" in gap["reason"] for gap in bundle["gaps"])


def test_missing_precise_event_version_is_gap_not_core(store):
    entities(store)
    ref = raw_ref(store)
    edge = put_relation(store, relation(ref), reviewer="operator")
    put_derived_record(store, "competitive_event_links", "bad:link", {"target_entity_id": "entity:alpha", "event_id": "event:1", "edge_version_id": edge, "review_status": "matched", "evidence_refs": [ref]})
    bundle = query_competition(store, "entity:alpha")
    assert not bundle["core_brief"] and bundle["gaps"]


def test_verified_website_requires_external_evidence(store):
    with pytest.raises(EvidenceError):
        put_entity(store, {"entity_id": "private:one", "name": "Private One", "websites": [{"url": "https://fixture.invalid", "verification_status": "verified"}]})


def test_candidate_event_cannot_become_core_via_scoped_link(store):
    entities(store)
    ref = raw_ref(store)
    edge = put_relation(store, relation(ref), reviewer="agent_evidence_review")
    assert load_record(store, edge)["quality"] == "agent_reviewed"
    event = put_derived_record(store, "competitive_events", "candidate", {"event_id": "candidate", "fact_stage": "candidate", "evidence_refs": [ref]})
    put_derived_record(store, "competitive_event_links", "candidate-link", {"target_entity_id": "entity:alpha", "event_version_id": event, "edge_version_id": edge, "review_status": "matched", "evidence_refs": [ref]})
    assert query_competition(store, "entity:alpha")["core_brief"] == []


def test_discovery_unknown_relation_is_candidate_only(store):
    entities(store)
    ref = raw_ref(store)
    value = relation(ref, relation_type="unknown", review_status="candidate", evidence_status="unverified")
    key = put_relation(store, value, reviewer="agent_candidate_discovery")
    assert load_record(store, key)["data"]["relation_type"] == "unknown"
    with pytest.raises(ValueError, match="unknown relation type"):
        put_relation(store, {**value, "review_status": "confirmed", "evidence_status": "source_explicit"}, reviewer="operator")


def test_trial_replay_discovers_primary_and_preserves_acquisition_identity(tmp_path):
    import json
    from scripts.build_competition_trial import build_trial
    source_root, target_root = tmp_path / "saved", tmp_path / "trial"
    with Store(source_root) as source:
        raw = source.put_raw(b"<p>Saved company coffee business</p>", source="sec_edgar", url="https://www.sec.gov/Archives/edgar/data/1234/000000123425000001/report.htm", media_type="text/html", retrieved_at="2025-03-01T10:00:00Z", metadata={"original": True})
        listing = source.put_raw(b"{}", source="sec_edgar", url="https://data.sec.gov/submissions/CIK0000001234.json", retrieved_at="2025-03-01T09:00:00Z")
        source.put_record("filings", "filing:1", {"accession": "0000001234-25-000001", "form": "10-K", "report_date": "2024-12-31", "filing_date": "2025-02-28", "primary_document": "report.htm"}, source="sec_edgar", raw_id=listing["raw_id"], company_id="sec:0000001234")
        before = source.db.execute("select count(*) from records").fetchone()[0]
    manifest = {"local_sources": [{"key": "annual", "company_id": "sec:0000001234", "forms": ["10-K"]}], "entities": [{"entity_id": "sec:0000001234", "name": "Generic Company", "evidence_refs": [{"local_source_key": "annual", "locator": "document:text", "excerpt": "coffee business"}]}]}
    bundle, audit = build_trial(manifest, source_root, target_root)
    ref = bundle["entities"][0]["evidence_refs"][0]
    assert ref["retrieval_id"] == raw["retrieval_id"]
    assert ref["retrieved_at"] == "2025-03-01T10:00:00.000000Z"
    assert audit["imports"][0]["network_requests"] == 0
    repeated, _ = build_trial(manifest, source_root, target_root)
    assert repeated == bundle
    with Store(target_root) as target:
        assert target.db.execute("select count(*) from raw_retrievals").fetchone()[0] == 1
    import sqlite3
    with sqlite3.connect((source_root / "data.sqlite3").as_uri() + "?mode=ro", uri=True) as source:
        assert source.execute("select count(*) from records").fetchone()[0] == before


def test_link_matched_flag_cannot_override_unknown_or_mismatched_scope(store):
    entities(store)
    ref = raw_ref(store)
    edge = put_relation(store, relation(ref, geography="unknown"), reviewer="operator")
    event_and_link(store, edge, ref)
    assert query_competition(store, "entity:alpha")["core_brief"] == []
