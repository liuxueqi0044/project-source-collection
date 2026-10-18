"""Independent audit unit semantics; no collector parser imports or expected facts."""
import pytest
from bs4 import BeautifulSoup
from scripts.audit_machine import independent_soup, independent_unit, independent_number

INSTANCE="http://www.xbrl.org/2003/instance"
MONEY="http://www.xbrl.org/2003/iso4217"


def unit(markup):
    return independent_unit(independent_soup(markup).find(lambda node: node.name.split(":")[-1]=="unit"))


def simple(declarations,measure):
    return f'<xbrl {declarations}><unit id="u"><measure>{measure}</measure></unit></xbrl>'


def test_default_and_alias_units_equal_without_lexical_prefix_matching():
    left=unit(simple(f'xmlns="{INSTANCE}"',"shares"))
    right=unit(simple(f'xmlns:Other="{INSTANCE}"',"Other:shares"))
    assert left==right
    assert left["label"]=="xbrli:shares"
    assert left["expanded_numerator"]==[{"namespace_uri":INSTANCE,"local_name":"shares"}]


def test_currency_is_namespace_based_not_prefix_based():
    real=unit(simple(f'xmlns:Money="{MONEY}"',"Money:USD"))
    fake=unit(simple('xmlns:iso4217="urn:other"',"iso4217:USD"))
    assert real["currency"]=="USD" and real["label"]=="iso4217:USD"
    assert fake["currency"] is None and fake["label"]=="{urn:other}USD"
    assert real!=fake


def test_prefix_case_and_actual_local_scope_are_preserved():
    markup=f'''<html xmlns:P="urn:upper" xmlns:p="{MONEY}"><body><unit id="u">
    <measure xmlns:P="{INSTANCE}">P:shares</measure><measure>p:USD</measure></unit></body></html>'''
    parsed=unit(markup)
    assert parsed["expanded_numerator"]==[{"namespace_uri":INSTANCE,"local_name":"shares"},{"namespace_uri":MONEY,"local_name":"USD"}]
    assert parsed["currency"] is None


def test_default_reset_not_inherited_from_ancestor():
    parsed=unit(f'<xbrl xmlns="{INSTANCE}"><unit><measure xmlns="">shares</measure></unit></xbrl>')
    assert parsed["expanded_numerator"]==[{"namespace_uri":None,"local_name":"shares"}]


def test_source_positions_and_quoted_attribute_text_do_not_invent_namespaces():
    markup=f'''<html\r\n data-note="look > here xmlns:P='urn:fake'"\r\n xmlns:P="{INSTANCE}"><body><unit><measure>P:shares</measure></unit></body></html>'''
    assert unit(markup)["label"]=="xbrli:shares"


def test_numerator_denominator_preserved_and_no_cancellation():
    parsed=unit(f'<xbrl xmlns:p="{INSTANCE}"><unit><divide><unitNumerator><measure>p:shares</measure><measure>p:shares</measure></unitNumerator><unitDenominator><measure>p:shares</measure></unitDenominator></divide></unit></xbrl>')
    assert parsed["numerator"]==["xbrli:shares","xbrli:shares"]
    assert parsed["denominator"]==["xbrli:shares"]
    assert parsed!=unit(simple(f'xmlns:p="{INSTANCE}"',"p:shares"))


def test_equivalent_quotients_match_across_different_prefixes_and_default_namespace():
    left=unit(f'<xbrl xmlns:s="{INSTANCE}" xmlns:m="{MONEY}"><unit><divide><unitNumerator><measure>m:USD</measure></unitNumerator><unitDenominator><measure>s:shares</measure></unitDenominator></divide></unit></xbrl>')
    right=unit(f'<xbrl xmlns="{INSTANCE}" xmlns:money="{MONEY}"><unit><divide><unitNumerator><measure>money:USD</measure></unitNumerator><unitDenominator><measure>shares</measure></unitDenominator></divide></unit></xbrl>')
    assert left==right
    assert left["label"]=="iso4217:USD/xbrli:shares" and left["currency"]=="USD"


@pytest.mark.parametrize("body",[
    '<measure>p:shares</measure>',
    '<measure xmlns:p="urn:p">p:a:b</measure>',
    '<measure xmlns:p="urn:p"><span>p:shares</span></measure>',
    '<divide><unitNumerator><measure>shares</measure></unitNumerator></divide>',
    '<divide><unitNumerator><measure>shares</measure></unitNumerator><unitDenominator/></divide>',
    '<measure>shares</measure><divide/>',
    '<box><measure>shares</measure></box>',
])
def test_ambiguous_or_unbound_units_fail_closed(body):
    with pytest.raises(ValueError):unit('<html><unit>'+body+'</unit></html>')


def test_exact_source_is_required_for_html_namespace_spelling():
    node=BeautifulSoup(simple(f'xmlns:P="{INSTANCE}"',"P:shares"),"html.parser").unit
    with pytest.raises(ValueError,match="independent_soup"):independent_unit(node)


def test_zero_dash_remains_unsupported_without_numeric_hardcoding():
    node=independent_soup('<html xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"><ix:nonFraction format="ixt:zero-dash">-</ix:nonFraction></html>').find('ix:nonfraction')
    with pytest.raises(ValueError,match="outside independent whitelist"):independent_number(node)
