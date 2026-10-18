"""Synthetic mutation tests. No issuer values or held-out documents are used."""
import hashlib

import pytest

from equity_data.parsers import parse_document, PARSER_VERSION


def ix_document(value="1,234.5", attributes='', context=None, unit=None):
    return f'''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:test="urn:test" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body>
    <ix:header><ix:resources>
    {context or '<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="sec">0000000123</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="test:ProductAxis">test:WidgetMember</xbrldi:explicitMember><xbrldi:typedMember dimension="test:RegionAxis"><test:Area>West</test:Area></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-02-01</xbrli:startDate><xbrli:endDate>2025-04-30</xbrli:endDate></xbrli:period></xbrli:context>'}
    {unit or '<xbrli:unit id="u1"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'}
    </ix:resources></ix:header><p>Custom metric:
    <ix:nonFraction id="fact1" name="test:CloudCommitments" contextRef="c1" unitRef="u1" format="ixt:num-dot-decimal" scale="6" decimals="-5" {attributes}>{value}</ix:nonFraction>
    </p></body></html>'''.encode()


def guidance(text):
    return parse_document(f'<html><body><p>{text}</p></body></html>'.encode(), source_url='https://issuer.test/quarter')


def only_guidance(result):
    return [o for o in result['observations'] if o['dataset'] == 'guidance']


def test_inline_custom_concept_context_dimensions_scale_and_evidence():
    content = ix_document(attributes='sign="-"')
    result = parse_document(content)
    row = result['observations'][0]
    assert row['data']['value'] == '-1234500000.0'
    assert row['data']['concept_qname'] == 'test:CloudCommitments'
    assert row['data']['comparable_metric'] is None
    assert row['data']['dimensions'][0]['member'] == 'test:WidgetMember'
    assert row['data']['dimensions'][1]['member'] == 'West'
    assert row['period_start'] == '2025-02-01'
    assert row['period_end'] == '2025-04-30'
    assert row['currency'] == 'USD'
    assert row['data']['content_sha256'] == hashlib.sha256(content).hexdigest()
    assert row['data']['parser_version'] == PARSER_VERSION
    assert row['source_locator'] and row['evidence_excerpt'] == '1,234.5'
    assert row['data']['context_locator'] and row['data']['unit_locator']


@pytest.mark.parametrize('before,after,expected', [
    ('1,234.5', '2,345.6', '2345600000.0'),
    ('scale="6"', 'scale="3"', '1234500.0'),
    ('format="ixt:num-dot-decimal"', 'format="ixt:numcommadecimal"', None),
    ('format="ixt:num-dot-decimal"', 'format="ixt:unknown-transform"', None),
    ('contextRef="c1"', 'contextRef="missing"', None),
    ('unitRef="u1"', 'unitRef="missing"', None),
    ('2025-04-30', 'not-a-date', None),
    ('decimals="-5"', 'decimals="nonsense"', None),
])
def test_numeric_mutation_reparse_or_reject(before, after, expected):
    result = parse_document(ix_document().replace(before.encode(), after.encode()))
    facts = [o for o in result['observations'] if o['dataset'] == 'financial_facts']
    if expected is None:
        assert facts == []
        assert result['issues'][0]['code'] == 'IXBRL_NUMERIC_REJECTED'
    else:
        assert facts[0]['data']['value'] == expected


def test_comma_decimal_divide_unit_and_instant():
    content = ix_document('1.234,50', context='<xbrli:context id="c1"><xbrli:period><xbrli:instant>2025-05-01</xbrli:instant></xbrli:period></xbrli:context>',
        unit='<xbrli:unit id="u1"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>')
    result = parse_document(content.replace(b'num-dot-decimal', b'num-comma-decimal').replace(b'scale="6"', b'scale="0"'))
    row = result['observations'][0]
    assert row['data']['value'] == '1234.50'
    assert row['unit'] == 'iso4217:EUR/xbrli:shares'
    assert row['currency'] == 'EUR'
    assert row['period_start'] is None and row['period_end'] == '2025-05-01'


