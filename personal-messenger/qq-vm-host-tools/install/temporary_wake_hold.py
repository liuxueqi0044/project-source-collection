"""One temporary host idle-sleep hold for a bounded VM acceptance run.

This does not change any power scheme or display setting.  It requests only
ES_SYSTEM_REQUIRED, so ordinary idle sleep is deferred while user-initiated
sleep remains available.  The fixed sentinel permits a cooperative stop.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from ctypes import wintypes


ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"
STATE = DIAG / "pmai-temporary-wake-hold-state.json"
SENTINEL = DIAG / "pmai-temporary-wake-hold-stop.json"
MUTEX_NAME = r"Local\PMAI_TemporaryWakeHold_20260911"
TTL_SECONDS = 30 * 60
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ERROR_ALREADY_EXISTS = 183


def utc_now() -> datetime:
    return datetime.now(UTC)


def write_state(payload: dict[str, object]) -> None:
    DIAG.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, STATE)


def kernel32():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    api.SetThreadExecutionState.argtypes = (wintypes.DWORD,)
    api.SetThreadExecutionState.restype = wintypes.DWORD
    return api


def main() -> int:
    api = kernel32()
    mutex = api.CreateMutexW(None, True, MUTEX_NAME)
    if not mutex:
        raise RuntimeError(f"CREATE_MUTEX_FAILED:{ctypes.get_last_error()}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        api.CloseHandle(mutex)
        return 3

    started = utc_now()
    expires = started + timedelta(seconds=TTL_SECONDS)
    result = "startup_failed"
    try:
        if not api.SetThreadExecutionState(wintypes.DWORD(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)):
            raise RuntimeError(f"SET_EXECUTION_STATE_FAILED:{ctypes.get_last_error()}")
        write_state({
            "schema": "pmai-temporary-wake-hold-v1",
            "active": True,
            "pid": os.getpid(),
            "started_at": started.isoformat(),
            "expires_at": expires.isoformat(),
            "mode": "system_required_only",
        })
        result = "expired"
        while utc_now() < expires:
            if SENTINEL.exists():
                result = "stopped_by_sentinel"
                break
            time.sleep(1)
        return 0
    finally:
        # Always clear the request, even if state recording or sentinel reading failed.
        api.SetThreadExecutionState(wintypes.DWORD(ES_CONTINUOUS))
        try:
            write_state({
                "schema": "pmai-temporary-wake-hold-v1",
                "active": False,
                "pid": os.getpid(),
                "started_at": started.isoformat(),
                "expires_at": expires.isoformat(),
                "stopped_at": utc_now().isoformat(),
                "result": result,
                "mode": "system_required_only",
            })
        finally:
            api.ReleaseMutex(mutex)
            api.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
