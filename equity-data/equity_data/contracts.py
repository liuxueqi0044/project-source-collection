"""Additive semantic contracts: extraction stage is independent of verification.

The saved raw response is the original. A snippet can be a candidate without
being a factual relationship or numerical observation. Only a separate,
explicit equivalence mapping may produce normalized metrics.
"""
from __future__ import annotations

CONTRACT_VERSION = "financial-semantic-v1"
CONTRACTS = {
    "financial_facts": {"family": "financial_metric", "required": ["concept_qname", "value", "period", "unit", "entity", "dimensions", "accounting_basis"]},
    "guidance": {"family": "management_guidance", "required": ["original_metric_name", "target_period", "point_or_bounds", "unit", "entity", "accounting_basis", "conditions"]},
    "operating_metrics": {"family": "operating_metric", "required": ["original_metric_name", "definition", "value", "unit", "period", "entity", "dimensions"]},
    "relations": {"family": "customer_supplier_relation", "required": ["subject", "counterparty", "relation_type", "applicable_period", "confirmation_level"]},
    "debt_maturities": {"family": "debt", "required": ["obligor", "instrument", "principal", "currency", "maturity", "interest_terms", "scope"]},
    "commitments": {"family": "commitment", "required": ["obligor", "category", "amount", "currency", "payment_period", "scope", "conditions"]},
    "risk_disclosures": {"family": "risk", "required": ["entity", "risk_category", "applicable_period", "statement"]},
}


def annotate_contract(observation, *, content_hash_reference=False):
    """Annotate only known facts; absent semantics stay explicitly unknown."""
    data, dataset = observation["data"], observation["dataset"]
    definition = CONTRACTS.get(dataset)
    if definition is None:
        return
    candidate = data.get("extraction_method") == "narrative_evidence_candidate"
    stage = "candidate" if candidate else "extracted_fact"
    unknowns = {}
    entity = data.get("entity")
    if not entity:
        entity = {"identifier": None, "scope": "unresolved_document_subject", "resolution": "requires_adapter_company_link_and_scope_evidence"}
        unknowns["entity_identifier"] = "No issuer identifier stated within this evidence span; adapter association must be retained separately"
    if dataset == "financial_facts":
        period = {"start": observation.get("period_start"), "end": observation.get("period_end"), "type": data.get("period_type")}
        basis = data.get("accounting_basis")
        if not basis:
            unknowns["accounting_basis"] = "Concept namespace or name alone does not prove accounting basis"
    elif dataset == "guidance":
        period, basis = data.get("target_period"), data.get("accounting_basis")
        if not observation.get("period_end"):
            unknowns["period_end"] = "Fiscal label retained; no explicit calendar end date"
        if basis == "unspecified":
            unknowns["accounting_basis"] = "No explicit GAAP or non-GAAP statement in evidence scope"
    else:
        period, basis = None, None
        unknowns["period"] = "Candidate has not resolved an applicable period"
    if not observation.get("currency") and observation.get("unit", "") and "dollar_symbol" in observation["unit"]:
        unknowns["currency"] = "Dollar symbol does not identify a unique currency"
    if candidate:
        for field in definition["required"]:
            unknowns.setdefault(field, "Evidence candidate only; field not structurally extracted or confirmed")
    data["fact_stage"] = stage
    data["semantic_contract"] = {"version": CONTRACT_VERSION, "family": definition["family"], "stage": stage,
        "applicable_entity": entity, "applicable_period": period, "unit": observation.get("unit"),
        "currency": observation.get("currency"), "accounting_basis": basis,
        "dimensions": data.get("dimensions", []), "unknown_reasons": unknowns,
        "evidence": {"source_locator": observation.get("source_locator"), "content_sha256": data.get("content_sha256"),
                     "parser_version": data.get("parser_version")},
        "normalized_metric": None, "normalization_status": "not_mapped", "verification_status": observation.get("quality", "parsed_unverified")}
    if content_hash_reference:
        # SEC snapshot hash changes when any unrelated fact is added. Keep the
        # immutable hash on the record/raw join rather than fact identity.
        evidence = data["semantic_contract"]["evidence"]
        evidence.pop("content_sha256", None)
        evidence["content_sha256_reference"] = "records.raw_id -> raw_blobs.sha256"


def validate_contract(observation):
    """Minimal invariant checks, not a substitute for independent raw audit."""
    data = observation["data"]
    contract = data.get("semantic_contract", {})
    errors = []
    if contract.get("stage") not in {"candidate", "extracted_fact", "normalized_metric"}:
        errors.append("missing_or_invalid_fact_stage")
    if contract.get("stage") == "normalized_metric" and not contract.get("mapping_evidence"):
        errors.append("normalized_metric_requires_mapping_evidence")
    evidence = contract.get("evidence", {})
    if not all(evidence.get(k) for k in ("source_locator", "parser_version")) or not (evidence.get("content_sha256") or evidence.get("content_sha256_reference") == "records.raw_id -> raw_blobs.sha256"):
        errors.append("missing_source_evidence")
    if data.get("extraction_method") == "narrative_evidence_candidate" and contract.get("stage") != "candidate":
        errors.append("candidate_must_not_be_counted_as_fact")
    return errors
