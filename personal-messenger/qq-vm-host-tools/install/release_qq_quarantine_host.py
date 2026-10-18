"""Prepared-only host runner for one exact contact-2 quarantine release.

It intentionally has no default evidence paths: an operator must provide the two
already-exported, settled reports when an approved execution is requested.
"""
from __future__ import annotations

import argparse
import base64
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
RELEASE_ID = "r20260912-07"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
GUEST_DATA = r"C:\PMAI\data"
BRIDGE_DB = GUEST_DATA + r"\runtime\qq-default-account\qq-vm-bridge.sqlite3"
FAILED_RUN_ID = "2f0f55f0-00f5-4287-b5ee-baa48d8117f5"
CONVERSATION_ID = "qq-session-conversation-2"
BINDING_ID = "session-contact-2"
BINDING_REVISION = 1
REQUEST_ID = "95a49385-57dd-45af-b0d9-92321f1bfedc"
FAILED_GENERATION = 1
OPERATOR_ID = "codex_architect"
REASON_CODE = "read_only_timeout_successor_healthy"
HOST_SCHEMA = "pmai-qq-quarantine-release-host-v1"
GUEST_SCHEMA = "pmai-qq-observation-quarantine-release-result-v1"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_:-]{1,127}$")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _report_digest(path: Path, label: str) -> str:
    if not path.is_file() or path.stat().st_size > 131_072:
        raise RuntimeError(label + "_REPORT_UNAVAILABLE")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(label + "_REPORT_INVALID") from exc
    if not isinstance(value, dict) or value.get("succeeded") is not True:
        raise RuntimeError(label + "_NOT_SETTLED")
    if label == "FOUR_STORE":
        valid = (
            value.get("schema") == "pmai-qq-contact2-lanes-host-v1"
            and value.get("release_id") == RELEASE_ID
            and value.get("conversation_id") == CONVERSATION_ID
            and value.get("all_settled") is True
            and isinstance(value.get("lanes"), dict)
            and all(items == [] for items in value["lanes"].values())
        )
    elif label == "IDENTITY":
        valid = (
            value.get("schema") == "pmai-qq-contact2-derived-evidence-v1"
            and value.get("release_id") == RELEASE_ID
            and value.get("manifest_release_id") == RELEASE_ID
            and value.get("conversation_id") == CONVERSATION_ID
            and value.get("settled") is True
            and value.get("all_lanes_settled") is True
            and value.get("identity_chain_verified") is True
            and value.get("health_status") == "ok"
            and value.get("observe_status") == "ok"
            and value.get("release_decision") == "release_allowed"
        )
    else:
        raise RuntimeError("REPORT_LABEL_INVALID")
    if not valid:
        raise RuntimeError(label + "_NOT_SETTLED")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_stopped(control: Any, start: Any, guest: Any, suffix: str) -> None:
    status = control._runtime_status(guest, "quarantine-release-" + suffix)
    if status.get("runtime_process_alive") is not False:
        raise RuntimeError("RUNTIME_NOT_STOPPED")
    pid = status.get("runtime_process_id")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise RuntimeError("RUNTIME_PID_UNAVAILABLE")
    if start._guest_pid_alive(guest, pid):
        raise RuntimeError("RUNTIME_PID_STILL_ALIVE")


def _guest_wrapper() -> str:
    # Call the frozen release entry-point's execute_release function directly.
    # Result transfer is an atomic file; GuestProcess stdout is never observed.
    return r'''import argparse,importlib.util,json,os,sys
script,report,bridge,run_id,conversation,binding,revision,request,generation,operator,reason=sys.argv[1:]
try:
 sys.path.insert(0,os.path.dirname(script))
 spec=importlib.util.spec_from_file_location("pmai_quarantine_release",script)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 result=module.execute_release(argparse.Namespace(sqlite_path=bridge,expected_run_id=run_id,conversation_id=conversation,binding_id=binding,binding_revision=int(revision),request_id=request,failed_generation=int(generation),operator_id=operator,reason_code=reason))
 payload={"schema":"pmai-qq-observation-quarantine-release-result-v1",**result}
except ValueError as exc:
 payload={"schema":"pmai-qq-observation-quarantine-release-result-v1","status":"rejected","error_code":str(exc)}
except Exception as exc:
 payload={"schema":"pmai-qq-observation-quarantine-release-result-v1","status":"failed","error_code":type(exc).__name__}
temporary=report+".tmp"
open(temporary,"w",encoding="utf-8").write(json.dumps(payload,sort_keys=True,separators=(",",":")))
os.replace(temporary,report)
'''


