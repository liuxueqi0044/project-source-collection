"""Offline, read-only raw-first SEC initialization audit.

No production discovery/parser imports and no network. Expected filings and files
are frozen from saved official submissions and SGML/HTML filing directories
before querying production records/inventory. Missing listing pages or filing
directories make the denominator incomplete rather than silently shrinking it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import fnmatch
import hashlib
import html
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tomllib
from urllib.parse import quote, urljoin, urlparse

from audit_machine import (attribute, independent_context, independent_number,
    independent_path, independent_soup, independent_unit, local, namespace)
from build_reference_v4 import sample_original_nodes

VERSION = "live-raw-first-audit-v4.1.1"
DEEP = {"relations", "operating_metrics", "commitments", "debt_maturities", "risk_disclosures"}
PERIODIC_FORMS = {"10-K", "10-Q", "20-F"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def configured_scope(config_file, storage=None):
    path = Path(config_file).resolve()
    conf = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent.parent if path.parent.name == "configs" else path.parent
    settings = conf.get("initialization", {})
    universe = conf.get("universe", {})
    if universe.get("mode", "configured") != "configured":
        raise ValueError("Audit requires a fixed configured pool or CSV")
    targets = list(conf.get("securities", []))
    targets += [{"symbol": symbol} for symbol in universe.get("symbols", [])]
    targets += [{"cik": cik} for cik in universe.get("ciks", [])]
    csv_evidence = None
    if universe.get("csv_file"):
        csv_path = path.parent / universe["csv_file"]
        content = csv_path.read_bytes()
        targets += list(csv.DictReader(content.decode("utf-8-sig").splitlines()))
        csv_evidence = {"path": str(csv_path.resolve()), "sha256": hashlib.sha256(content).hexdigest()}
    if not targets:
        raise ValueError("Audit pool is empty")
    root = Path(storage).resolve() if storage else (base / conf.get("storage", "data/live")).resolve()
    sec = conf.get("sec", {})
    return root, {"market": conf.get("market"), "start": str(settings.get("start", conf["start"])),
        "end": str(settings.get("end", conf["end"])), "targets": targets,
        "forms": sec.get("forms", []), "attachment_types": sec.get("attachment_types", ["EX-*"]),
        "support_document_types": sec.get("support_document_types", ["XML", "EX-101.INS"]),
        "config_path": str(path), "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "csv_evidence": csv_evidence}


class Evidence:
    def __init__(self, root, db):
        self.root = root
        self.rows = [dict(row) for row in db.execute("""SELECT r.raw_id,r.retrieval_id,r.source,r.url,r.retrieved_at,
            b.path,b.sha256,b.byte_size,b.media_type FROM raw_retrievals r JOIN raw_blobs b USING(raw_id)
            ORDER BY r.retrieved_at,r.retrieval_id""")]
        self.latest, self.raws, self.checked = {}, {}, {}
        for row in self.rows:
            if urlparse(row["url"]).hostname in {"www.sec.gov", "data.sec.gov", "sec.gov"}:
                self.latest[row["url"]] = row
            self.raws[row["raw_id"]] = row

    def read(self, row):
        path = Path(row["path"])
        path = path if path.is_absolute() else self.root / path
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"] or len(content) != row["byte_size"]:
            raise ValueError("Raw integrity failure: " + row["raw_id"])
        self.checked[row["raw_id"]] = {"raw_id": row["raw_id"], "sha256": row["sha256"], "byte_size": len(content)}
        return content

    def saved(self, row):
        if row is None:
            return {"saved": False, "reason": "No original retrieval in this snapshot"}
        try:
            if row["raw_id"] not in self.checked:
                self.read(row)
            return {"saved": True, "raw_id": row["raw_id"], "sha256": row["sha256"], "retrieval_id": row["retrieval_id"], "retrieved_at": row["retrieved_at"]}
        except (OSError, ValueError) as exc:
            return {"saved": False, "raw_id": row["raw_id"], "reason": str(exc)}

    @staticmethod
    def reference(row, locator):
        return {key: row[key] for key in ("raw_id", "sha256", "url", "retrieved_at")} | {"locator": locator}


def resolve_targets(evidence, scope):
    by_symbol = defaultdict(set)
    inputs = []
    for row in evidence.latest.values():
        if not ("company_tickers" in row["url"] or re.search(r"/submissions/CIK\d{10}\.json$", row["url"])):
            continue
        try:
            value = json.loads(evidence.read(row))
            if "filings" in value and value.get("cik"):
                items = [(symbol, value["cik"]) for symbol in value.get("tickers", [])]
            elif isinstance(value.get("fields"), list) and isinstance(value.get("data"), list):
                listing = [dict(zip(value["fields"], item)) for item in value["data"]]
                items = [(item.get("ticker"), item.get("cik")) for item in listing]
            else:
                items = [(item.get("ticker"), item.get("cik_str")) for item in value.values() if isinstance(item, dict)]
            for symbol, cik in items:
                if symbol and cik is not None:
                    by_symbol[str(symbol).upper()].add(str(int(cik)).zfill(10))
            inputs.append(evidence.reference(row, "official current ticker/CIK directory or filer tickers"))
        except (ValueError, TypeError, AttributeError, OSError):
            continue
    targets, unresolved = {}, []
    for target in scope["targets"]:
        symbol = str(target.get("symbol", "")).upper().strip()
        matches = {str(int(target["cik"])).zfill(10)} if target.get("cik") else by_symbol.get(symbol, set())
        if len(matches) != 1:
            unresolved.append({"target": target, "reason": "Saved official identity evidence absent or ambiguous", "candidate_ciks": sorted(matches)})
            continue
        cik = next(iter(matches))
        targets.setdefault(cik, {"company_id": "sec:"+cik, "cik": cik, "symbols": []})
        if symbol and symbol not in targets[cik]["symbols"]:
            targets[cik]["symbols"].append(symbol)
    return list(targets.values()), unresolved, inputs


def document_url(base, filename):
    parsed = urlparse(filename)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in filename or ".." in PurePosixPath(filename).parts or filename.startswith("/"):
        raise ValueError("Unverified source filename: " + filename)
    return base + quote(filename, safe="/-._~")


def manifest_entries(body, base):
    """Raw SEC SGML inventory; standard filing-index tables are a fallback."""
    text = html.unescape(body.decode("utf-8", errors="replace"))
    entries = []
    for number, block in enumerate(re.findall(r"<DOCUMENT>(.*?)(?=</DOCUMENT>|\Z)", text, re.I|re.S), 1):
        filename = re.search(r"<FILENAME>\s*([^\r\n<]+)", block, re.I)
        kind = re.search(r"<TYPE>\s*([^\r\n<]+)", block, re.I)
        if filename and kind:
            entries.append({"filename": filename[1].strip(), "type": kind[1].strip(), "locator": f"SGML DOCUMENT[{number}]/FILENAME"})
    if entries:
        return entries, "sgml"
    soup = independent_soup(body)
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headings = [cell.get_text(" ", strip=True).lower() for cell in rows[0].find_all(["th", "td"], recursive=False)]
        if "type" not in headings or "document" not in headings:
            continue
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) != len(headings):
                continue
            link = cells[headings.index("document")].find("a", href=True)
            if link is None:
                continue
            url = urljoin(base, link["href"])
            if not url.startswith(base):
                continue
            entries.append({"filename": url[len(base):], "type": cells[headings.index("type")].get_text(" ", strip=True), "locator": independent_path(tr)})
    return entries, "html_filing_index"


def build_document_reference(evidence, scope):
    targets, unresolved, identities = resolve_targets(evidence, scope)
    listings, gaps, filings, conflicts, metadata_limitations = [], [], {}, [], []
    for target in targets:
        cik = target["cik"]
        pending = [(f"https://data.sec.gov/submissions/CIK{cik}.json", None)]
        visited = set()
        while pending:
            url, discovered_by = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            row = evidence.latest.get(url)
            item = {"company_id": target["company_id"], "url": url, "discovered_by": discovered_by, **evidence.saved(row)}
            listings.append(item)
            if not item["saved"]:
                gaps.append({"code": "REFERENCE_LISTING_MISSING", **item})
                continue
            try:
                value = json.loads(evidence.read(row))
                for index, archive in enumerate(value.get("filings", {}).get("files", [])):
                    if archive.get("filingTo", scope["end"]) < scope["start"] or archive.get("filingFrom", scope["start"]) > scope["end"]:
                        continue
                    name = str(archive.get("name", ""))
                    if not re.fullmatch("CIK"+cik+r"-submissions-\d+\.json", name):
                        gaps.append({"code": "REFERENCE_ARCHIVE_PATH_UNKNOWN", "archive": archive, "evidence": evidence.reference(row, f"/filings/files/{index}")})
                        continue
                    pending.append(("https://data.sec.gov/submissions/"+name, evidence.reference(row, f"/filings/files/{index}")))
                columns = value.get("filings", {}).get("recent", value.get("recent", value))
                if not isinstance(columns.get("accessionNumber"), list):
                    raise ValueError("Missing accession column")
                required = ("filingDate", "form", "primaryDocument")
                if any(len(columns.get(key, [])) != len(columns["accessionNumber"]) for key in required):
                    raise ValueError("Submissions required column lengths differ")
                optional = {}
                for key in ("reportDate", "acceptanceDateTime"):
                    candidate = columns.get(key)
                    optional[key] = candidate if isinstance(candidate, list) and len(candidate) == len(columns["accessionNumber"]) else None
                    if optional[key] is None:
                        metadata_limitations.append({"code": "REFERENCE_OPTIONAL_METADATA_COLUMN_UNAVAILABLE", "column": key,
                            "evidence": evidence.reference(row, key), "effect": "Filing/document denominator retained; this time field is unknown"})
                for index, accession in enumerate(columns["accessionNumber"]):
                    day, form, primary = [columns[key][index] for key in required]
                    if not scope["start"] <= day <= scope["end"] or scope["forms"] and form not in scope["forms"]:
                        continue
                    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                        raise ValueError("Invalid accession in scoped listing")
                    filing = {"accession": accession, "company_id": target["company_id"], "cik": cik, "filing_date": day, "form": form,
                        "report_date": optional["reportDate"][index] if optional["reportDate"] is not None else None,
                        "acceptance_datetime_raw": optional["acceptanceDateTime"][index] if optional["acceptanceDateTime"] is not None else None,
                        "periodic_report": form.removesuffix("/A") in PERIODIC_FORMS,
                        "primary": primary, "base": f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/',
                        "evidence": evidence.reference(row, f"accessionNumber[{index}] and matching filingDate/form/primaryDocument/reportDate/acceptanceDateTime"),
                        "metadata_evidence": {key: evidence.reference(row, f"{key}[{index}]") for key in ("form", "filingDate", "reportDate", "acceptanceDateTime")}}
                    prior = filings.get(accession)
                    if prior and any(prior[key] != filing[key] for key in ("company_id", "filing_date", "form", "primary")):
                        conflicts.append({"accession": accession, "versions": [prior, filing]})
                    if prior is None or filing["evidence"]["retrieved_at"] > prior["evidence"]["retrieved_at"]:
                        filings[accession] = filing
            except (ValueError, TypeError, KeyError) as exc:
                gaps.append({"code": "REFERENCE_LISTING_INVALID", "url": url, "reason": str(exc)})
    documents, directory_status, outside = {}, [], []
    for filing in filings.values():
        base, accession = filing["base"], filing["accession"]
        def add(filename, kind, ev, role):
            try:
                url = document_url(base, filename)
            except ValueError as exc:
                gaps.append({"code": "REFERENCE_DOCUMENT_URL_UNKNOWN", "reason": str(exc), "accession": accession, "evidence": ev})
                return
            key = accession+"|"+filename
            documents.setdefault(key, {"reference_id": "doc:"+key, "accession": accession, "company_id": filing["company_id"],
                "filing_date": filing["filing_date"], "filing_form": filing["form"], "document_type": kind, "filename": filename,
                "url": url, "role": role, "evidence": ev})
        if filing["primary"]:
            add(filing["primary"], filing["form"], filing["evidence"], "submissions_primary")
        urls = [base+accession+suffix for suffix in ("-index-headers.html", "-index.html", "-headers.html", ".txt")]
        candidates = [evidence.latest[url] for url in urls if url in evidence.latest]
        usable = []
        for row in candidates:
            try:
                entries, method = manifest_entries(evidence.read(row), base)
                if entries:
                    usable.append((row, entries, method))
            except (OSError, ValueError) as exc:
                gaps.append({"code": "REFERENCE_DIRECTORY_INVALID", "url": row["url"], "reason": str(exc)})
        status = {"accession": accession, "company_id": filing["company_id"], "filing_date": filing["filing_date"],
            "directory_available": bool(usable), "expected_directory_url": urls[0], "entries": 0}
        if not usable:
            gaps.append({"code": "REFERENCE_FILING_DIRECTORY_MISSING", **status,
                "effect": "Primary denominator remains known; attachment and original-XML denominator is incomplete"})
        else:
            row, entries, method = max(usable, key=lambda item: (item[0]["retrieved_at"], len(item[1])))
            status.update(entries=len(entries), method=method, evidence=evidence.reference(row, "complete declared document manifest"))
            for entry in entries:
                kind = entry["type"]
                included = kind == filing["form"] or any(fnmatch.fnmatchcase(kind, pattern) for pattern in scope["attachment_types"]+scope["support_document_types"])
                if included:
                    role = "manifest_form_original" if kind == filing["form"] else "support" if any(fnmatch.fnmatchcase(kind, pattern) for pattern in scope["support_document_types"]) else "attachment"
                    add(entry["filename"], kind, evidence.reference(row, entry["locator"]), role)
                else:
                    outside.append({"accession": accession, **entry, "reason": "Outside configured form/attachment/support types"})
        directory_status.append(status)
    return {"targets": targets, "unresolved_targets": unresolved, "identity_evidence": identities, "listings": listings,
        "filings": sorted(filings.values(), key=lambda row: row["accession"]), "documents": sorted(documents.values(), key=lambda row: row["reference_id"]),
        "filing_directories": directory_status, "outside_configured_scope": outside, "reference_gaps": gaps, "listing_conflicts": conflicts,
        "metadata_limitations": metadata_limitations,
        "denominator_complete_for_saved_source_snapshot": not unresolved and not gaps and not conflicts}


def expanded_qname(node, value):
    value = " ".join(str(value or "").split())
    part = r"[A-Za-z_][A-Za-z0-9_.-]*"
    if not re.fullmatch(part+r"(?::"+part+r")?", value):
        raise ValueError("QName outside independent lexical verifier")
    prefix, name = value.split(":", 1) if ":" in value else ("", value)
    uri = namespace(node, prefix)
    if prefix and uri is None:
        raise ValueError("Unbound independent QName")
    return {"namespace_uri": uri, "local_name": name}


def audited_context(node):
    value = independent_context(node)
    dimensions = []
    for member in node.find_all(lambda tag: local(tag.name) in {"explicitmember", "typedmember"}):
        if local(member.name) == "typedmember":
            raise ValueError("Typed domain schema not independently validated")
        dimensions.append({"axis_expanded": expanded_qname(member, attribute(member, "dimension")),
            "member_expanded": expanded_qname(member, member.get_text(" ", strip=True)), "type": "explicit",
            "context_element": next((local(parent.name) for parent in member.parents if local(parent.name) in {"segment", "scenario"}), None)})
    semantic = {key: value[key] for key in ("period_start", "period_end", "entity")}
    semantic["dimension_definitions"] = sorted(dimensions, key=encode)
    return value, semantic


def field_reference(evidence, reference, per_document, documents_per_company):
    eligible = defaultdict(list)
    for target in reference["documents"]:
        row = evidence.latest.get(target["url"])
        if row and evidence.saved(row)["saved"] and re.search(br"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?(?:nonFraction|fraction)\b", evidence.read(row), re.I):
            eligible[target["company_id"]].append((target, row))
    fields, sampling = [], []
    for company, candidates in sorted(eligible.items()):
        candidates.sort(key=lambda pair: hashlib.sha256(("v41-live-raw-document|"+pair[0]["reference_id"]).encode()).hexdigest())
        selected = candidates[:documents_per_company]
        sampling.append({"company_id": company, "eligible_numeric_documents": len(candidates), "selected_urls": [row["url"] for _, row in selected]})
        for target, raw in selected:
            original = independent_soup(evidence.read(raw))
            contexts, units = {}, {}
            context_semantics = {}
            same_accession = [item for item in reference["documents"] if item["accession"] == target["accession"]]
            supports = [raw]+[evidence.latest[item["url"]] for item in same_accession if item["url"] in evidence.latest and evidence.latest[item["url"]]["raw_id"] != raw["raw_id"]]
            seen = set()
            for support in supports:
                if support["raw_id"] in seen:
                    continue
                seen.add(support["raw_id"])
                try:
                    body = evidence.read(support)
                    if not re.search(br"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?(?:context|unit)\b", body, re.I):
                        continue
                    soup = original if support["raw_id"] == raw["raw_id"] else independent_soup(body)
                    for node in soup.find_all(lambda tag: local(tag.name) in {"context", "unit"}):
                        identity, kind = attribute(node, "id"), local(node.name)
                        definitions = contexts if kind == "context" else units
                        try:
                            if kind == "context":
                                value, semantic = audited_context(node)
                            else:
                                value = independent_unit(node)
                                semantic = {key: value[key] for key in ("expanded_numerator", "expanded_denominator")}
                            signature = encode(semantic)
                            if (kind, identity) in context_semantics and context_semantics[(kind, identity)] != signature:
                                definitions[identity] = (None, support, node)
                            elif identity not in definitions:
                                definitions[identity] = (value, support, node)
                                context_semantics[(kind, identity)] = signature
                        except ValueError:
                            definitions[identity] = (None, support, node)
                except (OSError, ValueError):
                    continue
            nodes = original.find_all(lambda tag: local(tag.name) in {"nonfraction", "fraction"})
            chosen, strata = sample_original_nodes(nodes, contexts, units, raw["sha256"], per_document)
            sampling.append({"company_id": company, "raw_id": raw["raw_id"], "url": raw["url"], "numeric_targets_in_original": len(nodes), "sampled": len(chosen), "strata": strata})
            for node in chosen:
                locator = independent_path(node)
                item = {"reference_id": "field:"+hashlib.sha256((raw["sha256"]+locator).encode()).hexdigest(), "company_id": company,
                    "evidence": evidence.reference(raw, locator), "concept": attribute(node, "name"), "context_id": attribute(node, "contextref"),
                    "unit_id": attribute(node, "unitref"), "reference_status": "unverified", "expected": None}
                try:
                    if local(node.name) == "fraction":
                        raise ValueError("Fraction verifier not implemented")
                    number, text = independent_number(node)
                    context, cr, cn = contexts[item["context_id"]]
                    unit, ur, un = units[item["unit_id"]]
                    if context is None or unit is None:
                        raise ValueError("Ambiguous/unsupported context or unit")
                    _, context_identity = audited_context(cn)
                    item.update(reference_status="verified", original_text=text, expected={"value": str(number) if number is not None else None,
                        "period_start": context["period_start"], "period_end": context["period_end"], "dimensions": context["dimensions"],
                        "dimension_definitions": context_identity["dimension_definitions"], "entity": context["entity"], "unit": unit["label"], "currency": unit["currency"]},
                        support_evidence=[evidence.reference(cr, independent_path(cn)), evidence.reference(ur, independent_path(un))])
                except (ValueError, KeyError, ArithmeticError) as exc:
                    item["reference_limitation"] = str(exc)
                fields.append(item)
    return fields, sampling


def compare(db, evidence, reference):
    """Only now consult production outputs; they never choose the denominator."""
    inventory = defaultdict(list)
    for row in db.execute("SELECT * FROM machine_inventory WHERE source_id='sec_edgar'"):
        inventory[row["url"]].append(dict(row))
    documents = []
    for target in reference["documents"]:
        matches = inventory.get(target["url"], [])
        current = max(matches, key=lambda row: row.get("last_discovered_at") or "") if matches else {}
        saved = evidence.saved(evidence.latest.get(target["url"]))
        documents.append({**target, "discovered": bool(matches), "saved_original": saved,
            "production_raw_matches_saved_version": bool(current.get("raw_id") and current.get("raw_id") == saved.get("raw_id")),
            "workflow": {key: current.get(key, "unknown") for key in ("state", "raw_state", "refresh_state", "parse_state", "upstream_state", "last_error")},
            "resource_ids": [row["resource_id"] for row in matches]})
    filing_keys = {row[0] for row in db.execute("SELECT DISTINCT record_key FROM records WHERE source='sec_edgar' AND dataset='filings'")}
    filing_rows = [{**row, "record_present": row["accession"] in filing_keys} for row in reference["filings"]]
    gaps = defaultdict(list)
    for row in db.execute("SELECT gap_id,code,details_json,resolved_at FROM machine_gaps WHERE source_id='sec_edgar'"):
        details = json.loads(row["details_json"])
        if row["resolved_at"] is None:
            gaps[(details.get("raw_id"), details.get("source_locator"))].append({"gap_id": row["gap_id"], "code": row["code"]})
    raws = sorted({item["evidence"]["raw_id"] for item in reference["fields"]})
    records = defaultdict(list)
    if raws:
        query = """SELECT * FROM records WHERE source='sec_edgar' AND dataset='financial_facts'
            AND raw_id IN ("""+",".join("?" for _ in raws)+") ORDER BY sequence"
        latest = {}
        for row in db.execute(query, raws):
            item = dict(row); item["data"] = json.loads(item.pop("data_json"))
            latest[(item["raw_id"], item["record_key"])] = item
        for item in latest.values():
            if item["data"].get("status") != "not_emitted_by_current_parser":
                records[item["raw_id"]].append(item)
    fields = []
    for target in reference["fields"]:
        matches = [row for row in records[target["evidence"]["raw_id"]] if row["source_locator"] == target["evidence"]["locator"] and
            row["data"].get("concept_qname") == target["concept"] and row["data"].get("context_id") == target["context_id"] and row["data"].get("unit_id") == target["unit_id"]]
        rejection = [row for row in gaps[(target["evidence"]["raw_id"], target["evidence"]["locator"])] if any(word in row["code"] for word in ("REJECT", "UNSUPPORTED", "MISSING"))]
        checks = []
        if target["reference_status"] != "verified":
            outcome = "reference_unverified"
        elif not matches:
            same_identity = [row for row in records[target["evidence"]["raw_id"]] if row["data"].get("concept_qname") == target["concept"] and row["data"].get("context_id") == target["context_id"] and row["data"].get("unit_id") == target["unit_id"]]
            current = [row for row in inventory.get(target["evidence"]["url"], []) if row.get("raw_id") == target["evidence"]["raw_id"]]
            parsed = any(row.get("parse_state") == "parsed" for row in current)
            partial = any(row.get("parse_state") == "partial_parse" for row in current)
            outcome = "locator_unresolved" if same_identity else "explicitly_rejected" if rejection else "silent_omission_candidate" if parsed else "missing_field_without_explicit_rejection_in_partial_document" if partial else "original_not_yet_parsed"
        else:
            for row in matches:
                data = row["data"]
                actual = {"value": data.get("value"), "period_start": row["period_start"], "period_end": row["period_end"],
                    "dimensions": data.get("dimensions"), "entity": data.get("entity"), "unit": row["unit"], "currency": row["currency"],
                    "dimension_definitions": sorted([{key: dimension.get(key) for key in ("axis_expanded", "member_expanded", "type", "context_element")} for dimension in data.get("dimension_definitions", [])], key=encode)}
                compared = {}
                for key, wanted in target["expected"].items():
                    found = actual[key]
                    try:
                        equal = Decimal(str(found)) == Decimal(str(wanted)) if key == "value" and found is not None and wanted is not None else found == wanted
                    except ArithmeticError:
                        equal = False
                    compared[key] = {"expected": wanted, "actual": found, "passed": equal}
                checks.append({"record_id": row["record_id"], "parser_version": row["parser_version"], "quality": row["quality"], "checks": compared})
            outcome = "extracted_correct" if all(all(check["passed"] for check in row["checks"].values()) for row in checks) else "extracted_mismatch"
        fields.append({**target, "outcome": outcome, "checks": checks, "explicit_rejections": rejection})
    return filing_rows, documents, fields


def summary_rows(rows):
    return {"reference_denominator": len(rows), "discovered": sum(row["discovered"] for row in rows),
        "original_saved_hash_verified": sum(row["saved_original"]["saved"] for row in rows),
        "production_references_latest_saved_original": sum(row["production_raw_matches_saved_version"] for row in rows),
        "parse_states": dict(Counter(row["workflow"]["parse_state"] for row in rows)),
        "refresh_states": dict(Counter(row["workflow"]["refresh_state"] for row in rows)),
        "upstream_states": dict(Counter(row["workflow"]["upstream_state"] for row in rows))}


def valid_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        return None


def period_inventory(db, reference, filings, documents):
    """Filing report dates and numeric context dates are different contracts.

    Reference metadata remains raw-first; inventory queries here cannot alter the
    reference denominator. All retained fact periods are an inventory, not recall.
    """
    companies = [target["company_id"] for target in reference["targets"]]
    if not companies:
        return {"by_company": {}, "filing_metadata": [], "financial_fact_context_periods": []}
    placeholders = ",".join("?" for _ in companies)
    latest = {}
    query = """SELECT sequence,record_id,record_key,company_id,period_end,event_date,published_at,
        raw_id,source_locator,parser_version,data_json FROM records
        WHERE source='sec_edgar' AND dataset='filings' AND company_id IN ("""+placeholders+") ORDER BY sequence"
    for row in db.execute(query, companies):
        latest[(row["company_id"], row["record_key"])] = dict(row)
    by_accession = defaultdict(list)
    for document in documents:
        by_accession[(document["company_id"], document["accession"])].append(document)
    metadata = []
    for filing in filings:
        original = latest.get((filing["company_id"], filing["accession"]))
        production, checks = None, {}
        if original:
            data = json.loads(original["data_json"])
            production = {key: value for key, value in original.items() if key != "data_json"}
            production["data"] = {key: data.get(key) for key in ("accession", "form", "filing_date", "report_date", "acceptance_datetime_raw")}
            for name, actual, expected in (
                ("form", data.get("form"), filing["form"]),
                ("filing_date", data.get("filing_date"), filing["filing_date"]),
                ("report_date", data.get("report_date"), filing.get("report_date")),
                ("acceptance_datetime_raw", data.get("acceptance_datetime_raw"), filing.get("acceptance_datetime_raw")),
                ("standard_period_end_is_report_date", original["period_end"], filing.get("report_date")),
            ):
                # A missing optional column cannot establish a mismatch or pass.
                known = expected is not None
                if name == "standard_period_end_is_report_date":
                    expected = expected or None
                checks[name] = {"expected": expected, "actual": actual,
                    "status": "unknown_reference" if not known else "matched" if actual == expected else "mismatch"}
        primary = [row for row in by_accession[(filing["company_id"], filing["accession"]) ]
            if row["role"] in {"submissions_primary", "manifest_form_original"}]
        metadata.append({**filing, "production_metadata": production, "metadata_contract_checks": checks,
            "primary_original_saved": any(row["saved_original"]["saved"] for row in primary),
            "primary_original_states": [{"url": row["url"], "saved": row["saved_original"]["saved"], "workflow": row["workflow"]} for row in primary],
            "report_date_interpretation": "SEC-declared periodic report period; not independently checked against every statement" if filing["periodic_report"] else "Form-specific SEC reportDate; not classified as an annual/quarterly financial report period"})
    context_query = """SELECT company_id,period_start,period_end,COUNT(*) AS retained_versions,
        COUNT(DISTINCT record_key) AS distinct_logical_fact_keys,MIN(published_at) AS earliest_source_publication,
        MAX(published_at) AS latest_source_publication FROM records WHERE source='sec_edgar'
        AND dataset='financial_facts' AND company_id IN ("""+placeholders+") GROUP BY company_id,period_start,period_end ORDER BY company_id,period_end,period_start"
    periods = [dict(row) for row in db.execute(context_query, companies)]
    grouped = {}
    for company in companies:
        items = [row for row in metadata if row["company_id"] == company]
        financial_reports = [row for row in items if row["periodic_report"]]
        saved_reports = [row for row in financial_reports if row["primary_original_saved"]]
        latest_reference = max((day for row in financial_reports if (day := valid_day(row.get("report_date")))), default=None)
        latest_saved = max((day for row in saved_reports if (day := valid_day(row.get("report_date")))), default=None)
        context_rows = [row for row in periods if row["company_id"] == company]
        context_days = [day for row in context_rows if (day := valid_day(row["period_end"]))]
        grouped[company] = {
            "filings_by_form": [{"form": form, "reference_filings": sum(row["form"] == form for row in items),
                "declared_report_dates": sorted({row["report_date"] for row in items if row["form"] == form and valid_day(row.get("report_date"))}),
                "unknown_or_blank_report_dates": sum(not valid_day(row.get("report_date")) for row in items if row["form"] == form)} for form in sorted({row["form"] for row in items})],
            "periodic_reports": {"base_forms": sorted(PERIODIC_FORMS), "amendments_included": True,
                "reference_filings": len(financial_reports), "with_saved_primary_original": len(saved_reports),
                "latest_source_declared_report_date_in_reference": latest_reference,
                "latest_source_declared_report_date_with_saved_primary": latest_saved,
                "unknown_or_blank_report_dates": sum(not valid_day(row.get("report_date")) for row in financial_reports),
                "latest_saved_report_filings": [{key: row[key] for key in ("accession", "form", "report_date", "filing_date", "acceptance_datetime_raw", "evidence", "primary_original_states")}
                    for row in saved_reports if latest_saved and row.get("report_date") == latest_saved],
                "basis": "Saved SEC submissions reportDate plus hash-verified primary original; this does not prove all financial statements or values parsed"},
            "all_stored_financial_fact_context_periods": {"scope": "All retained SEC financial_facts versions for this configured company, including periods outside filing-date audit scope",
                "retained_versions": sum(row["retained_versions"] for row in context_rows), "distinct_period_pairs": len(context_rows),
                "minimum_context_period_end": min(context_days, default=None), "maximum_context_period_end": max(context_days, default=None),
                "unknown_or_invalid_period_end_versions": sum(row["retained_versions"] for row in context_rows if not valid_day(row["period_end"])),
                "context_end_after_latest_saved_report_date_versions": sum(row["retained_versions"] for row in context_rows if valid_day(row["period_end"]) and row["period_end"] > latest_saved) if latest_saved else None,
                "is_latest_financial_report_period": False,
                "interpretation": "Context dates can describe comparative, forward-looking or maturity/commitment periods. The maximum must never be labelled latest financial report period."},
            "metadata_contract_outcomes": dict(Counter(check["status"] for row in items for check in row["metadata_contract_checks"].values()))}
    return {"by_company": grouped, "filing_metadata": metadata, "financial_fact_context_periods": periods,
        "filings_standard_period_end_contract": "SEC submissions reportDate or null; distinct from financial_facts context period_end",
        "acceptance_caveat": "acceptanceDateTime is preserved verbatim; acceptance is not independently observed public dissemination",
        "report_date_missing_policy": "Unknown or blank reportDate remains unknown; never inferred from latest numeric context"}


def run(config_file, output, storage=None, per_document=10, max_documents=4):
    out = Path(output).resolve()
    if out.exists():
        raise ValueError("Refusing to overwrite audit evidence; choose a new output directory")
    root, scope = configured_scope(config_file, storage)
    if scope["market"] != "US":
        raise ValueError("This independent audit supports SEC US scope only")
    started = now()
    db = sqlite3.connect((root/"data.sqlite3").as_uri()+"?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    # Pin one SQLite read snapshot; no migration, checkpoint, task or file mutation.
    snapshot = dict(db.execute("SELECT COUNT(*) AS raw_retrievals,MAX(retrieved_at) AS latest_recorded_acquisition FROM raw_retrievals").fetchone())
    try:
        evidence = Evidence(root, db)
        reference = build_document_reference(evidence, scope)
        reference["fields"], sampling = field_reference(evidence, reference, per_document, max_documents)
        reference.update(schema_version="live-initialization-reference-v4.1", scope=scope, sampling=sampling,
            reference_provenance={"method": "Saved SEC submissions + declared filing SGML/HTML directory inventory; deterministic raw-first iXBRL sample",
                "uses_production_records_or_inventory_to_build_reference": False, "production_parser_imported": False,
                "network_requests": 0, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "reused_independent_helpers": ["build_reference_v4.sample_original_nodes", "audit_machine independent BeautifulSoup/Decimal/QName verifier"],
                "helper_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ("build_reference_v4.py", "audit_machine.py")}})
        # Persist the independent denominator before inspecting production outputs.
        out.mkdir(parents=True)
        (out/"reference.json").write_text(json.dumps(reference, indent=2, ensure_ascii=False), encoding="utf-8")
        filings, documents, fields = compare(db, evidence, reference)
        period_report = period_inventory(db, reference, filings, documents)
        by_company = {company["company_id"]: summary_rows([row for row in documents if row["company_id"] == company["company_id"]]) for company in reference["targets"]}
        companies = [company["company_id"] for company in reference["targets"]]
        deep = Counter()
        if companies:
            query = "SELECT dataset,data_json,period_end,published_at FROM records WHERE source='sec_edgar' AND company_id IN ("+",".join("?" for _ in companies)+") AND dataset IN ("+",".join("?" for _ in DEEP)+")"
            for row in db.execute(query, companies+sorted(DEEP)):
                data = json.loads(row["data_json"])
                stage = data.get("fact_stage", "unclassified")
                deep[(row["dataset"], stage)] += 1
        result = {"audit_version": VERSION, "started_at": started, "finished_at": now(), "read_only": True, "network_requests": 0,
            "storage": str(root), "scope": scope, "database_snapshot": snapshot,
            "reference_denominator_complete_for_saved_source_snapshot": reference["denominator_complete_for_saved_source_snapshot"],
            "filings": {"reference_denominator": len(filings), "metadata_record_present": sum(row["record_present"] for row in filings)},
            "documents": summary_rows(documents), "by_company": by_company,
            "report_periods_and_context_periods": period_report["by_company"],
            "field_extraction": {"sample_reference_denominator": len(fields), "outcomes": dict(Counter(row["outcome"] for row in fields)),
                "sampling": {"per_document": per_document, "max_documents_per_company": max_documents}, "complete_field_recall_claim": False},
            "deep_semantic": {"independent_reference_denominator": None, "complete": False,
                "stored_versions_by_stage": [{"dataset": dataset, "stage": stage, "stored_versions": count} for (dataset, stage), count in sorted(deep.items())],
                "basis": "Inventory only, across all retained versions for configured companies; candidate fragments are not facts or a reference denominator"},
            "listing_resources": {"expected": len(reference["listings"]), "saved": sum(row["saved"] for row in reference["listings"])},
            "filing_directories": {"expected": len(reference["filing_directories"]), "usable": sum(row["directory_available"] for row in reference["filing_directories"])},
            "unresolved_targets": reference["unresolved_targets"], "reference_gaps": reference["reference_gaps"], "listing_conflicts": reference["listing_conflicts"],
            "filing_metadata_limitations": reference["metadata_limitations"],
            "limitations": ["Saved official-source snapshot, not an independent upstream publisher. Unannounced omissions in SEC itself remain unknown.",
                "Missing required submissions archives or filing directories leave the attachment/original XML denominator incomplete.",
                "Saved originals, current production parse state and latest upstream confirmation are separate. No network freshness is established by this audit.",
                "Numeric sampling starts with raw tags and is frozen before records are read; it does not cover every numeric field or untagged tables/guidance.",
                "Typed domains, unsupported transforms including fixed-zero, ambiguous definitions and path mismatches stay unverified.",
                "The date scope is filing disclosure date, not every period described inside a report or an unbiased historical stock universe.",
                "Latest annual/quarterly report date comes only from SEC reportDate for 10-K/10-Q/20-F and amendments with saved originals. Maximum numeric context period is never a financial-report freshness measure.",
                "Deep semantic counts include retained versions; no customer/supplier/debt/commitment completeness is claimed."]}
        for name, value in (("coverage.json", result), ("documents.json", documents), ("filings.json", period_report["filing_metadata"]), ("period-inventory.json", period_report), ("fields.json", fields), ("raw-integrity.json", list(evidence.checked.values()))):
            (out/name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = ["# 日常研究库独立原文覆盖核验", "", f"范围：{scope['start']} 至 {scope['end']}（申报公开日期）；股票池来自配置。", "",
            f"申报参考 {len(filings)}；有结构化申报记录 {result['filings']['metadata_record_present']}。",
            f"文件参考 {len(documents)}；已发现 {result['documents']['discovered']}；原文哈希核验保存 {result['documents']['original_saved_hash_verified']}。",
            f"保存来源快照的参考分母是否完整：{result['reference_denominator_complete_for_saved_source_snapshot']}。缺失目录、历史分页和身份未知必须查看 coverage.json。",
            "", "|公司 ID|参考文件|原文保存|解析状态（单独统计）|", "|---|---:|---:|---|"]
        for company, item in by_company.items():
            lines.append(f"|{company}|{item['reference_denominator']}|{item['original_saved_hash_verified']}|{encode(item['parse_states'])}|")
        lines += ["", "财报期间与数字上下文期间分开统计：", "", "|公司 ID|参考中最新定期报告期|有原文的最新定期报告期|全部财务上下文最大结束日（不是最新财报期）|", "|---|---|---|---|"]
        for company, item in period_report["by_company"].items():
            report_dates, contexts = item["periodic_reports"], item["all_stored_financial_fact_context_periods"]
            lines.append(f"|{company}|{report_dates['latest_source_declared_report_date_in_reference']}|{report_dates['latest_source_declared_report_date_with_saved_primary']}|{contexts['maximum_context_period_end']}|")
        lines += ["", "定期报告仅指 10-K/10-Q/20-F 及更正，期间来自原始 SEC reportDate；仍须分别检查原文、解析和字段质量。其他表单的 reportDate 不自动解释为财报期。逐申报 form/reportDate/filingDate/acceptance、生产 period_end 合同比对以及所有财务上下文期间见 filings.json 与 period-inventory.json。"]
        lines += ["", f"原文先行数值抽样：{len(fields)} 项；结果 {encode(result['field_extraction']['outcomes'])}。", "",
            "深层语义没有独立人工参考分母；仅列候选/事实/统一指标库存，不计算完成率。", "",
            "reference.json 在读取生产 records/machine_inventory 用于比较前写出；所有结果来自同一只读 SQLite 快照，无请求、无采集、无数据库写入。", "",
            "运行不会证明最新上游版本或历史可见性完整。完整限制及逐项证据见 JSON。"]
        (out/"coverage.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        return {"output": str(out), "documents": result["documents"], "field_extraction": result["field_extraction"], "reference_complete": result["reference_denominator_complete_for_saved_source_snapshot"]}
    finally:
        db.rollback(); db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/live.toml")
    parser.add_argument("--storage")
    parser.add_argument("--out", required=True)
    parser.add_argument("--per-document", type=int, default=10)
    parser.add_argument("--max-documents-per-company", type=int, default=4)
    args = parser.parse_args()
    if args.per_document < 0 or args.max_documents_per_company < 0:
        parser.error("Sample limits must be nonnegative; zero explicitly skips numeric auditing")
    print(json.dumps(run(args.config, args.out, args.storage, args.per_document, args.max_documents_per_company), indent=2))
