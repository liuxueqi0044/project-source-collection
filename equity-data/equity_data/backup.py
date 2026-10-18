"""Verified local SQLite/raw backup and restore; no fetches or scheduling.

python -m equity_data.backup backup --storage data/live --destination backups/run1
python -m equity_data.backup verify --backup backups/run1
python -m equity_data.backup restore --backup backups/run1 --destination data/restored
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from types import SimpleNamespace

from .local_control import collector_lock
from .queue import exclusive_worker

SCHEMA = "equity-storage-backup-v1"
MANIFEST = "BACKUP_MANIFEST.json"
MANIFEST_HASH = "BACKUP_MANIFEST.sha256"
STATUS = "BACKUP_STATUS.json"
RESTORE_STATUS = "RESTORE_STATUS.json"
CHUNK = 1024 * 1024


class BackupBusy(RuntimeError):
    """An active collector prevents a consistent multi-database snapshot."""


class BackupInvalid(ValueError):
    """Missing, unsafe or corrupted backup evidence."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _sha(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _readonly(path, *, immutable=False):
    # Source storage may have committed WAL data, so immutable is snapshots only.
    suffix = "?mode=ro" + ("&immutable=1" if immutable else "")
    connection = sqlite3.connect(path.resolve().as_uri() + suffix, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _quickcheck(connection, label):
    messages = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if messages != ["ok"]:
        raise BackupInvalid(f"SQLite quick_check failed: {label}: {messages[:10]}")


def _inside(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise BackupInvalid("Unsafe backup path")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or not parsed.parts or any(part in {".", ".."} for part in parsed.parts):
        raise BackupInvalid("Unsafe backup path: " + relative)
    path = root.joinpath(*parsed.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise BackupInvalid("Backup path escapes root: " + relative)
    parents = [path]
    cursor = path.parent
    while cursor != root and cursor.is_relative_to(root):
        parents.append(cursor)
        cursor = cursor.parent
    if any(parent.is_symlink() for parent in parents):
        raise BackupInvalid("Backup file paths must not use symlinks: " + relative)
    return path


def _copy_verified(source, destination, sha256, byte_size):
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with source.open("rb") as src, destination.open("xb") as dst:
        for chunk in iter(lambda: src.read(CHUNK), b""):
            dst.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != sha256 or size != byte_size:
        raise BackupInvalid("File hash or size mismatch: " + str(source))


def _fresh_directory(destination, allow_empty=False):
    destination = Path(destination).resolve()
    if destination.exists():
        if not allow_empty or not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError("Destination must be new" + (" or empty" if allow_empty else "") + ": " + str(destination))
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


@contextmanager
def _collector_guards(storage):
    # Match the collector's lock order. Neither adapter causes Store migration.
    with collector_lock(storage) as owned:
        if not owned:
            raise BackupBusy("Local collector is busy; no mixed snapshot was made")
        engine_guard = exclusive_worker(SimpleNamespace(root=storage))
        try:
            engine_guard.__enter__()
        except RuntimeError as exc:
            raise BackupBusy("Acquisition engine is busy; no mixed snapshot was made") from exc
        try:
            yield
        finally:
            engine_guard.__exit__(None, None, None)


def create_backup(storage, destination):
    storage = Path(storage).resolve()
    destination_path = Path(destination).resolve()
    if not storage.is_dir() or not (storage / "data.sqlite3").is_file():
        raise FileNotFoundError("Storage must contain an existing data.sqlite3")
    if destination_path.is_relative_to(storage):
        raise ValueError("Choose a backup destination outside the source storage")
    destination = _fresh_directory(destination_path)
    started = _now()
    _write_json(destination / STATUS, {"state": "creating", "started_at": started, "source_storage": str(storage)})
    try:
        with _collector_guards(storage):
            names = ["data.sqlite3"] + [name for name in ("local-control.sqlite3", "control.sqlite3") if (storage / name).is_file()]
            database_checks = {}
            for name in names:
                with closing(_readonly(storage / name)) as source:
                    _quickcheck(source, name)
                    with closing(sqlite3.connect(destination / name)) as snapshot:
                        source.backup(snapshot)
                        _quickcheck(snapshot, name + " snapshot")
                database_checks[name] = {"sqlite_backup": "complete", "quick_check": "ok"}
            raw_refs = []
            with closing(sqlite3.connect(destination / "data.sqlite3")) as snapshot:
                snapshot.row_factory = sqlite3.Row
                if not snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_blobs'").fetchone():
                    raise BackupInvalid("Main database has no raw_blobs catalog")
                for raw in snapshot.execute("SELECT raw_id,sha256,path,byte_size FROM raw_blobs ORDER BY raw_id").fetchall():
                    if not re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]):
                        raise BackupInvalid("Invalid raw hash in database: " + str(raw["raw_id"]))
                    relative = raw["path"]
                    if not isinstance(relative, str) or not relative.startswith("raw/"):
                        raise BackupInvalid("Raw paths must be safe storage-relative raw/ paths; source database was not changed")
                    original = _inside(storage, relative)
                    target = _inside(destination, relative)
                    if target.exists():
                        if _sha(target) != (raw["sha256"], raw["byte_size"]):
                            raise BackupInvalid("Conflicting duplicate raw hash")
                    else:
                        _copy_verified(original, target, raw["sha256"], raw["byte_size"])
                    raw_refs.append({"raw_id": raw["raw_id"], "sha256": raw["sha256"], "bytes": raw["byte_size"],
                                     "backup_path": relative, "original_path": raw["path"]})
                _quickcheck(snapshot, "main snapshot")
            files = []
            for name in names:
                digest, size = _sha(destination / name)
                files.append({"path": name, "sha256": digest, "bytes": size, "kind": "sqlite_snapshot"})
            for item in {r["backup_path"]: r for r in raw_refs}.values():
                files.append({"path": item["backup_path"], "sha256": item["sha256"], "bytes": item["bytes"], "kind": "raw_evidence"})
            manifest = {"schema": SCHEMA, "created_at": _now(), "started_at": started,
                        "source_storage": str(storage), "lock_order": ["local_collector", "acquisition_engine"],
                        "consistency": "collectors stopped by shared locks; each SQLite snapshot atomic; control request enqueue may continue, so this is not a cross-database transaction",
                        "files": sorted(files, key=lambda item: item["path"]), "raw_references": raw_refs,
                        "database_checks": database_checks,
                        "timestamp_policy": "original publication, acquisition, record and control timestamps preserved; backup time is separate",
                        "portability_policy": "storage-relative raw paths and all database rows/triggers unchanged",
                        "exclusions": ["unreferenced loose files", "WAL/SHM sidecars (committed data included through SQLite backup)", "lock files", "configs and Windows scheduled tasks"],
                        "restore_requires_live_check": True}
            _write_json(destination / MANIFEST, manifest)
            manifest_digest, _ = _sha(destination / MANIFEST)
            (destination / MANIFEST_HASH).write_text(manifest_digest + "\n", encoding="ascii")
            _write_json(destination / STATUS, {"state": "complete", "completed_at": _now(), "manifest_sha256": manifest_digest})
            verified = verify_backup(destination)
        return {"state": "complete", "backup": str(destination), **verified}
    except Exception as exc:
        _write_json(destination / STATUS, {"state": "busy" if isinstance(exc, BackupBusy) else "failed",
                                          "failed_at": _now(), "error_type": type(exc).__name__, "error": str(exc),
                                          "partial_files_preserved": True})
        raise


def verify_backup(backup):
    root = Path(backup).resolve()
    try:
        expected_hash = (root / MANIFEST_HASH).read_text(encoding="ascii").strip()
        payload = (root / MANIFEST).read_bytes()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or hashlib.sha256(payload).hexdigest() != expected_hash:
            raise BackupInvalid("Manifest SHA-256 mismatch")
        manifest = json.loads(payload)
        if manifest.get("schema") != SCHEMA:
            raise BackupInvalid("Unsupported backup schema")
        state = json.loads((root / STATUS).read_text(encoding="utf-8"))
        if state.get("state") != "complete" or state.get("manifest_sha256") != expected_hash:
            raise BackupInvalid("Backup does not have a matching completion marker")
        files = {}
        for item in manifest["files"]:
            path = _inside(root, item["path"])
            if item["kind"] not in {"sqlite_snapshot", "raw_evidence"}:
                raise BackupInvalid("Unknown manifest file kind")
            if item["kind"] == "sqlite_snapshot" and item["path"] not in {"data.sqlite3", "local-control.sqlite3", "control.sqlite3"}:
                raise BackupInvalid("Unexpected SQLite snapshot path")
            if item["kind"] == "raw_evidence" and not item["path"].startswith("raw/"):
                raise BackupInvalid("Raw evidence must be inside raw/")
            if item["path"] in files:
                raise BackupInvalid("Duplicate file path in manifest")
            if _sha(path) != (item["sha256"], item["bytes"]):
                raise BackupInvalid("Backup file hash/size mismatch: " + item["path"])
            files[item["path"]] = item
            if item["kind"] == "sqlite_snapshot":
                with closing(_readonly(path, immutable=True)) as connection:
                    _quickcheck(connection, item["path"])
        if files.get("data.sqlite3", {}).get("kind") != "sqlite_snapshot":
            raise BackupInvalid("Main database missing from manifest")
        with closing(_readonly(root / "data.sqlite3", immutable=True)) as connection:
            catalog = [dict(row) for row in connection.execute("SELECT raw_id,sha256,path,byte_size FROM raw_blobs ORDER BY raw_id")]
        reference_by_id = {row["raw_id"]: row for row in manifest["raw_references"]}
        if len(reference_by_id) != len(manifest["raw_references"]) or len(catalog) != len(reference_by_id):
            raise BackupInvalid("Raw reference count differs between manifest and database")
        for row in catalog:
            expected = reference_by_id.get(row["raw_id"], {})
            if (row["sha256"], row["path"], row["byte_size"]) != (expected.get("sha256"), expected.get("backup_path"), expected.get("bytes")):
                raise BackupInvalid("Raw reference differs between manifest and database")
            item = files.get(row["path"], {})
            if item.get("kind") != "raw_evidence" or (item.get("sha256"), item.get("bytes")) != (row["sha256"], row["byte_size"]):
                raise BackupInvalid("Referenced raw file is absent from manifest")
        return {"manifest_sha256": expected_hash, "verified_files": len(files), "raw_references": len(catalog),
                "sqlite_quick_check": "ok", "requires_live_check_after_restore": True}
    except (OSError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
        raise BackupInvalid("Cannot verify backup: " + str(exc)) from exc


def restore_backup(backup, destination):
    backup = Path(backup).resolve()
    target_path = Path(destination).resolve()
    if target_path == backup or target_path.is_relative_to(backup):
        raise ValueError("Restore destination must be outside the backup")
    destination = _fresh_directory(target_path, allow_empty=True)
    _write_json(destination / RESTORE_STATUS, {"state": "restoring", "started_at": _now(), "requires_live_check": True})
    try:
        verification = verify_backup(backup)
        payload = (backup / MANIFEST).read_bytes()
        # Hash the exact bytes used below, rather than opening the file twice.
        if hashlib.sha256(payload).hexdigest() != verification["manifest_sha256"]:
            raise BackupInvalid("Manifest changed during restore")
        manifest = json.loads(payload)
        for item in manifest["files"]:
            source, target = _inside(backup, item["path"]), _inside(destination, item["path"])
            _copy_verified(source, target, item["sha256"], item["bytes"])
            if item["kind"] == "sqlite_snapshot":
                with closing(_readonly(target, immutable=True)) as connection:
                    _quickcheck(connection, "restored " + item["path"])
        state = {"state": "restored_requires_live_check", "restored_at": _now(), "backup": str(backup),
                 "backup_manifest_sha256": verification["manifest_sha256"], "requires_live_check": True,
                 "historical_watermarks_are_current_freshness": False, "original_timestamps_preserved": True,
                 "new_fetches_created": 0, "automatic_schedule_changed": False,
                 "next_action": "Review restored queues and run a genuine source check/update before claiming current coverage or freshness"}
        _write_json(destination / RESTORE_STATUS, state)
        return {**state, "destination": str(destination), "verified_files": verification["verified_files"]}
    except Exception as exc:
        _write_json(destination / RESTORE_STATUS, {"state": "restore_failed", "failed_at": _now(),
                                                  "requires_live_check": True, "error_type": type(exc).__name__,
                                                  "error": str(exc), "partial_files_preserved": True})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--storage", required=True)
    create.add_argument("--destination", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.storage, args.destination)
        elif args.command == "verify":
            result = verify_backup(args.backup)
        else:
            result = restore_backup(args.backup, args.destination)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"state": "busy" if isinstance(exc, BackupBusy) else "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))
        return 3 if isinstance(exc, BackupBusy) else 1


if __name__ == "__main__":
    raise SystemExit(main())