def test_nil_is_missing_not_zero_and_exclude_is_ignored():
    nil = parse_document(ix_document('', attributes='xsi:nil="true"'))['observations'][0]
    assert nil['data']['value'] is None
    assert nil['data']['value_status'] == 'source_nil'
    included = parse_document(ix_document('1<ix:exclude>footnote 20</ix:exclude>,234.5'))['observations'][0]
    assert included['data']['value'] == '1234500000.0'


def test_duplicate_context_is_rejected_not_last_value_wins():
    context = '<xbrli:context id="c1"><xbrli:period><xbrli:instant>2025-01-01</xbrli:instant></xbrli:period></xbrli:context>'
    result = parse_document(ix_document(context=context + context.replace('2025-01-01', '2024-01-01')))
    assert result['observations'] == []
    assert result['coverage']['inline_xbrl']['numeric_rejected'] == 1


def test_guidance_point_tolerance_period_and_version_evidence():
    result = guidance('For the fourth quarter of fiscal 2027, revenue is expected to be USD 30 billion, plus or minus 2%.')
    row = only_guidance(result)[0]
    assert row['data']['point'] == '30000000000'
    assert row['data']['low'] is None and row['data']['high'] is None
    assert row['data']['tolerance'] == {'value': '2', 'unit': 'percent', 'basis': 'relative_to_point'}
    assert row['data']['target_period']['fiscal_year'] == 2027
    assert row['period_end'] is None  # fiscal year is not calendar year
    assert row['unit'] == 'USD'
    assert '30 billion' in row['evidence_excerpt'] and '2%' in row['evidence_excerpt']


@pytest.mark.parametrize('old,new,path,expected', [
    ('30 billion', '31 billion', ('point',), '31000000000'),
    ('30 billion', '30 million', ('point',), '30000000'),
    ('2%', '4%', ('tolerance', 'value'), '4'),
    ('2%', '25 basis points', ('tolerance', 'unit'), 'basis_points'),
    ('2027', '2028', ('target_period', 'fiscal_year'), 2028),
    ('USD', 'EUR', ('currency',), 'EUR'),
])
def test_guidance_source_mutation_never_reuses_value(old, new, path, expected):
    original = 'For the fourth quarter of fiscal 2027, revenue is expected to be USD 30 billion, plus or minus 2%.'
    before = only_guidance(guidance(original))[0]
    after = only_guidance(guidance(original.replace(old, new)))[0]
    assert before['record_key'] == after['record_key']
    assert before['data']['content_sha256'] != after['data']['content_sha256']
    value = after['data']
    for key in path:
        value = value[key]
    assert value == expected


@pytest.mark.parametrize('text', [
    'Revenue is expected to be USD 30 billion, plus or minus 2%.',
    'For fiscal 2027, revenue is expected to be 30 billion.',
    'For fiscal 2027, revenue is expected to be USD 30 billion, plus or minus 2.',
    'For fiscal 2027, revenue is expected to be USD 30 billion, plus or minus something.',
    'For fiscal 2027, revenue is expected to be USD 30 billion; for fiscal 2028 it will be USD 40 billion.',
    'For fiscal 2027, revenue is expected to be USD 30 billion and EPS USD 20.',
])
def test_missing_or_ambiguous_guidance_rejects_without_invention(text):
    result = guidance(text)
    assert only_guidance(result) == []
    assert any(i['code'] == 'GUIDANCE_REJECTED' for i in result['issues'])


def test_simple_guidance_table_uses_structural_adapter_with_cell_evidence():
    source = b'<html><body><h2>Outlook for Q2 fiscal 2028</h2><table><tr><td>Revenue</td><td>USD 10 to USD 12 billion</td></tr></table></body></html>'
    result = parse_document(source)
    row = only_guidance(result)[0]
    assert row['data']['low'] == '10000000000' and row['data']['high'] == '12000000000'
    assert row['data']['extraction_method'] == 'rectangular_table_guidance_v1'
    assert row['data']['target_period']['label'] == 'Q2 fiscal 2028'
    assert len(row['data']['evidence_components']) == 3
    assert row['data']['original_value_cell'] == 'USD 10 to USD 12 billion'
    assert result['issues'] == []


