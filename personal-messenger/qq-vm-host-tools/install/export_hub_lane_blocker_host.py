"""Export only the hub lane metadata that blocks one cursor reanchor.

The guest query opens hub.sqlite3 in SQLite read-only mode and emits a fixed
allowlist over process stdout.  It never selects draft text, payload JSON,
message rows, prompts, or secrets, and it does not write guest files.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
DATA_DIR = r"C:\PMAI\data\runtime\qq-default-account"
HUB_DB = DATA_DIR + r"\hub.sqlite3"
CONVERSATION_ID = "qq-session-conversation-1"
MAX_RESULT_BYTES = 16 * 1024
SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def load_control() -> Any:
    spec = importlib.util.spec_from_file_location("pmai_hub_lane_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_value(value: object) -> str | None:
    return value if isinstance(value, str) and SAFE.fullmatch(value) else None


def safe_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _guest_program() -> str:
    # This source is fixed. The only variable is passed as a Python argv item,
    # never interpolated into SQL. The SELECT lists intentionally omit every
    # text/payload column from drafts, outbox, messages, and events.
    return r'''
import json, os, sqlite3, sys
conversation_id = sys.argv[1]
result_path = sys.argv[2]
path = r"C:\PMAI\data\runtime\qq-default-account\hub.sqlite3"
payload = {"schema":"pmai-hub-lane-readonly-v1", "succeeded":False}
try:
    with sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True) as db:
        operations = [dict(zip(("operation_id","draft_id","status","created_at","updated_at","error_code"), row)) for row in db.execute(
            """SELECT s.operation_id,s.draft_id,s.status,s.created_at,s.updated_at,s.error_code
               FROM send_operations s JOIN drafts d ON d.draft_id=s.draft_id
               WHERE d.conversation_id=? ORDER BY s.created_at,s.operation_id""", (conversation_id,))]
        outbox = [dict(zip(("outbox_id","status","aggregate_id","event_type","created_at","available_at","claimed_at","delivered_at","attempt_count"), row)) for row in db.execute(
            """SELECT o.outbox_id,o.status,o.aggregate_id,o.event_type,o.created_at,
                      o.available_at,o.claimed_at,o.delivered_at,o.attempt_count
               FROM outbox o LEFT JOIN send_operations s ON s.operation_id=o.aggregate_id
                 LEFT JOIN drafts d ON d.draft_id=s.draft_id
               WHERE o.status IN ('pending','dispatching')
                 AND (o.aggregate_id=? OR d.conversation_id=?)
               ORDER BY o.created_at,o.outbox_id""", (conversation_id, conversation_id))]
    payload.update({"succeeded":True,"send_operations":operations,"blocking_outbox":outbox})
except Exception as exc:
    payload["error_code"] = "GUEST_QUERY_FAILED_" + type(exc).__name__.upper()
temporary = result_path + '.tmp'
with open(temporary, 'w', encoding='utf-8') as stream:
    stream.write(json.dumps(payload, sort_keys=True, separators=(',',':')))
os.replace(temporary, result_path)
'''


def _read_guest(guest: object, nonce: str) -> dict[str, Any]:
    executable = r"C:\PMAI\app\.venv\Scripts\python.exe"
    result_guest = rf"C:\PMAI\data\hub-lane-blocker-{nonce}.json"
    result_host = DIAG / f".hub-lane-blocker-{nonce}.json"
    program = "import base64;exec(base64.b64decode(%r))" % base64.b64encode(_guest_program().encode("utf-8")).decode("ascii")
    process = guest.ProcessCreate(executable, [executable, "-c", program, CONVERSATION_ID, result_guest], DATA_DIR, [], [], 60_000)
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("READONLY_PROCESS_DID_NOT_START")
    if process.WaitForArray([2], 30_000) != 2 or int(process.ExitCode) != 0:
        raise RuntimeError("READONLY_PROCESS_FAILED")
    try:
        progress = guest.FileCopyFromGuest(result_guest, str(result_host), [])
        progress.WaitForCompletion(30_000)
        if not bool(progress.Completed) or int(progress.ResultCode) != 0:
            raise RuntimeError("READONLY_RESULT_COPY_FAILED")
        if result_host.stat().st_size > MAX_RESULT_BYTES:
            raise RuntimeError("READONLY_RESULT_SIZE_INVALID")
        value = json.loads(result_host.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise RuntimeError("READONLY_RESULT_INVALID")
        return value
    finally:
        result_host.unlink(missing_ok=True)
        try:
            guest.FileDelete(result_guest)
        except Exception:
            pass


def _project(value: dict[str, Any]) -> dict[str, list[dict[str, object]]]:
    if value.get("schema") != "pmai-hub-lane-readonly-v1":
        raise RuntimeError("READONLY_RESULT_SCHEMA_INVALID")
    if value.get("succeeded") is not True:
        raise RuntimeError("READONLY_GUEST_QUERY_FAILED")
    output: dict[str, list[dict[str, object]]] = {"send_operations": [], "blocking_outbox": []}
    operation_keys = ("operation_id", "draft_id", "status", "created_at", "updated_at", "error_code")
    outbox_keys = ("outbox_id", "status", "aggregate_id", "event_type", "created_at", "available_at", "claimed_at", "delivered_at", "attempt_count")
    for source, keys in (("send_operations", operation_keys), ("blocking_outbox", outbox_keys)):
        rows = value.get(source)
        if not isinstance(rows, list) or len(rows) > 100:
            raise RuntimeError("READONLY_RESULT_ROWS_INVALID")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(keys):
                raise RuntimeError("READONLY_RESULT_ROW_SCHEMA_INVALID")
            projected: dict[str, object] = {}
            for key in keys:
                raw = row[key]
                if key.endswith("_at"):
                    projected[key] = safe_time(raw)
                elif key == "attempt_count":
                    projected[key] = raw if isinstance(raw, int) and raw >= 0 else None
                else:
                    projected[key] = safe_value(raw)
            output[source].append(projected)
    return output


def main() -> int:
    nonce = uuid.uuid4().hex
    report: dict[str, Any] = {"schema": "pmai-hub-lane-blocker-host-v1", "conversation_id": CONVERSATION_ID, "succeeded": False}
    guest = host = None
    clear = bytearray()
    status_temp = DIAG / f".qq-runtime-status-hub-lane-{nonce}.tmp.json"
    try:
        control = load_control()
        _vbox, host, guest, clear, _password = control._open_guest()
        status = control._runtime_status(guest, f"hub-lane-{nonce}")
        if status.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        raw = _read_guest(guest, nonce)
        report.update(_project(raw))
        report.update({"runtime_stopped": True, "succeeded": True})
        return 0
    except Exception as exc:
        report["error_code"] = safe_value(str(exc)) or "HUB_LANE_READ_FAILED"
        return 2
    finally:
        status_temp.unlink(missing_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"hub-lane-blocker-{nonce}.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
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
