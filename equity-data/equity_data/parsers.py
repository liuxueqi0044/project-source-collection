"""Conservative, issuer-independent document extraction.

This is deliberately not an XBRL taxonomy validator or an LLM. Unsupported or
ambiguous input produces an issue, never a guessed financial value. Numeric
values are decimal strings (JSON-safe and lossless). Public availability is a
source-adapter responsibility; this module never invents a publication clock.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import re
from html import unescape
from html.parser import HTMLParser

from lxml import etree, html

PARSER_VERSION = "generic-document-v4.1.0"
_SPACE = re.compile(r"\s+")
_MONTHS = {m.lower(): i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
_GUIDANCE = re.compile(r"\b(?:expect(?:s|ed)?|anticipat(?:e|es|ed)|guidance|outlook|forecast(?:s|ed)?|project(?:s|ed))\b", re.I)
_METRICS = re.compile(r"\b(?:adjusted\s+|non-GAAP\s+|GAAP\s+)?(?:net\s+revenue|revenue|net\s+sales|sales|net\s+income|operating\s+income|free\s+cash\s+flow|gross\s+margin|operating\s+margin|earnings\s+per\s+(?:diluted\s+)?share|diluted\s+(?:earnings\s+per\s+share|EPS)|EPS|capital\s+expenditures)\b", re.I)
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_AMOUNT = re.compile(r"(?<![\w.,])(?P<sign_before>[+−-])?\s*(?P<open_before>\()?\s*(?P<currency>US\$|USD|EUR|GBP|\$|€|£)?\s*(?P<sign_after>[+−-])?\s*(?P<open_after>\()?\s*(?P<number>" + _NUMBER + r")(?![\w]|[.,]\d)\s*(?P<close_inner>\))?\s*(?P<scale>billion|million|thousand|bn|mm)?\s*(?P<unit>%|percent|basis\s+points|per\s+share)?\s*(?P<close_outer>\))?", re.I)
_CURRENCY = {"US$": "USD", "USD": "USD", "EUR": "EUR", "€": "EUR", "£": "GBP", "GBP": "GBP"}
_SCALE = {"billion": Decimal(10)**9, "bn": Decimal(10)**9, "million": Decimal(10)**6, "mm": Decimal(10)**6, "thousand": Decimal(10)**3, None: Decimal(1)}
_XBRL_INSTANCE = "http://www.xbrl.org/2003/instance"
_ISO4217 = "http://www.xbrl.org/2003/iso4217"


class _QNameScopes(HTMLParser):
    """Retain case-sensitive namespace declarations lost by HTML DOM parsing.

    QName values are XML names even in inline HTML. HTMLParser's attributes
    are lowercased, so declarations are read from each original start tag.
    Only scoped namespace environments are attached, never fabricated values.
    """
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _ATTR = re.compile(r'''([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')
    def __init__(self, source=""):
        super().__init__(convert_charrefs=True)
        self.stack, self.targets = [], []
        self.source, self.source_fragments = source, {}
        self.line_offsets = [0]
        for match in re.finditer("\n", source):
            self.line_offsets.append(match.end())

    def _offset(self):
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        scope = dict(self.stack[-1][1]) if self.stack else {"xml": "http://www.w3.org/XML/1998/namespace"}
        raw = self.get_starttag_text()
        start = re.match(r"<[^\s/>]+", raw)
        for match in self._ATTR.finditer(raw[start.end():]):
            name = match[1]
            if name == "xmlns" or name.startswith("xmlns:"):
                prefix = None if name == "xmlns" else name[6:]
                value = next((value for value in match.groups()[1:] if value is not None), "")
                scope[prefix] = unescape(value) or None
        name = tag.rsplit(":", 1)[-1].lower()
        target_index = None
        if name in {"measure", "nonfraction", "fraction", "explicitmember", "typedmember"}:
            target_index = len(self.targets)
            self.targets.append((name, scope))
        if tag not in self._VOID:
            self.stack.append((tag, scope, target_index, self._offset()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, _, target_index, start = self.stack[index]
                if target_index is not None:
                    end = self.source.find(">", self._offset())
                    if end >= 0:
                        self.source_fragments[target_index] = self.source[start:end+1]
                del self.stack[index:]
                break


def _local(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower() if isinstance(tag, str) else ""


def _attr(node, name, default=None):
    for key, value in node.attrib.items():
        if _local(key) == name.lower():
            return value
    return default


def _namespace(node, prefix):
    scopes = getattr(node.getroottree().getroot(), "_source_qname_scopes", None)
    if scopes is not None and node in scopes:
        return scopes[node].get(prefix or None)
    for element in (node, *node.iterancestors()):
        if (prefix or None) in (getattr(element, "nsmap", {}) or {}):
            return element.nsmap[prefix or None]
        for key, value in element.attrib.items():
            if key == ("xmlns:" + prefix if prefix else "xmlns"):
                return value or None
    return None


def _text(node):
    return _SPACE.sub(" ", " ".join(node.itertext())).strip()


def _hidden_markup(node):
    """Only explicit visibility evidence; never evaluate arbitrary CSS/JS."""
    name = _local(node.tag)
    style = (_attr(node, "style", "") or "").lower()
    return name in {"script", "style", "head", "hidden", "resources"} or (name == "header" and ":" in str(node.tag)) or "hidden" in node.attrib or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|opacity\s*:\s*0(?:\s|;|$)|font-size\s*:\s*0(?:px|pt|em|rem)?(?:\s|;|$)|color\s*:\s*transparent)", style))


def _visible_text(node):
    parts = []
    def visit(el):
        if _hidden_markup(el):
            return
        if el.text:
            parts.append(el.text)
        for child in el:
            visit(child)
            if child.tail:
                parts.append(child.tail)
    visit(node)
    return _SPACE.sub(" ", " ".join(parts)).strip()


def _table_stylesheet_visibility(table):
    """Detect relevant hiding selectors without pretending to render CSS."""
    nodes = [*table.iter(), *table.iterancestors()]
    classes = {value for node in nodes for value in (_attr(node, "class", "") or "").split()}
    identifiers = {_attr(node, "id") for node in nodes if _attr(node, "id")}
    tags = {_local(node.tag) for node in nodes}
    for style in table.getroottree().getroot().iter():
        if _local(style.tag) != "style":
            continue
        for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]+)\}", _text(style)):
            probe = html.Element("span", style=declarations)
            if not _hidden_markup(probe):
                continue
            for selector in selectors.split(","):
                selector_classes = set(re.findall(r"\.([\w-]+)", selector))
                selector_ids = set(re.findall(r"#([\w-]+)", selector))
                selector_tags = set(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", selector))
                if selector_classes & classes or selector_ids & identifiers or (not selector_classes and not selector_ids and (selector_tags & tags or "*" in selector)):
                    return selector.strip()
    return None


def _exact_text(node):
    """iXBRL concatenates text nodes; ix:exclude is not part of the fact."""
    parts = [node.text or ""]
    for child in node:
        if _local(child.tag) != "exclude":
            parts.append(_exact_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite number")
    return format(result, "f")


def _issue(code, message, locator=None, **details):
    return {"code": code, "message": message, "source_locator": locator, "details": details}


def _key(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _observation(dataset, key, data, locator, excerpt, **meta):
    return dict(dataset=dataset, record_key=key, data=data, source_locator=locator,
                evidence_excerpt=excerpt, period_start=meta.get("period_start"),
                period_end=meta.get("period_end"), event_date=meta.get("event_date"),
                unit=meta.get("unit"), currency=meta.get("currency"),
                knowledge_type=meta.get("knowledge_type", "company_statement"),
                quality=meta.get("quality", "parsed_unverified"))


def _expanded_dimension_qname(element, lexical, role):
    """Resolve XML QName values in the actual element scope, case intact."""
    lexical = _SPACE.sub(" ", lexical or "").strip()
    pieces = lexical.split(":")
    if len(pieces) not in {1, 2} or any(not part for part in pieces):
        raise ValueError("Invalid dimension " + role + " QName: " + lexical)
    try:
        for part in pieces:
            if etree.QName(part).namespace is not None:
                raise ValueError("Expanded-name syntax is not a lexical QName")
    except ValueError as exc:
        raise ValueError("Invalid dimension " + role + " QName: " + lexical) from exc
    prefix, local_name = (pieces[0], pieces[1]) if len(pieces) == 2 else (None, pieces[0])
    namespace_uri = _namespace(element, prefix)
    if prefix is not None and namespace_uri is None:
        raise ValueError("Unbound dimension " + role + " QName prefix: " + prefix)
    return {"namespace_uri": namespace_uri, "local_name": local_name}


def _context(node):
    period, dimensions, entity = {}, [], None
    for el in node.iter():
        name = _local(el.tag)
        if name in {"startdate", "enddate", "instant"}:
            # Date-time XBRL contexts and forever periods require another adapter.
            raw_date = _text(el)
            date.fromisoformat(raw_date)
            period[name] = raw_date
        elif name == "identifier":
            entity = {"identifier": _text(el), "scheme": _attr(el, "scheme")}
        elif name in {"explicitmember", "typedmember"}:
            axis = _attr(el, "dimension")
            raw_evidence = getattr(el.getroottree().getroot(), "_source_dimension_evidence", {}).get(el)
            dimension = {"axis": axis, "member": _text(el), "type": "explicit" if name == "explicitmember" else "typed",
                         "axis_expanded": _expanded_dimension_qname(el, axis, "axis"),
                         "context_element": next((_local(parent.tag) for parent in el.iterancestors() if _local(parent.tag) in {"segment", "scenario"}), None),
                         "source_locator": el.getroottree().getpath(el), "evidence_excerpt": _text(el),
                         "source_markup": etree.tostring(el, encoding="unicode", with_tail=False),
                         "source_markup_kind": "dom_serialization_original_bytes_via_context_evidence"}
            if raw_evidence is not None:
                dimension.update(source_markup=raw_evidence["source_markup"], source_markup_kind="original_source_fragment")
            if name == "explicitmember":
                if len(el):
                    raise ValueError("Explicit dimension member must contain a QName without child elements")
                dimension.update(member_expanded=_expanded_dimension_qname(el, _text(el), "member"),
                                 equivalence_status="expanded_qname_identity")
            else:
                dimension.update(typed_xml="".join(etree.tostring(child, encoding="unicode", with_tail=False) for child in el),
                                 equivalence_status="unverified_typed_domain_schema_not_loaded")
                # Typed content may itself be a QName, list or complex type.
                # Without its domain schema, identical lexical XML is not
                # evidence of equal values under changed namespace bindings.
                if len([child for child in el if isinstance(child.tag, str)]) != 1:
                    raise ValueError("Unsupported typed dimension structure: exactly one domain element required")
                if raw_evidence is None:
                    raise ValueError("Typed dimension original fragment/namespace evidence unavailable")
                dimension["typed_namespace_bindings"] = raw_evidence["namespace_bindings"]
                dimension["typed_reuse_method"] = "identical_original_fragment_and_namespace_bindings_only"
            dimensions.append(dimension)
    if "instant" not in period and not all(k in period for k in ("startdate", "enddate")):
        raise ValueError("Missing supported instant or duration context")
    if "startdate" in period and period["startdate"] > period["enddate"]:
        raise ValueError("Context start is after end")
    axis_keys = [(item["axis_expanded"]["namespace_uri"], item["axis_expanded"]["local_name"]) for item in dimensions]
    if len(set(axis_keys)) != len(axis_keys):
        raise ValueError("Duplicate expanded dimension axis within context")
    return {"period_type": "instant" if "instant" in period else "duration",
            "period_start": period.get("startdate"), "period_end": period.get("instant", period.get("enddate")),
            "dimensions": sorted(dimensions, key=lambda x: (x["axis"] or "", x["member"])), "entity": entity}


def _context_semantics(context):
    """Evidence and prefix spelling must never enter explicit context identity.

    Typed equality requires domain typing that this parser does not load.
    Only identical original fragments with identical namespace bindings may
    reuse an existing typed definition; no typed value equivalence is inferred.
    """
    dimensions = []
    for item in context["dimensions"]:
        definition = {"axis": item["axis_expanded"], "type": item["type"], "context_element": item["context_element"]}
        if item["type"] == "explicit":
            definition["member"] = item["member_expanded"]
        else:
            definition.update(typed_original_fragment=item["source_markup"], typed_namespace_bindings=item["typed_namespace_bindings"])
        dimensions.append(definition)
    return {"period_type": context["period_type"], "period_start": context["period_start"],
            "period_end": context["period_end"], "entity": context["entity"],
            "dimensions": sorted(dimensions, key=lambda item: json.dumps(item, sort_keys=True))}


def _unit(node):
    """Unit identity is a multiset of expanded QNames on each side of divide.

    Source prefixes are evidence only. Do not infer namespaces from familiar
    lexical prefixes and do not cancel numerator/denominator measures.
    """
    children = [child for child in node if isinstance(child.tag, str)]
    if not children:
        raise ValueError("Missing unit measure")
    denominator_nodes = []
    if len(children) == 1 and _local(children[0].tag) == "divide":
        parts = [part for part in children[0] if isinstance(part.tag, str)]
        if len(parts) != 2 or [_local(part.tag) for part in parts] != ["unitnumerator", "unitdenominator"]:
            raise ValueError("Malformed divide unit: exactly one numerator and denominator required")
        numerator_nodes = [child for child in parts[0] if isinstance(child.tag, str)]
        denominator_nodes = [child for child in parts[1] if isinstance(child.tag, str)]
        if not numerator_nodes or not denominator_nodes:
            raise ValueError("Missing numerator or denominator measure")
    else:
        numerator_nodes = children
    if any(_local(child.tag) != "measure" or len(child) for child in numerator_nodes + denominator_nodes):
        raise ValueError("Malformed unit: only direct QName measure children supported")
    source_measures = []
    def expanded(element, side):
        lexical = _text(element)
        # QName validation is delegated to libxml's Unicode NCName rules.
        pieces = lexical.split(":")
        if len(pieces) not in {1, 2} or any(not part for part in pieces):
            raise ValueError("Invalid unit measure QName: " + lexical)
        try:
            for part in pieces:
                if etree.QName(part).namespace is not None:
                    raise ValueError("Expanded-name syntax is not a lexical QName")
        except ValueError as exc:
            raise ValueError("Invalid unit measure QName: " + lexical) from exc
        prefix, local_name = (pieces[0], pieces[1]) if len(pieces) == 2 else (None, pieces[0])
        namespace_uri = _namespace(element, prefix)
        if prefix is not None and namespace_uri is None:
            raise ValueError("Unbound unit measure QName prefix: " + prefix)
        source_measures.append({"lexical_qname": lexical, "namespace_uri": namespace_uri,
                                "local_name": local_name, "side": side,
                                "source_locator": element.getroottree().getpath(element)})
        return {"namespace_uri": namespace_uri, "local_name": local_name}
    ordering = lambda item: (item["namespace_uri"] or "", item["local_name"])
    numerator = sorted([expanded(el, "numerator") for el in numerator_nodes], key=ordering)
    denominator = sorted([expanded(el, "denominator") for el in denominator_nodes], key=ordering)
    def label(measure):
        uri, name = measure["namespace_uri"], measure["local_name"]
        if uri == _ISO4217:
            return "iso4217:" + name
        if uri == _XBRL_INSTANCE:
            return "xbrli:" + name
        return "{" + (uri or "") + "}" + name
    numerator_labels, denominator_labels = [label(x) for x in numerator], [label(x) for x in denominator]
    display = "*".join(numerator_labels) + (("/" + "*".join(denominator_labels)) if denominator else "")
    # An isolated monetary numerator (possibly per share) identifies currency;
    # currency products and FX ratios are not a single-currency amount.
    currency = numerator[0]["local_name"] if len(numerator) == 1 and numerator[0]["namespace_uri"] == _ISO4217 and not any(item["namespace_uri"] == _ISO4217 for item in denominator) else None
    return {"label": display, "numerator": numerator_labels, "denominator": denominator_labels,
            "expanded_numerator": numerator, "expanded_denominator": denominator,
            "currency": currency, "source_measures": source_measures,
            "identity_method": "expanded_qname_multisets_no_cancellation_v1"}


def _unit_semantics(unit):
    return {"numerator": unit["expanded_numerator"], "denominator": unit["expanded_denominator"]}


def _ix_number(node):
    text = _exact_text(node).strip().replace("\xa0", " ")
    transform = (_attr(node, "format") or "").split(":")[-1].lower()
    if transform:
        format_qname = _attr(node, "format")
        namespace = _namespace(node, format_qname.split(":")[0]) if ":" in format_qname else None
        if not namespace or not re.fullmatch(r"https?://www\.xbrl\.org/inlineXBRL/transformation/\d{4}-\d{2}-\d{2}", namespace):
            raise ValueError("Unbound or unsupported transformation namespace")
    if _attr(node, "nil", "false").lower() in {"true", "1"}:
        if text:
            raise ValueError("Nil numeric fact unexpectedly contains text")
        return None
    if transform in {"numdotdecimal", "num-dot-decimal"}:
        if not re.fullmatch(r"(?:\d{1,3}(?:[, ]\d{3})+|\d+)(?:\.\d+)?", text):
            raise ValueError("Invalid dot-decimal transformation input")
        text = text.replace(",", "").replace(" ", "")
    elif transform in {"numcommadecimal", "num-comma-decimal"}:
        if not re.fullmatch(r"(?:\d{1,3}(?:[. ]\d{3})+|\d+)(?:,\d+)?", text):
            raise ValueError("Invalid comma-decimal transformation input")
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    elif transform in {"zerodash", "numdash", "num-dash"}:
        if text not in {"-", "–", "—"}:
            raise ValueError("Dash transformation requires dash input")
        text = "0"
    elif transform == "fixed-zero":
        if namespace not in {"http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
                             "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16"}:
            raise ValueError("fixed-zero requires a supported registry version")
        # XBRL Transformation Registry 4/5 explicitly maps any input text to
        # zero. This is source-tag semantics, never a missing-value heuristic.
        text = "0"
    elif transform:
        raise ValueError("Unsupported numeric transformation: " + transform)
    elif not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ValueError("Non-canonical number without a supported transformation")
    scale = _attr(node, "scale", "0")
    if not re.fullmatch(r"[+-]?\d+", scale) or abs(int(scale)) > 99:
        raise ValueError("Unsupported or invalid numeric scale")
    sign = _attr(node, "sign")
    if sign not in {None, "-"} or (sign and text.startswith(("+", "-"))):
        raise ValueError("Invalid or doubly signed numeric fact")
    with localcontext() as ctx:
        ctx.prec = max(50, len(text) + abs(int(scale)) + 10)
        result = Decimal(text) * (Decimal(10) ** int(scale)) * (-1 if sign == "-" else 1)
    return _decimal(result)


def _inline_xbrl(root, observations, issues, scope, *, origin, context_roots=(), lexical_facts=None):
    contexts, units, context_evidence, unit_evidence = {}, {}, {}, {}
    context_failures, unit_failures = {}, {}
    elements = list(root.iter())
    tree = root.getroottree()
    for definition_root, definition_origin in [(root, origin), *context_roots]:
        definition_tree = definition_root.getroottree()
        local_ids = set()
        for node in definition_root.iter():
            name, identifier = _local(node.tag), _attr(node, "id")
            if name not in {"context", "unit"} or not identifier:
                continue
            target, failures, parser, evidence = (contexts, context_failures, _context, context_evidence) if name == "context" else (units, unit_failures, _unit, unit_evidence)
            try:
                if (name, identifier) in local_ids or identifier in failures:
                    raise ValueError("Duplicate or previously conflicting context/unit identifier")
                local_ids.add((name, identifier))
                value = parser(node)
                if identifier in target:
                    same = _unit_semantics(target[identifier]) == _unit_semantics(value) if name == "unit" else _context_semantics(target[identifier]) == _context_semantics(value)
                    if not same:
                        if name == "context" and any(item["type"] == "typed" for item in target[identifier]["dimensions"] + value["dimensions"]):
                            raise ValueError("Conflicting context/unit definitions across document set: typed dimension original fragment or namespace bindings differ; typed-domain equivalence is unverified, refusing merge")
                        raise ValueError("Conflicting context/unit definitions across document set")
                    # Identical repeated document-set definitions are allowed.
                    # Keep the first exact evidence; prefer the primary input.
                    continue
                target[identifier] = value
                evidence[identifier] = {**definition_origin, "source_locator":definition_tree.getpath(node)}
            except (ValueError, InvalidOperation) as exc:
                target.pop(identifier, None)
                evidence.pop(identifier, None)
                failures[identifier] = str(exc)
    candidates = [n for n in elements if _local(n.tag) in {"nonfraction", "fraction"}]
    accepted = 0
    occurrences = Counter()
    facts_by_semantics = {}
    used_support = set()
    for node in candidates:
        locator = tree.getpath(node)
        try:
            if _local(node.tag) == "fraction":
                raise ValueError("ix:fraction is preserved in raw evidence but not supported")
            name, context_id, unit_id = (_attr(node, field) for field in ("name", "contextref", "unitref"))
            if not name or ":" not in name:
                raise ValueError("Missing qualified concept name")
            if context_id not in contexts:
                raise ValueError("Missing/unsupported context: " + str(context_failures.get(context_id, context_id)))
            if unit_id not in units:
                raise ValueError("Missing/unsupported unit: " + str(unit_failures.get(unit_id, unit_id)))
            decimals = _attr(node, "decimals")
            if decimals is not None and decimals != "INF" and not re.fullmatch(r"[+-]?\d+", decimals):
                raise ValueError("Invalid decimals accuracy attribute")
            value = _ix_number(node)
            context, unit = contexts[context_id], units[unit_id]
            identity = (name, context_id, unit_id)
            occurrences[identity] += 1
            data = {"concept_qname": name, "original_metric_name": name, "value": value,
                    "concept_namespace": _namespace(node, name.split(":")[0]),
                    "value_status": "source_nil" if value is None else "parsed",
                    "context_id": context_id, "unit_id": unit_id, "unit_definition": unit,
                    "entity": context["entity"], "period_type": context["period_type"],
                    # Keep the established lexical display contract stable;
                    # consumers needing identity must use expanded definitions.
                    "dimensions": [{key: item[key] for key in ("axis", "member", "type", "typed_xml") if key in item} for item in context["dimensions"]],
                    "dimension_definitions": context["dimensions"], "scale": int(_attr(node, "scale", "0")),
                    "sign": _attr(node, "sign"), "decimals": decimals, "precision": _attr(node, "precision"),
                    "format": _attr(node, "format"), "original_value_text": _exact_text(node),
                    "is_custom_concept": not name.startswith(("us-gaap:", "ifrs-full:", "dei:")),
                    "comparable_metric": None, "definition": None, "definition_status": "taxonomy_not_loaded",
                    "extraction_method": "inline_xbrl_local_no_taxonomy_network",
                    "context_locator": context_evidence[context_id]["source_locator"], "unit_locator": unit_evidence[unit_id]["source_locator"],
                    "context_evidence":context_evidence[context_id], "unit_evidence":unit_evidence[unit_id]}
            support_ids = sorted({item["raw_id"] for item in (context_evidence[context_id],unit_evidence[unit_id]) if item["raw_id"] != origin["raw_id"]})
            data["supporting_raw_ids"] = support_ids
            used_support.update(support_ids)
            excerpt = _text(node)
            lexical_key = tuple(_attr(node, field) for field in ("name","contextref","unitref","id"))
            if not excerpt and lexical_facts and lexical_key in lexical_facts:
                # Empty nil/fixed-zero has no visible text; retain the exact
                # original tag slice as evidence instead of inventing a quote.
                excerpt = lexical_facts[lexical_key]
                data["evidence_excerpt_kind"] = "original_tag_markup"
            observations.append(_observation("financial_facts", "ix:" + _key(scope, *identity, occurrences[identity]), data,
                locator, excerpt, period_start=context["period_start"], period_end=context["period_end"],
                unit=unit["label"], currency=unit["currency"]))
            typed_dimensions = [item for item in context["dimensions"] if item["type"] == "typed"]
            if typed_dimensions:
                # Same local context ID still identifies one exact definition;
                # different IDs are never inferred to have equal typed values.
                context_identity = {"typed_local_context_id": context_id, "raw_id": context_evidence[context_id]["raw_id"]}
            else:
                context_identity = _context_semantics(context)
            semantic_key = json.dumps([name, context_identity, _unit_semantics(unit)], sort_keys=True)
            prior = facts_by_semantics.get(semantic_key)
            prior_value = prior["data"]["value"] if prior is not None else None
            disagree = (prior_value is None) != (value is None)
            if prior_value is not None and value is not None:
                disagree = Decimal(prior_value) != Decimal(value)
            if prior is not None and disagree:
                prior["quality"] = observations[-1]["quality"] = "source_conflict_requires_review"
                issues.append(_issue("IXBRL_CONFLICTING_FACTS", "Equivalent contexts/units contain different values; rounding consistency is not validated", locator,
                                     previous_locator=prior["source_locator"], previous_value=prior["data"]["value"], current_value=value))
            facts_by_semantics[semantic_key] = observations[-1]
            accepted += 1
        except (ValueError, InvalidOperation) as exc:
            issues.append(_issue("IXBRL_NUMERIC_REJECTED", str(exc), locator, concept=_attr(node, "name"), evidence_excerpt=_text(node)))
    return {"numeric_candidates": len(candidates), "numeric_parsed": accepted, "numeric_rejected": len(candidates) - accepted,
            "contexts": len(contexts), "units": len(units), "taxonomy_validation": "not_performed",
            "nonnumeric_tags": sum(_local(n.tag) == "nonnumeric" for n in elements),
            "context_documents_supplied":len(context_roots), "supporting_raw_ids":sorted(used_support),
            "typed_contexts_semantically_unverified":sum(any(item["type"] == "typed" for item in context["dimensions"]) for context in contexts.values())}


def _period(text):
    """Return an explicit target label, without guessing issuer fiscal dates."""
    patterns = [
        r"(?:first|second|third|fourth|[1-4](?:st|nd|rd|th))\s+quarter\s+(?:of\s+)?(?:fiscal\s+(?:year\s+)?)?20\d{2}",
        r"Q[1-4]\s+(?:(?:of\s+)?fiscal\s+)?20\d{2}",
        r"(?:quarter|year|period)\s+end(?:ing|ed)\s+(?:[A-Za-z]+\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2})",
        r"(?:full[ -]year|fiscal\s+(?:year\s+)?|FY\s*)\s*20\d{2}",
    ]
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            if not any(match.start() >= s and match.end() <= e for s, e, _ in matches):
                matches.append((match.start(), match.end(), match.group()))
    # More than one period often means a prior/current comparison. Do not guess.
    labels = list(dict.fromkeys(label.lower() for _, _, label in matches))
    if len(labels) != 1:
        raise ValueError("Guidance requires one explicit, unambiguous target period in its evidence span")
    label = matches[0][2]
    year = int(re.search(r"20\d{2}", label).group())
    end = None
    if re.search(r"\bend(?:ing|ed)\b", label, re.I):
        raw = re.split(r"\bend(?:ing|ed)\s+", label, flags=re.I)[-1]
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
            end = date.fromisoformat(raw).isoformat()
        else:
            m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d{2})", raw)
            if m and m[1].lower() in _MONTHS:
                end = date(int(m[3]), _MONTHS[m[1].lower()], int(m[2])).isoformat()
            else:
                raise ValueError("Unsupported target-period calendar date")
    return {"label": label, "year": year, "fiscal_year": year if re.search(r"fiscal|FY", label, re.I) else None,
            "period_end": end, "calendar_mapping": "explicit_date" if end else "unknown_fiscal_calendar"}


def _money(match, fallback=None):
    result = match.groupdict()
    fallback = fallback or {}
    symbol = result["currency"] or fallback.get("currency")
    scale = result["scale"] or fallback.get("scale")
    suffix = result["unit"] or fallback.get("unit")
    currency = _CURRENCY.get(symbol.upper() if symbol else None)
    suffix = suffix.lower() if suffix else None
    if suffix in {"%", "percent"}:
        unit = "percent"
    elif suffix and suffix.startswith("basis"):
        unit = "basis_points"
    elif currency:
        unit = currency + ("/share" if suffix == "per share" else "")
    elif symbol == "$":
        # Several currencies use this symbol. A document's market/location
        # alone is insufficient currency evidence; do not silently assign USD.
        unit = "dollar_symbol" + ("/share" if suffix == "per share" else "")
    else:
        raise ValueError("Numeric guidance missing explicit currency or percentage unit")
    if symbol and unit in {"percent", "basis_points"}:
        raise ValueError("Conflicting currency and percentage units")
    if scale and unit in {"percent", "basis_points"}:
        raise ValueError("Monetary magnitude combined with percentage/basis-point unit is unsupported")
    signs = [result.get(k) for k in ("sign_before", "sign_after") if result.get(k)]
    opens = sum(bool(result.get(k)) for k in ("open_before", "open_after"))
    closes = sum(bool(result.get(k)) for k in ("close_inner", "close_outer"))
    if len(signs) > 1 or opens != closes or opens > 1 or (opens and signs):
        raise ValueError("Unbalanced parentheses or conflicting numeric signs")
    negative = opens == 1 or signs in [["-"], ["−"]]
    value = Decimal(result["number"].replace(",", "")) * _SCALE[scale.lower() if scale else None] * (-1 if negative else 1)
    return {"value": _decimal(value), "unit": unit, "currency": currency, "source_scale": scale,
            "source_text": match.group().strip(), "sign_notation": "parentheses" if opens else ("explicit_negative" if negative else "unsigned_or_positive")}


def _guidance(text, locator, observations, issues, scope, *, table_context=None):
    if not _GUIDANCE.search(text):
        return False, False
    metrics = list(_METRICS.finditer(text))
    if not re.search(r"\d", text):
        return False, False
    if table_context is not None and (metrics or any(m["currency"] or m["scale"] or m["unit"] for m in _AMOUNT.finditer(text))):
        # Flattening a table destroys scope, column association, header scale,
        # rowspan/colspan and footnote conditions. Until a table-specific
        # adapter resolves all of those together, NEVER interpret one flattened
        # guidance row as a complete amount. This includes apparently simple
        # tables: a displayed cell is not a base-currency amount when a
        # separate header supplies the monetary scale.
        issues.append(_issue("GUIDANCE_TABLE_CONTEXT_UNSUPPORTED",
            "Numeric table guidance rejected: generic paragraph rules do not validate header units/scales, segment dimensions, columns and qualifiers together",
            table_context["source_locator"], evidence_excerpt=table_context["evidence_excerpt"],
            row_locator=locator, row_excerpt=text, required_adapter="evidenced_table_guidance"))
        return True, False
    if not metrics:
        # Distinguish a heading with a date from an unsupported operational
        # guidance metric carrying an explicit amount/unit.
        if any(m["currency"] or m["scale"] or m["unit"] for m in _AMOUNT.finditer(text)):
            issues.append(_issue("GUIDANCE_METRIC_UNSUPPORTED", "Guidance contains an amount but no supported metric label; original text retained", locator, evidence_excerpt=text))
            return True, False
        return False, False
    try:
        if len(metrics) != 1:
            raise ValueError("Multiple guidance metrics require separate evidence spans/table rows")
        if re.search(r"\b(?:loss(?:es)?|negative|deficit)\b", text, re.I):
            raise ValueError("Lexical loss/negative/deficit semantics require explicit interpretation; no sign inferred")
        if re.search(r"\b(?:last\s+(?:quarter|year)|prior\s+(?:quarter|year)|previous\s+(?:quarter|year)|year\s+ago|was\s+(?:USD|US\$|\$)|reported\s+(?:USD|US\$|\$))\b", text, re.I):
            raise ValueError("Historical comparison in guidance span requires separate interpretation")
        target = _period(text)
        metric = metrics[0]
        following = text[metric.end():]
        # Remove target labels before finding values; year numbers are not amounts.
        following = following.replace(target["label"], " ")
        amount_matches = list(_AMOUNT.finditer(following))
        qualified = [m for m in amount_matches if m["currency"] or m["scale"] or m["unit"]]
        if not qualified:
            raise ValueError("No amount with an explicit currency/percentage unit after the metric")
        first = qualified[0]
        if any(match.start() < first.start() for match in amount_matches):
            raise ValueError("Unassigned numeric value before the first unit-qualified guidance amount")
        if re.search(r"[+−–—(\-]\s*$", following[:first.start()]):
            raise ValueError("Unassigned sign or parenthesis before guidance amount")
        # A range may omit the first amount's unit: '10 to 12 million USD' is
        # deliberately rejected; '$10 to $12 million' is safely inherited.
        second = next((m for m in amount_matches if m.start() >= first.end() and re.fullmatch(r"\s*(?:to|through|and|[-–—])\s*", following[first.end():m.start()], re.I)), None)
        tolerance = re.search(r"(?:plus\s+or\s+minus|\+\s*/\s*[-−]|±)\s*(?P<value>" + _NUMBER + r")\s*(?P<unit>%|percent|basis\s+points|billion|million|thousand)?", following, re.I)
        unsupported_bounds = re.search(r"\b(?:at\s+least|at\s+most|greater\s+than|less\s+than|more\s+than|up\s+to|approximately|about|roughly)\b|~", following[:first.start()], re.I)
        first_value = _money(first, second.groupdict() if second else None)
        second_value = _money(second, first.groupdict()) if second else None
        if second_value and (first_value["unit"] != second_value["unit"] or Decimal(first_value["value"]) > Decimal(second_value["value"])):
            raise ValueError("Guidance range units disagree or bounds are reversed")
        if tolerance and second:
            raise ValueError("Range plus tolerance requires separate interpretation")
        tolerance_data = None
        if tolerance:
            if not tolerance["unit"]:
                raise ValueError("Tolerance has no explicit unit")
            if tolerance["unit"].lower() in {"%", "percent"}:
                tolerance_data = {"value": _decimal(tolerance["value"].replace(",", "")), "unit": "percent", "basis": "relative_to_point"}
            elif tolerance["unit"].lower().startswith("basis"):
                tolerance_data = {"value": _decimal(tolerance["value"].replace(",", "")), "unit": "basis_points", "basis": "absolute"}
            elif first_value["currency"]:
                tolerance_data = {"value": _decimal(Decimal(tolerance["value"].replace(",", "")) * _SCALE[tolerance["unit"].lower()]), "unit": first_value["unit"], "basis": "absolute"}
            else:
                raise ValueError("Tolerance amount without matching currency")
        # If the source contains a tolerance cue but did not match, do not emit
        # the point value alone and silently drop the qualification.
        if re.search(r"plus\s+or\s+minus|±|\+\s*/\s*[-−]", following, re.I) and not tolerance_data:
            raise ValueError("Unsupported or incomplete guidance tolerance")
        allowed = {id(first)} | ({id(second)} if second else set())
        remaining = [m for m in qualified if id(m) not in allowed and not (tolerance and m.start() >= tolerance.start() and m.end() <= tolerance.end())]
        if remaining:
            raise ValueError("Unassigned additional numeric amount in guidance evidence span")
        metric_name = metric.group()
        if re.search(r"EPS|per\s+(?:diluted\s+)?share", metric_name, re.I) and first_value["unit"] != "percent":
            if first_value["unit"] not in {"USD", "EUR", "GBP", "dollar_symbol", "USD/share", "EUR/share", "GBP/share", "dollar_symbol/share"}:
                raise ValueError("EPS requires a monetary per-share unit")
            first_value["unit"] = first_value["unit"].removesuffix("/share") + "/share"
            if second_value:
                second_value["unit"] = first_value["unit"]
            if tolerance_data and tolerance_data["unit"] in {"USD", "EUR", "GBP", "dollar_symbol"}:
                tolerance_data["unit"] = first_value["unit"]
        qualifier = unsupported_bounds.group().lower() if unsupported_bounds else "stated"
        lower_bound = qualifier in {"at least", "greater than", "more than"}
        upper_bound = qualifier in {"at most", "less than", "up to"}
        if second and (lower_bound or upper_bound):
            raise ValueError("Inequality combined with a range has ambiguous bound semantics")
        data = {"original_metric_name": metric_name, "comparable_metric": None, "definition": None,
                "target_period": target, "point": None if second or lower_bound or upper_bound else first_value["value"],
                "low": first_value["value"] if second or lower_bound else None, "high": second_value["value"] if second else (first_value["value"] if upper_bound else None),
                "tolerance": tolerance_data, "value_qualifier": unsupported_bounds.group() if unsupported_bounds else "stated",
                "value_semantics": "interval" if second else ("lower_bound" if lower_bound else ("upper_bound" if upper_bound else ("approximate_point" if qualifier != "stated" else "point"))),
                "bound_inclusivity": {"lower": qualifier == "at least" if lower_bound else None, "upper": qualifier in {"at most", "up to"} if upper_bound else None},
                "source_amounts": [first_value] + ([second_value] if second else []),
                "conditions_text": text, "dimensions": [], "accounting_basis": "non-GAAP" if re.search(r"non-GAAP", text, re.I) else ("GAAP" if re.search(r"\bGAAP\b", text) else "unspecified"),
                "extraction_method": "explicit_guidance_span_rules", "automatic": True,
                "unit": first_value["unit"], "currency": first_value["currency"], "definition_status": "source_label_only"}
        observations.append(_observation("guidance", "guidance:" + _key(scope, locator, metric_name.lower()), data, locator, text,
            period_end=target["period_end"], unit=first_value["unit"], currency=first_value["currency"]))
        return True, True
    except (ValueError, InvalidOperation) as exc:
        issues.append(_issue("GUIDANCE_REJECTED", str(exc), locator, evidence_excerpt=text))
        return True, False


_NARRATIVE_RULES = (
    ("customer_concentration", "relations", r"customer.{0,100}(?:accounted\s+for|represented|concentration|%|percent)|(?:%|percent).{0,50}(?:revenue|sales).{0,30}customer"),
    ("supply_chain", "relations", r"\b(?:supplier|suppliers|supply\s+agreement|sole\s+source|single.source|competitor|competitors)\b"),
    ("commitments", "commitments", r"\b(?:purchase\s+(?:commitment|obligation)|contractual\s+obligation|capacity\s+commitment|cloud\s+(?:service|commitment)|lease\s+(?:commitment|payment|obligation))s?\b"),
    ("debt_maturities", "debt_maturities", r"\b(?:debt\s+maturit|principal\s+(?:payments|amount).{0,30}due|notes\s+(?:mature|due)|maturities\s+of\s+(?:debt|borrowings))"),
    ("contingencies", "risk_disclosures", r"\b(?:contingent\s+liabilit|contingencies|legal\s+proceeding|litigation|loss\s+contingenc)"),
    ("risk_statement", "risk_disclosures", r"\b(?:risk\s+factor|material\s+risk|adversely\s+affect|significant\s+uncertaint)"),
    ("segment_disclosure", "operating_metrics", r"\b(?:reportable\s+segment|operating\s+segment|geographic\s+(?:region|area)|product\s+(?:revenue|line))s?\b"),
)


def _table_guidance(table, heading, observations, issues, scope):
    """Bounded rectangular guidance tables, preserving cell/header evidence.

    Supported shape: metric column followed by one or more forecast columns;
    th header cells supply per-column period/units. No merged cells, segmented
    scope, nested tables, unlinked footnotes, or cross-table inheritance.
    Unsupported shapes generate explicit rejections, never flattened numbers.
    """
    tree, locator = table.getroottree(), table.getroottree().getpath(table)
    heading_text = heading[1] if heading else ""
    whole = (heading_text + " " + _text(table)).strip()
    if not _GUIDANCE.search(whole) or not re.search(r"\d", whole):
        return 0, 0
    def reject(message, where=locator, excerpt=whole):
        issues.append(_issue("GUIDANCE_TABLE_CONTEXT_UNSUPPORTED", message, where,
                             evidence_excerpt=excerpt, table_locator=locator, required_adapter="evidenced_table_guidance"))
    if not _METRICS.search(whole):
        reject("No supported metric in numeric guidance table; custom metric remains unmapped")
        return 1, 0
    if any(_hidden_markup(n) for n in table.iter()) or any(_hidden_markup(n) for n in table.iterancestors()):
        reject("Table contains explicit hidden or style-dependent content; numeric visibility requires a separate adapter")
        return 1, 0
    hiding_selector = _table_stylesheet_visibility(table)
    if hiding_selector:
        reject("Table may be affected by stylesheet visibility selector: " + hiding_selector)
        return 1, 0
    if any(_local(n.tag) == "table" for n in table.iterdescendants()) or any(_attr(n, "rowspan", "1") != "1" or _attr(n, "colspan", "1") != "1" for n in table.iterdescendants()):
        reject("Nested or merged table cells require an unsupported table layout adapter")
        return 1, 0
    rows = [n for n in table.iterdescendants() if _local(n.tag) == "tr"]
    grid = [[n for n in row if _local(n.tag) in {"th", "td"}] for row in rows]
    if not grid or len(grid[0]) < 2 or any(len(row) != len(grid[0]) for row in grid):
        reject("Guidance requires a rectangular metric-plus-forecast table")
        return 1, 0
    headers, body = [], []
    for row in grid:
        if all(_local(n.tag) == "th" for n in row) and not body:
            headers.append(row)
        else:
            body.append(row)
    if not body or (len(grid[0]) > 2 and not headers):
        reject("Multiple forecast columns need explicit column headers")
        return 1, 0
    captions = [n for n in table if _local(n.tag) == "caption"]
    base_components = ([{"source_locator": heading[0], "text": heading_text}] if heading else []) + [{"source_locator": tree.getpath(n), "text": _text(n)} for n in captions]
    candidates = accepted = 0
    for row in body:
        metric_cell = row[0]
        metric = _text(metric_cell)
        if not _METRICS.search(metric):
            if re.search(r"\d", " ".join(_text(n) for n in row[1:])):
                candidates += 1
                reject("Unsupported metric or dimension row; no company-specific metric mapping", tree.getpath(metric_cell), " ".join(_text(n) for n in row))
            continue
        for column, value_cell in enumerate(row[1:], 1):
            candidates += 1
            components = list(base_components)
            header_nodes = [h[column] for h in headers]
            header_text = " ".join(_text(n) for n in header_nodes)
            components.extend({"source_locator": tree.getpath(n), "text": _text(n)} for n in header_nodes)
            components.extend({"source_locator": tree.getpath(n), "text": _text(n)} for n in (metric_cell, value_cell))
            try:
                def cell_text(cell):
                    chunks = []
                    def visit(node):
                        is_marker = _local(node.tag) == "sup" or (_local(node.tag) == "a" and re.fullmatch(r"\(?[\d*†‡]+\)?", _text(node) or ""))
                        if is_marker:
                            links = [node] if _local(node.tag) == "a" else [n for n in node.iterdescendants() if _local(n.tag) == "a"]
                            if len(links) != 1 or not (_attr(links[0], "href") or "").startswith("#"):
                                raise ValueError("Footnote marker has no unique local target")
                            identifier = _attr(links[0], "href")[1:]
                            notes = table.getroottree().getroot().xpath("//*[@id=$identifier]", identifier=identifier)
                            if len(notes) != 1:
                                raise ValueError("Footnote target missing or duplicated")
                            note = _text(notes[0])
                            condition = re.sub(r"^[\s\d*†‡().]+", "", note)
                            if not re.match(r"(?:Assum(?:es|ing)|Subject to|Based on)\b", condition, re.I) or re.search(r"\b(?:except|excluding|including|millions?|billions?|thousands?|per share|units?)\b", condition, re.I):
                                raise ValueError("Footnote may change numeric scope, unit or basis; interpretation unsupported")
                            components.append({"source_locator": tree.getpath(notes[0]), "text": note, "role": "condition_footnote"})
                            return
                        if node.text:
                            chunks.append(node.text)
                        for child in node:
                            visit(child)
                            if child.tail:
                                chunks.append(child.tail)
                    visit(cell)
                    return _SPACE.sub(" ", " ".join(chunks)).strip()
                metric_text, amount_text = cell_text(metric_cell), cell_text(value_cell)
                if not _METRICS.fullmatch(metric_text):
                    raise ValueError("Metric cell contains unresolved entity, segment or qualification")
                if re.search(r"segment|division|services|except|excluding|including", " ".join(c["text"] for c in base_components) + " " + header_text, re.I):
                    raise ValueError("Table header has unresolved segment scope or unit exception")
                try:
                    target = _period(header_text)
                except ValueError:
                    # A header carrying conflicting periods must not fall back.
                    if re.search(r"20\d{2}", header_text):
                        raise
                    target = _period(" ".join(c["text"] for c in base_components))
                def accounting_basis(value):
                    matches = re.findall(r"\bnon[- ]GAAP\b|\bGAAP\b", value, re.I)
                    labels = {"non-GAAP" if re.match("non", item, re.I) else "GAAP" for item in matches}
                    if len(labels) > 1:
                        raise ValueError("Conflicting accounting bases in table evidence")
                    return next(iter(labels), None)
                header_basis = accounting_basis(header_text)
                row_basis = accounting_basis(metric_text)
                if header_basis and row_basis and header_basis != row_basis:
                    raise ValueError("Row and column accounting bases conflict")
                for component in [*base_components, {"text": header_text}]:
                    residual = component["text"]
                    try:
                        component_period = _period(residual)
                        if component_period["label"].lower() != target["label"].lower() and component in base_components:
                            raise ValueError("Global and column target periods conflict")
                        residual = residual.replace(component_period["label"], " ")
                    except ValueError as exc:
                        if "conflict" in str(exc) or re.search(r"20\d{2}", residual):
                            raise
                    residual = _GUIDANCE.sub(" ", residual)
                    residual = re.sub(r"US\$|\b(?:USD|EUR|GBP|non[- ]GAAP|GAAP|billions?|millions?|thousands?|percent|basis points|per share|for|the|in|of|amounts?|units?|unaudited)\b|[%$€£]", " ", residual, flags=re.I)
                    if re.search(r"[A-Za-z0-9]", residual):
                        raise ValueError("Header or title contains unresolved scope or qualification")
                symbols = re.findall(r"US\$|\bUSD\b|\bEUR\b|\bGBP\b|[$€£]", header_text, re.I)
                symbols = list(dict.fromkeys(x.upper() for x in symbols))
                scales = list(dict.fromkeys(s.lower().rstrip("s") for s in re.findall(r"\b(?:billion|million|thousand)s?\b", header_text, re.I)))
                if len(symbols) > 1 or len(scales) > 1:
                    raise ValueError("Conflicting column units or monetary scales")
                symbol = symbols[0] if symbols else None
                scale = scales[0] if scales else None
                if (symbol or scale) and re.search(r"%|\bpercent\b|\bbasis points\b", header_text, re.I):
                    raise ValueError("Percentage/basis-point header conflicts with monetary currency or scale")
                if re.search(r"\b(?:per share|shares?)\b", header_text, re.I) and not re.search(r"EPS|per.*share", metric_text, re.I):
                    raise ValueError("Per-share header applied to a non-EPS metric")
                amounts = list(_AMOUNT.finditer(amount_text))
                if not amounts:
                    raise ValueError("Forecast cell has no supported numeric amount")
                normalized = amount_text
                # Add only evidenced header units to the internal parse span.
                # The emitted evidence remains original cells, never this span.
                for match in reversed(amounts):
                    original = match.group()
                    if symbol and match["currency"] and match["currency"].upper() not in {symbol, "$"}:
                        raise ValueError("Cell currency conflicts with column header")
                    if scale and match["scale"] and match["scale"].lower() != scale:
                        raise ValueError("Cell scale conflicts with column header")
                    addition = original.strip()
                    if symbol and not match["currency"]:
                        # Prefix before an accounting parenthesis is supported.
                        addition = symbol + " " + addition
                    elif symbol and match["currency"] == "$" and symbol != "$":
                        addition = addition.replace("$", symbol, 1)
                    if scale and not match["scale"]:
                        addition += " " + scale
                    if not symbol and not match["unit"] and re.search(r"%|\bpercent\b", header_text, re.I):
                        addition += " percent"
                    normalized = normalized[:match.start()] + " " + addition + " " + normalized[match.end():]
                internal_metric = f"{header_basis} {metric_text}" if header_basis and not row_basis else metric_text
                internal = f"Outlook for {target['label']}: {internal_metric} expected {normalized}"
                temporary, errors = [], []
                _, ok = _guidance(internal, tree.getpath(value_cell), temporary, errors, scope)
                if not ok:
                    raise ValueError("; ".join(e["message"] for e in errors) or "Unrecognized table forecast")
                observation = temporary[0]
                excerpt = " | ".join(c["text"] for c in components)
                observation["source_locator"] = " + ".join(c["source_locator"] for c in components)
                observation["evidence_excerpt"] = excerpt
                observation["data"].update(extraction_method="rectangular_table_guidance_v1", evidence_components=components,
                    original_metric_name=metric_text, conditions_text=excerpt, original_value_cell=_text(value_cell), table_column=column,
                    table_scope="document_subject_reference_entity_resolution_required", table_layout="metric_rows_forecast_columns")
                observations.append(observation)
                accepted += 1
            except (ValueError, InvalidOperation) as exc:
                reject(str(exc), tree.getpath(value_cell), " | ".join(c["text"] for c in components))
    return candidates, accepted


def _narrative(text, locator, observations, scope):
    for category, dataset, pattern in _NARRATIVE_RULES:
        if re.search(pattern, text, re.I):
            data = {"semantic_type": category, "text": text, "counterparty_name": None,
                    "counterparty_status": "not_resolved", "numeric_values": None, "dimensions": [],
                    "extraction_method": "narrative_evidence_candidate", "verification_status": "requires_review",
                    "is_structured_numeric_fact": False, "change_analysis": "not_performed"}
            observations.append(_observation(dataset, "narrative:" + _key(scope, locator, category), data,
                locator, text, quality="evidence_candidate_requires_review"))


def _html_blocks(root):
    """Cover visible DOM text once, including container text and child tails.

    A fallback used only when no paragraphs exist drops div text from mixed
    documents. Here every visible text node belongs to its nearest block;
    text runs split around child blocks keep document order without duplicating
    descendants. Table rows remain one block for the structural table adapter.
    """
    tree, selected = root.getroottree(), []
    boundaries = {"p", "li", "tr", "div", "body", "section", "article", "main", "aside", "blockquote", "pre", "caption", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
    def excluded(node):
        return _hidden_markup(node)
    def walk(node):
        if excluded(node):
            return
        kind, locator = _local(node.tag), tree.getpath(node)
        if kind == "tr":
            # Nested tables are rejected structurally, but text stays visible.
            text = _visible_text(node)
            if text:
                selected.append((locator, text, kind))
            return
        parts, serial = [], 0
        def flush():
            nonlocal parts, serial
            text = _SPACE.sub(" ", " ".join(parts)).strip()
            if text:
                serial += 1
                selected.append((locator if serial == 1 else f"{locator}/text-run:{serial}", text, kind))
            parts = []
        def inline(el):
            if excluded(el):
                return
            if el.text:
                parts.append(el.text)
            for child in el:
                if _local(child.tag) in boundaries:
                    flush()
                    walk(child)
                else:
                    inline(child)
                if child.tail:
                    parts.append(child.tail)
        inline(node)
        flush()
    walk(root)
    return selected


def _table_block_evidence(root):
    """Locate every selected block touching a table, including nested blocks.

    Table rejection must follow DOM structure, not styling/issuer keywords.
    A paragraph inside a cell and a list item containing a table are both table
    input, even if the block selector does not return a tr element.
    """
    tree = root.getroottree()
    evidence = {}
    for node in root.iter():
        if _local(node.tag) not in {"p","li","tr","h1","h2","h3","h4","h5","h6","div","body"}:
            continue
        tables = [ancestor for ancestor in node.iterancestors() if _local(ancestor.tag)=="table"]
        if not tables:
            tables = [child for child in node.iterdescendants() if _local(child.tag)=="table"]
        if tables:
            table = tables[0]
            evidence[tree.getpath(node)] = {"source_locator":tree.getpath(table),"evidence_excerpt":_text(table)}
    return evidence


def _html_root(content):
    if re.search(br"<!ENTITY\s", content, re.I):
        raise ValueError("Documents declaring entities are not parsed")
    encoding = None
    if not re.search(br'(?:charset\s*=|encoding\s*=)', content[:4096], re.I):
        try:
            content.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            pass
    root = html.fromstring(content, parser=html.HTMLParser(no_network=True, remove_comments=True, encoding=encoding))
    decoded = content.decode(encoding or root.getroottree().docinfo.encoding or "utf-8")
    lexical = _QNameScopes(decoded)
    lexical.feed(decoded)
    lexical.close()
    targets = [node for node in root.iter() if _local(node.tag) in {"measure", "nonfraction", "fraction", "explicitmember", "typedmember"}]
    if [_local(node.tag) for node in targets] != [kind for kind, _ in lexical.targets]:
        raise ValueError("QName-bearing DOM elements do not align with original namespace scopes")
    root._source_qname_scopes = {node: scope for node, (_, scope) in zip(targets, lexical.targets)}
    root._source_dimension_evidence = {node: {"source_markup": lexical.source_fragments[index],
        "namespace_bindings": {prefix or "": uri for prefix, uri in scope.items()}}
        for index, (node, (kind, scope)) in enumerate(zip(targets, lexical.targets))
        if kind in {"explicitmember", "typedmember"} and index in lexical.source_fragments}
    return root


def _empty_numeric_markup(content):
    """Exact empty tag evidence, keyed by its source identity attributes."""
    text = content.decode("utf-8",errors="replace")
    result = {}
    pattern = r'<(?P<tag>[\w.-]+:(?:nonFraction|fraction))\b(?P<attrs>[^>]*?)(?:/\s*>|>\s*</(?P=tag)\s*>)'
    for match in re.finditer(pattern,text,re.I):
        attributes = {m[1].lower():m[3] for m in re.finditer(r'''([\w:.-]+)\s*=\s*(["'])(.*?)\2''',match['attrs'],re.S)}
        identity = tuple(attributes.get(field) for field in ("name","contextref","unitref","id"))
        result.setdefault(identity,match.group())
    return result


def parse_document(content: bytes, *, media_type="text/html", document_type=None, company_id=None, source_url=None, context_documents=None) -> dict:
    """Extract supported facts plus evidence candidates; gaps remain explicit.

    Context, unit and fact locators refer to exact saved raw content. Neither
    company identity nor source URL changes extraction rules. Fiscal period
    labels are not converted into dates without explicit source dates. Optional
    context_documents must contain saved, caller-verified same-document-set
    evidence {content:bytes, raw_id, sha256, source_url (or url)}. Only contexts
    and units are shared; facts/narrative in those documents are not emitted.
    """
    if not isinstance(content, bytes):
        raise TypeError("parse_document requires original bytes")
    result = {"parser_version": PARSER_VERSION, "observations": [], "issues": [], "coverage": {}}
    observations, issues = result["observations"], result["issues"]
    scope = source_url or company_id or "document"
    raw_hash = hashlib.sha256(content).hexdigest()
    origin = {"raw_id":"sha256:"+raw_hash,"sha256":raw_hash,"source_url":source_url}
    blocks, table_evidence, table_nodes = [], {}, {}
    if media_type.split(";")[0] == "application/pdf" or content.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            for index, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    issues.append(_issue("PDF_NO_TEXT", "Page has no extractable text; OCR is not implemented", f"page:{index}"))
                for line, paragraph in enumerate(re.split(r"\n\s*\n", text), 1):
                    if paragraph.strip():
                        blocks.append((f"page:{index}/text-block:{line}", _SPACE.sub(" ", paragraph).strip(), "pdf"))
            result["coverage"]["pdf_pages"] = len(reader.pages)
        except ImportError:
            issues.append(_issue("PDF_DEPENDENCY_MISSING", "Install pypdf for PDF text extraction"))
        except Exception as exc:
            issues.append(_issue("PDF_EXTRACTION_FAILED", str(exc)))
    elif media_type.split(";")[0] in {"text/html", "application/xhtml+xml", "text/xml", "application/xml"}:
        if re.search(br"<!ENTITY\s", content, re.I):
            issues.append(_issue("EXTERNAL_ENTITY_REJECTED", "Documents declaring entities are not parsed"))
        else:
            try:
                root = _html_root(content)
                context_roots, seen_hashes = [], {raw_hash}
                for support in context_documents or []:
                    try:
                        support_content = support.get("content")
                        if not isinstance(support_content,bytes):
                            raise ValueError("Supporting document content must be saved original bytes")
                        digest = hashlib.sha256(support_content).hexdigest()
                        if support.get("sha256") != digest or support.get("raw_id") != "sha256:"+digest:
                            raise ValueError("Supporting document SHA-256/raw ID mismatch")
                        if digest in seen_hashes:
                            continue
                        seen_hashes.add(digest)
                        context_roots.append((_html_root(support_content),{"raw_id":support["raw_id"],"sha256":digest,
                                             "source_url":support.get("source_url",support.get("url"))}))
                    except (ValueError,TypeError,etree.ParserError,etree.XMLSyntaxError) as exc:
                        issues.append(_issue("IXBRL_SUPPORT_DOCUMENT_REJECTED",str(exc),raw_id=support.get("raw_id")))
                result["coverage"]["inline_xbrl"] = _inline_xbrl(root, observations, issues, scope, origin=origin,
                    context_roots=context_roots,lexical_facts=_empty_numeric_markup(content))
                result["coverage"]["supporting_raw_ids"] = result["coverage"]["inline_xbrl"]["supporting_raw_ids"]
                for el in list(root.iter()):
                    if _local(el.tag) == "script":
                        el.drop_tree()
                blocks = _html_blocks(root)
                table_evidence = _table_block_evidence(root)
                table_nodes = {root.getroottree().getpath(node): node for node in root.iter() if _local(node.tag) == "table"}
            except (etree.ParserError, etree.XMLSyntaxError, ValueError) as exc:
                issues.append(_issue("DOCUMENT_PARSE_FAILED", str(exc)))
    elif media_type.split(";")[0] == "text/plain":
        blocks = [(f"text-block:{i}", _SPACE.sub(" ", text).strip(), "plain") for i, text in enumerate(re.split(r"\n\s*\n", content.decode("utf-8", errors="replace")), 1) if text.strip()]
    else:
        issues.append(_issue("UNSUPPORTED_MEDIA_TYPE", "No parser for this media type", media_type=media_type))
    guidance_candidates = guidance_parsed = 0
    context_block = None
    handled_tables = set()
    for locator, text, kind in blocks:
        _narrative(text, locator, observations, scope)
        candidate_text, candidate_locator = text, locator
        table_context = table_evidence.get(locator.split("/text-run:")[0])
        if table_context and table_context["source_locator"] in table_nodes:
            table_locator = table_context["source_locator"]
            if table_locator not in handled_tables:
                external = context_block if context_block and context_block[2] is None else None
                candidates, parsed = _table_guidance(table_nodes[table_locator], external, observations, issues, scope)
                guidance_candidates += candidates
                guidance_parsed += parsed
                handled_tables.add(table_locator)
            context_block = None
            continue
        if context_block and context_block[2] is not None and (table_context is None or context_block[2]["source_locator"] != table_context["source_locator"]):
            # A table's period/segment heading cannot leak into a later prose
            # paragraph or a different table after that table is closed.
            context_block = None
        if context_block and context_block[0] != locator and re.search(r"\b(?:actual|historical|prior[- ]year|previous[- ]year)\b", text, re.I):
            issues.append(_issue("GUIDANCE_CONTEXT_INTERRUPTED", "Historical or actual-result text interrupts guidance heading scope", locator,
                                 evidence_excerpt=text, previous_heading_locator=context_block[0], previous_heading=context_block[1]))
            context_block = None
        if kind.startswith("h"):
            context_block = (locator, text, table_context) if _GUIDANCE.search(text) else None
        elif _GUIDANCE.search(text) and not _METRICS.search(text) and len(text) < 400:
            context_block = (locator, text, table_context)
        if context_block and context_block[0] != locator and not _GUIDANCE.search(text):
            candidate_text = context_block[1] + " " + text
            candidate_locator = context_block[0] + " + " + locator
        if table_context is not None and context_block and context_block[0] != locator and context_block[2] is None:
            table_context = {"source_locator":context_block[0]+" + "+table_context["source_locator"],
                             "evidence_excerpt":context_block[1]+" "+table_context["evidence_excerpt"]}
        candidate, accepted = _guidance(candidate_text, candidate_locator, observations, issues, scope, table_context=table_context)
        guidance_candidates += int(candidate)
        guidance_parsed += int(accepted)
        if context_block and context_block[0] != locator and not accepted and not _GUIDANCE.search(text):
            issues.append(_issue("GUIDANCE_CONTEXT_INTERRUPTED", "Unrelated or unparsed text interrupts guidance heading scope", locator,
                                 evidence_excerpt=text, previous_heading_locator=context_block[0], previous_heading=context_block[1]))
            context_block = None
    for observation in observations:
        observation["data"]["evidence_excerpt"] = observation["evidence_excerpt"]
        observation["data"]["content_sha256"] = raw_hash
        observation["data"]["parser_version"] = PARSER_VERSION
        observation["data"]["source_document_type"] = document_type
        from .contracts import annotate_contract
        annotate_contract(observation)
    result["text_blocks"] = [{"source_locator": locator, "text": text, "kind": kind} for locator, text, kind in blocks]
    result["coverage"].update(text_blocks=len(blocks), guidance_candidates=guidance_candidates,
        guidance_parsed=guidance_parsed, guidance_rejected=guidance_candidates-guidance_parsed,
        narrative_candidates=sum(o["data"].get("extraction_method") == "narrative_evidence_candidate" for o in observations),
        observation_count=len(observations), issue_count=len(issues),
        fact_stage_counts=dict(Counter(o["data"].get("fact_stage", "unclassified") for o in observations)),
        document_semantically_complete=False,
        limitations=["No taxonomy validation or company-specific semantic mapping", "Typed dimension values are semantically unverified; only identical original fragments and namespace bindings may reuse a context definition, other typed equivalence is rejected without a domain schema", "Narrative categories are evidence candidates, not verified relations or amounts", "Unsupported/ambiguous guidance is rejected", "Table guidance supports rectangular metric rows and explicit forecast headers only; merged, nested, segment, ambiguous footnote layouts are rejected", "Publication time must be supplied by source evidence", "PDF OCR and arbitrary table numeric interpretation are not implemented"])
    result["status"] = "partial" if issues else "parsed"
    return result