def test_explicit_date_and_per_share_guidance():
    result = guidance('For the quarter ending January 31, 2028, diluted EPS is expected to be USD 2.00 to USD 2.50.')
    row = only_guidance(result)[0]
    assert row['period_end'] == '2028-01-31'
    assert row['unit'] == 'USD/share'
    assert row['data']['low'] == '2.00' and row['data']['high'] == '2.50'


def test_narrative_does_not_invent_customer_or_amount():
    result = guidance('One customer accounted for 24% of revenue. Purchase commitments exist with suppliers. Notes mature in 2030.')
    narratives = [o for o in result['observations'] if o['data'].get('extraction_method') == 'narrative_evidence_candidate']
    assert {'customer_concentration', 'supply_chain', 'commitments', 'debt_maturities'} <= {o['data']['semantic_type'] for o in narratives}
    assert all(o['data']['counterparty_name'] is None and o['data']['numeric_values'] is None for o in narratives)
    assert all(o['evidence_excerpt'] and o['source_locator'] for o in narratives)
    assert {'relations','commitments','debt_maturities'} <= {o['dataset'] for o in narratives}


def test_company_identifier_cannot_change_parser_rules():
    content = ix_document()
    a = parse_document(content, company_id='sec:new-company', source_url='https://issuer.test/doc')
    b = parse_document(content, company_id='sec:another-company', source_url='https://issuer.test/doc')
    assert a == b


def test_no_text_or_unsupported_media_is_not_fabricated():
    result = parse_document(b'abc', media_type='image/png')
    assert result['observations'] == []
    assert result['issues'][0]['code'] == 'UNSUPPORTED_MEDIA_TYPE'
    assert result['coverage']['document_semantically_complete'] is False


def test_entities_are_not_resolved():
    result = parse_document(b'<!DOCTYPE html [<!ENTITY x SYSTEM "file:///secret">]><html><p>&x;</p></html>')
    assert result['observations'] == []
    assert result['issues'][0]['code'] == 'EXTERNAL_ENTITY_REJECTED'


def test_unknown_transformation_namespace_is_rejected():
    content = ix_document().replace(b'http://www.xbrl.org/inlineXBRL/transformation/2020-02-12', b'https://untrusted.test/transform')
    result = parse_document(content)
    assert result['observations'] == []
    assert 'namespace' in result['issues'][0]['message']


def test_conflicting_equivalent_facts_mark_both_values():
    content = ix_document()
    fact = b'<ix:nonFraction name="test:CloudCommitments" contextRef="c1" unitRef="u1" scale="6">99</ix:nonFraction>'
    result = parse_document(content.replace(b'</body>', fact + b'</body>'))
    facts = [o for o in result['observations'] if o['dataset'] == 'financial_facts']
    assert len(facts) == 2
    assert all(o['quality'] == 'source_conflict_requires_review' for o in facts)
    assert result['issues'][0]['code'] == 'IXBRL_CONFLICTING_FACTS'


def test_equal_decimal_values_with_different_lexical_precision_are_not_conflicts():
    content = ix_document('1,234.0')
    fact = b'<ix:nonFraction name="test:CloudCommitments" contextRef="c1" unitRef="u1" scale="6">1234</ix:nonFraction>'
    result = parse_document(content.replace(b'</body>', fact + b'</body>'))
    assert not any(issue['code']=='IXBRL_CONFLICTING_FACTS' for issue in result['issues'])


def test_dollar_symbol_does_not_silently_assume_currency():
    row = only_guidance(guidance('For fiscal 2028 revenue is expected to be $10 billion.'))[0]
    assert row['unit'] == 'dollar_symbol' and row['currency'] is None


def test_unsupported_custom_guidance_has_explicit_gap():
    result = guidance('For fiscal 2028 we expect contracted backlog of USD 50 million.')
    assert only_guidance(result) == []
    assert result['issues'][0]['code'] == 'GUIDANCE_METRIC_UNSUPPORTED'


