"""Synthetic namespace mutations; no issuer-specific values or exceptions."""
import hashlib

import pytest

from equity_data import parsers


def document(dimensions, namespace='xmlns:a="urn:axis" xmlns:m="urn:member"', *, fact=True, context_id="c"):
    value = f'<ix:nonFraction name="a:Amount" contextRef="{context_id}" unitRef="u">17</ix:nonFraction>' if fact else ''
    return f'''<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" {namespace}><body>
    <xbrli:context id="{context_id}"><xbrli:entity><xbrli:identifier scheme="test">entity-1</xbrli:identifier><xbrli:segment>{dimensions}</xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2030-12-31</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:unit id="u"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>{value}</body></html>'''.encode()


def explicit(axis="a:ProductAxis", member="m:WidgetMember", attrs=""):
    return f'<xbrldi:explicitMember dimension="{axis}" {attrs}>{member}</xbrldi:explicitMember>'


def support(content):
    digest = hashlib.sha256(content).hexdigest()
    return dict(content=content, raw_id="sha256:"+digest, sha256=digest, source_url="https://synthetic.invalid/support.xml")


def parsed(content, extra=None, module=parsers):
    return module.parse_document(content, source_url="https://synthetic.invalid/main.html", context_documents=[support(extra)] if extra else [])


def facts(result):
    return [row for row in result["observations"] if row["dataset"] == "financial_facts"]


def synthetic_cases():
    primary = document(explicit())
    typed = '<xbrldi:typedMember dimension="a:RegionAxis"><m:Region>West</m:Region></xbrldi:typedMember>'
    return [
        ("different_prefix_same_uri", primary, document(explicit("AX:ProductAxis", "MEM:WidgetMember"), 'xmlns:AX="urn:axis" xmlns:MEM="urn:member"', fact=False), True),
        ("same_prefix_different_axis_uri", primary, document(explicit(), 'xmlns:a="urn:different-axis" xmlns:m="urn:member"', fact=False), False),
        ("same_prefix_different_member_uri", primary, document(explicit(), 'xmlns:a="urn:axis" xmlns:m="urn:different-member"', fact=False), False),
        ("default_namespace_same_uri", document(explicit("a:ProductAxis", "a:WidgetMember")), document(explicit("ProductAxis", "WidgetMember", 'xmlns="urn:axis"'), fact=False), True),
        ("element_scope_rebinding_equivalent", primary, document(explicit(attrs='xmlns:a="urn:axis" xmlns:m="urn:member"'), 'xmlns:a="urn:outer-axis" xmlns:m="urn:outer-member"', fact=False), True),
        ("element_scope_rebinding_conflict", primary, document(explicit(attrs='xmlns:m="urn:changed"'), fact=False), False),
        ("unbound_dimension_prefix", primary, document(explicit("unknown:ProductAxis"), fact=False), False),
        ("typed_same_prefix_different_uri", document(typed), document(typed, 'xmlns:a="urn:axis" xmlns:m="urn:changed-typed-domain"', fact=False), False),
        ("typed_equivalence_unverified", document(typed), document(typed, 'xmlns:a="urn:axis" xmlns:m="urn:member" xmlns:unused="urn:unused"', fact=False), False),
    ]


@pytest.mark.parametrize("name,main,extra,should_accept", synthetic_cases(), ids=[case[0] for case in synthetic_cases()])
def test_context_namespace_semantics(name, main, extra, should_accept):
    result = parsed(main, extra)
    assert bool(facts(result)) is should_accept
    if should_accept:
        assert facts(parsed(main)) == facts(result)
    else:
        assert any(issue["code"] == "IXBRL_NUMERIC_REJECTED" for issue in result["issues"])


def test_expanded_axis_member_and_original_evidence_are_retained():
    result = parsed(document(explicit("AX:ProductAxis", "MEM:WidgetMember"), 'xmlns:AX="urn:axis" xmlns:MEM="urn:member"'))
    dimension = facts(result)[0]["data"]["dimension_definitions"][0]
    assert dimension["axis"] == "AX:ProductAxis"
    assert dimension["member"] == "MEM:WidgetMember"
    assert dimension["axis_expanded"] == {"namespace_uri": "urn:axis", "local_name": "ProductAxis"}
    assert dimension["member_expanded"] == {"namespace_uri": "urn:member", "local_name": "WidgetMember"}
    assert dimension["source_locator"]
    assert "AX:ProductAxis" in dimension["source_markup"]
    assert dimension["evidence_excerpt"] == "MEM:WidgetMember"
    assert facts(result)[0]["data"]["context_evidence"]["sha256"] == hashlib.sha256(document(explicit("AX:ProductAxis", "MEM:WidgetMember"), 'xmlns:AX="urn:axis" xmlns:MEM="urn:member"')).hexdigest()


