"""Run the frozen guest release installer through the VirtualBox GuestSession."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
SCRIPT = ROOT / r"outputs\qq-vm\install\install_frozen_release_guest.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
STAGING = r"C:\PMAI\data\release-media\r20260912-01-5b183cf1df554921a06d23c8e62c3ff6"
GUEST_SCRIPT = STAGING + r"\install_frozen_release_guest.py"
GUEST_STATUS = r"C:\PMAI\data\guest-local-release-install.json"
GUEST_TARGET_MANIFEST = r"C:\PMAI\app\releases\r20260912-01\manifest.json"
RELEASE_ID = "r20260912-01"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _control_module():
    spec = importlib.util.spec_from_file_location("pmai_control_install", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_guest(guest: object) -> tuple[int, int]:
    executable = r"C:\PMAI\app\.venv\Scripts\python.exe"
    process = guest.ProcessCreate(executable, [executable, GUEST_SCRIPT], r"C:\PMAI\data", [], [], 180_000)
    if process.WaitForArray([1], 30_000) != 1:
        raise RuntimeError("INSTALL_PROCESS_DID_NOT_START")
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        result = process.WaitForArray([2, 4], 500)
        if result == 2:
            return int(process.Status), int(process.ExitCode)
        if result == 4:
            raise RuntimeError("INSTALL_PROCESS_WAIT_ERROR")
    raise RuntimeError("INSTALL_PROCESS_TIMEOUT")


def main() -> int:
    report: dict[str, object] = {"schema": "pmai-guest-release-host-install-v2", "release_id": RELEASE_ID, "succeeded": False}
    control = guest = host_session = None
    clear = bytearray()
    password = ""
    roundtrip = DIAG / ".install_frozen_release_guest.roundtrip.py"
    status_path = DIAG / ("guest-local-release-install-" + RELEASE_ID + ".json")
    target_manifest_path = DIAG / ("target-manifest-" + RELEASE_ID + ".json")
    try:
        if not SCRIPT.is_file():
            raise RuntimeError("GUEST_SCRIPT_MISSING")
        source_hash = _sha256(SCRIPT)
        control = _control_module()
        _vbox, host_session, guest, clear, password = control._open_guest()
        password = ""
        runtime = control._runtime_status(guest, RELEASE_ID + "-preinstall")
        if runtime.get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        control._wait_progress(guest.FileCopyToGuest(str(SCRIPT), GUEST_SCRIPT, []), 30_000)
        control._wait_progress(guest.FileCopyFromGuest(GUEST_SCRIPT, str(roundtrip), []), 30_000)
        if _sha256(roundtrip) != source_hash:
            raise RuntimeError("GUEST_SCRIPT_ROUNDTRIP_HASH_MISMATCH")
        report["guest_script_sha256"] = source_hash
        status_code, exit_code = _run_guest(guest)
        report.update({"process_status": status_code, "installer_exit_code": exit_code})
        control._wait_progress(guest.FileCopyFromGuest(GUEST_STATUS, str(status_path), []), 30_000)
        status = json.loads(status_path.read_text(encoding="utf-8-sig"))
        if not isinstance(status, dict):
            raise RuntimeError("INSTALL_STATUS_INVALID")
        report["install_status"] = {key: status.get(key) for key in ("state", "succeeded", "release_id", "error_code", "target")}
        if exit_code != 0 or status.get("succeeded") is not True or status.get("release_id") != RELEASE_ID:
            raise RuntimeError("INSTALL_FAILED")
        control._wait_progress(guest.FileCopyFromGuest(GUEST_TARGET_MANIFEST, str(target_manifest_path), []), 30_000)
        target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(target_manifest, dict) or target_manifest.get("release_id") != RELEASE_ID:
            raise RuntimeError("TARGET_MANIFEST_INVALID")
        report["target_manifest_sha256"] = _sha256(target_manifest_path)
        report["target_manifest_file_count"] = len(target_manifest.get("files", [])) if isinstance(target_manifest.get("files"), list) else None
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)
        return 2
    finally:
        if roundtrip.exists():
            roundtrip.unlink()
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / ("complete-staged-release-install-" + RELEASE_ID + ".json")).write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        for index in range(len(clear)):
            clear[index] = 0
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