def document_set():
    source = ix_document()
    begin,end = source.index(b'<ix:header>'),source.index(b'</ix:header>')+len(b'</ix:header>')
    facts = source[:begin]+source[end:]
    support = source[:begin]+source[begin:end]+b'</body></html>'
    digest = hashlib.sha256(support).hexdigest()
    return facts,{'content':support,'sha256':digest,'raw_id':'sha256:'+digest,'source_url':'https://issuer.test/same-filing/context.htm'}


def test_document_set_uses_evidenced_shared_context_and_units_only():
    facts,support = document_set()
    without = parse_document(facts)
    assert without['observations']==[] and without['issues'][0]['code']=='IXBRL_NUMERIC_REJECTED'
    result = parse_document(facts,source_url='https://issuer.test/same-filing/facts.htm',context_documents=[support])
    row = result['observations'][0]
    assert row['data']['value']=='1234500000.0'
    assert row['data']['context_evidence']['raw_id']==support['raw_id']
    assert row['data']['context_evidence']['sha256']==support['sha256']
    assert row['data']['unit_evidence']['source_url']==support['source_url']
    assert row['data']['supporting_raw_ids']==[support['raw_id']]
    assert result['coverage']['supporting_raw_ids']==[support['raw_id']]
    assert row['data']['content_sha256']==hashlib.sha256(facts).hexdigest()


def test_document_set_repeated_identical_definitions_allowed_and_support_facts_not_reemitted():
    facts,support=document_set()
    # Two distinct raw documents repeat identical resource definitions.
    extra={**support,'content':support['content'].replace(b'</body>',b'<p>Other source document</p></body>')}
    extra['sha256']=hashlib.sha256(extra['content']).hexdigest();extra['raw_id']='sha256:'+extra['sha256']
    result=parse_document(facts,context_documents=[support,extra,support])
    assert result['coverage']['inline_xbrl']['numeric_parsed']==1
    assert result['coverage']['inline_xbrl']['context_documents_supplied']==2
    assert result['issues']==[]


def test_conflicting_cross_document_context_rejects_numeric_fact():
    facts,support=document_set()
    conflict={**support,'content':support['content'].replace(b'2025-04-30',b'2025-05-31')}
    conflict['sha256']=hashlib.sha256(conflict['content']).hexdigest();conflict['raw_id']='sha256:'+conflict['sha256']
    result=parse_document(facts,context_documents=[support,conflict])
    assert result['observations']==[]
    assert 'Conflicting context/unit definitions across document set' in result['issues'][0]['message']


def test_supporting_hash_mismatch_is_rejected():
    facts,support=document_set()
    support['content']=support['content'].replace(b'2025-04-30',b'2025-05-31')
    result=parse_document(facts,context_documents=[support])
    assert result['observations']==[]
    assert result['issues'][0]['code']=='IXBRL_SUPPORT_DOCUMENT_REJECTED'


def test_supporting_document_period_mutation_changes_parsed_period():
    facts,support=document_set()
    first=parse_document(facts,context_documents=[support])['observations'][0]
    support['content']=support['content'].replace(b'2025-04-30',b'2025-05-31')
    support['sha256']=hashlib.sha256(support['content']).hexdigest();support['raw_id']='sha256:'+support['sha256']
    second=parse_document(facts,context_documents=[support])['observations'][0]
    assert first['record_key']==second['record_key']
    assert first['period_end']=='2025-04-30' and second['period_end']=='2025-05-31'
    assert first['data']['supporting_raw_ids'] != second['data']['supporting_raw_ids']


@pytest.mark.parametrize('text',['—','Not applicable',''])
def test_fixed_zero_uses_explicit_official_transform(text):
    content=ix_document(text).replace(b'num-dot-decimal',b'fixed-zero')
    result=parse_document(content)
    row=result['observations'][0]
    assert row['data']['value']=='0'
    assert row['evidence_excerpt']
    if not text:
        assert row['evidence_excerpt'].encode() in content
        assert row['data']['evidence_excerpt_kind']=='original_tag_markup'


