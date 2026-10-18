"""Host-side control channel for the guest QQ runtime.

This helper only exchanges redacted JSON control/status files through the
VirtualBox GuestSession. It never starts a guest process and never exports
credentials or message content.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
LOCK_SHARED = 1
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
HOST_DIAGNOSTICS = WORKSPACE / r"outputs\qq-vm\install\diagnostics"
GUEST_DATA = r"C:\PMAI\data"
GUEST_STATUS = GUEST_DATA + r"\qq-session-runtime-status.json"
GUEST_INCOMING = GUEST_DATA + r"\qq-session-runtime-control-request.incoming.json"
GUEST_REQUEST = GUEST_DATA + r"\qq-session-runtime-control-request.json"
GUEST_RESULT = GUEST_DATA + r"\qq-session-runtime-control-result.json"
MAX_BYTES = 8 * 1024 * 1024
STATUS_SCHEMA = "pmai-qq-session-runtime-status-v2"
CONTROL_REQUEST_SCHEMA = "pmai-qq-runtime-control-request-v1"
CONTROL_RESULT_SCHEMA = "pmai-qq-runtime-control-result-v1"


def _wait_progress(progress: object, timeout_ms: int) -> None:
    progress.WaitForCompletion(timeout_ms)
    if not bool(progress.Completed):
        raise RuntimeError("guest file operation timed out")
    if int(progress.ResultCode) != 0:
        raise RuntimeError(f"guest file operation failed: {int(progress.ResultCode)}")


def _read_credential() -> tuple[bytearray, str]:
    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1])
    protected = ""
    return clear, clear.decode("utf-16-le").rstrip("\x00")


def _copy_from_guest(session: object, guest_path: str, host_path: Path) -> dict[str, object]:
    host_path.parent.mkdir(parents=True, exist_ok=True)
    if host_path.exists():
        host_path.unlink()
    _wait_progress(session.FileCopyFromGuest(guest_path, str(host_path), []), 30_000)
    size = host_path.stat().st_size
    if size < 2 or size > MAX_BYTES:
        raise RuntimeError("guest JSON size guard failed")
    value = json.loads(host_path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("guest status must be a JSON object")
    return value


def _copy_to_guest(session: object, host_path: Path, guest_path: str) -> None:
    _wait_progress(session.FileCopyToGuest(str(host_path), guest_path, []), 30_000)


def _runtime_status(session: object, suffix: str) -> dict[str, object]:
    status = _copy_from_guest(session, GUEST_STATUS, HOST_DIAGNOSTICS / f".qq-runtime-status-{suffix}.tmp.json")
    if status.get("schema") != STATUS_SCHEMA:
        raise RuntimeError("runtime status schema mismatch")
    run_id = status.get("run_id")
    if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
        raise RuntimeError("runtime status run_id is not a canonical UUID")
    return status


def _matching_result(session: object, request_id: str, run_id: str, action: str) -> dict[str, object] | None:
    path = HOST_DIAGNOSTICS / ".qq-runtime-control-result.tmp.json"
    try:
        result = _copy_from_guest(session, GUEST_RESULT, path)
    except Exception:
        return None
    if (
        result.get("schema") != CONTROL_RESULT_SCHEMA
        or not isinstance(result.get("accepted"), bool)
        or result.get("request_id") != request_id
        or result.get("target_run_id") != run_id
        or result.get("action") != action
    ):
        return None
    return result


def _open_guest() -> tuple[object, object, object, bytearray, str]:
    clear, password = _read_credential()
    host_session = guest_session = None
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("fixed VM identity or running-state guard failed")
        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, LOCK_SHARED)
        guest_session = host_session.Console.Guest.CreateSession(GUEST_USER, password, "", "pmai-runtime-control")
        if guest_session.WaitForArray([1], 30_000) != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("qqbot guest session did not start")
        return vbox, host_session, guest_session, clear, password
    except BaseException:
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
        for index in range(len(clear)):
            clear[index] = 0
        password = ""
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("pause", "resume", "graceful_stop"), required=True)
    args = parser.parse_args()
    HOST_DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    request_id = str(uuid.uuid4())
    requested_at = datetime.now(UTC).isoformat()
    vbox = host_session = guest_session = None
    clear = bytearray()
    password = ""
    request_host = HOST_DIAGNOSTICS / f"qq-runtime-control-{request_id}.json"
    try:
        vbox, host_session, guest_session, clear, password = _open_guest()
        status = _runtime_status(guest_session, request_id)
        if status.get("runtime_process_alive") is not True or not status.get("run_id"):
            raise RuntimeError("runtime status is not alive or has no run_id")
        run_id = str(status["run_id"])
        request = {
            "schema": CONTROL_REQUEST_SCHEMA,
            "request_id": request_id,
            "target_run_id": run_id,
            "action": args.action,
            "requested_at": requested_at,
        }
        request_host.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
        _copy_to_guest(guest_session, request_host, GUEST_INCOMING)
        guest_session.FsObjRename(GUEST_INCOMING, GUEST_REQUEST, [1])

        # Match the shipped guest-local control contract.  A pause may need to
        # drain one bounded worker/UI request before its final acknowledgement.
        deadline = time.monotonic() + 120
        result = None
        accepted_pending = None
        while time.monotonic() < deadline:
            result = _matching_result(guest_session, request_id, run_id, args.action)
            if result is not None:
                if (
                    result.get("accepted") is True
                    and result.get("state") == "pausing"
                    and args.action in {"pause", "graceful_stop"}
                ):
                    accepted_pending = result
                    result = None
                    time.sleep(1)
                    continue
                break
            time.sleep(1)
        if result is None:
            if accepted_pending is not None:
                print(json.dumps({
                    "accepted": True,
                    "state": "pausing",
                    "error_code": "DRAIN_PENDING",
                    **request,
                }, sort_keys=True))
                return 4
            print(json.dumps({"accepted": None, "state": "pending_unknown", **request}, sort_keys=True))
            return 2

        output: dict[str, object] = {"accepted": result.get("accepted"), "state": result.get("state"), **request}
        if result.get("accepted") is not True:
            output["error_code"] = result.get("error_code")
            print(json.dumps(output, sort_keys=True))
            return 1
        if args.action == "graceful_stop" and result.get("state") == "stopping":
            status_deadline = time.monotonic() + 60
            stopped_status = None
            while time.monotonic() < status_deadline:
                try:
                    candidate = _runtime_status(guest_session, request_id + "-stop")
                    if candidate.get("run_id") == run_id and candidate.get("runtime_process_alive") is False:
                        stopped_status = candidate
                        break
                except Exception:
                    pass
                time.sleep(1)
            output["runtime_exited"] = stopped_status is not None
            if stopped_status is not None:
                output["runtime_status"] = stopped_status
        print(json.dumps(output, sort_keys=True))
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
