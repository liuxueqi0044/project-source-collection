"""Stable enterprise identities; names, tickers and domains never merge entities."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .domain import latest_record, put_derived_record
from .evidence import validate_evidence

ENTITY_TYPES = {"company", "private_company", "foreign_company", "subsidiary", "brand", "organization", "unknown"}


def put_entity(store, data: dict, *, reviewer: str = "configuration") -> str:
    value = dict(data)
    identity = value.get("entity_id")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("A configured stable entity_id is required; names/tickers are not identities")
    name = value.get("name") or value.get("legal_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Entity name is required")
    value["name"] = name.strip()
    value.setdefault("entity_type", "unknown")
    if value["entity_type"] not in ENTITY_TYPES:
        raise ValueError("Unsupported entity type")
    if identity.startswith("sec:") and not re.fullmatch(r"sec:\d{10}", identity):
        raise ValueError("Preserve the existing canonical SEC company ID: sec:<10-digit CIK>")
    if identity.startswith("sec:"):
        if value.get("company_id") not in (None, identity):
            raise ValueError("SEC entity/company ID mapping conflicts")
        value["company_id"] = identity
        if value.get("cik") is not None and str(value["cik"]).zfill(10) != identity[4:]:
            raise ValueError("SEC entity ID conflicts with CIK")
    nested_refs = []
    for website in value.get("websites", []):
        if not isinstance(website, dict) or urlparse(website.get("url", "")).scheme not in {"http", "https"}:
            raise ValueError("Each website requires an HTTP(S) URL and explicit verification status")
        if website.get("verification_status") == "verified":
            nested_refs.extend(validate_evidence(store, website.get("evidence_refs", []), require_external=True))
    for alias in value.get("aliases", []):
        if not isinstance(alias, dict) or not alias.get("name"):
            raise ValueError("Aliases must be sourced objects, not automatic merge keys")
        if alias.get("evidence_refs"):
            nested_refs.extend(validate_evidence(store, alias["evidence_refs"]))
    # Identical names/tickers/domains are intentionally permitted. Explicit
    # external IDs are not silently assigned to a second internal identity.
    external = value.get("external_ids", {})
    if not isinstance(external, dict):
        raise ValueError("external_ids must be an object")
    for row in store.query("entities"):
        if row["data"].get("entity_id") == identity:
            continue
        other = row["data"].get("external_ids", {})
        if any(v and other.get(k) == v for k, v in external.items()):
            raise ValueError("External identity collision requires an explicit identity review; no automatic merge")
    value.setdefault("identity_status", "configured_unverified")
    value["evidence_refs"] = list(value.get("evidence_refs", []))
    for ref in nested_refs:
        if ref not in value["evidence_refs"]:
            value["evidence_refs"].append(ref)
    return put_derived_record(store, "entities", identity, value, reviewer=reviewer,
                              knowledge_type="manual_observation", company_id=value.get("company_id"))


def put_entity_link(store, data: dict, *, reviewer: str) -> str:
    """Brand ownership and parent/subsidiary links remain separately versioned."""
    value = dict(data)
    for field in ("link_id", "from_entity_id", "to_entity_id", "link_type"):
        if not value.get(field):
            raise ValueError("Entity link requires " + field)
    if value["link_type"] not in {"brand_of", "subsidiary_of", "formerly_named", "identity_mapping"}:
        raise ValueError("Unsupported entity link type")
    versions = []
    for identity in (value["from_entity_id"], value["to_entity_id"]):
        record = latest_record(store, "entities", identity)
        if record is None:
            raise ValueError("Entity link references an unregistered identity")
        versions.append(record["record_id"])
    value["entity_version_ids"] = versions
    if value.get("review_status") == "confirmed":
        value["evidence_refs"] = validate_evidence(store, value.get("evidence_refs", []), require_external=True)
    value.setdefault("valid_from", None)
    value.setdefault("valid_to", None)
    return put_derived_record(store, "entity_links", value["link_id"], value, reviewer=reviewer,
                              quality="manual_reviewed" if value.get("review_status") == "confirmed" else "needs_review",
                              knowledge_type="manual_observation")
