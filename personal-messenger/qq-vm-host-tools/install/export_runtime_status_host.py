"""Export two privacy-safe snapshots of the QQ runtime status via GuestSession.

Raw guest JSON is used only in memory, then discarded. This tool never reads
QQ conversation UI, SQLite records, runtime logs, or configuration files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
STATUS = r"C:\PMAI\data\qq-session-runtime-status.json"
WITNESS = r"C:\PMAI\data\qq-session-runtime-worker-status.json"
MAX_JSON_BYTES = 256 * 1024
CODE = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,127}$")
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
RUNTIME_STATES = frozenset({"starting", "running", "paused", "stopped", "graceful_stopped"})
DRIVER_STATES = frozenset({"available", "paused", "degraded", "unknown"})
WORKER_STATES = frozenset({"available", "degraded", "unavailable", "stopping", "unknown"})


def load_control() -> Any:
    spec = importlib.util.spec_from_file_location("pmai_status_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_code(value: object) -> str | None:
    text = value if isinstance(value, str) else ""
    return text if CODE.fullmatch(text) else None


def safe_enum(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def safe_codes(value: object, *, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, str) and CODE.fullmatch(item)]


def safe_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat()


def safe_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def safe_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def project_status(value: dict[str, Any]) -> dict[str, Any]:
    """Project only fixed operational metadata; exclude metrics and content."""
    run_id = value.get("run_id")
    if value.get("schema") != "pmai-qq-session-runtime-status-v2" or not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise RuntimeError("STATUS_SCHEMA_INVALID")
    return {
        "schema": "pmai-qq-session-runtime-status-v2", "run_id": run_id.lower(),
        "state": safe_enum(value.get("state"), RUNTIME_STATES), "succeeded": safe_bool(value.get("succeeded")),
        "ready": safe_bool(value.get("ready")), "runtime_started": safe_bool(value.get("runtime_started")),
        "runtime_process_alive": safe_bool(value.get("runtime_process_alive")),
        "runtime_process_id": safe_int(value.get("runtime_process_id")),
        "web_reachable": safe_bool(value.get("web_reachable")),
        "driver_state": safe_enum(value.get("driver_state"), DRIVER_STATES),
        "driver_state_reasons": safe_codes(value.get("driver_state_reasons")),
        "started_at": safe_time(value.get("started_at")), "updated_at": safe_time(value.get("updated_at")),
        "stopped_at": safe_time(value.get("stopped_at")), "exit_code": safe_int(value.get("exit_code")),
        "error_code": safe_code(value.get("error_code")),
    }


def project_witness(value: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Whitelist same-run worker liveness only; never export request/binding data."""
    if value.get("schema") != "pmai-qq-runtime-worker-status-v1" or value.get("run_id") != run_id:
        return {"available": None, "reason_codes": ["WORKER_WITNESS_SCOPE_MISMATCH"]}
    return {
        "available": safe_bool(value.get("available")), "state": safe_enum(value.get("state"), WORKER_STATES),
        "worker_alive": safe_bool(value.get("worker_alive")),
        "worker_process_id": safe_int(value.get("worker_process_id")),
        "worker_generation": safe_int(value.get("worker_generation")),
        "worker_exit_code": safe_int(value.get("worker_exit_code")),
        "updated_at": safe_time(value.get("updated_at")), "reason_codes": safe_codes(value.get("reason_codes")),
    }


def read_guest_json(control: Any, guest: object, guest_path: str, temporary: Path) -> tuple[dict[str, Any], str]:
    temporary.unlink(missing_ok=True)
    control._wait_progress(guest.FileCopyFromGuest(guest_path, str(temporary), []), 30_000)
    raw = temporary.read_bytes()
    if not 2 <= len(raw) <= MAX_JSON_BYTES:
        raise RuntimeError("GUEST_JSON_SIZE_INVALID")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("GUEST_JSON_NOT_OBJECT")
    return value, hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only, redacted QQ runtime status export")
    parser.add_argument("--interval-seconds", type=int, default=2, choices=range(1, 11))
    args = parser.parse_args()
    nonce = uuid.uuid4().hex
    report: dict[str, Any] = {"schema": "pmai-runtime-status-host-v2", "succeeded": False}
    guest = host = None
    clear = bytearray()
    temporary = [DIAG / f".runtime-status-{nonce}-{index}.json" for index in (1, 2)]
    witness_temporary = DIAG / f".runtime-witness-{nonce}.json"
    try:
        control = load_control()
        _vbox, host, guest, clear, _password = control._open_guest()
        first_raw, first_fingerprint = read_guest_json(control, guest, STATUS, temporary[0])
        first = project_status(first_raw)
        time.sleep(args.interval_seconds)
        second_raw, second_fingerprint = read_guest_json(control, guest, STATUS, temporary[1])
        second = project_status(second_raw)
        same_run = first["run_id"] == second["run_id"]
        try:
            witness_raw, _ = read_guest_json(control, guest, WITNESS, witness_temporary)
            worker = project_witness(witness_raw, second["run_id"])
        except Exception:
            worker = {"available": None, "reason_codes": ["WORKER_WITNESS_UNAVAILABLE"]}
        report.update({
            "runtime": second, "worker": worker,
            "observation": {"sample_count": 2, "interval_seconds": args.interval_seconds,
                "same_run": same_run,
                "status_updated_during_interval": same_run and first_fingerprint != second_fingerprint,
                "first_updated_at": first["updated_at"], "second_updated_at": second["updated_at"]},
            "succeeded": True,
        })
        return 0
    except Exception as exc:
        report["error_code"] = safe_code(str(exc)) or "STATUS_EXPORT_FAILED"
        return 2
    finally:
        for path in (*temporary, witness_temporary):
            path.unlink(missing_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"runtime-status-host-{nonce}.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        for index in range(len(clear)):
            clear[index] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass


if __name__ == "__main__":
    raise SystemExit(main())
