"""Evidence-backed, scope-limited competition edges and append-only reviews."""
from __future__ import annotations

import hashlib
from datetime import date

from ..store import _json
from .domain import latest_record, load_record, put_derived_record
from .evidence import validate_evidence

RELATION_TYPES = {"direct_competition", "substitute", "potential_entrant", "direct", "substitute_product", "unknown"}
REVIEW_STATES = {"candidate", "confirmed", "rejected", "needs_review"}
EVIDENCE_STATES = {"source_explicit", "business_overlap_inference", "unverified"}


def unknown_scope(value) -> bool:
    if isinstance(value, list):
        return not value or any(unknown_scope(x) for x in value)
    return value is None or str(value).strip().lower() in {"", "unknown", "unspecified", "not_disclosed", "未知"}


def put_relation(store, data: dict, *, reviewer: str) -> str:
    value = dict(data)
    for field in ("edge_id", "target_entity_id", "peer_entity_id"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError("Relation requires " + field)
    if value["target_entity_id"] == value["peer_entity_id"]:
        raise ValueError("Self-competition requires separate identified business entities")
    versions = []
    for identity in (value["target_entity_id"], value["peer_entity_id"]):
        record = latest_record(store, "entities", identity)
        if record is None:
            raise ValueError("Relation references an unregistered entity: " + identity)
        versions.append(record["record_id"])
    value["entity_version_ids"] = versions
    value.setdefault("relation_type", "direct_competition")
    value.setdefault("review_status", "candidate")
    value.setdefault("evidence_status", "unverified")
    if value["relation_type"] not in RELATION_TYPES or value["review_status"] not in REVIEW_STATES or value["evidence_status"] not in EVIDENCE_STATES:
        raise ValueError("Unknown relation/evidence/review state")
    for field in ("product_or_business", "geography", "customer_segment"):
        if unknown_scope(value.get(field)):
            value[field] = "unknown"
    for field in ("valid_from", "valid_to", "review_due_at"):
        value.setdefault(field, None)
        if value[field] is not None:
            date.fromisoformat(str(value[field])[:10])
    if value["valid_from"] and value["valid_to"] and value["valid_from"] > value["valid_to"]:
        raise ValueError("Relation valid_to precedes valid_from")
    value.setdefault("overlap_basis", "unknown")
    value.setdefault("evidence_refs", [])
    if value["review_status"] == "confirmed":
        if value["relation_type"] == "unknown":
            raise ValueError("An unknown relation type cannot be confirmed")
        if unknown_scope(value["product_or_business"]) or unknown_scope(value["overlap_basis"]):
            raise ValueError("Confirmation requires an explicit business scope and overlap basis")
        if value["evidence_status"] == "unverified":
            raise ValueError("Same industry/co-mention is not a confirmed competition relation")
        value["evidence_refs"] = validate_evidence(store, value["evidence_refs"], require_external=True)
        if value["evidence_status"] == "business_overlap_inference":
            covered = set()
            for ref in value["evidence_refs"]:
                covered.update(ref.get("evidence_entity_ids", []))
                if ref.get("entity_id"):
                    covered.add(ref["entity_id"])
            if not {value["target_entity_id"], value["peer_entity_id"]} <= covered:
                raise ValueError("Overlap inference requires evidence explicitly assigned to both entities")
    agent_review = value.get("review_method") == "agent_evidence_review" or str(reviewer).lower().startswith("agent")
    value["review_kind"] = "agent_evidence_review" if agent_review else "operator_declared_review"
    return put_derived_record(store, "competition_edges", value["edge_id"], value, reviewer=reviewer,
                              company_id=value["target_entity_id"], knowledge_type="manual_observation" if value["review_status"] == "confirmed" and not agent_review else "system_inference",
                              quality=("agent_reviewed" if agent_review else "manual_reviewed") if value["review_status"] == "confirmed" else "needs_review")


def review_relation(store, edge_id: str, decision: str, reason: str, evidence_refs: list[dict], reviewer: str) -> str:
    if decision not in REVIEW_STATES or not str(reason).strip():
        raise ValueError("A supported review decision and nonempty reason are required")
    previous = load_record(store, edge_id) if edge_id.startswith("rec:") else latest_record(store, "competition_edges", edge_id)
    if previous is None or previous["dataset"] != "competition_edges":
        raise ValueError("Unknown competition edge")
    current = latest_record(store, "competition_edges", previous["record_key"])
    if previous["record_id"] != current["record_id"]:
        raise ValueError("Review is based on a stale edge version; reload before reviewing")
    data = dict(previous["data"])
    data.update(review_status=decision, review_reason=reason.strip(), evidence_refs=evidence_refs)
    new_id = put_relation(store, data, reviewer=reviewer)
    if new_id == previous["record_id"]:
        return new_id
    action = {"subject_dataset": "competition_edges", "subject_id": data["edge_id"],
              "previous_version_id": previous["record_id"], "new_version_id": new_id,
              "decision": decision, "reason": reason.strip(), "evidence_refs": evidence_refs,
              "reviewer": reviewer}
    key = "review:" + hashlib.sha256(_json(action).encode()).hexdigest()
    put_derived_record(store, "review_actions", key, action, reviewer=reviewer, quality="review_recorded", knowledge_type="manual_observation")
    return new_id
