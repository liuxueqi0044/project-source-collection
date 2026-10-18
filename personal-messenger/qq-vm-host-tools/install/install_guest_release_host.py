"""Install the frozen r20260912-01 guest release without desktop input."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path

ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
RELEASE = ROOT / r"outputs\personal-messenger-ai-v5\outputs\guest-local-releases\r20260912-01"
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
WHEEL = "0C6FC36BB386F71F6D4BA7B1B01295B3FFF18FE817770E8C0E6EAA2CCB27ADC5"

def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest().upper()

def module():
    spec = importlib.util.spec_from_file_location("ctl", CONTROL); assert spec and spec.loader
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value

def pump(proc, handle, buf):
    data = bytes(proc.Read(handle, 65536, 1000))
    if len(buf) + len(data) > 131072: raise RuntimeError("PROCESS_OUTPUT_TOO_LARGE")
    buf.extend(data)

def run_install(guest, stage: str):
    exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    command = f"& '{stage}\\Install-GuestLocalRelease.ps1' -SourceReleaseRoot '{stage}'"
    proc = guest.ProcessCreate(exe, [exe, "-NoProfile", "-NonInteractive", "-Command", command], r"C:\Windows\System32", [], [48], 180000)
    if proc.WaitForArray([1], 30000) != 1: raise RuntimeError("INSTALL_PROCESS_DID_NOT_START")
    out, err, deadline = bytearray(), bytearray(), time.monotonic() + 180
    while time.monotonic() < deadline:
        result = proc.WaitForArray([2, 8, 16], 500)
        if result == 7: pump(proc, 1, out)
        elif result == 8: pump(proc, 2, err)
        elif result == 2:
            pump(proc, 1, out); pump(proc, 2, err)
            return int(proc.ExitCode), bytes(out), bytes(err)
        elif result == 4: raise RuntimeError("INSTALL_PROCESS_WAIT_ERROR")
    raise RuntimeError("INSTALL_PROCESS_TIMEOUT")

def main() -> int:
    report = {"schema":"pmai-guest-release-host-install-v1", "release_id":"r20260912-01", "succeeded":False}
    guest = host = None; clear = bytearray()
    try:
        manifest = json.loads((RELEASE / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("release_id") != "r20260912-01" or digest(RELEASE / "personal_messenger_ai-0.1.0-py3-none-any.whl") != WHEEL: raise RuntimeError("SOURCE_RELEASE_HASH_INVALID")
        ctl = module(); _vbox, host, guest, clear, _password = ctl._open_guest()
        if ctl._runtime_status(guest, "r20260912-01-preinstall").get("runtime_process_alive") is not False: raise RuntimeError("RUNTIME_NOT_STOPPED")
        media_root = r"C:\PMAI\data\release-media"
        try:
            guest.DirectoryCreate(media_root, 0, [])
        except Exception:
            # Existing media roots are expected; the unique child below is the
            # only directory this installer owns for this invocation.
            pass
        stage = media_root + r"\r20260912-01-" + uuid.uuid4().hex
        guest.DirectoryCreate(stage, 0, [])
        files = ["manifest.json"] + [item["path"] for item in manifest["files"]]
        for rel in files:
            source, target = RELEASE / rel, stage + "\\" + rel
            ctl._wait_progress(guest.FileCopyToGuest(str(source), target, []), 30000)
            probe = DIAG / ("." + Path(rel).name + ".r20260912-01")
            ctl._wait_progress(guest.FileCopyFromGuest(target, str(probe), []), 30000)
            try:
                if digest(source) != digest(probe): raise RuntimeError("RELEASE_ROUNDTRIP_HASH_MISMATCH")
            finally:
                if probe.exists(): probe.unlink()
        report.update({"staging":stage, "roundtrip_file_count":len(files), "wheel_sha256":WHEEL})
        code, out, err = run_install(guest, stage)
        report.update({"installer_exit_code":code, "installer_stdout_bytes":len(out), "installer_stderr_bytes":len(err)})
        status_file = DIAG / "guest-local-release-install-r20260912-01.json"
        ctl._wait_progress(guest.FileCopyFromGuest(r"C:\PMAI\data\guest-local-release-install.json", str(status_file), []), 30000)
        status = json.loads(status_file.read_text(encoding="utf-8-sig"))
        report["install_status"] = {k:status.get(k) for k in ("state","succeeded","stage","release_id","error_code")}
        if code != 0 or status.get("succeeded") is not True or status.get("release_id") != "r20260912-01": raise RuntimeError("INSTALL_FAILED")
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc); return 2
    finally:
        for i in range(len(clear)): clear[i] = 0
        if guest:
            try: guest.Close()
            except Exception: pass
        if host:
            try: host.UnlockMachine()
            except Exception: pass
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / "install-guest-release-r20260912-01-host.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")

if __name__ == "__main__": raise SystemExit(main())
