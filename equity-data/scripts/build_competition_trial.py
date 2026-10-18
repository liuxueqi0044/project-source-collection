"""Resolve saved-filing evidence into a reviewed trial bundle, without live writes.

This is an acceptance preparation tool, not a production company adapter. All
company-specific identity/scope assertions live in the input review manifest.
Remote sources remain for the standard competition import command to acquire.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

from equity_data.competition.evidence import validate_evidence
from equity_data.local_control import collector_lock
from equity_data.replay import _copy_blob
from equity_data.store import Store, _json, now_utc


def saved_filing(source, definition):
    rows = source.execute("SELECT data_json FROM records WHERE dataset='filings' AND company_id=?", (definition["company_id"],)).fetchall()
    filings = [json.loads(row[0]) for row in rows]
    filings = [row for row in filings if row.get("form") in definition.get("forms", ["10-K", "20-F"])]
    if definition.get("report_end_not_after"):
        filings = [row for row in filings if row.get("report_date", "9999") <= definition["report_end_not_after"]]
    if not filings:
        raise ValueError("No matching saved filing for " + definition["key"])
    filing = max(filings, key=lambda item: (item.get("report_date", ""), item.get("filing_date", ""), item["accession"]))
    cik = str(int(definition["company_id"].split(":", 1)[1]))
    url = "https://www.sec.gov/Archives/edgar/data/" + cik + "/" + filing["accession"].replace("-", "") + "/" + filing["primary_document"]
    row = source.execute("SELECT r.*,b.sha256,b.path,b.byte_size,b.media_type,b.created_at FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.url=? ORDER BY r.retrieved_at DESC,r.rowid DESC LIMIT 1", (url,)).fetchone()
    if row is None:
        raise ValueError("Discovered latest filing has no saved primary original: " + url)
    return dict(row), filing


def import_selected_raw(store, source, source_root, row):
    origin = (source_root / row["path"]).resolve()
    if not origin.is_relative_to(source_root) or not origin.is_file():
        raise ValueError("Saved source path is outside its store or missing")
    body = origin.read_bytes()
    if hashlib.sha256(body).hexdigest() != row["sha256"] or len(body) != row["byte_size"]:
        raise ValueError("Saved filing failed SHA-256/size validation")
    old = store.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (row["retrieval_id"],)).fetchone()
    keys = ("retrieval_id", "raw_id", "source", "url", "retrieved_at", "metadata_json")
    if old and any(old[key] != row[key] for key in keys):
        raise ValueError("Original retrieval ID conflicts with target metadata")
    relative = (Path("raw") / row["sha256"][:2] / row["sha256"]).as_posix()
    _copy_blob(origin, store.root / relative, (row["sha256"], row["byte_size"]))
    registration = source.execute("SELECT * FROM sources WHERE source=?", (row["source"],)).fetchone()
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?)", tuple(registration[key] for key in ("source", "metadata_json", "updated_at")))
        store.db.execute("INSERT OR IGNORE INTO raw_blobs VALUES(?,?,?,?,?,?)", (row["raw_id"], row["sha256"], relative, row["byte_size"], row["media_type"], row["created_at"]))
        store.db.execute("INSERT OR IGNORE INTO raw_retrievals VALUES(?,?,?,?,?,?)", tuple(row[key] for key in keys))
    return {"raw_id": row["raw_id"], "sha256": row["sha256"], "url": row["url"], "retrieval_id": row["retrieval_id"]}


def build_trial(manifest, source_root, target_root):
    source_root, target_root = Path(source_root).resolve(), Path(target_root).resolve()
    if source_root == target_root:
        raise ValueError("Trial output must use a separate store; live is read-only")
    source = sqlite3.connect((source_root / "data.sqlite3").as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("BEGIN")
    output = copy.deepcopy(manifest)
    imports, references = [], {}
    try:
        with collector_lock(target_root) as acquired:
            if not acquired:
                raise RuntimeError("Trial collector busy; no import performed")
            with Store(target_root) as store:
                for definition in output.pop("local_sources", []):
                    row, filing = saved_filing(source, definition)
                    references[definition["key"]] = import_selected_raw(store, source, source_root, row)
                    imports.append({"source_key": definition["key"], **references[definition["key"]], "original_retrieved_at": row["retrieved_at"], "report_date": filing.get("report_date"), "filing_date": filing.get("filing_date"), "transferred_at": now_utc(), "network_requests": 0, "original_retrieval_identity_preserved": True})
                def replace(value):
                    if isinstance(value, dict):
                        if value.get("local_source_key"):
                            ref = {**references[value["local_source_key"]], **{key: val for key, val in value.items() if key != "local_source_key"}}
                            return validate_evidence(store, [ref])[0]
                        return {key: replace(val) for key, val in value.items()}
                    return [replace(item) for item in value] if isinstance(value, list) else value
                output = replace(output)
                audit = {"at": now_utc(), "manifest_sha256": hashlib.sha256(_json(manifest).encode()).hexdigest(), "imports": imports, "source_database": "read_only", "remote_sources": "not_acquired_by_this_preparation_step", "review_kind": "agent_evidence_review; not human approval", "uses_production_company_branches": False}
                store.checkpoint("competition:trial-preparation:" + audit["manifest_sha256"], audit)
        return output, audit
    finally:
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="configs/competition-trial.json")
    parser.add_argument("--source", default="data/live")
    parser.add_argument("--storage", default="data/competition-validation")
    parser.add_argument("--out", default="reports/v5/competition-trial-resolved.json")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    output, audit = build_trial(manifest, args.source, args.storage)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    destination.with_suffix(".audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"bundle": str(destination), "audit": str(destination.with_suffix('.audit.json')), "raw_files_imported_or_verified": len(audit["imports"]), "remote_http_requests": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
