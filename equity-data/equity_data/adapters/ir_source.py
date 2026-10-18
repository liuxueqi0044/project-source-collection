"""Evidence-led official IR discovery for static HTML, RSS/Atom and files.

This adapter does not guess issuer domains, execute JavaScript or equate a
current page with a historical copy. Unsupported pages remain explicit gaps.
"""
from __future__ import annotations

from datetime import datetime
import email.utils
import hashlib
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .base import BaseAdapter, UnsupportedCapability, persist_observations
from ..parsers import parse_document

PARSER_VERSION = "official-ir-2.0"
DOCUMENT_PARSER_VERSION = "official-ir-document-2.1"
_IR = re.compile(r"investor\s*(?:relations|information)?|shareholder", re.I)
_DOCUMENT = re.compile(r"earnings|(?:financial|quarterly|annual)\s+(?:results|report)|press\s+release|news\s+release|presentation|prepared\s+remarks|transcript|conference\s+call|webcast|outlook|guidance", re.I)
_NAV = re.compile(r"\b(?:next|older|previous|archive|archives|page\s*\d+|press|news|events?|presentations?|financials?|reports?|results|filings|earnings|quarterly|annual)\b", re.I)
_PATH = re.compile(r"(?:news[-_/]releases?|press[-_/]releases?|news[-_/]details?|event[-_/]details?|financial[-_/]results|earnings|presentations?|annual[-_/]reports?)", re.I)
_LIST_PATH = re.compile(r"(?:/news/?$|/events?/?$|/press/?$|archive|pagination|page=|page/\d|/financials?(?:/|$))", re.I)
_NON_DOCUMENT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico", ".woff", ".woff2"}
_FILES = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx"}


def _url(value, base=None):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    if value.startswith("www."):
        value = "https://" + value
    elif base:
        value = urljoin(base, value)
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return None
    if p.hostname.lower() in ("localhost", "localhost.localdomain"):
        return None
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            return None
    except ValueError:
        pass
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", p.query, ""))


def _same_site(url, root):
    hostname, root_host = urlsplit(url).hostname or "", urlsplit(root).hostname or ""
    return hostname == root_host or hostname.endswith("." + root_host) or root_host.endswith("." + hostname)


def _date(value):
    if not value:
        return {}
    value = str(value).strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return {"published_at": value, "publication_precision": "day", "publication_timezone": None}
        if parsed.tzinfo is not None:
            return {"published_at": parsed.isoformat(), "publication_precision": "second", "publication_timezone": str(parsed.tzinfo)}
        return {"published_at": value, "publication_precision": "unverified_local_clock", "publication_timezone": None}
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed and parsed.tzinfo:
            return {"published_at": parsed.isoformat(), "publication_precision": "second", "publication_timezone": str(parsed.tzinfo)}
    except (ValueError, TypeError):
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return {"published_at": datetime.strptime(value, fmt).date().isoformat(), "publication_precision": "day", "publication_timezone": None}
        except ValueError:
            pass
    return {}


def _link_date(anchor):
    for value in (anchor.get("data-date"), anchor.get("datetime")):
        if _date(value):
            return _date(value)
    parent = anchor.find_parent(["article", "li", "tr"])
    if parent:
        time = parent.find("time")
        if time:
            return _date(time.get("datetime") or time.get_text(" ", strip=True))
    return {}


def _page_dates(soup):
    result = {}
    for name, output in (("article:published_time", "published_at"), ("datePublished", "published_at"),
                         ("article:modified_time", "updated_at"), ("dateModified", "updated_at")):
        node = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"itemprop": name})
        evidence = _date(node.get("content")) if node else {}
        if evidence:
            if output == "published_at":
                result.update(evidence)
            else:
                result[output] = evidence["published_at"]
    return result


