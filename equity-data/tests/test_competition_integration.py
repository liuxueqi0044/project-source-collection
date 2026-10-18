"""Synthetic control/evidence tests; not real-world semantic recall evidence."""
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from equity_data.store import Store
from equity_data.competition import jobs
from equity_data.competition.entities import put_entity
from equity_data.competition.relations import put_relation
from equity_data.competition.domain import put_derived_record,load_record
from equity_data.competition.runner import enqueue_analysis,process_analysis,freeze_settings,run_competition
from equity_data.competition.queries import query_competition
from equity_data.competition.cli import read_store
from equity_data.adapters.competition_source import CompetitionWebAdapter,endpoint_resource
from equity_data.local_control import base_payload,scope_key,collector_lock,Control
from equity_data.local import active_config,plan_competition_analysis

@pytest.fixture
def sample(tmp_path):
    store=Store(tmp_path)
    refraw=store.put_raw(b'<p>Alpha and Beta compete in European batteries for utilities.</p>',source='synthetic',url='https://sample.invalid/relation',media_type='text/html')
    ref={k:refraw[k] for k in ('raw_id','sha256','retrieval_id','url')};ref.update(locator='document:text',excerpt='Alpha and Beta compete in European batteries for utilities.')
    for identity in ('alpha','beta'):put_entity(store,{'entity_id':'entity:'+identity,'name':identity,'role':'target' if identity=='alpha' else 'peer','identity_status':'verified','evidence_refs':[ref]})
    edge=put_relation(store,{'edge_id':'edge:a-b','target_entity_id':'entity:alpha','peer_entity_id':'entity:beta',
        'product_or_business':'batteries','geography':'Europe','customer_segment':'utilities','valid_from':'2020-01-01',
        'evidence_status':'source_explicit','review_status':'confirmed','overlap_basis':'Synthetic explicit claim','evidence_refs':[ref]},reviewer='synthetic_operator')
    endpoint={'endpoint_id':'endpoint:beta','entity_id':'entity:beta','url':'https://sample.invalid/news','allowed_domains':['sample.invalid'],
        'official_status':'verified_official','entity_verified':True,'evidence_refs':[ref],
        'format':'html','event_rules':[{'event_type':'commercial_agreement','pattern':r'(?P<action>signed) contract (?P<contract_id>[A-Z]-\d+) for (?P<object>[^,]+), business (?P<business_scope>[^,]+), region (?P<geography>[^,]+), customers (?P<customer_segment>[^,.]+)'}]}
    ep=put_derived_record(store,'watch_endpoints',endpoint['endpoint_id'],endpoint,reviewer='synthetic_operator')
    endpoint.update(endpoint_version_id=ep,config_hash='one',cadence='core3daily')
    config={'storage':str(tmp_path),'market':'US','start':'2024-01-01','end':'2026-10-10','sources':[],'data_types':[],
        'universe':{'symbols':['AAA']},'competition':{'enabled':True,'target_ids':['entity:alpha']}}
    plan={'plan_id':'test-plan','endpoints':[endpoint],'all_endpoints':[endpoint],'edge_versions':[load_record(store,edge)],'gaps':[],'excluded':[]}
    yield store,endpoint,config,plan
    store.close()

def add_snapshot(store,endpoint,text):
    raw=store.put_raw(text.encode(),source='competition_web',url=endpoint['url'],media_type='text/html')
    ctx=SimpleNamespace(store=store,emit=lambda r:None,gap=lambda *a,**k:None)
    return CompetitionWebAdapter().parse(ctx,endpoint_resource(endpoint,'test-plan'),text.encode(),raw)

def test_end_to_end_snapshot_analysis_link_evidence_and_idempotent_replay(sample):
    store,endpoint,config,plan=sample
    add_snapshot(store,endpoint,'<main><time datetime="2026-10-10T10:00:00Z"></time><p>Beta signed contract C-7 for storage, business batteries, region Europe, customers utilities.</p></main>')
    ids=enqueue_analysis(store,plan);result=process_analysis(store)
    assert len(result['processed'])==1 and not result['processed'][0]['persistence_rejections']
    assert len(store.query('competitive_events'))==1
    assert len(store.query('competitive_event_links'))==1
    before=store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    assert enqueue_analysis(store,plan)==ids
    assert process_analysis(store)['processed']==[]
    assert store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==before
    bundle=query_competition(store,'entity:alpha')
    assert bundle['events'] and bundle['evidence_refs']
    assert not query_competition(store,'entity:alpha',known_at='2020-01-01')['events']

def test_analysis_failure_does_not_fetch_and_is_bounded(sample):
    store,endpoint,config,plan=sample
    identity=jobs.enqueue(store,{'snapshot_version_id':'missing','endpoint':endpoint,'edge_versions':[]})
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('No network in analysis')):
        for _ in range(3):
            process_analysis(store)
            with store.db:store.db.execute('UPDATE competition_jobs SET next_attempt_at=NULL WHERE job_id=?',(identity,))
    row=store.db.execute('SELECT * FROM competition_jobs WHERE job_id=?',(identity,)).fetchone()
    assert row['state']=='exhausted' and row['attempts']==3
    assert jobs.enqueue(store,{'snapshot_version_id':'missing','endpoint':endpoint,'edge_versions':[]})==identity
    assert process_analysis(store)['processed']==[]
    assert store.db.execute('SELECT COUNT(*) FROM competition_job_history WHERE job_id=?',(identity,)).fetchone()[0]>=7

