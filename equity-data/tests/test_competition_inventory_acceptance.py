"""Independent inventory reference and no-network continuation acceptance."""
import copy
import socket
from unittest.mock import patch

from equity_data.cli import RunHttpClient
from equity_data.competition.runner import run_competition
from equity_data.local import plan_competition_analysis
from equity_data.local_control import Control
from equity_data.store import Store
from scripts.validate_competition_inventory import reference_links


def test_reference_inventory_has_independent_dedup_cap_and_pagination_denominator():
    endpoint={'url':'https://example.test/news','allowed_domains':['example.test'],
              'link_selector':'a.story','max_depth':1,'max_documents':1}
    content=b'''<a class="story" href="/a">A</a><a class="story" href="/a#top">duplicate</a>
    <a class="story" href="/b">B</a><a class="story" href="https://elsewhere.test/c">outside</a>
    <a rel="next" href="?page=2">Next</a>'''
    result=reference_links(content,endpoint)
    assert result['selector_matches']==4 and result['duplicate_or_self_links']==1
    assert [row['url'] for row in result['all_unique_allowed_links']]==['https://example.test/a','https://example.test/b']
    assert len(result['admitted_links'])==1 and len(result['cap_omitted_links'])==1
    assert result['excluded_links'][0]['reason']=='outside_configured_domains'
    assert result['pagination_signals'] and result['pagination_complete'] is None


def test_checked_raw_with_depth_gap_does_not_trigger_five_minute_network_retry(tmp_path):
    endpoint={'endpoint_id':'ep:review','entity_id':'entity:unlisted','url':'https://example.test/news',
              'allowed_domains':['example.test'],'official_status':'verified_official','evidence_refs':[],
              'channel':'web','enumerate_links':True,'link_selector':'a.story','selectors':{'body':'main'},
              'max_depth':1,'max_documents':12,'endpoint_version_id':'test-version','config_hash':'test-config','cadence':'core3daily'}
    plan={'plan_id':'fixed-reference-plan','all_endpoints':[endpoint],'endpoints':[endpoint],'edge_versions':[],'gaps':[],'excluded':[]}
    config={'storage':str(tmp_path),'market':'US','start':'2024-01-01','end':'2026-10-10','securities':[],
            'sources':['sec_edgar'],'data_types':['financial_facts'],
            'competition':{'enabled':True,'max_analysis_jobs':100,'budget':{'max_tasks':10,'max_requests':5,'max_bytes':10000,'max_runtime_seconds':30}},
            'scheduler':{'retry_seconds':0,'max_task_attempts_per_run':1}}
    calls=[]
    class SyntheticClient(RunHttpClient):
        def fetch(self,url,*,source,**kwargs):
            self.budget.before_request(source)
            target='/a' if url.endswith('/news') else '/b'
            body=('<main><p>Archive evidence is available.</p><a class="story" href="'+target+'">Archive</a></main>').encode()
            self.budget.consume_bytes(source,len(body));calls.append(url)
            return body,self.store.put_raw(body,source=source,url=url)
    with patch('equity_data.cli.RunHttpClient',SyntheticClient),patch('socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('8.8.8.8',443))]):
        with Store(tmp_path) as store:
            result=run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
            receipt=result['endpoint_receipts'][0]
            assert receipt['resources_checked_this_round']==2
            assert not receipt['scope_check_complete'] and not receipt['needs_network_retry']
            assert receipt['consecutive_failures']==0
            assert result['analysis']['queue']=={'completed':2}
            state=store.db.execute('SELECT * FROM competition_endpoint_state').fetchone()
            assert state['last_error'].startswith('Normalization')
            # Even after the cadence timestamp is old, the 5-minute continuation
            # pulse must not turn a parse/depth gap into an extra network run.
            with store.db:store.db.execute("UPDATE competition_endpoint_state SET next_due_at='2020-01-01T00:00:00Z'")
    with patch('equity_data.competition.runner.freeze_settings',return_value={'settings':config['competition'],'plan':plan}),patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Pulse must not fetch')):
        for _ in range(3):
            assert plan_competition_analysis(config)['status']=='no_ready_analysis_or_gated_retry'
    assert calls==['https://example.test/news','https://example.test/a']
    control=Control(tmp_path)
    try:assert control.db.execute('SELECT count(*) FROM local_requests').fetchone()[0]==0
    finally:control.close()
    with Store(tmp_path) as store:
        assert store.db.execute("SELECT count(*) FROM raw_retrievals WHERE source='competition_web'").fetchone()[0]==2
        assert store.db.execute("SELECT count(*) FROM machine_gaps WHERE code='COMPETITION_ENUMERATION_LIMIT' AND resolved_at IS NULL").fetchone()[0]>0
