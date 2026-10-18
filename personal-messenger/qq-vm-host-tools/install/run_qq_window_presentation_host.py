"""Run the bounded QQ window presentation action through GuestSession."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
SCRIPT = ROOT / r"outputs\qq-vm\install\set_qq_window_presentation_guest.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_SCRIPT = r"C:\PMAI\data\set_qq_window_presentation_guest.py"
GUEST_OUTPUT = r"C:\PMAI\data\qq-window-presentation-action.json"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _control():
    spec = importlib.util.spec_from_file_location("pmai_window_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(guest: object, *, pid: int, hwnd: int, mode: str) -> tuple[int, int]:
    args = [
        GUEST_PYTHON, GUEST_SCRIPT,
        "--pid", str(pid), "--hwnd", str(hwnd), "--mode", mode,
    ]
    process = guest.ProcessCreate(GUEST_PYTHON, args, r"C:\PMAI\data", [], [], 60_000)
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("WINDOW_PROCESS_DID_NOT_START")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = process.WaitForArray([2, 4], 500)
        if result == 2:
            return int(process.Status), int(process.ExitCode)
        if result == 4:
            raise RuntimeError("WINDOW_PROCESS_WAIT_ERROR")
    raise RuntimeError("WINDOW_PROCESS_TIMEOUT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--mode", choices=("normal", "maximized"), required=True)
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    report: dict[str, object] = {
        "schema": "pmai-qq-window-presentation-host-v1",
        "run_id": run_id,
        "mode": args.mode,
        "succeeded": False,
    }
    host_session = guest = None
    clear = bytearray()
    roundtrip = DIAG / f".qq-window-presentation-{run_id}.py"
    try:
        control = _control()
        _vbox, host_session, guest, clear, _password = control._open_guest()
        runtime = control._runtime_status(guest, "window-presentation-" + run_id)
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        source_hash = _sha(SCRIPT)
        control._wait_progress(guest.FileCopyToGuest(str(SCRIPT), GUEST_SCRIPT, []), 30_000)
        control._wait_progress(guest.FileCopyFromGuest(GUEST_SCRIPT, str(roundtrip), []), 30_000)
        if _sha(roundtrip) != source_hash:
            raise RuntimeError("GUEST_SCRIPT_HASH_MISMATCH")
        status, exit_code = _run(guest, pid=args.pid, hwnd=args.hwnd, mode=args.mode)
        output_path = DIAG / f"qq-window-presentation-{args.mode}-{run_id}.json"
        value = control._copy_from_guest(guest, GUEST_OUTPUT, output_path)
        if exit_code != 0 or value.get("succeeded") is not True:
            raise RuntimeError("WINDOW_ACTION_FAILED")
        if value.get("process_id") != args.pid or value.get("window_handle") != args.hwnd:
            raise RuntimeError("WINDOW_ACTION_SCOPE_MISMATCH")
        report.update({"process_status": status, "exit_code": exit_code, "result": value, "succeeded": True})
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        if roundtrip.exists():
            roundtrip.unlink()
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"run-qq-window-presentation-{args.mode}-{run_id}.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
