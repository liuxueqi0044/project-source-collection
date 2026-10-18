"""Full fixed-scope SEC runs in a separate V3 copy, with immutable metrics.

This is a development-scope performance/idempotence check, not a frozen holdout.
No raw evidence or databases beneath data/v3_holdout are opened for writing.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from equity_data.cli import load_config
from equity_data.machine import execute
from equity_data.store import Store

DESTINATION = ROOT / "data/v4_sec_repeat"
REPORTS = ROOT / "reports/v4/sec_repeat"
SOURCE = ROOT / "data/v3_holdout"


def write(path, value):
    if path.exists():
        raise RuntimeError("Evidence exists; choose another round label: " + str(path))
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(db):
    counts = {row[0]: row[1] for row in db.execute("SELECT dataset,COUNT(*) FROM records WHERE source='sec_edgar' GROUP BY dataset")}
    digest = hashlib.sha256()
    for row in db.execute("SELECT record_id,semantic_hash,raw_id,parser_version FROM records WHERE source='sec_edgar' ORDER BY sequence"):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode())
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"records_by_dataset": counts, "records_total": sum(counts.values()), "records_identity_sha256": digest.hexdigest(),
            "raw_blobs": db.execute("SELECT COUNT(DISTINCT raw_id) FROM raw_retrievals WHERE source='sec_edgar'").fetchone()[0],
            "raw_retrievals": db.execute("SELECT COUNT(*) FROM raw_retrievals WHERE source='sec_edgar'").fetchone()[0],
            "task_states": {row[0]: row[1] for row in db.execute("SELECT state,COUNT(*) FROM machine_tasks WHERE source_id='sec_edgar' GROUP BY state")},
            "resource_states": {row[0]: row[1] for row in db.execute("SELECT state,COUNT(*) FROM machine_inventory WHERE source_id='sec_edgar' GROUP BY state")},
            "unresolved_tasks": [dict(row) for row in db.execute("SELECT task_id,task_kind,state,generation,resource_id FROM machine_tasks WHERE source_id='sec_edgar' AND state NOT IN ('succeeded','superseded','cancelled') ORDER BY created_at")],
            "canonical_work_states": {row[0]: row[1] for row in db.execute("SELECT t.state,COUNT(*) FROM machine_tasks t JOIN machine_work_items w USING(task_id) WHERE t.source_id='sec_edgar' GROUP BY t.state")} if "machine_work_items" in tables else None,
            "parse_cache_rows": db.execute("SELECT COUNT(*) FROM machine_parse_cache").fetchone()[0] if "machine_parse_cache" in tables else None}


def clone():
    if DESTINATION.exists():
        raise RuntimeError("Destination already exists; refusing to replace it")
    DESTINATION.mkdir(parents=True)
    source_db = SOURCE / "data.sqlite3"
    source_hash = hashlib.sha256(source_db.read_bytes()).hexdigest()
    with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(DESTINATION / "data.sqlite3") as dst:
        src.backup(dst)
    shutil.copytree(SOURCE / "raw", DESTINATION / "raw")
    with sqlite3.connect(DESTINATION / "data.sqlite3") as db:
        db.row_factory = sqlite3.Row
        absolute = db.execute("SELECT COUNT(*) FROM raw_blobs WHERE path LIKE 'C:%' OR path LIKE '/%'").fetchone()[0]
        if absolute:
            raise RuntimeError("Clone contains absolute raw paths; stop before acquisition")
        initial = snapshot(db)
    write(REPORTS / "clone.json", {"created_at": datetime.now(timezone.utc).isoformat(), "source": str(SOURCE), "destination": str(DESTINATION),
          "sqlite_backup_source_mode": "ro", "source_database_sha256": source_hash, "initial": initial,
          "scope": {"symbols": ["SBUX", "HON", "SAP"], "source": "sec_edgar", "start": "2024-01-01", "end": "2024-04-30"}})


def run(label, command, full_history):
    path = REPORTS / (label + ".json")
    if path.exists():
        raise RuntimeError("Round already recorded")
    config = load_config(ROOT / "configs/v3_holdout.toml")
    config.update(storage=str(DESTINATION), sources=["sec_edgar"], reports=str(REPORTS))
    config["budget"] = {"max_tasks": 20000, "max_requests": 5000, "max_bytes": 1500000000, "max_runtime_seconds": 5400, "max_cost_usd": 0}
    args = SimpleNamespace(command=command, sources="sec_edgar", symbols=None, start="2024-01-01", end="2024-04-30",
                           max_tasks=20000, full_history=full_history, offline=False, raw_ids=None, reprocess_versions="latest")
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "equity_data").rglob("*.py"))}
    scope = {key: config[key] for key in ("market", "start", "end", "sources", "data_types", "universe")}
    scope["sec"] = {key: value for key, value in config["sec"].items() if "user_agent" not in key}
    started = datetime.now(timezone.utc).isoformat()
    begin = time.monotonic()
    with Store(DESTINATION) as store:
        before = snapshot(store.db)
        result = execute(store, config, args)
        after = snapshot(store.db)
    report = {"label": label, "development_pool_not_holdout": True, "started_at": started,
              "finished_at": datetime.now(timezone.utc).isoformat(), "wall_seconds": round(time.monotonic() - begin, 3),
              "command": command, "full_history_revision_refresh": full_history, "scope": scope,
              "scope_sha256": hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
              "production_sha256": hashes, "before": before, "after": after, "result": result,
              "production_files_changed_during_run": [str(p.relative_to(ROOT)) for p in sorted((ROOT / "equity_data").rglob("*.py")) if hashes.get(str(p.relative_to(ROOT))) != hashlib.sha256(p.read_bytes()).hexdigest()],
              "delta": {key: after[key] - before[key] for key in ("records_total", "raw_blobs", "raw_retrievals")},
              "business_records_unchanged": before["records_identity_sha256"] == after["records_identity_sha256"],
              "originals_and_business_values_separate_from_task_generations": True}
    write(path, report)
    print(json.dumps({key: report[key] for key in ("label", "wall_seconds", "delta", "business_records_unchanged")}, ensure_ascii=False), flush=True)
    print(json.dumps({"network_budget": result.get("network_budget"), "result": {key: result.get(key) for key in ("executed", "failed", "stop_reason", "remaining", "task_completion", "parsing_completion")},
                      "all_generation_unresolved_sec_tasks_including_legacy_copy": len(after["unresolved_tasks"]),
                      "canonical_work_states": after["canonical_work_states"],
                      "active_scope_remaining": result.get("remaining_tasks")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", action="store_true")
    parser.add_argument("--label")
    parser.add_argument("--command", choices=("backfill", "update", "run", "reprocess"), default="backfill")
    parser.add_argument("--full-history", action="store_true")
    args = parser.parse_args()
    REPORTS.mkdir(parents=True, exist_ok=True)
    if args.clone:
        clone()
        print("Read-only V3 backup and raw copy created", flush=True)
    else:
        if not args.label:
            parser.error("--label is required for a run")
        run(args.label, args.command, args.full_history)
