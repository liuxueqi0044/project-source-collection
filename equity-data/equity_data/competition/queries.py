"""Evidence bundles whose dependencies obey the same historical cutoffs."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..store import _timestamp
from .domain import load_record
from .evidence import EvidenceError, validate_evidence


class _Snapshot:
    def __init__(self, store, *, as_of=None, known_at=None, processed_at=None):
        self.store = store
        self.cutoffs = {"as_of": as_of, "known_at": known_at, "processed_at": processed_at}
        self.as_of = _timestamp(as_of, cutoff=True) if as_of else None
        self.known_at = _timestamp(known_at, cutoff=True) if known_at else None
        self.processed_at = _timestamp(processed_at, cutoff=True) if processed_at else None
        if self.processed_at:
            self.known_at = min(self.known_at, self.processed_at) if self.known_at else self.processed_at
        self.datasets = {}
        self.visibility = {}
        self.failures = {}

    def candidates(self, dataset):
        if dataset not in self.datasets:
            self.datasets[dataset] = {r["record_id"]: r for r in self.store.query(dataset, latest=False, **self.cutoffs)}
        return self.datasets[dataset]

    def visible(self, record_id, visiting=None):
        if record_id in self.visibility:
            return self.visibility[record_id]
        visiting = set(visiting or ())
        if record_id in visiting:
            self.failures[record_id] = "cyclic_evidence_dependency"
            return False
        visiting.add(record_id)
        record = load_record(self.store, record_id)
        if record is None:
            self.failures[record_id] = "referenced_record_missing"
            return False
        if record_id not in self.candidates(record["dataset"]):
            self.failures[record_id] = "record_outside_requested_time_or_rejected"
            self.visibility[record_id] = False
            return False
        try:
            refs = validate_evidence(self.store, record["data"].get("evidence_refs", []))
        except (EvidenceError, OSError) as exc:
            self.failures[record_id] = "evidence_validation_failed: " + str(exc)
            self.visibility[record_id] = False
            return False
        dependencies = []
        for ref in refs:
            if self.known_at and ref["retrieved_at"] > self.known_at:
                self.failures[record_id] = "supporting_raw_acquired_after_cutoff"
                self.visibility[record_id] = False
                return False
            if ref.get("record_id"):
                dependencies.append(ref["record_id"])
            elif self.as_of and ref["retrieved_at"] > self.as_of:
                # A date merely supplied in a reference is not proof that this
                # exact web copy existed in the past. Use source records for
                # verified disclosure clocks; raw-only copies are conservative.
                self.failures[record_id] = "raw_only_evidence_not_observed_by_as_of"
                self.visibility[record_id] = False
                return False
        data = record["data"]
        for field in ("edge_version_id", "event_version_id", "snapshot_version_id", "before_snapshot_version_id", "after_snapshot_version_id"):
            if data.get(field):
                dependencies.append(data[field])
        dependencies.extend(data.get("entity_version_ids", []))
        dependencies.extend(data.get("supporting_record_ids", []))
        if any(not self.visible(dependency, visiting) for dependency in dependencies):
            self.failures[record_id] = "referenced_version_not_visible"
            self.visibility[record_id] = False
            return False
        self.visibility[record_id] = True
        return True

    def records(self, dataset):
        # Select the current version under the time filters before checking its
        # dependencies. Broken newest evidence must not resurrect an older edge.
        selected = self.store.query(dataset, latest=True, **self.cutoffs)
        return [record for record in selected if self.visible(record["record_id"])]


def records_snapshot(store, dataset: str, *, as_of=None, known_at=None, processed_at=None) -> list[dict]:
    return _Snapshot(store, as_of=as_of, known_at=known_at, processed_at=processed_at).records(dataset)


def _valid_on(data, timestamp):
    day = timestamp[:10]
    return (not data.get("valid_from") or str(data["valid_from"])[:10] <= day) and (not data.get("valid_to") or str(data["valid_to"])[:10] >= day)


def _scope_is_explicit_match(event, edge):
    from .relations import unknown_scope
    for left, right in (("business_scope", "product_or_business"), ("geography", "geography"), ("customer_segment", "customer_segment")):
        a, b = event.get(left), edge.get(right)
        if unknown_scope(a) or unknown_scope(b):
            return False
        first = a if isinstance(a, list) else [a]
        second = b if isinstance(b, list) else [b]
        if not {str(x).strip().casefold() for x in first} & {str(x).strip().casefold() for x in second}:
            return False
    return True


def query_competition(store, target_id: str, *, as_of=None, known_at=None, processed_at=None,
                      include_candidates: bool = True) -> dict:
    """Current/historical research bundle; never substitute today's edge version."""
    state = _Snapshot(store, as_of=as_of, known_at=known_at, processed_at=processed_at)
    clock = state.as_of or datetime.now(timezone.utc).isoformat()
    all_edges = [r for r in state.records("competition_edges") if r["data"].get("target_entity_id") == target_id]
    edges = [r for r in all_edges if _valid_on(r["data"], clock)]
    current_edge_versions = {r["record_id"]: r for r in edges}
    entity_ids = {target_id} | {r["data"]["peer_entity_id"] for r in edges}
    entities = [r for r in state.records("entities") if r["data"].get("entity_id") in entity_ids]
    events = {r["record_id"]: r for r in state.records("competitive_events")}
    links, unresolved, core = [], [], []
    for row in state.records("competitive_event_links"):
        data = row["data"]
        if data.get("target_entity_id") != target_id:
            continue
        edge = current_edge_versions.get(data.get("edge_version_id"))
        event = events.get(data.get("event_version_id"))
        if edge is None or event is None:
            unresolved.append({"record_id": row["record_id"], "reason": "link_requires_recompute_or_exact_visible_edge_and_event_versions"})
            continue
        links.append(row)
        if (edge["data"].get("review_status") == "confirmed" and data.get("review_status") in {"confirmed", "matched", "core", "rule_matched"}
                and not data.get("mismatch_or_unknown") and event["data"].get("fact_stage") == "extracted_fact"
                and _scope_is_explicit_match(event["data"], edge["data"])):
            core.append({"link": row, "event": event, "edge": edge})
    linked_event_ids = {r["data"]["event_version_id"] for r in links}
    relevant_events = [r for key, r in events.items() if key in linked_event_ids]
    pending = [r for r in edges if r["data"].get("review_status") in {"candidate", "needs_review"}]
    pending += [r for r in links if r["data"].get("review_status") not in {"confirmed", "matched", "core", "rule_matched"} or r["data"].get("mismatch_or_unknown")]
    pending += [r for r in relevant_events if r["data"].get("fact_stage") != "extracted_fact"]
    refs = {}
    for row in entities + edges + relevant_events + links:
        for ref in row["data"].get("evidence_refs", []):
            refs[(ref["raw_id"], ref.get("retrieval_id"), ref["locator"], ref["excerpt"])] = ref
    if not include_candidates:
        edges = [r for r in edges if r["data"].get("review_status") == "confirmed"]
    hypotheses = [r for r in state.records("analysis_hypotheses") if r["data"].get("target_entity_id") == target_id]
    return {"schema_version": "competition-bundle-v5.0", "target_entity_id": target_id,
            "cutoffs": state.cutoffs, "entities": entities, "relations": edges, "edges": edges,
            "events": relevant_events, "links": links, "core_brief": core,
            "analysis_hypotheses": hypotheses, "evidence_refs": list(refs.values()), "pending_review": pending,
            "gaps": unresolved + [{"record_id": key, "reason": value} for key, value in state.failures.items()],
            "time_limitations": ["Raw-only web evidence uses its saved observation time, not an unverified claimed publication date.",
                                 "Use all three cutoffs to constrain public availability, acquisition and parsing; inherited V4.1 historical-state replay limitations remain.",
                                 "Source assertions and human-confirmed overlap are not independently verified financial impact."]}
