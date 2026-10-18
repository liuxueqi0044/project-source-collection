"""Resize only the already-certified QQ window; never touches chat controls."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import tempfile
import time
from ctypes import wintypes
from pathlib import Path


OUTPUT = Path(r"C:\PMAI\data\qq-window-presentation-action.json")
SW_MAXIMIZE = 3
SW_RESTORE = 9
SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG), ("top", wintypes.LONG),
        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
    ]


def _write(value: dict[str, object]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".qq-window-presentation-", suffix=".json", dir=str(OUTPUT.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, OUTPUT)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--mode", choices=("normal", "maximized"), required=True)
    args = parser.parse_args()
    report: dict[str, object] = {
        "schema": "pmai-qq-window-presentation-action-v1",
        "mode": args.mode,
        "process_id": args.pid,
        "window_handle": args.hwnd,
        "succeeded": False,
    }
    try:
        if os.name != "nt" or os.environ.get("COMPUTERNAME", "").upper() != "PMAI-QQVM":
            raise RuntimeError("GUEST_IDENTITY_INVALID")
        if os.environ.get("USERNAME", "").lower() != "qqbot":
            raise RuntimeError("GUEST_USER_INVALID")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetClassNameW.restype = ctypes.c_int
        user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindowAsync.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.IsZoomed.argtypes = (wintypes.HWND,)
        user32.IsZoomed.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
        user32.GetWindowRect.restype = wintypes.BOOL

        hwnd = wintypes.HWND(args.hwnd)
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            raise RuntimeError("QQ_WINDOW_UNAVAILABLE")
        actual_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid))
        if int(actual_pid.value) != args.pid:
            raise RuntimeError("QQ_WINDOW_PROCESS_MISMATCH")
        class_name = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, class_name, len(class_name)) <= 0:
            raise RuntimeError("QQ_WINDOW_CLASS_UNAVAILABLE")
        if class_name.value != "Chrome_WidgetWin_1":
            raise RuntimeError("QQ_WINDOW_CLASS_MISMATCH")

        command = SW_MAXIMIZE if args.mode == "maximized" else SW_RESTORE
        if not user32.ShowWindowAsync(hwnd, command):
            raise RuntimeError("QQ_WINDOW_STATE_CHANGE_REJECTED")
        time.sleep(1.0)
        if args.mode == "normal":
            if not user32.SetWindowPos(
                hwnd, None, 90, 60, 820, 612, SWP_NOACTIVATE | SWP_NOZORDER
            ):
                raise RuntimeError("QQ_WINDOW_RESIZE_REJECTED")
            time.sleep(1.0)

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("QQ_WINDOW_RECT_UNAVAILABLE")
        zoomed = bool(user32.IsZoomed(hwnd))
        if zoomed != (args.mode == "maximized"):
            raise RuntimeError("QQ_WINDOW_STATE_NOT_OBSERVED")
        report.update({
            "succeeded": True,
            "is_zoomed": zoomed,
            "rect": {
                "left": int(rect.left), "top": int(rect.top),
                "right": int(rect.right), "bottom": int(rect.bottom),
            },
        })
        _write(report)
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        _write(report)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
