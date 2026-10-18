"""Deterministic validation; failures never fill missing observations."""
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime,timezone


def validate(store, config=None):
    findings=[]
    def add(code,severity,message,**context):
        findings.append(dict(code=code,severity=severity,message=message,**context))
    for raw in store.db.execute('SELECT * FROM raw_blobs'):
        path=store.root/raw['path']
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=raw['sha256']:
            add('RAW_HASH_MISMATCH','error','原始文件缺失或 SHA256 不一致',raw_id=raw['raw_id'])
    for issue in store.db.execute('PRAGMA foreign_key_check'):
        add('BROKEN_REFERENCE','error','数据库外键引用不完整',detail=list(issue))
    by_context=defaultdict(list)
    cross_source=defaultdict(list)
    datasets=[r[0] for r in store.db.execute('SELECT DISTINCT dataset FROM records')]
    field_coverage={}
    for dataset in datasets:
        rows=store.query(dataset,latest=False)
        keys=set().union(*(set(r['data']) for r in rows))
        field_coverage[dataset]={k:{'present':sum(r['data'].get(k) is not None for r in rows),'null_or_absent':sum(r['data'].get(k) is None for r in rows)} for k in sorted(keys)}
        for r in rows:
            d=r['data']
            if not r.get('source_locator'):
                add('MISSING_LOCATOR','warning','原文定位缺失',record_id=r['record_id'])
            if r['period_start'] and r['period_end'] and r['period_start']>r['period_end']:
                add('INVALID_PERIOD','error','期间起止颠倒',record_id=r['record_id'])
            if dataset in {'daily_bars','benchmark_bars'}:
                nums=[d.get(k) for k in ['open','high','low','close']]
                if any(v is None for v in nums):
                    add('MISSING_OHLC','error','OHLC 不完整',record_id=r['record_id'])
                elif any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in nums):
                    add('INVALID_OHLC','error','价格不是有限数值',record_id=r['record_id'])
                elif min(nums)<=0 or d['low']>min(d['open'],d['close']) or d['high']<max(d['open'],d['close']):
                    add('INVALID_OHLC','error','价格范围异常',record_id=r['record_id'])
                if isinstance(d.get('volume'),(int,float)) and d['volume']<0:
                    add('NEGATIVE_VOLUME','error','成交量为负',record_id=r['record_id'])
                if d.get('turnover') is None:
                    add('TURNOVER_UNAVAILABLE','gap','真实成交额缺失，不能用 VWAP×成交量冒充',record_id=r['record_id'])
            if dataset=='financial_facts':
                if not r['unit']: add('MISSING_UNIT','error','财务数字缺少单位',record_id=r['record_id'])
                key=(r['company_id'],d.get('taxonomy'),d.get('concept'),r['period_start'],r['period_end'],r['unit'],d.get('accession'),d.get('accounting_basis'),d.get('consolidation_scope'))
                by_context[key].append(r)
                # Compare only exactly compatible contemporaneous contexts. A later
                # filing can legitimately revise/restate numbers and is not a source clash.
                if d.get('concept') and d.get('taxonomy') and r['company_id'] and r['period_end'] and r['unit'] and r['available_at'] and d.get('accounting_basis') and d.get('consolidation_scope'):
                    cross_key=(r['company_id'],d['taxonomy'],d['concept'],r['period_start'],r['period_end'],r['unit'],d['accounting_basis'],d['consolidation_scope'],r['available_at'])
                    cross_source[cross_key].append(r)
    for key,rs in by_context.items():
        if len({str(r['data'].get('value')) for r in rs})>1:
            add('FACT_CONTEXT_CONFLICT','warning','同一 accession、指标、期间及单位存在不同数值，保留冲突',context=list(key),record_ids=[r['record_id'] for r in rs])
    for key,rs in cross_source.items():
        if len({r['source'] for r in rs})>1 and len({str(r['data'].get('value')) for r in rs})>1:
            add('CROSS_SOURCE_FACT_CONFLICT','warning','不同来源在相同公开时间、指标、期间、单位及口径下数值冲突，未选择胜者',context=list(key),record_ids=[r['record_id'] for r in rs])
    counts={r['dataset']:r['n'] for r in store.db.execute('SELECT dataset,COUNT(*) n FROM records GROUP BY dataset')}
    required=['company_profiles','securities','calendar_sessions','security_status','market_rules','daily_bars','corporate_actions','financial_facts','documents','guidance','event_calendar','benchmark_bars','analyst_estimates']
    for dataset in required:
        if not counts.get(dataset): add('DATASET_EMPTY','gap','当前采样库未取得该数据集；见来源和依赖清单',dataset=dataset)
    if not counts.get('analyst_estimates'):
        add('NO_HISTORICAL_CONSENSUS','gap','无法研究相对市场预期的业绩差异')
    # Trading calendar is operational validation, not historical announcement evidence.
    if config:
        sessions=store.query('calendar_sessions')
        by_exchange=defaultdict(set)
        for r in sessions:
            d=r['data']
            if d.get('is_session'): by_exchange[d['exchange']].add(d['date'])
        for sec in config.get('securities',[]):
            start,end=sec.get('start',config['start']),sec.get('end',config['end'])
            expected={d for d in by_exchange[sec['exchange']] if start<=d<=end}
            bars=store.query('daily_bars',security_id=sec['security_id'])
            observed={r['data'].get('session_date',r['data'].get('date')) for r in bars}
            missing=sorted(expected-observed)
            if missing: add('SESSION_BAR_GAPS','gap','相对日历的缺少行情日期；可能是来源未接通或退市/停牌，不能补造',security_id=sec['security_id'],missing_count=len(missing),first_10=missing[:10],last_10=missing[-10:])
            if bars:
                latest=max(d for d in observed if d)
                expected_end=max(expected) if expected else None
                if expected_end and latest<expected_end: add('BAR_SOURCE_LAG','warning','行情末日早于要求的最后交易日；需结合状态解释',security_id=sec['security_id'],last_observed=latest,requested_end=expected_end)
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'status':'failed' if any(f['severity']=='error' for f in findings) else 'passed_with_gaps',
            'findings':findings,'field_coverage':field_coverage,'counts':counts}
