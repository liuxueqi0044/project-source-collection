import copy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from equity_data.initialization import (build_initialization_payload, ensure_plan,
    initialization_status, next_pending_plan, run_initialization_batch, retry_initialization)
from equity_data.local_control import collector_lock
from equity_data.replay import import_raw_cache, main as import_main
from equity_data.store import Store


def config(**extra):
    return {"market": "US", "start": "2024-01-01", "end": "2024-04-30",
            "sources": ["sec_edgar"], "data_types": ["filings", "documents", "financial_facts"],
            "universe": {"mode": "configured", "symbols": ["ONE"]}, "sec": {"forms": ["10-K"]}, **extra}


def successful(store, conf, args, *, parsed=True):
    return {"run_id": "synthetic", "source_scope_checks": [{"source_id": "sec_edgar", "scope_id": "fake",
            "progress": {"discovery_complete": True, "acquisition_complete": True, "parsing_complete": parsed}}],
            "universe": {"securities": [{"symbol": conf["securities"][0]["symbol"]}]}}


def test_status_is_read_only_before_and_after_initialization(tmp_path):
    with Store(tmp_path) as store:
        assert initialization_status(store, config())["state"] == "not_initialized"
        assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='initialization_batches'").fetchone()
        run_initialization_batch(store, config(), execute_fn=successful)
    db = sqlite3.connect((tmp_path / "data.sqlite3").as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        assert initialization_status(SimpleNamespace(db=db), config())["state"] == "initialized"
    finally:
        db.close()


def test_new_company_reuses_completed_batches_and_replay_is_noop(tmp_path):
    calls = []
    def execute(store, conf, args):
        calls.append(copy.deepcopy(conf))
        return successful(store, conf, args)
    with Store(tmp_path) as store:
        first = run_initialization_batch(store, config(), execute_fn=execute)
        assert first["initialization"]["acquisition_complete"]
        assert run_initialization_batch(store, config(), execute_fn=execute)["run"] is None
        changed = config(universe={"mode": "configured", "symbols": ["ONE", "TWO"]})
        second = run_initialization_batch(store, changed, execute_fn=execute)
        assert second["initialization"]["acquired_batches"] == 2
        assert [c["securities"][0]["symbol"] for c in calls] == ["ONE", "TWO"]


def test_budget_stop_cannot_mark_initialization_complete_and_reuses_dates(tmp_path):
    calls = []
    def execute(store, conf, args):
        calls.append((conf["start"], conf["end"], args.command))
        result = successful(store, conf, args)
        # No Queue has yet been built: a directory-stage budget interruption.
        result["source_scope_checks"] = []
        result["stop_reason"] = "max_requests"
        return result
    with Store(tmp_path) as store:
        first = run_initialization_batch(store, config(), execute_fn=execute)
        assert first["initialization"]["state"] == "in_progress"
        assert not first["initialization"]["acquisition_complete"]
        assert next_pending_plan(store, config())
        run_initialization_batch(store, config(), execute_fn=execute)
        assert calls[0] == calls[1] == ("2024-01-01", "2024-04-30", "backfill")
        assert store.db.execute("SELECT COUNT(*) FROM initialization_batches").fetchone()[0] == 1


def test_partial_parse_is_distinct_from_acquisition_and_not_polled_forever(tmp_path):
    with Store(tmp_path) as store:
        result = run_initialization_batch(store, config(), execute_fn=lambda *a: successful(*a, parsed=False))
        state = result["initialization"]
        assert state["state"] == "acquisition_initialized"
        assert state["acquisition_complete"] and not state["parsing_complete"]
        assert next_pending_plan(store, config()) is None


def test_unknown_source_completion_is_blocked_not_fabricated(tmp_path):
    from equity_data.queue import Queue
    with Store(tmp_path) as store:
        Queue(store)
        result = run_initialization_batch(store, config(), execute_fn=lambda *a: {"source_scope_checks": [{"scope_id": "unknown", "source_id": "sec_edgar", "progress": {}}]})
        assert result["initialization"]["state"] == "in_progress"
        assert next_pending_plan(store, config()) is None
        assert not result["initialization"]["acquisition_complete"]


def test_empty_scope_without_budget_cannot_repeat_on_automatic_pulses(tmp_path):
    calls = []
    def no_scope(store, conf, args):
        calls.append(args.command)
        return {"source_scope_checks": [], "remaining_tasks": {}, "stop_reason": None,
                "universe": {"securities": [], "gaps": [{"code": "MISSING_AUTHORIZATION"}]}}
    with Store(tmp_path) as store:
        result = run_initialization_batch(store, config(), execute_fn=no_scope)
        assert result["initialization"]["batches"][0]["state"] == "blocked"
        assert not result["initialization"]["acquisition_complete"]
        for _ in range(3):
            assert next_pending_plan(store, config()) is None
            assert run_initialization_batch(store, config(), execute_fn=no_scope)["run"] is None
        assert calls == ["backfill"]
        # Operator action is explicit and recorded; automatic pulses cannot
        # silently reset this stopped configuration or dependency failure.
        retry_initialization(store, config(), reason="authorization supplied")
        assert next_pending_plan(store, config())


def test_crash_before_scope_keeps_fixed_batch_and_exception_has_explicit_retry(tmp_path):
    with Store(tmp_path) as store:
        ensure_plan(store, config())
        store.db.execute("UPDATE initialization_batches SET state='running',attempt_count=1")
        store.db.commit()
        assert next_pending_plan(store, config())["start"] == "2024-01-01"
        with pytest.raises(ValueError, match="bad configuration"):
            run_initialization_batch(store, config(), execute_fn=lambda *a: (_ for _ in ()).throw(ValueError("bad configuration")))
        assert next_pending_plan(store, config()) is None
        retry_initialization(store, config())
        assert next_pending_plan(store, config())
        assert store.db.execute("SELECT COUNT(*) FROM initialization_events WHERE event='batch_failed'").fetchone()[0] == 1


def test_csv_and_explicit_dates_are_frozen_and_range_chunks_cover_leap_year(tmp_path):
    (tmp_path / "pool.csv").write_text("symbol,cik\nNEW,\nISSUER,456\n", encoding="utf-8")
    conf = config(_config_dir=str(tmp_path), universe={"mode": "configured", "csv_file": "pool.csv"}, initialization={"chunk_months": 1})
    payload = build_initialization_payload(conf, "2024-01-31", "2024-03-31")
    with Store(tmp_path / "store") as store:
        ensure_plan(store, conf, payload)
        (tmp_path / "pool.csv").write_text("symbol,cik\nOTHER,\n", encoding="utf-8")
        status = initialization_status(store, conf, payload)
        assert status["batch_count"] == 6
        assert {(r["start"], r["end"]) for r in status["batches"]} == {("2024-01-31", "2024-02-28"), ("2024-02-29", "2024-03-28"), ("2024-03-29", "2024-03-31")}
        assert {t["symbol"] for t in status["targets"]} == {"NEW", "ISSUER"}


def test_different_sources_types_options_and_ranges_cannot_share_completion(tmp_path):
    with Store(tmp_path) as store:
        run_initialization_batch(store, config(), execute_fn=successful)
        for changed in (config(sec={"forms": ["10-K", "10-Q"]}), config(end="2024-05-31"),
                        config(initialization={"sources": ["official_ir"]}),
                        config(data_types=["filings", "documents", "financial_facts", "guidance"])):
            assert not initialization_status(store, changed)["acquisition_complete"]


def make_raw(store, value=b"<xml>evidence</xml>", retrieved="2024-05-01T10:00:00Z"):
    return store.put_raw(value, source="sec_edgar", url="https://example.test/original.xml", retrieved_at=retrieved, metadata={"etag": "original"})


def test_raw_import_preserves_identity_timestamps_is_idempotent_and_does_not_claim_ready(tmp_path):
    with Store(tmp_path / "source") as source, Store(tmp_path / "target") as target:
        raw = make_raw(source)
        make_raw(source, retrieved="2024-05-02T10:00:00Z")
        source.put_record("financial_facts", "test", {"value": 23}, source="sec_edgar", raw_id=raw["raw_id"], published_at="2024-01-01T09:00:00Z")
        original_rows = [dict(r) for r in source.db.execute("SELECT * FROM raw_retrievals ORDER BY retrieval_id")]
        with collector_lock(target.root) as acquired:
            assert acquired
            first = import_raw_cache(target, source.root, sources=["sec_edgar"])
            second = import_raw_cache(target, source.root, sources=["sec_edgar"])
        assert first["added_blobs"] == 1 and first["added_retrievals"] == 2
        assert second["added_blobs"] == second["added_retrievals"] == 0
        assert first["research_records_after"] == 0 and first["network_requests"] == 0
        assert not first["acquisition_complete"]
        assert [dict(r) for r in target.db.execute("SELECT * FROM raw_retrievals ORDER BY retrieval_id")] == original_rows
        assert target.latest_raw("sec_edgar", "https://example.test/original.xml")["retrieved_at"].startswith("2024-05-02")
        assert first["manifest_sha256"] == second["manifest_sha256"]
        assert not target.db.execute("SELECT 1 FROM sqlite_master WHERE name='machine_scope_checks'").fetchone()


def test_corrupt_raw_import_leaves_no_partial_database_rows(tmp_path):
    with Store(tmp_path / "source") as source, Store(tmp_path / "target") as target:
        raw = make_raw(source)
        Path(raw["path"]).write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="SHA-256"):
            import_raw_cache(target, source.root)
        assert target.db.execute("SELECT COUNT(*) FROM raw_retrievals").fetchone()[0] == 0


