"""Explicit review is source-grounded and never automatic extraction credit."""
from copy import deepcopy

import pytest

from equity_data.store import Store
from equity_data.competition.domain import load_record, put_derived_record
from equity_data.competition.entities import put_entity
from equity_data.competition.events_review import review_event
from equity_data.competition.evidence import EvidenceError


@pytest.fixture
def setup(tmp_path):
    with Store(tmp_path) as store:
        put_entity(store,{"entity_id":"entity:actor","name":"Actor"})
        raw=store.put_raw(b"<p>Actor plans to launch coffee drinks for retail consumers in France on November 4, 2026.</p>",source="synthetic_official",url="https://actor.example/news/plan",media_type="text/html")
        evidence={**{k:raw[k] for k in ("raw_id","sha256","url","retrieval_id")},"locator":"document:text","excerpt":"Actor plans to launch coffee drinks for retail consumers in France on November 4, 2026.","entity_id":"entity:actor"}
        event=put_derived_record(store,"competitive_events","event:plan",{"event_id":"event:plan","actor_entity_id":"entity:actor","event_type":"product_change","action":"launch","object":"coffee drinks","status":"planned","fact_stage":"candidate","business_scope":"unknown","geography":"unknown","customer_segment":"unknown","evidence_refs":[evidence]})
        request={"event_id":"event:plan","fields":{"business_scope":"coffee drinks","geography":"France","customer_segment":"retail consumers","event_time":"2026-11-04","event_time_precision":"day"},"fact_evidence_refs":[evidence],"scope_evidence":{key:{"source_phrase":value,"evidence_refs":[evidence]} for key,value in [("business_scope","coffee drinks"),("geography","France"),("customer_segment","retail consumers")]},"field_evidence":{"event_time":[evidence]},"reviewer":"agent:test-review","method":"agent_evidence_review","reason":"The source explicitly describes a planned launch in this product and region."}
        yield store,event,evidence,request


def test_agent_review_persists_separate_fact_and_scope_evidence(setup):
    store,old,_,request=setup
    new=review_event(store,**request);row=load_record(store,new)
    assert row["supersedes"]==old
    assert row["data"]["fact_stage"]=="extracted_fact"
    assert row["data"]["automatic_extraction_success"] is False
    assert row["data"]["review"]["counts_as_automatic_extraction"] is False
    assert row["data"]["review"]["human_review_claimed"] is False
    assert row["data"]["status"]=="planned"
    assert len(row["data"]["reviewed_scope"] )==3
    assert len(store.query('review_actions'))==1


def test_repeat_review_is_idempotent_and_stale_version_rejected(setup):
    store,old,_,request=setup
    new=review_event(store,**request)
    counts=tuple(store.db.execute('select (select count(*) from records),(select count(*) from raw_retrievals)').fetchone())
    assert review_event(store,**deepcopy(request))==new
    assert tuple(store.db.execute('select (select count(*) from records),(select count(*) from raw_retrievals)').fetchone())==counts
    with pytest.raises(ValueError,match='stale'):
        review_event(store,**{**request,'event_id':old})


def test_future_plan_is_not_completed(setup):
    store,_,_,request=setup
    request['fields']['status']='completed'
    with pytest.raises(EvidenceError,match='stage'):
        review_event(store,**request)


def test_scope_requires_separate_literal_evidence(setup):
    store,_,_,request=setup
    request['scope_evidence'].pop('geography')
    with pytest.raises(EvidenceError,match='scope_evidence'):
        review_event(store,**request)


def test_scope_mapping_is_explicit_and_cannot_expand_to_global(setup):
    store,_,_,request=setup
    request['fields']['business_scope']='coffee beverages'
    request['scope_evidence']['business_scope']['mapping_reason']='Source coffee drinks are reviewed as coffee beverages for this specific product line.'
    key=review_event(store,**request)
    assert load_record(store,key)['data']['reviewed_scope']['business_scope']['method']=='reviewed_semantic_mapping'
    request['fields']['geography']='global'
    request['scope_evidence']['geography']['mapping_reason']='Reviewer guesses that all products must be available worldwide.'
    with pytest.raises(EvidenceError,match='global'):
        review_event(store,**request)


def test_profile_alone_cannot_supply_event_scope(setup):
    store,_,_,request=setup
    raw=store.put_raw(b'Actor has headquarters in France.',source='synthetic_profile',url='https://actor.example/about',media_type='text/plain')
    proof={**{k:raw[k] for k in ('raw_id','sha256','url','retrieval_id')},'locator':'document:text','excerpt':'Actor has headquarters in France.'}
    request['scope_evidence']['geography']['evidence_refs']=[proof]
    with pytest.raises(EvidenceError,match='event source'):
        review_event(store,**request)


@pytest.mark.parametrize('field',['profit_impact','estimated_target_revenue_loss','new_value','currency','unit','normalized_market_share'])
def test_review_does_not_accept_numeric_or_financial_impact_answers(setup,field):
    store,_,_,request=setup;request['fields'][field]='100'
    with pytest.raises(ValueError,match='Unsupported review fields'):
        review_event(store,**request)


def test_missing_or_fabricated_fact_evidence_rejected(setup):
    store,_,_,request=setup
    request['fact_evidence_refs'][0]['excerpt']='Actor completed a factory in France.'
    with pytest.raises(EvidenceError,match='absent'):
        review_event(store,**request)


def test_incomplete_date_cannot_borrow_publication_year(setup):
    store,_,_,request=setup
    request['fields']['event_time']='2027-11-04'
    with pytest.raises(EvidenceError,match='source date'):
        review_event(store,**request)


def test_agent_cannot_label_its_review_as_human(setup):
    store,_,_,request=setup;request['method']='human_review'
    with pytest.raises(ValueError,match='human:'):
        review_event(store,**request)


def test_rejection_preserves_prior_candidate_and_review_history(setup):
    store,old,_,request=setup
    request.update(decision='rejected',fields={},scope_evidence={})
    new=review_event(store,**request)
    assert load_record(store,new)['data']['fact_stage']=='rejected'
    assert load_record(store,old)['data']['fact_stage']=='candidate'


def test_review_refreshes_exact_event_and_edge_version_links_without_reacquisition(setup):
    from equity_data.competition.relations import put_relation
    from equity_data.competition.queries import query_competition
    store,_,ref,request=setup
    put_entity(store,{'entity_id':'entity:target','name':'Target'})
    edge=put_relation(store,{'edge_id':'edge:review-target','target_entity_id':'entity:target','peer_entity_id':'entity:actor','product_or_business':'coffee drinks','geography':'France','customer_segment':'retail consumers','overlap_basis':'Synthetic reviewer confirms only this local coffee product scope.','evidence_status':'source_explicit','review_status':'confirmed','valid_from':'2020-01-01','evidence_refs':[ref]},reviewer='agent:test-review')
    key=review_event(store,**request)
    links=store.query('competitive_event_links')
    assert len(links)==1 and links[0]['data']['event_version_id']==key
    assert links[0]['data']['edge_version_id']==edge
    assert links[0]['data']['review_status']=='matched'
    assert len(query_competition(store,'entity:target')['core_brief'])==1
    count=store.db.execute('select count(*) from records').fetchone()[0]
    assert review_event(store,**request)==key
    assert store.db.execute('select count(*) from records').fetchone()[0]==count
