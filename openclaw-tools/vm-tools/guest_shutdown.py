"""Request a clean shutdown of the isolated test clone through Guest Control."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path


SOURCE_HELPER = Path(
    r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat\outputs\qq-vm"
    r"\install\control_qq_runtime_host.py"
)
CLONE_ID = "5d948da3-2078-45bf-92b4-151de5412753"
CLONE_NAME = "XQ-OpenClaw-Test-01"


def main() -> int:
    spec = importlib.util.spec_from_file_location("pmai_control", SOURCE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load existing credential helper")
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    control.VM_ID = CLONE_ID
    control.VM_NAME = CLONE_NAME

    host_session = guest_session = None
    clear = bytearray()
    password = ""
    try:
        _, host_session, guest_session, clear, password = control._open_guest()
        executable = r"C:\Windows\System32\shutdown.exe"
        process = guest_session.ProcessCreate(
            executable,
            [executable, "/s", "/t", "0", "/d", "p:0:0", "/c", "OpenClaw test baseline snapshot"],
            r"C:\Windows\System32",
            [],
            [],
            30_000,
        )
        process.WaitForArray([1], 10_000)
        time.sleep(2)
        print("clean shutdown requested")
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


if __name__ == "__main__":
    raise SystemExit(main())
