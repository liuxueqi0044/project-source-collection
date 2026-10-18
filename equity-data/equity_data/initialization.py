"""Durable, per-company historical bootstrap. No company-specific answers.

Call mutating APIs under local_control.collector_lock. Each network batch uses
machine.execute's engine lock and budgets. Initialization describes the adapter
inventory, never independent source recall, field accuracy, or current freshness.
"""
from __future__ import annotations

import calendar
import copy
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from .store import _json, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS initialization_batches (
 batch_id TEXT PRIMARY KEY, target_json TEXT NOT NULL, source_id TEXT NOT NULL,
 start TEXT NOT NULL, end TEXT NOT NULL, specification_json TEXT NOT NULL,
 state TEXT NOT NULL, acquisition_complete INTEGER NOT NULL DEFAULT 0,
 parsing_complete INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 last_attempt_at TEXT, completed_at TEXT, next_attempt_at TEXT,
 attempt_count INTEGER NOT NULL DEFAULT 0, scope_ids_json TEXT NOT NULL DEFAULT '[]',
 result_json TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS initialization_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, event TEXT NOT NULL, details_json TEXT NOT NULL
);
"""
HISTORICAL_TYPES = ("filings", "documents", "financial_facts", "guidance",
                    "relations", "operating_metrics", "commitments", "debt_maturities", "risk_disclosures")


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _schema(store):
    store.db.executescript(SCHEMA)
    store.db.commit()


def _targets(config, symbols=None):
    universe = config.get("universe", {})
    if symbols is None and universe.get("mode", "configured") != "configured":
        raise ValueError("Historical initialization requires an explicit configured pool or CSV; freeze a discovered current universe first")
    rows = [] if symbols is not None else copy.deepcopy(config.get("securities", []))
    selected = symbols if symbols is not None else universe.get("symbols", [])
    if isinstance(selected, str):
        selected = selected.split(",")
    rows += [{"symbol": str(s).strip().upper()} for s in selected if str(s).strip()]
    if symbols is None:
        rows += [{"cik": c} for c in universe.get("ciks", [])]
        if universe.get("csv_file"):
            path = Path(universe["csv_file"])
            if not path.is_absolute():
                path = Path(config.get("_config_dir", ".")) / path
            rows += list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    from .universe import normalize_cik
    targets = {}
    for original in rows:
        row = {k: v for k, v in original.items() if v not in (None, "") and k not in {"start", "end"} and not k.startswith("_")}
        if row.get("symbol"):
            row["symbol"] = row["symbol"].upper().strip()
        if row.get("cik"):
            row["cik"] = normalize_cik(row["cik"])
        if not row.get("symbol") and not row.get("cik"):
            raise ValueError("Each initialization target needs a symbol or CIK")
        key = (row.get("cik"), row.get("symbol"))
        targets[key] = {**targets.get(key, {}), **row}
    if not targets:
        raise ValueError("Historical initialization pool is empty")
    return sorted(targets.values(), key=_json)


def build_initialization_payload(config, start=None, end=None, symbols=None, sources=None):
    settings = config.get("initialization", {})
    start = str(start or settings.get("start") or config.get("start", ""))
    end = str(end or settings.get("end") or config.get("end", ""))
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Initialization start must not exceed end")
    sources = sources or settings.get("sources", ["sec_edgar"])
    if isinstance(sources, str):
        sources = sources.split(",")
    sources = sorted(set(s.strip() for s in sources if s.strip()))
    if not sources:
        raise ValueError("Initialization needs at least one source")
    months = int(settings.get("chunk_months", 4))
    if not 1 <= months <= 24:
        raise ValueError("initialization.chunk_months must be 1..24")
    types = settings.get("data_types") or [t for t in config.get("data_types", HISTORICAL_TYPES) if t in HISTORICAL_TYPES]
    if not {"filings", "documents", "financial_facts"}.issubset(types):
        raise ValueError("Historical initialization must include filings, documents and financial_facts")
    payload = {"initialization": True, "market": config.get("market", "US"),
               "start": start, "end": end, "targets": _targets(config, symbols),
               "sources": sources, "data_types": sorted(set(types)), "chunk_months": months}
    payload["initialization_id"] = "init:" + _hash(payload)
    return payload


def _windows(start, end, months):
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        index = current.year * 12 + current.month - 1 + months
        following = date(index // 12, index % 12 + 1, min(current.day, calendar.monthrange(index // 12, index % 12 + 1)[1]))
        finish = min(last, following - timedelta(days=1))
        yield current.isoformat(), finish.isoformat()
        current = finish + timedelta(days=1)


def _specifications(config, payload):
    from .engine import source_scope
    for target in payload["targets"]:
        for source in payload["sources"]:
            for start, end in _windows(payload["start"], payload["end"], payload["chunk_months"]):
                conf = _batch_config(config, payload, target, source, start, end)
                specification = source_scope(conf, source)
                yield {"batch_id": "init-batch:" + _hash(specification), "target": target,
                       "source_id": source, "start": start, "end": end, "specification": specification}


def _batch_config(config, payload, target, source, start, end):
    conf = copy.deepcopy(config)
    conf.pop("_requested_scope", None)
    conf.update(market=payload["market"], start=start, end=end, sources=[source], data_types=list(payload["data_types"]),
                universe={"mode": "configured", "symbols": [], "ciks": []}, securities=[copy.deepcopy(target)])
    # Per-batch network/task limits may differ from normal daily increments.
    conf["budget"] = {**conf.get("budget", {}), **config.get("initialization", {}).get("budget", {})}
    conf["revision_refresh"] = False
    return conf


def ensure_plan(store, config, payload=None):
    payload = payload or build_initialization_payload(config)
    _schema(store)
    with store.db:
        for batch in _specifications(config, payload):
            store.db.execute("""INSERT OR IGNORE INTO initialization_batches
                (batch_id,target_json,source_id,start,end,specification_json,state,created_at)
                VALUES(?,?,?,?,?,?,'pending',?)""", (batch["batch_id"], _json(batch["target"]), batch["source_id"],
                   batch["start"], batch["end"], _json(batch["specification"]), now_utc()))
    return payload


def _rows(store, config, payload):
    table = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='initialization_batches'").fetchone()
    rows = []
    for spec in _specifications(config, payload):
        row = store.db.execute("SELECT * FROM initialization_batches WHERE batch_id=?", (spec["batch_id"],)).fetchone() if table else None
        if row:
            item = dict(row)
            for key in ("target", "specification", "scope_ids", "result"):
                item[key] = json.loads(item.pop(key + "_json") or "null")
        else:
            item = {**spec, "state": "not_initialized", "acquisition_complete": False, "parsing_complete": False, "attempt_count": 0}
        rows.append(item)
    return rows


def initialization_status(store, config, payload=None):
    payload = payload or build_initialization_payload(config)
    rows = _rows(store, config, payload)
    acquisition = bool(rows) and all(r["acquisition_complete"] for r in rows)
    parsing = acquisition and all(r["parsing_complete"] for r in rows)
    started = any(r.get("attempt_count", 0) for r in rows)
    return {"state": "initialized" if parsing else "acquisition_initialized" if acquisition else "in_progress" if started else "not_initialized",
            "initialization_id": payload["initialization_id"], "start": payload["start"], "end": payload["end"],
            "targets": payload["targets"], "sources": payload["sources"], "data_types": payload["data_types"],
            "acquisition_complete": acquisition, "parsing_complete": parsing, "batch_count": len(rows),
            "acquired_batches": sum(bool(r["acquisition_complete"]) for r in rows),
            "parsed_batches": sum(bool(r["parsing_complete"]) for r in rows),
            "attempted_batches": sum(bool(r.get("attempt_count")) for r in rows), "batches": rows,
            "basis": "Configured adapter discovery/acquisition only; partial parsing remains a gap. This does not assert independent recall, semantic accuracy, historical universe completeness or current freshness."}


def next_pending_plan(store, config, payload=None, now=None):
    payload = payload or build_initialization_payload(config)
    now = now or now_utc()
    rows = _rows(store, config, payload)
    for row in rows:
        if row["acquisition_complete"] or row["state"] == "blocked":
            continue
        if row.get("next_attempt_at") and row["next_attempt_at"] > now:
            continue
        return row
    return None


def _continuation(store, scope_ids):
    """Retry only unfinished work in this batch, respecting its engine gates."""
    if not scope_ids:
        # No persisted scope means no known work or engine retry gate to resume.
        # Only the explicit budget-interruption branch below may auto-continue.
        return "blocked", None
    placeholders = ",".join("?" for _ in scope_ids)
    rows = store.db.execute("SELECT DISTINCT t.state,t.next_attempt_at FROM machine_tasks t JOIN machine_task_demands d USING(task_id) WHERE d.scope_id IN (" + placeholders + ") AND t.state!='succeeded'", scope_ids).fetchall()
    actionable = [r for r in rows if r["state"] not in {"exhausted", "cancelled"}]
    if not actionable:
        return "blocked", None
    if any(r["state"] in {"pending", "running"} and not r["next_attempt_at"] for r in actionable):
        return "in_progress", None
    dates = [r["next_attempt_at"] for r in actionable if r["next_attempt_at"]]
    if dates:
        return "in_progress", min(dates)
    return "blocked", None


def run_initialization_batch(store, config, payload=None, execute_fn=None):
    payload = ensure_plan(store, config, payload)
    batch = next_pending_plan(store, config, payload)
    if batch is None:
        return {"run": None, "batch": None, "initialization": initialization_status(store, config, payload), "reason": "No initialization batch ready; complete or waiting for dependency/retry deadline"}
    conf = _batch_config(config, payload, batch["target"], batch["source_id"], batch["start"], batch["end"])
    args = SimpleNamespace(command="backfill", sources=None, symbols=None, start=batch["start"], end=batch["end"],
                           offline=False, data_types=None, full_history=False, max_tasks=None, resume_scope_ids=None)
    when = now_utc()
    with store.db:
        store.db.execute("UPDATE initialization_batches SET state='running',last_attempt_at=?,attempt_count=attempt_count+1,last_error=NULL WHERE batch_id=?", (when, batch["batch_id"]))
        store.db.execute("INSERT INTO initialization_events(batch_id,observed_at,event,details_json) VALUES(?,?,'batch_started',?)", (batch["batch_id"], when, _json({"trigger": "historical_initialization", "start": batch["start"], "end": batch["end"]})))
    if execute_fn is None:
        from .machine import execute as execute_fn
    try:
        result = execute_fn(store, conf, args)
        checks = [r for r in result.get("source_scope_checks", []) if r.get("source_id") == batch["source_id"]]
        scope_ids = [r["scope_id"] for r in checks]
        acquisition = (bool(checks) and all(r.get("progress", {}).get("discovery_complete") and r.get("progress", {}).get("acquisition_complete") for r in checks)
                       and not result.get("stop_reason") and not any(result.get("remaining_tasks", {}).values()))
        parsing = acquisition and all(r.get("progress", {}).get("parsing_complete") for r in checks)
        state, next_at = ("initialized" if parsing else "acquisition_initialized", None) if acquisition else _continuation(store, scope_ids)
        if result.get("stop_reason"):
            # Budget interruption remains eligible next pulse even when no
            # company scope could yet be established (directory budget).
            state, next_at = "in_progress", None
        saved = {"run_id": result.get("run_id"), "network_budget": result.get("network_budget"),
                 "source_scope_checks": checks, "remaining_tasks": result.get("remaining_tasks"),
                 "stop_reason": result.get("stop_reason"), "new_records": result.get("new_records")}
        with store.db:
            store.db.execute("UPDATE initialization_batches SET state=?,acquisition_complete=?,parsing_complete=?,completed_at=?,next_attempt_at=?,scope_ids_json=?,result_json=? WHERE batch_id=?",
                (state, int(acquisition), int(parsing), now_utc() if acquisition else None, next_at, _json(scope_ids), _json(saved), batch["batch_id"]))
            store.db.execute("INSERT INTO initialization_events(batch_id,observed_at,event,details_json) VALUES(?,?,'batch_result',?)", (batch["batch_id"], now_utc(), _json({"state": state, **saved})))
        return {"run": result, "batch": batch, "initialization": initialization_status(store, config, payload)}
    except Exception as exc:
        # Configuration/runtime faults are visible and require an explicit retry;
        # automatic polling must not execute a permanently broken batch forever.
        with store.db:
            store.db.execute("UPDATE initialization_batches SET state='blocked',last_error=? WHERE batch_id=?", (f"{type(exc).__name__}: {exc}", batch["batch_id"]))
            store.db.execute("INSERT INTO initialization_events(batch_id,observed_at,event,details_json) VALUES(?,?,'batch_failed',?)", (batch["batch_id"], now_utc(), _json({"error": f"{type(exc).__name__}: {exc}"})))
        raise


def retry_initialization(store, config, payload=None, reason="explicit_operator_retry"):
    payload = ensure_plan(store, config, payload)
    with store.db:
        for row in _rows(store, config, payload):
            if row["state"] == "blocked":
                store.db.execute("UPDATE initialization_batches SET state='pending',next_attempt_at=NULL WHERE batch_id=?", (row["batch_id"],))
                store.db.execute("INSERT INTO initialization_events(batch_id,observed_at,event,details_json) VALUES(?,?,'operator_retry',?)", (row["batch_id"], now_utc(), _json({"reason": reason})))
    return initialization_status(store, config, payload)


# Short aliases for programmatic clients.
status = initialization_status
run_batch = run_initialization_batch
