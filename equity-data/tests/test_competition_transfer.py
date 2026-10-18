"""Configuration transfer preserves raw evidence, not source completion claims."""
from pathlib import Path
from unittest.mock import patch

import pytest

from equity_data.store import Store
from equity_data.local_control import collector_lock
from equity_data.competition.cli import import_bundle
from equity_data.competition.domain import latest_record
from equity_data.competition.entities import put_entity
from equity_data.competition.evidence import validate_evidence
from equity_data.competition.transfer import transfer_configuration


def settings(root):
    return {"storage": str(root), "http": {}, "competition": {"budget": {"max_requests": 0}}}


def prepare(root, *, record_ref=False):
    with Store(root) as store:
        raw = store.put_raw(b"<h1>Alpha Official Newsroom</h1><p>Alpha and Beta compete in European coffee. Beta is a brand of Alpha.</p>",
                            source="synthetic_official", url="https://alpha.example/about", media_type="text/html", retrieved_at="2022-01-01T09:00:00Z")
        ref = {**{key: raw[key] for key in ("raw_id", "sha256", "retrieval_id", "url")}, "locator": "document:text",
               "excerpt": "Alpha and Beta compete in European coffee.", "evidence_entity_ids": ["entity:alpha", "entity:beta"]}
        if record_ref:
            ref["record_id"] = store.put_record("financial_facts", "synthetic:financial", {"amount": 5}, source="synthetic_official", raw_id=raw["raw_id"])
        identity_ref = {**ref, "excerpt": "Alpha Official Newsroom", "entity_id": "entity:alpha"}
        bundle = {"reviewer": "agent:evidence-review", "entities": [
            {"entity_id": "entity:alpha", "name": "Alpha", "role": "target", "identity_status": "verified", "evidence_refs": [ref]},
            {"entity_id": "entity:beta", "name": "Beta", "entity_type": "brand", "evidence_refs": [ref]}],
            "entity_links": [{"link_id": "link:brand", "from_entity_id": "entity:beta", "to_entity_id": "entity:alpha", "link_type": "brand_of", "review_status": "confirmed", "evidence_refs": [ref]}],
            "relations": [{"edge_id": "edge:coffee", "target_entity_id": "entity:alpha", "peer_entity_id": "entity:beta", "product_or_business": "coffee", "geography": "Europe", "customer_segment": "unknown", "overlap_basis": "Explicitly named business overlap in saved synthetic source.", "review_status": "confirmed", "evidence_status": "source_explicit", "evidence_refs": [ref]}],
            "endpoints": [{"endpoint_id": "ep:alpha", "entity_id": "entity:alpha", "url": "https://alpha.example/news", "allowed_domains": ["alpha.example"], "official_status": "verified_official", "evidence_refs": [identity_ref],
                "authority_review": {"entity_id": "entity:alpha", "method": "agent_evidence_review", "reviewer": "agent:source-inspection", "domains": ["alpha.example"], "basis": "Read saved official identity wording on the configured public domain; no legal ownership audit claimed.", "identity_phrase": "Alpha", "evidence_refs": [identity_ref]}}]}
        with patch("equity_data.store.now_utc", return_value="2023-01-01T00:00:00.000000Z"), patch("equity_data.competition.domain.now_utc", return_value="2023-01-01T00:00:00.000000Z"):
            outcome = import_bundle(store, bundle, settings(root))
        assert not outcome["failures"]
        store.checkpoint("competition:pretend-success", {"complete": True})
        return raw, tuple(store.db.iterdump())


def test_preserves_original_bytes_retrieval_times_and_reprocesses_config(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    raw, before = prepare(source)
    with patch("equity_data.http.HttpClient.fetch", side_effect=AssertionError("Transfer must never fetch")):
        result = transfer_configuration(source, settings(target))
    assert result["status"] == "configuration_imported"
    assert result["inventory"] == {"entities": 2, "entity_links": 1, "competition_edges": 1, "watch_endpoints": 1}
    assert result["new_business_records"] == 5 and result["network_requests"] == 0
    assert result["added_original_blobs"] == result["added_original_retrievals"] == 1
    with Store(source) as origin, Store(target) as store:
        # Store construction can update neither these pre-existing source rows
        # nor their contents; SELECT dump proves the transfer did not write them.
        assert tuple(origin.db.iterdump()) == before
        row = store.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (raw["retrieval_id"],)).fetchone()
        old = origin.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (raw["retrieval_id"],)).fetchone()
        assert dict(row) == dict(old)
        blob = store.db.execute("SELECT * FROM raw_blobs WHERE raw_id=?", (raw["raw_id"],)).fetchone()
        assert blob["created_at"] == "2022-01-01T09:00:00.000000Z"
        assert (store.root / blob["path"]).read_bytes() == Path(raw["path"]).read_bytes()
        assert not store.db.execute("SELECT 1 FROM checkpoints").fetchall()
        edge = latest_record(store, "competition_edges", "edge:coffee")
        assert edge["data"]["customer_segment"] == "unknown"
        assert edge["data"]["entity_version_ids"] == [latest_record(store, "entities", key)["record_id"] for key in ("entity:alpha", "entity:beta")]
        assert latest_record(store, "watch_endpoints", "ep:alpha")["data"]["official_status"] == "verified_official"
    assert all(row["target_created_at"] > row["source_created_at"] for row in result["records"])
    assert result["latest_upstream_confirmed"] is False


