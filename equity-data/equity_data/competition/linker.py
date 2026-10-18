"""Conservative event/competition-scope association, separate from impact."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from .change_detector import clean

LINKER_VERSION = "competition-link-v5.0.0"


def _values(value):
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return {clean(v).casefold() for v in values if v is not None and clean(v).casefold() not in ("", "unknown", "unspecified", "not_disclosed", "n/a")}


def scope_match(left, right):
    """No invented geography taxonomy, all-company wildcard or synonym map."""
    left, right = _values(left), _values(right)
    if not left or not right:
        return "unknown"
    return "matched" if left & right else "mismatch"


def _moment(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None and len(str(value)) == 10 else dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def link_event(event: dict, edges: list[dict]) -> list[dict]:
    links = []
    evt = event.get("data") or event
    moment = _moment(evt.get("event_time") or evt.get("publication_time"))
    for record in edges:
        edge = record.get("data") or (json.loads(record["data_json"]) if record.get("data_json") else record)
        if edge.get("peer_entity_id") != evt.get("actor_entity_id"):
            continue
        outcomes = {key: scope_match(evt.get(event_key), edge.get(edge_key)) for key, event_key, edge_key in (
            ("business", "business_scope", "product_or_business"), ("geography", "geography", "geography"),
            ("customer_segment", "customer_segment", "customer_segment"))}
        mismatch = [key for key, value in outcomes.items() if value == "mismatch"]
        unknown = [key for key, value in outcomes.items() if value == "unknown"]
        start, end = _moment(edge.get("valid_from")), _moment(edge.get("valid_to"))
        if end is not None and len(str(edge.get("valid_to"))) == 10:
            end += timedelta(days=1, microseconds=-1)
        if moment is None:
            unknown.append("event_time_and_publication_time")
        elif start is None:
            unknown.append("relation_valid_from")
        elif moment < start or (end is not None and moment > end):
            mismatch.append("relation_validity")
        if edge.get("valid_to") and end is None:
            unknown.append("unparseable_relation_valid_to")
        if edge.get("review_status") != "confirmed":
            unknown.append("relation_not_confirmed")
        if not edge.get("evidence_refs"):
            unknown.append("relation_evidence_missing")
        if evt.get("fact_stage") != "extracted_fact":
            unknown.append("event_not_extracted_fact")
        if not evt.get("evidence_refs"):
            unknown.append("event_evidence_missing")
        if mismatch or edge.get("review_status") == "rejected":
            review_status, core = "excluded", False
        elif unknown:
            review_status, core = "needs_review", False
        else:
            review_status, core = "matched", True
        links.append({"event_id": evt.get("event_id"),
            "event_version_id": event.get("record_id") or evt.get("event_version_id"),
            "target_entity_id": edge.get("target_entity_id"), "peer_entity_id": edge.get("peer_entity_id"),
            "edge_id": edge.get("edge_id"), "edge_version_id": record.get("record_id") or edge.get("edge_version_id"),
            "matched_scope": {key: evt.get(key) for key in ("business_scope", "geography", "customer_segment")},
            "scope_checks": outcomes, "mismatch_or_unknown": ["mismatch:" + key for key in mismatch] + ["unknown:" + key for key in unknown],
            "relevance_reason": "Source claim overlaps the explicitly recorded competition scope; financial impact is not inferred." if core else "Scope mismatch or unresolved evidence prevents core association.",
            "review_status": review_status, "core_eligible": core,
            "evidence_refs": evt.get("evidence_refs", []) + edge.get("evidence_refs", []),
            "linker_version": LINKER_VERSION, "processed_at": None})
    return links
