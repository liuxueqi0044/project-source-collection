"""Host-side, GuestSession-only launcher for the privacy-safe QQ selector."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
GUEST_SCRIPT = ROOT / r"outputs\qq-vm\install\select_qq_contact_guest.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_DATA = r"C:\PMAI\data"
MAX_OUTPUT = 64 * 1024


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def load_control():
    spec = importlib.util.spec_from_file_location("pmai_contact_control", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait(ctl: Any, progress: object) -> None:
    ctl._wait_progress(progress, 30_000)


def read_small_json(ctl: Any, guest: object, guest_path: str, host_path: Path) -> dict[str, Any]:
    wait(ctl, guest.FileCopyFromGuest(guest_path, str(host_path), []))
    if host_path.stat().st_size > MAX_OUTPUT:
        raise RuntimeError("RESULT_TOO_LARGE")
    value = json.loads(host_path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("RESULT_NOT_OBJECT")
    return value


def run_guest(guest: object, request: str, result: str) -> tuple[int, int, int]:
    exe = r"C:\PMAI\app\.venv\Scripts\python.exe"
    script = GUEST_DATA + r"\select-qq-contact-guest.py"
    proc = guest.ProcessCreate(exe, [exe, script, "--request", request, "--result", result], GUEST_DATA, [], [48], 60_000)
    if proc.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("SELECTOR_DID_NOT_START")
    stdout = stderr = 0
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = proc.WaitForArray([2, 8, 16], 500)
        if state == 7:
            stdout += len(bytes(proc.Read(1, 8192, 1000)))
        elif state == 8:
            stderr += len(bytes(proc.Read(2, 8192, 1000)))
        elif state == 2:
            stdout += len(bytes(proc.Read(1, 8192, 1000)))
            stderr += len(bytes(proc.Read(2, 8192, 1000)))
            if stdout > MAX_OUTPUT or stderr > MAX_OUTPUT:
                raise RuntimeError("SELECTOR_OUTPUT_TOO_LARGE")
            return int(proc.ExitCode), stdout, stderr
        elif state == 4:
            raise RuntimeError("SELECTOR_WAIT_ERROR")
    raise RuntimeError("SELECTOR_TIMEOUT")


def main() -> int:
    parser = argparse.ArgumentParser(description="GuestSession QQ contact selector")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--contact-index", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--expected-row-runtime-id-hash")
    args = parser.parse_args()
    if (
        re.fullmatch(r"r\d{8}-\d{2}", args.release_id) is None
        or not 1 <= args.contact_index <= 9999
        or not args.title
        or len(args.title) > 128
        or (
            args.expected_row_runtime_id_hash is not None
            and re.fullmatch(r"[0-9a-f]{64}", args.expected_row_runtime_id_hash) is None
        )
    ):
        parser.error("invalid release, contact index, or title")
    DIAG.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    request_host = DIAG / f".select-qq-contact-{nonce}.request.json"
    result_host = DIAG / f"select-qq-contact-{nonce}.result.json"
    report_host = DIAG / f"select-qq-contact-{nonce}.host.json"
    request_guest = GUEST_DATA + rf"\select-qq-contact-{nonce}.request.json"
    result_guest = GUEST_DATA + rf"\select-qq-contact-{nonce}.result.json"
    report: dict[str, Any] = {"schema": "pmai-qq-contact-select-host-v1", "succeeded": False,
                              "release_id": args.release_id, "contact_index": args.contact_index}
    guest = host = None
    clear = bytearray()
    try:
        request = {"schema": "pmai-qq-contact-select-request-v1", "release_id": args.release_id,
            "contact_index": args.contact_index, "title": args.title}
        if args.expected_row_runtime_id_hash is not None:
            request["expected_row_runtime_id_hash"] = args.expected_row_runtime_id_hash
        request_host.write_text(json.dumps(request, sort_keys=True), encoding="utf-8")
        ctl = load_control()
        _vbox, host, guest, clear, _password = ctl._open_guest()
        if ctl._runtime_status(guest, f"contact-select-{nonce}").get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        wait(ctl, guest.FileCopyToGuest(str(request_host), request_guest, []))
        wait(ctl, guest.FileCopyToGuest(str(GUEST_SCRIPT), GUEST_DATA + r"\select-qq-contact-guest.py", []))
        roundtrip = DIAG / f".select-qq-contact-{nonce}.guest.py"
        try:
            wait(ctl, guest.FileCopyFromGuest(GUEST_DATA + r"\select-qq-contact-guest.py", str(roundtrip), []))
            if digest(GUEST_SCRIPT) != digest(roundtrip):
                raise RuntimeError("GUEST_SCRIPT_HASH_MISMATCH")
            report["guest_script_sha256"] = digest(GUEST_SCRIPT)
        finally:
            roundtrip.unlink(missing_ok=True)
        exit_code, stdout_bytes, stderr_bytes = run_guest(guest, request_guest, result_guest)
        report.update({"guest_exit_code": exit_code, "stdout_bytes": stdout_bytes, "stderr_bytes": stderr_bytes})
        result = read_small_json(ctl, guest, result_guest, result_host)
        allowed = {"schema", "succeeded", "release_id", "contact_index", "title_sha256", "title_length", "match_count", "selected", "selected_row_runtime_id_hash", "qq_pid", "qq_hwnd", "error_code"}
        if set(result) - allowed or result.get("schema") != "pmai-qq-contact-select-result-v1":
            raise RuntimeError("RESULT_SCHEMA_INVALID")
        report["result_path"] = str(result_host)
        report["result"] = result
        if exit_code != 0 or result.get("succeeded") is not True:
            raise RuntimeError("SELECTOR_FAILED")
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc) if str(exc).isupper() else "HOST_SELECT_FAILED"
        return 2
    finally:
        # Only the transient request contains the title; remove it even on error.
        request_host.unlink(missing_ok=True)
        for i in range(len(clear)):
            clear[i] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass
        report_host.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
