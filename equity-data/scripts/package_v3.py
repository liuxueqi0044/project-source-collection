"""Package a stopped collection workspace without changing source databases.

Usage (run only after acquisition has stopped):
    python scripts/package_v3.py
    python scripts/package_v3.py --output ../another-delivery-name.zip

SQLite backup snapshots include committed WAL contents while the disposable
WAL/SHM sidecars stay out of the delivery. Existing archives are never replaced.
No production modules are imported and no packaging happens on import.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
import zipfile

MANIFEST = "DELIVERY_MANIFEST_V3.json"
BLOCK_SIZE = 1024 * 1024
EXCLUDED_DIRECTORIES = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "node_modules"}
EXCLUSION_RULES = [
    "directories: __pycache__, .pytest_cache, .git, .venv, venv, node_modules",
    "Python bytecode: *.pyc, *.pyo",
    "private configuration: *.local.toml, .env, .env.* (except .env.example)",
    "locks: *.lock, *.lck; SQLite transient sidecars: *-wal, *-shm, *-journal",
    "symbolic links are excluded instead of following paths outside the project",
    "existing root DELIVERY_MANIFEST_V3.json is replaced with the new manifest, never hashed into itself",
    "destination ZIP, its partial archive, and this run's SQLite staging files are outside the source tree",
]


def exclusion(path: Path, root: Path):
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return "excluded_directory"
    if path.is_symlink():
        return "symlink_not_followed"
    name = path.name.lower()
    if name.endswith((".pyc", ".pyo")):
        return "python_bytecode"
    if name.endswith(".local.toml") or name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "private_configuration"
    if name.endswith((".lock", ".lck")):
        return "lock_file"
    if name.endswith(("-wal", "-shm", "-journal")):
        return "sqlite_transient_sidecar"
    if relative.as_posix() == MANIFEST:
        return "manifest_regenerated_not_self_hashed"
    return None


def inventory(root: Path):
    included, excluded = [], []
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        kept = []
        for name in sorted(dirs):
            child = parent / name
            reason = exclusion(child, root)
            if reason:
                excluded.append({"path": child.relative_to(root).as_posix() + "/", "reason": reason})
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = parent / name
            reason = exclusion(path, root)
            if reason:
                excluded.append({"path": path.relative_to(root).as_posix(), "reason": reason})
            elif path.is_file():
                included.append(path)
    return sorted(included), sorted(excluded, key=lambda item: item["path"])


def digest_file(path: Path):
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def is_sqlite(path: Path):
    if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        return False
    if path.suffix.lower() in {".sqlite", ".sqlite3"}:
        # Treat declared SQLite files as databases even if corrupt, so an
        # invalid header fails quick_check instead of escaping validation.
        return True
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def open_readonly(path: Path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.execute("PRAGMA query_only=ON")
    return connection


def inspect_database(connection, label: str):
    checks = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if checks != ["ok"]:
        raise RuntimeError(f"SQLite quick_check failed for {label}: {checks[:10]}")
    has_tasks = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='machine_tasks'").fetchone() is not None
    if has_tasks:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(machine_tasks)")}
        if "state" not in columns:
            raise RuntimeError(f"Unknown machine_tasks schema in {label}; cannot verify stopped state")
        running = connection.execute("SELECT COUNT(*) FROM machine_tasks WHERE state='running'").fetchone()[0]
        if running:
            raise RuntimeError(f"Refusing package: {label} contains {running} running task(s). Stop/recover collection first; no task state was changed.")
    return {"quick_check": "ok", "machine_tasks_present": has_tasks, "running_tasks": 0 if has_tasks else None}


@contextmanager
def existing_worker_guard(directory: Path):
    """Hold the collector's existing OS lock without rewriting its bytes."""
    path = directory / ".machine-worker.lock"
    if not path.exists():
        yield "no_existing_worker_lock; consistent_SQLite_backup_and_stopped_task_checks_used"
        return
    stream = path.open("r+b")
    locked = False
    try:
        if path.stat().st_size < 1:
            raise RuntimeError(f"Empty worker lock cannot be safely acquired: {path}")
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError(f"Acquisition worker is active in {directory}; package after it stops") from exc
        yield "existing_worker_lock_held_until_archive_validation"
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def package(project_root: Path, output: Path):
    root, output = project_root.resolve(), output.resolve()
    if not root.is_dir():
        raise ValueError(f"Project root does not exist: {root}")
    if output.is_relative_to(root):
        raise ValueError("Archive must be outside the source project directory")
    if output.exists():
        raise FileExistsError(f"Existing archive will not be replaced: {output}; choose another --output")
    output.parent.mkdir(parents=True, exist_ok=True)
    files, excluded = inventory(root)
    databases = [path for path in files if is_sqlite(path)]
    snapshots, database_checks, guards = {}, {}, {}
    partial = output.with_name(output.name + ".partial-" + uuid.uuid4().hex)
    with ExitStack() as stack:
        for directory in sorted({path.parent for path in databases}):
            guards[directory] = stack.enter_context(existing_worker_guard(directory))
        # The temporary directory is outside the project and has a verified
        # system-temporary parent; cleanup affects only this run's own files.
        temporary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="equity-v3-package-"))).resolve()
        if not temporary.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError("Unexpected staging directory; refusing cleanup-sensitive work")
        for index, database in enumerate(databases):
            relative = database.relative_to(root).as_posix()
            with closing(open_readonly(database)) as source:
                checks = inspect_database(source, relative)
                snapshot = temporary / f"snapshot-{index}.sqlite3"
                with closing(sqlite3.connect(snapshot)) as destination:
                    source.backup(destination)
                with closing(open_readonly(snapshot)) as saved:
                    inspect_database(saved, relative + " [delivery snapshot]")
            snapshots[database] = snapshot
            database_checks[relative] = {**checks, "storage": "sqlite_backup_snapshot_including_committed_WAL",
                                         "worker_guard": guards[database.parent]}
        entries = []
        for path in files:
            packaged = snapshots.get(path, path)
            digest, size = digest_file(packaged)
            entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest, "bytes": size,
                            **({"sqlite_snapshot": True} if path in snapshots else {})})
        manifest = {"manifest_version": "delivery-v3-sha256-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                    "archive_root": root.name, "files": entries, "included_file_count_excluding_manifest": len(entries),
                    "included_bytes_excluding_manifest": sum(item["bytes"] for item in entries),
                    "manifest_self_hash": "intentionally_omitted", "exclusion_rules": EXCLUSION_RULES,
                    "excluded_paths": excluded, "sqlite_checks": database_checks,
                    "source_files_deleted": False, "source_databases_modified": False}
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        # This is the only intended project-file write. Prior evidence, raw
        # files and source databases are not modified or deleted.
        (root / MANIFEST).write_bytes(manifest_bytes)
        by_relative = {path.relative_to(root).as_posix(): path for path in files}
        try:
            with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for entry in entries:
                    original = by_relative[entry["path"]]
                    packaged = snapshots.get(original, original)
                    written_digest, written_size = hashlib.sha256(), 0
                    with packaged.open("rb") as source, archive.open(root.name + "/" + entry["path"], "w", force_zip64=True) as member:
                        for block in iter(lambda: source.read(BLOCK_SIZE), b""):
                            member.write(block)
                            written_digest.update(block)
                            written_size += len(block)
                    if written_digest.hexdigest() != entry["sha256"] or written_size != entry["bytes"]:
                        raise RuntimeError(f"File changed while packaging: {entry['path']}; source preserved, archive not published")
                archive.writestr(root.name + "/" + MANIFEST, manifest_bytes)
            # Recheck running state before publishing the final archive.
            for database in databases:
                with closing(open_readonly(database)) as connection:
                    inspect_database(connection, database.relative_to(root).as_posix())
            with zipfile.ZipFile(partial, "r") as archive:
                corrupt = archive.testzip()
                if corrupt:
                    raise RuntimeError(f"ZIP integrity verification failed: {corrupt}")
            if output.exists():
                raise FileExistsError(f"Destination appeared during packaging; not replacing it: {output}")
            # Windows rename refuses an existing target. All writes target a
            # fresh output; old delivery archives remain untouched.
            partial.rename(output)
        except Exception:
            # Preserve the partial archive as diagnostic evidence; never
            # delete an existing archive or any original project file.
            raise
    return {"archive": str(output), "manifest": str(root / MANIFEST), "files": len(entries),
            "archive_bytes": output.stat().st_size, "zip_test": "passed", "sqlite_databases_checked": len(databases)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.project_root.resolve().parent / "equity-data-system-v3.zip"
    print(json.dumps(package(args.project_root, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
