"""Copy one fixed, public QQ UIA report from the fixed guest via VirtualBox COM."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import win32com.client
import win32crypt


VM_ID = "7592dfa4-c60c-4818-921e-ced5c626254b"
VM_NAME = "PersonalMessengerQQ"
VM_RUNNING = 6
GUEST_USER = "qqbot"
WORKSPACE = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CREDENTIAL = WORKSPACE / r"outputs\qq-vm\install\credential-state\qqbot.dpapi"
OUTPUT_DIR = WORKSPACE / r"outputs\qq-vm\install\diagnostics"
REPORTS = {
    "baseline": (
        r"C:\PMAI\data\qq-uia-structural-probe.json",
        OUTPUT_DIR / "qq-uia-structural-probe-export.json",
    ),
    "foreground": (
        r"C:\PMAI\data\qq-uia-structural-probe-foreground.json",
        OUTPUT_DIR / "qq-uia-structural-probe-foreground-export.json",
    ),
    "warmup": (
        r"C:\PMAI\data\qq-uia-structural-probe-warmup.json",
        OUTPUT_DIR / "qq-uia-structural-probe-warmup-export.json",
    ),
    "selected": (
        r"C:\PMAI\data\qq-uia-structural-probe-selected.json",
        OUTPUT_DIR / "qq-uia-structural-probe-selected-export.json",
    ),
    "private": (
        r"C:\PMAI\data\fixedqq-uia-private-probe.json",
        OUTPUT_DIR / "fixedqq-uia-private-probe.json",
    ),
    "rulepack": (
        r"C:\PMAI\data\rulepack-activation-status.json",
        OUTPUT_DIR / "rulepack-activation-status.json",
    ),
    "windows": (
        r"C:\PMAI\data\qq-window-metadata.json",
        OUTPUT_DIR / "qq-window-metadata.json",
    ),
    "deepseek": (
        r"C:\PMAI\data\deepseek-provider-smoke.json",
        OUTPUT_DIR / "deepseek-provider-smoke-export.json",
    ),
    "deepseek-v2": (
        r"C:\PMAI\data\deepseek-provider-smoke-v2.json",
        OUTPUT_DIR / "deepseek-provider-smoke-v2-export.json",
    ),
    "deepseek-v3": (
        r"C:\PMAI\data\deepseek-provider-smoke-v3.json",
        OUTPUT_DIR / "deepseek-provider-smoke-v3-export.json",
    ),
    "deepseek-v4": (
        r"C:\PMAI\data\deepseek-provider-smoke-v4.json",
        OUTPUT_DIR / "deepseek-provider-smoke-v4-export.json",
    ),
    "install-update": (
        r"C:\PMAI\data\pmai-wheel-update-status.json",
        OUTPUT_DIR / "pmai-wheel-update-status-export.json",
    ),
    "identity-update": (
        r"C:\PMAI\data\pmai-wheel-identity-update-status.json",
        OUTPUT_DIR / "pmai-wheel-identity-update-status-export.json",
    ),
    "guest-local-release-install": (
        r"C:\PMAI\data\guest-local-release-install.json",
        OUTPUT_DIR / "guest-local-release-install.json",
    ),
    "guest-local-release-launch": (
        r"C:\PMAI\data\guest-local-release-launch.json",
        OUTPUT_DIR / "guest-local-release-launch.json",
    ),
    "r10-quarantine-release": (
        r"C:\PMAI\data\r10-contact3-quarantine-release-result.json",
        OUTPUT_DIR / "r10-contact3-quarantine-release-result.json",
    ),
    "r10-current-eval-projection": (
        r"C:\PMAI\data\r10-current-eval-projection.json",
        OUTPUT_DIR / "r10-current-eval-projection.json",
    ),
    "r11-reevaluation-inspect": (
        r"C:\PMAI\data\r11-reevaluation-inspect.json",
        OUTPUT_DIR / "r11-reevaluation-inspect.json",
    ),
    "r11-reevaluation-prepare": (
        r"C:\PMAI\data\r11-reevaluation-prepare.json",
        OUTPUT_DIR / "r11-reevaluation-prepare.json",
    ),
    "profile": (
        r"C:\PMAI\data\qq-guest-profile-probe.json",
        OUTPUT_DIR / "qq-guest-profile-probe-export.json",
    ),
    "composer-read": (
        r"C:\PMAI\data\qq-composer-read-probe.json",
        OUTPUT_DIR / "qq-composer-read-probe-export.json",
    ),
    "composer-diagnostic": (
        r"C:\PMAI\data\qq-composer-read-diagnostic.json",
        OUTPUT_DIR / "qq-composer-read-diagnostic-export.json",
    ),
    "composer-nonce": (
        r"C:\PMAI\data\qq-composer-nonce-smoke.json",
        OUTPUT_DIR / "qq-composer-nonce-smoke-export.json",
    ),
    "supervised-install": (
        r"C:\PMAI\data\pmai-supervised-diagnostics-install.json",
        OUTPUT_DIR / "pmai-supervised-diagnostics-install-export.json",
    ),
    "supervised-send": (
        r"C:\PMAI\data\qq-supervised-single-send.json",
        OUTPUT_DIR / "qq-supervised-single-send-export.json",
    ),
    "message-region-shape": (
        r"C:\PMAI\data\qq-message-region-shape.json",
        OUTPUT_DIR / "qq-message-region-shape-export.json",
    ),
    "session-observed-bootstrap": (
        r"C:\PMAI\data\qq-session-observed-bootstrap.json",
        OUTPUT_DIR / "qq-session-observed-bootstrap-export.json",
    ),
    "session-observed-bootstrap-1": (
        r"C:\PMAI\data\qq-session-observed-bootstrap-1.json",
        OUTPUT_DIR / "qq-session-observed-bootstrap-1-export.json",
    ),
    "session-observed-bootstrap-2": (
        r"C:\PMAI\data\qq-session-observed-bootstrap-2.json",
        OUTPUT_DIR / "qq-session-observed-bootstrap-2-export.json",
    ),
    "session-observed-bootstrap-3": (
        r"C:\PMAI\data\qq-session-observed-bootstrap-3.json",
        OUTPUT_DIR / "qq-session-observed-bootstrap-3-export.json",
    ),
    "session-runtime-status": (
        r"C:\PMAI\data\qq-session-runtime-status.json",
        OUTPUT_DIR / "qq-session-runtime-status-export.json",
    ),
    "session-runtime-worker-status": (
        r"C:\PMAI\data\qq-session-runtime-worker-status.json",
        OUTPUT_DIR / "qq-session-runtime-worker-status-export.json",
    ),
    "qq-worker-observe-timing": (
        r"C:\PMAI\data\qq-worker-observe-timing.json",
        OUTPUT_DIR / "qq-worker-observe-timing-export.json",
    ),
    "qq-worker-observe-timing-parent": (
        r"C:\PMAI\data\qq-worker-observe-timing-parent.json",
        OUTPUT_DIR / "qq-worker-observe-timing-parent-export.json",
    ),
    "profile-card": (
        r"C:\PMAI\data\qq-profile-card-structural.json",
        OUTPUT_DIR / "qq-profile-card-structural-export.json",
    ),
}
EXPECTED_SCHEMAS = {
    "session-runtime-worker-status": "pmai-qq-runtime-worker-status-v1",
    "r10-quarantine-release": "pmai-qq-observation-quarantine-release-result-v1",
}
MAX_BYTES = 8 * 1024 * 1024


def _fixed_path_guards(guest_source: str, output: Path, temp_output: Path) -> None:
    if Path(guest_source).suffix.lower() != ".json":
        raise RuntimeError("guest source extension guard failed")
    output_dir = OUTPUT_DIR.resolve(strict=True)
    if output.resolve(strict=False).parent != output_dir:
        raise RuntimeError("host output path guard failed")
    if temp_output.resolve(strict=False).parent != output_dir:
        raise RuntimeError("host temporary path guard failed")
    if output.suffix.lower() != ".json" or temp_output.suffix.lower() != ".json":
        raise RuntimeError("host output extension guard failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", choices=tuple(REPORTS), default="baseline")
    args = parser.parse_args()
    guest_source, output = REPORTS[args.report]
    temp_output = output.with_name(f".{output.stem}.tmp.json")
    _fixed_path_guards(guest_source, output, temp_output)
    if temp_output.exists():
        temp_output.unlink()

    protected = CREDENTIAL.read_text(encoding="ascii").strip()
    clear = bytearray(
        win32crypt.CryptUnprotectData(bytes.fromhex(protected), None, None, None, 0)[1]
    )
    protected = ""
    password = clear.decode("utf-16-le").rstrip("\x00")
    host_session = None
    guest_session = None
    try:
        virtualbox = win32com.client.Dispatch("VirtualBox.VirtualBox")
        machine = virtualbox.FindMachine(VM_ID)
        if (
            str(machine.Id).lower() != VM_ID
            or machine.Name != VM_NAME
            or int(machine.State) != VM_RUNNING
        ):
            raise RuntimeError("fixed VM identity or running-state guard failed")

        host_session = win32com.client.Dispatch("VirtualBox.Session")
        machine.LockMachine(host_session, 1)
        guest_session = host_session.Console.Guest.CreateSession(
            GUEST_USER, password, "", "pmai-readonly-uia-report-export"
        )
        wait_result = guest_session.WaitForArray([1], 30_000)
        if wait_result != 1 or int(guest_session.Status) != 100:
            raise RuntimeError("qqbot guest session did not start")

        progress = guest_session.FileCopyFromGuest(
            guest_source, str(temp_output), []
        )
        progress.WaitForCompletion(60_000)
        if not bool(progress.Completed):
            raise RuntimeError("guest report copy timed out")
        if int(progress.ResultCode) != 0:
            raise RuntimeError("guest report copy failed")

        size = temp_output.stat().st_size
        if size < 2 or size > MAX_BYTES:
            raise RuntimeError("guest report size guard failed")
        report = json.loads(temp_output.read_text(encoding="utf-8-sig"))
        if not isinstance(report, dict):
            raise RuntimeError("guest report must be a JSON object")
        expected_schema = EXPECTED_SCHEMAS.get(args.report)
        if expected_schema is not None and report.get("schema") != expected_schema:
            raise RuntimeError("guest report schema guard failed")
        os.replace(temp_output, output)
        print("exported=true")
        return 0
    finally:
        password = ""
        for index in range(len(clear)):
            clear[index] = 0
        if guest_session is not None:
            try:
                guest_session.Close()
            except Exception:
                pass
        if host_session is not None:
            try:
                host_session.UnlockMachine()
            except Exception:
                pass
        if temp_output.exists():
            temp_output.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
