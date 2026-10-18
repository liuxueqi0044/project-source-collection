"""Offline self-test for the read-only QQ row hit-test inventory scripts.

Run with the Python 3.12 interpreter that owns win32com:

    C:\\Users\\xiayu\\AppData\\Local\\Programs\\Python\\Python312\\python.exe \
        outputs\\qq-vm\\install\\selftest_qq_row_hit_inventory.py

The guest script cannot be exercised against a real QQ tree from the host, so
this test drives its pure helpers against a synthetic UIA tree shaped like the
recorded contact2 evidence: a registered row that exposes no UIA ``Name``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
GUEST_PATH = HERE / "diagnose_qq_row_hit_inventory_guest.py"
HOST_PATH = HERE / "diagnose_qq_row_hit_inventory_host.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guest = load("pmai_row_inventory_guest", GUEST_PATH)
host = load("pmai_row_inventory_host", HOST_PATH)


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class Node:
    def __init__(
        self, *, control="Group", class_name="recent-contact-item", name="",
        automation_id="", runtime_id=(1, 2), patterns=(), children=(),
        rect=(0.0, 0.0, 10.0, 10.0), offscreen=False, enabled=True,
    ):
        self.ControlTypeName = control
        self.ClassName = class_name
        self.Name = name
        self.AutomationId = automation_id
        self.ProcessId = 10596
        self.IsOffscreen = offscreen
        self.IsEnabled = enabled
        self.BoundingRectangle = Rect(*rect)
        self.runtime_id = tuple(runtime_id)
        self._pattern_names = set(patterns)
        self._children = list(children)
        self._parent = None
        for child in self._children:
            child._parent = self

    def GetChildren(self):
        return list(self._children)

    def GetRuntimeId(self):
        return self.runtime_id

    def GetParentControl(self):
        return self._parent

    def _pattern_object(self, name):
        if name not in self._pattern_names:
            return None
        if name == "legacy":
            return SimpleNamespace(
                DefaultAction="按", DoDefaultAction=lambda: None
            )
        return SimpleNamespace()

    def GetInvokePattern(self):
        return self._pattern_object("invoke")

    def GetLegacyIAccessiblePattern(self):
        return self._pattern_object("legacy")


PATTERN_GETTERS = {
    "invokepattern": ("GetInvokePattern", 10000),
    "legacyiaccessiblepattern": ("GetLegacyIAccessiblePattern", 10018),
    "selectionitempattern": ("GetSelectionItemPattern", 10010),
    "valuepattern": ("GetValuePattern", 10002),
    "textpattern": ("GetTextPattern", 10014),
    "selectionpattern": ("GetSelectionPattern", 10001),
    "scrollpattern": ("GetScrollPattern", 10004),
    "scrollitempattern": ("GetScrollItemPattern", 10017),
}


class FakeAccess:
    """Minimal stand-in for ``WindowsUIAQQAccessibility`` read helpers."""

    def __init__(self, root, composer=None):
        self._root = root
        self._composer = composer
        self._auto = SimpleNamespace(ControlFromPoint=self._from_point)
        self._from_point_control = None

    @staticmethod
    def _control_type(value):
        normalized = str(value).strip().lower().replace("controltype.", "")
        return normalized[:-7] if normalized.endswith("control") else normalized

    def _property(self, item, name, default=""):
        return getattr(item, name, default)

    def _window(self, _window):
        return self._root

    def _descendants(self, root):
        queue = list(root.GetChildren())
        while queue:
            item = queue.pop(0)
            yield item
            queue.extend(item.GetChildren())

    def _select(self, root, selector):
        if getattr(selector, "name", None) == "composer":
            return [self._composer] if self._composer is not None else []
        return []

    def _pattern(self, item, getter_name, _pattern_id):
        getter = getattr(item, getter_name, None)
        return getter() if callable(getter) else None

    def _has_required_patterns(self, item, names):
        for raw in names:
            entry = PATTERN_GETTERS.get(raw.lower())
            if entry is None or self._pattern(item, *entry) is None:
                return False
        return True

    def _patterns(self, item):
        return {
            name
            for name, (getter, pattern_id) in PATTERN_GETTERS.items()
            if self._pattern(item, getter, pattern_id) is not None
        }

    def _from_point(self, *args):
        return self._from_point_control


def composer_selector():
    return SimpleNamespace(name="composer")


def build_tree(*, second_header=False):
    header = Node(
        control="Button",
        class_name="chat-header__contact-name",
        name="星语",
        runtime_id=(9, 1),
        patterns=("invoke",),
        rect=(600.0, 10.0, 700.0, 40.0),
    )
    children = [header]
    if second_header:
        children.append(Node(
            control="Button",
            class_name="chat-header__contact-name",
            name="星语二",
            runtime_id=(9, 2),
            patterns=("invoke",),
            rect=(600.0, 45.0, 700.0, 70.0),
        ))
    composer = Node(
        control="Group",
        class_name="composer is-empty",
        runtime_id=(9, 3),
        rect=(600.0, 500.0, 900.0, 560.0),
    )
    children.append(composer)
    root = Node(control="Window", class_name="Chrome_WidgetWin_1", runtime_id=(1,),
                rect=(0.0, 0.0, 1000.0, 600.0), children=children)
    return root, composer


def test_describe_reports_empty_name_for_contact2_row():
    row = Node(runtime_id=(5, 5), patterns=("invoke", "legacy"),
               rect=(10.0, 100.0, 220.0, 160.0))
    access = FakeAccess(Node(control="Window", children=(row,)))
    described = guest.describe(access, row)
    assert described["name_is_empty"] is True
    assert described["name_sha256_12"] is None
    assert described["runtime_id"] == [5, 5]
    assert described["rect"] == [10.0, 100.0, 220.0, 160.0]
    assert described["class_name"] == "recent-contact-item"
    legacy = guest.legacy_action(access, row)
    assert legacy["legacy_pattern"] is True
    assert legacy["default_action_utf8_bytes"] == 3
    assert legacy["default_action_sha256"] == hashlib.sha256(
        "按".encode("utf-8")
    ).hexdigest()


def test_name_fingerprint_never_returns_clear_text():
    fingerprint = guest.name_fingerprint("  星语  ")
    assert fingerprint["name_is_empty"] is False
    assert fingerprint["name_length"] == 2
    assert "星语" not in str(fingerprint)
    assert len(fingerprint["name_sha256_12"]) == 12


def test_header_probe_reports_specific_failure_reasons():
    root, composer = build_tree()
    access = FakeAccess(root, composer)
    probe = guest.header_probe(access, object(), SimpleNamespace(
        selector=lambda name: composer_selector()
    ))
    assert probe["failure_reason"] is None
    assert probe["digest_count"] == 1
    assert probe["composer_match_count"] == 1

    no_composer = guest.header_probe(FakeAccess(root, None), object(),
                                     SimpleNamespace(selector=lambda name: composer_selector()))
    assert no_composer["failure_reason"] == "composer_not_unique"

    root2, composer2 = build_tree(second_header=True)
    two_headers = guest.header_probe(FakeAccess(root2, composer2), object(),
                                     SimpleNamespace(selector=lambda name: composer_selector()))
    assert two_headers["failure_reason"] == "header_not_unique"
    assert two_headers["digest_count"] == 2


def test_point_inventory_finds_row_through_ancestor_walk():
    inner = Node(control="Text", class_name="contact-name", runtime_id=(7, 9),
                 rect=(30.0, 110.0, 200.0, 140.0))
    row = Node(runtime_id=(5, 5), patterns=("invoke",),
               rect=(10.0, 100.0, 220.0, 160.0))
    wrapper = Node(control="Pane", class_name="row-wrapper", runtime_id=(6, 6),
                   children=(inner,), rect=(12.0, 102.0, 218.0, 158.0))
    row._children = [wrapper]
    wrapper._parent = row
    access = FakeAccess(Node(control="Window", children=(row,)))
    access._from_point_control = inner
    described = guest.describe(access, row)
    entry = guest.point_inventory(access, row, described, described["rect"], 50, 120)
    assert entry["error_code"] is None
    assert entry["hit_point_row_match_mode"] == "runtime_id"
    assert entry["hit_runtime_id_equals_row_at"] == [2]
    assert entry["window_from_point"]["root_hwnd"] >= 0


def test_point_inventory_reports_a_chain_that_never_reaches_the_row():
    # Exactly the recorded a836138f shape: ControlFromPoint resolves inside the
    # QQ window but no ancestor ever equals the registered row runtime id.
    stranger = Node(control="Pane", class_name="elsewhere", runtime_id=(8, 8))
    row = Node(runtime_id=(5, 5), rect=(10.0, 100.0, 220.0, 160.0))
    access = FakeAccess(Node(control="Window", children=(row,)))
    access._from_point_control = stranger
    described = guest.describe(access, row)
    entry = guest.point_inventory(access, row, described, described["rect"], 50, 120)
    assert entry["hit_point_row_match_mode"] is None
    assert entry["hit_ancestor_count"] == 1
    assert entry["ancestors"][0]["runtime_id"] == [8, 8]


def test_host_guard_accepts_the_guest_script_and_rejects_an_input_call():
    source = GUEST_PATH.read_text(encoding="utf-8")
    host.assert_read_only(source)
    for token in ("SendInput", ".Invoke(", "DoDefaultAction", "write_composer"):
        assert token not in source, token
    try:
        host.assert_read_only(source + "\nimport ctypes\nctypes.windll.SendInput(0)\n")
    except RuntimeError as exc:
        assert str(exc) == "GUEST_SCRIPT_NOT_READ_ONLY"
    else:
        raise AssertionError("guard must reject a synthetic input call")


def test_selector_census_counts_matches_and_editor_candidates():
    editor = Node(control="Group", class_name="ProseMirror ExEditor-qq-msg-editor",
                  runtime_id=(7, 1), patterns=("text",),
                  rect=(600.0, 500.0, 900.0, 560.0))
    root, composer = build_tree()
    root._children.append(editor)
    editor._parent = root
    access = FakeAccess(root, composer)
    access._select = lambda _root, sel: (
        [composer] if getattr(sel, "name", None) == "composer" else []
    )
    pack = SimpleNamespace(selector=lambda name: SimpleNamespace(name=name))
    census = guest.selector_census(access, object(), pack)
    assert census["selector_match_count"]["composer"] == 1
    assert census["selector_match_count"]["conversations"] == 0
    assert census["descendant_count"] >= 3
    assert any(item["class_name"].startswith("ProseMirror")
               for item in census["editor_candidates"])
    assert census["class_histogram"][0]["count"] >= 1


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - this is a test runner
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"pass {test.__name__}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