def test_fixed_zero_unknown_registry_not_permitted():
    content=ix_document('-').replace(b'num-dot-decimal',b'fixed-zero').replace(b'2020-02-12',b'1900-01-01')
    result=parse_document(content)
    assert result['observations']==[]
    assert result['issues'][0]['code']=='IXBRL_NUMERIC_REJECTED'


@pytest.mark.parametrize('unit,metric,period,qualifier,amount',[
    ('Amounts in millions','Net Income','Fiscal 2031','~','426'),
    ('Amounts in thousands','Net Income','Fiscal 2031','~','426'),
    ('Amounts in billions','Operating Income','Fiscal 2032','approximately','42.6'),
    ('EUR in millions','Revenue','Fiscal 2030','at least','525'),
    ('USD in billions','Free Cash Flow','Q2 fiscal 2032','less than','61'),
    ('','Revenue','Fiscal 2033','','575'),
])
def test_guidance_table_mutations_reject_inherited_scale_scope_period_and_qualifier(unit,metric,period,qualifier,amount):
    source=f'''<html><body><table><tr><th colspan="3">Example Segment Outlook for {period}</th></tr>
    <tr><td colspan="3">{unit}</td></tr><tr><td>Consumer Services {metric}</td><td>{qualifier}</td><td>$ {amount}</td></tr>
    </table></body></html>'''.encode()
    result=parse_document(source)
    assert only_guidance(result)==[]
    issue=next(item for item in result['issues'] if item['code']=='GUIDANCE_TABLE_CONTEXT_UNSUPPORTED')
    excerpt=issue['details']['evidence_excerpt']
    assert unit in excerpt and period in excerpt and metric in excerpt and amount in excerpt
    assert qualifier in excerpt and 'Consumer Services' in excerpt
    assert result['coverage']['guidance_rejected']>=1


@pytest.mark.parametrize('body',[
    '<tr><td><p>For fiscal 2032 revenue is expected to be USD 14 billion.</p></td></tr>',
    '<caption>Outlook for fiscal 2032</caption><tr><td>Revenue expected USD 14 billion for fiscal 2032</td></tr>',
    '<tr><td rowspan="2">North Division</td><td>For fiscal 2032 revenue expected USD 14 billion</td></tr>',
    '<tr><td><table><tr><td>For fiscal 2032 revenue expected USD 14 billion</td></tr></table></td></tr>',
])
def test_nested_and_merged_table_layouts_cannot_bypass_rejection(body):
    result=parse_document(('<html><body><table>'+body+'</table></body></html>').encode())
    assert only_guidance(result)==[]
    assert any(i['code']=='GUIDANCE_TABLE_CONTEXT_UNSUPPORTED' for i in result['issues'])


def test_table_context_does_not_leak_to_following_prose_or_other_table():
    content=b'''<html><body><table><tr><th>Segment Outlook for fiscal 2032</th></tr><tr><td>USD in millions</td></tr>
    <tr><td>Revenue USD 14</td></tr></table><p>Revenue USD 18 billion.</p>
    <table><tr><td>Revenue USD 20 billion</td></tr></table></body></html>'''
    result=parse_document(content)
    assert only_guidance(result)==[]
    assert result['coverage']['guidance_parsed']==0


def test_prose_numeric_guidance_still_reparsed_when_table_guidance_is_rejected():
    content=b'''<html><body><table><tr><th>Outlook for fiscal 2032</th></tr><tr><td>Revenue USD 14</td></tr></table>
    <p>For fiscal 2033 revenue is expected to be USD 22 billion.</p></body></html>'''
    result=parse_document(content)
    rows=only_guidance(result)
    assert len(rows)==1 and rows[0]['data']['point']=='22000000000'
    assert rows[0]['data']['target_period']['fiscal_year']==2033
    assert any(i['code']=='GUIDANCE_TABLE_CONTEXT_UNSUPPORTED' for i in result['issues'])


