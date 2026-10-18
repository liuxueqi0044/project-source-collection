"""Select one uniquely named QQ conversation inside the certified guest.

The request and result are deliberately privacy-minimal.  The title is read
only from a local request JSON and is never copied into result or diagnostics.
This helper does not inspect the composer or message region and cannot send.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any


SCHEMA = "pmai-qq-contact-select-result-v1"
MAX_TITLE_CHARS = 128


def normalize_display_name(value: str) -> str:
    """Canonical comparison key; it deliberately preserves meaningful text."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def title_fingerprint(value: str) -> tuple[str, int]:
    encoded = value.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, ValueError):
        return "REQUEST_INVALID"
    if type(exc).__name__ == "GuestFocusStageError":
        return "FOCUS_FAILED"
    message = str(exc)
    if message in {
        "QQ_TARGET_NOT_UNIQUE_OR_VISIBLE_TIMEOUT", "QQ_TARGET_PID_MISMATCH",
        "QQ_TARGET_WINDOW_STATE_CHANGED", "QQ_FOREGROUND_REQUEST_TIMEOUT",
        "QQ_SEARCH_SCOPE_UNAVAILABLE", "QQ_SEARCH_CONTROL_AMBIGUOUS",
        "QQ_SEARCH_SET_VALUE_FAILED", "QQ_SEARCH_RESULTS_UNAVAILABLE",
        "QQ_SEARCH_ENUMERATION_FAILED",
        "QQ_RECENT_ENUMERATION_FAILED", "QQ_SELECT_ACTION_FAILED",
        "QQ_SELECT_CONFIRM_FAILED",
        "QQ_SEARCH_FOCUS_FAILED", "QQ_SEARCH_FOCUS_MISMATCH",
        "QQ_SEARCH_ENTER_FAILED",
        "QQ_SEARCH_FOCUSED_CONTROL_FAILED",
        "QQ_SEARCH_CLICK_FAILED",
        "QQ_SEARCH_RESULT_PATTERN_UNAVAILABLE",
        "QQ_SEARCH_HIT_TEST_FAILED", "QQ_SEARCH_PATTERN_ACTION_FAILED",
    }:
        return message
    if "ambiguous" in message.casefold():
        return "STRUCTURE_AMBIGUOUS"
    return "SELECT_FAILED"


def read_request(path: Path) -> tuple[str, int, str, int, str | None]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("schema") != "pmai-qq-contact-select-request-v1":
        raise ValueError("bad schema")
    title, index, release_id = raw.get("title"), raw.get("contact_index"), raw.get("release_id")
    if not isinstance(title, str) or not title or len(title) > MAX_TITLE_CHARS:
        raise ValueError("bad title")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("bad index")
    if not isinstance(release_id, str) or not re.fullmatch(r"r\d{8}-\d{2}", release_id):
        raise ValueError("bad release")
    expected_row = raw.get("expected_row_runtime_id_hash")
    if expected_row is not None and (
        not isinstance(expected_row, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_row) is None
    ):
        raise ValueError("bad expected row")
    digest, length = title_fingerprint(title)
    return title, index, release_id, length, expected_row


def _matching_conversations(access: Any, window: Any, selector: Any, target_key: str) -> list[Any]:
    root = access._window(window)
    rows = access._select(root, selector)
    matching_ids: set[str] = set()
    for row in rows:
        controls = [row, *access._descendants(row)]
        names = {
            normalize_display_name(str(access._property(item, "Name", "")))
            for item in controls
        }
        if target_key in names:
            matching_ids.add(access._conversation_id(row))
    return [
        item for item in access.list_conversations(window, selector)
        if item.internal_id in matching_ids
    ]