def test_retrieval_collision_cannot_rewrite_time(tmp_path):
    with Store(tmp_path / "source") as source, Store(tmp_path / "target") as target:
        raw = make_raw(source)
        target_raw = make_raw(target, retrieved="2026-10-10T00:00:00Z")
        target.db.execute("INSERT INTO raw_retrievals SELECT ?,raw_id,source,url,retrieved_at,metadata_json FROM raw_retrievals WHERE retrieval_id=?", (raw["retrieval_id"], target_raw["retrieval_id"]))
        target.db.commit()
        with pytest.raises(ValueError, match="identity collision"):
            import_raw_cache(target, source.root)
        assert target.db.execute("SELECT retrieved_at FROM raw_retrievals WHERE retrieval_id=?", (raw["retrieval_id"],)).fetchone()[0].startswith("2026-10-10")


def test_import_cli_obeys_local_collector_lock(tmp_path):
    with Store(tmp_path / "source") as source:
        make_raw(source)
    with collector_lock(tmp_path / "target") as acquired:
        assert acquired
        with pytest.raises(RuntimeError, match="busy"):
            import_main(["--source", str(tmp_path / "source"), "--target", str(tmp_path / "target")])


def test_real_engine_budget_resume_replay_and_new_issuer_without_network(tmp_path):
    """Actual Queue/engine/Store integration; synthetic source, never real SEC evidence."""
    from equity_data.adapters.base import BaseAdapter
    from equity_data.engine import run_cycle
    from equity_data.budgets import Budget
    class Synthetic(BaseAdapter):
        source_id = "fixture"
        scope_kind = "company"
        data_types = ("filings", "documents", "financial_facts")
        def discover(self, ctx, scope):
            for i in range(3):
                ctx.emit({"source_id": self.source_id, "resource_key": scope["company_id"] + ":" + str(i),
                          "company_id": scope["company_id"], "kind": "document", "data_type": "documents",
                          "url": "https://example.invalid/" + scope["company_id"] + "/" + str(i),
                          "metadata": {"refresh": False, "immutable_archive": True}})
            return {"enumeration_complete": True}
        def acquire(self, ctx, resource):
            prior = ctx.store.latest_raw(self.source_id, resource["url"])
            if prior:
                return Path(prior["path"]).read_bytes(), prior
            content = resource["resource_key"].encode()
            return content, ctx.store.put_raw(content, source=self.source_id, url=resource["url"], retrieved_at="2024-05-01T10:00:00Z")
        def parse(self, ctx, resource, content, raw):
            record = ctx.store.put_record("documents", resource["resource_key"], {"text": content.decode()},
                source=self.source_id, raw_id=raw["raw_id"], company_id=resource["company_id"], parser_version="fixture")
            return {"status": "parsed", "record_ids": [record]}
    adapter = Synthetic()
    conf = config(universe={"mode": "configured", "symbols": []}, securities=[{"cik": "123", "company_id": "sec:0000000123"}],
                  initialization={"sources": ["fixture"]}, budget={"max_tasks": 2})
    def execute(store, local, args):
        return run_cycle(store, SimpleNamespace(budget=Budget(local["budget"])), local, {"fixture": adapter}, max_tasks=2)
    with Store(tmp_path) as store:
        first = run_initialization_batch(store, conf, execute_fn=execute)
        assert first["run"]["stop_reason"] == "max_tasks"
        assert first["initialization"]["state"] == "in_progress"
        second = run_initialization_batch(store, conf, execute_fn=execute)
        assert second["initialization"]["state"] == "initialized"
        before = tuple(store.db.execute("SELECT (SELECT COUNT(*) FROM records),(SELECT COUNT(*) FROM raw_retrievals),(SELECT COUNT(*) FROM machine_tasks)").fetchone())
        assert run_initialization_batch(store, conf, execute_fn=execute)["run"] is None
        assert tuple(store.db.execute("SELECT (SELECT COUNT(*) FROM records),(SELECT COUNT(*) FROM raw_retrievals),(SELECT COUNT(*) FROM machine_tasks)").fetchone()) == before
        new = copy.deepcopy(conf)
        new["securities"].append({"cik": "456", "company_id": "sec:0000000456"})
        run_initialization_batch(store, new, execute_fn=execute)
        final = run_initialization_batch(store, new, execute_fn=execute)
        assert final["initialization"]["state"] == "initialized"
        assert {r["company_id"] for r in store.query("documents")} == {"sec:0000000123", "sec:0000000456"}