@pytest.mark.parametrize('amount,expected', [
    ('USD -2.50', '-2.50'), ('-USD 2.50', '-2.50'), ('USD −2.50', '-2.50'),
    ('(USD 2.50)', '-2.50'), ('USD (2.50)', '-2.50'), ('USD +2.50', '2.50'),
    ('(USD 2.50 million)', '-2500000.00'), ('USD (2.50) million', '-2500000.00'),
])
def test_signed_guidance_preserves_explicit_sign_and_parentheses(amount, expected):
    result = guidance(f'For fiscal 2031 EPS is expected to be {amount}.')
    assert only_guidance(result)[0]['data']['point'] == expected


@pytest.mark.parametrize('amount', ['USD (2.50', 'USD 2.50)', '--USD 2.50', '-(USD 2.50)', 'USD (-2.50)', '-2.50 to USD 1.50'])
def test_malformed_or_unassigned_signs_and_unqualified_range_do_not_emit_positive(amount):
    result = guidance(f'For fiscal 2031 EPS is expected to be {amount}.')
    assert only_guidance(result) == []
    assert any(item['code'] == 'GUIDANCE_REJECTED' for item in result['issues'])


@pytest.mark.parametrize('amount,low,high', [
    ('USD -2.50 to USD -1.50', '-2.50', '-1.50'),
    ('(USD 2.50) to (USD 1.50)', '-2.50', '-1.50'),
    ('USD -2.50 to USD 1.50', '-2.50', '1.50'),
])
def test_signed_range_order_is_checked_after_sign_interpretation(amount, low, high):
    result = guidance(f'For fiscal 2031 EPS is expected to be {amount}.')
    row = only_guidance(result)[0]
    assert (row['data']['low'], row['data']['high']) == (low, high)


def test_reversed_negative_bounds_rejected():
    result = guidance('For fiscal 2031 EPS is expected to be USD -1.50 to USD -2.50.')
    assert only_guidance(result) == []
    assert 'reversed' in result['issues'][0]['message']


def test_mixed_dom_collects_container_text_tails_headings_and_rows_exactly_once():
    source = b'''<html><head><title>EXCLUDED_HEAD</title></head><body><header>VISIBLE_HEADER</header>
    BODY_START<p>PARAGRAPH <span>INLINE</span></p>BODY_TAIL
    <div>DIV_START<p>NESTED_PARAGRAPH</p>DIV_TAIL<span>INLINE_TAIL</span></div>
    <section>SECTION_TEXT<table><tr><td>CELL_A</td><td><p>CELL_B</p></td></tr></table>SECTION_TAIL</section>
    <div hidden>EXCLUDED_HIDDEN</div><script>EXCLUDED_SCRIPT</script></body></html>'''
    result = parse_document(source)
    combined = ' '.join(block['text'] for block in result['text_blocks'])
    for token in ['VISIBLE_HEADER', 'BODY_START', 'PARAGRAPH', 'INLINE', 'BODY_TAIL', 'DIV_START', 'NESTED_PARAGRAPH', 'DIV_TAIL', 'INLINE_TAIL', 'SECTION_TEXT', 'CELL_A', 'CELL_B', 'SECTION_TAIL']:
        assert combined.split().count(token) == 1
    assert 'EXCLUDED' not in combined


def table_document(value='(2.50)', unit='USD in millions', period='fiscal 2031'):
    return f'<html><body><h2>Outlook</h2><table><tr><th>Metric</th><th>{period} ({unit})</th></tr><tr><td>Revenue</td><td>{value}</td></tr></table></body></html>'.encode()


@pytest.mark.parametrize('value,unit,period,expected', [
    ('(2.50)', 'USD in millions', 'fiscal 2031', '-2500000.00'),
    ('-2.50', 'EUR in thousands', 'Q2 fiscal 2032', '-2500.00'),
    ('approximately 2.50', 'USD in billions', 'year ending 2031-12-31', '2500000000.00'),
])
def test_rectangular_table_inherits_evidenced_column_unit_scale_and_period(value, unit, period, expected):
    result = parse_document(table_document(value, unit, period))
    row = only_guidance(result)[0]
    assert row['data']['point'] == expected
    assert row['data']['target_period']['label'] == period
    assert unit in row['evidence_excerpt']
    assert row['data']['fact_stage'] == 'extracted_fact'
    assert row['data']['extraction_method'] == 'rectangular_table_guidance_v1'


