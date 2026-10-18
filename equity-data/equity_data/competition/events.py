"""Evidence-bound source claims, not investment conclusions or LLM guesses."""
from __future__ import annotations

import re

from .change_detector import clean, compare_snapshots, digest, numeric_field, parse_time

EXTRACTION_VERSION = "competition-events-v5.0.0"
EVENT_TYPES = ("financial_update", "product_change", "pricing_terms_change",
               "commercial_agreement", "capacity_market_entry", "regulatory_merger")
CAPABILITY_MATRIX = {
    "financial_update": {"rules": "explicit guidance/reporting sentences; configured captures for values and period", "unsupported": "unconfigured financial tables, implicit GAAP equivalence, full forecast parsing"},
    "product_change": {"rules": "explicit launch/withdrawal statements", "unsupported": "inferred withdrawal from a missing or failed page"},
    "pricing_terms_change": {"rules": "two successful structured snapshots with identical explicit price context", "unsupported": "transaction prices, unconfigured variants, baseline as change"},
    "commercial_agreement": {"rules": "explicit contract/partnership statements and optional exact identifiers", "unsupported": "inferred target lost order or implicit execution"},
    "capacity_market_entry": {"rules": "explicit plans/opening/production statements with separate stages", "unsupported": "implied capacity from hiring or permits"},
    "regulatory_merger": {"rules": "explicit proposed/approved/completed/cancelled transaction statements", "unsupported": "legal interpretation or exhaustive regulator coverage"},
}

_DEFAULT_RULES = [
    {"event_type": "financial_update", "pattern": r"\b(?P<action>raises?|lowers?|updates?|revises?|withdraws?|issues?)\b.{0,100}?\b(?P<object>guidance|outlook|forecast)\b[^.!?]{0,250}"},
    {"event_type": "product_change", "pattern": r"\b(?P<action>launch(?:es|ed)?|introduc(?:es|ed)|unveil(?:s|ed)?|discontinu(?:es|ed)|retir(?:es|ed))\s+(?P<object>[^.!?]{3,220})"},
    {"event_type": "commercial_agreement", "pattern": r"\b(?P<action>sign(?:s|ed)?|win(?:s)?|won|award(?:s|ed)?|enter(?:s|ed)?)\b[^.!?]{0,100}?\b(?P<object>contract|agreement|partnership|order)\b[^.!?]{0,220}"},
    {"event_type": "capacity_market_entry", "pattern": r"\b(?P<action>plans?|opens?|opened|expands?|expanded|commences?|commenced)\b[^.!?]{0,100}?\b(?P<object>plant|factory|capacity|production|facility|distribution center|market)\b[^.!?]{0,220}"},
    {"event_type": "regulatory_merger", "pattern": r"\b(?P<action>propos(?:es|ed)|approv(?:es|ed)|complet(?:es|ed)|cancel(?:s|led)|announc(?:es|ed))\b[^.!?]{0,100}?\b(?P<object>merger|acquisition|regulatory approval|investigation|settlement)\b[^.!?]{0,220}"},
]


def evidence(snapshot, excerpt, locator="document:text", **extra):
    ref = {key: snapshot.get(key) for key in ("raw_id", "sha256", "retrieval_id", "url", "observed_at", "endpoint_id", "publication_time", "publication_precision")}
    ref.update(locator=locator, excerpt=clean(excerpt), snapshot_version_id=snapshot.get("record_id") or snapshot.get("snapshot_version_id"))
    if snapshot.get("record_id") or snapshot.get("snapshot_version_id"):
        ref["record_id"] = snapshot.get("record_id") or snapshot.get("snapshot_version_id")
    ref.update(extra)
    return ref


def validate_excerpt(snapshot, excerpt):
    """This local guard complements persistence's independent raw validation."""
    wanted = clean(excerpt)
    return bool(wanted) and wanted in clean(snapshot.get("body_text"))


def _known(value):
    return value is not None and clean(value).casefold() not in ("", "unknown", "not_disclosed", "unspecified", "n/a")


def _identity_verified(endpoint):
    # A verified official domain by itself does not establish which legal
    # entity made a particular claim. Runner must resolve the entity record.
    return endpoint.get("identity_status") in ("verified", "confirmed") or endpoint.get("entity_verified") is True


def _status(text, action, supplied=None):
    if supplied:
        supplied = clean(supplied).casefold()
        if supplied in ("planned", "announced", "signed", "completed", "cancelled", "corrected", "approved", "proposed"):
            return supplied
    if re.search(r"\b(?:correction|corrected|corrects|restated|amended)\b", text, re.I):
        return "corrected"
    if re.search(r"\b(?:cancel(?:s|led)|withdraw(?:s|n))\b", text, re.I):
        return "cancelled"
    if re.search(r"\b(?:plans?|planned|intends?|expects?|proposes?|will|scheduled)\b", text, re.I):
        return "proposed" if re.search(r"\bpropos", action, re.I) else "planned"
    if re.search(r"\b(?:complet(?:es|ed)|open(?:s|ed)|commenc(?:es|ed))\b", action, re.I):
        return "completed"
    if re.search(r"\bapprov", action, re.I):
        return "approved"
    if re.search(r"\bsign", action, re.I):
        return "signed"
    return "announced"


