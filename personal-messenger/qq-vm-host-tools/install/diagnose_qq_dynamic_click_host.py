"""Dynamically click one registered QQ row, then confirm in a fresh process."""
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
ACTION_SCRIPT = ROOT / r"outputs\qq-vm\install\diagnose_qq_dynamic_click_once_guest.py"
CONFIRM_SCRIPT = ROOT / r"outputs\qq-vm\install\diagnose_qq_dynamic_click_confirmation_guest.py"
DIAGNOSTICS = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_DATA = r"C:\PMAI\data"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
RELEASE_ID = "r20260912-17"
BINDING_ID = "session-contact-2"
HOST_SCHEMA = "pmai-qq-dynamic-click-host-v1"
ACTION_SCHEMA = "pmai-qq-dynamic-click-once-v1"
CONFIRM_SCHEMA = "pmai-qq-dynamic-click-confirmation-v1"
MAX_STREAM_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024

ACTION_KEYS = {
    "schema", "succeeded", "binding_id", "process_id", "window_handle",
    "direct_binding", "match_count", "expected_row_hash_match",
    "selected_before", "row_enabled", "row_onscreen", "visual_identity_unique",
    "row_rect_valid", "row_rect_stable",
    "conversation_region_unique", "conversation_rect_valid",
    "row_inside_conversations", "point_inside_row",
    "point_inside_conversations", "foreground_match",
    "window_from_point_match", "uia_point_check_available",
    "uia_point_row_match", "uia_point_match_mode", "hit_candidates_checked",
    "mouse_sendinput_calls",
    "mouse_inputs_requested", "mouse_inputs_sent", "elapsed_ms", "error_code",
}
CONFIRM_KEYS = {
    "schema", "succeeded", "binding_id", "process_id", "window_handle",
    "direct_binding", "match_count", "expected_row_hash_match",
    "selected_token", "header_match", "group_marker_count", "identity_match",
    "elapsed_ms", "error_code",
}
ACTION_ERROR_CODES = {
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
    "DISCOVERY_FAILED",
    "TARGET_ALREADY_SELECTED",
    "TARGET_ROW_DISABLED",
    "TARGET_ROW_OFFSCREEN",
    "ROW_VISUAL_IDENTITY_INVALID",
    "ROW_VISUAL_IDENTITY_NOT_UNIQUE",
    "ROW_RECT_INVALID",
    "ROW_RECT_UNSTABLE",
    "CONVERSATION_REGION_NOT_UNIQUE",
    "CONVERSATION_RECT_INVALID",
    "ROW_OUTSIDE_CONVERSATIONS",
    "CLICK_POINT_OUT_OF_BOUNDS",
    "FOREGROUND_DRIFT",
    "WINDOW_POINT_SCOPE_DRIFT",
    "UIA_POINT_CHECK_UNAVAILABLE",
    "UIA_POINT_CHECK_FAILED",
    "UIA_POINT_TARGET_MISMATCH",
    "VIRTUAL_SCREEN_INVALID",
    "ACTION_PRECHECK_FAILED",
    "MOUSE_INPUT_INCOMPLETE",
    "ACTION_DIAGNOSTIC_FAILED",
}
CONFIRM_ERROR_CODES = {
    None,
    "RELEASE_UNAVAILABLE",
    "CONFIG_OR_RELEASE_INVALID",
    "TARGET_BINDING_INVALID",
    "UIA_INITIALIZATION_FAILED",
    "QQ_WINDOW_UNAVAILABLE",
    "QQ_WINDOW_SCOPE_DRIFT",
    "TARGET_ROW_NOT_UNIQUE",
    "TARGET_ROW_HASH_MISMATCH",
    "SELECTION_NOT_CONFIRMED",
    "GROUP_MARKER_DETECTED",
    "HEADER_MISMATCH",
    "HEADER_PROBE_FAILED",
    "IDENTITY_CONFIRMATION_FAILED",
    "CONFIRMATION_READ_FAILED",
    "CONFIRMATION_DIAGNOSTIC_FAILED",
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
            if stdout_bytes > MAX_STREAM_BYTES or stderr_bytes > MAX_STREAM_BYTES:
                raise RuntimeError("GUEST_PROCESS_OUTPUT_TOO_LARGE")
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


def positive_or_none(value: object) -> bool:
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and value > 0
    )


def nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_action(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != ACTION_KEYS
        or value.get("schema") != ACTION_SCHEMA
        or value.get("binding_id") != BINDING_ID
        or not isinstance(value.get("succeeded"), bool)
        or not positive_or_none(value.get("process_id"))
        or not positive_or_none(value.get("window_handle"))
    ):
        raise RuntimeError("ACTION_RESULT_INVALID")
    for key in (
        "direct_binding", "expected_row_hash_match", "selected_before",
        "row_enabled", "row_onscreen", "visual_identity_unique",
        "row_rect_valid", "row_rect_stable", "conversation_region_unique",
        "conversation_rect_valid", "row_inside_conversations",
        "point_inside_row", "point_inside_conversations", "foreground_match",
        "window_from_point_match", "uia_point_check_available",
        "uia_point_row_match",
    ):
        if not isinstance(value.get(key), bool):
            raise RuntimeError("ACTION_RESULT_INVALID")
    for key in (
        "match_count", "hit_candidates_checked", "mouse_sendinput_calls", "mouse_inputs_requested",
        "mouse_inputs_sent", "elapsed_ms",
    ):
        if not nonnegative_int(value.get(key)):
            raise RuntimeError("ACTION_RESULT_INVALID")
    if value["mouse_inputs_requested"] not in (0, 3):
        raise RuntimeError("ACTION_RESULT_INVALID")
    if value["hit_candidates_checked"] > 9:
        raise RuntimeError("ACTION_RESULT_INVALID")
    if value["uia_point_match_mode"] not in (None, "runtime_id", "visual_identity"):
        raise RuntimeError("ACTION_RESULT_INVALID")
    if value["mouse_sendinput_calls"] not in (0, 1):
        raise RuntimeError("ACTION_RESULT_INVALID")
    if value["mouse_inputs_sent"] > value["mouse_inputs_requested"]:
        raise RuntimeError("ACTION_RESULT_INVALID")
    if value.get("error_code") not in ACTION_ERROR_CODES:
        raise RuntimeError("ACTION_RESULT_INVALID")
    succeeded = value["succeeded"] is True
    if succeeded != bool(
        value["process_id"] is not None
        and value["window_handle"] is not None
        and value["direct_binding"] is True
        and value["match_count"] == 1
        and value["expected_row_hash_match"] is True
        and value["selected_before"] is False
        and value["row_enabled"] is True
        and value["row_onscreen"] is True
        and value["visual_identity_unique"] is True
        and value["row_rect_valid"] is True
        and value["row_rect_stable"] is True
        and value["conversation_region_unique"] is True
        and value["conversation_rect_valid"] is True
        and value["row_inside_conversations"] is True
        and value["point_inside_row"] is True
        and value["point_inside_conversations"] is True
        and value["foreground_match"] is True
        and value["window_from_point_match"] is True
        and value["uia_point_check_available"] is True
        and value["uia_point_row_match"] is True
        and value["uia_point_match_mode"] in ("runtime_id", "visual_identity")
        and 1 <= value["hit_candidates_checked"] <= 9
        and value["mouse_sendinput_calls"] == 1
        and value["mouse_inputs_requested"] == 3
        and value["mouse_inputs_sent"] == 3
        and value["error_code"] is None
    ):
        raise RuntimeError("ACTION_RESULT_INVALID")
    return dict(value)


def validate_confirmation(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != CONFIRM_KEYS
        or value.get("schema") != CONFIRM_SCHEMA
        or value.get("binding_id") != BINDING_ID
        or not isinstance(value.get("succeeded"), bool)
        or not isinstance(value.get("direct_binding"), bool)
        or not positive_or_none(value.get("process_id"))
        or not positive_or_none(value.get("window_handle"))
        or not nonnegative_int(value.get("match_count"))
        or not nonnegative_int(value.get("elapsed_ms"))
    ):
        raise RuntimeError("CONFIRM_RESULT_INVALID")
    for key in (
        "expected_row_hash_match", "selected_token", "header_match", "identity_match",
    ):
        if not isinstance(value.get(key), bool):
            raise RuntimeError("CONFIRM_RESULT_INVALID")
    marker_count = value.get("group_marker_count")
    if marker_count is not None and not nonnegative_int(marker_count):
        raise RuntimeError("CONFIRM_RESULT_INVALID")
    if value.get("error_code") not in CONFIRM_ERROR_CODES:
        raise RuntimeError("CONFIRM_RESULT_INVALID")
    succeeded = value["succeeded"] is True
    if succeeded != bool(
        value["process_id"] is not None
        and value["window_handle"] is not None
        and value["direct_binding"] is True
        and value["match_count"] == 1
        and value["expected_row_hash_match"] is True
        and value["selected_token"] is True
        and value["header_match"] is True
        and value["group_marker_count"] == 0
        and value["identity_match"] is True
        and value["error_code"] is None
    ):
        raise RuntimeError("CONFIRM_RESULT_INVALID")
    return dict(value)