def test_table_column_reordering_and_range_do_not_mix_periods():
    source = b'''<html><body><h2>Guidance</h2><table><tr><th>Metric</th><th>fiscal 2032 USD in millions</th><th>fiscal 2031 USD in millions</th></tr>
    <tr><td>Revenue</td><td>10 to 12</td><td>5</td></tr></table></body></html>'''
    rows = only_guidance(parse_document(source))
    assert len(rows) == 2
    assert [(row['data']['target_period']['year'], row['data']['point'], row['data']['low'], row['data']['high']) for row in rows] == [(2032, None, '10000000', '12000000'), (2031, '5000000', None, None)]


def test_table_linked_condition_footnote_retains_original_target():
    source = table_document('2.50<sup><a href="#note1">1</a></sup>').replace(b'</body>', b'<p id="note1">1. Assumes stable exchange rates.</p></body>')
    row = only_guidance(parse_document(source))[0]
    assert row['data']['point'] == '2500000.00'
    assert 'Assumes stable exchange rates' in row['data']['conditions_text']
    assert any(item.get('role') == 'condition_footnote' and item['source_locator'].endswith('/p') for item in row['data']['evidence_components'])


@pytest.mark.parametrize('value,unit', [
    ('EUR 2.50', 'USD in millions'), ('2.50 billion', 'USD in millions'),
    ('2.50<sup>1</sup>', 'USD in millions'), ('2.50', 'USD in millions except per share'),
])
def test_ambiguous_table_units_and_footnotes_fail_closed(value, unit):
    result = parse_document(table_document(value, unit))
    assert only_guidance(result) == []
    assert any(item['code'] == 'GUIDANCE_TABLE_CONTEXT_UNSUPPORTED' for item in result['issues'])


@pytest.mark.parametrize('amount', ['USD 1e3', 'USD 2.5.1', '–USD 2.50', '—USD 2.50'])
def test_unsupported_numeric_notation_is_not_partially_parsed(amount):
    result = guidance(f'For fiscal 2031 EPS is expected to be {amount}.')
    assert only_guidance(result) == []
    assert result['issues']


@pytest.mark.parametrize('basis', ['GAAP', 'non-GAAP'])
def test_table_header_accounting_basis_is_preserved(basis):
    raw = table_document('2.50', f'{basis} USD').replace(b'<td>Revenue</td>', b'<td>EPS</td>')
    row = only_guidance(parse_document(raw))[0]
    assert row['data']['accounting_basis'] == basis
    assert row['data']['semantic_contract']['accounting_basis'] == basis
    assert basis in row['evidence_excerpt']


def test_conflicting_row_and_column_accounting_basis_rejects():
    raw = table_document('2.50', 'non-GAAP USD').replace(b'<td>Revenue</td>', b'<td>GAAP EPS</td>')
    result = parse_document(raw)
    assert only_guidance(result) == []
    assert any('bases conflict' in issue['message'] for issue in result['issues'])


@pytest.mark.parametrize('heading', ['Americas Outlook', 'Manufacturing Outlook', 'Forecast for Division A'])
def test_unresolved_entity_or_region_in_header_rejects(heading):
    raw = table_document().replace(b'<h2>Outlook</h2>', f'<h2>{heading}</h2>'.encode())
    result = parse_document(raw)
    assert only_guidance(result) == []
    assert any(issue['code'] == 'GUIDANCE_TABLE_CONTEXT_UNSUPPORTED' for issue in result['issues'])


@pytest.mark.parametrize('qualifier', ['at least', 'less than', 'approximately', '~'])
def test_table_point_qualifiers_remain_part_of_fact(qualifier):
    row = only_guidance(parse_document(table_document(f'{qualifier} 2.50')))[0]
    assert row['data']['value_qualifier'] == qualifier
    assert qualifier in row['data']['original_value_cell']
    if qualifier == 'at least':
        assert row['data']['point'] is None and row['data']['low'] == '2500000.00'
        assert row['data']['bound_inclusivity']['lower'] is True
    elif qualifier == 'less than':
        assert row['data']['point'] is None and row['data']['high'] == '2500000.00'
        assert row['data']['bound_inclusivity']['upper'] is False
    else:
        assert row['data']['value_semantics'] == 'approximate_point'


