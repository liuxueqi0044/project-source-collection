"""Run the frozen guest cursor reanchor tool through a short GuestSession.

This host tool is deliberately narrow: it requires the QQ runtime to be
stopped, starts exactly one guest process, exports only the guest's
privacy-minimal result, and never prints guest stdout or message content.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
START_HELPER = ROOT / r"outputs\qq-vm\install\start_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
GUEST_DATA = r"C:\PMAI\data"
GUEST_CONFIG = GUEST_DATA + r"\runtime-session-1.json"
GUEST_REPORT_ROOT = GUEST_DATA
HOST_SCHEMA = "pmai-qq-cursor-reanchor-host-v1"
GUEST_SCHEMA = "pmai-qq-cursor-reanchor-result-v1"
_RELEASE = re.compile(r"r\d{8}-\d{2}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SAFE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_:-]{1,127}")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(name.upper() + "_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _assert_runtime_stopped(control: Any, start_helper: Any, guest: Any, suffix: str) -> dict[str, object]:
    status = control._runtime_status(guest, "reanchor-before-" + suffix)
    if status.get("runtime_process_alive") is not False:
        raise RuntimeError("RUNTIME_NOT_STOPPED")
    pid = status.get("runtime_process_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise RuntimeError("RUNTIME_PID_UNAVAILABLE")
    # Probe the recorded PID even when the status file says stopped.  A stale
    # status must never authorize a cursor mutation.
    if start_helper._guest_pid_alive(guest, pid):
        raise RuntimeError("RUNTIME_NOT_STOPPED")
    return status


def _validated_guest_report(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("schema") != GUEST_SCHEMA:
        raise RuntimeError("REANCHOR_REPORT_SCHEMA_MISMATCH")
    status = raw.get("status")
    if status == "succeeded":
        allowed = {
            "schema", "status", "conversation_id", "binding_id", "cursor_applied",
            "cursor_idempotent", "pause_cleared", "next_seq",
        }
        if set(raw) != allowed:
            raise RuntimeError("REANCHOR_REPORT_FIELDS_INVALID")
        for field in ("conversation_id", "binding_id"):
            value = raw.get(field)
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise RuntimeError("REANCHOR_REPORT_IDENTIFIER_INVALID")
        for field in ("cursor_applied", "cursor_idempotent", "pause_cleared"):
            if not isinstance(raw.get(field), bool):
                raise RuntimeError("REANCHOR_REPORT_BOOLEAN_INVALID")
        if not isinstance(raw.get("next_seq"), int) or isinstance(raw.get("next_seq"), bool) or raw["next_seq"] < 0:
            raise RuntimeError("REANCHOR_REPORT_SEQUENCE_INVALID")
        return {key: raw[key] for key in allowed}
    if status == "rejected":
        if set(raw) != {"schema", "status", "error_code"}:
            raise RuntimeError("REANCHOR_REPORT_FIELDS_INVALID")
        error = raw.get("error_code")
        if not isinstance(error, str) or _SAFE_CODE.fullmatch(error) is None:
            raise RuntimeError("REANCHOR_REPORT_ERROR_INVALID")
        return {"schema": GUEST_SCHEMA, "status": "rejected", "error_code": error}
    raise RuntimeError("REANCHOR_REPORT_STATUS_INVALID")


def _copy_and_validate(control: Any, guest: Any, guest_path: str, host_path: Path) -> dict[str, object]:
    # _copy_from_guest enforces the 8 MiB transfer limit and JSON-object shape.
    value = control._copy_from_guest(guest, guest_path, host_path)
    return _validated_guest_report(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--reason-code", required=True)
    args = parser.parse_args()
    for name in ("conversation_id", "operator_id", "reason_code"):
        if _IDENTIFIER.fullmatch(getattr(args, name)) is None:
            parser.error("bad " + name.replace("_", "-"))
    if _RELEASE.fullmatch(args.release_id) is None:
        parser.error("bad release id")

    nonce = uuid.uuid4().hex
    report: dict[str, object] = {
        "schema": HOST_SCHEMA,
        "release_id": args.release_id,
        "succeeded": False,
    }
    guest = host = None
    clear = bytearray()
    guest_report = GUEST_REPORT_ROOT + rf"\cursor-reanchor-{nonce}.json"
    host_guest_report = DIAG / (".cursor-reanchor-" + nonce + "-guest.tmp.json")
    try:
        control = _load(CONTROL, "pmai_cursor_reanchor_control")
        start_helper = _load(START_HELPER, "pmai_cursor_reanchor_start")
        _vbox, host, guest, clear, _password = control._open_guest()
        _assert_runtime_stopped(control, start_helper, guest, nonce)
        script = rf"C:\PMAI\app\releases\{args.release_id}\reanchor_message_cursor_guest.py"
        process = guest.ProcessCreate(
            GUEST_PYTHON,
            [
                GUEST_PYTHON, script,
                "--config", GUEST_CONFIG,
                "--conversation-id", args.conversation_id,
                "--operator-id", args.operator_id,
                "--reason-code", args.reason_code,
                "--report", guest_report,
            ],
            GUEST_DATA,
            [],
            [],
            120_000,
        )
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("REANCHOR_DID_NOT_START")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = process.WaitForArray([2, 4], 500)
            if state == 2:
                report["process_status"] = int(process.Status)
                report["exit_code"] = int(process.ExitCode)
                break
            if state == 4:
                raise RuntimeError("REANCHOR_WAIT_FAILED")
        else:
            raise RuntimeError("REANCHOR_TIMEOUT")

        guest_result = _copy_and_validate(control, guest, guest_report, host_guest_report)
        report["guest_result"] = guest_result
        if int(report["exit_code"]) != 0 or guest_result.get("status") != "succeeded":
            raise RuntimeError("REANCHOR_REJECTED")
        report["succeeded"] = True
        return 0
    except Exception as exc:
        text = str(exc)
        report["error_code"] = text if _SAFE_CODE.fullmatch(text) else type(exc).__name__
        return 2
    finally:
        # The temporary copy is parsed and then removed; the final report only
        # contains the validated privacy-minimal guest fields.
        try:
            host_guest_report.unlink()
        except FileNotFoundError:
            pass
        for index in range(len(clear)):
            clear[index] = 0
        if guest is not None:
            try:
                guest.Close()
            except Exception:
                pass
        if host is not None:
            try:
                host.UnlockMachine()
            except Exception:
                pass
        _atomic_json(DIAG / ("cursor-reanchor-" + args.release_id + "-" + nonce + ".json"), report)


if __name__ == "__main__":
    raise SystemExit(main())
