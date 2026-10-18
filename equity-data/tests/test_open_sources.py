"""Task graph and version/permission tests using synthetic source responses."""
import json
from types import SimpleNamespace

import pytest

from equity_data.adapters.open_sources import PublicNewsAdapter, FederalRegisterAdapter, BLSAdapter
from equity_data.store import Store


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path/'store')
    emitted,gaps = [],[]
    def emit(resource):
        emitted.append(resource)
        return 'resource:'+str(len(emitted))
    def gap(code,message,**details):
        gaps.append({'code':code,'message':message,**details})
    result = SimpleNamespace(store=store, config={'start':'2024-01-01','end':'2026-12-31'}, emit=emit, gap=gap,
                             emitted=emitted,gaps=gaps, client=None)
    yield result
    store.close()


def parse(adapter,ctx,resource,payload,minute=1):
    content = payload if isinstance(payload,bytes) else json.dumps(payload).encode()
    raw = ctx.store.put_raw(content,source=adapter.source_id,url=resource['url'],retrieved_at=f'2026-10-09T00:{minute:02d}:00Z')
    return adapter.parse(ctx,resource,content,raw)


def rss(count, *, change='Initial',source=''):
    items = ''.join(f'<item><title>Test Company {i}</title><link>https://news.test/story/{i}?utm_source=feed</link><pubDate>Mon, 03 Jun 2024 16:30:00 -0400</pubDate><description>{change}</description>{source}</item>' for i in range(count))
    return ('<rss version="2.0"><channel>'+items+'</channel></rss>').encode()


def test_news_all_entries_no_legacy_cap_and_permission_gap(ctx):
    ctx.config['news'] = {'feeds':['https://news.test/feed.xml']}
    ctx.config['securities'] = [{'name':'Test Company','company_id':'sec:test'}]
    adapter = PublicNewsAdapter()
    assert adapter.discover(ctx,{})['discovered'] == 1
    result = parse(adapter,ctx,ctx.emitted[0],rss(103))
    assert result['parsed_count'] == 103
    rows = ctx.store.query('news')
    assert len(rows) == 103
    assert all(r['data']['article_full_text_retrieved'] is False for r in rows)
    assert all(r['data']['canonical_url'].startswith('https://news.test/story/') for r in rows)
    assert all('utm_' not in r['data']['canonical_url'] for r in rows)
    assert all(r['data']['company_association_candidates'][0]['status']=='candidate_needs_review' for r in rows)
    assert len([g for g in ctx.gaps if g['code']=='NEWS_FULL_TEXT_PERMISSION_MISSING']) == 103
    assert rows[0]['published_at'] == '2024-06-03T20:30:00+00:00'
    assert rows[0]['data']['original_feed_item_xml'].startswith('<item>')
    assert ctx.store.query('news',as_of='2024-06-04') == []


def test_news_repeat_idempotent_and_revision_is_retained(ctx):
    ctx.config['news'] = {'feeds':['https://news.test/feed.xml']}
    adapter=PublicNewsAdapter()
    adapter.discover(ctx,{})
    resource=ctx.emitted[0]
    parse(adapter,ctx,resource,rss(1),1)
    parse(adapter,ctx,resource,rss(1),2)
    assert len(ctx.store.query('news',latest=False)) == 1
    parse(adapter,ctx,resource,rss(1,change='Updated'),3)
    rows=ctx.store.query('news',latest=False)
    assert len(rows)==2 and rows[1]['supersedes']==rows[0]['record_id']
    assert rows[1]['available_at']=='2026-10-09T00:03:00.000000Z'


