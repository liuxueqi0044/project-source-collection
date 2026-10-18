"""Export contact2's settled-lane evidence without reading message payloads."""
from __future__ import annotations

import argparse
import hashlib
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
START = ROOT / r"outputs\qq-vm\install\start_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
GUEST_DATA = r"C:\PMAI\data"
CONVERSATION = "qq-session-conversation-2"
RELEASE = "r20260912-07"
HOST_SCHEMA = "pmai-qq-contact2-lanes-host-v1"
SUMMARY_SCHEMA = "pmai-qq-contact2-derived-evidence-v1"
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")

GUEST_SOURCE = r'''from __future__ import annotations
import json, os, sqlite3
from pathlib import Path

DATA = Path(r"C:\PMAI\data\runtime\qq-default-account")
CONVERSATION = "qq-session-conversation-2"
REPORT = Path(os.environ["PMAI_CONTACT2_REPORT"])
LAST_TABLE = "none"
LAST_DB = "none"

def db(name):
    global LAST_DB
    LAST_DB = name
    return sqlite3.connect(f"file:{(DATA / name).as_posix()}?mode=ro", uri=True)

def rows(conn, sql, params=()):
    global LAST_TABLE
    LAST_TABLE = sql.split("FROM", 1)[1].split()[0]
    return [{"id": r[0], "status": r[1], **({"type": r[2]} if len(r) > 2 else {})}
            for r in conn.execute(sql, params).fetchall()]

def main():
    bridge = db("qq-vm-bridge.sqlite3")
    runtime = db("runtime.sqlite3")
    hub = db("hub.sqlite3")
    pacing = db("pacing.sqlite3")
    try:
        result = {
            "schema": "pmai-qq-contact2-lanes-guest-v1",
            "conversation_id": CONVERSATION,
            "lanes": {
                "bridge": rows(bridge, """SELECT operation_id,status FROM qq_vm_ops
                    WHERE conversation_id=? AND status NOT IN ('verified','failed','uncertain','cancelled')
                    ORDER BY operation_id""", (CONVERSATION,)),
                "runtime_event_outbox": rows(runtime, """SELECT event_id,status FROM runtime_event_outbox
                    WHERE aggregate_id=? AND status IN ('pending','dispatching') ORDER BY event_id""", (CONVERSATION,)),
                "runtime_planning_jobs": rows(runtime, """SELECT conversation_id,status FROM runtime_planning_jobs
                    WHERE conversation_id=? AND status IN ('pending','running')""", (CONVERSATION,)),
                "runtime_plan_artifacts": rows(runtime, """SELECT pacing_plan_id,status FROM runtime_plan_artifacts
                    WHERE conversation_id=? AND status='waiting'""", (CONVERSATION,)),
                "runtime_segment_executions": rows(runtime, """SELECT operation_id,status FROM runtime_segment_executions
                    WHERE conversation_id=? AND status!='verified' ORDER BY operation_id""", (CONVERSATION,)),
                "hub_send_operations": rows(hub, """SELECT s.operation_id,s.status FROM send_operations s
                    JOIN drafts d ON d.draft_id=s.draft_id WHERE d.conversation_id=?
                    AND s.status NOT IN ('verified','failed','uncertain','cancelled') ORDER BY s.operation_id""", (CONVERSATION,)),
                "hub_outbox": rows(hub, """SELECT o.outbox_id,o.status,o.event_type FROM outbox o
                    LEFT JOIN send_operations s ON s.operation_id=o.aggregate_id
                    LEFT JOIN drafts d ON d.draft_id=s.draft_id
                    WHERE o.status IN ('pending','dispatching')
                    AND (o.aggregate_id=? OR d.conversation_id=?) ORDER BY o.outbox_id""", (CONVERSATION, CONVERSATION)),
                "pacing_plans": rows(pacing, """SELECT pacing_plan_id,status FROM m10_plans
                    WHERE conversation_id=? AND status IN ('waiting','due_for_revalidation')""", (CONVERSATION,)),
                "pacing_due_outbox": rows(pacing, """SELECT o.outbox_id,o.status FROM m10_due_outbox o
                    JOIN m10_plans p ON p.pacing_plan_id=o.pacing_plan_id
                    WHERE p.conversation_id=? AND o.status IN ('pending','dispatching') ORDER BY o.outbox_id""", (CONVERSATION,)),
            },
        }
        result["all_settled"] = all(not value for value in result["lanes"].values())
        result["succeeded"] = result["all_settled"]
        temporary = REPORT.with_name("." + REPORT.name + ".tmp")
        temporary.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
        os.replace(temporary, REPORT)
        return 0
    finally:
        bridge.close(); runtime.close(); hub.close(); pacing.close()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        temporary = REPORT.with_name("." + REPORT.name + ".tmp")
        temporary.write_text(json.dumps({
            "schema": "pmai-qq-contact2-lanes-guest-v1",
            "conversation_id": CONVERSATION,
            "succeeded": False,
            "error_code": type(exc).__name__ + ":" + LAST_DB + ":" + LAST_TABLE,
        }, sort_keys=True), encoding="utf-8")
        os.replace(temporary, REPORT)
        raise SystemExit(2)
'''


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(name.upper() + "_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        tmp.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def derive_summary(report: dict[str, object]) -> dict[str, object]:
    manifest_path = ROOT / r"outputs\personal-messenger-ai-v5\outputs\guest-local-releases\r20260912-07\manifest.json"
    probe_path = DIAG / "qq-contact2-recovery-probe.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wheel = next(item for item in manifest["files"] if item.get("path") == "personal_messenger_ai-0.1.0-py3-none-any.whl")
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    health_ok = probe.get("health_status") == "ok"
    observe_ok = probe.get("observe_status") == "ok"
    all_settled = report.get("all_settled") is True
    succeeded = bool(health_ok and observe_ok and all_settled)
    return {
        "schema": SUMMARY_SCHEMA,
        "release_id": RELEASE,
        "conversation_id": CONVERSATION,
        "manifest_release_id": manifest.get("release_id"),
        "wheel_sha256": wheel.get("sha256"),
        "wheel_length": wheel.get("length"),
        "probe_schema": probe.get("schema"),
        "probe_succeeded": probe.get("succeeded"),
        "probe_schema_derived_field_bug": probe.get("succeeded") is False,
        "health_status": probe.get("health_status"),
        "observe_status": probe.get("observe_status"),
        "identity_chain_verified": bool(health_ok and observe_ok),
        "all_lanes_settled": all_settled,
        "succeeded": succeeded,
        "settled": succeeded,
        "release_decision": "release_allowed" if succeeded else "no_release",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", default=RELEASE)
    args = parser.parse_args()
    if args.release_id != RELEASE or _SAFE.fullmatch(args.release_id) is None:
        parser.error("release must be r20260912-07")
    nonce = uuid.uuid4().hex
    host = guest = None
    clear = bytearray()
    host_script = DIAG / (".contact2-lanes-" + nonce + ".py")
    guest_script = GUEST_DATA + rf"\contact2-lanes-{nonce}.py"
    guest_report = GUEST_DATA + rf"\contact2-lanes-{nonce}.json"
    copied = DIAG / (".contact2-lanes-" + nonce + "-guest.json")
    report: dict[str, object] = {"schema": HOST_SCHEMA, "release_id": RELEASE, "conversation_id": CONVERSATION, "succeeded": False}
    try:
        control = load(CONTROL, "pmai_contact2_control")
        start = load(START, "pmai_contact2_start")
        _vbox, host, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "contact2-lanes-" + nonce)
        pid = runtime.get("runtime_process_id")
        if runtime.get("runtime_process_alive") is not False or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        if start._guest_pid_alive(guest, pid):
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        host_script.parent.mkdir(parents=True, exist_ok=True)
        guest_source = GUEST_SOURCE.replace(
            'Path(os.environ["PMAI_CONTACT2_REPORT"])',
            f"Path({guest_report!r})",
        )
        host_script.write_text(guest_source, encoding="utf-8")
        control._copy_to_guest(guest, host_script, guest_script)
        process = guest.ProcessCreate(GUEST_PYTHON, [GUEST_PYTHON, guest_script], GUEST_DATA, [], [], 120_000)
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("LANE_EXPORT_DID_NOT_START")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = process.WaitForArray([2, 4], 500)
            if state == 2:
                report["exit_code"] = int(process.ExitCode)
                break
            if state == 4:
                raise RuntimeError("LANE_EXPORT_WAIT_FAILED")
        else:
            raise RuntimeError("LANE_EXPORT_TIMEOUT")
        guest_result = control._copy_from_guest(guest, guest_report, copied)
        if guest_result.get("schema") != "pmai-qq-contact2-lanes-guest-v1" or guest_result.get("conversation_id") != CONVERSATION:
            raise RuntimeError("LANE_EXPORT_SCHEMA_MISMATCH")
        if report.get("exit_code") != 0:
            error = guest_result.get("error_code")
            report["guest_error_code"] = error if isinstance(error, str) and _SAFE.fullmatch(error) else "GUEST_EXPORT_FAILED"
            raise RuntimeError("LANE_EXPORT_FAILED")
        lanes = guest_result.get("lanes")
        if not isinstance(lanes, dict) or set(lanes) != {"bridge", "runtime_event_outbox", "runtime_planning_jobs", "runtime_plan_artifacts", "runtime_segment_executions", "hub_send_operations", "hub_outbox", "pacing_plans", "pacing_due_outbox"}:
            raise RuntimeError("LANE_EXPORT_FIELDS_INVALID")
        if any(not isinstance(items, list) for items in lanes.values()):
            raise RuntimeError("LANE_EXPORT_ROWS_INVALID")
        report.update({"lanes": lanes, "all_settled": guest_result.get("all_settled")})
        if guest_result.get("succeeded") is not True:
            report["guest_error_code"] = "LANES_NOT_SETTLED"
            raise RuntimeError("LANES_NOT_SETTLED")
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        host_script.unlink(missing_ok=True)
        copied.unlink(missing_ok=True)
        for index in range(len(clear)):
            clear[index] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass
        atomic(DIAG / ("contact2-lanes-" + nonce + ".json"), report)
        if report.get("succeeded") is True:
            atomic(DIAG / ("contact2-derived-evidence-" + nonce + ".json"), derive_summary(report))


if __name__ == "__main__":
    raise SystemExit(main())