def test_repeat_has_zero_new_business_records_or_original_retrievals(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    prepare(source)
    first = transfer_configuration(source, settings(target))
    with Store(target) as store:
        counts = [store.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("records", "raw_blobs", "raw_retrievals")]
    second = transfer_configuration(source, settings(target))
    assert second["new_business_records"] == second["added_original_blobs"] == second["added_original_retrievals"] == 0
    assert [row["target_record_id"] for row in first["records"]] == [row["target_record_id"] for row in second["records"]]
    assert not any(row["new_version"] for row in second["records"])
    with Store(target) as store:
        assert counts == [store.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("records", "raw_blobs", "raw_retrievals")]


def test_financial_and_progress_not_copied_or_modified_and_refs_remain_traceable(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    prepare(source, record_ref=True)
    with Store(target) as store:
        raw = store.put_raw(b"target financial", source="existing", url="https://existing.example")
        identity = store.put_record("financial_facts", "keep", {"amount": 9}, source="existing", raw_id=raw["raw_id"])
        original = dict(store.db.execute("SELECT * FROM records WHERE record_id=?", (identity,)).fetchone())
        store.checkpoint("existing:success", {"date": "2020-01-01"})
        checkpoint = tuple(store.db.execute("SELECT * FROM checkpoints").fetchone())
    result = transfer_configuration(source, settings(target))
    assert result["detached_source_records"][0]["dataset"] == "financial_facts"
    with Store(target) as store:
        assert [dict(row) for row in store.db.execute("SELECT * FROM records WHERE dataset='financial_facts'")] == [original]
        assert [tuple(row) for row in store.db.execute("SELECT * FROM checkpoints")] == [checkpoint]
        ref = latest_record(store, "entities", "entity:alpha")["data"]["evidence_refs"][0]
        assert "record_id" not in ref and ref["source_record_provenance"]["dataset"] == "financial_facts"
        assert validate_evidence(store, [ref])[0]["hash_verified"]


def test_dry_run_validates_nested_evidence_without_creating_target(tmp_path):
    source, target = tmp_path / "source", tmp_path / "not-created"
    prepare(source)
    result = transfer_configuration(source, settings(target), dry_run=True)
    assert result["status"] == "validated_inventory_only" and result["evidence"]["verified_blobs"] == 1
    assert not target.exists()


def test_corrupt_source_fails_before_target_is_created(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    raw, _ = prepare(source)
    Path(raw["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256"):
        transfer_configuration(source, settings(target))
    assert not target.exists()


def test_target_collector_lock_blocks_transfer(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    prepare(source)
    with collector_lock(target) as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match="Collector active"):
            transfer_configuration(source, settings(target))
    assert not (target / "data.sqlite3").exists()


def test_same_store_rejected(tmp_path):
    with pytest.raises(ValueError, match="must be different"):
        transfer_configuration(tmp_path, settings(tmp_path))


def test_nested_only_evidence_is_copied_and_current_version_only(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    prepare(source)
    with Store(source) as store:
        raw = store.put_raw(b"Additional unverified identity detail", source="synthetic_extra", url="https://alpha.example/detail", retrieved_at="2021-05-01T00:00:00Z")
        ref = {**{key: raw[key] for key in ("raw_id", "sha256", "retrieval_id", "url")}, "locator": "document:text", "excerpt": "Additional unverified identity detail"}
        value = latest_record(store, "entities", "entity:alpha")["data"]
        value.update(name="Alpha New Name", extension={"still_unverified": {"evidence_refs": [ref]}})
        put_entity(store, value, reviewer="agent:evidence-review")
    result = transfer_configuration(source, settings(target))
    assert result["evidence"]["verified_blobs"] == 2
    with Store(target) as store:
        assert store.db.execute("SELECT COUNT(*) FROM records WHERE dataset='entities' AND record_key='entity:alpha'").fetchone()[0] == 1
        current = latest_record(store, "entities", "entity:alpha")["data"]
        assert current["name"] == "Alpha New Name"
        nested = current["extension"]["still_unverified"]["evidence_refs"][0]
        assert validate_evidence(store, [nested])[0]["retrieved_at"] == "2021-05-01T00:00:00.000000Z"


def test_existing_corrupt_target_evidence_rejected_without_business_writes(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    raw, _ = prepare(source)
    transfer_configuration(source, settings(target))
    with Store(target) as store:
        blob = store.db.execute("SELECT path FROM raw_blobs WHERE raw_id=?", (raw["raw_id"],)).fetchone()
        (store.root / blob[0]).write_bytes(b"target-corruption")
        before = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    with pytest.raises(ValueError, match="Target raw cache"):
        transfer_configuration(source, settings(target))
    with Store(target) as store:
        assert store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == before