def test_atom_times_independent_paginate_and_full_text_explicit(ctx):
    ctx.config['news']={'feeds':[{'url':'https://news.test/feed.xml','allow_full_text':True}]}
    adapter=PublicNewsAdapter()
    adapter.discover(ctx,{})
    content=b'''<feed xmlns="http://www.w3.org/2005/Atom"><link rel="next" href="/page2.xml"/><entry><title>Sample</title><link href="https://news.test/article"/><published>2024-06-03T16:30:00</published><updated>2024-06-04T12:00:00+02:00</updated><summary>Excerpt only</summary></entry></feed>'''
    result=parse(adapter,ctx,ctx.emitted[0],content)
    row=ctx.store.query('news')[0]
    assert row['published_at'] is None
    assert row['data']['updated_precision']=='second'
    assert row['data']['source_updated_at']=='2024-06-04T10:00:00+00:00'
    assert result['coverage']['children_discovered']==2
    assert {r['kind'] for r in ctx.emitted[1:]}=={'news_article','news_feed'}
    article=next(r for r in ctx.emitted if r['kind']=='news_article')
    parse(adapter,ctx,article,b'<html><body><h1>Sample</h1><p>News story.</p></body></html>',2)
    assert ctx.store.query('documents')[0]['data']['article_structure_verified'] is False


def test_news_current_feed_not_claimed_complete_archive(ctx):
    ctx.config['news']={'feeds':['https://news.test/feed']}
    adapter=PublicNewsAdapter()
    adapter.discover(ctx,{})
    result=parse(adapter,ctx,ctx.emitted[0],rss(0))
    assert result['coverage']['historical_enumeration_complete'] is False
    assert any(g['code']=='NEWS_HISTORICAL_ARCHIVE_UNVERIFIED' for g in ctx.gaps)


