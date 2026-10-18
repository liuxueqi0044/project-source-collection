"""Officiality is a reviewed entity/domain assertion, not an arbitrary excerpt."""
from copy import deepcopy

import pytest

from equity_data.store import Store
from equity_data.competition.cli import import_bundle
from equity_data.competition.entities import put_entity
from equity_data.competition.domain import latest_record


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path) as result:
        yield result


def ref(store, *, host="issuer.example", entity="entity:issuer"):
    raw=store.put_raw(b"<h1>Issuer Newsroom</h1><p>Issuer owns its official newsroom.</p>",source="synthetic_official",url="https://"+host+"/about",media_type="text/html")
    return {**{key:raw[key] for key in ("raw_id","sha256","retrieval_id","url")},"locator":"css:h1","excerpt":"Issuer Newsroom","entity_id":entity}


def endpoint(evidence):
    return {"endpoint_id":"ep:issuer","entity_id":"entity:issuer","url":"https://news.issuer.example/releases","allowed_domains":["issuer.example"],"official_status":"verified_official","evidence_refs":[evidence]}


def apply(store, value):
    result=import_bundle(store,{"reviewer":"agent_evidence_review","endpoints":[value]}, {"http":{},"competition":{"budget":{"max_requests":0,"max_bytes":10000,"max_runtime_seconds":10}}})
    assert result["network_budget"]["requests"]==0
    return result,latest_record(store,"watch_endpoints",value["endpoint_id"])


def test_arbitrary_excerpt_does_not_verify_officiality(store):
    proof=ref(store,host="unrelated.example")
    put_entity(store,{"entity_id":"entity:issuer","name":"Issuer"})
    result,row=apply(store,endpoint(proof))
    assert not result["failures"]
    assert row["data"]["official_status"]=="needs_review"
    assert row["data"]["requested_official_status"]=="verified_official"
    assert row["data"]["authority_validation"]["reasons"]


def test_verified_website_must_attribute_exact_entity_and_cover_domains(store):
    proof=ref(store)
    put_entity(store,{"entity_id":"entity:issuer","name":"Issuer","websites":[{"url":"https://issuer.example/","verification_status":"verified","evidence_refs":[proof]}]})
    _,row=apply(store,endpoint(proof))
    assert row["data"]["official_status"]=="verified_official"
    bad=endpoint(proof);bad["allowed_domains"].append("unrelated.example")
    _,row=apply(store,bad)
    assert row["data"]["official_status"]=="needs_review"


def test_same_domain_without_subject_evidence_is_unverified(store):
    proof=ref(store,entity="entity:other")
    put_entity(store,{"entity_id":"entity:issuer","name":"Issuer","websites":[{"url":"https://issuer.example/","verification_status":"verified","evidence_refs":[proof]}]})
    _,row=apply(store,endpoint(proof))
    assert row["data"]["official_status"]=="needs_review"


def reviewed_endpoint(store):
    proof=ref(store)
    value=endpoint(proof)
    value["authority_review"]={"entity_id":"entity:issuer","method":"agent_evidence_review","reviewer":"agent:evidence-inspection","domains":["issuer.example"],"basis":"Agent inspected the official newsroom identity wording and domain; not a legal ownership audit.","identity_phrase":"Issuer","evidence_refs":[proof]}
    put_entity(store,{"entity_id":"entity:issuer","name":"Issuer"})
    return value


def test_explicit_agent_ownership_review_remains_attributed_not_human(store):
    value=reviewed_endpoint(store)
    _,row=apply(store,value)
    assert row["data"]["official_status"]=="verified_official"
    proof=row["data"]["authority_validation"]["proof"]
    assert proof["method"]=="agent_evidence_review" and proof["human_review_claimed"] is False
    assert row["knowledge_type"]=="system_inference"


@pytest.mark.parametrize("change",[{"entity_id":"entity:other"},{"domains":["other.example"]},{"basis":"official"},{"method":"model_confidence"},{"identity_phrase":"Some different company"},{"method":"human_review","reviewer":"agent:pretend-human"}])
def test_incomplete_or_conflicting_review_downgraded(store,change):
    value=reviewed_endpoint(store);value["authority_review"].update(change)
    _,row=apply(store,value)
    assert row["data"]["official_status"]=="needs_review"


def test_third_party_mention_cannot_establish_official_host(store):
    value=reviewed_endpoint(store)
    value["authority_review"]["evidence_refs"]=[ref(store,host="other.example")]
    _,row=apply(store,value)
    assert row["data"]["official_status"]=="needs_review"
    assert "Third-party" in row["data"]["authority_validation"]["reasons"][0]


def test_repeat_authority_review_is_idempotent(store):
    value=reviewed_endpoint(store)
    _,first=apply(store,value)
    _,second=apply(store,deepcopy(value))
    assert first["record_id"]==second["record_id"]
