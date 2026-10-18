"""Bounded discovery of evidence-linked candidates, never auto confirmation."""
from __future__ import annotations

import re

from .change_detector import clean, digest
from .events import evidence

DISCOVERY_VERSION = "competition-discovery-v5.0.0"


def discover_candidates(documents: list[dict], entities: list[dict], target_id=None, budget=100, **kwargs) -> dict:
    """Find named peers in existing evidence; no search/network dependency.

    Known names and aliases are identity candidates. Duplicate aliases remain
    ambiguous. An explicit 'competitor' sentence is still a review candidate
    because product, geography and entity identity need separate confirmation.
    """
    output = {"candidates": [], "gaps": ["cross_web_search_not_configured", "unknown_entity_names_not_resolved"], "discovery_version": DISCOVERY_VERSION, "truncated": False}
    aliases = {}
    for record in entities:
        entity = record.get("data") or record
        entity_id = entity.get("entity_id")
        names = [entity.get("legal_name"), entity.get("name")]
        for alias in entity.get("aliases", []):
            names.append(alias.get("name") or alias.get("alias") if isinstance(alias, dict) else alias)
        for name in names:
            if clean(name):
                aliases.setdefault(clean(name).casefold(), set()).add(entity_id)
    seen = set()
    for document in documents:
        data = document.get("data") or document
        body = clean(data.get("body_text") or data.get("text"))
        owner = target_id or data.get("entity_id") or data.get("company_id")
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
            if not sentence:
                continue
            explicit = bool(re.search(r"\b(?:competitors?|competes? with|competition from|competitive with)\b", sentence, re.I))
            for name, identities in aliases.items():
                if not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", sentence, re.I):
                    continue
                peers = sorted(identity for identity in identities if identity and identity != owner)
                for peer in peers:
                    key = digest([owner, peer, data.get("raw_id"), sentence])
                    if key in seen:
                        continue
                    if len(output["candidates"]) >= int(budget):
                        output["truncated"] = True
                        output["gaps"].append("candidate_budget_exhausted_remaining_documents_not_complete")
                        return output
                    seen.add(key)
                    output["candidates"].append({"candidate_id": "competition:candidate:" + key,
                        "target_entity_id": owner, "peer_entity_id": peer,
                        "relation_type": "unknown", "product_or_business": "unknown",
                        "geography": "unknown", "customer_segment": "unknown",
                        "overlap_basis": "explicit_competition_language" if explicit else "cooccurrence_only",
                        "evidence_status": "source_explicit" if explicit else "unverified",
                        "review_status": "candidate", "identity_status": "ambiguous" if len(identities) > 1 else "name_match_needs_review",
                        "evidence_refs": [evidence(data, sentence)],
                        "valid_from": None, "valid_to": None,
                        "discovery_version": DISCOVERY_VERSION,
                        "unknowns": ["identity_confirmation", "competition_scope", "validity_interval"]})
    return output
