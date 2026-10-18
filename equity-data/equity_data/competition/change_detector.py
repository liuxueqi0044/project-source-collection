"""Pure, conservative snapshots and changes; no network or storage writes.

CSS rules and observation context are configuration, never financial answers.
Every extracted field retains its source text. Failed acquisition is not a
snapshot and a first snapshot is not evidence of a price change.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import hashlib
import json
import re

from bs4 import BeautifulSoup

PARSER_VERSION = "competition-snapshot-v5.0.2"
_SPACE = re.compile(r"\s+")
_NUM = re.compile(r"^\s*(?P<paren>\()?\s*(?P<s1>[+\-−])?\s*(?P<currency>USD|EUR|GBP|JPY|CNY|US\$|\$|€|£)?\s*(?P<s2>[+\-−])?\s*(?P<n>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<scale>billion|million|thousand)?\s*(?P<percent>%|percent)?\s*(?P<close>\))?\s*$", re.I)


def clean(value):
    return _SPACE.sub(" ", str(value or "")).strip()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def parse_time(value):
    """Return only source supplied timezone or day precision; do not assume UTC."""
    value = clean(value)
    if not value:
        return None, "unknown"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value, "day"
        except ValueError:
            return None, "unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None, "unknown"
    if parsed.tzinfo is None:
        return None, "unknown"
    return parsed.isoformat(), "second"


def numeric_field(text, specification=None):
    """Strict decimal grammar; preserve signs, parentheses and stated scales."""
    specification = specification or {}
    match = _NUM.fullmatch(clean(text))
    if not match:
        return {"value": None, "issue": "unsupported_or_ambiguous_numeric_text"}
    g = match.groupdict()
    if bool(g["paren"]) != bool(g["close"]) or (g["s1"] and g["s2"]) or (g["paren"] and (g["s1"] or g["s2"])):
        return {"value": None, "issue": "ambiguous_numeric_sign"}
    cur = g["currency"]
    canonical = {"US$": "USD", "€": "EUR", "£": "GBP"}.get(cur, cur.upper() if cur else None)
    if cur == "$":
        canonical = specification.get("currency")
        if not canonical:
            return {"value": None, "issue": "ambiguous_dollar_currency"}
    declared = specification.get("currency")
    if declared and canonical and declared.upper() != canonical.upper():
        return {"value": None, "issue": "currency_configuration_conflict"}
    try:
        value = Decimal(g["n"].replace(",", ""))
        scale = {None: 1, "thousand": 1000, "million": 1000000, "billion": 1000000000}[g["scale"].lower() if g["scale"] else None]
        value *= scale
        if g["paren"] or (g["s1"] or g["s2"]) in ("-", "−"):
            value = -value
    except InvalidOperation:
        return {"value": None, "issue": "invalid_decimal"}
    return {"value": format(value, "f"), "currency": canonical or declared,
            "unit": "%" if g["percent"] else specification.get("unit"),
            "scale": scale, "original_text": clean(text)}


def _selected(soup, specification):
    if isinstance(specification, str):
        specification = {"selector": specification}
    specification = specification or {}
    selector = specification.get("selector")
    if not selector:
        return None, "document:text", "selector_missing"
    try:
        nodes = soup.select(selector)
    except Exception:
        return None, "css:" + selector, "invalid_selector"
    if len(nodes) != 1:
        return None, "css:" + selector, "field_missing" if not nodes else "ambiguous_multiple_fields"
    node = nodes[0]
    value = node.get(specification["attribute"]) if specification.get("attribute") else node.get_text(" ", strip=True)
    return clean(value), "css:" + selector, None


def _json_path(obj, path):
    for key in str(path).removeprefix("$.").split("."):
        obj = obj[int(key)] if isinstance(obj, list) else obj[key]
    return obj


def normalize_snapshot(content: bytes, endpoint: dict) -> dict:
    selectors = endpoint.get("selectors") or {}
    result = {"valid": True, "body_text": "", "body_hash": None,
              "raw_content_hash": digest(content), "fields": {}, "title": None,
              "locators": [], "publication_time": None, "publication_precision": "unknown",
              "issues": [], "parser_version": PARSER_VERSION,
              "observation_context": endpoint.get("observation_context") or {},
              "config_signature": digest({"selectors": selectors, "format": endpoint.get("format"),
                  "ignore_selectors": endpoint.get("ignore_selectors", []),
                  "observation_context": endpoint.get("observation_context") or {},
                  "selector_version": endpoint.get("selector_version")})}
    if not content or int(endpoint.get("response_status", 200)) >= 400:
        result.update(valid=False, issues=["acquisition_failed_or_empty"])
        return result
    fmt = (endpoint.get("format") or endpoint.get("media_type") or endpoint.get("content_type") or "html").split(";")[0].lower()
    try:
        text = content.decode(endpoint.get("encoding", "utf-8"), errors="strict")
    except (UnicodeDecodeError, LookupError):
        result.update(valid=False, issues=["encoding_not_decodable"])
        return result
    if fmt in ("html", "text/html"):
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title
        result["title"] = clean(title.get_text(" ")) if title else None
        if selectors.get("title"):
            result["title"], _, issue = _selected(soup, selectors["title"])
            if issue:
                result["issues"].append("title:" + issue)
        date_selector = selectors.get("publication_time")
        if date_selector:
            value, locator, issue = _selected(soup, date_selector)
            result["publication_time"], result["publication_precision"] = parse_time(value)
            result["publication_evidence"] = {"locator": locator, "excerpt": value}
            if issue or result["publication_time"] is None:
                result["issues"].append("publication_time:" + (issue or "unresolved_time_or_timezone"))
        else:
            meta = soup.find("meta", attrs={"property": "article:published_time"})
            if meta and meta.get("content"):
                result["publication_time"], result["publication_precision"] = parse_time(meta["content"])
                result["publication_evidence"] = {"locator": "css:meta[property='article:published_time']", "excerpt": meta["content"], "attribute": "content"}
        for name, spec in (selectors.get("fields") or endpoint.get("field_selectors") or {}).items():
            spec = {"selector": spec} if isinstance(spec, str) else spec
            value, locator, issue = _selected(soup, spec)
            field = {"value": value, "original_text": value, "locator": locator, "excerpt": value, "kind": spec.get("kind", "text")}
            if issue:
                field.update(value=None, issue=issue)
                result["issues"].append(name + ":" + issue)
            elif spec.get("kind") in ("number", "price"):
                field.update(numeric_field(value, spec))
                if field.get("issue"):
                    result["issues"].append(name + ":" + field["issue"])
            result["fields"][name] = field
        for node in soup.select("script,style,noscript,template"):
            node.decompose()
        for selector in endpoint.get("ignore_selectors", []):
            try:
                for node in soup.select(selector):
                    node.decompose()
            except Exception:
                result["valid"] = False
                result["issues"].append("invalid_ignore_selector:" + selector)
        body_selector = selectors.get("body")
        if body_selector:
            try:
                bodies = soup.select(body_selector)
            except Exception:
                bodies = []
            if not bodies:
                result.update(valid=False)
                result["issues"].append("body_selector_missing")
        else:
            for node in soup.select("nav,footer"):
                node.decompose()
            bodies = [soup.find("article") or soup.find("main") or soup.body or soup]
        result["body_text"] = "\n".join(clean(node.get_text(" ", strip=True)) for node in bodies)
        result["locators"] = [{"locator": "css:" + body_selector if body_selector else "document:text", "excerpt": result["body_text"]}]
    elif fmt in ("rss", "atom", "xml", "application/rss+xml", "application/atom+xml", "application/xml", "text/xml"):
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            result.update(valid=False, issues=["xml_entities_not_supported"])
            return result
        soup = BeautifulSoup(text, "xml")
        entries = soup.find_all(["item", "entry"])
        if not entries:
            result.update(valid=False, issues=["feed_has_no_entries_or_unsupported_xml"])
            return result
        result["items"] = []
        for index, item in enumerate(entries):
            item_text = clean(BeautifulSoup(item.get_text(" ", strip=True), "html.parser").get_text(" ", strip=True))
            title = item.find("title")
            pub = item.find(["pubDate", "published"])
            link = item.find("link")
            guid = item.find(["guid", "id"])
            timestamp, precision = parse_time(pub.get_text() if pub else None)
            result["items"].append({"body_text": item_text, "title": clean(title.get_text()) if title else None,
                "publication_time": timestamp, "publication_precision": precision,
                "url": link.get("href") or clean(link.get_text()) if link else None,
                "source_id": clean(guid.get_text()) if guid else None,
                "locator": "document:text", "item_index": index})
        result["body_text"] = "\n".join(i["body_text"] for i in result["items"])
        result["locators"] = [{"locator": "document:text", "excerpt": i["body_text"]} for i in result["items"]]
    elif fmt in ("json", "application/json"):
        try:
            obj = json.loads(text, parse_float=Decimal)
            path = selectors.get("body")
            body = _json_path(obj, path) if path else obj
            result["body_text"] = clean(body) if isinstance(body, str) else json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
            for name, spec in (selectors.get("fields") or {}).items():
                spec = {"path": spec} if isinstance(spec, str) else spec
                try:
                    val = _json_path(obj, spec["path"])
                    if not isinstance(val, (str, int, Decimal)) or isinstance(val, bool):
                        raise ValueError("not_scalar")
                    field = {"value": str(val), "original_text": str(val), "excerpt": str(val), "locator": "document:text", "json_path": spec["path"], "kind": spec.get("kind", "text")}
                    if spec.get("kind") in ("number", "price"):
                        field.update(numeric_field(str(val), spec))
                    result["fields"][name] = field
                except (KeyError, TypeError, IndexError, ValueError):
                    result["fields"][name] = {"value": None, "issue": "json_field_missing_or_non_scalar"}
                    result["issues"].append(name + ":json_field_missing_or_non_scalar")
            if selectors.get("publication_time"):
                result["publication_time"], result["publication_precision"] = parse_time(_json_path(obj, selectors["publication_time"]))
            if selectors.get("title"):
                result["title"] = clean(_json_path(obj, selectors["title"]))
            result["locators"] = [{"locator": "document:text", "excerpt": result["body_text"]}]
        except (ValueError, TypeError, KeyError, IndexError):
            result.update(valid=False, issues=["invalid_json_or_selector"])
            return result
    elif fmt in ("text", "text/plain"):
        result["body_text"] = clean(text)
        result["locators"] = [{"locator": "document:text", "excerpt": result["body_text"]}]
    else:
        result.update(valid=False, issues=["unsupported_snapshot_format:" + fmt])
        return result
    if not result["body_text"].strip():
        result["valid"] = False
        result["issues"].append("empty_body")
    if re.match(r"^(?:access denied|forbidden|please (?:sign|log) in|just a moment|checking your browser)\b", result["body_text"], re.I):
        result["valid"] = False
        result["issues"].append("access_or_interstitial_page")
    short_body=result['body_text'][:4000]
    if (len(result['body_text'])<8000 and re.search(r'(?:cannot be accessed from your current location|website is only available in|not available in your (?:country|region)|CLOUDFLARE_ERROR_\w+)',short_body,re.I)):
        result['valid']=False
        result['issues'].append('geographic_or_access_interstitial')
    result["body_hash"] = digest(result["body_text"].encode())
    return result


def compare_snapshots(before: dict | None, after: dict, endpoint: dict) -> dict:
    if not after.get("valid", True) or not after.get("body_text"):
        return {"status": "rejected", "changes": [], "issues": ["invalid_current_snapshot", *after.get("issues", [])]}
    if before is None:
        return {"status": "baseline", "changes": [], "issues": []}
    if not before.get("valid", True):
        return {"status": "rejected", "changes": [], "issues": ["invalid_previous_snapshot"]}
    if before.get("parser_version") != after.get("parser_version") or before.get("config_signature") != after.get("config_signature"):
        return {"status": "rejected", "changes": [], "issues": ["parser_or_observation_configuration_changed_reparse_both_versions"]}
    fields = []
    for name in sorted(set(before.get("fields", {})) | set(after.get("fields", {}))):
        old, new = before.get("fields", {}).get(name), after.get("fields", {}).get(name)
        comparable = lambda f: {k: f.get(k) for k in ("value", "unit", "currency", "issue")} if f else None
        if comparable(old) != comparable(new):
            fields.append({"field": name, "before": old, "after": new,
                           "status": "changed" if old and new and old.get("value") is not None and new.get("value") is not None else "unresolved_missing_field"})
    same_body = before.get("body_hash") == after.get("body_hash")
    clock_changed=any(before.get(key)!=after.get(key) for key in ('publication_time','publication_precision'))
    if same_body and not fields and not clock_changed:
        return {"status": "no_change" if before.get("raw_content_hash") == after.get("raw_content_hash") else "noise_only", "changes": [], "issues": []}
    return {"status": "changed", "changes": fields, "body_changed": not same_body,"publication_changed":clock_changed,
            "observation_interval": {"after": before.get("observed_at"), "on_or_before": after.get("observed_at")},
            "issues": ["body_change_requires_event_extraction"] if not fields else []}
