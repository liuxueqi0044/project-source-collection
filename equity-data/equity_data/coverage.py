"""Reference-denominator coverage audit, independent of collection task success.

A reference manifest is research/audit input, never an acquisition configuration.
It must describe how expectations were established without collector outputs.
Unknown reference scope is kept unknown rather than reported as 100% coverage.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from .store import now_utc


def _get(obj, path):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def _equal(actual, expected, tolerance=None):
    if actual is None or expected is None:
        return actual is expected
    if tolerance is not None:
        try:
            a, b, t = Decimal(str(actual)), Decimal(str(expected)), Decimal(str(tolerance))
            return a.is_finite() and b.is_finite() and t.is_finite() and t >= 0 and abs(a-b) <= t
        except (InvalidOperation, ValueError):
            return False
    return actual == expected


def _summary(items, success):
    counts = dict(Counter(i['outcome'] for i in items))
    denominator = len(items)
    numerator = sum(counts.get(k, 0) for k in success)
    return {'reference_denominator': denominator, 'outcomes': counts, 'success_numerator': numerator,
            'success_rate': numerator / denominator if denominator else None,
            'rate_scope': 'only independently referenced targets; not whole-source recall'}


def _document_summary(items):
    """Saved evidence and the current workflow are independent denominators."""
    denominator=len(items)
    downloaded=sum(bool(item['downloaded']) for item in items)
    states=dict(Counter(item['outcome'] for item in items))
    return {'reference_denominator':denominator,'outcomes':states,'workflow_state_counts':states,
            'discovered':sum(bool(item['discovered']) for item in items),'downloaded':downloaded,
            'success_numerator':downloaded,'success_rate':downloaded/denominator if denominator else None,
            'success_basis':'downloaded_has_saved_raw','raw_integrity_requirement':'saved blob exists and SHA256 matches',
            'saved_raw_with_pending_or_failed_workflow':sum(bool(item['downloaded']) and item['outcome'] in {
                'discovered_not_downloaded','download_failed','missing_authorization'} for item in items),
            'latest_requested_version_downloaded':None,
            'rate_scope':'only independently referenced targets with an already saved original; not whole-source recall, latest-refresh completion, or full parsing',
            'workflow_caveat':'A retained old original can count as downloaded while a newer request is pending or failed; no latest source-version claim is inferred.'}


def _saved_original(store, raw_id, cache):
    """Read-only, once-per-blob evidence check; workflow labels prove no bytes."""
    if not raw_id:
        return {'raw_id':None,'saved':False,'status':'no_raw_pointer'}
    if raw_id in cache:
        return cache[raw_id]
    row=store.db.execute('SELECT path,sha256 FROM raw_blobs WHERE raw_id=?',(raw_id,)).fetchone()
    result={'raw_id':raw_id,'saved':False,'status':'raw_blob_not_recorded'}
    if row:
        result.update(sha256=row['sha256'],status='raw_file_missing_or_unreadable')
        try:
            digest=hashlib.sha256((store.root/row['path']).read_bytes()).hexdigest()
            result.update(saved=digest==row['sha256'],status='saved_raw_verified' if digest==row['sha256'] else 'raw_hash_mismatch')
        except OSError:
            pass
    cache[raw_id]=result
    return result


def _evidence_valid(store, evidence):
    if not isinstance(evidence, dict) or not evidence.get('raw_id') or not evidence.get('sha256') or not evidence.get('locator'):
        return False
    row = store.db.execute('SELECT path,sha256 FROM raw_blobs WHERE raw_id=?', (evidence['raw_id'],)).fetchone()
    return bool(row and row['sha256'] == evidence['sha256'] and
                hashlib.sha256((store.root/row['path']).read_bytes()).hexdigest() == evidence['sha256'])


def _time(value):
    if not value or len(value) <= 10:
        return None
    try:
        t = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except ValueError:
        return None


def freshness_matrix(resources, store=None):
    """Per company/type acquisition age; never substitute it for release timeliness."""
    groups = defaultdict(list)
    for resource in resources:
        groups[(resource['source_id'], resource.get('company_id'), resource['data_type'])].append(resource)
    result = []
    for (source, company, kind), rows in sorted(groups.items(), key=lambda p: str(p[0])):
        acquisition_times = {}
        for resource in rows:
            stamp = resource.get('retrieved_at')
            if store is not None and resource.get('retrieval_id'):
                retrieval = store.db.execute('SELECT retrieved_at FROM raw_retrievals WHERE retrieval_id=?', (resource['retrieval_id'],)).fetchone()
                stamp = retrieval[0] if retrieval else None
            acquisition_times[id(resource)] = stamp
        acquired = [acquisition_times[id(r)] for r in rows if acquisition_times[id(r)]]
        dates, delays = [], []
        for r in rows:
            meta = r.get('metadata') or {}
            ev = meta.get('evidence', meta)
            pub = ev.get('published_at') or (meta.get('filing') or {}).get('filingDate')
            if pub: dates.append(pub)
            available = _time(ev.get('available_at'))
            fetched = _time(acquisition_times[id(r)])
            if available and fetched:
                delays.append((fetched-available).total_seconds())
        result.append({'source_id': source, 'company_id': company, 'data_type': kind,
                       'resource_count': len(rows), 'states': dict(Counter(r['state'] for r in rows)),
                       'oldest_resource_acquisition': min(acquired) if acquired else None,
                       'newest_resource_acquisition': max(acquired) if acquired else None,
                       'latest_parse_completion': max((r['last_success_at'] for r in rows if r.get('last_success_at')), default=None),
                       'source_reported_publication_min': min(dates) if dates else None,
                       'source_reported_publication_max': max(dates) if dates else None,
                       'unknown_publication_count': len(rows)-len(dates),
                       'availability_to_acquisition_max_seconds': max(delays) if delays else None,
                       'disclosure_update_delay_seconds': None,
                       'delay_caveat': 'Backfill delay and conservative availability are not monitoring latency; unseen disclosures remain unknown.'})
    return result


def evaluate_reference(store, manifest, resources=None, gaps=None):
    """Compare a frozen independent expectation list with current machine state."""
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 'coverage-reference-1':
        raise ValueError('Unsupported reference manifest schema')
    provenance = manifest.get('reference_provenance', {})
    if not provenance.get('method') or provenance.get('uses_collector_outputs') is not False:
        raise ValueError('Reference must state independent method and uses_collector_outputs=false')
    if resources is None or gaps is None:
        from .queue import Queue
        q = Queue(store)
        resources, gaps = q.resources(), q.gaps()
    document_targets, field_targets = manifest.get('documents', []), manifest.get('fields', [])
    ids = [r.get('reference_id') for r in document_targets+field_targets]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('Reference IDs must be present and unique')
    documents, fields, raw_checks = [], [], {}
    for ref in document_targets:
        if not _evidence_valid(store, ref.get('evidence')):
            raise ValueError('Unverifiable document reference: '+ref['reference_id'])
        matches = [r for r in resources if r['source_id'] == ref['source_id'] and
                   (r.get('url') == ref.get('url') if ref.get('url') else r['resource_key'] == ref.get('resource_key'))]
        if ref.get('company_id'):
            matches = [r for r in matches if r.get('company_id') == ref['company_id']]
        state = 'not_discovered'
        if matches:
            current = max(matches, key=lambda r: r.get('last_discovered_at') or '')
            state = current['state']
        saved_originals=[{**_saved_original(store,r.get('raw_id'),raw_checks),'resource_id':r['resource_id']} for r in matches]
        documents.append({**ref, 'outcome': state, 'workflow_state':state,'discovered': bool(matches),
                          'downloaded': any(item['saved'] for item in saved_originals),
                          'download_basis':'downloaded_has_saved_raw','saved_originals':saved_originals,
                          'latest_requested_version_downloaded':None,
                          'resource_ids': [r['resource_id'] for r in matches]})
    datasets = {r['dataset'] for r in field_targets}
    records = {}
    for dataset in datasets:
        versions = {}
        for row in store.query(dataset, latest=False):
            key = (row['source'], row['raw_id'], row['record_key'])
            if key not in versions or row['sequence'] > versions[key]['sequence']:
                versions[key] = row
        records[dataset] = list(versions.values())
    for ref in field_targets:
        if not _evidence_valid(store, ref.get('evidence')):
            raise ValueError('Unverifiable field reference: '+ref['reference_id'])
        if not ref.get('selector') or not ref.get('evidence', {}).get('locator'):
            raise ValueError('Field reference requires a specific selector and original locator')
        for supporting in ref.get('support_evidence', []):
            if not _evidence_valid(store, supporting):
                raise ValueError('Unverifiable supporting reference: '+ref['reference_id'])
        raw_id = ref['evidence']['raw_id']
        matched = [r for r in records[ref['dataset']] if r['source'] == ref['source_id'] and r['raw_id'] == raw_id and
                   all(_get(r, key) == value for key, value in ref.get('selector', {}).items()) and
                   r['data'].get('status') != 'not_emitted_by_current_parser']
        locator_unresolved = False
        if not matched and 'source_locator' in ref['selector']:
            locator_unresolved = any(r['source'] == ref['source_id'] and r['raw_id'] == raw_id and
                all(_get(r,key) == value for key,value in ref['selector'].items() if key != 'source_locator')
                for r in records[ref['dataset']])
        candidates = [r for r in matched if r['data'].get('fact_stage') == 'candidate' or
                      r['data'].get('extraction_method') == 'narrative_evidence_candidate']
        manual = [r for r in matched if r['data'].get('extraction_mode','').startswith('manual') or
                  r.get('quality','').startswith('manual') or r.get('parser_version','').startswith('manual') or
                  r['data'].get('import_manifest_raw_id')]
        facts = [r for r in matched if r not in candidates and r not in manual and
                 (r['data'].get('fact_stage') in {'extracted_fact','normalized_metric'} or r['data'].get('extraction_mode') == 'automatic' or
                  r['data'].get('extraction_method') == 'inline_xbrl_local_no_taxonomy_network')]
        # A rejection must identify this exact raw and field, not merely the document.
        rejects = []
        for gap in gaps:
            detail = gap.get('details') or {}
            locator = detail.get('source_locator')
            if detail.get('raw_id') == raw_id and locator and locator == ref.get('rejection_locator', ref['evidence']['locator']):
                if any(word in gap['code'] for word in ('REJECT', 'UNSUPPORTED', 'UNRESOLVED', 'MISSING')):
                    rejects.append(gap['gap_id'])
        checks = []
        if ref.get('reference_status', 'verified') != 'verified':
            outcome = 'reference_unverified'
        elif facts:
            for fact in facts:
                checks.append({'record_id': fact['record_id'], 'fields': {
                    key: {'expected': value, 'actual': _get(fact, key),
                          'pass': _equal(_get(fact, key), value, ref.get('tolerances', {}).get(key))}
                    for key, value in ref.get('expected', {}).items()}})
            outcome = 'extracted_correct' if checks and all(all(v['pass'] for v in c['fields'].values()) for c in checks) and ref.get('expected') else 'extracted_mismatch'
        elif rejects:
            outcome = 'explicitly_rejected'
        elif candidates:
            outcome = 'candidate_only'
        elif manual:
            outcome = 'manual_only'
        elif matched:
            outcome = 'extraction_provenance_unknown'
        elif locator_unresolved:
            outcome = 'target_locator_unresolved'
        else:
            outcome = 'silent_omission'
        fields.append({**ref, 'outcome': outcome, 'checks': checks,
                       'matched_record_ids': [r['record_id'] for r in matched], 'rejection_gap_ids': rejects})
    by_group = []
    group_keys = {(r['source_id'], r.get('company_id'), r.get('data_type', r.get('dataset'))) for r in documents+fields}
    for source, company, kind in sorted(group_keys, key=str):
        ds = [r for r in documents if (r['source_id'], r.get('company_id'), r.get('data_type')) == (source, company, kind)]
        fs = [r for r in fields if (r['source_id'], r.get('company_id'), r.get('dataset')) == (source, company, kind)]
        by_group.append({'source_id': source, 'company_id': company, 'data_type': kind,
                         'documents': _document_summary(ds),
                         'fields': _summary(fs, {'extracted_correct'})})
    return {'schema_version': 'reference-coverage-1', 'generated_at': now_utc(),
            'reference_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'reference_provenance': provenance, 'scope': manifest.get('scope'),
            'limitations': manifest.get('limitations', []), 'whole_source_coverage_complete': None,
            'documents_summary': _document_summary(documents),
            'fields_summary': _summary(fields, {'extracted_correct'}),
            'documents': documents, 'fields': fields, 'by_source_company_type': by_group,
            'freshness': freshness_matrix(resources, store),
            'interpretation': 'Task completion, parsing completion and reference coverage are independent; zero targets means unknown, not complete.'}


def write_coverage(store, reference_path, out):
    manifest = json.loads(Path(reference_path).read_text(encoding='utf-8'))
    report = evaluate_reference(store, manifest)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    (out/'reference-coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 独立参考清单覆盖核验', '', '分母来自冻结参考清单；不代表全网站或全部财务信息。', '',
             '## 资料覆盖', '', '```json', json.dumps(report['documents_summary'], ensure_ascii=False, indent=2), '```', '',
             '资料 success_numerator/success_rate 的口径是 downloaded_has_saved_raw：已有原文文件并通过 SHA256 检查。当前 workflow 状态单独保留；旧原文存在不代表最新更新请求已完成，也不代表全部字段解析成功。', '',
             '## 目标字段覆盖', '', '```json', json.dumps(report['fields_summary'], ensure_ascii=False, indent=2), '```', '',
             '候选片段不计成功提取。明确拒绝仍属于未取得目标事实。reference_unverified 不计正确或错误，但保留在总分母中。', '',
             '## 参考清单局限', '', *['- '+s for s in report['limitations']], '',
             '全部参考目标、原文哈希、匹配结果和逐公司时效见 reference-coverage.json。']
    (out/'reference-coverage.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'status': 'reported', 'output': str(out.resolve()), 'documents': report['documents_summary'], 'fields': report['fields_summary']}
