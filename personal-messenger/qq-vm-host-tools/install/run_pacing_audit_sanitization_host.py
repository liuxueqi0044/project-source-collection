"""Run one frozen guest pacing-audit sanitization command through GuestSession."""
from __future__ import annotations

import argparse
import contextlib
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
GUEST_RUNTIME_ROOT = GUEST_DATA + r"\runtime\qq-default-account"
HOST_SCHEMA = "pmai-pacing-audit-sanitization-host-v1"
GUEST_SCHEMA = "pmai-pacing-audit-sanitization-result-v1"
MIGRATION_VERSION = "redacted-v1"
_RELEASE = re.compile(r"r\d{8}-\d{2}")
_OPERATOR = re.compile(r"[a-z][a-z0-9_-]{2,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_:-]{1,127}")
_HASHES = (
    "audit_before_sha256",
    "audit_after_sha256",
    "plans_before_sha256",
    "plans_after_sha256",
    "due_outbox_before_sha256",
    "due_outbox_after_sha256",
)
_COUNTS = ("audit_rows", "audit_rows_rewritten", "plans", "due_outbox")


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


def _assert_runtime_stopped(
    control: Any, start_helper: Any, guest: Any, suffix: str
) -> None:
    status = control._runtime_status(guest, "pacing-audit-" + suffix)
    if status.get("runtime_process_alive") is not False:
        raise RuntimeError("RUNTIME_NOT_STOPPED")
    pid = status.get("runtime_process_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise RuntimeError("RUNTIME_PID_UNAVAILABLE")
    if start_helper._guest_pid_alive(guest, pid):
        raise RuntimeError("RUNTIME_NOT_STOPPED")


def _validated_guest_report(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("schema") != GUEST_SCHEMA:
        raise RuntimeError("PACING_AUDIT_REPORT_SCHEMA_MISMATCH")
    if raw.get("status") == "rejected":
        if set(raw) != {"schema", "status", "error_code"}:
            raise RuntimeError("PACING_AUDIT_REPORT_FIELDS_INVALID")
        error = raw.get("error_code")
        if not isinstance(error, str) or _SAFE_CODE.fullmatch(error) is None:
            raise RuntimeError("PACING_AUDIT_REPORT_ERROR_INVALID")
        return dict(raw)
    allowed = {
        "schema",
        "status",
        "operator_id",
        "migration_version",
        "migration_applied",
        "idempotent",
        "business_byte_identical",
        "audit_before_sha256",
        "audit_after_sha256",
        "plans_before_sha256",
        "plans_after_sha256",
        "due_outbox_before_sha256",
        "due_outbox_after_sha256",
        "counts",
    }
    if set(raw) != allowed or raw.get("status") not in {"applied", "noop"}:
        raise RuntimeError("PACING_AUDIT_REPORT_FIELDS_INVALID")
    operator = raw.get("operator_id")
    if not isinstance(operator, str) or _OPERATOR.fullmatch(operator) is None:
        raise RuntimeError("PACING_AUDIT_REPORT_OPERATOR_INVALID")
    if raw.get("migration_version") != MIGRATION_VERSION:
        raise RuntimeError("PACING_AUDIT_REPORT_VERSION_INVALID")
    for field in ("migration_applied", "idempotent"):
        if not isinstance(raw.get(field), bool):
            raise TypeError("PACING_AUDIT_REPORT_BOOLEAN_INVALID")
    if raw.get("business_byte_identical") is not True:
        raise RuntimeError("PACING_AUDIT_BUSINESS_MUTATED")
    for field in _HASHES:
        value = raw.get(field)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise RuntimeError("PACING_AUDIT_REPORT_HASH_INVALID")
    if raw["plans_before_sha256"] != raw["plans_after_sha256"]:
        raise RuntimeError("PACING_AUDIT_BUSINESS_MUTATED")
    if raw["due_outbox_before_sha256"] != raw["due_outbox_after_sha256"]:
        raise RuntimeError("PACING_AUDIT_BUSINESS_MUTATED")
    counts = raw.get("counts")
    if not isinstance(counts, dict) or set(counts) != set(_COUNTS):
        raise RuntimeError("PACING_AUDIT_REPORT_COUNTS_INVALID")
    for field in _COUNTS:
        value = counts.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TypeError("PACING_AUDIT_REPORT_COUNTS_INVALID")
    if counts["audit_rows_rewritten"] > counts["audit_rows"]:
        raise RuntimeError("PACING_AUDIT_REPORT_COUNTS_INVALID")
    return {key: raw[key] for key in allowed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--operator-id", required=True)
    args = parser.parse_args()
    if _RELEASE.fullmatch(args.release_id) is None:
        parser.error("bad release id")
    if _OPERATOR.fullmatch(args.operator_id) is None:
        parser.error("bad operator id")

    nonce = uuid.uuid4().hex
    report: dict[str, object] = {
        "schema": HOST_SCHEMA,
        "release_id": args.release_id,
        "operator_id": args.operator_id,
        "succeeded": False,
    }
    guest = host = None
    clear = bytearray()
    guest_report = GUEST_DATA + rf"\pacing-audit-sanitization-{nonce}.json"
    host_guest_report = DIAG / (".pacing-audit-sanitization-" + nonce + ".tmp.json")
    try:
        control = _load(CONTROL, "pmai_pacing_audit_control")
        start_helper = _load(START_HELPER, "pmai_pacing_audit_start")
        _vbox, host, guest, clear, _password = control._open_guest()
        _assert_runtime_stopped(control, start_helper, guest, nonce)
        script = (
            rf"C:\PMAI\app\releases\{args.release_id}"
            r"\sanitize_pacing_audit_guest.py"
        )
        command = [
            GUEST_PYTHON,
            script,
            "--runtime-root",
            GUEST_RUNTIME_ROOT,
            "--operator-id",
            args.operator_id,
            "--report",
            guest_report,
        ]
        process = guest.ProcessCreate(
            GUEST_PYTHON, command, GUEST_DATA, [], [], 120_000
        )
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("PACING_AUDIT_DID_NOT_START")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = process.WaitForArray([2, 4], 500)
            if state == 2:
                report["process_status"] = int(process.Status)
                report["exit_code"] = int(process.ExitCode)
                break
            if state == 4:
                raise RuntimeError("PACING_AUDIT_WAIT_FAILED")
        else:
            raise RuntimeError("PACING_AUDIT_TIMEOUT")
        raw = control._copy_from_guest(guest, guest_report, host_guest_report)
        guest_result = _validated_guest_report(raw)
        report["guest_result"] = guest_result
        if int(report["exit_code"]) != 0 or guest_result.get("status") == "rejected":
            raise RuntimeError("PACING_AUDIT_REJECTED")
        report["succeeded"] = True
        return 0
    except Exception as exc:  # noqa: BLE001 - host report must fail closed
        value = str(exc)
        report["error_code"] = (
            value if _SAFE_CODE.fullmatch(value) else type(exc).__name__
        )
        return 2
    finally:
        try:
            host_guest_report.unlink()
        except FileNotFoundError:
            pass
        for index in range(len(clear)):
            clear[index] = 0
        if guest is not None:
            with contextlib.suppress(Exception):
                guest.Close()
        if host is not None:
            with contextlib.suppress(Exception):
                host.UnlockMachine()
        _atomic_json(
            DIAG / ("pacing-audit-sanitization-" + args.release_id + "-" + nonce + ".json"),
            report,
        )


if __name__ == "__main__":
    raise SystemExit(main())