def _validate_guest(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("schema") != GUEST_SCHEMA:
        raise RuntimeError("GUEST_REPORT_INVALID")
    status = raw.get("status")
    if status in {"released", "already_released"}:
        if set(raw) != {"schema", "status", "release_id", "released_at"}:
            raise RuntimeError("GUEST_REPORT_FIELDS_INVALID")
        if not isinstance(raw.get("release_id"), str) or _UUID.fullmatch(raw["release_id"]) is None:
            raise RuntimeError("GUEST_REPORT_RELEASE_INVALID")
        if not isinstance(raw.get("released_at"), str) or len(raw["released_at"]) > 64:
            raise RuntimeError("GUEST_REPORT_TIME_INVALID")
    elif status in {"rejected", "failed"}:
        if set(raw) != {"schema", "status", "error_code"} or not isinstance(raw.get("error_code"), str) or _CODE.fullmatch(raw["error_code"]) is None:
            raise RuntimeError("GUEST_REPORT_REJECTION_INVALID")
    else:
        raise RuntimeError("GUEST_REPORT_STATUS_INVALID")
    return dict(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Approved execution only; no default evidence paths.")
    parser.add_argument("--four-store-settled-report", type=Path, required=True)
    parser.add_argument("--identity-evidence-report", type=Path, required=True)
    args = parser.parse_args()
    nonce = uuid.uuid4().hex
    report: dict[str, object] = {"schema": HOST_SCHEMA, "release_id": RELEASE_ID, "conversation_id": CONVERSATION_ID, "succeeded": False}
    guest = host = None
    clear = bytearray()
    guest_result = GUEST_DATA + rf"\quarantine-release-{nonce}.json"
    host_result = DIAG / f".quarantine-release-{nonce}.json"
    try:
        # These reports must be created by prior read-only diagnostics and show
        # explicit success+settlement before opening a guest session.
        report["four_store_report_sha256"] = _report_digest(args.four_store_settled_report, "FOUR_STORE")
        report["identity_report_sha256"] = _report_digest(args.identity_evidence_report, "IDENTITY")
        control = _load(CONTROL, "quarantine_release_control")
        start = _load(START, "quarantine_release_start")
        _vbox, host, guest, clear, _password = control._open_guest()
        _require_stopped(control, start, guest, nonce)
        script = rf"C:\PMAI\app\releases\{RELEASE_ID}\release_qq_observation_quarantine.py"
        encoded = base64.b64encode(_guest_wrapper().encode("utf-8")).decode("ascii")
        process = guest.ProcessCreate(GUEST_PYTHON, [GUEST_PYTHON, "-c", "import base64;exec(base64.b64decode(%r))" % encoded,
            script, guest_result, BRIDGE_DB, FAILED_RUN_ID, CONVERSATION_ID, BINDING_ID, str(BINDING_REVISION), REQUEST_ID,
            str(FAILED_GENERATION), OPERATOR_ID, REASON_CODE], GUEST_DATA, [], [], 60_000)
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("RELEASE_DID_NOT_START")
        if process.WaitForArray([2], 60_000) != 2:
            raise RuntimeError("RELEASE_TIMEOUT")
        report["exit_code"] = int(process.ExitCode)
        raw = control._copy_from_guest(guest, guest_result, host_result)
        result = _validate_guest(raw)
        report["guest_result"] = result
        if int(process.ExitCode) != 0 or result["status"] not in {"released", "already_released"}:
            raise RuntimeError("RELEASE_REJECTED")
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc) if _CODE.fullmatch(str(exc)) else type(exc).__name__
        return 2
    finally:
        host_result.unlink(missing_ok=True)
        if guest is not None:
            try: guest.FileDelete(guest_result)
            except Exception: pass
        for index in range(len(clear)): clear[index] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass
        _atomic(DIAG / f"quarantine-release-{RELEASE_ID}-{nonce}.json", report)

if __name__ == "__main__":
    raise SystemExit(main())
