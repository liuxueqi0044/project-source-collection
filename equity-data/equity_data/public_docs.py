"""Evidence-guarded public documents and authorized local material imports."""
from __future__ import annotations
import hashlib
import html
import json
import re
from pathlib import Path
from datetime import datetime, date

from .parsers import PARSER_VERSION, parse_document

DATASETS = {'company_profiles','securities','identity_history','calendar_sessions','security_status','market_rules','daily_bars','corporate_actions','financial_facts','filings','documents','guidance','analyst_estimates','earnings_actuals','event_calendar','benchmark_bars','relations','industry_metrics','macro_observations','policy_documents','news','research_materials'}
KNOWLEDGE_TYPES = {'fact_observation','company_statement','expert_opinion','survey_estimate','system_inference'}


def _observed_time(store, source, dataset, key, data, acquired):
    row=store.db.execute('SELECT data_json,available_at FROM records WHERE source=? AND dataset=? AND record_key=? ORDER BY sequence DESC LIMIT 1',(source,dataset,key)).fetchone()
    if row and json.loads(row['data_json'])==data:
        return row['available_at']
    return acquired


def visible_text(content):
    value = content.decode('utf-8', errors='replace')
    value = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', value, flags=re.S|re.I)
    value = re.sub(r'<[^>]+>', ' ', value)
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()


def collect_public_documents(store, client, config):
    setting = config.get('public_documents', {})
    if not setting.get('manifest'):
        return {'status':'disabled','count':0}
    manifest_path = Path(config.get('_base_dir','.')) / setting['manifest']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    count, failures, automatic_extracted_count, raw_download_count = 0, [], 0, 0
    for item in manifest['documents']:
        try:
            content, raw = client.fetch(item['url'], source=item['source'], refresh=config.get('_refresh',True))
            raw_download_count += 1
            # Live web pages are mutable. Their displayed publication date is retained,
            # but is NOT proof that today's exact copy was visible on that past day.
            evidence = dict(source=item['source'],raw_id=raw['raw_id'],company_id=item.get('company_id'),
                security_id=item.get('security_id'),published_at=item.get('published_date'),
                publication_precision='day' if item.get('published_date') else 'unknown',
                publication_timezone=item.get('publication_timezone'),
                available_at=raw['retrieved_at'],availability_basis='current_web_copy_first_observed',
                source_locator=item.get('locator','whole document'),quality='source_copy_not_historical_version_verified',
                knowledge_type=item.get('knowledge_type','company_statement'),parser_version=PARSER_VERSION)
            base = {'title':item['title'],'document_type':item.get('document_type','public_document'),
                    'url':item['url'],'content_sha256':raw['sha256'],
                    'historical_version_verified':False,'original_published_date':item.get('published_date'),
                    'licensing_status':'public_access_internal_validation_only; redistribution_not_verified'}
            evidence['available_at']=_observed_time(store,item['source'],'documents',item['id'],base,raw['retrieved_at'])
            store.put_record('documents',item['id'],base,**evidence)
            count += 1
            # Old manifests embedded the final numbers and merely checked a few
            # keywords. They remain discoverable raw evidence, never automatic
            # extraction. Human excerpts have a separate authorized JSONL entry.
            if any('data' in rec for rec in item.get('records', [])):
                message = 'Prefilled records.data is forbidden in automatic collection. Use import-materials with explicit evidence, rights and manual_import_unverified status.'
                store.issue('PREFILLED_MANIFEST_REJECTED', message, dataset='documents',
                            details={'url':item['url'],'manual_import_required':True})
                failures.append({'id':item['id'],'reason':message,'status':'capability_gap'})
                continue
            media_type = item.get('media_type') or ('application/pdf' if item['url'].lower().split('?')[0].endswith('.pdf') else 'text/html')
            parsed = parse_document(content, media_type=media_type, document_type=item.get('document_type'),
                                    company_id=item.get('company_id'), source_url=item['url'])
            for problem in parsed['issues']:
                store.issue(problem['code'], problem['message'], dataset='documents',company_id=item.get('company_id'),
                            details={'url':item['url'], 'raw_id':raw['raw_id'], **problem})
            if parsed['issues']:
                failures.append({'id':item['id'],'reason':'Some document fields were rejected or unsupported',
                                 'status':'partial_parse','coverage':parsed['coverage']})
            for rec in parsed['observations']:
                payload = dict(rec['data'])
                payload['historical_version_verified'] = False
                local = dict(evidence)
                local['available_at']=_observed_time(store,item['source'],rec['dataset'],rec['record_key'],payload,raw['retrieved_at'])
                for field in ['unit','currency','period_start','period_end','event_date','source_locator','knowledge_type','quality']:
                    if field in rec: local[field] = rec[field]
                store.put_record(rec['dataset'],rec['record_key'],payload,**local)
                count += 1
                automatic_extracted_count += 1
        except Exception as exc:
            failures.append({'id':item['id'],'reason':str(exc)})
            store.issue('PUBLIC_DOCUMENT_FAILED',str(exc),dataset='documents',details={'id':item['id']})
    return {'status':'partial' if failures else 'ok','count':count,'failures':failures,
            'raw_download_count':raw_download_count,'automatic_extracted_count':automatic_extracted_count,
            'manual_import_count':0}


