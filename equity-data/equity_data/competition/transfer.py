"""Transfer current competition configuration and immutable evidence, not progress.

The source database is read-only. Target configuration is revalidated and processed
now; original retrieval IDs/times survive. No observations, jobs, financial facts,
success watermarks, or coverage claims are copied.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

from ..local_control import collector_lock
from ..replay import _copy_blob, _digest, _safe_path
from ..store import Store, _json, now_utc
from .cli import import_bundle
from .evidence import validate_evidence

SECTIONS = {"entities": "entities", "entity_links": "entity_links",
            "competition_edges": "relations", "watch_endpoints": "endpoints"}
RETRIEVAL_KEYS = ("retrieval_id", "raw_id", "source", "url", "retrieved_at", "metadata_json")


def _source_store(root):
    store = Store.__new__(Store)
    store.root = root
    store.db = sqlite3.connect((root / "data.sqlite3").as_uri() + "?mode=ro", uri=True)
    store.db.row_factory = sqlite3.Row
    store.db.execute("PRAGMA query_only=ON")
    store.db.execute("BEGIN")
    return store


def _prepare(source):
    records, blobs, retrievals, registrations, paths, detached = [], {}, {}, {}, {}, []

    def evidence(value):
        if isinstance(value, list):
            return [evidence(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "raw_id" in value:
            checked = validate_evidence(source, [value])[0]
            raw_id, retrieval_id = checked["raw_id"], checked["retrieval_id"]
            blob = dict(source.db.execute("SELECT * FROM raw_blobs WHERE raw_id=?", (raw_id,)).fetchone())
            path = _safe_path(source.root, blob["path"])
            if raw_id != "sha256:" + blob["sha256"] or _digest(path) != (blob["sha256"], blob["byte_size"]):
                raise ValueError("Source raw fails SHA-256/size verification")
            retrieval = dict(source.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (retrieval_id,)).fetchone())
            registration = source.db.execute("SELECT * FROM sources WHERE source=?", (retrieval["source"],)).fetchone()
            if registration is None:
                raise ValueError("Evidence source registration is missing")
            blobs[raw_id], paths[raw_id], retrievals[retrieval_id] = blob, path, retrieval
            registrations[retrieval["source"]] = dict(registration)
            # Referenced research records are intentionally NOT copied. Retain
            # their origin as provenance, never as a target-store record pointer.
            if checked.get("record_id"):
                identity = checked.pop("record_id")
                original = dict(source.db.execute("SELECT * FROM records WHERE record_id=?", (identity,)).fetchone())
                provenance = {key: original[key] for key in ("record_id", "dataset", "record_key", "created_at", "available_at", "availability_basis", "published_at", "publication_precision")}
                checked["source_record_provenance"] = provenance
                if provenance not in detached:
                    detached.append(provenance)
            return checked
        return {key: evidence(item) for key, item in value.items()}

    for dataset, section in SECTIONS.items():
        rows = source.db.execute("SELECT * FROM records WHERE source='competition' AND dataset=? AND sequence IN (SELECT MAX(sequence) FROM records WHERE source='competition' AND dataset=? GROUP BY record_key) ORDER BY record_key", (dataset, dataset)).fetchall()
        for row in rows:
            original = dict(row)
            value = evidence(json.loads(original["data_json"]))
            # Writers establish the target entity versions; never preserve a
            # source-store dependency as though it already existed in target.
            dependencies = {}
            for key in ("entity_version_ids", "supporting_record_ids", "review_of_version_id"):
                if key in value:
                    dependencies[key] = value.pop(key)
            records.append({"dataset": dataset, "section": section, "source_record_id": original["record_id"],
                            "source_created_at": original["created_at"], "source_available_at": original["available_at"],
                            "record_key": original["record_key"], "source_reviewer": value.get("reviewer"),
                            "source_dependencies": dependencies, "data": value})
    fingerprint = hashlib.sha256(_json({"records": records, "blobs": blobs, "retrievals": retrievals}).encode()).hexdigest()
    return {"records": records, "blobs": blobs, "retrievals": retrievals, "registrations": registrations,
            "paths": paths, "detached_source_records": detached, "manifest_sha256": fingerprint}


def _copy_evidence(store, prepared):
    # Preflight every identity before committing any source evidence. Existing
    # source registrations remain local, with differing metadata in the receipt.
    conflicts = []
    for identity, row in prepared["retrievals"].items():
        old = store.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (identity,)).fetchone()
        if old is not None and dict(old) != row:
            raise ValueError("Retrieval identity collision; refusing to rewrite evidence")
    for identity, row in prepared["blobs"].items():
        old = store.db.execute("SELECT * FROM raw_blobs WHERE raw_id=?", (identity,)).fetchone()
        if old is not None:
            if (old["sha256"], old["byte_size"]) != (row["sha256"], row["byte_size"]):
                raise ValueError("Raw identity collision")
            if _digest(_safe_path(store.root, old["path"])) != (row["sha256"], row["byte_size"]):
                raise ValueError("Target raw cache hash/size conflict")
    for name, row in prepared["registrations"].items():
        old = store.db.execute("SELECT * FROM sources WHERE source=?", (name,)).fetchone()
        if old is not None and dict(old) != row:
            conflicts.append({"source": name, "action": "kept_existing_target_registration", "original_registration": row})
    for identity, row in prepared["blobs"].items():
        relative = Path("raw") / row["sha256"][:2] / row["sha256"]
        destination = _safe_path(store.root, relative)
        _copy_blob(prepared["paths"][identity], destination, (row["sha256"], row["byte_size"]))
    added_blobs = added_retrievals = 0
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        for row in prepared["registrations"].values():
            store.db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?)", tuple(row[key] for key in ("source", "metadata_json", "updated_at")))
        for row in prepared["blobs"].values():
            relative = (Path("raw") / row["sha256"][:2] / row["sha256"]).as_posix()
            added_blobs += store.db.execute("INSERT OR IGNORE INTO raw_blobs VALUES(?,?,?,?,?,?)", (row["raw_id"], row["sha256"], relative, row["byte_size"], row["media_type"], row["created_at"])).rowcount
        for row in prepared["retrievals"].values():
            added_retrievals += store.db.execute("INSERT OR IGNORE INTO raw_retrievals VALUES(?,?,?,?,?,?)", tuple(row[key] for key in RETRIEVAL_KEYS)).rowcount
    return {"added_original_blobs": added_blobs, "added_original_retrievals": added_retrievals,
            "source_registration_differences": conflicts}


def transfer_configuration(source_root, config, *, dry_run=False, reviewer="agent:configuration-transfer"):
    """Revalidate current config, preserving all uncertainty and review labels.

    Existing target keys receive append-only revisions, never deletion. Import
    failures produce a partial receipt; retry after repair is safe and idempotent.
    """
    source_root, target_root = Path(source_root).resolve(), Path(config["storage"]).resolve()
    if source_root == target_root:
        raise ValueError("Source and target storage must be different")
    source = _source_store(source_root)
    try:
        prepared = _prepare(source)
        receipt = {"started_at": now_utc(), "source_root": str(source_root), "target_root": str(target_root),
                   "source_database_access": "read_only_snapshot", "dry_run": dry_run, "reviewer": reviewer,
                   "manifest_sha256": prepared["manifest_sha256"], "network_requests": 0,
                   "original_retrieval_ids_and_times_preserved": True, "original_processing_time_copied": False,
                   "copied_progress_or_financial_records": False, "latest_upstream_confirmed": False,
                   "inventory": {dataset: sum(row["dataset"] == dataset for row in prepared["records"]) for dataset in SECTIONS},
                   "evidence": {"verified_blobs": len(prepared["blobs"]), "verified_retrievals": len(prepared["retrievals"]),
                                "bytes": sum(row["byte_size"] for row in prepared["blobs"].values()),
                                "original_retrievals": list(prepared["retrievals"].values())},
                   "detached_source_records": prepared["detached_source_records"], "records": [], "failures": [],
                   "limitations": ["New target review/processing timestamps are assigned by import_bundle.",
                                   "A copied original is not a successful refresh, completed parse, or coverage result.",
                                   "Detached source record availability is provenance only; raw-only historical evidence remains conservative."]}
        if dry_run:
            receipt["status"] = "validated_inventory_only"
            receipt["records"] = [{key: value for key, value in row.items() if key != "data"} for row in prepared["records"]]
            return receipt
        with collector_lock(target_root) as acquired:
            if not acquired:
                raise RuntimeError("Collector active; configuration transfer was not started")
            with Store(target_root) as store:
                before = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                receipt.update(_copy_evidence(store, prepared))
                local_config = copy.deepcopy(config)
                local_config.setdefault("competition", {})["budget"] = {"max_requests": 0, "max_bytes": 0, "max_runtime_seconds": 300}
                for row in prepared["records"]:
                    old = store.db.execute("SELECT record_id FROM records WHERE source='competition' AND dataset=? AND record_key=? ORDER BY sequence DESC LIMIT 1", (row["dataset"], row["record_key"])).fetchone()
                    result = import_bundle(store, {"sources": [], "reviewer": reviewer, row["section"]: [row["data"]]}, local_config)
                    receipt["failures"].extend({"source_record_id": row["source_record_id"], **failure} for failure in result["failures"])
                    for imported in result["records"]:
                        target = store.db.execute("SELECT created_at,available_at FROM records WHERE record_id=?", (imported["record_id"],)).fetchone()
                        receipt["records"].append({**{key: value for key, value in row.items() if key != "data"},
                                                   "target_record_id": imported["record_id"], "target_created_at": target["created_at"],
                                                   "target_available_at": target["available_at"], "new_version": old is None or old[0] != imported["record_id"]})
                receipt["new_business_records"] = store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0] - before
                receipt["status"] = "partial" if receipt["failures"] else "configuration_imported"
                receipt["finished_at"] = now_utc()
        return receipt
    finally:
        source.db.close()


def main(argv=None):
    from ..cli import load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reviewer", default="agent:configuration-transfer")
    args = parser.parse_args(argv)
    try:
        receipt = transfer_configuration(args.source, load_config(args.config), dry_run=args.dry_run, reviewer=args.reviewer)
    except Exception as exc:
        receipt = {"status": "failed", "error": str(exc), "at": now_utc(), "network_requests": 0,
                   "note": "No claim of completion. Earlier committed configuration, if any, is append-only and safe to retry."}
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(receipt, ensure_ascii=False, indent=2)
    destination.write_text(text, encoding="utf-8")
    print(text)
    return 1 if receipt["status"] in {"failed", "partial"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
