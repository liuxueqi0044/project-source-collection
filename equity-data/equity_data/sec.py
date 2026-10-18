"""SEC EDGAR adapter: exact raw bytes, accession-grained facts and cautious clocks.

Companyfacts is an entity-wide standard-taxonomy extract, not a complete XBRL
processor.  It does not provide full segment/custom-tag/footnote coverage.  This
adapter never substitutes a period end for the date information became public.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

PARSER_VERSION = "sec-1.0"
SOURCE = "SEC EDGAR"
DEFAULT_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "20-F/A", "6-K", "25-NSE", "15-12B")
NY = "America/New_York"


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _in_range(value, start: str, end: str) -> bool:
    parsed = _day(value)
    return parsed is not None and date.fromisoformat(start) <= parsed <= date.fromisoformat(end)


def _columns_to_rows(columns: dict) -> list[dict]:
    """SEC archived files are columnar at the root; recent files are nested."""
    if "filings" in columns:
        columns = columns.get("filings", {}).get("recent", {})
    elif "recent" in columns:
        columns = columns["recent"]
    count = len(columns.get("accessionNumber", []))
    return [{key: values[i] if i < len(values) else None
             for key, values in columns.items() if isinstance(values, list)}
            for i in range(count)]


def _header_metadata(content: bytes) -> dict:
    """Read the SGML text in SEC's index-headers HTML, without executing HTML."""
    text = html.unescape(content.decode("utf-8", errors="replace"))
    accepted = re.search(r"<ACCEPTANCE-DATETIME>\s*(\d{14})", text, re.I)
    documents = []
    # Some older header files omit closing DOCUMENT tags.
    for block in re.split(r"<DOCUMENT>", text, flags=re.I)[1:]:
        values = {}
        for name in ("TYPE", "FILENAME", "DESCRIPTION", "SEQUENCE"):
            match = re.search(r"<" + name + r">\s*([^\r\n<]+)", block, re.I)
            values[name.lower()] = match.group(1).strip() if match else None
        if values.get("filename"):
            documents.append(values)
    return {"accepted_local_clock": accepted.group(1) if accepted else None,
            "documents": documents}


