"""Export a bounded, redacted startup-failure summary from fixed guest logs.

This host-only diagnostic never starts a guest process.  It copies two fixed
runtime logs through GuestControl, retains no log copy, and emits only
whitelisted error codes so message/model text cannot be exported accidentally.
"""
from __future__ import annotations

import json
import os
import re
import argparse
import hashlib
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
OUTPUT = WORKSPACE / r"outputs\qq-vm\install\diagnostics\qq-runtime-failure-summary.json"
LOGS = {
    "stdout": r"C:\PMAI\data\logs\qq-session-runtime.stdout.log",
    "stderr": r"C:\PMAI\data\logs\qq-session-runtime.stderr.log",
}
# Runtime stdout is append-only across supervised runs and can legitimately
# exceed the original 2 MiB diagnostic cap.  The copied file is temporary and
# the exporter still emits only the existing whitelisted/redacted summary.
MAX_BYTES = 8 * 1024 * 1024
SAFE_EVENT = re.compile(
    r"(?:QQ worker startup health check failed|blocked):\s*(?:QQ worker startup health check failed:\s*)?([A-Za-z0-9_.:-]+)"
)
RUN_BOUNDARY = re.compile(
    r"^=== PMAI_RUNTIME_RUN_START run_id=([0-9a-f-]{36}) "
    r"started_at_utc=([^ ]+) web_port=(\d+) ===$"
)
TRACE_FILE = re.compile(r'^\s*File "([^"]+)", line (\d+), in (.+)$')
EXCEPTION = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt))(?::\s*(.*))?$")


def _safe_events(raw: bytes) -> list[str]:
    events: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = SAFE_EVENT.search(line)
        if match:
            code = match.group(1)
            if code not in events:
                events.append(code)
    return events


def _safe_latest_run(raw: bytes) -> dict[str, object] | None:
    """Return only fixed identifiers and whitelisted errors after the last boundary."""
    latest: dict[str, object] | None = None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        boundary = RUN_BOUNDARY.match(line.strip())
        if boundary:
            latest = {
                "run_id": boundary.group(1),
                "started_at_utc": boundary.group(2)[:64],
                "web_port": int(boundary.group(3)),
                "safe_error_codes": [],
            }
            continue
        if latest is None:
            continue
        match = SAFE_EVENT.search(line)
        if match:
            code = match.group(1)
            errors = latest["safe_error_codes"]
            if isinstance(errors, list) and code not in errors:
                errors.append(code)
    return latest


def _safe_stderr_diagnostics(raw: bytes) -> list[str]:
    """Keep stack locations and error types; never retain arbitrary log bodies."""
    result: list[str] = []
    for raw_line in raw.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "Traceback (most recent call last):":
            result.append(line)
            continue
        match = TRACE_FILE.match(line)
        if match:
            result.append(f'File "{Path(match.group(1)).name}", line {match.group(2)}, in {match.group(3)[:120]}')
            continue
        match = EXCEPTION.match(line)
        if match:
            # Error messages are capped and redact common secret-bearing forms.
            message = (match.group(2) or "")[:180]
            message = re.sub(r"(?i)(api[_ -]?key|authorization|bearer|token|secret)\s*[:=]\s*\S+", r"\1=<redacted>", message)
            result.append(match.group(1) + (f": {message}" if message else ""))
            continue
        if line.startswith(("INFO:     Uvicorn running on ", "INFO:     Started server process", "INFO:     Waiting for application startup.", "INFO:     Application startup complete.", "INFO:     Shutting down")):
            result.append(line[:240])
            continue
        # Preserve evidence that an unexpected stderr line exists without
        # exporting arbitrary content such as a prompt or model response.
        result.append("UNCLASSIFIED_STDERR_LINE_SHA256=" + hashlib.sha256(raw_line.encode("utf-8", errors="replace")).hexdigest()[:16])
    return result