def _scope(group, endpoint):
    result, provenance = {}, {}
    for key in ("business_scope", "geography", "customer_segment"):
        if _known(group.get(key)):
            result[key] = clean(group[key])
            provenance[key] = "source_capture"
        else:
            # Endpoint context is useful for variants but cannot silently turn
            # a generic news sentence into a known-scoped claim.
            result[key] = "unknown"
            provenance[key] = "unknown"
    return result, provenance


def _event(snapshot, endpoint, kind, group, excerpt):
    scope, scope_provenance = _scope(group, endpoint)
    action = clean(group.get("action")) or "source_statement"
    timestamp, precision = parse_time(group.get("event_time"))
    number = None
    if group.get("amount"):
        # Captured currency is part of the same source match, not a guessed
        # default '$ = USD'. This never reuses a previous successful value.
        number = numeric_field(group["amount"], {"currency": group.get("currency"), "unit": group.get("unit")})
    event = {"actor_entity_id": endpoint.get("entity_id") or snapshot.get("entity_id"),
        "event_type": kind, "action": action, "object": clean(group.get("object")) or "unknown",
        **scope, "scope_provenance": scope_provenance,
        "status": _status(excerpt, action, group.get("status")),
        "event_time": timestamp, "event_precision": precision,
        "publication_time": snapshot.get("publication_time"),
        "publication_precision": snapshot.get("publication_precision", "unknown"),
        "first_observed_at": snapshot.get("observed_at"), "processed_at": None,
        "old_value": None, "new_value": number.get("value") if number else None,
        "unit": number.get("unit") if number else clean(group.get("unit")) or None,
        "currency": number.get("currency") if number else clean(group.get("currency")) or None,
        "period": clean(group.get("period")) or None,
        "context": {"numeric_interpretation": "source_capture" if number else "not_extracted",
                    "statement": clean(excerpt), "source_claimed": True},
        "evidence_refs": [evidence(snapshot, excerpt)],
        "extraction_method": "deterministic_source_capture",
        "extraction_version": EXTRACTION_VERSION,
        "verification_status": "source_claimed_unverified", "fact_stage": "extracted_fact",
        "revision_of": clean(group.get("revision_of")) or None,
        "unknowns": [], "candidate_reasons": []}
    if not event["actor_entity_id"] or not _identity_verified(endpoint):
        event["candidate_reasons"].append("actor_identity_not_verified")
    for key in ("object", "business_scope", "geography", "customer_segment"):
        if not _known(event[key]):
            event["unknowns"].append(key)
            event["candidate_reasons"].append("unknown_" + key)
    if number and number.get("issue"):
        event["candidate_reasons"].append(number["issue"])
    if number and (not event["unit"] or not event["period"]):
        event["candidate_reasons"].append("numeric_unit_or_period_unknown")
    if not snapshot.get("raw_id") or not snapshot.get("sha256") or not snapshot.get("retrieval_id"):
        event["candidate_reasons"].append("missing_raw_provenance")
    if not validate_excerpt(snapshot, excerpt):
        event["candidate_reasons"].append("excerpt_not_in_snapshot")
    identifier = next((clean(group.get(k)) for k in ("event_identifier", "contract_id", "project_id", "product_id") if _known(group.get(k))), None)
    if identifier:
        event["event_identity"] = {"basis": "explicit_source_identifier", "identifier": identifier,
            "key": digest([event["actor_entity_id"], kind, identifier]), "cross_source_merge_allowed": True}
    else:
        event["event_identity"] = {"basis": "source_claim_only", "identifier": None,
            "key": digest([event["actor_entity_id"], snapshot.get("endpoint_id"), snapshot.get("url"), kind, clean(excerpt)]),
            "cross_source_merge_allowed": False, "duplicate_candidate": True}
    event["event_id"] = "competition:event:" + event["event_identity"]["key"]
    if event["candidate_reasons"]:
        event["fact_stage"] = "candidate"
    return event


