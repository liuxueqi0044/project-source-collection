"""Task-enumerating public feed, Federal Register and BLS adapters.

They consume explicit endpoints/series, not search-engine snippets. Feed and
current API responses provide acquisition-time versions, not historical
publication-time archives. Every discovered item/page becomes durable work.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import parse_qs, quote, urljoin, urlsplit
import xml.etree.ElementTree as ET

from .base import BaseAdapter, persist_observations
from ..parsers import parse_document
from ..public_docs import visible_text
from ..research import ATOM, _canonical, _feed_entries, _timestamp

PARSER_VERSION = 'open-sources-v2.0.0'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _put(ctx, resource, raw, dataset, key, data, *, published_at=None, publication_precision='unknown',
         publication_timezone=None, source_locator=None, quality='parsed_unverified', **kwargs):
    """Unchanged snapshots attach new evidence without fabricating revisions."""
    source = resource['source_id']
    previous = ctx.store.db.execute('SELECT data_json,available_at FROM records WHERE dataset=? AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1',
                                   (dataset, source, key)).fetchone()
    available = previous['available_at'] if previous and json.loads(previous['data_json']) == data else raw['retrieved_at']
    return ctx.store.put_record(dataset, key, data, source=source, raw_id=raw['raw_id'],
        company_id=resource.get('company_id'), security_id=resource.get('security_id'),
        published_at=published_at, publication_precision=publication_precision, publication_timezone=publication_timezone,
        available_at=available, availability_basis='first_observed_current_source_version', source_locator=source_locator,
        quality=quality, knowledge_type='fact_observation', parser_version=PARSER_VERSION, **kwargs)


def _result(ids=(), *, children=0, issues=(), **coverage):
    return {'status':'partial' if issues else 'parsed', 'record_ids':list(ids), 'parsed_count':len(ids),
            'errors':list(issues), 'coverage':{'children_discovered':children, **coverage}}


def _gap(ctx, source, code, message, *, data_type, status='capability_gap', **details):
    ctx.gap(code, message, source_id=source, data_type=data_type, status=status, details=details)
    return {'code':code, 'message':message, 'details':details}


def _http_url(url):
    return isinstance(url, str) and urlsplit(url).scheme in {'http','https'} and bool(urlsplit(url).hostname)


def _government_url(url):
    if not _http_url(url) or urlsplit(url).scheme != 'https':
        return False
    host = urlsplit(url).hostname
    return any(host == domain or host.endswith('.' + domain) for domain in ('federalregister.gov','govinfo.gov'))


def _association_candidates(ctx, text):
    candidates = []
    for company in ctx.config.get('securities', []):
        name = company.get('name') or company.get('company_name')
        if name and len(name) >= 4 and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I):
            candidates.append({'company_id':company.get('company_id'), 'matched_name':name,
                               'method':'exact_configured_company_name', 'status':'candidate_needs_review'})
    return candidates


class PublicNewsAdapter(BaseAdapter):
    source_id = 'public_news'
    scope_kind = 'global'
    data_types = ('news','documents')

    def discover(self, ctx, scope):
        feeds = ctx.config.get('news', {}).get('feeds', [])
        count, gaps = 0, []
        if not feeds:
            gaps.append(_gap(ctx,self.source_id,'NEWS_FEEDS_NOT_CONFIGURED','Configure news.feeds with explicit public feed endpoints.',data_type='news'))
        for spec in feeds:
            feed = {'url':spec} if isinstance(spec,str) else dict(spec)
            if not feed.get('enabled',True):
                continue
            if not _http_url(feed.get('url')):
                gaps.append(_gap(ctx,self.source_id,'NEWS_FEED_URL_INVALID','Feed endpoint must be an explicit HTTP(S) URL.',data_type='news',feed=feed))
                continue
            ctx.emit({'source_id':self.source_id,'resource_key':'feed:'+_canonical(feed['url']), 'kind':'news_feed','data_type':'news',
                      'url':feed['url'],'metadata':{'feed':feed,'start':scope.get('start',ctx.config.get('start')),
                      'end':scope.get('end',ctx.config.get('end')),'seen_pages':[],'refresh':True}})
            count += 1
        return {'enumeration_complete':not gaps,'discovered':count,'gaps':gaps}

    def parse(self, ctx, resource, content, raw):
        if resource['kind'] == 'news_article':
            return self._article(ctx,resource,content,raw)
        # The shared RSS/Atom decoder has no limit and refuses entity/DTD input.
        entries = list(_feed_entries(content, resource['url']))
        root = ET.fromstring(content)
        atom = root.tag == ATOM+'feed'
        nodes = root.findall(ATOM+'entry') if atom else root.findall('./channel/item')
        if not atom and root.tag != 'rss':
            raise ValueError('Unsupported feed format; RSS 2.0 or Atom required')
        feed = resource['metadata']['feed']
        original_feed = _canonical(feed['url'])
        ids, issues, children, eligible = [], [], 0, 0
        start, end = resource['metadata'].get('start'),resource['metadata'].get('end')
        for index, entry in entries:
            locator = f'{"Atom entry" if atom else "RSS item"}[{index+1}]'
            if entry is None:
                issues.append(_gap(ctx,self.source_id,'NEWS_ITEM_URL_MISSING','Feed item without HTTP(S) article URL remains in original feed; cannot fetch article.',data_type='news',status='partial_parse',source_locator=locator,raw_id=raw['raw_id']))
                continue
            published, precision, zone = _timestamp(entry['publication'])
            updated, updated_precision, updated_zone = _timestamp(entry['updated'])
            in_scope = lambda timestamp: timestamp and not ((start and timestamp[:10] < start) or (end and timestamp[:10] > end))
            if published and not in_scope(published) and not in_scope(updated):
                continue
            eligible += 1
            text = visible_text((entry['title']+' '+entry['content']).encode())
            content_digest = _hash([entry['title'],entry['content']])
            item_key = 'feed:'+_hash(original_feed)+'|'+entry['canonical_url']
            data = dict(entry, feed_url=original_feed, source_published_at=published, publication_precision=precision,
                publication_timezone=zone, source_updated_at=updated, updated_precision=updated_precision, updated_timezone=updated_zone,
                content_scope='feed_entry_only', article_full_text_retrieved=False, original_feed_item_xml=ET.tostring(nodes[index],encoding='unicode'),
                item_content_sha256=content_digest, canonical_duplicate_candidate=_hash(entry['canonical_url']),
                content_duplicate_candidate=content_digest, title_duplicate_candidate=_hash(visible_text(entry['title'].encode()).casefold()),
                duplicate_status='candidate_only_not_merged', event_cluster_status='needs_review',
                company_association_candidates=_association_candidates(ctx,text), historical_version_time_verified=False,
                republication_status='feed_source_field_only' if entry.get('source_url') else 'unknown',
                full_text_permission='configured_allow' if feed.get('allow_full_text') is True else 'missing_authorization')
            ids.append(_put(ctx,resource,raw,'news',item_key,data,published_at=published,publication_precision=precision,
                publication_timezone=zone,source_locator=locator,quality='feed_excerpt_not_full_text'))
            if feed.get('allow_full_text') is True:
                ctx.emit({'source_id':self.source_id,'resource_key':'article:'+entry['canonical_url'], 'kind':'news_article','data_type':'documents',
                    'url':entry['canonical_url'],'metadata':{'feed_resource_key':resource['resource_key'],'news_record_key':item_key,
                    'title':entry['title'],'published_at':published,'publication_precision':precision,'publication_timezone':zone,
                    'updated_at':updated,'allow_full_text':True,'authorization_note':feed.get('authorization_note'), 'refresh':True}})
                children += 1
            else:
                _gap(ctx,self.source_id,'NEWS_FULL_TEXT_PERMISSION_MISSING','Only feed metadata/excerpt acquired. Article full text requires configured allow_full_text and applicable rights.',
                     data_type='news',status='missing_authorization',article_url=entry['canonical_url'],news_record_key=item_key)
        # Follow standards-based feed pagination, but never claim a current feed
        # exhausts historical articles. A server loop is an explicit gap.
        next_links = root.findall(ATOM+'link')
        channel = root.find('channel')
        if channel is not None:
            next_links += channel.findall(ATOM+'link')
        seen = resource['metadata'].get('seen_pages',[]) + [_canonical(resource['url'])]
        for link in next_links:
            if link.get('rel') != 'next':
                continue
            url = urljoin(resource['url'],link.get('href',''))
            if not _http_url(url) or _canonical(url) in seen:
                issues.append(_gap(ctx,self.source_id,'NEWS_PAGINATION_INVALID','Invalid or looping feed next-page URL; enumeration incomplete.',data_type='news',status='partial_parse',url=url))
                continue
            ctx.emit({'source_id':self.source_id,'resource_key':'feed:'+_canonical(url),'kind':'news_feed','data_type':'news','url':url,
                      'metadata':{**resource['metadata'],'seen_pages':seen}})
            children += 1
        _gap(ctx,self.source_id,'NEWS_HISTORICAL_ARCHIVE_UNVERIFIED','A current feed cannot prove exhaustive historical article/version coverage for the requested dates.',
             data_type='news',feed_url=original_feed,start=start,end=end,entries_in_response=len(entries),eligible_entries=eligible)
        return _result(ids,children=children,issues=issues,entries_in_response=len(entries),eligible_entries=eligible,
                       historical_enumeration_complete=False,full_text_success_count=0)

    def _article(self,ctx,resource,content,raw):
        if resource['metadata'].get('allow_full_text') is not True:
            raise ValueError('Article task lacks explicit full-text permission')
        media = 'application/pdf' if content.startswith(b'%PDF') else 'text/html'
        data = {'url':resource['url'],'title':resource['metadata'].get('title'),'content_sha256':raw['sha256'],
                'document_type':'news_article_response','historical_version_time_verified':False,
                'article_structure_verified':False,'full_text_status':'response_acquired_requires_article_structure_review',
                'authorization_note':resource['metadata'].get('authorization_note'),'news_record_key':resource['metadata']['news_record_key']}
        meta = resource['metadata']
        document_id = _put(ctx,resource,raw,'documents',resource['resource_key'],data,source_locator='whole original article response',
            published_at=meta.get('published_at'),publication_precision=meta.get('publication_precision','unknown'),
            publication_timezone=meta.get('publication_timezone'),quality='article_structure_unverified')
        result = persist_observations(ctx,resource,parse_document(content,media_type=media,document_type='news_article',source_url=resource['url']),raw)
        result['record_ids'].insert(0,document_id)
        result['parsed_count'] += 1
        return result


class FederalRegisterAdapter(BaseAdapter):
    source_id = 'federal_register'
    scope_kind = 'global'
    data_types = ('policy_documents',)
    endpoint = 'https://www.federalregister.gov/api/v1/documents.json'

    def _emit_query(self,ctx,params,page=1):
        identity = _hash({k:v for k,v in params.items() if k!='page'})
        ctx.emit({'source_id':self.source_id,'resource_key':f'search:{identity}:page:{page}', 'kind':'policy_search',
                  'data_type':'policy_documents','url':self.endpoint,'metadata':{'params':{**params,'page':page},'query_id':identity,'page':page,'refresh':True}})

    def discover(self,ctx,scope):
        config = ctx.config.get('policy',{})
        topics, agencies = config.get('topics',[]),config.get('agencies',[])
        if not topics and not agencies:
            gap = _gap(ctx,self.source_id,'POLICY_SCOPE_NOT_CONFIGURED','Configure policy.topics and/or policy.agencies; no implicit all-policy crawl.',data_type='policy_documents')
            return {'enumeration_complete':False,'discovered':0,'gaps':[gap]}
        base = {'per_page':100,'order':'relevance','conditions[publication_date][gte]':scope.get('start',ctx.config.get('start')),
                'conditions[publication_date][lte]':scope.get('end',ctx.config.get('end'))}
        count = 0
        for term in topics:
            self._emit_query(ctx,{**base,'conditions[term]':term if isinstance(term,str) else term['term']})
            count += 1
        for agency in agencies:
            self._emit_query(ctx,{**base,'conditions[agencies][]':agency if isinstance(agency,(str,int)) else agency['id']})
            count += 1
        return {'enumeration_complete':True,'discovered':count}

    def parse(self,ctx,resource,content,raw):
        if resource['kind'] == 'policy_body':
            return self._body(ctx,resource,content,raw)
        response = json.loads(content)
        if resource['kind'] == 'policy_detail':
            return self._detail(ctx,resource,response,raw)
        if not isinstance(response.get('results'),list):
            raise ValueError('Federal Register response lacks results array')
        params, page = resource['metadata']['params'],int(resource['metadata'].get('page',1))
        count, issues = 0, []
        total = int(response.get('count',0))
        # Federal Register broad-search result caps must not masquerade as a
        # full inventory. Automatically partition dated queries when possible.
        if total >= 10000:
            start,end = params.get('conditions[publication_date][gte]'),params.get('conditions[publication_date][lte]')
            if start and end and start < end:
                begin,finish = date.fromisoformat(start),date.fromisoformat(end)
                middle = begin + (finish-begin)//2
                self._emit_query(ctx,{**params,'conditions[publication_date][lte]':middle.isoformat()})
                self._emit_query(ctx,{**params,'conditions[publication_date][gte]':(middle+timedelta(days=1)).isoformat()})
                return _result(children=2,query_partitioned=True,source_count=total,enumeration_complete=False)
            issues.append(_gap(ctx,self.source_id,'POLICY_SEARCH_SOURCE_LIMIT','Search reports 10,000 or more documents; exact total and complete pagination cannot be assumed. Narrow the single-day query.',
                               data_type='policy_documents',status='partial_parse',params=params,reported_count=total))
        for item in response['results']:
            number = item.get('document_number')
            if not number or not re.fullmatch(r'[A-Za-z0-9-]+',number):
                issues.append(_gap(ctx,self.source_id,'POLICY_DOCUMENT_NUMBER_INVALID','Search item has no valid document number.',data_type='policy_documents',status='partial_parse',item=item))
                continue
            ctx.emit({'source_id':self.source_id,'resource_key':'detail:'+number,'kind':'policy_detail','data_type':'policy_documents',
                'url':f'https://www.federalregister.gov/api/v1/documents/{number}.json',
                'metadata':{'document_number':number,'search_resource_key':resource['resource_key'],'refresh':True}})
            count += 1
        next_url = response.get('next_page_url')
        if next_url:
            query = parse_qs(urlsplit(next_url).query)
            try:
                next_page = int(query['page'][0])
            except (KeyError,ValueError,TypeError):
                next_page = 0
            if not _government_url(next_url) or next_page <= page:
                issues.append(_gap(ctx,self.source_id,'POLICY_PAGINATION_INVALID','Untrusted or non-advancing next-page URL; remaining search inventory is incomplete.',data_type='policy_documents',status='partial_parse',next_url=next_url))
            else:
                self._emit_query(ctx,params,next_page)
                count += 1
        elif int(response.get('total_pages',page)) > page:
            self._emit_query(ctx,params,page+1)
            count += 1
        return _result(children=count,issues=issues,response_document_count=len(response['results']),source_count=total,
                       enumeration_complete=not next_url and not issues and int(response.get('total_pages',page)) <= page)

    def _detail(self,ctx,resource,response,raw):
        number = response.get('document_number',resource['metadata']['document_number'])
        if number != resource['metadata']['document_number']:
            raise ValueError('Federal Register detail document number mismatch')
        public_date = response.get('publication_date')
        if public_date:
            date.fromisoformat(public_date)
        data = dict(response, metadata_only=True, full_text_status='separate_inventory_resources',
                    historical_version_time_verified=False, legal_edition='HTML_informational_verify_official_govinfo_PDF',
                    correction_relationship_status='source_fields_only_no_inferred_link')
        record = _put(ctx,resource,raw,'policy_documents','metadata:'+number,data,source_locator='$',published_at=public_date,
                      publication_precision='day' if public_date else 'unknown',publication_timezone='America/New_York' if public_date else None,
                      event_date=response.get('effective_on'),quality='metadata_only')
        count,issues = 0,[]
        for field in ('body_html_url','full_text_xml_url','pdf_url'):
            url = response.get(field)
            if not url:
                continue
            if not _government_url(url):
                issues.append(_gap(ctx,self.source_id,'POLICY_BODY_URL_REJECTED','Document body URL is outside the supported government domains.',data_type='policy_documents',status='partial_parse',url=url))
                continue
            ctx.emit({'source_id':self.source_id,'resource_key':f'body:{number}:{field}','kind':'policy_body','data_type':'policy_documents','url':url,
                'metadata':{'document_number':number,'format_field':field,'title':response.get('title'),'published_at':public_date,
                'publication_precision':'day' if public_date else 'unknown','publication_timezone':'America/New_York' if public_date else None,
                'effective_on':response.get('effective_on'),'metadata_raw_id':raw['raw_id'],'refresh':True}})
            count += 1
        if not count:
            issues.append(_gap(ctx,self.source_id,'POLICY_FULL_TEXT_UNAVAILABLE','Detail response has no supported body/PDF endpoint. Abstract and search excerpts are not full text.',data_type='policy_documents',status='partial_parse',document_number=number))
        return _result([record],children=count,issues=issues,metadata_only=True)

    def _body(self,ctx,resource,content,raw):
        if not content.strip():
            raise ValueError('Empty policy body response')
        meta = resource['metadata']
        kind = meta['format_field']
        if kind == 'pdf_url' and not content.startswith(b'%PDF'):
            raise ValueError('Expected government PDF but response does not have a PDF signature')
        if kind != 'pdf_url' and re.search(br'<title[^>]*>\s*(?:Access Denied|Error|Forbidden)',content,re.I):
            raise ValueError('Policy body is an HTTP block/error page')
        text = visible_text(content) if kind != 'pdf_url' else None
        data = {'document_number':meta['document_number'],'title':meta.get('title'),'format_field':kind,'url':resource['url'],
                'full_text_status':'original_response_acquired','content_sha256':raw['sha256'],'metadata_raw_id':meta['metadata_raw_id'],
                'historical_version_time_verified':False,'publication_date':meta.get('published_at'),'effective_date':meta.get('effective_on'),
                'legal_edition':'official_govinfo_PDF' if kind=='pdf_url' and urlsplit(resource['url']).hostname.endswith('govinfo.gov') else 'informational_rendition',
                'text':text,'text_extraction_status':'not_performed_original_pdf_retained' if text is None else 'extracted_unverified'}
        record = _put(ctx,resource,raw,'policy_documents',resource['resource_key'],data,source_locator='whole original document',
            published_at=meta.get('published_at'),publication_precision=meta.get('publication_precision','unknown'),publication_timezone=meta.get('publication_timezone'),
            event_date=meta.get('effective_on'),quality='original_retained_needs_verification')
        return _result([record],original_full_response_acquired=True)


class BLSAdapter(BaseAdapter):
    source_id = 'bls'
    scope_kind = 'global'
    data_types = ('industry_metrics',)

    def discover(self,ctx,scope):
        series = ctx.config.get('industry',{}).get('bls_series',[])
        count,gaps = 0,[]
        if not series:
            gaps.append(_gap(ctx,self.source_id,'BLS_SERIES_NOT_CONFIGURED','Configure industry.bls_series with public BLS series IDs.',data_type='industry_metrics'))
        for item in series:
            spec = {'id':item} if isinstance(item,str) else dict(item)
            series_id = spec.get('id','')
            if not re.fullmatch(r'[A-Z0-9_#-]+',series_id):
                gaps.append(_gap(ctx,self.source_id,'BLS_SERIES_ID_INVALID','BLS series ID must use the documented uppercase/digit/underscore/dash/hash alphabet.',data_type='industry_metrics',series_id=series_id))
                continue
            ctx.emit({'source_id':self.source_id,'resource_key':'series:'+series_id,'kind':'bls_series','data_type':'industry_metrics',
                'url':'https://api.bls.gov/publicAPI/v2/timeseries/data/'+quote(series_id,safe=''),
                'metadata':{'series':spec,'start':scope.get('start',ctx.config.get('start')),'end':scope.get('end',ctx.config.get('end')),'refresh':True}})
            count += 1
        return {'enumeration_complete':not gaps,'discovered':count,'gaps':gaps}

    def parse(self,ctx,resource,content,raw):
        payload = json.loads(content)
        if payload.get('status') != 'REQUEST_SUCCEEDED':
            raise ValueError('BLS request not successful: '+json.dumps(payload.get('message',[])))
        results = payload.get('Results',{})
        envelopes = results if isinstance(results,list) else [results]
        series = [s for envelope in envelopes for s in envelope.get('series',[])]
        spec = resource['metadata']['series']
        start,end = resource['metadata'].get('start'),resource['metadata'].get('end')
        oldest_supported = f'{int(raw["retrieved_at"][:4])-2}-01-01'
        issues,ids = [],[]
        if start and start < oldest_supported:
            issues.append(_gap(ctx,self.source_id,'BLS_RECENT_GET_HISTORY_LIMIT','No-key single-series GET supplies recent three years only; requested earlier dates require a separate history adapter.',
                data_type='industry_metrics',start=start,end=end,earliest_get_year=oldest_supported,series_id=spec['id']))
        if not spec.get('unit'):
            issues.append(_gap(ctx,self.source_id,'BLS_UNIT_UNVERIFIED','Series response does not establish a machine-verified unit; numeric values retain unknown unit until series metadata is verified.',data_type='industry_metrics',series_id=spec['id']))
        if payload.get('message'):
            issues.append(_gap(ctx,self.source_id,'BLS_RESPONSE_MESSAGE','BLS supplied an advisory/error message; review coverage.',data_type='industry_metrics',messages=payload['message']))
        matched = False
        observed_periods = []
        for entry in series:
            if entry.get('seriesID') != spec['id']:
                issues.append(_gap(ctx,self.source_id,'BLS_SERIES_MISMATCH','Unexpected series returned by BLS; it was not imported.',data_type='industry_metrics',expected=spec['id'],actual=entry.get('seriesID')))
                continue
            matched = True
            for index,item in enumerate(entry.get('data',[])):
                try:
                    period_start,period_end,frequency = self._period(item)
                except (ValueError,TypeError,KeyError) as exc:
                    issues.append(_gap(ctx,self.source_id,'BLS_PERIOD_UNSUPPORTED',str(exc),data_type='industry_metrics',status='partial_parse',item=item))
                    continue
                observed_periods.append(period_end)
                if (start and period_end<start) or (end and period_start>end):
                    continue
                raw_value = item.get('value')
                try:
                    value = Decimal(str(raw_value).replace(',',''))
                    if not value.is_finite():
                        raise InvalidOperation
                    normalized = format(value,'f')
                except (InvalidOperation,ValueError):
                    normalized = None
                    issues.append(_gap(ctx,self.source_id,'BLS_VALUE_MISSING','BLS observation has no finite numeric value; missing value retained.',data_type='industry_metrics',status='partial_parse',item=item))
                data = {'series_id':spec['id'],'original_series_name':spec.get('name'),'value':normalized,'original_value':raw_value,
                    'year':item.get('year'),'period':item.get('period'),'period_name':item.get('periodName'),'frequency':frequency,
                    'footnotes':item.get('footnotes',[]),'source_item':item,'catalog':entry.get('catalog'),
                    'unit':spec.get('unit'),'unit_evidence_status':'configured_unverified' if spec.get('unit') else 'unknown',
                    'source_published_at':None,'source_revision_at':None,'vintage_status':'current_api_snapshot_only',
                    'historical_release_time_verified':False,'seasonal_adjustment':spec.get('seasonal_adjustment','unknown'),
                    'knowledge_type':'fact_observation'}
                ids.append(_put(ctx,resource,raw,'industry_metrics',f'{spec["id"]}|{item["year"]}|{item["period"]}',data,
                    source_locator=f'$.Results.series[seriesID={spec["id"]}].data[{index}]',period_start=period_start,period_end=period_end,
                    unit=spec.get('unit'),quality='current_vintage_unit_unverified' if normalized is not None else 'missing'))
        if not matched:
            issues.append(_gap(ctx,self.source_id,'BLS_SERIES_NOT_RETURNED','BLS successful envelope did not contain the configured series.',data_type='industry_metrics',series_id=spec['id']))
        elif not ids:
            issues.append(_gap(ctx,self.source_id,'BLS_NO_OBSERVATIONS_IN_SCOPE','No supported observations were returned within the requested date range; this is not complete series coverage.',data_type='industry_metrics',series_id=spec['id'],start=start,end=end))
        _gap(ctx,self.source_id,'BLS_HISTORICAL_VINTAGES_UNAVAILABLE','Current GET values do not supply original release timestamps or historical revision vintages; historical-public-time reconstruction is unsupported.',data_type='industry_metrics',series_id=spec['id'])
        return _result(ids,issues=issues,returned_period_start=min(observed_periods) if observed_periods else None,
                       returned_period_end=max(observed_periods) if observed_periods else None,historical_vintages_available=False)

    @staticmethod
    def _period(item):
        year,period = int(item['year']),item['period']
        if re.fullmatch(r'M(?:0[1-9]|1[0-2])',period):
            month = int(period[1:])
            return date(year,month,1).isoformat(),date(year,month,calendar.monthrange(year,month)[1]).isoformat(),'monthly'
        if period in {'M13','A01'}:
            return f'{year}-01-01',f'{year}-12-31','annual_average' if period=='M13' else 'annual'
        if re.fullmatch(r'Q0[1-4]',period):
            quarter = int(period[-1])
            month = quarter*3
            return date(year,month-2,1).isoformat(),date(year,month,calendar.monthrange(year,month)[1]).isoformat(),'quarterly'
        raise ValueError('Unsupported BLS period code: '+str(period))