def test_feed_dtd_rejected_without_network(ctx):
    ctx.config['news']={'feeds':['https://news.test/feed']}
    adapter=PublicNewsAdapter();adapter.discover(ctx,{})
    with pytest.raises(ValueError):
        parse(adapter,ctx,ctx.emitted[0],b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss><channel/></rss>')
    assert ctx.store.db.execute('SELECT count(*) FROM raw_blobs').fetchone()[0]==1


def test_federal_register_enumerates_queries_results_and_next_page(ctx):
    ctx.config['policy']={'topics':['semiconductors'],'agencies':['commerce-department']}
    adapter=FederalRegisterAdapter()
    assert adapter.discover(ctx,{})['discovered']==2
    assert ctx.emitted[1]['metadata']['params']['conditions[agencies][]']=='commerce-department'
    response={'count':250,'total_pages':3,'next_page_url':'https://www.federalregister.gov/api/v1/documents?format=json&page=2',
              'results':[{'document_number':f'2024-{i:05d}'} for i in range(100)]}
    result=parse(adapter,ctx,ctx.emitted[0],response)
    assert result['coverage']['children_discovered']==101
    assert len([r for r in ctx.emitted if r['kind']=='policy_detail'])==100
    assert ctx.emitted[-1]['metadata']['page']==2
    assert result['coverage']['enumeration_complete'] is False


def test_federal_register_caps_partition_dates_not_silent_truncate(ctx):
    ctx.config['policy']={'topics':['all']}
    adapter=FederalRegisterAdapter();adapter.discover(ctx,{})
    result=parse(adapter,ctx,ctx.emitted[0],{'count':10000,'results':[]})
    assert result['coverage']['query_partitioned'] is True
    assert len(ctx.emitted)==3
    left=ctx.emitted[1]['metadata']['params'];right=ctx.emitted[2]['metadata']['params']
    assert left['conditions[publication_date][gte]']=='2024-01-01'
    assert right['conditions[publication_date][lte]']=='2026-12-31'
    assert left['conditions[publication_date][lte]'] < right['conditions[publication_date][gte]']


def test_policy_detail_emits_original_bodies_and_pdf(ctx):
    adapter=FederalRegisterAdapter()
    resource={'source_id':adapter.source_id,'resource_key':'detail:2024-12345','kind':'policy_detail','url':'https://www.federalregister.gov/api/v1/documents/2024-12345.json',
              'metadata':{'document_number':'2024-12345'}}
    payload={'document_number':'2024-12345','title':'Rule','abstract':'Not a full text','publication_date':'2024-06-03','effective_on':'2024-07-01',
             'body_html_url':'https://www.federalregister.gov/documents/full_text/2024-12345.html','pdf_url':'https://www.govinfo.gov/content/pkg/FR-2024-06-03/pdf/2024-12345.pdf'}
    result=parse(adapter,ctx,resource,payload)
    assert result['coverage']['children_discovered']==2
    row=ctx.store.query('policy_documents')[0]
    assert row['data']['metadata_only'] is True
    assert row['data']['full_text_status']=='separate_inventory_resources'
    body=next(r for r in ctx.emitted if r['metadata']['format_field']=='body_html_url')
    parse(adapter,ctx,body,b'<p>Actual rule body.</p>',2)
    rows=ctx.store.query('policy_documents')
    assert any(r['data'].get('text')=='Actual rule body.' for r in rows)
    assert ctx.store.query('policy_documents',as_of='2024-07-02')==[]


def test_policy_missing_body_is_explicit(ctx):
    adapter=FederalRegisterAdapter()
    resource={'source_id':adapter.source_id,'resource_key':'detail:X','kind':'policy_detail','url':'https://www.federalregister.gov/api/v1/documents/X.json',
              'metadata':{'document_number':'X'}}
    result=parse(adapter,ctx,resource,{'document_number':'X','abstract':'Only summary','html_url':'https://www.federalregister.gov/search'})
    assert result['status']=='partial'
    assert ctx.emitted==[]
    assert any(g['code']=='POLICY_FULL_TEXT_UNAVAILABLE' for g in ctx.gaps)


@pytest.mark.parametrize('envelope_list',[False,True])
def test_bls_envelope_periods_units_and_unknown_public_time(ctx,envelope_list):
    ctx.config['industry']={'bls_series':[{'id':'TEST0001','unit':'index','name':'Synthetic index'}]}
    adapter=BLSAdapter();adapter.discover(ctx,{})
    envelope={'series':[{'seriesID':'TEST0001','data':[{'year':'2025','period':'M02','periodName':'February','value':'101.23','footnotes':[{'code':'P','text':'Preliminary.'}]},
                                                  {'year':'2025','period':'M13','periodName':'Annual','value':'101.99','footnotes':[]}]}]}
    result=parse(adapter,ctx,ctx.emitted[0],{'status':'REQUEST_SUCCEEDED','Results':[envelope] if envelope_list else envelope})
    assert result['parsed_count']==2
    rows=ctx.store.query('industry_metrics')
    monthly=next(r for r in rows if r['data']['period']=='M02')
    assert monthly['period_start']=='2025-02-01' and monthly['period_end']=='2025-02-28'
    assert monthly['data']['value']=='101.23'
    assert monthly['unit']=='index' and monthly['data']['unit_evidence_status']=='configured_unverified'
    assert monthly['published_at'] is None
    assert ctx.store.query('industry_metrics',as_of='2025-03-01')==[]


def test_bls_recent_limit_invalid_period_and_revision(ctx):
    ctx.config['start']='2010-01-01'
    ctx.config['industry']={'bls_series':['TEST0001']}
    adapter=BLSAdapter();adapter.discover(ctx,{})
    resource=ctx.emitted[0]
    data={'status':'REQUEST_SUCCEEDED','Results':{'series':[{'seriesID':'TEST0001','data':[{'year':'2025','period':'M02','value':'10'},
                                                                                     {'year':'2025','period':'BAD','value':'99'}]}]}}
    first=parse(adapter,ctx,resource,data)
    assert first['status']=='partial'
    assert {'BLS_RECENT_GET_HISTORY_LIMIT','BLS_UNIT_UNVERIFIED','BLS_PERIOD_UNSUPPORTED'} <= {g['code'] for g in ctx.gaps}
    parse(adapter,ctx,resource,data,2)
    assert len(ctx.store.query('industry_metrics',latest=False))==1
    data['Results']['series'][0]['data'][0]['value']='11'
    parse(adapter,ctx,resource,data,3)
    rows=ctx.store.query('industry_metrics',latest=False)
    assert len(rows)==2 and rows[-1]['data']['value']=='11'
    assert rows[-1]['supersedes']==rows[0]['record_id']


def test_bls_failure_envelope_does_not_become_success(ctx):
    ctx.config['industry']={'bls_series':['TEST0001']}
    adapter=BLSAdapter();adapter.discover(ctx,{})
    with pytest.raises(ValueError,match='not successful'):
        parse(adapter,ctx,ctx.emitted[0],{'status':'REQUEST_FAILED','message':['Daily limit exceeded']})
    assert ctx.store.query('industry_metrics')==[]
