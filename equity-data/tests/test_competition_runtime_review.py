"""Independent V5 runtime review: real queues/SQLite, synthetic transport only."""
import copy
import json
import socket
from unittest.mock import patch

from equity_data.cli import RunHttpClient
from equity_data.competition.runner import run_competition
from equity_data.store import Store


def sample(root):
    ep={'endpoint_id':'endpoint:unlisted','entity_id':'entity:unlisted','url':'https://runtime.example.test/rss',
        'allowed_domains':['runtime.example.test'],'official_status':'verified_official','evidence_refs':[],
        'channel':'rss','endpoint_version_id':'synthetic-endpoint-version','config_hash':'synthetic-config','cadence':'core3daily'}
    plan={'plan_id':'fixed-review-plan','all_endpoints':[ep],'endpoints':[ep],'edge_versions':[],'gaps':[],'excluded':[]}
    config={'storage':str(root),'market':'US','start':'2024-01-01','end':'2026-10-10','securities':[],
            'sources':['competition_web'],'data_types':['competition_snapshots'],
            'competition':{'enabled':True,'max_analysis_jobs':0,'budget':{'max_tasks':1,'max_requests':3,'max_bytes':10000,'max_runtime_seconds':30}},
            'scheduler':{'retry_seconds':0,'max_task_attempts_per_run':1}}
    return config,plan


def transport(calls):
    class SyntheticClient(RunHttpClient):
        def fetch(self,url,*,source,**kwargs):
            bodies={'https://runtime.example.test/rss':b'<rss><channel><title>Public news</title><item><title>New product</title><description>Public release.</description><link>https://runtime.example.test/release</link></item></channel></rss>',
                    'https://runtime.example.test/release':b'<main><p>A product announcement without invented financial values.</p></main>'}
            body=bodies[url]
            if self.budget:
                self.budget.before_request(source);self.budget.consume_bytes(source,len(body))
            calls.append(url)
            return body,self.store.put_raw(body,source=source,url=url)
    return SyntheticClient


def dns(*args,**kwargs):
    return [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('8.8.8.8',443))]


def test_one_task_budget_progresses_after_database_reopen_without_rediscovery(tmp_path):
    config,plan=sample(tmp_path);calls=[];results=[]
    with patch('equity_data.cli.RunHttpClient',transport(calls)),patch('socket.getaddrinfo',side_effect=dns):
        # Close and reopen between every batch, as separate local activations do.
        for _ in range(3):
            with Store(tmp_path) as store:
                results.append(run_competition(store,config,frozen=copy.deepcopy(plan),force=True))
        with Store(tmp_path) as store:
            assert store.db.execute('SELECT count(*) FROM machine_tasks').fetchone()[0]==3
            assert store.db.execute("SELECT count(*) FROM machine_attempts a JOIN machine_tasks t USING(task_id) WHERE t.task_kind='discover'").fetchone()[0]==1
            assert store.db.execute('SELECT state FROM competition_cycles').fetchone()[0]=='complete'
            assert store.db.execute('SELECT last_success_at FROM competition_endpoint_state').fetchone()[0]
            assert len(store.query('competition_snapshots'))==2
    assert calls==['https://runtime.example.test/rss','https://runtime.example.test/release']
    assert [r['acquisition']['competition_cycle']['resumed_without_rediscovery'] for r in results]==[False,True,True]
    assert results[-1]['endpoint_receipts'][0]['scope_check_complete']
    assert all(r['endpoint_receipts'][0]['consecutive_failures']==0 for r in results)


def test_cycle_survives_interruption_after_acquisition_before_receipt(tmp_path):
    import equity_data.machine
    config,plan=sample(tmp_path);calls=[]
    original=equity_data.machine.execute
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('synthetic process termination before endpoint receipt')
    with patch('equity_data.cli.RunHttpClient',transport(calls)),patch('socket.getaddrinfo',side_effect=dns):
        with Store(tmp_path) as store,patch('equity_data.machine.execute',side_effect=interrupted):
            try:run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
            except SystemExit:pass
            else:raise AssertionError('Interruption was not injected')
        for _ in range(2):
            with Store(tmp_path) as store:
                result=run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
        with Store(tmp_path) as store:
            assert store.db.execute('SELECT state FROM competition_cycles').fetchone()[0]=='complete'
            assert store.db.execute("SELECT count(*) FROM machine_attempts a JOIN machine_tasks t USING(task_id) WHERE t.task_kind='discover'").fetchone()[0]==1
    assert result['endpoint_receipts'][0]['scope_check_complete']
    assert len(calls)==2

