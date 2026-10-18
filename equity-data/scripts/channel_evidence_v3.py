"""Report real channel evidence; absent independent denominators stay null."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]


def build(storages,out):
    registry=json.loads((ROOT/'configs/source_registry.json').read_text(encoding='utf-8'))['sources']
    evidence={r['source_id']:{'registration':r,'requests':[],'datasets':Counter(),'fact_stages':Counter(),'attempts':[],'failures':[],'scopes':[]} for r in registry}
    for storage in storages:
        root=Path(storage).resolve();db=sqlite3.connect((root/'data.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        for row in db.execute('SELECT r.*,b.sha256,b.path,b.byte_size FROM raw_retrievals r JOIN raw_blobs b USING(raw_id)'):
            sid=row['source']
            if sid not in evidence:continue
            evidence[sid]['requests'].append({'storage':str(root),'retrieval_id':row['retrieval_id'],'url':row['url'],
                'raw_id':row['raw_id'],'sha256':row['sha256'],'raw_path':str(root/row['path']),'bytes':row['byte_size'],
                'retrieved_at':row['retrieved_at'],'http_evidence':json.loads(row['metadata_json'])})
        for row in db.execute('SELECT source,dataset,data_json FROM records'):
            if row['source'] not in evidence:continue
            item=evidence[row['source']];item['datasets'][row['dataset']]+=1
            data=json.loads(row['data_json']);item['fact_stages'][data.get('fact_stage','not_labeled')]+=1
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'machine_attempts' in tables:
            for row in db.execute('SELECT t.source_id,a.* FROM machine_attempts a JOIN machine_tasks t USING(task_id)'):
                if row['source_id'] in evidence:
                    evidence[row['source_id']]['attempts'].append({'storage':str(root),**dict(row)})
            for row in db.execute('SELECT config_json FROM machine_runs'):
                conf=json.loads(row[0]);scope={'start':conf.get('start'),'end':conf.get('end'),'companies':[s.get('company_id') for s in conf.get('securities',[])]}
                for sid in conf.get('sources',[]):
                    if sid in evidence and scope not in evidence[sid]['scopes']:evidence[sid]['scopes'].append(scope)
        for row in db.execute('SELECT * FROM quality_issues'):
            if row['code']!='HTTP_ATTEMPT_FAILED':continue
            for sid,item in evidence.items():
                if row['message'].startswith(sid+':'):item['failures'].append({'storage':str(root),**dict(row)})
        db.close()
    rows=[]
    for sid,item in evidence.items():
        http=[r for r in item['requests'] if r['http_evidence'].get('status_code')==200]
        failures=[a for a in item['attempts'] if a['state'] not in {'succeeded','running'}]
        network_recoveries=[]
        for failure in item['failures']:
            details=json.loads(failure['details_json'])
            recovered=sorted((r for r in http if r['storage']==failure['storage'] and r['url']==details.get('url') and r['retrieved_at']>failure['created_at']), key=lambda r:r['retrieved_at'])
            if recovered:
                response=recovered[0]
                network_recoveries.append({'failure_issue_id':failure['issue_id'],'url':response['url'],
                    'failed_at':failure['created_at'],'error':failure['message'],'succeeded_at':response['retrieved_at'],
                    'retrieval_id':response['retrieval_id'],'raw_id':response['raw_id'],'http_evidence':response['http_evidence']})
        registration=item['registration']
        if http:
            status='partially_connected' # Connection ≠ complete semantic/reference coverage.
        elif sid=='exchange_calendars' and item['requests']:
            status='local_generated_calendar_verified_not_live_exchange_feed'
        elif any(a['state']=='failed' for a in item['attempts']):
            status='real_access_attempted_but_failed'
        elif any(a['state']=='blocked' for a in item['attempts']):
            status='missing_dependency'
        elif registration['status'] in {'missing_dependency','candidate'}:
            status='missing_dependency' if registration['status']=='missing_dependency' else 'candidate_only'
        else:status='interface_only_no_successful_live_response_in_this_run'
        rows.append({'source_id':sid,'actual_status':status,'registration_status':registration['status'],
                     'successful_http_responses':len(http),'raw_versions':len({r['raw_id'] for r in item['requests']}),
                     'record_version_counts':dict(item['datasets']),'fact_stage_counts':dict(item['fact_stages']),
                     'coverage_denominator':None,'coverage_rate':None,'coverage_note':'Use independent reference reports for bounded denominators; these are runtime counts.',
                     'failed_or_partial_attempts':len(failures),'task_failure_then_success_observed':any(a['state']=='succeeded' and any(b['task_id']==a['task_id'] and b['started_at']<a['started_at'] for b in failures) for a in item['attempts']),
                     'real_network_failure_recovery_observed':bool(network_recoveries),
                     'network_recovery_evidence':network_recoveries,
                     'recovery_note':'Task recovery may be parsing/cache recovery. Network evidence separately links a failed HTTP issue with a later HTTP 200 retrieval for the same source URL in the same storage; this does not establish new-content or long-term availability.',
                     **{k:item[k] for k in ['requests','attempts','failures','scopes']},
                     'limitations':registration.get('historical_coverage'),'dependencies':registration.get('authentication_authorization_cost')})
    target=Path(out);target.mkdir(parents=True,exist_ok=True)
    (target/'channel-evidence.json').write_text(json.dumps({'sources':rows,'note':'Actual evidence from named V3 databases only; simulated tests reported separately.'},ensure_ascii=False,indent=2),encoding='utf-8')
    md=['# V3 逐渠道实际能力','', '请求数量不代表资料或字段完整率。同一任务失败后成功可能来自缓存或重解析，不能当作真实网络恢复。BLS 的真实 HTTP 403 本轮未恢复，详见 public_channels/PUBLIC_CHANNELS_V3.md。', '',
        '| 渠道 | 本轮实际状态 | 成功HTTP响应 | 原文版本 | 真实HTTP失败后成功 |','|---|---|---:|---:|---|']
    md += [f"| {r['source_id']} | {r['actual_status']} | {r['successful_http_responses']} | {r['raw_versions']} | {r['real_network_failure_recovery_observed']} |" for r in rows]
    md += ['', '完整真实请求、原文路径/哈希、解析记录计数、任务尝试与失败日志见 channel-evidence.json。',
           '已连通来源仍标部分接通：历史版本、全文或字段的完整范围未获独立全面证明。缺少许可的来源不会发请求伪装验证。']
    (target/'CHANNELS_V3.md').write_text('\n'.join(md),encoding='utf-8')
    return {'sources':len(rows),'output':str(target.resolve())}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--storage',action='append',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    print(json.dumps(build(a.storage,a.out)))
