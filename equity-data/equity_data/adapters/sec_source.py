"""Issuer-independent SEC acquisition graph, without document/sample caps."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

from lxml import etree, html

from .base import BaseAdapter, DependencyMissing
from ..sec import _columns_to_rows, _document_url, _duration, _filing_base, _header_metadata, _in_range, _time_evidence
from ..universe import (DIRECTORY_URL, directory_rows, normalize_cik, persist_listing,
                        sec_headers, security_identity)
from ..store import _json, now_utc

PARSER_VERSION = "sec-adapter-4.0.0"


DEPENDENCY_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_document_dependencies (
 resource_id TEXT NOT NULL, raw_id TEXT NOT NULL, retrieval_id TEXT NOT NULL,
 company_id TEXT NOT NULL, accession TEXT NOT NULL, references_json TEXT NOT NULL,
 consumed_fingerprint TEXT NOT NULL, supporting_raw_ids_json TEXT NOT NULL,
 missing_json TEXT NOT NULL, state TEXT NOT NULL, parsed_at TEXT NOT NULL,
 PRIMARY KEY(resource_id,raw_id)
);
CREATE INDEX IF NOT EXISTS sec_dependencies_accession
 ON sec_document_dependencies(company_id,accession);
"""


def _document_ids(content):
    """Names are identifiers, never inferred financial or issuer semantics."""
    definitions, references = {"context": set(), "unit": set()}, {"context": set(), "unit": set()}
    try:
        root = etree.fromstring(content, parser=etree.XMLParser(recover=False, resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        try:
            root = html.fromstring(content)
        except (etree.ParserError, etree.XMLSyntaxError, ValueError):
            return definitions, references
    def local(value):
        return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower() if isinstance(value, str) else ""
    for node in root.iter():
        name = local(node.tag)
        attributes = {local(key): value for key, value in node.attrib.items()}
        if name in definitions and attributes.get("id"):
            definitions[name].add(attributes["id"])
        if name in {"nonfraction", "fraction"}:
            for kind in references:
                if attributes.get(kind + "ref"):
                    references[kind].add(attributes[kind + "ref"])
    return definitions, references


def _security(scope):
    row = {**scope.get("security", {}), **scope}
    if not row.get("cik") and str(row.get("company_id", "")).startswith("sec:"):
        row["cik"] = row["company_id"][4:]
    row["cik"] = normalize_cik(row.get("cik"))
    row["company_id"] = "sec:" + row["cik"]
    return row


def _put(ctx, resource, raw, dataset, key, data, *, evidence=None, **kwargs):
    # Hashes are linked via records.raw_id/record_evidence. Inserting a whole
    # companyfacts snapshot hash in each value would falsely revise unchanged
    # facts whenever any other fact was added to that source snapshot.
    if dataset == "financial_facts":
        from ..contracts import annotate_contract, validate_contract
        data = {**data, "concept_qname": str(data["taxonomy"]) + ":" + str(data["concept"]),
                "entity": {"identifier": resource["metadata"]["cik"], "scheme": "https://www.sec.gov/CIK",
                           "scope": "entire_filing_entity"}, "parser_version": PARSER_VERSION}
        observation = {"dataset": dataset, "data": data,
                       **{name: kwargs.get(name) for name in ("period_start", "period_end", "unit", "currency", "source_locator", "quality")}}
        # The direct record/evidence relation resolves exact snapshot bytes. A
        # whole snapshot hash in each payload would falsely revise unchanged
        # facts whenever some unrelated API observation is added or corrected.
        annotate_contract(observation, content_hash_reference=True)
        contract_errors = validate_contract(observation)
        if contract_errors:
            raise ValueError("SEC financial fact contract rejected: " + "; ".join(contract_errors))
    return ctx.store.put_record(dataset, key, data, source="sec_edgar", raw_id=raw["raw_id"],
        company_id=resource.get("company_id"), parser_version=PARSER_VERSION,
        **(evidence or {}), **kwargs)


class SecAdapter(BaseAdapter):
    source_id = "sec_edgar"
    scope_kind = "company"
    data_types = ("company_profiles", "securities", "identity_history", "filings", "documents", "financial_facts",
                  "guidance", "relations", "commitments", "debt_maturities", "risk_disclosures", "operating_metrics", "event_calendar")

    def health(self, ctx):
        try:
            sec_headers(ctx.config)
        except ValueError as exc:
            return {"status": "missing_dependency", "reason": str(exc), "requires": ["SEC_USER_AGENT"],
                    "implemented": list(self.data_types)}
        return {"status": "implemented", "dependency_ready": True, "network_checked": False,
                "implemented": list(self.data_types), "limitations": [
                    "Current identity snapshot; no complete historical security master",
                    "SEC header acceptance is not independently observed public dissemination",
                    "Companyfacts covers standard taxonomies and entity-wide contexts; document parsing is separately assessed"]}

    def discover(self, ctx, scope):
        try:
            sec_headers(ctx.config)
        except ValueError as exc:
            raise DependencyMissing(str(exc)) from exc
        security = _security(scope)
        start, end = str(scope.get("start", ctx.config["start"])), str(scope.get("end", ctx.config["end"]))
        if not _in_range(start, start, end) or start > end:
            raise ValueError("SEC start/end must be ordered ISO dates")
        metadata = {"security": security, "cik": security["cik"], "start": start, "end": end,
                    "data_types": list(scope.get("data_types") or ctx.config.get("data_types") or self.data_types),
                    "refresh": True}
        ctx.emit({"source_id": self.source_id, "resource_key": "submissions:" + security["cik"] + ":" + start + ":" + end,
                  "kind": "submissions", "data_type": "filings", "company_id": security["company_id"],
                  "security_id": None,
                  "url": f"https://data.sec.gov/submissions/CIK{security['cik']}.json", "metadata": metadata})
        return {"enumeration_complete": True, "discovered": 1,
                "scope": "Discovery root queued; expected children enumerated while parsing submissions and headers"}

    def acquire(self, ctx, resource):
        # Accession archives are reused between explicit revision sweeps. A
        # reused original keeps its actual retrieval timestamp; it is not a
        # claim that the source was checked again today. Mutable API snapshots
        # still refresh on every newly scheduled discovery/update.
        archive = resource.get("metadata", {}).get("immutable_archive", False)
        refresh = bool(ctx.config.get("_refresh_archives") or ctx.config.get("revision_refresh")) if archive else resource.get("metadata", {}).get("refresh", True)
        return ctx.client.fetch(resource["url"], source=self.source_id, headers=sec_headers(ctx.config),
                                refresh=refresh)

    def _cache(self, ctx):
        # An adapter can be used against an isolated historical audit DB; never
        # share its support index or cached evidence with the production DB.
        if getattr(self, "_cache_connection", None) is not ctx.store.db:
            self._cache_connection = ctx.store.db
            self._local_cache = {"raws": {}, "ids": {}, "scopes": {}, "notified": {}}
        return self._local_cache

    def _ids(self, ctx, content, raw_id):
        cache = self._cache(ctx)["ids"]
        if raw_id not in cache:
            cache[raw_id] = _document_ids(content)
        return cache[raw_id]

    def _read_saved_raw(self, ctx, raw_id, retrieval_id=None):
        sql = """SELECT b.*,r.retrieval_id,r.retrieved_at,r.url,r.source
            FROM raw_blobs b JOIN raw_retrievals r USING(raw_id)
            WHERE b.raw_id=?"""
        params = [raw_id]
        if retrieval_id is not None:
            sql += " AND r.retrieval_id=?"
            params.append(retrieval_id)
        row = ctx.store.db.execute(sql + " ORDER BY r.retrieved_at DESC LIMIT 1", params).fetchone()
        if row is None:
            raise ValueError("Same-accession support document raw/retrieval is missing")
        raw = dict(row)
        path = Path(raw["path"])
        path = path if path.is_absolute() else ctx.store.root / path
        stat = path.stat()
        signature = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, raw["sha256"])
        cached = self._cache(ctx)["raws"].get(raw_id)
        if cached is None or cached[0] != signature:
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != raw["sha256"]:
                raise ValueError("Same-accession context support SHA-256 mismatch")
            self._cache(ctx)["raws"][raw_id] = (signature, content)
        else:
            content = cached[1]
        raw["path"] = str(path)
        return content, raw

    def _scope_documents(self, ctx, resource):
        """Index only latest acquired document records in this issuer/accession.

        The old implementation re-ran the full point-in-time financial query
        machinery for every rendered report. This index never returns research
        values: it locates exact original bytes and source declarations only.
        """
        accession = resource["metadata"]["filing"]["accessionNumber"]
        scope = (resource["company_id"], accession)
        params = (self.source_id, scope[0], accession + "|%")
        maximum = ctx.store.db.execute("SELECT COALESCE(MAX(sequence),0) FROM records WHERE dataset='documents' AND source=? AND company_id=? AND record_key LIKE ?", params).fetchone()[0]
        cache = self._cache(ctx)["scopes"]
        if scope not in cache or cache[scope][0] != maximum:
            if scope in cache:
                previous, documents = cache[scope]
                # Append-only records let the index consume only document
                # versions added since its prior state. Re-reading every
                # already-known R*.htm row here would still be quadratic.
                rows = ctx.store.db.execute("SELECT * FROM records WHERE sequence>? AND dataset='documents' AND source=? AND company_id=? AND record_key LIKE ? ORDER BY sequence",
                                            (previous, *params)).fetchall()
            else:
                rows = ctx.store.db.execute("""SELECT r.* FROM records r JOIN
                    (SELECT MAX(sequence) AS seq FROM records WHERE dataset='documents' AND source=? AND company_id=? AND record_key LIKE ? GROUP BY record_key) latest
                    ON r.sequence=latest.seq ORDER BY r.sequence""", params).fetchall()
                documents = {}
            for row in rows:
                documents.pop(row["record_key"], None)
                data = json.loads(row["data_json"])
                if data.get("accession") != accession or data.get("document_kind") not in {"primary_filing", "filing_attachment"}:
                    continue
                if PurePosixPath(data.get("filename") or "").suffix.lower() not in {".htm", ".html", ".xhtml", ".xml"}:
                    continue
                content, raw = self._read_saved_raw(ctx, row["raw_id"], row["retrieval_id"])
                definitions, _ = self._ids(ctx, content, row["raw_id"])
                if any(definitions.values()):
                    documents[row["record_key"]] = {"content": content, "raw_id": row["raw_id"], "sha256": raw["sha256"],
                                      "source_url": data.get("url"), "retrieval_id": row["retrieval_id"], "definitions": definitions}
            cache[scope] = (maximum, documents)
        # Integrity remains checked after external file changes even on an
        # in-process index hit; unchanged files are not reread or rehashed.
        for document in cache[scope][1].values():
            self._read_saved_raw(ctx, document["raw_id"], document["retrieval_id"])
        return list(cache[scope][1].values())

    def _effective_metadata(self, ctx, resource):
        metadata = dict(resource["metadata"])
        filing = metadata["filing"]
        saved = ctx.store.db.execute("SELECT data_json,raw_id FROM records WHERE dataset='documents' AND source=? AND record_key=? ORDER BY sequence DESC LIMIT 1",
            (self.source_id, filing["accessionNumber"] + "|index-headers")).fetchone()
        if saved and not ctx.config.get("_historical_reparse_audit"):
            data = json.loads(saved["data_json"])
            metadata.update(header={"accepted_local_clock": data.get("accepted_local_clock"), "documents": data.get("document_manifest", [])}, time_evidence_raw_id=saved["raw_id"])
        if metadata.get("header"):
            metadata["evidence"] = _time_evidence(filing, metadata["header"])
        return metadata

    def parse_input_fingerprint(self, ctx, resource, raw):
        """Stable parse inputs, not a promise that the upstream copy is current.

        Enumeration nodes deliberately opt out so a new generation still emits
        the complete child inventory. Engine persists results for exact inputs.
        """
        if resource["kind"] not in {"primary_filing", "filing_attachment", "companyfacts"}:
            return None
        from ..parsers import PARSER_VERSION as document_version
        from ..contracts import CONTRACT_VERSION
        cache = self._cache(ctx)
        if "code_hashes" not in cache:
            package = Path(__file__).resolve().parents[1]
            cache["code_hashes"] = {str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (Path(__file__), Path(__file__).with_name("base.py"), package / "parsers.py", package / "contracts.py")}
        meta = resource.get("metadata", {})
        inputs = {"adapter_version": PARSER_VERSION, "document_version": document_version,
                  "contract_version": CONTRACT_VERSION, "code_hashes": cache["code_hashes"], "raw_id": raw["raw_id"],
                  "kind": resource["kind"], "company_id": resource.get("company_id"), "security_id": resource.get("security_id"), "url": resource["url"]}
        if resource["kind"] == "companyfacts":
            headers = ctx.store.db.execute("""SELECT record_key,raw_id FROM records WHERE sequence IN
                (SELECT MAX(sequence) FROM records WHERE dataset='documents' AND source=? AND company_id=? AND record_key LIKE '%|index-headers' GROUP BY record_key)
                ORDER BY record_key""", (self.source_id, resource["company_id"])).fetchall()
            inputs.update(metadata={key: meta.get(key) for key in ("cik", "start", "end")}, headers=[list(row) for row in headers])
        else:
            meta = self._effective_metadata(ctx, resource)
            inputs["metadata"] = {key: meta.get(key) for key in ("filename", "document_type", "filing", "evidence", "time_evidence_raw_id")}
            content, _ = self._read_saved_raw(ctx, raw["raw_id"], raw["retrieval_id"])
            inputs["document_set"] = self._dependency_snapshot(ctx, {**resource, "metadata": meta}, content, raw)["fingerprint"]
        return hashlib.sha256(_json(inputs).encode()).hexdigest()

    def parse(self, ctx, resource, content, raw):
        kind = resource["kind"]
        if kind in ("submissions", "submissions_archive"):
            return self._submissions(ctx, resource, json.loads(content), raw)
        if kind == "filing_index":
            return self._index(ctx, resource, content, raw)
        if kind == "companyfacts":
            return self._facts(ctx, resource, json.loads(content), raw)
        if kind in ("primary_filing", "filing_attachment"):
            return self._document(ctx, resource, content, raw)
        raise ValueError("Unsupported SEC resource kind: " + str(kind))

    def _child(self, ctx, parent, *, key, kind, data_type, url, metadata=None):
        return ctx.emit({"source_id": self.source_id, "resource_key": key, "kind": kind, "data_type": data_type,
                         "url": url, "company_id": parent.get("company_id"), "security_id": parent.get("security_id"),
                         "metadata": {**parent.get("metadata", {}), **(metadata or {})}})

    def _identity(self, ctx, resource, payload, raw):
        company_id = resource["company_id"]
        ids = [_put(ctx, resource, raw, "company_profiles", company_id, {
            "cik": resource["metadata"]["cik"], "name": payload.get("name"), "sic": payload.get("sic"),
            "sic_description": payload.get("sicDescription"), "entity_type": payload.get("entityType"),
            "fiscal_year_end": payload.get("fiscalYearEnd"), "website": payload.get("website"),
            "investor_website": payload.get("investorWebsite"), "current_tickers": payload.get("tickers", []),
            "current_exchanges": payload.get("exchanges", []), "snapshot_only": True, "history_complete": False,
            "evidence_excerpt": json.dumps({k: payload.get(k) for k in ("cik", "name", "sic", "website", "investorWebsite", "tickers", "exchanges")}, sort_keys=True),
        }, source_locator="/cik,/name,/sic,/website,/investorWebsite,/tickers,/exchanges", quality="unverified",
           evidence={"availability_basis": "unknown_current_submissions_profile_snapshot"})]
        configured = resource["metadata"].get("security", {})
        for index, symbol in enumerate(payload.get("tickers") or []):
            exchanges = payload.get("exchanges") or []
            row = security_identity({"cik": resource["metadata"]["cik"], "symbol": symbol,
                "name": payload.get("name"), "exchange": exchanges[index] if index < len(exchanges) else None,
                **({k: configured[k] for k in ("security_id", "share_class_id", "currency", "security_type") if configured.get(k)}
                   if symbol == configured.get("symbol") else {}),
                "source_row": {"ticker": symbol, "exchange": exchanges[index] if index < len(exchanges) else None}})
            record_id = persist_listing(ctx.store, row, raw, source=self.source_id, locator=f"/tickers/{index}; /exchanges/{index}")
            if record_id:
                ids.append(record_id)
        for index, former in enumerate(payload.get("formerNames") or []):
            key = company_id + "|former_name|" + json.dumps(former, sort_keys=True)
            ids.append(_put(ctx, resource, raw, "identity_history", key, {
                "identity_type": "company_name", "name": former.get("name"),
                "valid_from_reported": former.get("from"), "valid_to_reported": former.get("to"),
                "original_publication_unknown": True, "evidence_excerpt": json.dumps(former, sort_keys=True),
            }, source_locator=f"/formerNames/{index}", quality="unverified"))
        # Company-provided websites are evidenced candidates, not claimed IR success.
        for field in ("investorWebsite", "website"):
            url = payload.get(field)
            if url:
                ids.append(_put(ctx, resource, raw, "source_candidates", company_id + "|" + field, {
                    "url": url, "candidate_type": "official_ir" if field == "investorWebsite" else "official_company_website",
                    "verified_official_by": "SEC submissions filer profile", "current_snapshot_only": True,
                    "evidence_excerpt": str(url),
                }, source_locator="/" + field, quality="unverified"))
        return ids

    def _submissions(self, ctx, resource, payload, raw):
        if resource["kind"] == "submissions" and not isinstance(payload.get("filings"), dict):
            raise ValueError("SEC submissions missing filings object")
        metadata = resource["metadata"]
        start, end, cik = metadata["start"], metadata["end"], metadata["cik"]
        ids, emitted, errors = [], 0, []
        requested = set(metadata.get("data_types") or self.data_types)
        if resource["kind"] == "submissions":
            if payload.get("cik") is not None and normalize_cik(payload["cik"]) != cik:
                raise ValueError("SEC response CIK differs from requested issuer")
            ids.extend(self._identity(ctx, resource, payload, raw))
            for archive in payload.get("filings", {}).get("files", []):
                if (archive.get("filingTo") and archive["filingTo"] < start) or (archive.get("filingFrom") and archive["filingFrom"] > end):
                    continue
                name = str(archive.get("name", ""))
                if not re.fullmatch(r"CIK" + cik + r"-submissions-\d+\.json", name):
                    errors.append({"code": "SEC_ARCHIVE_NAME_INVALID", "name": name})
                    ctx.gap("SEC_ARCHIVE_NAME_INVALID", "Cannot safely enumerate archive filename", source_id=self.source_id,
                            company_id=resource["company_id"], data_type="filings", details=archive)
                    continue
                self._child(ctx, resource, key="archive:" + name + ":" + start + ":" + end,
                    kind="submissions_archive", data_type="filings", url="https://data.sec.gov/submissions/" + name,
                    metadata={"discovery_raw_id": raw["raw_id"], "discovery_locator": "/filings/files", "archive": archive})
                emitted += 1
            if "financial_facts" in requested:
                self._child(ctx, resource, key="companyfacts:" + cik + ":" + start + ":" + end,
                    kind="companyfacts", data_type="financial_facts", url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
                emitted += 1
        elif not isinstance(payload.get("accessionNumber"), list) and not isinstance(payload.get("recent"), dict):
            raise ValueError("SEC archive missing accessionNumber column")
        forms = set(ctx.config.get("sec", {}).get("forms") or [])
        document_types = {"filings", "documents", "guidance", "relations", "commitments", "debt_maturities", "risk_disclosures", "operating_metrics"}
        seen = set()
        for index, filing in enumerate(_columns_to_rows(payload)):
            if not _in_range(filing.get("filingDate"), start, end) or (forms and filing.get("form") not in forms):
                continue
            accession = filing.get("accessionNumber")
            try:
                base = _filing_base(cik, accession or "")
            except ValueError:
                errors.append({"code": "SEC_ACCESSION_INVALID", "accession": accession})
                ctx.gap("SEC_ACCESSION_INVALID", "In-range filing has invalid accession", source_id=self.source_id,
                        company_id=resource["company_id"], data_type="filings", details=filing)
                continue
            if accession in seen:
                continue
            seen.add(accession)
            evidence = _time_evidence(filing)
            locator = "column arrays where accessionNumber=" + accession
            ids.append(_put(ctx, resource, raw, "filings", accession, {
                "accession": accession, "form": filing.get("form"), "filing_date": filing.get("filingDate"),
                "report_date": filing.get("reportDate"), "acceptance_datetime_raw": filing.get("acceptanceDateTime"),
                "primary_document": filing.get("primaryDocument"), "items": filing.get("items"),
                "is_amendment": str(filing.get("form", "")).endswith("/A"), "amends_accession": None,
                "amendment_relation": "form_detected; original_accession_not_inferred",
                "evidence_excerpt": json.dumps(filing, sort_keys=True),
                "document_status_basis": "Consult resource inventory; metadata never implies original downloaded",
            }, evidence=evidence, source_locator=locator, event_date=filing.get("filingDate"),
               period_end=filing.get("reportDate") or None, quality="unverified"))
            ids.append(_put(ctx, resource, raw, "event_calendar", "sec_filing:" + accession, {
                "event_type": "actual_sec_filing", "event_date": filing.get("filingDate"), "status": "actual",
                "form": filing.get("form"), "accession": accession, "is_earnings_announcement_date": False,
                "evidence_excerpt": json.dumps(filing, sort_keys=True),
            }, evidence=evidence, source_locator=locator, event_date=filing.get("filingDate"), quality="unverified"))
            if requested & document_types:
                child_metadata = {"filing": filing, "accession": accession, "evidence": evidence, "immutable_archive": True,
                                  "discovery_raw_id": raw["raw_id"], "discovery_locator": locator}
                self._child(ctx, resource, key="header:" + accession, kind="filing_index", data_type="documents",
                    url=base + accession + "-index-headers.html", metadata=child_metadata)
                emitted += 1
                primary = filing.get("primaryDocument")
                if primary:
                    try:
                        self._child(ctx, resource, key="document:" + accession + ":" + primary, kind="primary_filing", data_type="documents",
                            url=_document_url(base, primary), metadata={**child_metadata, "filename": primary, "document_type": filing.get("form")})
                        emitted += 1
                    except ValueError as exc:
                        errors.append({"code": "SEC_PRIMARY_PATH_INVALID", "accession": accession})
                        ctx.gap("SEC_PRIMARY_PATH_INVALID", str(exc), source_id=self.source_id,
                            company_id=resource["company_id"], data_type="documents", details={"filing": filing})
                else:
                    # Header may recover this later. Missing is not silently omitted.
                    ctx.gap("SEC_PRIMARY_FILENAME_MISSING", "Submissions omits primaryDocument; header inventory must resolve it",
                        source_id=self.source_id, company_id=resource["company_id"], data_type="documents", details={"accession": accession})
        return {"status": "partial" if errors else "parsed", "record_ids": ids, "parsed_count": len(ids), "errors": errors,
                "coverage": {"filings_enumerated": len(seen), "child_resources_emitted": emitted,
                             "enumeration_complete": not errors, "forms": sorted(forms) if forms else "all",
                             "date_filter": "filing disclosure date", "historical_pool_complete": False}}

    def _index(self, ctx, resource, content, raw):
        header = _header_metadata(content)
        metadata = resource["metadata"]
        filing = metadata["filing"]
        accession = filing["accessionNumber"]
        evidence = _time_evidence(filing, header)
        initial_evidence = _time_evidence(filing)
        if (evidence.get("available_at") and initial_evidence.get("available_at")
                and evidence["available_at"] > initial_evidence["available_at"]):
            # Headers can arrive after companyfacts. Append a time constraint so
            # prior file-date assumptions are no longer eligible too early.
            for fact in ctx.store.query("financial_facts", company_id=resource["company_id"], latest=False):
                if fact["source"] == self.source_id and fact["data"].get("accession") == accession:
                    ctx.store.restrict_availability("financial_facts", fact["record_key"], source=self.source_id,
                        not_before=evidence["available_at"], raw_id=raw["raw_id"],
                        basis="SEC header acceptance day proves filed-day-only assumption was too early; next-day NY floor")
        ids = [_put(ctx, resource, raw, "documents", accession + "|index-headers", {
            "document_kind": "filing_header_index", "accession": accession, "form": filing.get("form"),
            "document_manifest": header["documents"], "accepted_local_clock": header.get("accepted_local_clock"),
            "publication_time_kind": "SEC acceptance; not independently observed public dissemination",
            "full_text_preserved": True, "evidence_excerpt": json.dumps(header, sort_keys=True),
        }, evidence=evidence, source_locator="<ACCEPTANCE-DATETIME>; <DOCUMENT>", quality="unverified")]
        errors = []
        if not header["documents"]:
            errors.append({"code": "SEC_HEADER_MANIFEST_MISSING"})
            ctx.gap("SEC_HEADER_MANIFEST_MISSING", "Header has no usable document manifest; attachment completeness unknown",
                    source_id=self.source_id, company_id=resource["company_id"], data_type="documents", details={"accession": accession})
        # Default all EX-* textual/binary attachments; every object remains a task.
        # User may narrow by document type, never by a silent per-company limit.
        allowed = ctx.config.get("sec", {}).get("attachment_types", ["EX-*"])
        # SEC may label an extracted XBRL instance XML rather than EX-101.INS.
        # These manifest-declared files can carry same-accession context/unit
        # declarations. Taxonomy schemas/linkbases are not fetched by guessing.
        support_types = ctx.config.get("sec", {}).get("support_document_types", ["XML", "EX-101.INS"])
        import fnmatch
        primary = filing.get("primaryDocument")
        if not primary:
            match = next((doc for doc in header["documents"] if doc.get("type") == filing.get("form")), None)
            primary = match.get("filename") if match else None
        if not primary:
            errors.append({"code": "SEC_PRIMARY_FILENAME_UNRESOLVED"})
            ctx.gap("SEC_PRIMARY_FILENAME_UNRESOLVED", "Neither submissions nor header identifies primary document",
                    source_id=self.source_id, company_id=resource["company_id"], data_type="documents", details={"accession": accession})
        documents = list(header["documents"])
        if primary and not any(doc["filename"] == primary for doc in documents):
            documents.insert(0, {"filename": primary, "type": filing.get("form")})
        emitted = 0
        excluded = []
        for document in documents:
            filename = document["filename"]
            # A submissions primaryDocument can be an XSL-rendered path while
            # the SGML manifest identifies a separate original XML with TYPE
            # equal to the filing form. Keep every such declared original;
            # presentation URLs never substitute for the original bytes.
            is_form_original = document in header["documents"] and bool(filing.get("form")) and document.get("type") == filing.get("form")
            is_primary = filename == primary or is_form_original
            is_support = any(fnmatch.fnmatchcase(str(document.get("type") or ""), pattern) for pattern in support_types)
            if not is_primary and not is_support and not any(fnmatch.fnmatchcase(str(document.get("type") or ""), pattern) for pattern in allowed):
                excluded.append({"filename": filename, "type": document.get("type"), "reason": "outside_configured_attachment_types"})
                continue
            try:
                url = _document_url(_filing_base(metadata["cik"], accession), filename)
            except ValueError as exc:
                errors.append({"code": "SEC_ATTACHMENT_PATH_INVALID", "filename": filename})
                ctx.gap("SEC_ATTACHMENT_PATH_INVALID", str(exc), source_id=self.source_id, company_id=resource["company_id"],
                        data_type="documents", details={"accession": accession, "document": document})
                continue
            self._child(ctx, resource, key="document:" + accession + ":" + filename,
                kind="primary_filing" if is_primary else "filing_attachment", data_type="documents", url=url,
                metadata={"filename": filename, "document_type": document.get("type"), "description": document.get("description"),
                          "document_set_support_candidate": is_support,
                          "manifest_form_original": is_form_original,
                          "primary_representation": "manifest_declared_form_document" if is_form_original else "submissions_primary_presentation_or_original" if filename == primary else "manifest_attachment",
                          "evidence": evidence, "time_evidence_raw_id": raw["raw_id"], "header": header,
                          "discovery_raw_id": raw["raw_id"], "discovery_locator": "<DOCUMENT><FILENAME>" + filename})
            emitted += 1
        return {"status": "partial" if errors else "parsed", "record_ids": ids, "parsed_count": len(ids), "errors": errors,
                "coverage": {"manifest_items": len(header["documents"]), "expected_documents": emitted,
                             "excluded_by_type": excluded, "attachment_types": allowed, "support_document_types": support_types,
                             "enumeration_complete": not errors}}

    def _document(self, ctx, resource, content, raw):
        from ..parsers import parse_document
        from .base import persist_observations
        metadata = self._effective_metadata(ctx, resource)
        filing = metadata["filing"]
        resource = {**resource, "metadata": metadata}
        evidence = metadata.get("evidence") or _time_evidence(filing)
        key = filing["accessionNumber"] + "|" + metadata["filename"]
        record_id = _put(ctx, resource, raw, "documents", key, {
            "document_kind": resource["kind"], "accession": filing["accessionNumber"], "form": filing.get("form"),
            "filename": metadata["filename"], "document_type": metadata.get("document_type"), "url": resource["url"],
            "full_text_preserved": True, "time_evidence_raw_id": metadata.get("time_evidence_raw_id"),
            "resource_key": resource["resource_key"], "content_sha256": raw["sha256"],
        }, evidence=evidence, period_end=filing.get("reportDate") or None, source_locator="whole original document", quality="unverified")
        suffix = PurePosixPath(metadata["filename"]).suffix.lower()
        media_type = {".pdf": "application/pdf", ".htm": "text/html", ".html": "text/html", ".xhtml": "text/html", ".xml": "application/xml",
                      ".txt": "text/plain"}.get(suffix, "application/octet-stream")
        if media_type == "application/octet-stream":
            ctx.gap("SEC_ATTACHMENT_PARSER_UNSUPPORTED", "Original attachment retained; parser does not support this media type",
                    source_id=self.source_id, company_id=resource["company_id"], data_type="documents",
                    details={"filename": metadata["filename"], "resource_key": resource["resource_key"]})
            return {"status": "partial", "record_ids": [record_id], "parsed_count": 1,
                    "errors": [{"code": "SEC_ATTACHMENT_PARSER_UNSUPPORTED"}], "coverage": {"original_preserved": True}}
        context_documents, support_retrievals = self._context_documents(ctx, resource, content, raw)
        kwargs = {"context_documents": context_documents} if context_documents else {}
        parsed = parse_document(content, media_type=media_type, document_type=metadata.get("document_type"),
                                company_id=resource["company_id"], source_url=resource["url"], **kwargs)
        dependency = self._register_dependencies(ctx, resource, content, raw)
        if dependency and any(dependency["missing"].values()):
            issue = {"code": "SEC_DOCUMENT_SUPPORT_MISSING", "message": "Inline facts reference context/unit declarations not present in the acquired same-accession document set",
                     "data_type": "financial_facts", "missing": dependency["missing"], "accession": filing["accessionNumber"]}
            parsed.setdefault("issues", []).append(issue)
        elif dependency:
            ctx.queue.resolve_gaps(source_id=self.source_id, code="SEC_DOCUMENT_SUPPORT_MISSING", company_id=resource["company_id"],
                                   details_match={"resource_key": resource["resource_key"], "raw_id": raw["raw_id"]})
        result = persist_observations(ctx, resource, parsed, raw)
        if not any(issue.get("code") == "IXBRL_NUMERIC_REJECTED" for issue in parsed.get("issues", [])):
            # Close only the exact-original issue after successful reconsideration.
            # The former failure stays in machine_gaps with its resolved stamp.
            ctx.queue.resolve_gaps(source_id=self.source_id, code="IXBRL_NUMERIC_REJECTED", company_id=resource["company_id"],
                                   details_match={"resource_key": resource["resource_key"], "raw_id": raw["raw_id"]})
        if result is None:
            result = {}
        if isinstance(result, list):
            result = {"record_ids": result, "parsed_count": len(result)}
        # Each fact names only the support documents it actually used. Preserve
        # those raw/retrieval edges rather than attaching every filing file.
        record_ids = result.get("record_ids", [])
        edges = []
        for offset in range(0, len(record_ids), 500):
            selected = record_ids[offset:offset + 500]
            rows = ctx.store.db.execute("SELECT record_id,data_json FROM records WHERE record_id IN (" + ",".join("?" for _ in selected) + ")", selected)
            for record in rows:
                for supporting_raw_id in json.loads(record["data_json"]).get("supporting_raw_ids", []):
                    if supporting_raw_id in support_retrievals:
                        edges.append((record["record_id"], support_retrievals[supporting_raw_id], supporting_raw_id))
        if edges:
            ctx.store.db.executemany("INSERT OR IGNORE INTO record_evidence(record_id,retrieval_id,raw_id) VALUES(?,?,?)", edges)
            ctx.store.db.commit()
        return {**result, "record_ids": [record_id] + result.get("record_ids", []),
                "parsed_count": 1 + result.get("parsed_count", len(parsed.get("observations", []))),
                "status": result.get("status", "partial" if parsed.get("issues") else "parsed"),
                "coverage": {**parsed.get("coverage", {}), **result.get("coverage", {}), "original_preserved": True,
                             "document_set_dependency": dependency}}

    def _context_documents(self, ctx, resource, content, raw):
        """Local-only Inline XBRL Document Set support, confined to accession.

        SEC filings may split numeric facts and context/unit declarations over
        primary and exhibit HTML files. The parser receives exact separate
        bytes, never a fabricated merged source or an implicit remote download.
        """
        if PurePosixPath(resource["metadata"]["filename"]).suffix.lower() not in (".htm", ".html", ".xhtml", ".xml"):
            return [], {}
        _, references = self._ids(ctx, content, raw["raw_id"])
        if not any(references.values()):
            return [], {}
        documents, retrievals, seen = [], {}, {raw["raw_id"]}
        for document in self._scope_documents(ctx, resource):
            if document["raw_id"] in seen:
                continue
            seen.add(document["raw_id"])
            if not any(document["definitions"][kind] & references[kind] for kind in references):
                continue
            documents.append({key: document[key] for key in ("content", "raw_id", "sha256", "source_url")})
            retrievals[document["raw_id"]] = document["retrieval_id"]
        return documents, retrievals

    def _dependency_snapshot(self, ctx, resource, content, raw):
        """Content-addressed document-set state; no HTTP and no cross-filing joins."""
        definitions, references = self._ids(ctx, content, raw["raw_id"])
        supporting, _ = self._context_documents(ctx, resource, content, raw)
        available = {kind: set(values) for kind, values in definitions.items()}
        for document in supporting:
            support_definitions, _ = self._ids(ctx, document["content"], document["raw_id"])
            for kind in available:
                available[kind].update(support_definitions[kind])
        refs = {kind: sorted(values) for kind, values in references.items()}
        missing = {kind: sorted(references[kind] - available[kind]) for kind in references}
        raw_ids = sorted(document["raw_id"] for document in supporting)
        fingerprint = hashlib.sha256(_json({"dependent_raw_id": raw["raw_id"], "references": refs,
                                            "supporting_raw_ids": raw_ids}).encode()).hexdigest()
        return {"references": refs, "missing": missing, "supporting_raw_ids": raw_ids,
                "fingerprint": fingerprint, "state": "missing_support" if any(missing.values()) else "declarations_available",
                "scope": "same company and accession; availability does not prove numeric validation"}

    def _register_dependencies(self, ctx, resource, content, raw):
        if PurePosixPath(resource["metadata"]["filename"]).suffix.lower() not in (".htm", ".html", ".xhtml", ".xml"):
            return None
        ctx.store.db.executescript(DEPENDENCY_SCHEMA)
        snapshot = self._dependency_snapshot(ctx, resource, content, raw)
        if not any(snapshot["references"].values()):
            return None
        resource_id = resource.get("resource_id")
        if not resource_id:
            row = ctx.store.db.execute("SELECT resource_id FROM machine_inventory WHERE source_id=? AND resource_key=?",
                                       (self.source_id, resource["resource_key"])).fetchone()
            if not row:
                return snapshot
            resource_id = row["resource_id"]
        ctx.store.db.execute("""INSERT INTO sec_document_dependencies VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(resource_id,raw_id) DO UPDATE SET retrieval_id=excluded.retrieval_id,
            references_json=excluded.references_json,consumed_fingerprint=excluded.consumed_fingerprint,
            supporting_raw_ids_json=excluded.supporting_raw_ids_json,missing_json=excluded.missing_json,
            state=excluded.state,parsed_at=excluded.parsed_at""",
            (resource_id, raw["raw_id"], raw["retrieval_id"], resource["company_id"], resource["metadata"]["filing"]["accessionNumber"],
             _json(snapshot["references"]), snapshot["fingerprint"], _json(snapshot["supporting_raw_ids"]),
             _json(snapshot["missing"]), snapshot["state"], now_utc()))
        ctx.store.db.commit()
        return snapshot

    def after_resource_parsed(self, ctx, resource, raw, result):
        """Wake exact saved dependent raws only when their support set changes.

        The engine calls this after committing the source task. Dependencies
        survive restart and each dependency fingerprint maps to a stable task.
        A reparse with unchanged support cannot recursively generate new work.
        """
        if resource["kind"] not in {"primary_filing", "filing_attachment"}:
            return
        ctx.store.db.executescript(DEPENDENCY_SCHEMA)
        # A new HTTP retrieval of identical bytes can reuse a parse. Track its
        # current checkpoint without pretending parsing happened again; future
        # support-triggered work must target the current retrieval, not become
        # an isolated historical audit of an earlier identical copy.
        current = ctx.store.db.execute("SELECT resource_id,raw_id,retrieval_id FROM machine_inventory WHERE source_id=? AND resource_key=?",
                                       (self.source_id, resource["resource_key"])).fetchone()
        if current and (current["raw_id"], current["retrieval_id"]) == (raw["raw_id"], raw["retrieval_id"]):
            ctx.store.db.execute("UPDATE sec_document_dependencies SET retrieval_id=? WHERE resource_id=? AND raw_id=?",
                                 (raw["retrieval_id"], current["resource_id"], raw["raw_id"]))
            ctx.store.db.commit()
        accession = resource["metadata"]["filing"]["accessionNumber"]
        scope = (resource["company_id"], accession)
        supporting = self._scope_documents(ctx, resource)
        support_state = hashlib.sha256(_json(sorted({document["raw_id"] for document in supporting})).encode()).hexdigest()
        notified = self._cache(ctx)["notified"]
        if notified.get(scope) == support_state:
            return
        dependencies = ctx.store.db.execute("""SELECT d.* FROM sec_document_dependencies d
            JOIN machine_inventory i ON i.resource_id=d.resource_id AND i.raw_id=d.raw_id
            WHERE d.company_id=? AND d.accession=?""", (resource["company_id"], accession)).fetchall()
        for dependency in dependencies:
            dependent_resource = ctx.queue.resource(dependency["resource_id"])
            content, dependent_raw = self._read_saved_raw(ctx, dependency["raw_id"], dependency["retrieval_id"])
            snapshot = self._dependency_snapshot(ctx, dependent_resource, content, dependent_raw)
            if snapshot["fingerprint"] == dependency["consumed_fingerprint"]:
                continue
            # Include the last consumed parse epoch: support can legitimately
            # change A -> B -> A. The return to A must not reuse a succeeded
            # task from the first A, while repeated notifications before the
            # pending parse still coalesce into one durable task.
            wake_epoch = hashlib.sha256(_json([snapshot["fingerprint"], dependency["consumed_fingerprint"],
                                               dependency["parsed_at"]]).encode()).hexdigest()
            ctx.queue.enqueue_reprocess(dependent_resource, "sec-support:" + wake_epoch,
                                        raw_id=dependency["raw_id"], retrieval_id=dependency["retrieval_id"])
        notified[scope] = support_state

    def _facts(self, ctx, resource, payload, raw):
        if not isinstance(payload.get("facts"), dict):
            raise ValueError("SEC companyfacts missing facts object")
        metadata = resource["metadata"]
        if payload.get("cik") is not None and normalize_cik(payload["cik"]) != metadata["cik"]:
            raise ValueError("SEC companyfacts CIK differs from requested issuer")
        ids, errors = [], []
        header_evidence = {}
        for record in ctx.store.query("documents", company_id=resource["company_id"]):
            document = record["data"]
            if record["source"] == self.source_id and document.get("document_kind") == "filing_header_index":
                header_evidence[document.get("accession")] = {"accepted_local_clock": document.get("accepted_local_clock"), "raw_id": record["raw_id"]}
        for taxonomy, concepts in payload["facts"].items():
            for concept, definition in concepts.items():
                for unit, observations in definition.get("units", {}).items():
                    for fact in observations:
                        if not _in_range(fact.get("filed"), metadata["start"], metadata["end"]):
                            continue
                        accession = fact.get("accn")
                        value = fact.get("val")
                        if (not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(accession)) or isinstance(value, bool)
                                or not isinstance(value, (int, float)) or not math.isfinite(value)):
                            errors.append({"code": "SEC_FACT_INVALID", "concept": concept, "accession": accession})
                            continue
                        context = {k: fact.get(k) for k in ("accn", "start", "end", "fy", "fp", "frame")}
                        key = hashlib.sha256(json.dumps([resource["company_id"], taxonomy, concept, unit, context], sort_keys=True).encode()).hexdigest()
                        currency = unit.split("/")[0] if re.fullmatch(r"[A-Z]{3}(?:/.*)?", unit) else None
                        locator = "/facts/" + taxonomy + "/" + concept + "/units/" + unit.replace("~", "~0").replace("/", "~1") + "; context=" + json.dumps(context, sort_keys=True)
                        header = header_evidence.get(accession)
                        evidence = _time_evidence({"filed": fact.get("filed"), "form": fact.get("form")}, header)
                        # Preserve filed-day label for API facts. Header evidence
                        # only tightens the lower bound, never turns acceptance
                        # into a claim of verified intraday public availability.
                        if evidence["available_at"]:
                            evidence.update(published_at=fact.get("filed"), publication_precision="day", publication_timezone="America/New_York")
                            evidence["availability_basis"] = "conservative_next_day_filing_day_or_later_known_header_acceptance_unverified_intraday"
                        filed_evidence = _time_evidence({"filed": fact.get("filed"), "form": fact.get("form")})
                        if (header and evidence.get("available_at") and filed_evidence.get("available_at")
                                and evidence["available_at"] > filed_evidence["available_at"]):
                            ctx.store.restrict_availability("financial_facts", key, source=self.source_id,
                                not_before=evidence["available_at"], raw_id=header["raw_id"],
                                basis="Known SEC header acceptance day tightens conservative public-availability floor")
                        ids.append(_put(ctx, resource, raw, "financial_facts", key, {
                            "taxonomy": taxonomy, "concept": concept, "original_name": taxonomy + ":" + concept,
                            "label": definition.get("label"), "description": definition.get("description"),
                            "value": value, "unit": unit, "currency": currency, "scale": 1,
                            "scale_basis": "SEC API indicated units; no display-scale multiplication",
                            "accession": accession, "form": fact.get("form"), "filed": fact.get("filed"),
                            "start": fact.get("start"), "end": fact.get("end"), "fy": fact.get("fy"), "fp": fact.get("fp"), "frame": fact.get("frame"),
                            "period_type": "duration" if fact.get("start") else "instant", "duration_days": _duration(fact.get("start"), fact.get("end")),
                            "quarter_or_cumulative": "not_inferred; use exact start/end", "consolidation_scope": "entire_filing_entity", "dimensions": {},
                            "accounting_basis": "US-GAAP" if taxonomy == "us-gaap" else "IFRS" if taxonomy == "ifrs-full" else "taxonomy_specific",
                            "is_amendment": str(fact.get("form", "")).endswith("/A"), "amends_accession": None,
                            "source_fact": fact, "evidence_excerpt": json.dumps(fact, sort_keys=True),
                        }, evidence=evidence, period_start=fact.get("start"), period_end=fact.get("end"),
                            source_locator=locator, unit=unit, currency=currency, quality="unverified"))
        if errors:
            ctx.gap("SEC_FACT_INVALID", "Some facts failed accession/value validation and were rejected", source_id=self.source_id,
                    company_id=resource["company_id"], data_type="financial_facts", details={"rejected_count": len(errors)})
        return {"status": "partial" if errors else "parsed", "record_ids": ids, "parsed_count": len(ids), "errors": errors,
                "coverage": {"facts": len(ids), "rejected": len(errors), "custom_taxonomies": False, "dimensional_facts": False,
                             "availability": "conservative_next_day_filing_date; intraday dissemination unverified"}}


class SecDirectoryAdapter(BaseAdapter):
    source_id = "sec_directory"
    scope_kind = "global"
    data_types = ("securities", "company_profiles")

    def health(self, ctx):
        try:
            sec_headers(ctx.config)
        except ValueError as exc:
            return {"status": "missing_dependency", "reason": str(exc)}
        return {"status": "implemented", "network_checked": False, "snapshot_only": True}

    def discover(self, ctx, scope):
        ctx.emit({"source_id": self.source_id, "resource_key": "current-directory", "kind": "directory",
                  "data_type": "securities", "url": DIRECTORY_URL, "metadata": {"refresh": True}})
        return {"enumeration_complete": True, "discovered": 1}

    def acquire(self, ctx, resource):
        return ctx.client.fetch(resource["url"], source=self.source_id, headers=sec_headers(ctx.config), refresh=True)

    def parse(self, ctx, resource, content, raw):
        rows = directory_rows(content)
        ids = [persist_listing(ctx.store, security_identity(row), raw) for row in rows]
        ctx.gap("SEC_DIRECTORY_HISTORICAL_POOL_UNSUPPORTED", "SEC directory lists current symbols only, without complete delisted or share-class history",
                source_id=self.source_id, data_type="securities")
        return {"status": "parsed", "record_ids": [item for item in ids if item], "parsed_count": len(ids),
                "coverage": {"current_listings": len(rows), "historical_pool_complete": False}}