def _document_title(content):
    """Read the downloaded document's title, never its incoming link label.

    Discovery labels remain on resource metadata. They describe the referring
    page and can change with traversal order despite identical document bytes.
    Feed containers and individual entries have separate title scopes.
    """
    absent = {"status": "unknown", "reason": "no_supported_title_in_original"}
    if content.startswith((b"%PDF", b"PK\x03\x04")):
        return None, {"status": "unknown", "reason": "file_title_parser_unavailable"}
    feed_like = bool(re.search(rb"<(?:rss|feed|item|entry)(?:\s|>)", content[:4096], re.I))
    if feed_like:
        soup = BeautifulSoup(content, "xml")
        root = soup.find(True)
        if root is None:
            return None, absent
        if root.name == "rss":
            container = root.find("channel", recursive=False)
            scope, locator = "whole_feed", "/rss/channel/title"
        elif root.name == "feed":
            container, scope, locator = root, "whole_feed", "/feed/title"
        elif root.name in ("item", "entry"):
            container, scope, locator = root, "feed_item", "/" + root.name + "/title"
        else:
            return None, absent
        node = container.find("title", recursive=False) if container else None
        title = node.get_text(" ", strip=True) if node else ""
        if title:
            return title, {"status": "extracted", "scope": scope, "locator": locator, "excerpt": title}
        return None, absent
    soup = BeautifulSoup(content, "html.parser")
    node = soup.head.find("title", recursive=False) if soup.head else soup.find("title", recursive=False)
    title = node.get_text(" ", strip=True) if node else ""
    locator = "head/title" if soup.head else "title"
    if not title:
        node = soup.find("meta", attrs={"property": "og:title"})
        title = str(node.get("content", "")).strip() if node else ""
        locator = "meta[property='og:title']@content"
    if title:
        return title, {"status": "extracted", "scope": "whole_document", "locator": locator, "excerpt": title}
    return None, absent