def test_table_unit_altering_footnote_is_explicitly_rejected():
    raw = table_document('2.50<sup><a href="#note1">1</a></sup>').replace(b'</body>', b'<p id="note1">1. Assumes amounts in thousands except per share.</p></body>')
    result = parse_document(raw)
    assert only_guidance(result) == []
    assert any('Footnote' in issue['message'] for issue in result['issues'])


@pytest.mark.parametrize('marker', ['hidden', 'style="display:none"', 'style="visibility:hidden"', 'style="opacity:0"'])
def test_hidden_table_sign_rejected_without_visible_text_sign_leak(marker):
    result = parse_document(table_document(f'<span {marker}>-</span>5'))
    assert only_guidance(result) == []
    assert any('visibility' in issue['message'] for issue in result['issues'])
    assert all('- 5' not in block['text'] and '-5' not in block['text'] for block in result['text_blocks'])


def test_hidden_table_row_does_not_emit_hidden_or_visible_guidance_as_complete():
    source = table_document('5').replace(b'<tr><td>Revenue</td>', b'<tr hidden><td>Revenue</td><td>-5</td></tr><tr><td>Revenue</td>')
    result = parse_document(source)
    assert only_guidance(result) == []
    assert all('-5' not in block['text'] for block in result['text_blocks'])
    assert any('visibility' in issue['message'] for issue in result['issues'])


def test_class_stylesheet_hidden_sign_rejected_without_css_render_claim():
    source = table_document('<span class="hide">-</span>5').replace(b'<body>', b'<head><style>.hide {display:none}</style></head><body>')
    result = parse_document(source)
    assert only_guidance(result) == []
    assert any('stylesheet visibility' in issue['message'] for issue in result['issues'])


@pytest.mark.parametrize('unit', ['percent millions', 'percent thousands', 'USD percent', 'basis points billions'])
def test_table_incompatible_magnitude_and_percentage_rejected(unit):
    source = table_document('5', unit).replace(b'Revenue', b'Operating margin')
    result = parse_document(source)
    assert only_guidance(result) == []
    assert result['issues']


@pytest.mark.parametrize('cue', ['a loss of', 'negative', 'a deficit of'])
def test_lexical_negative_semantics_do_not_emit_positive(cue):
    result = guidance(f'We expect net income for fiscal 2025 to be {cue} USD 5 million.')
    assert only_guidance(result) == []
    assert any('Lexical' in issue['message'] for issue in result['issues'])


def test_actual_period_interrupts_heading_before_later_table():
    source = b'<h2>Outlook fiscal 2025</h2><p>Actual results for fiscal 2024 are shown below.</p><table><tr><th>Metric</th><th>USD millions</th></tr><tr><td>Revenue</td><td>5</td></tr></table>'
    result = parse_document(source)
    assert only_guidance(result) == []
    issue = next(issue for issue in result['issues'] if issue['code'] == 'GUIDANCE_CONTEXT_INTERRUPTED')
    assert '2024' in issue['details']['evidence_excerpt'] and '2025' in issue['details']['previous_heading']


def test_unrelated_prose_interrupts_stale_guidance_heading():
    source = table_document('5').replace(b'<table>', b'<p>We now discuss our prior operations.</p><table>').replace(b'fiscal 2031', b'')
    result = parse_document(source)
    assert only_guidance(result) == []
    assert any(issue['code'] == 'GUIDANCE_CONTEXT_INTERRUPTED' for issue in result['issues'])


def test_hidden_ixbrl_fact_remains_valid_separate_from_visible_text():
    source = ix_document().replace(b'<p>Custom metric:', b'<p hidden>Custom metric:')
    result = parse_document(source)
    assert result['observations'][0]['data']['value'] == '1234500000.0'
    assert not any('Custom metric' in block['text'] for block in result['text_blocks'])
