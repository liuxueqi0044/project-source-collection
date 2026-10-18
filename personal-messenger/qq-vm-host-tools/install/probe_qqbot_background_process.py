"""Verify a qqbot GuestSession process without using desktop input."""
from __future__ import annotations

import importlib.util
import json
import time
import uuid
from pathlib import Path


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"


def _control_module():
    spec = importlib.util.spec_from_file_location("pmai_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    nonce = uuid.uuid4().hex
    guest_result = rf"C:\PMAI\data\background-process-probe-{nonce}.json"
    host_result = DIAG / f"background-process-probe-{nonce}.json"
    report_path = DIAG / "background-process-probe-latest.json"
    report: dict[str, object] = {
        "schema": "pmai-background-process-probe-v1",
        "nonce": nonce,
        "succeeded": False,
    }
    host = guest = None
    clear = bytearray()
    try:
        control = _control_module()
        _vbox, host, guest, clear, _password = control._open_guest()
        executable = r"C:\PMAI\app\.venv\Scripts\python.exe"
        code = (
            "import json; from pathlib import Path; "
            f"Path({guest_result!r}).write_text("
            f"json.dumps({{'schema':'pmai-background-process-result-v1','nonce':{nonce!r}}}),"
            "encoding='utf-8')"
        )
        process = guest.ProcessCreate(
            executable,
            [executable, "-c", code],
            r"C:\PMAI\data",
            [],
            [],
            30_000,
        )
        if process.WaitForArray([1], 30_000) != 1:
            raise RuntimeError("PROCESS_DID_NOT_START")
        deadline = time.monotonic() + 30.0
        terminated = False
        while time.monotonic() < deadline:
            result = process.WaitForArray([2], 500)
            if result == 2:
                terminated = True
                break
            if result == 4:
                raise RuntimeError("PROCESS_WAIT_ERROR")
        if not terminated:
            raise RuntimeError("PROCESS_TIMEOUT")
        if int(process.Status) != 500 or int(process.ExitCode) != 0:
            raise RuntimeError("PROCESS_EXIT_FAILED")
        control._wait_progress(
            guest.FileCopyFromGuest(guest_result, str(host_result), []), 30_000
        )
        payload = json.loads(host_result.read_text(encoding="utf-8-sig"))
        if payload != {
            "schema": "pmai-background-process-result-v1",
            "nonce": nonce,
        }:
            raise RuntimeError("PROCESS_RESULT_MISMATCH")
        report.update({"succeeded": True, "exit_code": 0})
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)
        return 2
    finally:
        DIAG.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
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