def test_rebound_prefix_does_not_leak_to_sibling_dimensions():
    markup = explicit(attrs='xmlns:m="urn:local"') + explicit("a:OtherAxis", "m:OtherMember")
    result = facts(parsed(document(markup)))[0]["data"]["dimension_definitions"]
    assert {item["axis"]: item["member_expanded"]["namespace_uri"] for item in result} == {"a:OtherAxis": "urn:member", "a:ProductAxis": "urn:local"}


def test_dimension_order_and_prefixes_do_not_change_context_identity():
    main = document(explicit() + explicit("a:RegionAxis", "m:WestMember"))
    extra = document(explicit("b:RegionAxis", "n:WestMember") + explicit("b:ProductAxis", "n:WidgetMember"), 'xmlns:b="urn:axis" xmlns:n="urn:member"', fact=False)
    assert facts(parsed(main, extra)) == facts(parsed(main))


def test_duplicate_semantic_axis_is_rejected_even_with_two_prefixes():
    main = document(explicit() + explicit("b:ProductAxis", "m:WidgetMember"), 'xmlns:a="urn:axis" xmlns:b="urn:axis" xmlns:m="urn:member"')
    result = parsed(main)
    assert not facts(result)
    assert any("Duplicate expanded dimension axis" in issue["message"] for issue in result["issues"])


def test_same_explicit_context_two_ids_detects_conflicting_numeric_values():
    main = document(explicit())
    extra = document(explicit("b:ProductAxis", "n:WidgetMember"), 'xmlns:b="urn:axis" xmlns:n="urn:member"', fact=False, context_id="other")
    main = main.replace(b"</body>", b'<ix:nonFraction name="a:Amount" contextRef="other" unitRef="u">18</ix:nonFraction></body>')
    result = parsed(main, extra)
    assert len(facts(result)) == 2
    assert any(issue["code"] == "IXBRL_CONFLICTING_FACTS" for issue in result["issues"])


def test_typed_dimension_local_value_is_preserved_but_equivalence_is_unknown():
    result = parsed(document('<xbrldi:typedMember dimension="a:RegionAxis"><m:Region>West</m:Region></xbrldi:typedMember>'))
    dimension = facts(result)[0]["data"]["dimension_definitions"][0]
    assert dimension["axis_expanded"] == {"namespace_uri": "urn:axis", "local_name": "RegionAxis"}
    assert dimension["member"] == "West" and "m:region" in dimension["typed_xml"]
    assert dimension["equivalence_status"] == "unverified_typed_domain_schema_not_loaded"
    assert result["coverage"]["inline_xbrl"]["typed_contexts_semantically_unverified"] == 1
    assert dimension["typed_reuse_method"] == "identical_original_fragment_and_namespace_bindings_only"


def test_identical_typed_original_fragment_and_namespace_bindings_are_reused():
    typed = '<xbrldi:typedMember dimension="a:RegionAxis"><m:Region>West</m:Region></xbrldi:typedMember>'
    main = document(typed)
    result = parsed(main, document(typed, fact=False))
    assert facts(result) == facts(parsed(main))
    assert facts(result)[0]["data"]["dimension_definitions"][0]["source_markup"] == typed


def test_typed_child_case_and_inner_namespace_changes_are_not_merged():
    typed = '<xbrldi:typedMember dimension="a:RegionAxis"><m:Region>West</m:Region></xbrldi:typedMember>'
    main = document(typed)
    for change in (typed.replace('m:Region', 'm:region'), typed.replace('<m:Region>', '<m:Region xmlns:m="urn:changed">')):
        result = parsed(main, document(change, fact=False))
        assert not facts(result)
        assert any("typed-domain equivalence is unverified" in issue["message"] for issue in result["issues"])


@pytest.mark.parametrize("children", ["", "<m:Region>West</m:Region><m:Region>East</m:Region>"])
def test_unsupported_typed_member_structure_is_explicitly_rejected(children):
    result = parsed(document(f'<xbrldi:typedMember dimension="a:RegionAxis">{children}</xbrldi:typedMember>'))
    assert not facts(result)
    assert any("Unsupported typed dimension structure" in issue["message"] for issue in result["issues"])


@pytest.mark.parametrize("axis,member", [("a:Bad:Axis", "m:WidgetMember"), ("a:ProductAxis", "m:Bad Member"), ("", "m:WidgetMember")])
def test_invalid_qname_is_rejected_without_guessing(axis, member):
    assert not facts(parsed(document(explicit(axis, member))))


def test_member_namespace_case_is_significant_and_default_undeclaration_is_not_inherited():
    main = document(explicit("a:ProductAxis", "M:WidgetMember"), 'xmlns:a="urn:axis" xmlns:M="urn:upper" xmlns:m="urn:lower"')
    assert facts(parsed(main))[0]["data"]["dimension_definitions"][0]["member_expanded"]["namespace_uri"] == "urn:upper"
    no_default = document(explicit("ProductAxis", "WidgetMember", 'xmlns=""'), 'xmlns="urn:outer"')
    assert facts(parsed(no_default))[0]["data"]["dimension_definitions"][0]["member_expanded"]["namespace_uri"] is None