def import_materials(store, input_path):
    """JSONL with explicit authorization and exact original evidence file required."""
    path=Path(input_path).resolve()
    entries=[json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    # Validate entire batch before any durable inserts.
    prepared=[]
    for i, entry in enumerate(entries,1):
        if entry.get('rights') not in {'public','authorized'}:
            raise ValueError(f'Line {i}: rights must be public or authorized')
        if entry.get('classification') != 'public_or_authorized_nonconfidential':
            raise ValueError(f'Line {i}: confidential / leaked / nonpublic trading information is not admitted')
        if entry.get('knowledge_type') not in KNOWLEDGE_TYPES:
            raise ValueError(f'Line {i}: explicit knowledge_type required')
        if entry.get('dataset') not in DATASETS:
            raise ValueError(f'Line {i}: unknown dataset')
        if not isinstance(entry.get('data'),dict):
            raise ValueError(f'Line {i}: data must be an object')
        json.dumps(entry['data'],allow_nan=False)
        for required in ('record_key','source','source_url','source_locator','evidence_path','data','authorization_note'):
            if not entry.get(required): raise ValueError(f'Line {i}: missing {required}')
        if entry['knowledge_type']=='survey_estimate' and not all(entry['data'].get(x) for x in ['sample_scope','methodology']):
            raise ValueError(f'Line {i}: survey needs sample_scope and methodology')
        if entry['knowledge_type']=='system_inference' and not all(entry['data'].get(x) for x in ['method','input_record_ids']):
            raise ValueError(f'Line {i}: inference needs method and input_record_ids')
        ev=(path.parent / entry['evidence_path']).resolve()
        if not ev.is_file(): raise ValueError(f'Line {i}: evidence file not found')
        if entry.get('available_at') and not entry.get('availability_evidence'):
            raise ValueError(f'Line {i}: available_at needs explicit availability_evidence')
        if entry.get('available_at'):
            parsed=datetime.fromisoformat(entry['available_at'].replace('Z','+00:00'))
            if parsed.tzinfo is None or entry.get('availability_basis','unknown')=='unknown':
                raise ValueError(f'Line {i}: available_at needs timezone and explicit basis')
        if entry.get('published_at'):
            if entry.get('publication_precision')=='day':
                date.fromisoformat(entry['published_at'])
                if not entry.get('publication_timezone'): raise ValueError(f'Line {i}: publication day needs timezone')
            else:
                parsed=datetime.fromisoformat(entry['published_at'].replace('Z','+00:00'))
                if parsed.tzinfo is None: raise ValueError(f'Line {i}: publication timestamp needs timezone')
        for field in ['period_start','period_end','event_date']:
            if entry.get(field): date.fromisoformat(entry[field])
        prepared.append((entry,ev.read_bytes()))
    ids=[]
    batch=store.put_raw(path.read_bytes(),source='manual_import_manifest',url=path.as_uri(),media_type='application/x-ndjson')
    for entry,content in prepared:
        raw=store.put_raw(content,source=entry['source'],url=entry['source_url'],
                          media_type=entry.get('media_type','application/octet-stream'),
                          metadata={'rights':entry['rights'],'authorization_note':entry['authorization_note'],'import_manifest_raw_id':batch['raw_id']})
        payload=dict(entry['data'])
        payload['import_manifest_raw_id']=batch['raw_id']
        payload['availability_evidence']=entry.get('availability_evidence')
        meta={k:entry[k] for k in ['company_id','security_id','published_at','publication_precision','publication_timezone','period_start','period_end','event_date','source_locator','unit','currency','knowledge_type'] if k in entry}
        meta.update(available_at=entry.get('available_at'),availability_basis=entry.get('availability_basis','unknown'),
                    quality='manual_import_unverified',parser_version='manual-jsonl-v1')
        ids.append(store.put_record(entry['dataset'],entry['record_key'],payload,source=entry['source'],raw_id=raw['raw_id'],**meta))
    return {'status':'ok','count':len(ids),'record_ids':ids}