def test_new_feed_cycle_does_not_require_refreshing_disappeared_old_links(tmp_path):
    config,plan=sample(tmp_path);config['competition']['budget']['max_tasks']=10
    calls=[];selected={'name':'release'}
    class ChangingClient(RunHttpClient):
        def fetch(self,url,*,source,**kwargs):
            if url.endswith('/rss'):
                body=('<rss><channel><title>Public news</title><item><title>Product news</title><description>Public release.</description><link>https://runtime.example.test/'+selected['name']+'</link></item></channel></rss>').encode()
            else:body=b'<main><p>Company public product statement.</p></main>'
            self.budget.before_request(source);self.budget.consume_bytes(source,len(body));calls.append(url)
            return body,self.store.put_raw(body,source=source,url=url)
    with Store(tmp_path) as store,patch('equity_data.cli.RunHttpClient',ChangingClient),patch('socket.getaddrinfo',side_effect=dns):
        one=run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
        assert one['endpoint_receipts'][0]['scope_check_complete']
        selected['name']='other'
        two=run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
        assert two['endpoint_receipts'][0]['scope_check_complete']
        assert two['endpoint_receipts'][0]['enumerated_resources']==2
    assert calls==['https://runtime.example.test/rss','https://runtime.example.test/release','https://runtime.example.test/rss','https://runtime.example.test/other']

def test_exhausted_peer_cannot_prevent_healthy_endpoint_next_check(tmp_path):
    config,plan=sample(tmp_path);config['competition']['budget']['max_tasks']=10
    config['competition']['max_endpoint_failures']=1
    config['scheduler']['max_resource_attempts']=1
    good=plan['endpoints'][0];good.update(channel='web',url='https://runtime.example.test/good')
    bad={**good,'endpoint_id':'endpoint:bad','entity_id':'entity:bad','endpoint_version_id':'bad-version','url':'https://runtime.example.test/bad'}
    plan['endpoints'].append(bad);plan['all_endpoints']=[good,bad]
    calls=[]
    class PartlyFailingClient(RunHttpClient):
        def fetch(self,url,*,source,**kwargs):
            self.budget.before_request(source);calls.append(url)
            if url.endswith('/bad'):raise RuntimeError('HTTP 403 permanent permission failure')
            body=b'<main><p>Company public statement.</p></main>';self.budget.consume_bytes(source,len(body))
            return body,self.store.put_raw(body,source=source,url=url)
    with Store(tmp_path) as store,patch('equity_data.cli.RunHttpClient',PartlyFailingClient),patch('socket.getaddrinfo',side_effect=dns):
        one=run_competition(store,config,frozen=copy.deepcopy(plan),force=True)
        assert any(r['scope_check_complete'] for r in one['endpoint_receipts'])
        with store.db:store.db.execute("UPDATE competition_endpoint_state SET next_due_at='2020-01-01T00:00:00Z' WHERE endpoint_id=?",(good['endpoint_id'],))
        two=run_competition(store,config,frozen=copy.deepcopy(plan))
    assert calls.count(good['url'])==2
    assert calls.count(bad['url'])==1
    assert any(r['endpoint_id']==good['endpoint_id'] and r['scope_check_complete'] for r in two['endpoint_receipts'])

def test_removed_endpoint_failure_does_not_schedule_new_collection(tmp_path):
    from equity_data.competition.jobs import ensure_schema
    from equity_data.local import plan_competition_analysis
    config,_=sample(tmp_path)
    with Store(tmp_path) as store:
        ensure_schema(store)
        with store.db:
            store.db.execute("INSERT INTO competition_endpoint_state(endpoint_id,next_due_at,last_error,consecutive_failures) VALUES('old:removed','2020-01-01T00:00:00Z','HTTP 403',1)")
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('No network in scheduler pulse')):
        result=plan_competition_analysis(config)
    assert result['status']=='no_ready_analysis_or_gated_retry'
    with Store(tmp_path) as store:
        assert store.db.execute("SELECT last_error FROM competition_endpoint_state WHERE endpoint_id='old:removed'").fetchone()[0]=='HTTP 403'


def run_local_competition_request(config, plan, *, analysis_only=False):
    """Exercise the actual local dispatcher without financial or external I/O."""
    from equity_data.local import run_request
    from equity_data.local_control import Control, base_payload
    from pathlib import Path
    payload=base_payload({**config,'competition':{'enabled':False}})
    payload.update(competition_only=True,analysis_only=analysis_only,
                   competition_frozen={'settings':copy.deepcopy(config['competition']),'plan':copy.deepcopy(plan)})
    control=Control(config['storage'])
    try:
        control.enqueue(payload,'unfinished_work_resume')
        result=run_request(control,config,control.claim())
    finally:control.close()
    assert result['state']!='failed',Path(result['result_path']).read_text(encoding='utf-8')
    return result,json.loads(Path(result['result_path']).read_text(encoding='utf-8'))