def _search_for_conversation(
    access: Any, window: Any, selector: Any, target_key: str, title: str,
) -> list[Any] | None:
    """Use the unique top-left writable edit when the recent list has no match."""

    root = access._window(window)
    window_rect = access._property(root, "BoundingRectangle", None)
    if window_rect is None:
        raise RuntimeError("QQ_SEARCH_SCOPE_UNAVAILABLE")
    candidates: list[tuple[Any, Any]] = []
    try:
        try:
            focused = access._auto.GetFocusedControl()
        except Exception as exc:
            raise RuntimeError("QQ_SEARCH_FOCUSED_CONTROL_FAILED") from exc
        if focused is not None:
            try:
                focused_type = access._control_type(
                    access._property(focused, "ControlTypeName", "")
                )
                focused_pid = int(
                    access._property(focused, "ProcessId", 0) or 0
                )
                focused_class = str(
                    access._property(focused, "ClassName", "")
                )
                focused_pattern = access._pattern(
                    focused, "GetValuePattern", 10002
                )
                focused_writable = (
                    focused_pattern is not None
                    and not bool(getattr(focused_pattern, "IsReadOnly", False))
                )
            except Exception:
                focused_type = ""
                focused_pid = 0
                focused_class = ""
                focused_pattern = None
                focused_writable = False
            if (
                focused_type == "edit"
                and focused_pid == window.process_id
                and focused_writable
                and not {
                    "ProseMirror", "ExEditor-qq-msg-editor"
                }.intersection(focused_class.split())
            ):
                candidates.append((focused, focused_pattern))
        queue = [] if candidates else list(root.GetChildren())
        while queue:
            item = queue.pop(0)
            try:
                rect = access._property(item, "BoundingRectangle", None)
                in_scope = bool(
                    rect is not None
                    and rect.right > window_rect.left
                    and rect.bottom > window_rect.top
                    and rect.left <= window_rect.left + 420
                    and rect.top <= window_rect.top + 150
                )
            except Exception:
                in_scope = False
            if not in_scope:
                continue
            try:
                is_edit = access._control_type(
                    access._property(item, "ControlTypeName", "")
                ) == "edit"
            except Exception:
                is_edit = False
            if is_edit:
                try:
                    pattern = access._pattern(item, "GetValuePattern", 10002)
                except Exception:
                    pattern = None
                if pattern is not None:
                    try:
                        writable = not bool(getattr(pattern, "IsReadOnly", False))
                    except Exception:
                        writable = False
                    if writable:
                        candidates.append((item, pattern))
                        break
            try:
                queue.extend(item.GetChildren())
            except Exception:
                continue
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("QQ_SEARCH_ENUMERATION_FAILED") from exc
    if len(candidates) != 1:
        raise RuntimeError("QQ_SEARCH_CONTROL_AMBIGUOUS")
    try:
        candidates[0][1].SetValue(title)
    except Exception as exc:
        raise RuntimeError("QQ_SEARCH_SET_VALUE_FAILED") from exc
    search = candidates[0][0]
    try:
        search.SetFocus()
    except Exception as exc:
        raise RuntimeError("QQ_SEARCH_FOCUS_FAILED") from exc
    try:
        focused = access._auto.GetFocusedControl()
        search_id = tuple(search.GetRuntimeId() or ())
        focused_id = tuple(focused.GetRuntimeId() or ()) if focused is not None else ()
        if (
            focused is None
            or int(getattr(focused, "ProcessId", 0) or 0) != window.process_id
            or not search_id
            or focused_id != search_id
        ):
            raise RuntimeError("QQ_SEARCH_FOCUS_MISMATCH")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("QQ_SEARCH_FOCUS_MISMATCH") from exc
    time.sleep(1.0)
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if int(user32.GetForegroundWindow() or 0) != window.window_handle:
            raise RuntimeError("QQ_SEARCH_FOCUS_MISMATCH")
        click_x = int(window_rect.left) + 170
        click_y = int(window_rect.top) + 150
        if (
            click_x < int(window_rect.left)
            or click_x > int(window_rect.left) + 420
            or click_y < int(window_rect.top) + 70
            or click_y > int(window_rect.top) + 170
        ):
            raise RuntimeError("QQ_SEARCH_CLICK_FAILED")
        try:
            hit = access._auto.ControlFromPoint(click_x, click_y)
        except Exception as exc:
            raise RuntimeError("QQ_SEARCH_HIT_TEST_FAILED") from exc
        acted = False
        action_failed = False
        for _depth in range(8):
            if hit is None:
                break
            try:
                hit_pid = int(access._property(hit, "ProcessId", 0) or 0)
            except Exception:
                hit_pid = 0
            if hit_pid != window.process_id:
                break
            for getter, pattern_id, action in (
                ("GetSelectionItemPattern", 10010, "Select"),
                ("GetInvokePattern", 10000, "Invoke"),
                ("GetLegacyIAccessiblePattern", 10018, "DoDefaultAction"),
            ):
                try:
                    pattern = access._pattern(hit, getter, pattern_id)
                except Exception:
                    pattern = None
                method = getattr(pattern, action, None) if pattern is not None else None
                if callable(method):
                    try:
                        method()
                    except Exception:
                        action_failed = True
                        continue
                    acted = True
                    break
            if acted:
                break
            parent = getattr(hit, "GetParentControl", None)
            hit = parent() if callable(parent) else None
        if not acted:
            raise RuntimeError(
                "QQ_SEARCH_PATTERN_ACTION_FAILED"
                if action_failed
                else "QQ_SEARCH_RESULT_PATTERN_UNAVAILABLE"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("QQ_SEARCH_CLICK_FAILED") from exc
    time.sleep(1.0)
    # QQ rebuilds the search-result UIA tree after Enter and can block tree
    # enumeration.  The following bootstrap is the authoritative identity
    # certification step, so return a provisional selection without reading it.
    return None


def select_once(
    *, release_root: Path, title: str,
    expected_row_runtime_id_hash: str | None = None,
) -> dict[str, Any]:
    if not release_root.is_dir() or not (release_root / "selector-pack-session-1.json").is_file():
        raise RuntimeError("release selector pack absent")
    # The release carries the only permitted focus helper and selector pack.
    sys.path.insert(0, str(release_root))
    from guest_focus_helper import select_and_focus_target
    from messenger_ai.adapters.qq.models import QQSelectorPack
    from messenger_ai.adapters.qq.vm_driver.selectors import validate_guest_selector_pack
    from messenger_ai.adapters.qq.vm_driver.transport import WindowsUIAQQAccessibility

    pack = QQSelectorPack.model_validate_json((release_root / "selector-pack-session-1.json").read_text(encoding="utf-8"))
    validate_guest_selector_pack(pack)
    target_key = normalize_display_name(title)
    if not target_key:
        raise ValueError("empty normalized title")
    (pid, hwnd), _focus = select_and_focus_target(max_wait=0)
    access = WindowsUIAQQAccessibility()
    windows = [item for item in access.find_main_windows(pack.selector("main_window")) if item.process_id == pid and item.window_handle == hwnd]
    if len(windows) != 1:
        raise RuntimeError("QQ_WINDOW_NOT_UNIQUE")
    window = windows[0]
    selector = pack.selector("conversation_item")
    matches = _search_for_conversation(
        access, window, selector, target_key, title,
    )
    if matches is None:
        return {"match_count": 1, "selected": True}
    if expected_row_runtime_id_hash is not None:
        expected_id = "runtime:" + expected_row_runtime_id_hash
        matches = [item for item in matches if item.internal_id == expected_id]
    if len(matches) != 1:
        return {"match_count": len(matches), "selected": False}
    candidate = matches[0]
    # The transport calls only a certified selection action.  Confirmation is
    # deliberately independent of the post-click list operation.
    try:
        access.select_conversation(window, candidate, selector)
    except Exception as exc:
        raise RuntimeError("QQ_SELECT_ACTION_FAILED") from exc
    try:
        access.confirm_conversation_selected(window, candidate, selector)
    except Exception as exc:
        raise RuntimeError("QQ_SELECT_CONFIRM_FAILED") from exc
    return {
        "match_count": 1,
        "selected": True,
        "selected_row_runtime_id_hash": candidate.internal_id.removeprefix("runtime:"),
        "qq_pid": pid,
        "qq_hwnd": hwnd,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy-safe one-contact QQ selector")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result: dict[str, Any] = {"schema": SCHEMA, "succeeded": False}
    try:
        title, contact_index, release_id, title_length, expected_row = read_request(
            args.request
        )
        title_sha256, _ = title_fingerprint(title)
        result.update({"release_id": release_id, "contact_index": contact_index,
                       "title_sha256": title_sha256, "title_length": title_length})
        selection = select_once(
            release_root=Path(r"C:\PMAI\app\releases") / release_id,
            title=title,
            expected_row_runtime_id_hash=expected_row,
        )
        result.update(selection)
        if selection["match_count"] != 1:
            result["error_code"] = "TITLE_MATCH_NOT_UNIQUE"
            return 2
        result["succeeded"] = True
        return 0
    except BaseException as exc:
        result.setdefault("match_count", 0)
        result["error_code"] = safe_error(exc)
        return 2
    finally:
        # Atomic enough for a single producer: never print title or exception.
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
