"""Append-only competition records built on the existing evidence store."""
from __future__ import annotations

import hashlib
import json

from ..store import _json, _timestamp, now_utc
from .evidence import EvidenceError, validate_evidence

SCHEMA_VERSION = "competition-v5.0"
PARSER_VERSION = "competition-domain-v5.0.0"
DATASETS = {"entities", "entity_links", "competition_edges", "competitive_events", "competitive_event_links", "analysis_hypotheses", "watch_endpoints", "competition_snapshots", "review_actions"}


def load_record(store, record_id: str) -> dict | None:
    row = store.db.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["data"] = json.loads(record.pop("data_json"))
    return record


def latest_record(store, dataset: str, record_key: str) -> dict | None:
    row = store.db.execute("SELECT record_id FROM records WHERE dataset=? AND source='competition' AND record_key=? ORDER BY sequence DESC LIMIT 1", (dataset, record_key)).fetchone()
    return load_record(store, row[0]) if row else None


def put_derived_record(store, dataset: str, record_key: str, data: dict, *, reviewer: str | None = None,
                       quality: str = "unverified", knowledge_type: str = "system_inference",
                       available_at: str | None = None, availability_basis: str = "derived_at_processing",
                       company_id: str | None = None) -> str:
    """Stable same-content writes; processing time is never taken from user config.

    Generated JSON is itself an audit artifact, not proof of an external claim.
    External facts remain reachable through independently checked evidence_refs.
    """
    if dataset not in DATASETS or not isinstance(record_key, str) or not record_key:
        raise ValueError("Unknown competition dataset or empty stable record key")
    value = dict(data)
    value["schema_version"] = SCHEMA_VERSION
    value["evidence_refs"] = validate_evidence(store, value.get("evidence_refs", []))
    if reviewer is not None:
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("A nonempty reviewer identity is required")
        value["reviewer"] = reviewer.strip()
    prior = latest_record(store, dataset, record_key)
    if (prior and prior["data"] == value and prior["quality"] == quality and prior["knowledge_type"] == knowledge_type
            and prior["availability_basis"] == availability_basis
            and (available_at is None or prior["available_at"] == _timestamp(available_at))):
        return prior["record_id"]
    stamp = now_utc()
    public_time = _timestamp(available_at) if available_at else stamp
    generated = {"dataset": dataset, "record_key": record_key, "payload": value,
                 "generated_at": stamp, "evidence_nature": "review" if reviewer else "system_inference"}
    content = _json(generated).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    source = "competition_review" if reviewer else "competition_config"
    store.put_raw(content, source=source, url=f"derived://competition/{dataset}/{digest}", media_type="application/json", retrieved_at=stamp, metadata={"evidence_nature": generated["evidence_nature"]})
    raw = store.put_raw(content, source="competition", url=f"derived://competition/{dataset}/{digest}", media_type="application/json", retrieved_at=stamp, metadata={"evidence_nature": generated["evidence_nature"], "origin_source": source})
    return store.put_record(dataset, record_key, value, source="competition", raw_id=raw["raw_id"],
                            company_id=company_id, available_at=public_time, availability_basis=availability_basis,
                            published_at=None, publication_precision="unknown", source_locator="generated audit JSON; external evidence_refs",
                            quality=quality, knowledge_type=knowledge_type, parser_version=PARSER_VERSION)
