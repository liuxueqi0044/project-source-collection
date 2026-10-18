"""Fixed COM staging/export entry for the read-only worker timing diagnostic.

This file is intentionally an explicit action entry point. It is not called by
deployment or runtime startup and never sends QQ input or messages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
SCRIPT = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq_worker_observe_timing_guest.py"
HOST_REPORT = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq-worker-observe-timing-focus-export.json"
HOST_PARENT_REPORT = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq-worker-observe-timing-focus-parent-export.json"
HOST_STAGE = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq-worker-observe-timing-stage.json"
GUEST_SCRIPT = r"C:\PMAI\data\qq_worker_observe_timing_guest.py"
GUEST_REPORT = r"C:\PMAI\data\qq-worker-observe-timing-focus.json"
GUEST_PARENT_REPORT = r"C:\PMAI\data\qq-worker-observe-timing-focus-parent.json"
GUEST_STATUS = r"C:\PMAI\data\qq-session-runtime-status.json"
MAX_BYTES = 8 * 1024 * 1024


def _wait(progress: object, timeout_ms: int) -> None:
    progress.WaitForCompletion(timeout_ms)
    if not bool(progress.Completed) or int(progress.ResultCode) != 0:
        raise RuntimeError("guest file operation failed")


def _open_guest():
    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1])
    password = clear.decode("utf-16-le").rstrip("\x00")
    host_session = guest_session = None
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("fixed VM identity or running-state guard failed")
        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, 1)
        guest_session = host_session.Console.Guest.CreateSession(GUEST_USER, password, "", "pmai-observe-timing")
        if guest_session.WaitForArray([1], 30_000) != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("qqbot guest session did not start")
        return guest_session, host_session, clear, password
    except BaseException:
        if guest_session is not None:
            try: guest_session.Close()
            except Exception: pass
        if host_session is not None:
            try: host_session.UnlockMachine()
            except Exception: pass
        for index in range(len(clear)): clear[index] = 0
        raise


def _close(guest_session, host_session, clear) -> None:
    for index in range(len(clear)): clear[index] = 0
    if guest_session is not None:
        try: guest_session.Close()
        except Exception: pass
    if host_session is not None:
        try: host_session.UnlockMachine()
        except Exception: pass


def _stage() -> None:
    guest_session = host_session = None
    clear = bytearray()
    try:
        guest_session, host_session, clear, password = _open_guest()
        status_host = HOST_STAGE.with_name(".qq-runtime-status-stage.tmp.json")
        _wait(guest_session.FileCopyFromGuest(GUEST_STATUS, str(status_host), []), 30_000)
        status = json.loads(status_host.read_text(encoding="utf-8-sig"))
        status_host.unlink()
        if status.get("schema") != "pmai-qq-session-runtime-status-v2" or status.get("runtime_process_alive") is not False:
            raise RuntimeError("runtime must be stopped before diagnostic stage")
        run_id = str(status.get("run_id", ""))
        _wait(guest_session.FileCopyToGuest(str(SCRIPT), GUEST_SCRIPT, []), 30_000)
        HOST_STAGE.write_text(json.dumps({"schema": "pmai-qq-worker-observe-timing-stage-v1", "runtime_run_id": run_id}, sort_keys=True), encoding="utf-8")
        print(json.dumps({"staged": True, "runtime_run_id": run_id, "guest_script": GUEST_SCRIPT}, sort_keys=True))
    finally:
        _close(guest_session, host_session, clear)


def _export() -> None:
    HOST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = HOST_REPORT.with_name("." + HOST_REPORT.name + ".tmp")
    if temporary.exists(): temporary.unlink()
    guest_session = host_session = None
    clear = bytearray()
    try:
        guest_session, host_session, clear, password = _open_guest()
        if not HOST_STAGE.exists():
            raise RuntimeError("diagnostic stage metadata missing")
        stage = json.loads(HOST_STAGE.read_text(encoding="utf-8"))
        _wait(guest_session.FileCopyFromGuest(GUEST_REPORT, str(temporary), []), 60_000)
        if temporary.stat().st_size < 2 or temporary.stat().st_size > MAX_BYTES:
            raise RuntimeError("timing report size guard failed")
        value = json.loads(temporary.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or value.get("schema") != "pmai-qq-worker-observe-timing-v1":
            raise RuntimeError("timing report schema mismatch")
        if value.get("runtime_run_id") != stage.get("runtime_run_id"):
            raise RuntimeError("timing report runtime run_id mismatch")
        temporary.replace(HOST_REPORT)
        parent_tmp = HOST_PARENT_REPORT.with_name("." + HOST_PARENT_REPORT.name + ".tmp")
        _wait(guest_session.FileCopyFromGuest(GUEST_PARENT_REPORT, str(parent_tmp), []), 60_000)
        parent = json.loads(parent_tmp.read_text(encoding="utf-8-sig"))
        if not isinstance(parent, dict) or parent.get("schema") != "pmai-qq-worker-observe-timing-parent-v1" or parent.get("request_id") != value.get("request_id"):
            raise RuntimeError("timing parent report request mismatch")
        parent_tmp.replace(HOST_PARENT_REPORT)
        print(json.dumps({"exported": True, "path": str(HOST_REPORT), "parent_path": str(HOST_PARENT_REPORT)}, sort_keys=True))
    finally:
        if temporary.exists(): temporary.unlink()
        _close(guest_session, host_session, clear)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("stage", "export"), required=True)
    args = parser.parse_args()
    if args.action == "stage": _stage()
    else: _export()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
