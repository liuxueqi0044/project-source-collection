"""Terminate only a runtime that already acknowledged graceful stopping.

This is a bounded recovery tool for a runtime whose supervisor status became
stale after VirtualBox closed the launching GuestSession.  It never inspects
QQ, messages, configuration, logs, or credentials beyond opening the fixed
guest session through the existing control helper.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import uuid
from pathlib import Path


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
WITNESS = r"C:\PMAI\data\qq-session-runtime-worker-status.json"
WITNESS_SCHEMA = "pmai-qq-runtime-worker-status-v1"


def _control_module():
    spec = importlib.util.spec_from_file_location("pmai_terminate_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-run-id", required=True)
    args = parser.parse_args()
    expected_run_id = str(uuid.UUID(args.expected_run_id))
    nonce = uuid.uuid4().hex
    report: dict[str, object] = {
        "schema": "pmai-terminate-stopping-runtime-host-v1",
        "run_id": expected_run_id,
        "succeeded": False,
    }
    host = guest = None
    clear = bytearray()
    witness_temp = DIAG / f".stopping-worker-{nonce}.json"
    try:
        control = _control_module()
        _vbox, host, guest, clear, _password = control._open_guest()
        status = control._runtime_status(guest, "terminate-" + nonce)
        if status.get("run_id") != expected_run_id:
            raise RuntimeError("RUN_ID_MISMATCH")
        if status.get("runtime_process_alive") is not True:
            raise RuntimeError("RUNTIME_NOT_REPORTED_ALIVE")
        pid = status.get("runtime_process_id")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RuntimeError("RUNTIME_PID_INVALID")
        witness = control._copy_from_guest(guest, WITNESS, witness_temp)
        if (
            witness.get("schema") != WITNESS_SCHEMA
            or witness.get("run_id") != expected_run_id
            or witness.get("state") != "stopping"
            or "STOPPING" not in witness.get("reason_codes", [])
        ):
            raise RuntimeError("GRACEFUL_STOP_NOT_ACKNOWLEDGED")

        executable = r"C:\Windows\System32\taskkill.exe"
        process = guest.ProcessCreate(
            executable,
            [executable, "/PID", str(pid), "/T", "/F"],
            r"C:\PMAI\data",
            [],
            [48],
            30_000,
        )
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("RUNTIME_TERMINATOR_DID_NOT_START")
        if process.WaitForArray([2], 30_000) != 2 or int(process.ExitCode) != 0:
            raise RuntimeError("RUNTIME_TERMINATION_FAILED")
        report["succeeded"] = True
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        witness_temp.unlink(missing_ok=True)
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"terminate-stopping-runtime-{nonce}.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
