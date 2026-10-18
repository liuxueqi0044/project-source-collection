"""Read-only reconciliation of the complete fixed SEC development scope."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports/v4/sec_repeat"
target = REPORTS / "summary-development.json"
if target.exists():
    raise SystemExit("Refusing to overwrite evidence")
reference_path = ROOT / "reports/v3/holdout-reference-final.json"
reference = json.loads(reference_path.read_text(encoding="utf-8"))
data = ROOT / "data/v4_sec_repeat"
original = ROOT / "data/v3_holdout/data.sqlite3"
clone = json.loads((REPORTS / "clone.json").read_text(encoding="utf-8"))
with sqlite3.connect((data / "data.sqlite3").as_uri() + "?mode=ro", uri=True) as db, sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as old:
    db.row_factory = sqlite3.Row
    known_urls = {row[0] for row in old.execute("SELECT DISTINCT url FROM raw_retrievals WHERE source='sec_edgar'")}
    resources = {row["url"]: dict(row) for row in db.execute("SELECT * FROM machine_inventory WHERE source_id='sec_edgar'")}
    documents, verified = [], {}
    for ref in reference["documents"]:
        row = resources.get(ref["url"])
        blob = db.execute("SELECT path,sha256 FROM raw_blobs WHERE raw_id=?", (row["raw_id"],)).fetchone() if row and row["raw_id"] else None
        if blob and row["raw_id"] not in verified:
            path = data / blob["path"]
            verified[row["raw_id"]] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == blob["sha256"]
        saved = bool(blob and verified[row["raw_id"]])
        documents.append({"reference_id": ref["reference_id"], "company_id": ref["company_id"], "url": ref["url"],
                          "saved_original_verified": saved, "workflow_state": row["state"] if row else "not_discovered",
                          "newly_obtained_since_V3": saved and ref["url"] not in known_urls,
                          "raw_id": row["raw_id"] if row else None})
    canonical = [dict(row) for row in db.execute("SELECT t.task_id,t.task_kind,t.state,t.resource_id,t.last_error FROM machine_tasks t JOIN machine_work_items w USING(task_id) WHERE t.source_id='sec_edgar' AND t.state!='succeeded'")]
    runs = [dict(row) for row in db.execute("SELECT run_id,mode,started_at,finished_at,state,summary_json FROM machine_runs WHERE started_at>=? ORDER BY started_at", (clone["created_at"].replace("+00:00", "Z"),))]
rounds = []
previous_code = None
for path in sorted(REPORTS.glob("[0-9][0-9]-*.json")):
    report = json.loads(path.read_text(encoding="utf-8"))
    with sqlite3.connect((data / "data.sqlite3").as_uri() + "?mode=ro", uri=True) as stats:
        completed_parse_attempts = stats.execute("""SELECT COUNT(*) FROM machine_attempts a JOIN machine_tasks t USING(task_id)
            WHERE a.run_id=? AND a.state='succeeded' AND t.task_kind IN ('acquire','reprocess')""", (report["result"]["run_id"],)).fetchone()[0]
        by_kind = {row[0]: row[1] for row in stats.execute("""SELECT i.kind,COUNT(*) FROM machine_attempts a
            JOIN machine_tasks t USING(task_id) JOIN machine_inventory i USING(resource_id)
            WHERE a.run_id=? AND a.state='succeeded' GROUP BY i.kind""", (report["result"]["run_id"],))}
    cache_hits = report["result"].get("parse_cache_hits", 0)
    rounds.append({key: report.get(key) for key in ("label", "command", "full_history_revision_refresh", "wall_seconds", "delta", "business_records_unchanged", "production_files_changed_during_run")}
                  | {"result": report["result"], "evidence": path.name,
                     "successful_parse_task_attempts": completed_parse_attempts, "parse_cache_hits": cache_hits,
                     "actual_uncached_parse_completions": completed_parse_attempts - cache_hits,
                     "completed_acquisition_attempts_by_kind": by_kind,
                     "uncached_parse_count_basis": "Successful acquire/reprocess completions minus engine-recorded cache hits; includes discovery/listing/header parsing, not only financial-document parsing.",
                     "same_production_files_as_previous_round": report["production_sha256"] == previous_code if previous_code else None})
    previous_code = report["production_sha256"]
summary = {"captured_at": datetime.now(timezone.utc).isoformat(), "scope": reference["scope"], "symbols": ["SBUX", "HON", "SAP"],
           "development_pool_not_frozen_generalization": True,
           "independent_reference": {"path": str(reference_path.relative_to(ROOT)), "sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                                      "provenance": reference["reference_provenance"], "limitations": reference["limitations"]},
           "documents_expected": len(documents), "documents_saved_original_verified": sum(row["saved_original_verified"] for row in documents),
           "success_basis": "downloaded_has_saved_raw; not new upstream verification, complete field extraction or whole filing-package coverage",
           "missing_documents": [row for row in documents if not row["saved_original_verified"]],
           "newly_obtained_documents": [row for row in documents if row["newly_obtained_since_V3"]],
           "canonical_unfinished_work": canonical, "rounds": rounds,
           "source_V3_database_unchanged": hashlib.sha256(original.read_bytes()).hexdigest() == clone["source_database_sha256"],
           "records": documents,
           "unparsed_or_partial_originals": [row for row in documents if row["workflow_state"] not in ("parsed", "not_disclosed", "not_applicable")]}
target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: summary[key] for key in ("documents_expected", "documents_saved_original_verified", "missing_documents", "newly_obtained_documents", "source_V3_database_unchanged")}))
