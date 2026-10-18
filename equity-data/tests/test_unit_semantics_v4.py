"""QName semantics, independent of issuer identities or business values."""
import hashlib
import pytest
from equity_data.parsers import parse_document

INSTANCE='http://www.xbrl.org/2003/instance'
MONEY='http://www.xbrl.org/2003/iso4217'

def document(measures, declarations='', extra=''):
    return f'''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="{INSTANCE}" xmlns:iso4217="{MONEY}" xmlns:t="urn:generic-test" {declarations}><body>
    <xbrli:context id="c"><xbrli:period><xbrli:instant>2031-12-31</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:unit id="u">{measures}</xbrli:unit>{extra}<ix:nonFraction name="t:Metric" contextRef="c" unitRef="u">17</ix:nonFraction></body></html>'''.encode()

def supporting(measures, declarations=''):
    content=f'<xbrl xmlns="{INSTANCE}" xmlns:xbrli="{INSTANCE}" {declarations}><unit id="u">{measures}</unit></xbrl>'.encode()
    digest=hashlib.sha256(content).hexdigest()
    return {'content':content,'raw_id':'sha256:'+digest,'sha256':digest,'source_url':'https://unit.test/support.xml'}

def facts(result):return [row for row in result['observations'] if row['dataset']=='financial_facts']

@pytest.mark.parametrize('main_measure,support_measure,declarations,label,currency',[
    ('iso4217:USD','money:USD',f'xmlns:money="{MONEY}"','iso4217:USD','USD'),
    ('xbrli:shares','shares','', 'xbrli:shares',None),
    ('xbrli:pure','base:pure',f'xmlns:base="{INSTANCE}"','xbrli:pure',None),
])
def test_equivalent_prefix_or_default_scope_keeps_full_primary_fact_identical(main_measure,support_measure,declarations,label,currency):
    raw=document(f'<xbrli:measure>{main_measure}</xbrli:measure>')
    single=parse_document(raw)
    combined=parse_document(raw,context_documents=[supporting(f'<measure>{support_measure}</measure>',declarations)])
    assert facts(single)==facts(combined)
    assert len(facts(combined))==1
    assert facts(combined)[0]['unit']==label
    assert facts(combined)[0]['currency']==currency


def test_same_prefix_and_localname_with_different_uri_is_real_conflict():
    raw=document('<xbrli:measure>iso4217:USD</xbrli:measure>')
    sup=supporting('<measure>iso4217:USD</measure>','xmlns:iso4217="urn:unrelated"')
    result=parse_document(raw,context_documents=[sup])
    assert facts(result)==[]
    assert 'Conflicting context/unit' in result['issues'][0]['message']


def test_alias_currency_and_exact_namespace_preserved():
    row=facts(parse_document(document('<xbrli:measure>cash:EUR</xbrli:measure>',f'xmlns:cash="{MONEY}"')))[0]
    assert row['currency']=='EUR'
    unit=row['data']['unit_definition']
    assert unit['expanded_numerator']==[{'namespace_uri':MONEY,'local_name':'EUR'}]
    assert unit['source_measures'][0]['lexical_qname']=='cash:EUR'


def test_familiar_prefix_does_not_create_fake_currency_namespace():
    raw=document('<xbrli:measure xmlns:iso4217="urn:fake">iso4217:USD</xbrli:measure>')
    row=facts(parse_document(raw))[0]
    assert row['currency'] is None and row['unit']=='{urn:fake}USD'


def test_default_namespace_declared_on_measure_applies_to_its_qname_value():
    raw=document(f'<xbrli:measure xmlns="{MONEY}">USD</xbrli:measure>')
    row=facts(parse_document(raw))[0]
    assert row['currency']=='USD' and row['unit']=='iso4217:USD'


