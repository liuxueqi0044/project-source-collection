"""Run the frozen runtime's side-effect-free --check through GuestSession."""
from __future__ import annotations
import argparse
import importlib.util
import json
import re
import time
import uuid
from pathlib import Path

ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
START_HELPER = ROOT / r"outputs\qq-vm\install\start_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
CONFIG = r"C:\PMAI\data\runtime-session-1.json"


def load_control():
    spec = importlib.util.spec_from_file_location("pmai_check_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_start_helper():
    spec = importlib.util.spec_from_file_location("pmai_check_start", START_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("START_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"r\d{8}-\d{2}", args.release_id) is None:
        parser.error("bad release id")
    run = uuid.uuid4().hex
    report = {"schema": "pmai-runtime-check-host-v1", "release_id": args.release_id, "succeeded": False}
    guest = host = None
    clear = bytearray()
    try:
        control = load_control()
        _vbox, host, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "config-check-" + run)
        if (
            runtime.get("runtime_process_alive") is True
            and load_start_helper()._guest_pid_alive(
                guest, runtime.get("runtime_process_id")
            )
        ):
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        script = rf"C:\PMAI\app\releases\{args.release_id}\run_vm_runtime.py"
        proc = guest.ProcessCreate(PYTHON, [PYTHON, script, "--config", CONFIG, "--check"], r"C:\PMAI\data", [], [], 120_000)
        if proc.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("CHECK_DID_NOT_START")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = proc.WaitForArray([2, 4], 500)
            if state == 2:
                report.update({"process_status": int(proc.Status), "exit_code": int(proc.ExitCode)})
                if int(proc.ExitCode) != 0:
                    raise RuntimeError("RUNTIME_CONFIG_CHECK_FAILED")
                report["succeeded"] = True
                return 0
            if state == 4:
                raise RuntimeError("CHECK_WAIT_FAILED")
        raise RuntimeError("CHECK_TIMEOUT")
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"runtime-config-check-{args.release_id}-{run}.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        for i in range(len(clear)):
            clear[i] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass

if __name__ == "__main__":
    raise SystemExit(main())