def _price_events(before, after, endpoint):
    output = {"events": [], "candidates": [], "issues": []}
    if before is None:
        return output
    comparison = compare_snapshots(before, after, endpoint)
    if comparison["status"] != "changed":
        if comparison["status"] == "rejected":
            output["issues"].extend(comparison["issues"])
        return output
    required = ("product", "geography", "specification", "quantity", "tax_inclusion", "payment_period", "price_type", "login_status")
    for change in comparison["changes"]:
        old, new = change.get("before") or {}, change.get("after") or {}
        if new.get("kind") != "price" and change["field"] not in endpoint.get("price_fields", []):
            continue
        reasons = []
        if change["status"] != "changed" or old.get("issue") or new.get("issue"):
            output["issues"].append("price_not_comparable_missing_or_rejected_field:" + change["field"])
            continue
        old_ctx, new_ctx = before.get("observation_context", {}), after.get("observation_context", {})
        for key in required:
            if not _known(old_ctx.get(key)) or not _known(new_ctx.get(key)):
                reasons.append("unknown_price_context:" + key)
            elif clean(old_ctx[key]).casefold() != clean(new_ctx[key]).casefold():
                reasons.append("changed_price_context:" + key)
        for key in ("currency", "unit"):
            if not _known(old.get(key)) or not _known(new.get(key)) or old[key] != new[key]:
                reasons.append("incomparable_price_" + key)
        group = {"action": "observed_price_change", "object": new_ctx.get("product"),
            "business_scope": new_ctx.get("business_scope"), "geography": new_ctx.get("geography"),
            "customer_segment": new_ctx.get("customer_segment")}
        event = _event(after, endpoint, "pricing_terms_change", group, new.get("excerpt"))
        event.update(old_value=old.get("value"), new_value=new.get("value"),
            unit=new.get("unit"), currency=new.get("currency"), period=new_ctx.get("payment_period"),
            status="observed_change", event_time=None, event_precision="observation_interval",
            observation_interval={"after": before.get("observed_at"), "on_or_before": after.get("observed_at")},
            evidence_refs=[evidence(before, old.get("excerpt"), old.get("locator", "document:text")), evidence(after, new.get("excerpt"), new.get("locator", "document:text"))],
            context={"before": old_ctx, "after": new_ctx, "price_type": new_ctx.get("price_type"), "context_provenance": "configured_fixed_observation_environment"})
        event["scope_provenance"] = {key: "configured_observation_environment" for key in ("business_scope", "geography", "customer_segment")}
        event["candidate_reasons"].extend(reasons)
        event["event_identity"] = {"basis": "two_snapshot_price_observation", "cross_source_merge_allowed": False,
            "key": digest([endpoint.get("endpoint_id"), change["field"], before.get("raw_id"), after.get("raw_id")])}
        event["event_id"] = "competition:event:" + event["event_identity"]["key"]
        if not validate_excerpt(before, old.get("excerpt")):
            event["candidate_reasons"].append("previous_excerpt_not_in_snapshot")
        event["fact_stage"] = "candidate" if event["candidate_reasons"] else "extracted_fact"
        output["candidates" if event["candidate_reasons"] else "events"].append(event)
    return output


def extract_events(snapshot: dict, endpoint: dict, before: dict | None = None) -> dict:
    result = {"events": [], "candidates": [], "issues": [], "extraction_version": EXTRACTION_VERSION}
    if not snapshot.get("valid", True):
        result["issues"].append("invalid_snapshot_no_business_event")
        return result
    prices = _price_events(before, snapshot, endpoint)
    for key in ("events", "candidates", "issues"):
        result[key].extend(prices[key])
    rules = endpoint.get("event_rules") or _DEFAULT_RULES
    # Feed item dates remain independent; the newest item does not date the
    # entire feed. A feed excerpt is not a claim that full article was saved.
    sources = snapshot.get("items") or [snapshot]
    seen = set()
    for source in sources:
        local = {**snapshot, **source, "body_text": source.get("body_text", snapshot.get("body_text", ""))}
        if source is not snapshot:
            local["url"] = snapshot.get("url")  # evidence URL is acquired feed
        body = clean(local.get("body_text"))
        for rule in rules:
            kind = rule.get("event_type")
            if kind not in EVENT_TYPES or kind == "pricing_terms_change":
                result["issues"].append("unsupported_rule_event_type:" + str(kind))
                continue
            try:
                pattern = re.compile(rule["pattern"], re.I)
            except (KeyError, re.error, TypeError):
                result["issues"].append("invalid_event_pattern")
                continue
            # Regex patterns are trusted local configuration. Bounded input
            # prevents oversized documents; truncation is explicit, never
            # reported as a completed extraction.
            limit = int(endpoint.get("max_semantic_characters", 500000))
            if len(body) > limit:
                result["issues"].append("semantic_character_budget_truncated")
            for match in pattern.finditer(body[:limit]):
                excerpt = clean(match.group())
                context_start = max(body.rfind(".", 0, match.start()) + 1, body.rfind("!", 0, match.start()) + 1)
                prefix = body[context_start:match.start()]
                if re.search(r"\b(?:not|never|no plans? to|den(?:y|ies|ied)|rumou?rs?)\b", prefix[-140:] + excerpt, re.I):
                    result["issues"].append("negated_or_uncertain_claim_rejected")
                    continue
                groups = {k: clean(v) if v is not None else None for k, v in match.groupdict().items()}
                # Preserve a plan qualifier immediately before the action.
                full_excerpt = clean(prefix[-140:] + excerpt) if re.search(r"\b(?:plans?|intends?|expects?|will)\b", prefix[-140:], re.I) else excerpt
                identity = (kind, full_excerpt)
                if identity in seen:
                    continue
                seen.add(identity)
                event = _event(local, endpoint, kind, groups, full_excerpt)
                if source is not snapshot:
                    event["context"].update(evidence_extent="feed_entry_only", linked_article_url=source.get("url"), feed_item_id=source.get("source_id"))
                event["rule_id"] = rule.get("rule_id") or digest(rule)[:16]
                result["candidates" if event["candidate_reasons"] else "events"].append(event)
    return result
