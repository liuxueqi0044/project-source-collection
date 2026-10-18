"""Run one read-only observed-session bootstrap inside the QQ guest."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
import uuid
from pathlib import Path


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
GUEST_PYTHON = r"C:\PMAI\app\.venv\Scripts\python.exe"
GUEST_DATA = r"C:\PMAI\data"
SCHEMA = "pmai-qq-session-observed-bootstrap-v1"


def _control_module():
    spec = importlib.util.spec_from_file_location("pmai_control_bootstrap", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _guest_output(index: int) -> str:
    return GUEST_DATA + rf"\qq-session-observed-bootstrap-{index}.json"


def _run_guest(guest: object, index: int, release_id: str) -> tuple[int, int]:
    binding = f"session-contact-{index}"
    output = _guest_output(index)
    release = rf"C:\PMAI\app\releases\{release_id}"
    args = [
        GUEST_PYTHON,
        release + r"\qq_session_observed_bootstrap_guest.py",
        "--selector-pack", release + r"\selector-pack-session-1.json",
        "--binding-id", binding,
        "--operator-observed-direct",
    ]
    process = guest.ProcessCreate(GUEST_PYTHON, args, GUEST_DATA, [], [], 90_000)
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("BOOTSTRAP_PROCESS_DID_NOT_START")
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        result = process.WaitForArray([2, 4], 500)
        if result == 2:
            return int(process.Status), int(process.ExitCode)
        if result == 4:
            raise RuntimeError("BOOTSTRAP_PROCESS_WAIT_ERROR")
    raise RuntimeError("BOOTSTRAP_PROCESS_TIMEOUT")


def _copy_guest_json(control: object, guest: object, guest_path: str, host_path: Path) -> dict[str, object]:
    host_path.parent.mkdir(parents=True, exist_ok=True)
    control._wait_progress(guest.FileCopyFromGuest(guest_path, str(host_path), []), 30_000)
    value = json.loads(host_path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("BOOTSTRAP_JSON_NOT_OBJECT")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--contact-index", type=int, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"r\d{8}-\d{2}", args.release_id) is None:
        parser.error("release-id must use rYYYYMMDD-NN")
    if not 1 <= args.contact_index <= 9999:
        parser.error("contact-index must be between 1 and 9999")
    index = args.contact_index
    binding = f"session-contact-{index}"
    run_id = uuid.uuid4().hex
    guest_output = _guest_output(index)
    report: dict[str, object] = {
        "schema": "pmai-qq-session-bootstrap-host-v1",
        "run_id": run_id,
        "contact_index": index,
        "binding_id": binding,
        "release_id": args.release_id,
        "succeeded": False,
    }
    control = guest = host_session = None
    clear = bytearray()
    password = ""
    previous_path = DIAG / f"qq-session-observed-bootstrap-{index}-previous-{run_id}.json"
    output_path = DIAG / f"qq-session-observed-bootstrap-{index}-{run_id}.json"
    try:
        control = _control_module()
        _vbox, host_session, guest, clear, password = control._open_guest()
        password = ""
        runtime = control._runtime_status(guest, f"bootstrap-{index}-{run_id}")
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        try:
            control._wait_progress(guest.FileCopyFromGuest(guest_output, str(previous_path), []), 30_000)
            report["previous_bootstrap_backup"] = str(previous_path)
            report["previous_bootstrap_sha256"] = _sha256(previous_path)
        except Exception:
            report["previous_bootstrap_backup"] = None
        status, exit_code = _run_guest(guest, index, args.release_id)
        report.update({"process_status": status, "bootstrap_exit_code": exit_code})
        value = _copy_guest_json(control, guest, guest_output, output_path)
        evidence = value.get("evidence")
        report["bootstrap"] = {
            "schema": value.get("schema"),
            "state": value.get("state"),
            "succeeded": value.get("succeeded"),
            "failure_stage": value.get("failure_stage"),
            "error_code": value.get("error_code"),
            "evidence_binding_id": evidence.get("binding_id") if isinstance(evidence, dict) else None,
        }
        if (
            exit_code != 0
            or value.get("schema") != SCHEMA
            or value.get("succeeded") is not True
            or not isinstance(evidence, dict)
            or evidence.get("binding_id") != binding
        ):
            raise RuntimeError("BOOTSTRAP_RESULT_INVALID")
        report.update({"succeeded": True, "bootstrap_sha256": _sha256(output_path)})
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)
        return 2
    finally:
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"run-session-bootstrap-{index}-{run_id}-host.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
        for position in range(len(clear)):
            clear[position] = 0
        password = ""
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
