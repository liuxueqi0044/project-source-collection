"""Explicit source-grounded human/agent review, separate from automatic extraction.

Run: python -m equity_data.competition.events_review --config CONFIG --input JSON
This entry does not fetch, generate financial-impact estimates or place trades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from ..store import Store, _json
from .domain import latest_record, load_record, put_derived_record
from .evidence import EvidenceError, normalized_text, validate_evidence
from .events import EVENT_TYPES
from .relations import unknown_scope

REVIEW_VERSION = "competition-event-review-v5.0.0"
_FIELDS = {"actor_entity_id", "event_type", "action", "object", "status", "event_time", "event_time_precision",
           "publication_time", "publication_precision", "business_scope", "geography", "customer_segment"}
_SCOPE = ("business_scope", "geography", "customer_segment")
_STATUS = {
    "planned": r"\b(?:plans?|planned|intends?|will|scheduled|beginning)\b",
    "announced": r"\b(?:announces?|announced|unveils?|unveiled)\b",
    "signed": r"\b(?:signs?|signed|executed an agreement)\b",
    "completed": r"\b(?:completed|opened|launched|commenced|now available)\b",
    "cancelled": r"\b(?:cancelled|canceled|terminated|withdrawn)\b",
    "corrected": r"\b(?:corrected|correction|amended|restated)\b",
    "approved": r"\b(?:approved|approval granted|cleared)\b",
    "proposed": r"\b(?:proposed|proposes?|proposal)\b",
}


def _relink_reviewed_event(store, record_id):
    """Refresh only associations; completed acquisition/parse jobs stay intact."""
    from .linker import link_event
    from .queries import records_snapshot
    record = load_record(store, record_id)
    identities = []
    for link in link_event(record, records_snapshot(store, "competition_edges")):
        key = "link:" + hashlib.sha256(_json([record["data"]["event_id"], link["edge_id"]]).encode()).hexdigest()
        identities.append(put_derived_record(store, "competitive_event_links", key, link, quality=link["review_status"]))
    return identities


def _contains(value, refs):
    wanted = normalized_text(str(value)).casefold()
    return bool(wanted) and any(wanted in normalized_text(ref["excerpt"]).casefold() for ref in refs)


def _reviewed_scope(store, field, value, specification, fact_raws):
    if unknown_scope(value):
        return {"value": "unknown", "method": "not_established", "evidence_refs": []}
    if not isinstance(specification, dict):
        raise EvidenceError("Reviewed " + field + " needs separate scope_evidence")
    refs = validate_evidence(store, specification.get("evidence_refs", []), require_external=True)
    if not any(ref["raw_id"] in fact_raws for ref in refs):
        raise EvidenceError("Event scope requires evidence in the event source, not a competitor profile alone")
    source_phrase = specification.get("source_phrase")
    if not isinstance(source_phrase, str) or not _contains(source_phrase, refs):
        raise EvidenceError("Scope source_phrase must occur in the saved excerpt")
    literal = _contains(value, refs)
    mapping_reason = str(specification.get("mapping_reason", "")).strip()
    if not literal and len(mapping_reason) < 24:
        raise EvidenceError("Nonliteral scope requires an explicit reviewed mapping reason")
    broad = {"global", "worldwide", "all", "all businesses", "all customers", "all products", "全球", "全部业务", "所有客户"}
    if normalized_text(str(value)).casefold() in broad and not literal:
        raise EvidenceError("Unknown or local scope cannot be reviewed into a global/all-business claim")
    return {"value": value, "source_phrase": source_phrase, "mapping_reason": mapping_reason or None,
            "method": "literal_source_text" if literal else "reviewed_semantic_mapping", "evidence_refs": refs}


def _date_supported(value, refs):
    if _contains(value, refs):
        return True
    # Explicit complete source dates only; no year/timezone guessed from a
    # publication or observation clock, and no current-year completion.
    try:
        wanted = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if len(str(value)) != 10:
        return False
    patterns = [r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\b"]
    for ref in refs:
        for pattern in patterns:
            for raw in re.findall(pattern, ref["excerpt"], flags=re.I):
                cleaned = raw.replace(",", "").replace(".", "")
                for form in ("%B %d %Y", "%b %d %Y"):
                    try:
                        if datetime.strptime(cleaned, form).date() == wanted.date():
                            return True
                    except ValueError:
                        pass
    return False


def review_event(store, event_id: str, fields: dict, *, fact_evidence_refs: list[dict],
                 scope_evidence: dict, reviewer: str, reason: str,
                 method: str = "agent_evidence_review", decision: str = "confirmed",
                 field_evidence: dict | None = None) -> str:
    """Review an existing event version; every changed field remains auditable.

    Fields involving numeric amounts, currency/unit normalization or profit
    impact are deliberately outside this entry and rejected, not inferred.
    """
    if method not in {"agent_evidence_review", "human_review"}:
        raise ValueError("Review method must explicitly distinguish human and agent")
    if not str(reviewer).strip() or method == "human_review" and not reviewer.startswith("human:"):
        raise ValueError("Human review requires an explicit human: identity; agent review cannot impersonate it")
    if decision not in {"confirmed", "rejected", "needs_review"} or len(str(reason).strip()) < 12:
        raise ValueError("Supported decision and substantive review reason required")
    if not isinstance(fields, dict) or set(fields) - _FIELDS:
        raise ValueError("Unsupported review fields: financial amounts/impact and unrecognized fields cannot be supplied here")
    previous = load_record(store, event_id) if event_id.startswith("rec:") else latest_record(store, "competitive_events", event_id)
    if previous is None or previous["dataset"] != "competitive_events":
        raise ValueError("An existing event version is required")
    current = latest_record(store, "competitive_events", previous["record_key"])
    if previous["record_id"] != current["record_id"]:
        raise ValueError("Review is based on a stale event version")
    refs = validate_evidence(store, fact_evidence_refs, require_external=True)
    field_evidence = field_evidence or {}
    value = {**previous["data"], **fields}
    # Remove the previous review bookkeeping when deriving the next review.
    for key in ("review", "reviewed_scope", "review_of_version_id", "reviewer"):
        value.pop(key, None)
    actor = latest_record(store, "entities", value.get("actor_entity_id", ""))
    if actor is None:
        raise ValueError("Reviewed actor must have a stable registered entity identity")
    if not any(ref.get("entity_id") == value["actor_entity_id"] or value["actor_entity_id"] in ref.get("evidence_entity_ids", []) for ref in refs):
        raise EvidenceError("Fact source must be explicitly attributed to the event actor")
    scope = {}
    if decision == "confirmed":
        if value.get("event_type") not in EVENT_TYPES:
            raise ValueError("Unsupported event type")
        for key in ("action", "object"):
            if unknown_scope(value.get(key)) or not _contains(value[key], refs):
                raise EvidenceError("Reviewed " + key + " must be literal in the fact excerpt")
        status = value.get("status")
        if status not in _STATUS or not any(re.search(_STATUS[status], ref["excerpt"], re.I) for ref in refs):
            raise EvidenceError("Reviewed stage is not supported by an explicit source statement")
        if status == "completed" and any(re.search(r"\b(?:will|plans?|planned|intends?|scheduled)\b", ref["excerpt"], re.I) for ref in refs):
            raise EvidenceError("Future/planned language cannot be promoted to a completed event")
        for key in ("event_time", "publication_time"):
            if value.get(key):
                time_refs = validate_evidence(store, field_evidence.get(key, []), require_external=True)
                if not _date_supported(value[key], time_refs):
                    raise EvidenceError("Reviewed " + key + " lacks a complete source date/time")
                refs.extend(ref for ref in time_refs if ref not in refs)
        fact_raws = {ref["raw_id"] for ref in refs}
        for key in _SCOPE:
            scope[key] = _reviewed_scope(store, key, value.get(key), scope_evidence.get(key), fact_raws)
            value[key] = scope[key]["value"]
            refs.extend(ref for ref in scope[key]["evidence_refs"] if ref not in refs)
    review_data = {"method": method, "reviewer": reviewer, "reason": reason.strip(), "decision": decision,
                   "fields_explicitly_supplied": fields, "fact_evidence_refs": validate_evidence(store, fact_evidence_refs, require_external=True),
                   "scope_evidence": scope, "review_version": REVIEW_VERSION,
                   "counts_as_automatic_extraction": False, "human_review_claimed": method == "human_review",
                   "financial_impact_generated": False}
    # Repeating the same review is a no-op, including its processing evidence.
    if previous["data"].get("review") == review_data:
        _relink_reviewed_event(store, previous["record_id"])
        return previous["record_id"]
    value.update(review=review_data, reviewed_scope=scope, review_of_version_id=previous["record_id"],
                 evidence_refs=refs, entity_version_ids=[actor["record_id"]],
                 supporting_record_ids=sorted(set(previous["data"].get("supporting_record_ids", []) + [previous["record_id"]])),
                 fact_stage="extracted_fact" if decision == "confirmed" else "rejected" if decision == "rejected" else "candidate",
                 extraction_method="human_review" if method == "human_review" else "agent_review",
                 extraction_version=REVIEW_VERSION, automatic_extraction_success=False,
                 verification_status="human_reviewed_source_claim" if method == "human_review" else "agent_reviewed_source_claim")
    identity = put_derived_record(store, "competitive_events", previous["record_key"], value, reviewer=reviewer,
                                  knowledge_type="company_statement", quality="human_reviewed" if method == "human_review" else "agent_reviewed")
    action = {"subject_dataset": "competitive_events", "subject_id": value.get("event_id"), "previous_version_id": previous["record_id"],
              "new_version_id": identity, "decision": decision, "reason": reason.strip(), "review_method": method,
              "counts_as_automatic_extraction": False, "evidence_refs": refs}
    key = "event-review:" + hashlib.sha256(_json(action).encode()).hexdigest()
    put_derived_record(store, "review_actions", key, action, reviewer=reviewer, knowledge_type="manual_observation" if method == "human_review" else "system_inference")
    _relink_reviewed_event(store, identity)
    return identity


def main(argv=None):
    from ..cli import load_config
    from ..local_control import collector_lock
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    request = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    with collector_lock(config["storage"]) as acquired:
        if not acquired:
            raise RuntimeError("Collector active; event review was not written")
        with Store(config["storage"]) as store:
            identity = review_event(store, **request)
            result = {"record_id": identity, "counts_as_automatic_extraction": False, "record": load_record(store, identity),
                      "refreshed_links": [row for row in store.query("competitive_event_links") if row["data"].get("event_version_id") == identity]}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