def test_analysis_owner_recovery_retains_attempt_history(sample):
    store,_,_,_=sample
    identity=jobs.enqueue(store,{'snapshot_version_id':'missing'})
    jobs.claim(store);assert jobs.recover(store)==[identity]
    assert jobs.claim(store)['attempts']==2
    jobs.recover(store);assert jobs.claim(store)['attempts']==3
    jobs.recover(store)
    assert jobs.status(store)['jobs']=={'exhausted':1}

def test_disabled_module_does_not_change_request_identity_or_create_job_tables(tmp_path):
    config={'storage':str(tmp_path),'universe':{'symbols':['B','A']},'sources':['sec_edgar'],'data_types':['documents']}
    baseline=base_payload(config)
    assert 'competition_frozen' not in baseline
    config['competition']={'enabled':False,'target_ids':['anything']}
    assert base_payload(config)==baseline
    with Store(tmp_path) as store:
        # Direct optional call is guarded, and never accesses the network.
        assert run_competition(store,config)=={'enabled':False}

def test_frozen_scope_is_clock_stable_and_relationship_version_sensitive(sample):
    store,endpoint,config,plan=sample
    one=base_payload(config);two=base_payload(config)
    assert scope_key(one)==scope_key(two)
    frozen=one['competition_frozen']['plan']
    assert frozen and frozen['all_endpoints']
    edge=plan['edge_versions'][0]['data'];edge['geography']='unknown'
    put_relation(store,edge,reviewer='synthetic_changed_review')
    three=base_payload(config)
    assert scope_key(one)!=scope_key(three)
    assert one['competition_frozen']==two['competition_frozen']
    assert active_config(config,one)['_competition_frozen']==frozen

def test_old_request_does_not_silently_gain_new_competition_scope(sample):
    _,_,config,_=sample
    disabled={**config,'competition':{'enabled':False}}
    old=base_payload(disabled)
    assert active_config(config,old)['competition']['enabled'] is False

def test_readonly_query_does_not_write_schema_or_data(sample):
    store,_,config,_=sample
    with read_store(config) as readonly:
        assert readonly.query('entities')
        with pytest.raises(Exception):readonly.db.execute('CREATE TABLE should_not_exist(x)')
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='should_not_exist'").fetchone()

def test_analysis_pulse_queues_without_network_and_respects_shared_lock(sample):
    store,_,config,_=sample
    jobs.enqueue(store,{'snapshot_version_id':'missing'})
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Pulse must not fetch')):
        result=plan_competition_analysis(config)
        assert result['status']=='queued'
        assert plan_competition_analysis(config)['request_id']==result['request_id']
        with collector_lock(config['storage']) as owned:
            assert owned
            assert plan_competition_analysis(config)['status']=='owner_active'
    control=Control(config['storage'])
    try:
        payload=json.loads(control.get(result['request_id'])['payload_json'])
        assert payload['competition_only'] and payload['analysis_only']
    finally:control.close()

def test_exhausted_parent_budget_retains_due_work_without_network(sample):
    store,_,config,plan=sample
    config['budget']={'max_requests':1,'max_bytes':1000,'max_runtime_seconds':1}
    with patch('equity_data.machine.execute',side_effect=AssertionError('No remaining budget')):
        result=run_competition(store,config,frozen=plan,used_budget={'requests':1,'bytes':1000,'elapsed_seconds':1})
    assert result['budget_gap'] and result['due_endpoints']==1
    assert not jobs.status(store)['endpoints']

def test_http_200_geographic_interstitial_is_not_business_snapshot():
    from equity_data.competition.change_detector import normalize_snapshot,compare_snapshots
    before=normalize_snapshot(b'<main>Available products and prices.</main>',{})
    blocked=normalize_snapshot(b"<html><body>The U.S. website is only available in the United States. It cannot be accessed from your current location. CLOUDFLARE_ERROR_1000S_BOX</body></html>",{})
    assert not blocked['valid']
    assert compare_snapshots(before,blocked,{})['status']=='rejected'

def test_script_only_refresh_retains_raw_versions_but_not_new_event_versions(sample):
    store,endpoint,_,plan=sample
    body='<main>Beta signed contract C-7 for storage, business batteries, region Europe, customers utilities.</main>'
    add_snapshot(store,endpoint,body+'<script>tracking=1</script>');enqueue_analysis(store,plan);process_analysis(store)
    before=store.db.execute("SELECT COUNT(*) FROM records WHERE dataset='competitive_events'").fetchone()[0]
    for number in (2,3):
        add_snapshot(store,endpoint,body+f'<script>tracking={number}</script>');enqueue_analysis(store,plan);result=process_analysis(store)
        assert result['processed'][0]['reused_event_versions']==1
    assert store.db.execute("SELECT COUNT(*) FROM records WHERE dataset='competition_snapshots'").fetchone()[0]==3
    assert store.db.execute("SELECT COUNT(*) FROM records WHERE dataset='competitive_events'").fetchone()[0]==before

def test_publication_metadata_change_is_not_suppressed_as_script_noise():
    from equity_data.competition.change_detector import normalize_snapshot,compare_snapshots
    html='<meta property="article:published_time" content="{}"><main>Unchanged announcement.</main>'
    a=normalize_snapshot(html.format('2026-10-01T10:00:00Z').encode(),{})
    b=normalize_snapshot(html.format('2026-10-02T10:00:00Z').encode(),{})
    result=compare_snapshots(a,b,{})
    assert result['status']=='changed' and result['publication_changed']
