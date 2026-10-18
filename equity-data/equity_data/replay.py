"""Verified raw-cache import; keeps original acquisition evidence unchanged.

Only raw_blobs, raw_retrievals and missing source registrations are imported.
No research records, tasks, success clocks or completeness claims are copied.
Run ordinary historical initialization afterward to enumerate and parse.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from .local_control import collector_lock
from .queue import exclusive_worker
from .store import Store, _json, now_utc


def _digest(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _safe_path(root, name):
    path = Path(name)
    if not path.is_absolute():
        path = root / path
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source raw path escapes source storage")
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p.is_relative_to(root)):
        raise ValueError("Source raw path uses symlink")
    return path


def _copy_blob(source, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _digest(destination) != expected:
            raise ValueError("Target raw cache hash/size conflict")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".raw-import-", delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as src:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        if _digest(temporary) != expected:
            raise ValueError("Source raw changed while importing")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if _digest(destination) != expected:
                raise ValueError("Target raw conflict during copy")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def import_raw_cache(target_store, source_root, *, sources=None):
    """Caller holds collector_lock(target); this function acquires engine lock.

    A read-only SQLite backup provides a stable source metadata snapshot, including
    WAL commits. Exact retrieval IDs/timestamps/metadata and blob created_at are
    retained. A separate import event records today's transfer time.
    """
    root = Path(source_root).resolve()
    if root == target_store.root.resolve():
        raise ValueError("Source and target storage must be different")
    source_path = root / "data.sqlite3"
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        source.backup(snapshot)
    finally:
        source.close()
    try:
        if [r[0] for r in snapshot.execute("PRAGMA quick_check")] != ["ok"]:
            raise ValueError("Source SQLite quick_check failed")
        selected = sorted(set(sources or [r[0] for r in snapshot.execute("SELECT DISTINCT source FROM raw_retrievals")]))
        if not selected:
            raise ValueError("No source selected for raw import")
        marks = ",".join("?" for _ in selected)
        retrievals = [dict(r) for r in snapshot.execute("SELECT * FROM raw_retrievals WHERE source IN (" + marks + ") ORDER BY retrieved_at,rowid", selected)]
        blobs = {r["raw_id"]: dict(r) for r in snapshot.execute("SELECT DISTINCT b.* FROM raw_blobs b JOIN raw_retrievals r USING(raw_id) WHERE r.source IN (" + marks + ")", selected)}
        paths = {}
        for raw_id, blob in blobs.items():
            if raw_id != "sha256:" + blob["sha256"]:
                raise ValueError("Source raw ID does not match digest")
            path = _safe_path(root, blob["path"])
            if _digest(path) != (blob["sha256"], blob["byte_size"]):
                raise ValueError("Source raw fails SHA-256/size verification: " + raw_id)
            paths[raw_id] = path
        registrations = {r["source"]: dict(r) for r in snapshot.execute("SELECT * FROM sources WHERE source IN (" + marks + ")", selected)}
        manifest = {"blobs": sorted((b["raw_id"], b["sha256"], b["byte_size"], b["created_at"]) for b in blobs.values()),
                    "retrievals": retrievals, "source_registrations": registrations}
        fingerprint = hashlib.sha256(_json(manifest).encode()).hexdigest()
        with exclusive_worker(target_store):
            target_store.db.executescript("""CREATE TABLE IF NOT EXISTS raw_import_events (
                import_id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, source_root TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL, sources_json TEXT NOT NULL, result_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS raw_import_provenance (
                import_id TEXT NOT NULL, retrieval_id TEXT NOT NULL, original_retrieved_at TEXT NOT NULL,
                PRIMARY KEY(import_id,retrieval_id));""")
            target_store.db.commit()
            before_records = target_store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            # Detect every identifier conflict before any database insertion.
            for row in retrievals:
                old = target_store.db.execute("SELECT * FROM raw_retrievals WHERE retrieval_id=?", (row["retrieval_id"],)).fetchone()
                if old is not None and dict(old) != row:
                    raise ValueError("Retrieval identity collision; refusing to rewrite evidence")
            for raw_id, blob in blobs.items():
                old = target_store.db.execute("SELECT * FROM raw_blobs WHERE raw_id=?", (raw_id,)).fetchone()
                if old is not None and (old["sha256"], old["byte_size"]) != (blob["sha256"], blob["byte_size"]):
                    raise ValueError("Raw identity collision")
                relative = Path("raw") / blob["sha256"][:2] / blob["sha256"]
                _copy_blob(paths[raw_id], target_store.root / relative, (blob["sha256"], blob["byte_size"]))
            import_id, when = str(uuid.uuid4()), now_utc()
            added_blobs = added_retrievals = 0
            with target_store.db:
                target_store.db.execute("BEGIN IMMEDIATE")
                for row in registrations.values():
                    target_store.db.execute("INSERT OR IGNORE INTO sources(source,metadata_json,updated_at) VALUES(?,?,?)", tuple(row[k] for k in ("source", "metadata_json", "updated_at")))
                for blob in blobs.values():
                    relative = (Path("raw") / blob["sha256"][:2] / blob["sha256"]).as_posix()
                    cursor = target_store.db.execute("INSERT OR IGNORE INTO raw_blobs VALUES(?,?,?,?,?,?)", (blob["raw_id"], blob["sha256"], relative, blob["byte_size"], blob["media_type"], blob["created_at"]))
                    added_blobs += cursor.rowcount
                for row in retrievals:
                    cursor = target_store.db.execute("INSERT OR IGNORE INTO raw_retrievals VALUES(?,?,?,?,?,?)", tuple(row[k] for k in ("retrieval_id", "raw_id", "source", "url", "retrieved_at", "metadata_json")))
                    added_retrievals += cursor.rowcount
                    target_store.db.execute("INSERT INTO raw_import_provenance VALUES(?,?,?)", (import_id, row["retrieval_id"], row["retrieved_at"]))
                result = {"import_id": import_id, "imported_at": when, "manifest_sha256": fingerprint,
                          "verified_blobs": len(blobs), "verified_retrievals": len(retrievals),
                          "added_blobs": added_blobs, "added_retrievals": added_retrievals,
                          "imported_bytes": sum(b["byte_size"] for b in blobs.values()), "network_requests": 0,
                          "original_retrieval_ids_and_times_preserved": True, "research_records_before": before_records,
                          "research_records_after": target_store.db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                          "acquisition_complete": False, "requires_discovery_and_replay": True,
                          "note": "Cache transfer is not a new upstream check, parse, or initialization completion."}
                target_store.db.execute("INSERT INTO raw_import_events VALUES(?,?,?,?,?,?)", (import_id, when, str(root), fingerprint, _json(selected), _json(result)))
            return result
    finally:
        snapshot.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--sources", help="Comma-separated source identifiers; default all raw sources")
    args = parser.parse_args(argv)
    with collector_lock(Path(args.target)) as acquired:
        if not acquired:
            raise RuntimeError("Collector busy; raw import did not run")
        with Store(args.target) as store:
            result = import_raw_cache(store, args.source, sources=args.sources.split(",") if args.sources else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