def copy_json(
    control: Any, guest: Any, guest_path: str, host_path: Path,
    validator: Any,
) -> dict[str, Any]:
    wait_progress(control, guest.FileCopyFromGuest(guest_path, str(host_path), []))
    size = host_path.stat().st_size
    if size < 2 or size > MAX_RESULT_BYTES:
        raise RuntimeError("RESULT_SIZE_INVALID")
    return validator(json.loads(host_path.read_text(encoding="utf-8-sig")))


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
        DIAGNOSTICS / f"qq-dynamic-click-{RELEASE_ID}-{nonce}.json"
    )
    staged_action = GUEST_DATA + rf"\qq-dynamic-click-action-{nonce}.py"
    staged_confirm = GUEST_DATA + rf"\qq-dynamic-click-confirm-{nonce}.py"
    action_result_guest = GUEST_DATA + rf"\qq-dynamic-click-action-{nonce}.json"
    confirm_result_guest = GUEST_DATA + rf"\qq-dynamic-click-confirm-{nonce}.json"
    action_result_host = DIAGNOSTICS / f".qq-dynamic-click-action-{nonce}.json"
    confirm_result_host = DIAGNOSTICS / f".qq-dynamic-click-confirm-{nonce}.json"
    action_roundtrip = DIAGNOSTICS / f".qq-dynamic-click-action-{nonce}.py"
    confirm_roundtrip = DIAGNOSTICS / f".qq-dynamic-click-confirm-{nonce}.py"
    report: dict[str, Any] = {
        "schema": HOST_SCHEMA,
        "diagnostic_nonce": nonce,
        "succeeded": False,
        "action": None,
        "confirmation": None,
        "action_started": False,
        "action_evidence_copied": False,
        "confirmation_started": False,
        "confirmation_evidence_copied": False,
        "error_code": None,
    }
    control = guest = host_session = action_process = confirm_process = None
    clear = bytearray()
    exit_code = 2
    try:
        control = load_control()
        _vbox, host_session, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "dynamic-click-action-" + nonce)
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")

        for local_path, guest_path, roundtrip in (
            (ACTION_SCRIPT, staged_action, action_roundtrip),
            (CONFIRM_SCRIPT, staged_confirm, confirm_roundtrip),
        ):
            wait_progress(
                control, guest.FileCopyToGuest(str(local_path), guest_path, [])
            )
            wait_progress(
                control, guest.FileCopyFromGuest(guest_path, str(roundtrip), [])
            )
            if digest(local_path) != digest(roundtrip):
                raise RuntimeError("GUEST_SCRIPT_HASH_MISMATCH")

        action_process = guest.ProcessCreate(
            GUEST_PYTHON,
            [GUEST_PYTHON, staged_action, "--result", action_result_guest],
            GUEST_DATA,
            [],
            [48],
            120_000,
        )
        report["action_started"] = True
        action_exit = poll_process(action_process)

        # Copy and validate the action evidence before confirmation starts.
        # A later confirmation startup failure must not erase whether input
        # was attempted or how many input records Windows accepted.
        action_result = copy_json(
            control, guest, action_result_guest, action_result_host,
            validate_action,
        )
        report["action"] = action_result
        report["action_evidence_copied"] = True
        if action_exit not in (0, 2):
            raise RuntimeError("GUEST_PROCESS_EXIT_INVALID")
        if (action_exit == 0) is not action_result["succeeded"]:
            raise RuntimeError("ACTION_EXIT_MISMATCH")

        # The action process has terminated and its evidence is durable.  The
        # confirmation process is a distinct, non-overlapping UIA client.
        confirm_process = guest.ProcessCreate(
            GUEST_PYTHON,
            [GUEST_PYTHON, staged_confirm, "--result", confirm_result_guest],
            GUEST_DATA,
            [],
            [48],
            120_000,
        )
        report["confirmation_started"] = True
        confirm_exit = poll_process(confirm_process)

        confirm_result = copy_json(
            control, guest, confirm_result_guest, confirm_result_host,
            validate_confirmation,
        )
        report["confirmation"] = confirm_result
        report["confirmation_evidence_copied"] = True
        if confirm_exit not in (0, 2):
            raise RuntimeError("GUEST_PROCESS_EXIT_INVALID")
        if (confirm_exit == 0) is not confirm_result["succeeded"]:
            raise RuntimeError("CONFIRM_EXIT_MISMATCH")
        report["succeeded"] = bool(
            action_result["succeeded"] and confirm_result["succeeded"]
        )
        if not report["succeeded"]:
            report["error_code"] = "DIAGNOSTIC_NOT_CONFIRMED"
        exit_code = 0 if report["succeeded"] else 2
    except Exception as exc:
        safe_codes = {
            "CONTROL_UNAVAILABLE", "RUNTIME_NOT_STOPPED",
            "GUEST_SCRIPT_HASH_MISMATCH", "GUEST_PROCESS_DID_NOT_START",
            "GUEST_PROCESS_OUTPUT_TOO_LARGE", "GUEST_PROCESS_WAIT_FAILED",
            "GUEST_PROCESS_TIMEOUT", "RESULT_SIZE_INVALID",
            "ACTION_RESULT_INVALID", "CONFIRM_RESULT_INVALID",
            "GUEST_PROCESS_EXIT_INVALID", "ACTION_EXIT_MISMATCH",
            "CONFIRM_EXIT_MISMATCH",
        }
        code = str(exc)
        report["error_code"] = code if code in safe_codes else "HOST_DIAGNOSTIC_FAILED"
        exit_code = 2
    finally:
        for path in (
            action_result_host, confirm_result_host,
            action_roundtrip, confirm_roundtrip,
        ):
            path.unlink(missing_ok=True)
        if guest is not None:
            guest_paths = [staged_action, staged_confirm]
            if report["action_evidence_copied"]:
                guest_paths.append(action_result_guest)
            if report["confirmation_evidence_copied"]:
                guest_paths.append(confirm_result_guest)
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
