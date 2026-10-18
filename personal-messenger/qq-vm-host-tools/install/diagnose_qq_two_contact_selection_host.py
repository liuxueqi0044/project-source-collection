"""Run the no-send two-contact QQ selection diagnostic through GuestSession."""
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
GUEST_SCRIPT = ROOT / r"outputs\qq-vm\install\diagnose_qq_two_contact_selection_guest.py"
DIAGNOSTICS = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_DATA = r"C:\PMAI\data"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
RELEASE_ID = "r20260912-14"
RESULT_SCHEMA = "pmai-qq-two-contact-selection-result-v1"
HOST_SCHEMA = "pmai-qq-two-contact-selection-host-v1"
TARGET_BINDINGS = ("session-contact-1", "session-contact-2")
MAX_OUTPUT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024

STEP_KEYS = {
    "binding_id",
    "process_id",
    "window_handle",
    "target_row_hash_match",
    "target_row_hash_stable",
    "invoke_exists",
    "selected_before",
    "selected_after",
    "header_match",
    "group_marker_count",
    "match_count",
    "elapsed_ms",
    "error_code",
}
FIXED_ERROR_CODES = {
    None,
    "RELEASE_UNAVAILABLE",
    "CONFIG_OR_RELEASE_INVALID",
    "TARGET_BINDINGS_INVALID",
    "UIA_INITIALIZATION_FAILED",
    "QQ_WINDOW_UNAVAILABLE",
    "QQ_WINDOW_SCOPE_DRIFT",
    "FOREGROUND_FAILED",
    "TARGET_ROW_NOT_UNIQUE",
    "TARGET_ROW_HASH_MISMATCH",
    "DISCOVERY_FAILED",
    "INVOKE_PATTERN_MISSING",
    "SELECT_ACTION_FAILED",
    "HEADER_PROBE_FAILED",
    "GROUP_MARKER_DETECTED",
    "CONFIRMATION_FAILED",
    "HEADER_MISMATCH",
    "IDENTITY_CONFIRMATION_FAILED",
    "SELECTION_NOT_CONFIRMED",
    "STEP_FAILED",
    "DIAGNOSTIC_FAILED",
}


