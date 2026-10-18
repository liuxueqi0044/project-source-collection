"""Run configuration-only unseen-company acquisition against frozen production.

Acceptance orchestration, not an issuer-specific production rule. All identity,
endpoint, scope and evidence assertions are in the reviewed input manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urldefrag, urljoin

from equity_data.cli import load_config
from equity_data.competition.cli import import_bundle
from equity_data.competition.queries import query_competition
from equity_data.competition.runner import run_competition
from equity_data.competition.watch_planner import plan_watches
from equity_data.local_control import collector_lock
from equity_data.store import Store, now_utc


def freeze_check(base, freeze):
    current = {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in freeze["files"]}
    return {"checked_at": now_utc(), "frozen_at": freeze["frozen_at"], "file_count": len(current),
            "unchanged": current == freeze["files"], "mismatches": [name for name in current if current[name] != freeze["files"][name]],
            "files": current}


def inventory(store):
    datasets = {row[0]: row[1] for row in store.db.execute("SELECT dataset,COUNT(*) FROM records GROUP BY dataset")}
    stages = {row[0]: row[1] for row in store.db.execute("SELECT json_extract(data_json,'$.fact_stage'),COUNT(*) FROM records WHERE dataset='competitive_events' GROUP BY 1")}
    return {"record_versions": datasets, "semantic_stages": stages,
            "raw_blobs": store.db.execute("SELECT COUNT(*) FROM raw_blobs").fetchone()[0],
            "raw_bytes": store.db.execute("SELECT COALESCE(SUM(byte_size),0) FROM raw_blobs").fetchone()[0],
            "retrievals": store.db.execute("SELECT COUNT(*) FROM raw_retrievals").fetchone()[0]}


def reference_assessment(store, entry_url):
    """Independent acceptance DOM inventory, not the production link selector.

    The evaluator inspected the original list's publisher title-card structure.
    This reference is assembled after execution from the pre-refresh saved copy;
    it is independently enumerated, not a blinded semantic benchmark.
    """
    from bs4 import BeautifulSoup
    raw = dict(store.db.execute("SELECT r.*,b.path,b.sha256 FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.source='competition_web' AND r.url=? ORDER BY r.retrieved_at LIMIT 1", (entry_url,)).fetchone())
    content = (store.root / raw["path"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != raw["sha256"]:
        raise ValueError("Independent reference source hash mismatch")
    titles = BeautifulSoup(content, "html.parser").select("div.wd_title a[href]")
    snapshots = {row["data"]["url"]: row for row in store.query("competition_snapshots")}
    references = []
    for index, title in enumerate(titles, 1):
        url = urldefrag(urljoin(entry_url, title["href"]))[0]
        saved = store.db.execute("SELECT retrieval_id,raw_id,retrieved_at FROM raw_retrievals WHERE source='competition_web' AND url=? ORDER BY retrieved_at LIMIT 1", (url,)).fetchone()
        snapshot = snapshots.get(url)
        references.append({"url": url, "title": title.get_text(" ", strip=True), "reference_locator": "css:div.wd_title a[href=" + json.dumps(title["href"]) + "]", "title_card_order": index,
                           "raw_acquired": saved is not None, "first_original": dict(saved) if saved else None,
                           "snapshot_version_id": snapshot["record_id"] if snapshot else None,
                           "valid_normalized_text": bool(snapshot and snapshot["data"].get("valid")),
                           "publication_precision": snapshot["data"].get("publication_precision") if snapshot else None})
    gaps = [dict(row) for row in store.db.execute("SELECT code,message,details_json,first_seen_at,last_seen_at FROM machine_gaps WHERE resolved_at IS NULL")]
    return {"created_at": now_utc(), "basis": "Publisher title cards in earliest saved list, independently read with div.wd_title; production URL selector not reused.",
            "limitations": ["Single current first page; no archive or pagination denominator.", "Assembled after the run from the pre-refresh saved original, not blinded pre-registered ground truth.", "Acquisition generalization only; no semantic precision/recall denominator established."],
            "reference_original": {key: raw[key] for key in ("raw_id", "sha256", "retrieval_id", "retrieved_at", "url")},
            "expected_articles": len(references), "acquired_articles": sum(row["raw_acquired"] for row in references),
            "valid_text_snapshots": sum(row["valid_normalized_text"] for row in references), "references": references,
            "pending_gaps": gaps}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/competition-unseen.toml")
    parser.add_argument("--manifest", default="configs/competition-unseen.json")
    parser.add_argument("--freeze", default="reports/v5/production-freeze.json")
    parser.add_argument("--output", default="reports/v5/unseen-company")
    parser.add_argument("--stage", choices=["import", "refresh", "repeat", "cadence", "audit"], required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    base = Path(config["_base_dir"])
    root = Path(config["storage"]).resolve()
    if root != (base / "data/competition-unseen").resolve():
        raise ValueError("Acceptance script only writes isolated data/competition-unseen")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    freeze = json.loads(Path(args.freeze).read_text(encoding="utf-8-sig"))
    check_before = freeze_check(base, freeze)
    if not check_before["unchanged"]:
        raise ValueError("Production freeze changed; unseen acquisition acceptance stopped")
    began = time.monotonic()
    report = {"stage": args.stage, "started_at": now_utc(), "freeze_before": check_before,
              "selection": "Parent-specified previously unused Workday; configured official pages inspected after production freeze; not a randomized semantic holdout.",
              "production_company_rules_modified": False, "counts_as_semantic_recall_acceptance": False}
    with collector_lock(root) as acquired:
        if not acquired:
            raise RuntimeError("Unseen collector already active")
        with Store(root) as store:
            report["before"] = inventory(store)
            if args.stage == "import":
                report["result"] = import_bundle(store, json.loads(Path(args.manifest).read_text(encoding="utf-8-sig")), config)
            elif args.stage in {"refresh", "repeat", "cadence"}:
                report["result"] = run_competition(store, config, trigger="unseen_acceptance_" + args.stage, force=args.stage != "cadence")
            else:
                report["result"] = {"plan": plan_watches(store, config),
                    "queries": [query_competition(store, target) for target in config["competition"]["target_ids"]]}
                manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
                reference = reference_assessment(store, manifest["endpoints"][0]["url"])
                (output / "independent-reference.json").write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")
                report["independent_reference"] = {key: reference[key] for key in ("expected_articles", "acquired_articles", "valid_text_snapshots", "limitations")}
            report["after"] = inventory(store)
            report["originals"] = [dict(row) for row in store.db.execute("SELECT r.retrieval_id,r.raw_id,r.source,r.url,r.retrieved_at,b.sha256,b.byte_size,b.path FROM raw_retrievals r JOIN raw_blobs b USING(raw_id) WHERE r.source='competition_web' ORDER BY r.retrieved_at")]
    report.update(finished_at=now_utc(), elapsed_seconds=round(time.monotonic() - began, 3), freeze_after=freeze_check(base, freeze))
    target = output / (args.stage + "-" + report["started_at"].replace(":", "").replace(".", "-") + ".json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), "before": report["before"], "after": report["after"], "production_unchanged": report["freeze_after"]["unchanged"], "elapsed_seconds": report["elapsed_seconds"]}, indent=2))
    return 0 if report["freeze_after"]["unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
