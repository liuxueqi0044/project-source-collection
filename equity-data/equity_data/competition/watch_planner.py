"""Read-only, one-hop monitoring scope frozen from reviewed entity records."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

from ..adapters.competition_source import validate_url
from ..store import now_utc


def _stamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _known(value):
    if isinstance(value, list):
        return bool(value) and all(_known(v) for v in value)
    return value is not None and str(value).strip().lower() not in {"", "unknown", "unspecified", "none", "null", "n/a", "not_disclosed", "未知"}


def _records(store, dataset):
    # Domain query applies recursive evidence/time eligibility when available.
    from .queries import records_snapshot
    return records_snapshot(store, dataset)


def plan_watches(store, config, target_ids=None, now=None, force=False):
    """Freeze current versions and due entrypoints; do not mutate state or fetch.

    Caps are resource controls, not claims about the number of real competitors.
    At most a single relationship hop is considered, even if config requests more.
    """
    settings = config.get("competition", {})
    when = _stamp(now or now_utc())
    stamp = when.isoformat().replace("+00:00", "Z")
    entities = {r["data"]["entity_id"]: r for r in _records(store, "entities")}
    edges = _records(store, "competition_edges")
    endpoint_rows = _records(store, "watch_endpoints")
    targets = sorted(set(target_ids if target_ids is not None else settings.get("target_ids", [])))
    if not targets:
        targets = sorted(key for key, row in entities.items() if row["data"].get("role") == "target")
    gaps, excluded = [], []
    caps = {"max_core_peers_per_target": max(0, int(settings.get("max_core_peers_per_target", 5))),
            "max_watch_peers_per_target": max(0, int(settings.get("max_watch_peers_per_target", 10))),
            "max_unique_peers": max(0, int(settings.get("max_unique_peers", 30))), "max_depth": 1}
    if int(settings.get("max_depth", 1)) != 1:
        gaps.append({"code": "COMPETITION_ONE_HOP_ONLY", "requested_depth": settings.get("max_depth"), "effective_depth": 1})
    for target in targets:
        if target not in entities:
            gaps.append({"code": "TARGET_IDENTITY_MISSING", "entity_id": target})
    eligible = []
    for row in edges:
        edge = row["data"]
        if edge.get("target_entity_id") not in targets:
            continue
        reason = None
        if edge.get("review_status") != "confirmed":
            reason = "relationship_not_confirmed"
        elif edge.get("peer_entity_id") not in entities or edge.get("target_entity_id") not in entities:
            reason = "identity_missing"
        elif edge.get("relation_type") not in {"direct_competitor", "direct_competition", "competitor", "substitute", "substitute_product", "potential_entrant", "direct", "competition"}:
            reason = "not_a_competition_relationship"
        elif not _known(edge.get("product_or_business")):
            reason = "unknown_business_scope_requires_review"
        elif edge.get("valid_from") and _stamp(edge["valid_from"]) > when:
            reason = "relationship_not_yet_valid"
        elif edge.get("valid_to") and (str(edge["valid_to"])[:10] < when.date().isoformat() if len(str(edge["valid_to"])) == 10 else _stamp(edge["valid_to"]) < when):
            reason = "relationship_expired"
        if reason:
            excluded.append({"edge_id": edge.get("edge_id"), "edge_version_id": row["record_id"], "reason": reason})
            continue
        unknown_scope = any(not _known(edge.get(key)) for key in ("geography", "customer_segment"))
        if unknown_scope:
            gaps.append({"code": "UNKNOWN_SCOPE_REQUIRES_REVIEW", "edge_id": edge["edge_id"], "core_eligible": False})
        review_overdue = bool(edge.get("review_due_at") and _stamp(edge["review_due_at"]) < when)
        if review_overdue:
            gaps.append({"code": "RELATIONSHIP_REVIEW_OVERDUE", "edge_id": edge["edge_id"], "evidence_ended": False})
        eligible.append((row, "watch" if edge.get("watch_level") == "watch" or review_overdue or unknown_scope else "core"))
    # Stable selection and explicit exclusion: no dependency on SQL row order.
    eligible.sort(key=lambda pair: (pair[0]["data"]["target_entity_id"], pair[1] != "core",
                                   int(pair[0]["data"].get("priority", 100)), pair[0]["data"]["peer_entity_id"], pair[0]["record_id"]))
    chosen, global_peers, per_target = [], set(), {}
    for row, desired in eligible:
        edge = row["data"]
        target, peer = edge["target_entity_id"], edge["peer_entity_id"]
        level_sets = per_target.setdefault(target, {"core": set(), "watch": set()})
        level = "core" if peer in level_sets["core"] else "watch" if peer in level_sets["watch"] else desired
        if level == "core" and peer not in level_sets["core"] and len(level_sets["core"]) >= caps["max_core_peers_per_target"]:
            level = "watch"
        reason = None
        if peer not in global_peers and len(global_peers) >= caps["max_unique_peers"]:
            reason = "global_peer_cap"
        elif peer not in level_sets[level] and len(level_sets[level]) >= caps["max_" + level + "_peers_per_target"]:
            reason = "per_target_" + level + "_cap"
        if reason:
            excluded.append({"edge_id": edge["edge_id"], "edge_version_id": row["record_id"], "reason": reason, "peer_entity_id": peer})
            continue
        global_peers.add(peer)
        level_sets[level].add(peer)
        chosen.append((row, level))
    subjects = {target: {"target_ids": {target}, "edge_version_ids": set(), "level": "core"} for target in targets if target in entities}
    for row, level in chosen:
        edge = row["data"]
        subject = subjects.setdefault(edge["peer_entity_id"], {"target_ids": set(), "edge_version_ids": set(), "level": level})
        subject["target_ids"].add(edge["target_entity_id"])
        subject["edge_version_ids"].add(row["record_id"])
        if level == "core":
            subject["level"] = "core"
    table = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='competition_endpoint_state'").fetchone()
    states = {r["endpoint_id"]: dict(r) for r in store.db.execute("SELECT * FROM competition_endpoint_state")} if table else {}
    merged = {}
    for row in sorted(endpoint_rows, key=lambda r: r["data"]["endpoint_id"]):
        endpoint = copy.deepcopy(row["data"])
        endpoint.setdefault("transport", copy.deepcopy(settings.get("transport", {})))
        subject = subjects.get(endpoint.get("entity_id"))
        if not subject:
            continue
        reason = None
        if endpoint.get("enabled") is False:
            reason = "endpoint_disabled"
        elif (endpoint.get("official_status") or endpoint.get("verification_status")) not in {"verified_official", "official_verified", "verified", "third_party_verified", "verified_third_party"}:
            reason = "endpoint_authority_requires_review"
        elif not endpoint.get("evidence_refs"):
            reason = "endpoint_authority_evidence_missing"
        endpoint.setdefault("official_status", endpoint.get("verification_status", "unknown"))
        try:
            canonical, _ = validate_url(endpoint.get("url", ""), endpoint.get("allowed_domains", []))
        except ValueError as exc:
            reason = str(exc)
        if reason:
            excluded.append({"endpoint_id": endpoint.get("endpoint_id"), "endpoint_version_id": row["record_id"], "reason": reason})
            continue
        endpoint.update(url=canonical, target_ids=sorted(subject["target_ids"]), edge_version_ids=sorted(subject["edge_version_ids"]),
                        entity_version_id=entities[endpoint["entity_id"]]["record_id"], endpoint_version_id=row["record_id"],
                        cadence="core3daily" if subject["level"] == "core" else "watch1daily")
        key = _hash({k: endpoint.get(k) for k in ("entity_id", "url", "locale", "currency", "auth_context", "selectors", "config_version", "observation_context", "format", "document_selectors", "ignore_selectors")})
        endpoint["config_hash"] = _hash({k: v for k, v in endpoint.items() if k not in {"last_success_at", "next_due_at"}})
        endpoint["endpoint_ids"] = [endpoint["endpoint_id"]]
        endpoint["endpoint_version_ids"] = [row["record_id"]]
        if key in merged:
            old = merged[key]
            old["endpoint_ids"].append(endpoint["endpoint_id"])
            old["endpoint_version_ids"].append(row["record_id"])
            old["target_ids"] = sorted(set(old["target_ids"] + endpoint["target_ids"]))
            old["edge_version_ids"] = sorted(set(old["edge_version_ids"] + endpoint["edge_version_ids"]))
        else:
            merged[key] = endpoint
    all_endpoints, due = [], []
    for endpoint in sorted(merged.values(), key=lambda e: (e["cadence"] != "core3daily", e["entity_id"], e["endpoint_id"])):
        state = states.get(endpoint["endpoint_id"], {})
        stale_config = state.get("config_hash") != endpoint["config_hash"]
        next_due = _stamp(state.get("next_due_at"))
        is_due = bool(force or stale_config or not next_due or next_due <= when)
        endpoint.update(due=is_due, last_success_at=state.get("last_success_at"), next_due_at=state.get("next_due_at"),
                        due_reason="manual_force" if force else "configuration_changed" if stale_config else "never_checked" if not next_due else "cadence_due" if is_due else "not_due")
        all_endpoints.append(endpoint)
        if is_due:
            due.append(endpoint)
    for entity_id in subjects:
        if not any(e["entity_id"] == entity_id for e in all_endpoints):
            gaps.append({"code": "NO_REVIEWED_ENDPOINT", "entity_id": entity_id, "target_ids": sorted(subjects[entity_id]["target_ids"])})
    result = {"planned_at": stamp, "target_ids": targets, "endpoints": due, "due_endpoints": due, "all_endpoints": all_endpoints,
              "entity_versions": [copy.deepcopy(entities[key]) for key in sorted(subjects)],
              "edge_versions": [copy.deepcopy(row) for row, _ in chosen], "excluded": excluded, "gaps": gaps,
              "caps": caps, "unique_peer_count": len(global_peers), "config_hash": _hash(settings),
              "coverage_basis": "Reviewed configured relationships and endpoints; depth one; exclusions retained"}
    result["plan_id"] = "competition-plan:" + _hash(result)
    return result