def test_local_competition_budget_pending_is_not_task_complete(tmp_path):
    config,plan=sample(tmp_path);calls=[]
    # The financial data types in the parent configuration must not initialize
    # or collect finance as a side effect of a competition-only continuation.
    config.update(sources=['sec_edgar'],data_types=['financial_facts'])
    with patch('equity_data.cli.RunHttpClient',transport(calls)),patch('socket.getaddrinfo',side_effect=dns):
        result,report=run_local_competition_request(config,plan)
    assert report['competition']['acquisition']['remaining_tasks'].get('pending',0)>0
    assert not report['task_execution_complete']
    assert not report['parsing_complete']
    assert result['state']=='partial'
    assert not report['coverage_complete']


def test_analysis_only_never_claims_a_source_check(tmp_path):
    config,plan=sample(tmp_path)
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Analysis cannot fetch')):
        _,report=run_local_competition_request(config,plan,analysis_only=True)
    assert report['competition']['acquisition'] is None
    assert not report['source_checks_complete']
    assert report['required_freshness_satisfied'] is None


def test_exhausted_analysis_only_request_is_not_completed(tmp_path):
    from equity_data.competition import jobs
    config,plan=sample(tmp_path)
    with Store(tmp_path) as store:
        identity=jobs.enqueue(store,{'snapshot_version_id':'missing'})
        with store.db:store.db.execute("UPDATE competition_jobs SET state='exhausted',attempts=3 WHERE job_id=?",(identity,))
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Analysis cannot fetch')):
        result,report=run_local_competition_request(config,plan,analysis_only=True)
    assert report['competition']['analysis']['queue'].get('exhausted')==1
    assert not report['task_execution_complete']
    assert result['state']!='completed'


def test_competition_continuation_preserves_latest_financial_report(tmp_path):
    config,plan=sample(tmp_path)
    financial={'request_id':'prior-financial-check','requested':{'data_types':['financial_facts']},'source_checks_complete':True}
    saved=tmp_path/'latest-decision-report.json';saved.write_text(json.dumps(financial),encoding='utf-8')
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Analysis cannot fetch')):
        run_local_competition_request(config,plan,analysis_only=True)
    assert json.loads(saved.read_text(encoding='utf-8'))==financial


def test_competition_report_markdown_matches_final_machine_states(tmp_path):
    from pathlib import Path
    config,plan=sample(tmp_path)
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Analysis cannot fetch')):
        result,report=run_local_competition_request(config,plan,analysis_only=True)
    markdown=Path(result['result_path']).with_suffix('.md').read_text(encoding='utf-8')
    assert f"任务执行完成：{report['task_execution_complete']}" in markdown
    assert f"本轮来源检查：{report['source_checks_complete']}" in markdown
    assert f"分析处理完成：{report['analysis_processing_complete']}" in markdown
    assert report['information_scope']=='competition_only'
    pointer=json.loads((tmp_path/'latest-competition-report.json').read_text(encoding='utf-8'))
    assert pointer['result_path']==result['result_path']


def test_not_due_failed_endpoint_is_not_a_new_source_check(tmp_path):
    from equity_data.competition import jobs
    config,plan=sample(tmp_path);endpoint=plan['endpoints'][0]
    with Store(tmp_path) as store:
        jobs.ensure_schema(store)
        with store.db:
            store.db.execute('''INSERT INTO competition_endpoint_state(endpoint_id,config_hash,next_due_at,last_error,consecutive_failures)
                VALUES(?,?,'9999-12-31T00:00:00Z','HTTP 403',5)''',(endpoint['endpoint_id'],endpoint['config_hash']))
    with patch('equity_data.http.HttpClient.fetch',side_effect=AssertionError('Not-due failure must not fetch')):
        _,report=run_local_competition_request(config,plan)
    assert report['competition']['due_endpoints']==0
    assert report['competition']['acquisition'] is None
    assert report['source_checks_complete'] is None
    assert report['required_freshness_satisfied'] is None
    assert report['coverage_complete'] is False
    with Store(tmp_path) as store:
        state=store.db.execute('SELECT last_error,last_success_at,consecutive_failures FROM competition_endpoint_state').fetchone()
        assert state['last_error']=='HTTP 403' and state['last_success_at'] is None and state['consecutive_failures']==5


def test_competition_report_cannot_match_financial_refresh_scope(tmp_path):
    from equity_data.local_control import base_payload,utcnow
    from equity_data.local_status import reassess_refresh
    config,_=sample(tmp_path)
    config.update(sources=['sec_edgar'],data_types=['financial_facts'])
    payload=base_payload(config)
    payload['competition_only']=True
    report={'requested':payload,'current_scope_checks':[]}
    assessed=reassess_refresh(config,report,{},utcnow())
    assert not assessed['scope_matches_current_configuration']
    assert not assessed['required_freshness_satisfied']