_STRUCTURED_EVENT_FIELDS = {
    "pmai-runtime-background-task-failure-v1": (
        "schema", "run_id", "recorded_at", "component", "exception_type", "winerror",
        "errno",
    ),
    "pmai-qq-worker-stage-event-v1": (
        "schema", "run_id", "worker_epoch", "request_id", "kind", "binding_id", "stage",
        "event", "recorded_at", "elapsed_ms", "exception_type",
    ),
    "pmai-qq-worker-terminal-event-v1": (
        "schema", "run_id", "recorded_at", "request_id", "kind", "binding_id",
        "completed_at", "elapsed_ms", "error_code", "worker_exit_code",
        "parent_terminate_reason", "exception_types",
    ),
}


def _safe_structured_events(raw: bytes) -> list[dict[str, object]]:
    """Export only fixed metadata fields from known, text-free event schemas."""
    result: list[dict[str, object]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        fields = _STRUCTURED_EVENT_FIELDS.get(value.get("schema"))
        if fields is None:
            continue
        event: dict[str, object] = {}
        for field in fields:
            if field not in value:
                continue
            item = value[field]
            if item is None or isinstance(item, (bool, int, float)):
                event[field] = item
            elif isinstance(item, str) and len(item) <= 160:
                event[field] = item
            elif field == "exception_types" and isinstance(item, list) and all(
                isinstance(name, str) and len(name) <= 120 for name in item
            ):
                event[field] = item
        if value.get("schema") == "pmai-runtime-background-task-failure-v1":
            frames = value.get("project_frames")
            if isinstance(frames, list):
                safe_frames: list[dict[str, object]] = []
                for frame in frames[:8]:
                    if not isinstance(frame, dict):
                        continue
                    function = frame.get("function")
                    file = frame.get("file")
                    line_number = frame.get("line")
                    if (
                        isinstance(function, str) and len(function) <= 160
                        and isinstance(file, str)
                        and (file == "scripts/run_vm_runtime.py" or file.startswith("messenger_ai/"))
                        and ".." not in file and len(file) <= 320
                        and isinstance(line_number, int) and not isinstance(line_number, bool)
                        and 1 <= line_number <= 10_000_000
                    ):
                        safe_frames.append({"function": function, "file": file, "line": line_number})
                event["project_frames"] = safe_frames
        result.append(event)
    return result


def _copy_log(session: object, name: str, guest_path: str) -> dict[str, object]:
    temporary = OUTPUT.with_name(f".{name}.runtime-log.tmp")
    try:
        if temporary.exists():
            temporary.unlink()
        progress = session.FileCopyFromGuest(guest_path, str(temporary), [])
        progress.WaitForCompletion(30_000)
        if not bool(progress.Completed) or int(progress.ResultCode) != 0:
            return {"available": False, "error_code": "LOG_COPY_FAILED"}
        size = temporary.stat().st_size
        if size > MAX_BYTES:
            return {"available": False, "error_code": "LOG_SIZE_GUARD"}
        raw = temporary.read_bytes()
        value: dict[str, object] = {
            "available": True,
            "bytes": size,
            "safe_error_codes": _safe_events(raw),
            "safe_structured_events": _safe_structured_events(raw),
        }
        if name == "stdout":
            value["latest_run"] = _safe_latest_run(raw)
        if name == "stderr":
            value["safe_diagnostics"] = _safe_stderr_diagnostics(raw)
        return value
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stderr-only", action="store_true")
    args = parser.parse_args()
    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1])
    password = clear.decode("utf-16-le").rstrip("\x00")
    host_session = guest_session = None
    result: dict[str, object] = {"schema": "pmai-qq-runtime-failure-summary-v1", "logs": {}}
    try:
        vbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = vbox.FindMachine(VM_ID)
        if str(machine.Id).lower() != VM_ID or machine.Name != VM_NAME or int(machine.State) != VM_RUNNING:
            raise RuntimeError("VM_GUARD_FAILED")
        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, 1)
        guest_session = host_session.Console.Guest.CreateSession(GUEST_USER, password, "", "pmai-readonly-runtime-log-export")
        if guest_session.WaitForArray([1], 30_000) != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("GUEST_SESSION_FAILED")
        selected = {"stderr": LOGS["stderr"]} if args.stderr_only else LOGS
        result["logs"] = {name: _copy_log(guest_session, name, path) for name, path in selected.items()}
        result["succeeded"] = True
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
        OUTPUT.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