def _time_evidence(filing: dict, header: dict | None = None) -> dict:
    """Do not trust an undocumented JSON 'Z' clock as intraday availability.

    The header's acceptance clock is preserved in NY time. Availability remains
    conservatively next midnight after BOTH filing day and accepted local day.
    SEC acceptance is not proof of the actual public dissemination instant.
    This intentionally does not claim intraday point-in-time reconstruction.
    """
    # Confidential submissions and correspondence can become public well after
    # their filing/acceptance dates; those dates are not availability evidence.
    if str(filing.get("form", "")).upper() in ("CORRESP", "UPLOAD", "DRS", "DRS/A"):
        return {"published_at": None, "publication_precision": "unknown", "publication_timezone": None,
                "available_at": None, "availability_basis": "unknown_delayed_or_confidential_publication"}
    filing_day = _day(filing.get("filingDate") or filing.get("filed"))
    local = None
    if header and header.get("accepted_local_clock"):
        try:
            local = datetime.strptime(header["accepted_local_clock"], "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo(NY))
        except ValueError:
            pass
    days = [d for d in (filing_day, local.date() if local else None) if d]
    available = _utc(datetime.combine(max(days) + timedelta(days=1), time.min, ZoneInfo(NY))) if days else None
    return {
        "published_at": _utc(local) if local else (filing_day.isoformat() if filing_day else None),
        "publication_precision": "second" if local else ("day" if filing_day else "unknown"),
        "publication_timezone": NY if days else None,
        "available_at": available,
        "availability_basis": "conservative_next_day_after_filing_and_header_acceptance" if local else ("conservative_next_day_after_filing_date_unverified_intraday" if filing_day else "unknown"),
    }


def _filing_base(cik: str, accession: str) -> str:
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("Invalid SEC accession number")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def _document_url(base: str, filename: str) -> str:
    # A remote filename must not redirect us to an unrelated host/path.
    # SEC primaryDocument can legitimately contain xslF25X02/primary_doc.xml.
    parts = filename.split("/") if filename else []
    if not parts or "\\" in filename or ":" in filename or any(p in ("", ".", "..") for p in parts):
        raise ValueError("Invalid SEC document filename")
    return base + "/".join(quote(part, safe="-._") for part in parts)


def _selected_filings(rows: list[dict], limit: int, required_accessions=()) -> list[dict]:
    """A small sample includes amendments, delisting forms and annual/quarterly news."""
    if limit <= 0:
        return []
    ordered = sorted(rows, key=lambda row: (row.get("filingDate") or "", row["accessionNumber"]), reverse=True)
    selected = []
    seen = set()
    by_accession = {r["accessionNumber"]: r for r in ordered}
    for accession in required_accessions:
        if accession in by_accession and accession not in seen and len(selected) < limit:
            selected.append(by_accession[accession])
            seen.add(accession)
    groups = (
        lambda r: str(r.get("form", "")).endswith("/A"),
        lambda r: r.get("form") in ("25-NSE", "15-12B"),
        lambda r: r.get("form") in ("10-K", "20-F"),
        lambda r: r.get("form") == "10-Q",
        lambda r: r.get("form") == "8-K" and "2.02" in str(r.get("items", "")),
    )
    for predicate in groups:
        match = next((r for r in ordered if predicate(r) and r["accessionNumber"] not in seen), None)
        if match is not None and len(selected) < limit:
            selected.append(match)
            seen.add(match["accessionNumber"])
    for row in ordered:
        if len(selected) >= limit:
            break
        if row["accessionNumber"] not in seen:
            selected.append(row)
            seen.add(row["accessionNumber"])
    return selected


def _put(store, dataset, key, data, *, raw, company_id, **kwargs):
    return store.put_record(dataset, key, data, source=SOURCE, raw_id=raw["raw_id"],
                            company_id=company_id, parser_version=PARSER_VERSION, **kwargs)


def _identity(store, payload, raw, security, company_id):
    _put(store, "company_profiles", company_id, {
        "cik": str(payload.get("cik", security["cik"])).zfill(10),
        "name": payload.get("name"), "sic": payload.get("sic"),
        "sic_description": payload.get("sicDescription"), "entity_type": payload.get("entityType"),
        "fiscal_year_end": payload.get("fiscalYearEnd"), "state_of_incorporation": payload.get("stateOfIncorporation"),
        "website": payload.get("website"), "investor_website": payload.get("investorWebsite"),
        "current_tickers": payload.get("tickers", []), "current_exchanges": payload.get("exchanges", []),
        "snapshot_only": True, "history_complete": False,
        "temporal_caveat": "Current API snapshot; original publication/validity of profile fields unknown.",
    }, raw=raw, company_id=company_id, source_locator="/", quality="unverified")
    tickers = payload.get("tickers", [])
    exchanges = payload.get("exchanges", [])
    configured_symbol = security.get("symbol")
    confirmed = configured_symbol in tickers
    i = tickers.index(configured_symbol) if confirmed else None
    _put(store, "securities", security["security_id"], {
        "company_id": company_id, "security_id": security["security_id"],
        "configured_symbol": configured_symbol,
        "symbol": configured_symbol if confirmed else None,
        "name": payload.get("name"),
        "exchange": exchanges[i] if i is not None and i < len(exchanges) else None,
        "configured_exchange": security.get("exchange"),
        "currency": security.get("currency"), "security_type": security.get("security_type"),
        "classification_basis": "user_config_currency_and_security_type_not_SEC_security_master",
        "current_symbol_confirmed": confirmed, "listing_date": None, "delisting_date": None,
        "shares_outstanding": None, "snapshot_only": True,
        "security_mapping_basis": "local_identifier_explicit_config; SEC CIK identifies issuer, not share class",
    }, raw=raw, company_id=company_id, security_id=security["security_id"],
        source_locator="/tickers,/exchanges + configured security mapping", quality="unverified")
    for i, former in enumerate(payload.get("formerNames", [])):
        key = f"{company_id}|former_name|{former.get('name')}|{former.get('from')}|{former.get('to')}"
        _put(store, "identity_history", key, {
            "identity_type": "company_name", "name": former.get("name"),
            "valid_from_reported": former.get("from"), "valid_to_reported": former.get("to"),
            "validity_basis": "SEC formerNames snapshot; not original announcement evidence",
            "original_publication_unknown": True,
        }, raw=raw, company_id=company_id, source_locator=f"/formerNames/{i}", quality="unverified")
    store.issue("SEC_IDENTITY_HISTORY_INCOMPLETE", "SEC current ticker/profile and former names do not reconstruct a historical security master, share classes, or listing/delisting dates.",
                dataset="securities", company_id=company_id, security_id=security["security_id"])


class _NotCached(Exception):
    pass


def _read_cached(store, url):
    if not hasattr(store, "latest_raw"):
        raise _NotCached()
    raw = store.latest_raw(SOURCE, url)
    if raw is None:
        raise _NotCached()
    path = Path(raw["path"])
    if not path.is_absolute():
        path = store.root / path
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != raw["sha256"]:
        raise ValueError("Cached SEC raw evidence failed SHA-256 verification")
    return content, raw


def _collect_documents(store, client, selected, cik, company_id, sec, headers, *, cache_only=False, retained=None):
    results = {}
    count = 0
    failures = 0
    for row in selected:
        filing_failures = 0
        accession = row["accessionNumber"]
        base = _filing_base(cik, accession)
        previous = (retained or {}).get(accession, {})
        metadata = previous.get("metadata")
        metadata_raw = previous.get("header_raw")
        try:
            header_url = base + accession + "-index-headers.html"
            if cache_only:
                content, metadata_raw = _read_cached(store, header_url)
            else:
                content, metadata_raw = client.fetch(header_url, source=SOURCE, headers=headers,
                                                      refresh=bool(sec.get("refresh_documents", False)))
            metadata = _header_metadata(content)
            evidence = _time_evidence(row, metadata)
            _put(store, "documents", f"{accession}|index-headers", {
                "accession": accession, "document_kind": "filing_header_index", "form": row.get("form"),
                "url": metadata_raw["url"], "accepted_local_clock": metadata.get("accepted_local_clock"),
                "document_manifest": metadata["documents"],
                "publication_time_kind": "SEC acceptance, not observed dissemination", "full_text_preserved": True,
            }, raw=metadata_raw, company_id=company_id, source_locator="<ACCEPTANCE-DATETIME>; <DOCUMENT>",
                quality="unverified", **evidence)
            count += 1
        except _NotCached:
            pass
        except Exception as exc:
            if cache_only:
                # Corrupt cached evidence is an integrity failure, not a reason
                # to silently downgrade already retained publication precision.
                raise
            failures += 1
            filing_failures += 1
            store.issue("SEC_HEADER_UNAVAILABLE", str(exc), dataset="documents", company_id=company_id, details={"accession": accession})
        evidence = _time_evidence(row, metadata)
        candidates = []
        primary = row.get("primaryDocument")
        if primary:
            candidates.append({"filename": primary, "type": row.get("form"), "description": row.get("primaryDocDescription"), "kind": "primary_filing"})
        if metadata and sec.get("download_exhibits", True):
            exhibits = [d for d in metadata["documents"] if str(d.get("type", "")).startswith("EX-99")]
            exhibit_limit = max(0, int(sec.get("max_exhibits_per_filing", 3)))
            candidates.extend({**d, "kind": "management_exhibit"} for d in exhibits[:exhibit_limit])
            if len(exhibits) > exhibit_limit:
                store.issue("SEC_EXHIBIT_LIMIT", "Only selected EX-99 exhibits downloaded.", dataset="documents", company_id=company_id,
                            details={"accession": accession, "available_exhibits": len(exhibits), "limit": exhibit_limit})
        downloaded = list(previous.get("downloaded_record_ids", []))
        primary_downloaded = bool(previous.get("primary_downloaded"))
        for doc in candidates:
            try:
                url = _document_url(base, doc["filename"])
                if cache_only:
                    _, raw = _read_cached(store, url)
                else:
                    _, raw = client.fetch(url, source=SOURCE, headers=headers, refresh=bool(sec.get("refresh_documents", False)))
                record_id = _put(store, "documents", f"{accession}|{doc['filename']}", {
                    "accession": accession, "filename": doc["filename"], "url": url,
                    "form": row.get("form"), "document_type": doc.get("type"), "document_kind": doc["kind"],
                    "description": doc.get("description"), "full_text_preserved": True,
                    "structured_guidance_extracted": False, "links_images_downloaded": False,
                    "time_evidence_raw_id": metadata_raw["raw_id"] if metadata_raw else None,
                }, raw=raw, company_id=company_id, source_locator="whole original document", quality="unverified", **evidence)
                if record_id not in downloaded:
                    downloaded.append(record_id)
                if doc["kind"] == "primary_filing":
                    primary_downloaded = True
                count += 1
            except _NotCached:
                pass
            except Exception as exc:
                if cache_only:
                    raise
                failures += 1
                filing_failures += 1
                store.issue("SEC_DOCUMENT_UNAVAILABLE", str(exc), dataset="documents", company_id=company_id,
                            details={"accession": accession, "filename": doc["filename"]})
        if not cache_only or metadata_raw or downloaded:
            results[accession] = {"metadata": metadata, "header_raw": metadata_raw, "downloaded_record_ids": downloaded,
                                  "primary_downloaded": primary_downloaded}
        if not cache_only:
            store.checkpoint(f"sec:documents:{accession}", {"complete": filing_failures == 0, "downloaded_record_ids": downloaded})
    return results, count, failures


def _duration(start, end):
    first, last = _day(start), _day(end)
    if not first or not last:
        return None
    return (last - first).days + 1


def _financial_facts(store, payload, raw, company_id, filings, documents, start, end):
    count = 0
    rejected = 0
    for taxonomy, concepts in payload.get("facts", {}).items():
        for concept, definition in concepts.items():
            for unit, observations in definition.get("units", {}).items():
                for i, fact in enumerate(observations):
                    if not _in_range(fact.get("filed"), start, end):
                        continue
                    accession = fact.get("accn")
                    if not accession or "val" not in fact:
                        rejected += 1
                        continue
                    filing = filings.get(accession, {"filed": fact.get("filed")})
                    header_info = documents.get(accession, {})
                    evidence = _time_evidence(filing, header_info.get("metadata"))
                    # Numeric facts are not revised merely because the document
                    # downloader later obtains (or selects) richer clock evidence.
                    # Their publication label is the SEC filed day; header details
                    # remain accession-linked in filings/documents. The conservative
                    # boundary still honors a later header acceptance day if found.
                    if evidence["available_at"] is not None:
                        evidence.update(published_at=filing.get("filingDate") or fact.get("filed"),
                                        publication_precision="day", publication_timezone=NY,
                                        availability_basis="sec_filing_day_next_day_conservative_with_header_day_floor")
                    # All accession/period/unit/frame contexts retained, including comparatives
                    # and restatements. Never collapse to the last value for the fiscal period.
                    context = [company_id, taxonomy, concept, unit, accession,
                               fact.get("start"), fact.get("end"), fact.get("fy"), fact.get("fp"), fact.get("frame")]
                    key = hashlib.sha256(json.dumps(context, separators=(",", ":")).encode()).hexdigest()
                    currency = unit if re.fullmatch(r"[A-Z]{3}", unit) else None
                    if "/" in unit and re.fullmatch(r"[A-Z]{3}", unit.split("/")[0]):
                        currency = unit.split("/")[0]
                    # Index positions move when SEC adds another disclosure.
                    # A logical source locator remains valid across snapshots
                    # and must not turn reordering into a financial revision.
                    locator = ("/facts/" + taxonomy + "/" + concept + "/units/" + unit.replace("~", "~0").replace("/", "~1")
                               + "; observation context=" + json.dumps({k: fact.get(k) for k in ("accn", "start", "end", "fy", "fp", "frame")}, sort_keys=True))
                    _put(store, "financial_facts", key, {
                        "taxonomy": taxonomy, "concept": concept, "label": definition.get("label"),
                        "description": definition.get("description"), "value": fact["val"], "unit": unit,
                        "scale": 1, "scale_basis": "API numeric value in indicated units; no report display-scale multiplication",
                        "currency": currency, "accession": accession, "form": fact.get("form"),
                        "filed": fact.get("filed"), "start": fact.get("start"), "end": fact.get("end"),
                        "fy": fact.get("fy"), "fp": fact.get("fp"), "frame": fact.get("frame"),
                        "period_type": "duration" if fact.get("start") else "instant",
                        "duration_days": _duration(fact.get("start"), fact.get("end")),
                        "quarter_or_cumulative": "not_inferred_from_fp; use exact start/end",
                        "consolidation_scope": "entire_filing_entity",
                        "accounting_basis": "US-GAAP" if taxonomy == "us-gaap" else ("IFRS" if taxonomy == "ifrs-full" else "taxonomy_specific"),
                        "is_amendment": str(fact.get("form", "")).endswith("/A"),
                        "amends_accession": None, "revision_link_basis": "explicit accession and comparable context; no unproved supersession",
                        "time_evidence_accession": accession,
                        "time_evidence_location": "SEC filed day in this source fact; accession-linked filing/header for richer evidence",
                        "source_fact": fact,
                    }, raw=raw, company_id=company_id, period_start=fact.get("start"), period_end=fact.get("end"),
                        source_locator=locator, unit=unit, currency=currency, quality="unverified", **evidence)
                    count += 1
    if rejected:
        store.issue("SEC_FACT_INVALID", "Facts without accession or value rejected.", dataset="financial_facts", company_id=company_id, details={"count": rejected})
    return count


def collect_sec(store, client, config: dict) -> dict:
    """Backfill or refresh SEC data. Re-running upserts evidence idempotently.

    Date filters apply to filing disclosure day, not the financial reporting
    period. A security can override start/end. Set max_documents_per_company=0
    for metadata/facts only; omitted original documents become explicit gaps.
    """
    sec = config.get("sec", {})
    user_agent = sec.get("user_agent", "")
    if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_agent):
        store.issue("SEC_CONTACT_REQUIRED", "Configure a real contact email in sec.user_agent before SEC automation.", dataset="filings")
        return {"status": "blocked", "reason": "SEC contact identity missing", "companies": []}
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Accept": "application/json,text/html,*/*"}
    companies = []
    seen_companies = set()
    for security in config.get("securities", []):
        cik = str(security.get("cik", "")).zfill(10)
        if not re.fullmatch(r"\d{10}", cik) or int(cik) == 0:
            store.issue("SEC_CIK_MISSING", "A valid issuer CIK is required.", dataset="company_profiles", security_id=security.get("security_id"))
            continue
        company_id = security.get("company_id", "sec:" + cik)
        start, end = str(security.get("start", config["start"])), str(security.get("end", config["end"]))
        if _day(start) is None or _day(end) is None or start > end:
            raise ValueError("SEC start/end must be ordered ISO dates")
        identity_key = (company_id, start, end)
        # A company can have multiple securities: write each mapping, but only
        # ingest company-level facts once per requested interval.
        row = {"company_id": company_id, "cik": cik, "start": start, "end": end,
               "filings": 0, "financial_facts": 0, "documents": 0, "status": "ok"}
        try:
            content, submissions_raw = client.fetch(f"https://data.sec.gov/submissions/CIK{cik}.json", source=SOURCE, headers=headers)
            payload = json.loads(content)
            _identity(store, payload, submissions_raw, security, company_id)
            if identity_key in seen_companies:
                row["status"] = "identity_only_duplicate_issuer_interval"
                companies.append(row)
                continue
            seen_companies.add(identity_key)
            filing_rows = [(r, submissions_raw, "SEC column arrays: index where accessionNumber=" + str(r.get("accessionNumber")) + "; corresponding index in sibling arrays") for r in _columns_to_rows(payload)]
            archive_count = 0
            archive_failures = 0
            for archive in payload.get("filings", {}).get("files", []):
                before = archive.get("filingTo") and str(archive["filingTo"]) < start
                after = archive.get("filingFrom") and str(archive["filingFrom"]) > end
                if before or after:
                    continue
                if not sec.get("historical_submissions", True):
                    store.issue("SEC_ARCHIVED_SUBMISSIONS_DISABLED", "Requested dates may require older submission pages.", dataset="filings", company_id=company_id)
                    row["status"] = "partial"
                    continue
                name = str(archive.get("name", ""))
                if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
                    store.issue("SEC_ARCHIVE_NAME_INVALID", "Unexpected archive filename rejected.", dataset="filings", company_id=company_id, details={"name": name})
                    continue
                try:
                    archive_content, archive_raw = client.fetch("https://data.sec.gov/submissions/" + name, source=SOURCE, headers=headers)
                    filing_rows.extend((r, archive_raw, "SEC column arrays: index where accessionNumber=" + str(r.get("accessionNumber")) + "; corresponding index in sibling arrays") for r in _columns_to_rows(json.loads(archive_content)))
                    archive_count += 1
                except Exception as exc:
                    archive_failures += 1
                    store.issue("SEC_ARCHIVE_UNAVAILABLE", str(exc), dataset="filings", company_id=company_id, details={"archive": name})
            # Keep metadata for all forms so companyfacts time joins are not
            # broken by a user choosing a narrower set of downloaded forms.
            all_filings = {r["accessionNumber"]: r for r, _, _ in filing_rows if r.get("accessionNumber")}
            wanted_forms = set(sec.get("forms", DEFAULT_FORMS))
            selected_rows = {}
            for filing, raw, locator in filing_rows:
                if _in_range(filing.get("filingDate"), start, end) and (not wanted_forms or filing.get("form") in wanted_forms):
                    selected_rows[filing["accessionNumber"]] = (filing, raw, locator)
            required = security.get("required_accessions", sec.get("required_accessions", []))
            for accession in required:
                if accession not in selected_rows:
                    store.issue("SEC_REQUIRED_ACCESSION_OUTSIDE_SELECTION", "A requested accession is unavailable within configured forms/date range.", dataset="documents", company_id=company_id, details={"accession": accession})
            selected = _selected_filings([r[0] for r in selected_rows.values()], max(0, int(sec.get("max_documents_per_company", 6))), required)
            # Selection limits govern NEW downloads, never the evidence we are
            # allowed to retain. A later window may select another annual/Q3
            # filing; dropping a previously fetched Q1 header would create a
            # metadata-only financial revision and obscure the old PIT value.
            cached_candidates = [r for r in all_filings.values() if _in_range(r.get("filingDate"), start, end)]
            cache_settings = {**sec, "download_exhibits": True, "max_exhibits_per_filing": 1000000}
            document_results, cached_count, _ = _collect_documents(store, client, cached_candidates, cik, company_id,
                                                                   cache_settings, headers, cache_only=True)
            newly_selected, processed_count, failures = _collect_documents(store, client, selected, cik, company_id, sec, headers,
                                                                           retained=document_results)
            retained_accessions = set(document_results)
            document_results.update(newly_selected)
            row["documents"] = sum((1 if r.get("header_raw") else 0) + len(r.get("downloaded_record_ids", [])) for r in document_results.values())
            row["cached_document_records_retained"] = cached_count
            row["cached_accessions_retained"] = len(retained_accessions)
            row["selected_document_records_processed"] = processed_count
            row["selected_primary_filings"] = len(selected)
            row["archive_pages"] = archive_count
            row["document_failures"] = failures
            if failures or archive_failures:
                row["status"] = "partial"
            originals_available = sum(bool(document_results.get(a, {}).get("primary_downloaded")) for a in selected_rows)
            row["primary_originals_available"] = originals_available
            if originals_available < len(selected_rows):
                row["status"] = "partial"
                store.issue("SEC_DOCUMENT_SAMPLE_LIMIT", "Filing metadata covers the requested forms; original documents cover only the configured selected sample.",
                            dataset="documents", company_id=company_id, details={"eligible_filings": len(selected_rows), "selected_filings": len(selected), "primary_originals_available_including_cache": originals_available})
            for accession, (filing, raw, locator) in selected_rows.items():
                doc_result = document_results.get(accession, {})
                evidence = _time_evidence(filing, doc_result.get("metadata"))
                base = _filing_base(cik, accession)
                primary_url = None
                if filing.get("primaryDocument"):
                    try:
                        primary_url = _document_url(base, filing["primaryDocument"])
                    except ValueError as exc:
                        row["status"] = "partial"
                        store.issue("SEC_PRIMARY_PATH_INVALID", str(exc), dataset="filings", company_id=company_id,
                                    details={"accession": accession, "filename": filing["primaryDocument"]})
                data = {
                    "accession": accession, "form": filing.get("form"), "filing_date": filing.get("filingDate"),
                    "report_date": filing.get("reportDate"), "acceptance_datetime_raw": filing.get("acceptanceDateTime"),
                    "accepted_local_clock_header": (doc_result.get("metadata") or {}).get("accepted_local_clock"),
                    "items": filing.get("items"), "primary_document": filing.get("primaryDocument"),
                    "primary_url": primary_url,
                    "is_xbrl": filing.get("isXBRL"), "is_inline_xbrl": filing.get("isInlineXBRL"),
                    "is_amendment": str(filing.get("form", "")).endswith("/A"), "amends_accession": None,
                    "revision_relation": "amendment form identified; original accession relationship not inferred",
                    "document_record_ids": doc_result.get("downloaded_record_ids", []),
                    "original_downloaded": bool(doc_result.get("primary_downloaded")),
                    "time_evidence_raw_id": doc_result.get("header_raw", {}).get("raw_id") if doc_result.get("header_raw") else None,
                    "time_caveat": "Acceptance is not verified public dissemination; daily conservative availability only.",
                }
                _put(store, "filings", accession, data, raw=raw, company_id=company_id, event_date=filing.get("filingDate"),
                    period_end=filing.get("reportDate") or None, source_locator=locator, quality="unverified", **evidence)
                _put(store, "event_calendar", "sec_filing:" + accession, {
                    "event_type": "actual_sec_filing", "event_date": filing.get("filingDate"), "status": "actual",
                    "form": filing.get("form"), "accession": accession,
                    "expected_earnings_date": None, "is_earnings_announcement_date": False,
                }, raw=raw, company_id=company_id, event_date=filing.get("filingDate"), source_locator=locator, quality="unverified", **evidence)
                row["filings"] += 1
            try:
                facts_content, facts_raw = client.fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", source=SOURCE, headers=headers)
                row["financial_facts"] = _financial_facts(store, json.loads(facts_content), facts_raw, company_id,
                                                         all_filings, document_results, start, end)
            except Exception as exc:
                row["status"] = "partial"
                store.issue("SEC_COMPANYFACTS_UNAVAILABLE", str(exc), dataset="financial_facts", company_id=company_id)
            store.issue("SEC_STRUCTURED_COVERAGE_LIMIT", "Companyfacts excludes custom-taxonomy and dimensional segment facts; full filings preserve notes for later parsing. Guidance, analyst estimates and earnings releases are not substituted for reported facts.",
                        dataset="financial_facts", company_id=company_id)
            store.issue("SEC_INTRADAY_AVAILABILITY_UNVERIFIED", "Original API acceptance clock retained. Header acceptance evidence available for selected filings; public dissemination is not independently observed. Historical queries use an explicit next-day NY assumption.",
                        dataset="filings", company_id=company_id)
            store.checkpoint(f"sec:company:{company_id}:{start}:{end}", row)
        except Exception as exc:
            row["status"] = "failed"
            row["reason"] = str(exc)
            store.issue("SEC_COMPANY_FAILED", str(exc), dataset="filings", company_id=company_id)
        companies.append(row)
    return {"status": "ok" if companies and all(r["status"] == "ok" for r in companies) else "partial",
            "companies": companies, "parser_version": PARSER_VERSION,
            "filings": sum(r["filings"] for r in companies), "financial_facts": sum(r["financial_facts"] for r in companies),
            "documents": sum(r["documents"] for r in companies)}


def financial_snapshot(store, company_id: str, as_of: str, known_at: str | None = None) -> dict:
    """Latest eligible disclosure per exact concept/unit/start/end, with evidence.

    This is not a statement-to-metric mapping, nor an assertion that an amendment
    restated every number. Quarter and cumulative contexts remain distinct.
    Tied/conflicting values fail closed; originals stay in financial_facts.
    """
    if not as_of:
        raise ValueError("An explicit as_of cutoff is required")
    rows = store.query("financial_facts", company_id=company_id, as_of=as_of, known_at=known_at, latest=False)
    groups = {}
    excluded=[]
    for row in rows:
        fact = row["data"]
        if not fact.get('taxonomy') or not fact.get('concept') or fact.get('is_observation') is False:
            excluded.append(row['record_id'])
            continue
        group = (fact.get("taxonomy"), fact.get("concept"), fact.get("unit") or row.get("unit"),
                 fact.get("start"), fact.get("end"), fact.get("consolidation_scope"), fact.get("accounting_basis"))
        groups.setdefault(group, []).append(row)
    selected, conflicts = [], []
    for group, candidates in sorted(groups.items(), key=lambda item: str(item[0])):
        by_key = {}
        for candidate in candidates:
            by_key.setdefault((candidate["source"], candidate["record_key"]), []).append(candidate)
        ambiguity = any(len({json.dumps(r["data"].get("value"), sort_keys=True) for r in versions}) > 1
                        and any(r.get("point_in_time_status") == "acquisition_only_ambiguous_revision" for r in versions)
                        for versions in by_key.values())
        newest = max(r.get("available_at") or "" for r in candidates)
        latest = [r for r in candidates if r.get("available_at") == newest]
        values = {json.dumps(r["data"].get("value"), sort_keys=True) for r in latest}
        if ambiguity or len(values) > 1:
            conflicts.append({"context": list(group),
                              "reason": "undated_same_accession_revision" if ambiguity else "different_values_at_same_availability",
                              "record_ids": [r["record_id"] for r in candidates],
                              "accessions": sorted({r["data"].get("accession", "") for r in candidates}),
                              "candidates": candidates})
            continue
        chosen = dict(sorted(latest, key=lambda r: (r["data"].get("accession") or "", r["record_id"]))[-1])
        chosen["snapshot_selection"] = {
            "method": "latest_eligible_disclosure_same_concept_unit_exact_period",
            "as_of": as_of, "known_at": known_at,
            "supporting_record_ids": [r["record_id"] for r in latest],
            "supporting_accessions": sorted({r["data"].get("accession", "") for r in latest}),
            "earlier_eligible_disclosures": len(candidates) - len(latest),
        }
        selected.append(chosen)
    return {"company_id": company_id, "as_of": as_of, "known_at": known_at,
            "records": selected, "conflicts": conflicts,
            "excluded_nonstandard_or_unavailable_count":len(excluded),
            "excluded_reason":"Raw inline/custom/dimensional observations remain queryable but are not silently compared with entity-wide API concepts.",
            "unavailable_reason": None if selected else ("all_eligible_contexts_conflict" if conflicts else "no_records_with_sufficient_availability_and_acquisition_evidence"),
            "time_caveat": "SEC rows use conservative next-day availability; no verified intraday dissemination reconstruction.",
            "scope_caveat": "Standard-taxonomy entity-wide facts only; same concept and exact period remain distinct from similar concepts/other periods."}
