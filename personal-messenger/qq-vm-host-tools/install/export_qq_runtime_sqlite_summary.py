"""Take a bounded read-only summary from the runtime DB after a verified stop.

The database (and a WAL sidecar when present) is copied only after the fixed
status file proves the requested run is no longer alive.  Temporary database
copies are deleted after aggregate/metadata-only queries; no message, draft,
prompt, or planner JSON is queried or retained.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
RUN_ID = "63218075-38fe-43d7-964d-f0714ccc31c3"
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
OUTPUT = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq-runtime-sqlite-summary-r05.json"
TEMP = OUTPUT.with_name(".qq-runtime-sqlite-r05-tmp")
GUEST_STATUS = r"C:\PMAI\data\qq-session-runtime-status.json"
GUEST_DB = r"C:\PMAI\data\runtime\qq-default-account\runtime.sqlite3"
MAX_DB_BYTES = 32 * 1024 * 1024


def _copy(session: object, guest_path: str, host_path: Path) -> bool:
    try:
        progress = session.FileCopyFromGuest(guest_path, str(host_path), [])
        progress.WaitForCompletion(30_000)
        return bool(progress.Completed) and int(progress.ResultCode) == 0
    except Exception:
        return False


def _summary(db_path: Path) -> dict[str, object]:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        planning = dict(db.execute("SELECT status,COUNT(*) FROM runtime_planning_jobs GROUP BY status").fetchall())
        planning_errors = dict(db.execute(
            "SELECT COALESCE(error_code,'none'),COUNT(*) FROM runtime_planning_jobs GROUP BY error_code").fetchall())
        pauses = dict(db.execute(
            "SELECT COALESCE(pause_reason,'none'),COUNT(*) FROM runtime_conversations GROUP BY pause_reason").fetchall())
        try:
            decisions = [
                dict(zip(("request_id", "conversation_id", "action", "selection_reason", "model", "latency_ms", "created_at"), row))
                for row in db.execute(
                    """SELECT request_id,conversation_id,action,selection_reason,model,latency_ms,created_at
                       FROM runtime_planner_decisions ORDER BY created_at DESC LIMIT 5"""
                ).fetchall()
            ]
        except sqlite3.Error:
            decisions = []
    return {"planning_status_counts": planning, "planning_error_code_counts": planning_errors,
            "conversation_pause_reason_counts": pauses, "recent_planner_decisions": decisions}


def main() -> int:
    result: dict[str, object] = {"schema": "pmai-qq-runtime-sqlite-summary-v1", "run_id": RUN_ID}
    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1])
    password = clear.decode("utf-16-le").rstrip("\x00")
    host_session = guest_session = None
    shutil.rmtree(TEMP, ignore_errors=True)
    TEMP.mkdir()
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("VM_GUARD_FAILED")
        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, 1)
        guest_session = host_session.Console.Guest.CreateSession(GUEST_USER, password, "", "pmai-readonly-runtime-sqlite-summary")
        if guest_session.WaitForArray([1], 30_000) != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("GUEST_SESSION_FAILED")
        status_path = TEMP / "status.json"
        if not _copy(guest_session, GUEST_STATUS, status_path):
            raise RuntimeError("STATUS_COPY_FAILED")
        status = json.loads(status_path.read_text(encoding="utf-8-sig"))
        if status.get("run_id") != RUN_ID or status.get("runtime_process_alive") is not False or status.get("state") != "graceful_stopped":
            raise RuntimeError("RUNTIME_NOT_VERIFIABLY_STOPPED")
        db_path = TEMP / "runtime.sqlite3"
        if not _copy(guest_session, GUEST_DB, db_path):
            raise RuntimeError("DB_COPY_FAILED")
        if db_path.stat().st_size < 1 or db_path.stat().st_size > MAX_DB_BYTES:
            raise RuntimeError("DB_SIZE_GUARD")
        wal_path = Path(GUEST_DB + "-wal")
        local_wal = TEMP / "runtime.sqlite3-wal"
        wal_present = _copy(guest_session, str(wal_path), local_wal)
        if wal_present:
            local_shm = TEMP / "runtime.sqlite3-shm"
            if not _copy(guest_session, GUEST_DB + "-shm", local_shm):
                raise RuntimeError("WAL_SHM_COPY_FAILED")
        result.update({"succeeded": True, "database_bytes": db_path.stat().st_size,
                       "wal_copied": wal_present, "summary": _summary(db_path)})
        return 0
    except Exception as exc:
        result.update({"succeeded": False, "error_code": str(exc)[:120]})
        return 2
    finally:
        password = ""
        for index in range(len(clear)):
            clear[index] = 0
        if guest_session is not None:
            try:
                guest_session.Close()
            except Exception:
                pass
        if host_session is not None:
            try:
                host_session.UnlockMachine()
            except Exception:
                pass
        shutil.rmtree(TEMP, ignore_errors=True)
        OUTPUT.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