def test_nested_binding_and_default_undeclaration_do_not_leak():
    raw=document('<xbrli:measure xmlns:u="urn:inner">u:thing</xbrli:measure><xbrli:measure xmlns="">thing</xbrli:measure>', 'xmlns:u="urn:outer"')
    row=facts(parse_document(raw))[0]
    assert row['data']['unit_definition']['expanded_numerator']==[
        {'namespace_uri':None,'local_name':'thing'},{'namespace_uri':'urn:inner','local_name':'thing'}]


@pytest.mark.parametrize('measure',['missing:USD','a:b:c','{urn}USD','bad name','1notName'])
def test_unbound_or_invalid_measure_qname_is_rejected(measure):
    result=parse_document(document(f'<xbrli:measure>{measure}</xbrli:measure>'))
    assert facts(result)==[]
    assert 'unit' in result['issues'][0]['message'].lower()


def test_prefix_case_is_significant_despite_html_lowercasing_attributes():
    raw=document('<xbrli:measure>U:thing</xbrli:measure>', 'xmlns:U="urn:upper" xmlns:u="urn:lower"')
    row=facts(parse_document(raw))[0]
    assert row['unit']=='{urn:upper}thing'
    other=facts(parse_document(raw.replace(b'U:thing',b'u:thing')))[0]
    assert other['unit']=='{urn:lower}thing'


def test_undeclared_uppercase_prefix_cannot_use_lowercase_binding():
    result=parse_document(document('<xbrli:measure>U:thing</xbrli:measure>','xmlns:u="urn:lower"'))
    assert facts(result)==[]


def test_namespace_lookalike_inside_unrelated_attribute_is_not_binding():
    raw=document('<xbrli:measure>wrong:thing</xbrli:measure>', '''data-note="xmlns:wrong='urn:fake'"''')
    assert facts(parse_document(raw))==[]


def test_division_preserves_sides_and_equivalent_prefixes():
    original='<xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide>'
    alternate='<divide><unitNumerator><measure>cash:USD</measure></unitNumerator><unitDenominator><measure>shares</measure></unitDenominator></divide>'
    raw=document(original)
    result=parse_document(raw,context_documents=[supporting(alternate,f'xmlns:cash="{MONEY}"')])
    row=facts(result)[0]
    assert row['unit']=='iso4217:USD/xbrli:shares'
    assert row['data']['unit_definition']['expanded_denominator']==[{'namespace_uri':INSTANCE,'local_name':'shares'}]
    assert facts(parse_document(raw))==facts(result)


def test_equal_measure_multisets_ignore_order_but_not_multiplicity():
    raw=document('<xbrli:measure>t:a</xbrli:measure><xbrli:measure>t:b</xbrli:measure>')
    sup=supporting('<measure>v:b</measure><measure>v:a</measure>','xmlns:v="urn:generic-test"')
    assert len(facts(parse_document(raw,context_documents=[sup])))==1
    duplicate=supporting('<measure>v:b</measure><measure>v:a</measure><measure>v:a</measure>','xmlns:v="urn:generic-test"')
    assert facts(parse_document(raw,context_documents=[duplicate]))==[]


@pytest.mark.parametrize('fragment',[
    '<xbrli:measure>xbrli:shares</xbrli:measure><xbrli:divide/>',
    '<xbrli:divide><xbrli:unitNumerator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator/></xbrli:divide>',
    '<xbrli:divide><xbrli:unitNumerator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitNumerator><xbrli:unitNumerator><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unitNumerator></xbrli:divide>',
])
def test_malformed_division_is_not_flattened_to_monetary_unit(fragment):
    assert facts(parse_document(document(fragment)))==[]


def test_denominator_difference_is_real_conflict_and_not_cancelled():
    main='<xbrli:divide><xbrli:unitNumerator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide>'
    sup=supporting('<measure>pure</measure>')
    assert facts(parse_document(document(main),context_documents=[sup]))==[]


def test_currency_ratio_does_not_claim_single_currency():
    fragment='<xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unitDenominator></xbrli:divide>'
    row=facts(parse_document(document(fragment)))[0]
    assert row['unit']=='iso4217:USD/iso4217:EUR' and row['currency'] is None
