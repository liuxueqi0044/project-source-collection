"""Run the frozen-code CSV-only held-out bootstrap and repeat-init acceptance.

This verification harness contains no financial answers, CIKs or filing URLs.
It does not change production code, create OS tasks, or write the live library.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from equity_data.cli import load_config
from equity_data.initialization import (build_initialization_payload, initialization_status,
    next_pending_plan, run_initialization_batch)
from equity_data.local_control import collector_lock
from equity_data.store import Store

CANDIDATES = ["ADP", "AZO", "EMR", "GPC", "KR", "LIN", "MCD", "NUE"]
SEED = 20261010


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def production_hashes():
    files = sorted(PROJECT.joinpath("equity_data").rglob("*.py"))
    files += [PROJECT / "configs/source_registry.json", PROJECT / "pyproject.toml"]
    result = {p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {"observed_at": now(), "files": result,
            "aggregate_sha256": hashlib.sha256(canonical(result).encode()).hexdigest()}


def require_freeze(frozen):
    actual = production_hashes()
    expected = frozen.get("files", {})
    if not expected:
        raise ValueError("Freeze manifest has no files mapping")
    changes = sorted(key for key in set(expected) | set(actual["files"]) if expected.get(key) != actual["files"].get(key))
    if changes:
        raise ValueError("Production differs from freeze: " + ", ".join(changes))
    return actual


def counts(store):
    existing = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = ("records", "raw_blobs", "raw_retrievals", "machine_tasks", "machine_resource_versions", "initialization_events")
    result = {name: store.db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] if name in existing else 0 for name in tables}
    result["raw_bytes"] = store.db.execute("SELECT COALESCE(SUM(byte_size),0) FROM raw_blobs").fetchone()[0]
    result["database_bytes"] = sum(p.stat().st_size for p in store.root.glob("*.sqlite3*"))
    return result


def business_hash(store):
    digest = hashlib.sha256()
    for row in store.db.execute("SELECT * FROM records ORDER BY sequence"):
        digest.update(canonical(dict(row)).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def inventory(store):
    data = []
    for row in store.db.execute("""SELECT company_id,dataset,COUNT(*) stored_versions,
        MIN(period_start) earliest_period_start,MAX(period_end) latest_period_end,
        MIN(published_at) earliest_publication,MAX(published_at) latest_publication,
        MIN(retrieved_at) earliest_acquisition,MAX(retrieved_at) latest_acquisition,
        MIN(created_at) earliest_parsing,MAX(created_at) latest_parsing
        FROM records GROUP BY company_id,dataset ORDER BY company_id,dataset"""):
        data.append(dict(row))
    stages = [dict(r) for r in store.db.execute("""SELECT dataset,
        COALESCE(json_extract(data_json,'$.fact_stage'),
          CASE WHEN json_extract(data_json,'$.extraction_method')='narrative_evidence_candidate' THEN 'candidate' ELSE 'record_without_semantic_contract' END) stage,
        quality,COUNT(*) stored_versions FROM records GROUP BY dataset,stage,quality ORDER BY dataset,stage,quality""")]
    return {"inventory": data, "semantic_stages": stages,
            "basis": "Stored record versions, not an independent coverage denominator or numeric accuracy audit"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/acceptance_v41_new_company.toml")
    parser.add_argument("--freeze", required=True, help="Frozen production files mapping; must match before every batch")
    parser.add_argument("--out", default="reports/v41/new-company")
    parser.add_argument("--max-batches", type=int, default=20, help="Explicit resource cap; unfinished remains unfinished")
    parser.add_argument("--resume", action="store_true", help="Continue only the same persisted config/payload and frozen code")
    args = parser.parse_args(argv)
    if args.max_batches < 1:
        raise ValueError("max-batches must be positive")
    conf = load_config(PROJECT / args.config)
    out = (PROJECT / args.out).resolve()
    frozen_path = (PROJECT / args.freeze).resolve()
    frozen = json.loads(frozen_path.read_text(encoding="utf-8-sig"))
    before_code = require_freeze(frozen)
    payload = build_initialization_payload(conf)
    chosen = random.Random(SEED).choice(CANDIDATES)
    if [row.get("symbol") for row in payload["targets"]] != [chosen]:
        raise ValueError("CSV does not match the predeclared seeded sample; do not resample after inspecting results")
    if Path(conf["storage"]).resolve() == (PROJECT / "data/live").resolve():
        raise ValueError("Held-out acceptance must use separate storage")
    csv_path = Path(conf["_config_dir"]) / conf["universe"]["csv_file"]
    config_hash = hashlib.sha256(Path(conf["_config_path"]).read_bytes()).hexdigest()
    csv_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    selection = {"declared_at": now(), "candidate_order": CANDIDATES, "seed": SEED,
                 "method": "random.Random(20261010).choice(candidate_order); one draw, no result-based resampling",
                 "selected_symbol": chosen, "config_sha256": config_hash, "csv_sha256": csv_hash,
                 "payload": payload, "production_freeze_path": str(frozen_path),
                 "production_files": before_code["files"], "financial_answers_configured": False,
                 "filing_urls_configured": False, "independent_numeric_accuracy_verified": False}
    selection_path = out / "selection.json"
    if selection_path.exists():
        if not args.resume:
            raise ValueError("Existing acceptance evidence; use --resume to preserve prior rounds")
        old = json.loads(selection_path.read_text(encoding="utf-8"))
        for key in ("config_sha256", "csv_sha256", "payload", "production_files"):
            if old.get(key) != selection[key]:
                raise ValueError("Resume changes the frozen acceptance " + key)
    else:
        write(selection_path, selection)
    rounds = sorted(out.glob("round-*.json"))
    started = now()
    overall = time.perf_counter()
    with collector_lock(conf["storage"]) as acquired:
        if not acquired:
            raise RuntimeError("Acceptance storage collector is already running")
        with Store(conf["storage"]) as store:
            initial = counts(store)
            if not args.resume and any(initial[k] for k in ("records", "raw_blobs", "raw_retrievals", "machine_tasks")):
                raise ValueError("Held-out storage is not empty; refusing to claim fresh initialization")
            try:
                for index in range(args.max_batches):
                    if index and next_pending_plan(store, conf, payload) is None:
                        break
                    current_code = require_freeze(frozen)
                    before = counts(store)
                    timestamp = now()
                    clock = time.perf_counter()
                    result = run_initialization_batch(store, conf, payload)
                    after = counts(store)
                    evidence = {"started_at": timestamp, "finished_at": now(), "elapsed_seconds": time.perf_counter()-clock,
                                "production_hash_before": current_code["aggregate_sha256"],
                                "production_hash_after": require_freeze(frozen)["aggregate_sha256"],
                                "before": before, "after": after, "delta": {k: after[k]-before[k] for k in before},
                                "run": result["run"], "batch": result["batch"], "initialization": result["initialization"],
                                "evidence_kind": "real_network_and_disk_execution"}
                    destination = out / ("round-%03d.json" % (len(rounds)+1))
                    write(destination, evidence)
                    rounds.append(destination)
                    print(canonical({"round": len(rounds), "state": result["initialization"]["state"],
                                     "network_budget": (result["run"] or {}).get("network_budget"), "delta": evidence["delta"]}), flush=True)
                    if result["run"] is None or result["initialization"]["acquisition_complete"]:
                        break
                status = initialization_status(store, conf, payload)
                repeat = {"performed": False, "reason": "Initialization is not complete; repeat-success cannot be claimed"}
                if status["acquisition_complete"]:
                    before_repeat, business_before = counts(store), business_hash(store)
                    repeated = run_initialization_batch(store, conf, payload)
                    after_repeat, business_after = counts(store), business_hash(store)
                    repeat = {"performed": True, "engine_run": repeated["run"], "before": before_repeat, "after": after_repeat,
                              "delta": {k: after_repeat[k]-before_repeat[k] for k in before_repeat},
                              "business_sha256_before": business_before, "business_sha256_after": business_after,
                              "business_unchanged": business_before == business_after,
                              "network_requests": 0 if repeated["run"] is None else (repeated["run"].get("network_budget") or {}).get("requests"),
                              "basis": "Repeat of completed initialization ledger; not an independent upstream refresh"}
                    write(out / "repeat-initialization.json", repeat)
                summary = {"started_at": started, "finished_at": now(), "elapsed_seconds_this_invocation": time.perf_counter()-overall,
                           "selected_symbol": chosen, "config": str(conf["_config_path"]), "storage": conf["storage"],
                           "scope": payload, "production_unchanged_from_freeze": require_freeze(frozen)["files"] == before_code["files"],
                           "round_files": [p.name for p in rounds], "initialization": status, "counts": counts(store),
                           "repeat_initialization": repeat, **inventory(store),
                           "resource_cap_reached": len(rounds) >= args.max_batches and not status["acquisition_complete"],
                           "coverage_complete": False, "numeric_accuracy_verified": False,
                           "limitations": ["Adapter inventory is not an independently established source recall denominator", "Candidate semantics remain candidates", "Natural new disclosure/amendment observation is not tested by this historical run"]}
                write(out / "summary.json", summary)
                print(canonical({"summary": str(out / "summary.json"), "state": status["state"], "production_unchanged": summary["production_unchanged_from_freeze"]}), flush=True)
                return 0 if status["acquisition_complete"] and repeat.get("business_unchanged") else 2
            except Exception as exc:
                write(out / ("failure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"),
                      {"observed_at": now(), "error": f"{type(exc).__name__}: {exc}", "counts": counts(store),
                       "scope": payload, "production_hashes": production_hashes()})
                raise


if __name__ == "__main__":
    raise SystemExit(main())