def _read_raw(store, raw_id, source):
    row = store.db.execute("SELECT b.*, r.retrieval_id,r.source,r.url,r.retrieved_at FROM raw_blobs b JOIN raw_retrievals r USING(raw_id) WHERE raw_id=? AND source=? ORDER BY r.retrieved_at DESC,r.rowid DESC LIMIT 1", (raw_id, source)).fetchone()
    if not row:
        return None, None
    raw = dict(row)
    content = (store.root / raw["path"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != raw["sha256"]:
        raise ValueError("Saved SEC document failed SHA-256 verification")
    return content, raw


class OfficialIRAdapter(BaseAdapter):
    source_id = "official_ir"
    scope_kind = "company"
    discovery_dependencies = ("sec_edgar",)
    data_types = ("documents", "guidance", "event_calendar", "operating_metrics", "financial_facts", "relations",
                  "commitments", "debt_maturities", "risk_disclosures")

    def health(self, ctx):
        return {"status": "implemented", "network_checked": False, "requires": [],
                "capabilities": ["evidenced official entry points", "static HTML links", "RSS/Atom", "PDF text", "versioned current copies"],
                "limitations": ["No arbitrary JavaScript portal execution", "No historic-copy equivalence", "Webcast is not transcript", "No paid transcript rights"]}

    def _filing_candidates(self, ctx, company_id):
        candidates = []
        for record in ctx.store.query("documents", company_id=company_id):
            data = record["data"]
            if data.get("document_kind") not in ("primary_filing", "filing_attachment"):
                continue
            checkpoint = "ir:website-links:" + company_id + ":" + record["raw_id"] + ":" + PARSER_VERSION
            if ctx.store.checkpoint(checkpoint):
                continue
            content, raw = _read_raw(ctx.store, record["raw_id"], record["source"])
            if not content or not raw:
                continue
            soup = BeautifulSoup(content, "html.parser")
            seen_urls = set()
            for index, anchor in enumerate(soup.find_all("a", href=True)):
                label = anchor.get_text(" ", strip=True)
                parent_text = anchor.parent.get_text(" ", strip=True)[:1200] if anchor.parent else label
                is_ir = bool(re.search(r"(?:our|its|company(?:'s)?)\s+(?:investor\s+relations|investor)\s+(?:website|site)", parent_text, re.I)
                             or _IR.search(label) and re.search(r"\bour\b|\bcompany(?:'s)?\b", parent_text, re.I))
                is_company = bool(re.search(r"(?:our|company(?:'s)?)\s+(?:corporate\s+)?(?:website|web\s+site)", parent_text, re.I))
                if not (is_ir or is_company):
                    continue
                url = _url(anchor["href"], raw["url"])
                hostname = urlsplit(url).hostname if url else None
                if not url or any(hostname == host or hostname.endswith("." + host) for host in ("sec.gov", "xbrl.org", "w3.org")):
                    continue
                key = company_id + "|sec_stated_url|" + record["record_key"] + "|" + url
                candidate = {"url": url, "candidate_type": "official_ir_candidate" if is_ir else "official_company_website_candidate",
                             "authority_basis": "company-stated link in SEC disclosure; current domain ownership not independently verified",
                             "candidate_only": True, "evidence_excerpt": parent_text, "source_document_record_id": record["record_id"]}
                ctx.store.put_record("source_candidates", key, candidate, source=record["source"], raw_id=raw["raw_id"],
                    company_id=company_id, source_locator=f"a[{index}][href] in company website statement", parser_version=PARSER_VERSION,
                    quality="candidate_needs_verification", knowledge_type="company_statement",
                    availability_basis="unknown_current_relevance_of_historical_website_statement")
                candidates.append({**candidate, "evidence_raw_id": raw["raw_id"]})
                seen_urls.add(url)
            # Many filings print an official URL as text, without an HTML link.
            # Require an explicit company website statement in the same short
            # source block; bare domain mentions are not sufficient evidence.
            for index, block in enumerate(soup.find_all(["p", "div", "td"])):
                block_text = block.get_text(" ", strip=True)
                if len(block_text) > 1800:
                    continue
                is_ir = bool(re.search(r"(?:our|its|company(?:'s)?)\s+(?:investor\s+relations|investor)\s+(?:website|site)", block_text, re.I))
                is_company = bool(re.search(r"(?:our|its|company(?:'s)?)\s+(?:corporate\s+)?(?:website|web\s+site|internet\s+(?:site|address))", block_text, re.I))
                if not (is_ir or is_company):
                    continue
                for match in re.finditer(r"(?:https?://|www\.)[^\s<>()\[\]{}]+", block_text, re.I):
                    url = _url(match.group().rstrip(".,;:"))
                    hostname = urlsplit(url).hostname if url else None
                    if not url or url in seen_urls or any(hostname == host or hostname.endswith("." + host) for host in ("sec.gov", "xbrl.org", "w3.org")):
                        continue
                    candidate = {"url": url, "candidate_type": "official_ir_candidate" if is_ir else "official_company_website_candidate",
                                 "authority_basis": "company-stated literal URL in SEC disclosure; current domain ownership not independently verified",
                                 "candidate_only": True, "evidence_excerpt": block_text, "source_document_record_id": record["record_id"]}
                    key = company_id + "|sec_stated_url|" + record["record_key"] + "|" + url
                    ctx.store.put_record("source_candidates", key, candidate, source=record["source"], raw_id=raw["raw_id"],
                        company_id=company_id, source_locator=f"p/div/td[{index}] company website statement", parser_version=PARSER_VERSION,
                        quality="candidate_needs_verification", knowledge_type="company_statement",
                        availability_basis="unknown_current_relevance_of_historical_website_statement")
                    candidates.append({**candidate, "evidence_raw_id": raw["raw_id"]})
                    seen_urls.add(url)
            ctx.store.checkpoint(checkpoint, {"complete": True, "urls": sorted(seen_urls)})
        return candidates

    def discover(self, ctx, scope):
        company_id = scope.get("company_id") or scope.get("security", {}).get("company_id")
        if not company_id:
            raise ValueError("Official IR discovery requires resolved company_id")
        security = {**scope.get("security", {}), **scope}
        candidates = []
        for name in ("official_ir", "investor_website", "website", "official_website"):
            values = security.get(name, [])
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                url = _url(value)
                if url:
                    candidates.append({"url": url, "candidate_type": "official_ir" if name in ("official_ir", "investor_website") else "official_company_website",
                                       "authority_basis": "user_configured_official_entrypoint", "candidate_only": False})
        for record in ctx.store.query("company_profiles", company_id=company_id):
            for name in ("investor_website", "website"):
                url = _url(record["data"].get(name))
                if url:
                    candidates.append({"url": url, "candidate_type": "official_ir" if name == "investor_website" else "official_company_website",
                                       "authority_basis": "SEC submissions filer profile", "evidence_raw_id": record["raw_id"], "candidate_only": False})
        for record in ctx.store.query("source_candidates", company_id=company_id):
            data = record["data"]
            url = _url(data.get("url"))
            if url and str(data.get("candidate_type", "")).startswith("official_"):
                candidates.append({**data, "url": url, "evidence_raw_id": record["raw_id"]})
        candidates.extend(self._filing_candidates(ctx, company_id))
        # Prefer disclosed IR entries. Corporate homepages are a fallback that
        # must actually expose a link; no symbol-to-URL template exists here.
        direct = [candidate for candidate in candidates if "official_ir" in candidate.get("candidate_type", "")]
        selected = direct or candidates
        seen = set()
        for candidate in selected:
            url = candidate["url"]
            if url in seen:
                continue
            seen.add(url)
            metadata = {"depth": 0, "root_url": url, "ancestry": [], "refresh": True,
                        "start": scope.get("start", ctx.config.get("start")), "end": scope.get("end", ctx.config.get("end")),
                        "authority_evidence": candidate, "immutable_archive": False,
                        "origin_kind": "ir_entrypoint" if direct else "company_homepage"}
            ctx.emit({"source_id": self.source_id, "resource_key": "page:" + url, "kind": "ir_listing", "data_type": "documents",
                      "url": url, "company_id": company_id, "security_id": None, "metadata": metadata})
        if not seen:
            ctx.gap("IR_ENTRYPOINT_UNRESOLVED", "No evidenced official IR or company website available; add official entrypoint or acquire SEC disclosure first",
                    source_id=self.source_id, company_id=company_id, data_type="documents", status="not_discovered")
        elif hasattr(ctx.queue, "resolve_gaps"):
            ctx.queue.resolve_gaps(source_id=self.source_id, code="IR_ENTRYPOINT_UNRESOLVED", company_id=company_id)
        return {"enumeration_complete": bool(seen), "discovered": len(seen),
                "coverage_basis": "Known official entrypoints only; website-wide historical completeness is not established"}

    def acquire(self, ctx, resource):
        metadata = resource.get("metadata", {})
        limit = int(ctx.config.get("ir", {}).get("max_depth", 3))
        if limit < 0:
            raise ValueError("ir.max_depth must be nonnegative")
        if metadata.get("depth", 0) > limit:
            ctx.gap("IR_TRAVERSAL_LIMIT", "Link discovered but not downloaded due to ir.max_depth; increase depth and resume",
                source_id=self.source_id, company_id=resource.get("company_id"), data_type="documents", status="resource_limited",
                details={"url": resource["url"], "depth": metadata["depth"], "max_depth": limit})
            raise UnsupportedCapability("IR_TRAVERSAL_LIMIT: discovered URL requires a larger configured ir.max_depth")
        if hasattr(ctx.queue, "resolve_gaps"):
            ctx.queue.resolve_gaps(source_id=self.source_id, code="IR_TRAVERSAL_LIMIT", company_id=resource.get("company_id"),
                                   details_match={"url": resource["url"]})
        return ctx.client.fetch(resource["url"], source=self.source_id, refresh=metadata.get("refresh", True))

    def _emit_link(self, ctx, resource, url, title, *, kind, date_evidence=None, locator=None):
        metadata = resource.get("metadata", {})
        inherited = {key: value for key, value in metadata.items() if key not in
                     {"published_at", "publication_precision", "publication_timezone", "updated_at", "available_at", "evidence"}}
        ancestry = metadata.get("ancestry", []) + [resource["url"]]
        if url in ancestry:
            return False
        ctx.emit({"source_id": self.source_id, "resource_key": "page:" + url, "kind": kind, "data_type": "documents",
                  "url": url, "company_id": resource.get("company_id"), "security_id": None,
                  "metadata": {**inherited, **(date_evidence or {}), "depth": metadata.get("depth", 0) + 1,
                               "ancestry": ancestry, "title": title, "parent_url": resource["url"],
                               "discovery_locator": locator, "immutable_archive": False,
                               "authority_basis": "linked_from_evidenced_company_or_IR_page",
                               "origin_kind": "ir_document" if kind == "ir_document" else "ir_listing"}})
        return True

    def _outside_dates(self, metadata, date_evidence):
        published = date_evidence.get("published_at")
        if not published:
            return False
        day = published[:10]
        return bool((metadata.get("start") and day < metadata["start"]) or (metadata.get("end") and day > metadata["end"]))

    def _listing(self, ctx, resource, content, raw):
        is_feed = bool(re.search(rb"<(?:rss|feed)(?:\s|>)", content[:4096], re.I))
        soup = BeautifulSoup(content, "xml" if is_feed else "html.parser")
        metadata = resource.get("metadata", {})
        count, excluded, seen, unknown_dates = 0, [], set(), 0
        feed_items = soup.find_all(["item", "entry"])
        if feed_items:
            for index, item in enumerate(feed_items):
                link = item.find("link")
                url = _url((link.get("href") or link.get_text(" ", strip=True)) if link else None, resource["url"])
                title_node = item.find("title")
                title = title_node.get_text(" ", strip=True) if title_node else ""
                date_node = item.find(["pubDate", "pubdate", "published", "updated"])
                dates = _date(date_node.get_text(" ", strip=True)) if date_node else {}
                if url and self._outside_dates(metadata, dates):
                    excluded.append({"url": url, "published_at": dates.get("published_at"), "reason": "outside_requested_publication_dates"})
                elif url and url not in seen:
                    seen.add(url)
                    count += self._emit_link(ctx, resource, url, title, kind="ir_document", date_evidence=dates, locator=f"feed item[{index}]/link")
                    unknown_dates += int(not dates)
                for enclosure in item.find_all("enclosure", url=True):
                    enclosed = _url(enclosure["url"], resource["url"])
                    if enclosed and enclosed not in seen and not self._outside_dates(metadata, dates):
                        seen.add(enclosed)
                        count += self._emit_link(ctx, resource, enclosed, title, kind="ir_document", date_evidence=dates, locator=f"feed item[{index}]/enclosure")
        anchors = soup.find_all(["a", "link"], href=True)
        for index, anchor in enumerate(anchors):
            url = _url(anchor.get("href"), resource["url"])
            if not url or url in seen:
                continue
            suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
            if suffix in _NON_DOCUMENT:
                continue
            title = anchor.get_text(" ", strip=True) or anchor.get("title") or anchor.get("aria-label") or ""
            rel = " ".join(anchor.get("rel", []))
            feed = "rss" in str(anchor.get("type", "")).lower() or "atom" in str(anchor.get("type", "")).lower()
            is_ir = bool(_IR.search(title))
            is_file = suffix in _FILES
            is_webcast = bool(re.search(r"webcast|audio\s+(?:replay|conference)|listen\s+(?:live|to)", title, re.I))
            is_detail = bool(is_file or _PATH.search(urlsplit(url).path) and not _LIST_PATH.search(url) or _DOCUMENT.search(title))
            is_nav = bool(feed or "next" in rel or "prev" in rel or is_ir or _NAV.search(title) or _LIST_PATH.search(url))
            generic_nav = bool(re.fullmatch(r"(?:press\s+releases?|news\s+releases?|news(?:\s+and\s+events)?|events?(?:\s+and\s+presentations)?|presentations?|annual\s+reports?|quarterly\s+results|financial\s+(?:results|information)|earnings|reports?|archives?)", title.strip(), re.I))
            if generic_nav and not is_file:
                is_detail, is_nav = False, True
            same_site = _same_site(url, metadata.get("root_url", resource["url"]))
            # Official IR pages frequently link hosted files and event services.
            # Off-domain navigational links need an explicit IR label; arbitrary
            # external news links are not silently treated as issuer originals.
            if not same_site and not (is_ir or is_file or is_webcast):
                if is_detail or is_nav:
                    excluded.append({"url": url, "title": title,
                                     "reason": "external_html_source_unverified; add an evidenced source adapter or official root to follow"})
                continue
            if not (is_detail or is_nav):
                continue
            kind = "ir_webcast" if is_webcast else "ir_document" if is_detail and not (feed or is_ir or "next" in rel or "prev" in rel) else "ir_listing"
            dates = _link_date(anchor)
            if kind != "ir_listing" and self._outside_dates(metadata, dates):
                excluded.append({"url": url, "published_at": dates.get("published_at"), "reason": "outside_requested_publication_dates"})
                continue
            seen.add(url)
            count += self._emit_link(ctx, resource, url, title, kind=kind, date_evidence=dates, locator=f"a/link[{index}]@href")
            if kind != "ir_listing":
                unknown_dates += int(not dates)
        errors = []
        if count == 0:
            code = "IR_DYNAMIC_OR_UNRECOGNIZED_LISTING" if soup.find("script") else "IR_NO_SUPPORTED_DOCUMENT_LINKS"
            issue = {"code": code, "message": "No supported new document/navigation links extracted; this does not prove source non-disclosure"}
            ctx.gap(code, issue["message"], source_id=self.source_id, company_id=resource.get("company_id"), data_type="documents",
                    details={"url": resource["url"], "raw_id": raw["raw_id"]})
            errors.append(issue)
        if unknown_dates:
            ctx.gap("IR_PUBLICATION_DATE_UNKNOWN", "Some discovered document dates are unknown; date-window membership requires later verification",
                    source_id=self.source_id, company_id=resource.get("company_id"), data_type="documents",
                    details={"url": resource["url"], "count": unknown_dates})
        record_id = self._save_document(ctx, resource, raw, "ir_listing", _page_dates(soup), extra={"enumerated_links": count}, content=content)
        return {"status": "partial" if errors else "parsed", "record_ids": [record_id], "parsed_count": 1, "errors": errors,
                "coverage": {"enumerated_links": count, "excluded_by_publication_date": excluded, "unknown_publication_dates": unknown_dates,
                             "website_historical_inventory_complete": False, "javascript_executed": False,
                             "pagination_basis": "Only explicit static next/archive links; unsupported pagination stays a capability gap"}}

    def _save_document(self, ctx, resource, raw, kind, dates=None, extra=None, *, content):
        metadata = {**resource.get("metadata", {}), **(dates or {})}
        title, title_evidence = _document_title(content)
        data = {"document_kind": kind, "url": resource["url"], "title": title,
                "title_evidence": {**title_evidence, "raw_id": raw["raw_id"], "content_sha256": raw["sha256"]},
                "resource_key": resource["resource_key"], "full_text_preserved": True,
                "original_published_at": metadata.get("published_at"), "source_revision_at": metadata.get("updated_at"),
                "current_copy_only": True, "historical_copy_verified": False, "content_sha256": raw["sha256"],
                "authority_evidence": metadata.get("authority_evidence"), **(extra or {})}
        # Same bytes obtained later are a repeated observation, not a new claim
        # that the current version only became available on the latest fetch.
        prior = ctx.store.db.execute("SELECT data_json,available_at FROM records WHERE dataset='documents' AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1",
                                     (self.source_id, resource["resource_key"])).fetchone()
        available = raw["retrieved_at"]
        if prior and json.loads(prior["data_json"]) == data:
            available = prior["available_at"]
        return ctx.store.put_record("documents", resource["resource_key"], data, source=self.source_id, raw_id=raw["raw_id"],
            company_id=resource.get("company_id"), published_at=metadata.get("published_at"),
            publication_precision=metadata.get("publication_precision", "unknown"), publication_timezone=metadata.get("publication_timezone"),
            available_at=available, availability_basis="first_observed_current_document_version", source_locator="whole original response",
            quality="unverified", parser_version=DOCUMENT_PARSER_VERSION)

    def parse(self, ctx, resource, content, raw):
        if resource["kind"] == "ir_listing":
            return self._listing(ctx, resource, content, raw)
        if resource["kind"] == "ir_webcast":
            record_id = self._save_document(ctx, resource, raw, "webcast_landing_page", extra={"transcript_obtained": False, "audio_downloaded": False}, content=content)
            issue = {"code": "IR_WEBCAST_NOT_TRANSCRIPT", "message": "Webcast landing page retained; audio and full transcript have not been obtained"}
            ctx.gap(issue["code"], issue["message"], source_id=self.source_id, company_id=resource.get("company_id"),
                    data_type="documents", details={"url": resource["url"], "raw_id": raw["raw_id"]})
            return {"status": "partial", "record_ids": [record_id], "parsed_count": 1, "errors": [issue], "coverage": {"transcript_obtained": False}}
        suffix = PurePosixPath(urlsplit(resource["url"]).path).suffix.lower()
        media_type = "application/pdf" if suffix == ".pdf" or content.startswith(b"%PDF") else "text/html"
        if suffix in _FILES and suffix != ".pdf":
            record_id = self._save_document(ctx, resource, raw, "ir_attachment_unparsed", content=content)
            issue = {"code": "IR_ATTACHMENT_PARSER_UNSUPPORTED", "message": "File retained; this attachment format has no installed semantic parser"}
            ctx.gap(issue["code"], issue["message"], source_id=self.source_id, company_id=resource.get("company_id"),
                    data_type="documents", details={"url": resource["url"]})
            return {"status": "partial", "record_ids": [record_id], "parsed_count": 1, "errors": [issue]}
        dates = _page_dates(BeautifulSoup(content, "html.parser")) if media_type == "text/html" else {}
        resource = {**resource, "metadata": {**resource.get("metadata", {}), **dates, "immutable_archive": False}}
        record_id = self._save_document(ctx, resource, raw, "ir_document", dates, content=content)
        parsed = parse_document(content, media_type=media_type, document_type="official_ir_document",
                                company_id=resource.get("company_id"), source_url=resource["url"])
        result = persist_observations(ctx, resource, parsed, raw)
        return {**result, "record_ids": [record_id] + result.get("record_ids", []), "parsed_count": 1 + result.get("parsed_count", 0),
                "coverage": {**result.get("coverage", {}), "current_copy_only": True, "historical_copy_verified": False}}
