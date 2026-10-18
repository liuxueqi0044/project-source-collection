"""Bounded, explicitly configured public news feeds and policy documents.

Feed content is a captured feed entry, not a licensed full-text news archive.
Neither a current feed nor a current policy rendition proves old version timing.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .providers import _put

PARSER_VERSION = "research-1.0"
ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def _canonical(url):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def _timestamp(value):
    if not value:
        return None, "unknown", None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        # Preserve a date as a date, never use midnight as asserted release time.
        return value, "day", None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None, "unknown", None
    if parsed.tzinfo is None:
        return None, "unknown", None
    return parsed.astimezone(timezone.utc).isoformat(), "second", str(parsed.tzinfo)


def _text(node, *names):
    for name in names:
        child = node.find(name)
        if child is not None:
            # Keep XML/XHTML markup where supplied; text-only RSS descriptions
            # are entity decoded by XML exactly once, with raw bytes retained.
            return (child.text or "") + "".join(ET.tostring(c, encoding="unicode") for c in child)
    return ""


def _feed_entries(content, feed_url):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("DTD/entity declarations not accepted in feeds")
    root = ET.fromstring(content)
    is_atom = root.tag == ATOM + "feed"
    nodes = root.findall(ATOM + "entry") if is_atom else root.findall("./channel/item")
    for index, item in enumerate(nodes):
        if is_atom:
            links = [link for link in item.findall(ATOM + "link") if link.get("rel", "alternate") == "alternate"]
            link = links[0].get("href", "") if links else ""
            publication = _text(item, ATOM + "published")
            updated = _text(item, ATOM + "updated")
            entry = {"title": _text(item, ATOM + "title"), "content": _text(item, ATOM + "content", ATOM + "summary"),
                     "source_url": None, "source_name": None}
        else:
            link = _text(item, "link")
            publication = _text(item, "pubDate", DC + "date")
            updated = _text(item, "updated", ATOM + "updated")
            source = item.find("source")
            entry = {"title": _text(item, "title"), "content": _text(item, CONTENT + "encoded", "description"),
                     "source_url": source.get("url") if source is not None else None,
                     "source_name": source.text if source is not None else None}
        link = urljoin(feed_url, link.strip()) if link else None
        if not link or urlsplit(link).scheme not in {"http", "https"}:
            yield index, None
            continue
        entry.update({"canonical_url": _canonical(link), "publication": publication, "updated": updated})
        yield index, entry


def _collect_feeds(store, client, config, cfg):
    count, failures = 0, []
    for specification in cfg.get("feeds", []):
        feed = {"url": specification} if isinstance(specification, str) else specification
        if not feed.get("enabled", True):
            continue
        url = feed["url"]
        try:
            if urlsplit(url).scheme not in {"http", "https"}:
                raise ValueError("Feed URL must be HTTP(S)")
            content, raw = client.fetch(url, source="public_news_feed")
            limit = min(500, max(1, int(feed.get("max_entries", cfg.get("max_feed_entries", 50)))))
            entries = list(_feed_entries(content, url))
            for index, entry in entries[:limit]:
                if entry is None:
                    store.issue("news_missing_original_url", "Feed entry without an HTTP(S) article URL retained only in raw feed.", dataset="news")
                    continue
                published, precision, zone = _timestamp(entry.pop("publication"))
                updated, updated_precision, updated_zone = _timestamp(entry.pop("updated"))
                start, end = feed.get("start", config["start"]), feed.get("end", config["end"])
                if published and not start <= published[:10] <= end:
                    continue
                digest = hashlib.sha256((entry["title"] + "\n" + entry["content"]).encode()).hexdigest()
                entry.update({"content_sha256": digest, "updated_at": updated, "updated_precision": updated_precision,
                              "source_published_at": published, "publication_precision": precision, "publication_timezone": zone,
                              "updated_timezone": updated_zone, "content_scope": "feed_entry_only", "article_full_text_retrieved": False,
                              "event_cluster_id": None, "event_cluster_status": "needs_review", "historical_version_time_verified": False,
                              "duplicate_detection": "canonical_url_and_content_hash_only", "version_publication_status": "source_reported_unverified"})
                _put(store, "news", entry["canonical_url"], entry, source="public_news_feed", raw_id=raw["raw_id"],
                     company_id=feed.get("company_id"), security_id=feed.get("security_id"),
                     published_at=published, publication_precision=precision, publication_timezone=zone,
                     available_at=raw["retrieved_at"], availability_basis="first_observed_feed_version",
                     source_locator=f"{'Atom entry' if b'http://www.w3.org/2005/Atom' in content else 'RSS item'}[{index + 1}]",
                     quality="unverified", parser_version=PARSER_VERSION)
                count += 1
            if len(entries) > limit:
                store.issue("sample_limit", "Configured feed entry limit reached; older/archive articles not acquired.", dataset="news", details={"url": url, "available_entries": len(entries), "limit": limit})
        except Exception as exc:
            failures.append({"url": url, "error_type": type(exc).__name__})
            store.issue("adapter_failed", "Configured news feed failed; no inferred news created.", dataset="news", details=failures[-1])
    return {"count": count, "failures": failures}


def _trusted_policy_url(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(host == domain or host.endswith("." + domain)
                                             for domain in ("federalregister.gov", "govinfo.gov"))


def _collect_policy(store, client, config, cfg):
    policy = cfg.get("policy", {})
    if not policy.get("enabled", False):
        return {"count": 0, "status": "disabled", "failures": []}
    count, full_count, failures, seen = 0, 0, [], set()
    maximum = min(100, max(1, int(policy.get("max_documents", 3))))
    for term in policy.get("terms", []):
        if count >= maximum:
            break
        try:
            content, search_raw = client.fetch("https://www.federalregister.gov/api/v1/documents.json", source="federal_register",
                params={"conditions[term]": term, "conditions[publication_date][gte]": policy.get("start", config["start"]),
                        "conditions[publication_date][lte]": policy.get("end", config["end"]), "per_page": maximum, "order": "newest"})
            search = json.loads(content)
            if int(search.get("count", 0)) > maximum:
                store.issue("sample_limit", "Federal Register search limited to configured document sample, not complete history.",
                            dataset="policy_documents", details={"term": term, "total_results": search["count"], "limit": maximum})
            for result in search.get("results", []):
                number = result.get("document_number")
                if not number or number in seen or count >= maximum:
                    continue
                if not re.fullmatch(r"[0-9A-Za-z-]+", number):
                    raise ValueError("Unexpected document number")
                seen.add(number)
                detail_content, detail_raw = client.fetch(f"https://www.federalregister.gov/api/v1/documents/{number}.json", source="federal_register")
                detail = json.loads(detail_content)
                full_url = detail.get("body_html_url") or detail.get("html_url")
                full_raw = None
                if full_url and _trusted_policy_url(full_url):
                    try:
                        full_content, full_raw = client.fetch(full_url, source="federal_register")
                        if not full_content.strip():
                            raise ValueError("Empty full text")
                        full_count += 1
                    except Exception as exc:
                        full_raw = None
                        failures.append({"document_number": number, "stage": "full_text", "error_type": type(exc).__name__})
                        store.issue("policy_full_text_missing", "Policy metadata acquired but full-text request failed.", dataset="policy_documents", details=failures[-1])
                else:
                    store.issue("policy_full_text_missing", "Policy detail has no trusted government HTML URL.", dataset="policy_documents", details={"document_number": number})
                public_date = detail.get("publication_date")
                data = {"document_number": number, "title": detail.get("title"), "abstract": detail.get("abstract"),
                        "document_type": detail.get("type"), "publication_date": public_date, "effective_date": detail.get("effective_on"),
                        "agencies": [agency.get("name") for agency in detail.get("agencies", [])], "citation": detail.get("citation"),
                        "html_url": detail.get("html_url"), "pdf_url": detail.get("pdf_url"), "body_html_url": detail.get("body_html_url"),
                        "correction_of": detail.get("correction_of"), "correction_relationship_status": "source_field_only_not_inferred",
                        "metadata_raw_id": detail_raw["raw_id"], "full_text_raw_id": full_raw["raw_id"] if full_raw else None,
                        "full_text_sha256": full_raw["sha256"] if full_raw else None,
                        "full_text_status": "acquired" if full_raw else "missing",
                        "historical_version_time_verified": False, "industry_relevance": "search_term_candidate_needs_review",
                        "legal_edition": "informational_HTML_verify_against_official_govinfo_PDF"}
                _put(store, "policy_documents", number, data, source="federal_register", raw_id=(full_raw or detail_raw)["raw_id"],
                     published_at=public_date, publication_precision="day" if public_date else "unknown",
                     publication_timezone="America/New_York" if public_date else None,
                     available_at=(full_raw or detail_raw)["retrieved_at"], availability_basis="first_observed_policy_rendition",
                     event_date=detail.get("effective_on"), source_locator="document JSON; full_text_raw_id references captured HTML",
                     quality="unverified" if full_raw else "missing", parser_version=PARSER_VERSION)
                count += 1
        except Exception as exc:
            failures.append({"term": term, "error_type": type(exc).__name__})
            store.issue("adapter_failed", "Federal Register sample collection failed; acquired evidence retained.", dataset="policy_documents", details=failures[-1])
    return {"count": count, "full_text_count": full_count, "failures": failures, "status": "sample_only"}


def collect_research(store, client, config):
    """P2 demonstration entry point; only configured URLs and bounded searches."""
    cfg = config.get("research", {})
    if not cfg.get("enabled", False):
        return {"status": "disabled", "count": 0, "reason": "research.enabled is false"}
    feeds = _collect_feeds(store, client, config, cfg)
    policy = _collect_policy(store, client, config, cfg)
    failures = feeds["failures"] + policy["failures"]
    return {"status": "partial" if failures else "sample_only", "count": feeds["count"] + policy["count"],
            "counts": {"news": feeds["count"], "policy_documents": policy["count"]}, "policy": policy, "failures": failures,
            "limitations": ["Feeds are entry snapshots, not full news archive", "Policy HTML is informational rendition; official PDF not downloaded", "Current document versions are visible only from acquisition"]}
