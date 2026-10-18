"""Build expectations from RAW source bytes, never from collected records/inventory.

Independent implementation of SEC column lists, SGML document inventories and a
raw-first stratified iXBRL sample. Reuses only the independent audit's numeric
checker, not any production financial/document parser. Reference fixtures and
manual narrative annotations belong to reports, never production configuration.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fnmatch
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from audit_machine import attribute, independent_context, independent_number, independent_path, independent_soup, independent_unit, local


def sample_original_nodes(nodes, contexts, units, raw_sha256, count):
    """Raw-only strata; unsupported evidence is sampled too, never filtered away."""
    ordered=sorted(nodes,key=lambda n:hashlib.sha256(('v4-unseen-reference|'+raw_sha256+'|'+independent_path(n)).encode()).hexdigest())
    def strata(node):
        tags=set()
        if attribute(node,'sign')=='-':tags.add('negative')
        if attribute(node,'scale','0')!='0':tags.add('display_scale')
        if attribute(node,'nil','').lower() in {'1','true'}:tags.add('nil')
        if attribute(node,'format'):tags.add('transformed')
        if not str(attribute(node,'name','')).startswith(('us-gaap:','ifrs-full:','dei:')):tags.add('custom_concept')
        context=contexts.get(attribute(node,'contextref'),(None,))[0]
        unit=units.get(attribute(node,'unitref'),(None,))[0]
        if context and context.get('dimensions'):tags.add('dimensions')
        if unit:
            if unit['denominator']:tags.add('quotient_unit')
            if unit['currency']:tags.add('currency')
        return tags or {'other'}
    chosen=[];used=set()
    for category in ('negative','display_scale','quotient_unit','custom_concept','dimensions','nil','transformed','currency'):
        node=next((n for n in ordered if id(n) not in used and category in strata(n)),None)
        if node is not None and len(chosen)<count:chosen.append(node);used.add(id(node))
    for node in ordered:
        if len(chosen)>=count:break
        if id(node) not in used:chosen.append(node);used.add(id(node))
    return chosen, sorted(set().union(*(strata(node) for node in chosen))) if chosen else []


def build(storage, start, end, forms, out, per_document=12, max_documents_per_company=4, support_types=None):
    if Path(out).exists():raise ValueError('Reference already exists; use a new versioned output path')
    root = Path(storage).resolve()
    support_types = list(support_types or [])
    db = sqlite3.connect((root/'data.sqlite3').as_uri()+'?mode=ro', uri=True); db.row_factory = sqlite3.Row
    # Deliberately only read raw source evidence tables, never records/machine_inventory.
    rows = db.execute('SELECT r.*,b.path,b.sha256,b.media_type FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) ORDER BY r.retrieved_at,r.retrieval_id').fetchall()
    latest = {}
    for row in rows: latest[row['url']] = dict(row)
    def content(row):
        body = (root/row['path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != row['sha256']: raise ValueError('Reference raw integrity failure')
        return body
    def evidence(row, locator):
        return {'raw_id': row['raw_id'], 'sha256': row['sha256'], 'url': row['url'], 'locator': locator}
    documents, fields, known = [], [], {}
    listing_evidence, document_sampling = [], []
    for row in latest.values():
        if '/submissions/' not in row['url']: continue
        try: payload = json.loads(content(row))
        except (ValueError, UnicodeError): continue
        cik_match = re.search(r'CIK(\d{10})', row['url'])
        if not cik_match: continue
        cik = cik_match[1]
        columns = payload.get('filings', {}).get('recent', payload)
        if not isinstance(columns.get('accessionNumber'), list): continue
        listing_evidence.append(evidence(row, '/filings/recent or historical column arrays'))
        for i, accession in enumerate(columns['accessionNumber']):
            try: date, form, primary = columns['filingDate'][i], columns['form'][i], columns['primaryDocument'][i]
            except (KeyError, IndexError): continue
            if not start <= date <= end or (forms and form not in forms): continue
            base = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/'
            known[accession] = (cik, base, form)
            if primary:
                documents.append({'reference_id': 'doc:'+accession+':'+primary, 'source_id': 'sec_edgar',
                                  'company_id': 'sec:'+cik, 'data_type': 'documents', 'url': base+primary,
                                  'filing_date': date, 'form': form, 'evidence': evidence(row, f'/primaryDocument/{i}')})
    # Read saved SEC accession SGML inventory independently from adapter code.
    by_id = {r['reference_id']: r for r in documents}
    outside_scope = {}
    for row in latest.values():
        accession_match = re.search(r'(\d{10}-\d{2}-\d{6})(?:-index-headers|-headers)?\.', row['url'])
        if not accession_match or accession_match[1] not in known: continue
        accession = accession_match[1]; cik, base, form = known[accession]
        text = html.unescape(content(row).decode('utf-8', errors='replace'))
        for number, block in enumerate(re.findall(r'<DOCUMENT>(.*?)(?=</DOCUMENT>|\Z)', text, re.S|re.I)):
            filename = re.search(r'<FILENAME>\s*([^\r\n<]+)', block, re.I)
            dtype = re.search(r'<TYPE>\s*([^\r\n<]+)', block, re.I)
            if not filename or not dtype: continue
            name = filename[1].strip(); refid = 'doc:'+accession+':'+name
            if dtype[1].strip()!=form and not any(fnmatch.fnmatchcase(dtype[1].strip(), pattern) for pattern in ['EX-*', *support_types]):
                if refid not in by_id:
                    outside_scope[refid]={'reference_id':refid,'company_id':'sec:'+cik,'url':base+name,
                        'document_type':dtype[1].strip(),'reason':'Outside configured primary / EX-* / support document types',
                        'evidence':evidence(row,f'SGML DOCUMENT[{number}]/FILENAME')}
                continue
            by_id.setdefault(refid, {'reference_id': refid, 'source_id': 'sec_edgar', 'company_id': 'sec:'+cik,
                                     'data_type': 'documents', 'url': base+name, 'form': dtype[1].strip(),
                                     'evidence': evidence(row, f'SGML DOCUMENT[{number}]/FILENAME')})
    documents = sorted(by_id.values(), key=lambda d: d['reference_id'])
    by_company = defaultdict(list)
    for ref in documents:
        raw = latest.get(ref['url'])
        if raw and (any(kind in raw['media_type'] for kind in ('html','xml')) or urlparse(raw['url']).path.endswith(('.htm','.html','.xhtml','.xml'))):
            by_company[ref['company_id']].append((ref, raw))
    soups = {}
    def soup(raw):
        if raw['raw_id'] not in soups: soups[raw['raw_id']] = independent_soup(content(raw))
        return soups[raw['raw_id']]
    for company, pairs in sorted(by_company.items()):
        eligible = [(ref, raw) for ref, raw in pairs if soup(raw).find(lambda t: local(t.name) in {'nonfraction','fraction'})]
        eligible.sort(key=lambda p: hashlib.sha256(('v4-unseen-doc-seed|'+p[0]['reference_id']).encode()).hexdigest())
        selected = eligible[:max_documents_per_company]
        document_sampling.append({'company_id': company, 'eligible_numeric_documents': len(eligible),
                                  'selected_documents': [r['url'] for _,r in selected]})
        for ref, raw in selected:
            doc = soup(raw)
            support = [(rr, soup(rr)) for _,rr in pairs if rr['url'].rsplit('/',1)[0] == raw['url'].rsplit('/',1)[0]]
            contexts, units = {}, {}
            for rr, ss in support:
                for node in ss.find_all(lambda t: local(t.name) in {'context','unit'}):
                    identity = attribute(node,'id')
                    target = contexts if local(node.name)=='context' else units
                    try:
                        parsed = independent_context(node) if target is contexts else independent_unit(node)
                        if identity in target and target[identity][0] != parsed: target[identity] = (None, rr, node)
                        elif identity not in target: target[identity] = (parsed,rr,node)
                    except ValueError: pass
            nodes = doc.find_all(lambda t: local(t.name) in {'nonfraction','fraction'})
            sampled,strata=sample_original_nodes(nodes,contexts,units,raw['sha256'],per_document)
            document_sampling.append({'raw_id': raw['raw_id'], 'company_id':company, 'numeric_targets_in_original': len(nodes), 'sampled': len(sampled), 'raw_strata':strata})
            for node in sampled:
                locator = independent_path(node)
                cid, uid = attribute(node,'contextref'), attribute(node,'unitref')
                target = {'reference_id': 'field:'+hashlib.sha256((raw['sha256']+locator).encode()).hexdigest(),
                          'source_id':'sec_edgar','company_id':company,'dataset':'financial_facts',
                          'evidence': evidence(raw,locator), 'rejection_locator':locator,
                          'selector': {'source_locator':locator,'data.concept_qname':attribute(node,'name'), 'data.context_id':cid, 'data.unit_id':uid},
                          'reference_status':'verified', 'expected':{}, 'tolerances':{'data.value':'0'}}
                try:
                    if local(node.name) == 'fraction': raise ValueError('Independent fraction verifier not implemented')
                    value, original = independent_number(node)
                    context,cr,cn = contexts[cid]; unit,ur,un = units[uid]
                    if context is None or unit is None: raise ValueError('Ambiguous cross-file context/unit')
                    target['expected'] = {'data.value':str(value) if value is not None else None,
                                          'period_start':context['period_start'],'period_end':context['period_end'],
                                          'data.dimensions':context['dimensions'], 'data.entity':context['entity'],
                                          'unit':unit['label'], 'currency':unit['currency']}
                    target['support_evidence'] = [evidence(cr,independent_path(cn)),evidence(ur,independent_path(un))]
                    target['original_text'] = original
                except (ValueError, KeyError, ArithmeticError) as exc:
                    target.update(reference_status='unverified',reference_limitation=str(exc))
                fields.append(target)
    manifest = {'schema_version':'coverage-reference-1','reference_provenance':{
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'builder_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'verifier_sha256':hashlib.sha256((Path(__file__).parent/'audit_machine.py').read_bytes()).hexdigest(),
        'method':'Raw-first SEC listing/SGML enumeration and stratified deterministic iXBRL sampling; independent BeautifulSoup original namespaces + Decimal verifier; no records/inventory reads',
        'uses_collector_outputs':False, 'listing_evidence':listing_evidence,'sampling':document_sampling},
        'scope':{'start':start,'end':end,'forms':sorted(forms),'attachment_types':['EX-*'],'support_document_types':support_types,
                 'field_sampling_per_document':per_document,'max_documents_per_company':max_documents_per_company},
        'limitations':['Reference uses independently read saved official listings, not a separate upstream publisher; source listing omissions remain unknown.',
                       'Undownloaded historical listing pages and headers are outside this bounded reference; attachment denominator is incomplete for absent headers.',
                       'Fields are raw-first samples of iXBRL numeric facts only; guidance, relations, operating metrics and untagged tables need separately annotated reference targets.',
                       'Independent unsupported transformations remain unverified, never assumed correct.',
                       'Source-declared GRAPHIC/JSON/ZIP and other nonconfigured assets are separately listed outside the denominator; saved HTML does not imply its external figures are archived.',
                       'This is a sampled source-window audit, not whole-market coverage.'],
        'source_declared_outside_configured_scope':list(outside_scope.values()),
        'documents':documents,'fields':fields}
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    Path(out).write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    db.close()
    return {'documents':len(documents),'fields':len(fields),'path':str(Path(out).resolve())}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--storage',required=True);p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--forms',default='10-K,10-K/A,10-Q,10-Q/A,8-K,8-K/A,20-F,20-F/A,6-K');p.add_argument('--out',required=True)
    p.add_argument('--per-document',type=int,default=12);p.add_argument('--max-documents-per-company',type=int,default=4)
    p.add_argument('--support-types',default='XML,EX-101.INS')
    a=p.parse_args();print(json.dumps(build(a.storage,a.start,a.end,set(a.forms.split(',')),a.out,a.per_document,a.max_documents_per_company,[s for s in a.support_types.split(',') if s])))
