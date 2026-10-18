"""Offline synthetic durability checks; these do not claim live source freshness."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from equity_data.backup import (BackupBusy, BackupInvalid, MANIFEST, MANIFEST_HASH,
                               RESTORE_STATUS, STATUS, create_backup, main,
                               restore_backup, verify_backup)
from equity_data.local_control import collector_lock
from equity_data.queue import exclusive_worker
from equity_data.store import Store


@pytest.fixture
def source(tmp_path):
    store = Store(tmp_path / "source")
    store.db.execute("PRAGMA wal_autocheckpoint=0")
    for value, acquired, published in [(17, "2025-01-02T00:00:00Z", "2024-12-01T21:00:00Z"),
                                      (19, "2025-01-03T00:00:00Z", "2024-12-02T21:00:00Z")]:
        raw = store.put_raw(f"SYNTHETIC value {value}".encode(), source="test", url="test://original",
                            retrieved_at=acquired)
        store.put_record("financial_facts", "generic-metric", {"value": value}, source="test",
                         raw_id=raw["raw_id"], published_at=published, available_at=published,
                         availability_basis="synthetic_test", publication_precision="second")
    controls = []
    for name in ("local-control.sqlite3", "control.sqlite3"):
        connection = sqlite3.connect(store.root / name)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE control_test(value TEXT, updated_at TEXT)")
        connection.execute("INSERT INTO control_test VALUES('watermark-is-historical','2025-01-03T00:00:00Z')")
        connection.commit()
        controls.append(connection)
    yield store
    for connection in controls:
        connection.close()
    store.close()


def table_rows(path, table):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT * FROM "' + table + '"').fetchall()


def reseal_manifest(directory, mutate):
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    mutate(manifest)
    payload = json.dumps(manifest).encode()
    digest = hashlib.sha256(payload).hexdigest()
    (directory / MANIFEST).write_bytes(payload)
    (directory / MANIFEST_HASH).write_text(digest)
    (directory / STATUS).write_text(json.dumps({"state": "complete", "manifest_sha256": digest}))


def test_wal_backup_restore_preserves_all_evidence_rows_and_triggers(source, tmp_path):
    assert (source.root / "data.sqlite3-wal").stat().st_size > 0
    source_tables = [row[0] for row in source.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {table: table_rows(source.root / "data.sqlite3", table) for table in source_tables}
    backup = tmp_path / "backup"
    result = create_backup(source.root, backup)
    assert result["state"] == "complete" and result["raw_references"] == 2
    assert result["verified_files"] == 5
    restored = tmp_path / "restored"
    state = restore_backup(backup, restored)
    assert state["state"] == "restored_requires_live_check"
    assert state["new_fetches_created"] == 0
    assert state["historical_watermarks_are_current_freshness"] is False
    assert state["automatic_schedule_changed"] is False
    for table, expected in before.items():
        assert table_rows(restored / "data.sqlite3", table) == expected
        assert table_rows(source.root / "data.sqlite3", table) == expected
    for name in ("local-control.sqlite3", "control.sqlite3"):
        assert table_rows(source.root / name, "control_test") == table_rows(restored / name, "control_test")
    with closing(sqlite3.connect(restored / "data.sqlite3")) as connection:
        for path, digest in connection.execute("SELECT path,sha256 FROM raw_blobs"):
            assert hashlib.sha256((restored / path).read_bytes()).hexdigest() == digest
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE raw_blobs SET path='modified'")
    assert not (restored / ".machine-worker.lock").exists()
    assert not (restored / ".local-collector.lock").exists()


@pytest.mark.parametrize("which", ["local", "engine"])
def test_busy_lock_is_explicit_and_does_not_mix_snapshots(source, tmp_path, which):
    target = tmp_path / "busy"
    lock = collector_lock(source.root) if which == "local" else exclusive_worker(SimpleNamespace(root=source.root))
    with lock:
        with pytest.raises(BackupBusy):
            create_backup(source.root, target)
    assert json.loads((target / STATUS).read_text())["state"] == "busy"
    assert not (target / "data.sqlite3").exists()
    assert create_backup(source.root, tmp_path / "after-release")["state"] == "complete"


def test_backup_never_overwrites_existing_destination(source, tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("preserve")
    with pytest.raises(FileExistsError):
        create_backup(source.root, existing)
    assert (existing / "keep.txt").read_text() == "preserve"
    with pytest.raises(ValueError, match="outside"):
        create_backup(source.root, source.root / "nested")


def test_restore_empty_allowed_nonempty_refused(source, tmp_path):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert restore_backup(backup, empty)["requires_live_check"]
    before = (empty / "data.sqlite3").read_bytes()
    with pytest.raises(FileExistsError):
        restore_backup(backup, empty)
    assert (empty / "data.sqlite3").read_bytes() == before
    with pytest.raises(ValueError):
        restore_backup(backup, backup / "nested")


def test_source_corruption_retains_failure_evidence(source, tmp_path):
    path = source.db.execute("SELECT path FROM raw_blobs LIMIT 1").fetchone()[0]
    (source.root / path).write_bytes(b"corrupted-original")
    backup = tmp_path / "failed"
    with pytest.raises(BackupInvalid, match="hash or size mismatch"):
        create_backup(source.root, backup)
    state = json.loads((backup / STATUS).read_text())
    assert state["state"] == "failed" and state["partial_files_preserved"]
    assert (backup / "data.sqlite3").exists()
    with pytest.raises(BackupInvalid):
        verify_backup(backup)


@pytest.mark.parametrize("kind", ["raw", "database", "manifest", "completion"])
def test_backup_corruption_is_rejected_before_restoring_database(source, tmp_path, kind):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    manifest = json.loads((backup / MANIFEST).read_text())
    path = {"raw": backup / manifest["raw_references"][0]["backup_path"],
            "database": backup / "data.sqlite3", "manifest": backup / MANIFEST,
            "completion": backup / STATUS}[kind]
    path.write_bytes(b"corrupt")
    with pytest.raises(BackupInvalid):
        verify_backup(backup)
    restored = tmp_path / "restored"
    with pytest.raises(BackupInvalid):
        restore_backup(backup, restored)
    assert not (restored / "data.sqlite3").exists()
    assert json.loads((restored / RESTORE_STATUS).read_text())["state"] == "restore_failed"


def test_database_quick_check_is_independent_of_matching_file_hash(source, tmp_path):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    (backup / "control.sqlite3").write_bytes(b"not a sqlite database")
    def change(manifest):
        row = next(item for item in manifest["files"] if item["path"] == "control.sqlite3")
        row["sha256"] = hashlib.sha256((backup / row["path"]).read_bytes()).hexdigest()
        row["bytes"] = (backup / row["path"]).stat().st_size
    reseal_manifest(backup, change)
    with pytest.raises(BackupInvalid, match="database"):
        verify_backup(backup)


def test_manifest_traversal_rejected_even_with_matching_manifest_hash(source, tmp_path):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    reseal_manifest(backup, lambda m: m["files"][0].update(path="../outside.sqlite3"))
    with pytest.raises(BackupInvalid, match="Unsafe"):
        verify_backup(backup)


def test_raw_catalog_manifest_mismatch_rejected(source, tmp_path):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    reseal_manifest(backup, lambda m: m["raw_references"][0].update(raw_id="invented-id"))
    with pytest.raises(BackupInvalid, match="reference differs"):
        verify_backup(backup)


def test_cli_busy_and_verify_exit_status(source, tmp_path, capsys):
    with collector_lock(source.root):
        assert main(["backup", "--storage", str(source.root), "--destination", str(tmp_path / "busy")]) == 3
    assert json.loads(capsys.readouterr().out)["state"] == "busy"
    backup = tmp_path / "backup"
    assert main(["backup", "--storage", str(source.root), "--destination", str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "complete"
    assert main(["verify", "--backup", str(backup)]) == 0
    assert json.loads(capsys.readouterr().out)["sqlite_quick_check"] == "ok"


def test_empty_catalog_and_absent_control_can_be_backed_up(tmp_path):
    with closing(Store(tmp_path / "empty-source")) as store:
        result = create_backup(store.root, tmp_path / "backup")
    assert result["verified_files"] == 1 and result["raw_references"] == 0
    restore_backup(tmp_path / "backup", tmp_path / "restored")
    assert table_rows(tmp_path / "restored" / "data.sqlite3", "raw_blobs") == []


def test_missing_raw_file_preserves_failed_backup(source, tmp_path):
    relative = source.db.execute("SELECT path FROM raw_blobs LIMIT 1").fetchone()[0]
    (source.root / relative).unlink()
    with pytest.raises(FileNotFoundError):
        create_backup(source.root, tmp_path / "backup")
    status = json.loads((tmp_path / "backup" / STATUS).read_text())
    assert status["state"] == "failed" and status["partial_files_preserved"]


def test_unlisted_files_and_sqlite_sidecars_are_not_restored(source, tmp_path):
    backup = tmp_path / "backup"
    create_backup(source.root, backup)
    for name in (".env", "data.sqlite3-wal", "data.sqlite3-shm"):
        (backup / name).write_text("unlisted file; never restore")
    assert verify_backup(backup)["sqlite_quick_check"] == "ok"
    restored = tmp_path / "restored"
    restore_backup(backup, restored)
    for name in (".env", "data.sqlite3-wal", "data.sqlite3-shm"):
        assert not (restored / name).exists()