def load_control() -> Any:
    spec = importlib.util.spec_from_file_location("pmai_two_contact_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_progress(control: Any, progress: object, timeout_ms: int = 30_000) -> None:
    control._wait_progress(progress, timeout_ms)


def poll_guest_process(process: Any, timeout_seconds: float = 90.0) -> int:
    """Pump VBox process events until termination; bound all captured output."""
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("DIAGNOSTIC_DID_NOT_START")
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
            if stdout_bytes > MAX_OUTPUT_BYTES or stderr_bytes > MAX_OUTPUT_BYTES:
                raise RuntimeError("DIAGNOSTIC_OUTPUT_TOO_LARGE")
            return int(process.ExitCode)
        elif state == 4:
            raise RuntimeError("DIAGNOSTIC_WAIT_FAILED")
        if stdout_bytes > MAX_OUTPUT_BYTES or stderr_bytes > MAX_OUTPUT_BYTES:
            raise RuntimeError("DIAGNOSTIC_OUTPUT_TOO_LARGE")
    try:
        process.Terminate()
    except Exception:
        pass
    raise RuntimeError("DIAGNOSTIC_TIMEOUT")


def validate_step(value: object, expected_binding_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != STEP_KEYS:
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if value.get("binding_id") != expected_binding_id:
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    nullable_positive_ints = ("process_id", "window_handle")
    if any(
        value.get(key) is not None
        and (
            not isinstance(value.get(key), int)
            or isinstance(value.get(key), bool)
            or value.get(key) <= 0
        )
        for key in nullable_positive_ints
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if any(
        not isinstance(value.get(key), bool)
        for key in (
            "target_row_hash_match",
            "target_row_hash_stable",
            "invoke_exists",
            "selected_before",
            "selected_after",
            "header_match",
        )
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    marker_count = value.get("group_marker_count")
    if marker_count is not None and (
        not isinstance(marker_count, int)
        or isinstance(marker_count, bool)
        or marker_count < 0
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if any(
        not isinstance(value.get(key), int)
        or isinstance(value.get(key), bool)
        or value.get(key) < 0
        for key in ("match_count", "elapsed_ms")
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if value.get("error_code") not in FIXED_ERROR_CODES:
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if value.get("error_code") is None and (
        value.get("process_id") is None
        or value.get("window_handle") is None
        or value.get("target_row_hash_match") is not True
        or value.get("target_row_hash_stable") is not True
        or value.get("invoke_exists") is not True
        or value.get("selected_after") is not True
        or value.get("header_match") is not True
        or value.get("group_marker_count") != 0
        or value.get("match_count") != 1
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    return dict(value)


def validate_guest_result(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    allowed_top = {"schema", "succeeded", "steps", "error_code"}
    if set(value) - allowed_top or value.get("schema") != RESULT_SCHEMA:
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if not isinstance(value.get("succeeded"), bool):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    steps = value.get("steps")
    if not isinstance(steps, list):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    if steps:
        if len(steps) != len(TARGET_BINDINGS):
            raise RuntimeError("RESULT_SCHEMA_INVALID")
        clean_steps = [
            validate_step(step, binding_id)
            for step, binding_id in zip(steps, TARGET_BINDINGS, strict=True)
        ]
    else:
        clean_steps = []
    top_error = value.get("error_code")
    if top_error not in FIXED_ERROR_CODES:
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    steps_succeeded = (
        len(clean_steps) == len(TARGET_BINDINGS)
        and all(step["error_code"] is None for step in clean_steps)
    )
    if (
        value["succeeded"] is not steps_succeeded
        or (value["succeeded"] is True and top_error is not None)
        or (not clean_steps and top_error is None)
    ):
        raise RuntimeError("RESULT_SCHEMA_INVALID")
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "succeeded": value["succeeded"],
        "steps": clean_steps,
    }
    if top_error is not None:
        result["error_code"] = top_error
    return result


def read_guest_result(control: Any, guest: Any, guest_path: str, host_path: Path) -> dict[str, Any]:
    wait_progress(control, guest.FileCopyFromGuest(guest_path, str(host_path), []))
    size = host_path.stat().st_size
    if size < 2 or size > MAX_RESULT_BYTES:
        raise RuntimeError("RESULT_SIZE_INVALID")
    return validate_guest_result(
        json.loads(host_path.read_text(encoding="utf-8-sig"))
    )


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
        DIAGNOSTICS / f"qq-two-contact-selection-{RELEASE_ID}-{nonce}.json"
    )
    staged_script = GUEST_DATA + rf"\qq-two-contact-selection-{nonce}.py"
    guest_result = GUEST_DATA + rf"\qq-two-contact-selection-{nonce}.json"
    host_result = DIAGNOSTICS / f".qq-two-contact-selection-{nonce}.json"
    roundtrip = DIAGNOSTICS / f".qq-two-contact-selection-{nonce}.py"
    report: dict[str, Any] = {
        "schema": HOST_SCHEMA,
        "succeeded": False,
        "steps": [],
    }
    control = guest = host_session = process = None
    clear = bytearray()
    exit_code = 2
    try:
        control = load_control()
        _vbox, host_session, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "two-contact-selection-" + nonce)
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")

        wait_progress(
            control,
            guest.FileCopyToGuest(str(GUEST_SCRIPT), staged_script, []),
        )
        wait_progress(
            control,
            guest.FileCopyFromGuest(staged_script, str(roundtrip), []),
        )
        if file_digest(GUEST_SCRIPT) != file_digest(roundtrip):
            raise RuntimeError("GUEST_SCRIPT_HASH_MISMATCH")

        process = guest.ProcessCreate(
            GUEST_PYTHON,
            [GUEST_PYTHON, staged_script, "--result", guest_result],
            GUEST_DATA,
            [],
            [48],
            120_000,
        )
        guest_exit_code = poll_guest_process(process)
        result = read_guest_result(control, guest, guest_result, host_result)
        report.update(result)
        report["schema"] = HOST_SCHEMA
        if guest_exit_code not in (0, 2):
            raise RuntimeError("DIAGNOSTIC_PROCESS_FAILED")
        if guest_exit_code == 0 and result["succeeded"] is not True:
            raise RuntimeError("DIAGNOSTIC_EXIT_MISMATCH")
        if guest_exit_code == 2 and result["succeeded"] is not False:
            raise RuntimeError("DIAGNOSTIC_EXIT_MISMATCH")
        exit_code = 0 if report["succeeded"] else 2
    except Exception as exc:
        safe_host_codes = {
            "CONTROL_UNAVAILABLE",
            "RUNTIME_NOT_STOPPED",
            "GUEST_SCRIPT_HASH_MISMATCH",
            "DIAGNOSTIC_DID_NOT_START",
            "DIAGNOSTIC_OUTPUT_TOO_LARGE",
            "DIAGNOSTIC_WAIT_FAILED",
            "DIAGNOSTIC_TIMEOUT",
            "RESULT_SIZE_INVALID",
            "RESULT_SCHEMA_INVALID",
            "DIAGNOSTIC_PROCESS_FAILED",
            "DIAGNOSTIC_EXIT_MISMATCH",
        }
        code = str(exc)
        report["error_code"] = code if code in safe_host_codes else "HOST_DIAGNOSTIC_FAILED"
        exit_code = 2
    finally:
        roundtrip.unlink(missing_ok=True)
        host_result.unlink(missing_ok=True)
        if guest is not None:
            for guest_path in (staged_script, guest_result):
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