def test_failed_parse_is_not_finished_initialization_despite_saved_raw(tmp_path):
    from equity_data.queue import Queue
    with Store(tmp_path) as store:
        Queue(store)
        def execute(*a):
            result = successful(*a, parsed=False)
            result["remaining_tasks"] = {"failed": 1}
            return result
        result = run_initialization_batch(store, config(), execute_fn=execute)
        assert not result["initialization"]["acquisition_complete"]


def test_performance_index_migration_preserves_all_evidence_and_query_results(tmp_path):
    """Upgrade an old-shaped DB; index creation must not alter evidence/version rows."""
    with Store(tmp_path) as store:
        raw = make_raw(store)
        for version in range(2):
            store.put_record("financial_facts", "versioned-key", {"value": version, "resource_key": "resource-A"},
                             source="sec_edgar", raw_id=raw["raw_id"], parser_version="fixture")
        store.put_record("documents", "other-key", {"resource_key": "resource-B"}, source="sec_edgar", raw_id=raw["raw_id"])
        store.db.execute("DROP INDEX retrieval_raw_source_time")
        store.db.execute("DROP INDEX records_source_resource")
        store.db.commit()
        tables = ("raw_blobs", "raw_retrievals", "records", "record_evidence", "record_validations", "availability_constraints")
        before = {t: [dict(r) for r in store.db.execute("SELECT * FROM " + t + " ORDER BY rowid")] for t in tables}
        sql = "SELECT r.* FROM records r WHERE source=? AND json_extract(data_json,'$.resource_key')=? AND sequence=(SELECT MAX(sequence) FROM records r2 WHERE r2.source=r.source AND r2.dataset=r.dataset AND r2.record_key=r.record_key)"
        params = ("sec_edgar", "resource-A")
        expected = [dict(r) for r in store.db.execute(sql, params)]
        assert len(expected) == 1 and json.loads(expected[0]["data_json"])["value"] == 1
    with Store(tmp_path) as upgraded:
        assert {t: [dict(r) for r in upgraded.db.execute("SELECT * FROM " + t + " ORDER BY rowid")] for t in tables} == before
        assert [dict(r) for r in upgraded.db.execute(sql, params)] == expected
        plan = " ".join(r[3] for r in upgraded.db.execute("EXPLAIN QUERY PLAN " + sql, params))
        assert "records_source_resource" in plan and "SCAN r" not in plan
        raw_sql = "SELECT * FROM raw_retrievals WHERE raw_id=? AND source=? ORDER BY retrieved_at DESC,rowid DESC LIMIT 1"
        raw_plan = " ".join(r[3] for r in upgraded.db.execute("EXPLAIN QUERY PLAN " + raw_sql, (raw["raw_id"], "sec_edgar")))
        assert "retrieval_raw_source_time" in raw_plan and "TEMP B-TREE" not in raw_plan


