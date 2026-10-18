"""Host runner for the read-only QQ row hit-test inventory.

Stages ``diagnose_qq_row_hit_inventory_guest.py`` into the guest, executes it
once, and copies the evidence back.  The guest script performs no UI mutation;
this runner additionally refuses to stage a script that contains any input or
action API, and refuses to run while the QQ runtime is alive.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
GUEST_SCRIPT = ROOT / r"outputs\qq-vm\install\diagnose_qq_row_hit_inventory_guest.py"
DIAGNOSTICS = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_DATA = r"C:\PMAI\data"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
RELEASE_ID = "r20260912-17"
BINDING_ID = "session-contact-2"
HOST_SCHEMA = "pmai-qq-row-hit-inventory-host-v1"
GUEST_SCHEMA = "pmai-qq-row-hit-inventory-v1"
MAX_STREAM_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024

FORBIDDEN_TOKENS = (
    "SendInput",
    "mouse_event",
    "keybd_event",
    "DoDefaultAction",
    ".Invoke(",
    "SetFocus",
    "SetValue",
    "write_composer",
)

REQUIRED_KEYS = {
    "schema", "succeeded", "read_only_attested", "binding_id", "release_id",
    "process_id", "window_handle", "foreground_handle", "virtual_screen",
    "expected_row_hash_match", "selected_token_before", "group_marker_count",
    "binding", "target_row", "row_gate", "rows", "header_probe", "points",
    "census", "elapsed_ms", "error_code",
}

SAFE_ERROR_CODES = {
    None,
    "RELEASE_UNAVAILABLE",
    "CONFIG_OR_RELEASE_INVALID",
    "TARGET_BINDING_INVALID",
    "UIA_INITIALIZATION_FAILED",
    "QQ_WINDOW_UNAVAILABLE",
    "QQ_WINDOW_SCOPE_DRIFT",
    "FOREGROUND_FAILED",
    "TARGET_ROW_NOT_UNIQUE",
    "TARGET_ROW_HASH_MISMATCH",
    "TARGET_ROW_OBSERVATION_NOT_UNIQUE",
    "ROW_RECT_INVALID",
    "INVENTORY_READ_FAILED",
    "INVENTORY_DIAGNOSTIC_FAILED",
}


def load_control() -> Any:
    spec = importlib.util.spec_from_file_location("pmai_focus_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_read_only(source: str) -> None:
    for token in FORBIDDEN_TOKENS:
        if token in source:
            raise RuntimeError("GUEST_SCRIPT_NOT_READ_ONLY")


def wait_progress(control: Any, progress: object, timeout_ms: int = 30_000) -> None:
    control._wait_progress(progress, timeout_ms)


def poll_process(process: Any, timeout_seconds: float = 90.0) -> int:
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("GUEST_PROCESS_DID_NOT_START")
    stdout_bytes = 0
    stderr_bytes = 0
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = process.WaitForArray([2, 8, 16], 500)
        if state == 7:
            stdout_bytes += len(bytes(process.Read(1, 8192, 1000)))
        elif state == 8:
            stderr_bytes += len(bytes(process.Read(2, 8192, 1000)))
        elif state == 2:
            stdout_bytes += len(bytes(process.Read(1, 8192, 1000)))
            stderr_bytes += len(bytes(process.Read(2, 8192, 1000)))
            return int(process.ExitCode)
        elif state == 4:
            raise RuntimeError("GUEST_PROCESS_WAIT_FAILED")
        if stdout_bytes > MAX_STREAM_BYTES or stderr_bytes > MAX_STREAM_BYTES:
            raise RuntimeError("GUEST_PROCESS_OUTPUT_TOO_LARGE")
    try:
        process.Terminate()
    except Exception:
        pass
    raise RuntimeError("GUEST_PROCESS_TIMEOUT")


def validate_result(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != REQUIRED_KEYS:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if value.get("schema") != GUEST_SCHEMA:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if value.get("read_only_attested") is not True:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if not isinstance(value.get("succeeded"), bool):
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if value.get("binding_id") != BINDING_ID or value.get("release_id") != RELEASE_ID:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    points = value.get("points")
    rows = value.get("rows")
    if not isinstance(points, list) or len(points) > 9:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if not isinstance(value.get("elapsed_ms"), int) or value["elapsed_ms"] < 0:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    if value.get("error_code") not in SAFE_ERROR_CODES:
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    for key in ("header_probe", "row_gate", "census"):
        if value.get(key) is not None and not isinstance(value[key], dict):
            raise RuntimeError("INVENTORY_RESULT_INVALID")
    # A completed probe must carry the structures the decision depends on.
    if value["succeeded"] and (
        value["error_code"] is not None
        or value.get("target_row") is None
        or not isinstance(value["points"], list)
        or len(value["points"]) != 9
        or value.get("header_probe") is None
        or value.get("row_gate") is None
        or value.get("census") is None
    ):
        raise RuntimeError("INVENTORY_RESULT_INVALID")
    return dict(value)


def copy_json(control: Any, guest: Any, guest_path: str, host_path: Path) -> dict[str, Any]:
    wait_progress(control, guest.FileCopyFromGuest(guest_path, str(host_path), []))
    size = host_path.stat().st_size
    if size < 2 or size > MAX_RESULT_BYTES:
        raise RuntimeError("RESULT_SIZE_INVALID")
    return validate_result(json.loads(host_path.read_text(encoding="utf-8-sig")))


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    report_path = args.report or (
        DIAGNOSTICS / f"qq-row-hit-inventory-{RELEASE_ID}-{nonce}.json"
    )
    staged = GUEST_DATA + rf"\qq-row-hit-inventory-{nonce}.py"
    result_guest = GUEST_DATA + rf"\qq-row-hit-inventory-{nonce}.json"
    result_host = DIAGNOSTICS / f".qq-row-hit-inventory-{nonce}.json"
    roundtrip = DIAGNOSTICS / f".qq-row-hit-inventory-{nonce}.py"
    report: dict[str, Any] = {
        "schema": HOST_SCHEMA,
        "diagnostic_nonce": nonce,
        "succeeded": False,
        "guest_started": False,
        "evidence_copied": False,
        "result": None,
        "error_code": None,
    }
    control = guest = host_session = probe_process = None
    clear = bytearray()
    exit_code = 2
    try:
        source = GUEST_SCRIPT.read_text(encoding="utf-8")
        assert_read_only(source)
        control = load_control()
        _vbox, host_session, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "row-hit-inventory-" + nonce)
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")

        wait_progress(control, guest.FileCopyToGuest(str(GUEST_SCRIPT), staged, []))
        wait_progress(control, guest.FileCopyFromGuest(staged, str(roundtrip), []))
        if digest(GUEST_SCRIPT) != digest(roundtrip):
            raise RuntimeError("GUEST_SCRIPT_HASH_MISMATCH")

        probe_process = guest.ProcessCreate(
            GUEST_PYTHON,
            [GUEST_PYTHON, staged, "--result", result_guest],
            GUEST_DATA,
            [],
            [48],
            120_000,
        )
        report["guest_started"] = True
        probe_exit = poll_process(probe_process)

        # Copy and validate the evidence before anything else can fail, so a
        # later host-side problem cannot erase what the guest actually saw.
        result = copy_json(control, guest, result_guest, result_host)
        report["result"] = result
        report["evidence_copied"] = True
        if probe_exit not in (0, 2):
            raise RuntimeError("GUEST_PROCESS_EXIT_INVALID")
        if (probe_exit == 0) is not result["succeeded"]:
            raise RuntimeError("INVENTORY_EXIT_MISMATCH")
        report["succeeded"] = bool(result["succeeded"])
        exit_code = 0 if report["succeeded"] else 2
    except Exception as exc:
        safe_codes = {
            "CONTROL_UNAVAILABLE", "GUEST_SCRIPT_NOT_READ_ONLY", "RUNTIME_NOT_STOPPED",
            "GUEST_SCRIPT_HASH_MISMATCH", "GUEST_PROCESS_DID_NOT_START",
            "GUEST_PROCESS_OUTPUT_TOO_LARGE", "GUEST_PROCESS_WAIT_FAILED",
            "GUEST_PROCESS_TIMEOUT", "RESULT_SIZE_INVALID",
            "INVENTORY_RESULT_INVALID", "GUEST_PROCESS_EXIT_INVALID",
            "INVENTORY_EXIT_MISMATCH",
        }
        code = str(exc)
        report["error_code"] = code if code in safe_codes else "HOST_DIAGNOSTIC_FAILED"
        exit_code = 2
    finally:
        for path in (result_host, roundtrip):
            path.unlink(missing_ok=True)
        if guest is not None:
            guest_paths = [staged]
            if report["evidence_copied"]:
                guest_paths.append(result_guest)
            for guest_path in guest_paths:
                try:
                    guest.FileDelete(guest_path)
                except Exception:
                    pass
        for index in range(len(clear)):
            clear[index] = 0
        if guest is not None:
            try:
                guest.Close()
            except Exception:
                pass
        if host_session is not None:
            try:
                host_session.UnlockMachine()
            except Exception:
                pass
        atomic_write(report_path, report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
