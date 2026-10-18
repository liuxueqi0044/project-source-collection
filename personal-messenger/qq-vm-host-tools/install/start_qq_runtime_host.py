"""Start one frozen QQ runtime supervisor through a background GuestSession.

The VirtualBox GuestSession owns the process it starts directly.  A short-lived
launcher therefore creates the long-running supervisor as its child; closing
the GuestSession then cannot freeze supervision while leaving only the runtime
grandchild alive.
"""
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
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
DATA = r"C:\PMAI\data"


def load_control():
    spec = importlib.util.spec_from_file_location("pmai_start_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guest_pid_alive(guest, pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    command = (
        f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
        "{ exit 0 } else { exit 3 }"
    )
    process = guest.ProcessCreate(
        powershell,
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        DATA,
        [],
        [48],
        30_000,
    )
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("PID_PROBE_DID_NOT_START")
    if process.WaitForArray([2], 30_000) != 2:
        raise RuntimeError("PID_PROBE_TIMEOUT")
    return int(process.ExitCode) == 0


def _detached_launcher_code(script: str) -> str:
    return (
        "import subprocess; "
        "flags=(getattr(subprocess,'CREATE_NO_WINDOW',0)"
        "|getattr(subprocess,'DETACHED_PROCESS',0)"
        "|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)); "
        f"subprocess.Popen([{PYTHON!r},{script!r}],cwd={DATA!r},"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,close_fds=True,creationflags=flags)"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"r\d{8}-\d{2}", args.release_id) is None:
        parser.error("bad release id")
    nonce = uuid.uuid4().hex
    report = {"schema": "pmai-runtime-start-host-v1", "release_id": args.release_id, "succeeded": False}
    guest = host = None
    clear = bytearray()
    try:
        control = load_control()
        _vbox, host, guest, clear, _password = control._open_guest()
        previous = control._runtime_status(guest, "start-before-" + nonce)
        if previous.get("runtime_process_alive") is True and _guest_pid_alive(
            guest, previous.get("runtime_process_id")
        ):
            raise RuntimeError("RUNTIME_ALREADY_RUNNING")
        script = rf"C:\PMAI\app\releases\{args.release_id}\qq_session_runtime_supervisor_guest.py"
        proc = guest.ProcessCreate(
            PYTHON,
            [PYTHON, "-c", _detached_launcher_code(script)],
            DATA,
            [],
            [],
            120_000,
        )
        if proc.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("SUPERVISOR_LAUNCHER_DID_NOT_START")
        if proc.WaitForArray([2], 30_000) != 2 or int(proc.ExitCode) != 0:
            raise RuntimeError("SUPERVISOR_LAUNCHER_FAILED")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                status = control._runtime_status(guest, "start-" + nonce)
                if (
                    status.get("run_id") != previous.get("run_id")
                    and status.get("runtime_process_alive") is True
                    and isinstance(status.get("updated_at"), str)
                    and status.get("web_reachable") is True
                ):
                    report.update({
                        "succeeded": True,
                        "run_id": status.get("run_id"),
                        "state": status.get("state"),
                        "web_reachable": status.get("web_reachable"),
                        "driver_state": status.get("driver_state"),
                    })
                    return 0
                if status.get("run_id") != previous.get("run_id") and status.get("runtime_process_alive") is False:
                    raise RuntimeError(str(status.get("error_code") or "RUNTIME_EXITED"))
            except RuntimeError as exc:
                if str(exc) not in {"guest file operation failed: -102", "guest file operation timed out"}:
                    raise
            time.sleep(1)
        raise RuntimeError("RUNTIME_SUPERVISION_NOT_READY")
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"runtime-start-{args.release_id}-{nonce}.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
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
