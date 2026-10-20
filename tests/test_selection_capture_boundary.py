"""Deterministic selection races; never read host clipboard or inject input."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
import os

import pytest

from lilies.core import selection as module
from lilies.core.database import Database
from lilies.core.selection import SelectionService
from lilies.core.win_event import WinEvent, WinEventKind


class Timer:
    def __init__(self): self.active = True
    def isActive(self): return self.active
    def start(self): self.active = True
    def stop(self): self.active = False


class Subscription:
    ready = True
    available = True
    signed_in = True
    plan_type = "fixture"
    def __init__(self, *_args, **_kwargs): pass
    def stop(self): pass
    def refresh(self): pass


@dataclass
class World:
    identity: dict = field(default_factory=lambda: {
        "handle": 101, "processId": 202, "processStarted": 303,
        "processName": "wpspdf.exe", "executableHash": "program-a",
        "className": "Reader", "titleHash": "document-a", "focusWindow": 404,
    })
    allowed: bool = True
    keys: set = field(default_factory=set)
    cursor: tuple = (20, 30)
    clock: float = 100.0
    sequence: int = 10
    owner_pid: int = 999
    clipboard_value: str = "用户原剪贴板"
    captured_value: str = "correlation is not causation"
    pending: list = field(default_factory=list)
    model_calls: list = field(default_factory=list)
    copy_calls: int = 0
    clipboard_reads: int = 0
    restores: list = field(default_factory=list)
    paper: dict = field(default_factory=lambda: {"paperId": "paper-a", "page": 4})
    on_clone: object = None
    on_read: object = None
    on_copy: object = None
    on_restore: object = None

    def GetAsyncKeyState(self, key): return 0x8000 if key in self.keys else 0
    def GetForegroundWindow(self): return self.identity.get("handle", 0)
    def snapshot(self): return deepcopy(self.identity)
    def clone(self):
        data = self.clipboard_value
        if self.on_clone: self.on_clone()
        return data, bool(data)
    def copy(self):
        self.copy_calls += 1
        if self.on_copy:
            self.on_copy()
        else:
            self.sequence += 1
            self.owner_pid = self.identity["processId"]
            self.clipboard_value = self.captured_value
    def text(self):
        self.clipboard_reads += 1
        value = self.clipboard_value
        if self.on_read: self.on_read()
        return value
    def setMimeData(self, value):
        self.restores.append(value)
        self.clipboard_value = value
        self.sequence += 1
        if self.on_restore: self.on_restore()
    def clear(self): self.setMimeData("")
    def next_callback(self):
        assert self.pending
        delay, callback = self.pending.pop(0)
        self.clock += delay / 1000
        callback()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    world = World()
    monkeypatch.setattr(module, "CodexSubscriptionClient", Subscription)
    # This fixture replaces all host-I/O boundaries before constructing the
    # service. Unexpected model creation, screenshots or clipboard access fail.
    monkeypatch.setattr(module, "_user32", lambda: world)
    monkeypatch.setattr(module, "QGuiApplication", SimpleNamespace(clipboard=lambda: world))
    monkeypatch.setattr(module.time, "monotonic", lambda: world.clock)
    database = Database(tmp_path / "selection.db")
    service = SelectionService(database, active=False)
    service._timer.stop()
    service._timer = Timer()
    service._active = True
    service._enabled = True
    service.capture_identity_provider = world.snapshot
    service.capture_policy = lambda _hwnd: world.allowed
    service.paper_context_provider = lambda: deepcopy(world.paper)
    monkeypatch.setattr(module, "QTimer", SimpleNamespace(singleShot=lambda delay, callback: world.pending.append((delay, callback))))
    monkeypatch.setattr(service, "_cursor", lambda: world.cursor)
    monkeypatch.setattr(service, "_clone_mime", world.clone)
    monkeypatch.setattr(service, "_clipboard_sequence", lambda: world.sequence)
    monkeypatch.setattr(service, "_clipboard_owner_pid", lambda: world.owner_pid)
    monkeypatch.setattr(service, "_send_copy", world.copy)
    monkeypatch.setattr(service, "_start_request", lambda request, x, y, **kw: world.model_calls.append((request, x, y, deepcopy(kw))))
    yield service, world, database
    service.shutdown()


def select(service, world):
    world.keys.add(0x01)
    service._poll_mouse()
    world.keys.remove(0x01)
    world.clock += 0.2
    world.cursor = (world.cursor[0] + 30, world.cursor[1] + 10)
    service._poll_mouse()
    assert service._capture is not None


def test_valid_selection_restores_clipboard_and_freezes_original_paper(setup):
    service, world, database = setup
    select(service, world)
    world.paper["paperId"] = "new-paper"
    world.paper["page"] = 9
    world.next_callback()
    world.next_callback()
    assert world.copy_calls == 1 and world.clipboard_reads == 1
    assert world.restores == ["用户原剪贴板"]
    assert len(world.model_calls) == 1
    request, x, y, kwargs = world.model_calls[0]
    assert request.source_text == world.captured_value
    assert (x, y) == (50, 40)
    assert kwargs["provenance"] == {"sourceApp": "wpspdf.exe", "paper": {"paperId": "paper-a", "page": 4}}
    assert not hasattr(request, "paper") and not database.reading_cards()
    assert service._capture is None


@pytest.mark.parametrize("field,value", [
    ("handle", 500), ("processId", 999), ("processStarted", 404),
    ("executableHash", "program-b"), ("focusWindow", 909), ("titleHash", "document-b"),
])
@pytest.mark.parametrize("boundary", ["before-copy", "after-copy"])
def test_changed_identity_is_never_sent_to_model(setup, field, value, boundary):
    service, world, _ = setup
    select(service, world)
    if boundary == "after-copy": world.next_callback()
    world.identity[field] = value
    world.next_callback()
    assert world.copy_calls == (1 if boundary == "after-copy" else 0)
    assert not world.model_calls and not world.restores and not world.clipboard_reads
    assert service._capture is None


@pytest.mark.parametrize("boundary", ["before-copy", "after-copy"])
@pytest.mark.parametrize("cancel", ["disabled", "suspended", "private", "shutdown", "signed-out"])
def test_monitor_cancellation_retires_delayed_callbacks(setup, boundary, cancel):
    service, world, _ = setup
    select(service, world)
    if boundary == "after-copy": world.next_callback()
    if cancel == "disabled": service.set_enabled(False)
    elif cancel == "suspended": service.set_interaction_suspended(True)
    elif cancel == "private": service.set_privacy_suppressed(True)
    elif cancel == "shutdown": service.shutdown()
    else:
        service._subscription.ready = False
        service.refresh_subscription()
    world.next_callback()
    assert not world.model_calls and not world.clipboard_reads and not world.restores
    assert service._capture is None


@pytest.mark.parametrize("kind", [WinEventKind.FOREGROUND, WinEventKind.DESTROY, WinEventKind.HIDE, WinEventKind.MINIMIZE_START])
def test_shared_window_events_cancel_even_after_switching_back(setup, kind):
    service, world, _ = setup
    select(service, world)
    hwnd = 999 if kind is WinEventKind.FOREGROUND else 101
    service.handle_window_event(WinEvent(kind, hwnd, 0))
    # Current provider already looks like the original window again.
    assert world.identity["handle"] == 101
    world.next_callback()
    assert not world.copy_calls and not world.model_calls


@pytest.mark.parametrize("owner", [0, 999])
def test_unattributed_clipboard_change_is_not_read_or_overwritten(setup, owner):
    service, world, _ = setup
    select(service, world)
    world.next_callback()
    world.owner_pid = owner
    world.clipboard_value = "另一个应用的新内容"
    world.sequence += 1
    world.next_callback()
    assert not world.clipboard_reads and not world.restores and not world.model_calls
    assert world.clipboard_value == "另一个应用的新内容"


@pytest.mark.parametrize("change", ["clipboard", "focus", "privacy"])
def test_clipboard_clone_reentrancy_is_revalidated_before_copy(setup, change):
    service, world, _ = setup
    select(service, world)
    def mutate():
        if change == "clipboard": world.sequence += 1
        elif change == "focus": world.identity["focusWindow"] = 500
        else: world.allowed = False
    world.on_clone = mutate
    world.next_callback()
    assert not world.copy_calls and not world.model_calls and not world.restores


@pytest.mark.parametrize("change", ["sequence", "owner", "window", "privacy"])
def test_clipboard_read_reentrancy_never_restores_or_sends_stale_content(setup, change):
    service, world, _ = setup
    select(service, world)
    world.next_callback()
    def mutate():
        if change == "sequence": world.sequence += 1
        elif change == "owner": world.owner_pid = 999
        elif change == "window": world.identity["handle"] = 500
        else: world.allowed = False
    world.on_read = mutate
    world.next_callback()
    assert world.clipboard_reads == 1 and not world.restores and not world.model_calls


@pytest.mark.parametrize("key", [0x01, 0x10, 0x12, 0x5B, 0x5C, 0x43])
def test_new_gesture_or_shortcut_modifiers_prevent_copy(setup, key):
    service, world, _ = setup
    select(service, world)
    world.keys.add(key)
    world.next_callback()
    assert not world.copy_calls and not world.model_calls


def test_new_selection_supersedes_old_callbacks_without_touching_new_capture(setup):
    service, world, _ = setup
    select(service, world)
    old_begin = world.pending.pop()[1]
    select(service, world)
    current = service._capture
    old_begin()
    assert service._capture is current and not world.copy_calls
    world.next_callback()
    world.next_callback()
    assert world.copy_calls == 1 and len(world.model_calls) == 1


def test_missing_copy_result_has_bounded_wait_and_no_clipboard_restore(setup):
    service, world, _ = setup
    world.on_copy = lambda: None
    select(service, world)
    while world.pending: world.next_callback()
    assert world.copy_calls == 1 and service._capture is None
    assert not world.model_calls and not world.clipboard_reads and not world.restores


def test_foreground_changes_inside_copy_or_restore_discard_output(setup):
    service, world, _ = setup
    select(service, world)
    world.on_copy = lambda: world.identity.update(handle=999)
    world.next_callback()
    assert not world.pending and not world.model_calls and not world.clipboard_reads
    world.on_copy = None
    select(service, world)
    world.next_callback()
    world.on_restore = lambda: world.identity.update(handle=777)
    world.next_callback()
    assert not world.model_calls


def test_unrelated_window_events_do_not_cancel_selection(setup):
    service, world, _ = setup
    select(service, world)
    service.handle_window_event(WinEvent(WinEventKind.DESTROY, 999, 0))
    service.handle_window_event(WinEvent(WinEventKind.FOREGROUND, 101, 0))
    service.handle_window_event(WinEvent(WinEventKind.LOCATION_CHANGE, 101, 0))
    world.next_callback()
    world.next_callback()
    assert len(world.model_calls) == 1


def test_privacy_pause_clears_ephemeral_content_but_not_saved_cards(setup):
    service, world, database = setup
    database.save_reading_card("主动保存", "解释")
    service._last_text = "未保存的选区"
    service._current_answer = "不再显示"
    service._pending_requests[33] = module.prepare_reading_request("未保存的选区")
    service._pending_provenance[33] = {"sourceApp": "wps.exe"}
    service.set_privacy_suppressed(True)
    assert not service._last_text and not service._current_answer
    assert not service._pending_requests and not service._pending_provenance
    assert len(database.reading_cards()) == 1
    with pytest.raises(ValueError): service.explain_provided_text("本次文字", 1, 2)
    service.set_privacy_suppressed(False)
    assert service._timer.isActive() and not service.bubble["visible"]


def test_late_model_result_after_privacy_pause_never_reappears_or_autosaves(setup):
    service, _world, database = setup
    request = module.prepare_reading_request("correlation", "term")
    service._request_id = 17
    service._pending_requests[17] = request
    service._pending_provenance[17] = {"sourceApp": "wpspdf.exe"}
    service._current_request = request
    service._bubble = {"visible": True, "busy": True}
    service.set_privacy_suppressed(True)
    service.set_privacy_suppressed(False)
    service._finish_request(17, "obsolete model result", "")
    assert not service.bubble["visible"] and not service.bubble["busy"]
    assert service._current_request is None and not service._current_answer
    assert not database.reading_cards()


@pytest.mark.parametrize("boundary", ["clone", "read"])
def test_reentrant_new_capture_is_not_retired_by_old_callback(setup, boundary):
    service, world, _ = setup
    select(service, world)
    newer = []
    def replace():
        select(service, world)
        newer.append(service._capture)
    if boundary == "clone": world.on_clone = replace
    else:
        world.next_callback()
        world.on_read = replace
    world.next_callback()
    assert service._capture is newer[0]
    assert not world.model_calls
    world.on_clone = world.on_read = None
    world.next_callback()
    world.next_callback()
    assert len(world.model_calls) == 1


@pytest.mark.parametrize("operation", ["clone", "copy", "read", "identity", "policy"])
def test_native_or_clipboard_failures_end_only_this_capture(setup, operation):
    service, world, _ = setup
    select(service, world)
    def fail(): raise OSError("synthetic boundary failure")
    if operation == "clone": world.on_clone = fail
    elif operation == "copy": world.on_copy = fail
    elif operation == "identity": service.capture_identity_provider = fail
    elif operation == "policy": service.capture_policy = lambda _: fail()
    else:
        world.next_callback()
        world.on_read = fail
    world.next_callback()
    assert not world.model_calls and not world.restores and service._capture is None


def test_empty_clipboard_is_restored_as_empty_and_no_result_is_sent_twice(setup):
    service, world, _ = setup
    world.clipboard_value = ""
    select(service, world)
    world.next_callback()
    read = world.pending[0][1]
    world.next_callback()
    read()
    assert world.restores == [""] and len(world.model_calls) == 1


@pytest.mark.parametrize("text,expected", [("x", None), ("...", None), ("", None), ("术语" * 3000, "术语" * 2500), ("one   two\n\n\nthree", "one two\n\nthree")],
                         ids=["short", "punctuation", "empty", "long-chinese", "whitespace"])
def test_selection_text_bounds_remain_context_free(setup, text, expected):
    service, world, _ = setup
    world.captured_value = text
    select(service, world)
    world.next_callback()
    world.next_callback()
    assert world.restores == ["用户原剪贴板"]
    if expected is None: assert not world.model_calls
    else: assert world.model_calls[0][0].source_text == expected


@pytest.mark.parametrize("kind", ["allowed", "own-process", "unmanaged", "unknown-birth", "unknown-image", "unknown-focus", "changed-pid", "switched-during-focus"])
def test_native_identity_requires_known_process_and_focused_window(monkeypatch, kind):
    identity = World().identity
    if kind == "own-process": identity["processId"] = os.getpid()
    if kind == "unmanaged": identity["processName"] = "game.exe"
    if kind == "unknown-birth": identity["processStarted"] = None
    if kind == "unknown-image": identity["executableHash"] = ""
    foreground = [101]
    def thread_pid(_hwnd, pid):
        pid._obj.value = 999 if kind == "changed-pid" else identity["processId"]
        return 77
    def focus(_thread, info):
        info._obj.hwndFocus = 0 if kind == "unknown-focus" else 404
        if kind == "switched-during-focus": foreground[0] = 999
        return True
    monkeypatch.setattr(module, "window_identity", lambda hwnd: dict(identity))
    monkeypatch.setattr(module, "_user32", lambda: SimpleNamespace(
        GetForegroundWindow=lambda: foreground[0], GetWindowThreadProcessId=thread_pid,
        GetGUIThreadInfo=focus,
    ))
    result = SelectionService._native_capture_identity()
    assert bool(result) is (kind == "allowed")
    if result: assert result["focusWindow"] == 404 and result["processStarted"] == 303


def test_backend_delivers_privacy_and_window_events_to_capture_service():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "src/lilies/backend.py").read_text("utf-8")
    assert "self.selection.set_privacy_suppressed(is_suppressed)" in source
    function = source.split("def _on_unified_win_event", 1)[1].split("\n    def ", 1)[0]
    assert function.index("self.selection.handle_window_event(event)") < function.index("if event.kind is not WinEventKind.FOREGROUND")


@pytest.mark.parametrize("boundary", ["before-copy", "after-copy"])
@pytest.mark.parametrize("cancel", ["privacy", "disabled", "replaced"])
def test_identity_policy_reentrancy_cannot_access_clipboard(setup, boundary, cancel):
    service, world, _ = setup
    select(service, world)
    if boundary == "after-copy": world.next_callback()
    clone_calls = []
    world.on_clone = lambda: clone_calls.append(True)
    newer = []
    def mutate(_hwnd):
        # A policy adapter may notify subscribers synchronously. Its return
        # value still describes the earlier identity, not the current request.
        service.capture_policy = lambda _: True
        if cancel == "privacy": service.set_privacy_suppressed(True)
        elif cancel == "disabled": service.set_enabled(False)
        else:
            select(service, world)
            newer.append(service._capture)
        return True
    service.capture_policy = mutate
    world.next_callback()
    assert not clone_calls and not world.clipboard_reads and not world.restores
    assert not world.model_calls
    assert world.copy_calls == (1 if boundary == "after-copy" else 0)
    if newer: assert service._capture is newer[0]


@pytest.mark.parametrize("change", ["privacy", "replaced", "replaced-error"])
def test_paper_provenance_reentrancy_does_not_revive_or_replace_capture(setup, change):
    service, world, _ = setup
    newer = []
    def context():
        service.paper_context_provider = lambda: deepcopy(world.paper)
        if change == "privacy": service.set_privacy_suppressed(True)
        else:
            select(service, world)
            newer.append(service._capture)
            if change == "replaced-error": raise RuntimeError("retired context provider")
        return {"paperId": "obsolete-paper", "page": 1}
    service.paper_context_provider = context
    world.keys.add(0x01)
    service._poll_mouse()
    world.keys.remove(0x01)
    world.clock += 0.2
    world.cursor = (50, 40)
    service._poll_mouse()
    if newer:
        assert service._capture is newer[0]
        world.next_callback()
        world.next_callback()
        assert world.model_calls[0][3]["provenance"]["paper"] == world.paper
    else:
        assert service._capture is None and not world.pending