def test_machine_directory_actual_budget_retains_resumable_initialization(tmp_path, monkeypatch):
    """Use real Budget + machine + engine; replace only the remote directory boundary."""
    def budgeted_directory(store, client, conf):
        client.budget.before_request("sec_directory")
        pytest.fail("Zero request allowance must stop before HTTP")
    monkeypatch.setattr("equity_data.machine.resolve_universe", budgeted_directory)
    with Store(tmp_path) as store:
        conf = config(budget={"max_requests": 0})
        result = run_initialization_batch(store, conf)
        assert "Request budget exhausted" in result["run"]["stop_reason"]
        assert not result["run"]["execution_complete"]
        assert result["run"]["network_budget"]["requests"] == 0
        assert result["initialization"]["state"] == "in_progress"
        assert next_pending_plan(store, conf)["start"] == "2024-01-01"
        saved = store.db.execute("SELECT state,summary_json FROM machine_runs WHERE run_id=?", (result["run"]["run_id"],)).fetchone()
        assert saved["state"] == "budget_limited"
        assert json.loads(saved["summary_json"])["stop_reason"] == result["run"]["stop_reason"]
        for scope in result["run"]["source_scope_checks"]:
            assert not scope["progress"]["complete_check"]
            assert scope["last_complete_check_at"] is None


def test_machine_directory_403_is_dependency_failure_not_budget_continuation(tmp_path, monkeypatch):
    import requests
    calls = []
    def denied_directory(*args):
        calls.append(True)
        raise requests.HTTPError("HTTP 403")
    monkeypatch.setattr("equity_data.machine.resolve_universe", denied_directory)
    with Store(tmp_path) as store:
        result = run_initialization_batch(store, config())
        assert result["run"]["stop_reason"] is None
        assert result["initialization"]["batches"][0]["state"] == "blocked"
        assert next_pending_plan(store, config()) is None
        assert run_initialization_batch(store, config())["run"] is None
        assert len(calls) == 1
