from __future__ import annotations

import json
from copy import deepcopy

from lilies.core.desktop_peek import DesktopPeekService


def placement(show_cmd: int = 1) -> dict[str, object]:
    return {
        "flags": 0,
        "showCmd": show_cmd,
        "minPosition": [-32000, -32000],
        "maxPosition": [0, 0],
        "normalPosition": [10, 20, 810, 620],
        "device": [0, 0, 1920, 1080],
    }


def identity(handle: int, *, process_started: int | None = None) -> dict[str, object]:
    return {
        "handle": handle,
        "processId": 1000 + handle,
        "processStarted": process_started if process_started is not None else 50_000 + handle,
        "processName": f"app-{handle}.exe",
        "executableHash": f"exe-{handle}",
        "className": f"WindowClass{handle}",
        "titleHash": f"title-{handle}",
    }


class FakeWindowApi:
    def __init__(self) -> None:
        self.handles = [1, 2, 3]
        self.identities = {handle: identity(handle) for handle in self.handles}
        self.placements = {handle: placement(3 if handle == 3 else 1) for handle in self.handles}
        self.minimized = {2}
        self.foreground_handle = 1
        self.minimize_calls: list[int] = []
        self.restore_calls: list[tuple[int, dict[str, object]]] = []
        self.foreground_calls: list[int] = []
        self.fail_restore: set[int] = set()

    def list_handles(self) -> list[int]:
        return list(self.handles)

    def identity(self, handle: int):
        value = self.identities.get(handle)
        return deepcopy(value) if value is not None else None

    def placement(self, handle: int):
        value = self.placements.get(handle)
        return deepcopy(value) if value is not None else None

    def is_minimized(self, handle: int) -> bool:
        return handle in self.minimized

    def minimize(self, handle: int, _placement: dict[str, object]) -> bool:
        self.minimize_calls.append(handle)
        self.minimized.add(handle)
        return True

    def restore(self, handle: int, original: dict[str, object]) -> bool:
        self.restore_calls.append((handle, deepcopy(original)))
        if handle in self.fail_restore:
            return False
        self.minimized.discard(handle)
        return True

    def foreground(self) -> int:
        return self.foreground_handle

    def set_foreground(self, handle: int) -> bool:
        self.foreground_calls.append(handle)
        return True


def test_toggle_restores_only_windows_minimized_by_this_transaction(tmp_path):
    api = FakeWindowApi()
    service = DesktopPeekService(tmp_path, api)

    hidden = service.toggle()

    assert hidden["active"] is True
    assert hidden["minimized"] == 2
    assert api.minimize_calls == [1, 3]
    assert api.minimized == {1, 2, 3}

    # Window 3 was manually restored, while a new window appeared during peek.
    api.minimized.remove(3)
    api.handles.append(4)
    api.identities[4] = identity(4)
    api.placements[4] = placement()

    shown = service.toggle()

    assert shown["active"] is False
    assert shown["restored"] == 1
    assert [handle for handle, _ in api.restore_calls] == [1]
    assert api.restore_calls[0][1]["showCmd"] == 1
    assert 2 in api.minimized  # It was already minimized before the transaction.
    assert 4 not in api.minimized  # New windows are never touched.
    assert api.foreground_calls == [1]
    assert not service.journal_path.exists()


def test_restore_order_preserves_original_relative_z_order(tmp_path):
    api = FakeWindowApi()
    api.minimized.clear()
    service = DesktopPeekService(tmp_path, api)

    service.peek()
    result = service.restore()

    assert result["restored"] == 3
    assert [handle for handle, _ in api.restore_calls] == [3, 2, 1]
    assert api.restore_calls[0][1]["showCmd"] == 3


def test_recycled_hwnd_is_skipped(tmp_path):
    api = FakeWindowApi()
    service = DesktopPeekService(tmp_path, api)
    service.peek()
    api.identities[1]["processStarted"] = 999_999

    result = service.restore()

    assert result["restored"] == 1
    assert [handle for handle, _ in api.restore_calls] == [3]
    assert 1 in api.minimized
    assert not service.journal_path.exists()


def test_failed_restore_stays_recoverable_and_next_start_retries(tmp_path):
    api = FakeWindowApi()
    service = DesktopPeekService(tmp_path, api)
    service.peek()
    api.fail_restore.add(1)

    first = service.restore()

    assert first["active"] is True
    assert first["failed"] == 1
    assert service.status()["recoverable"] is True
    api.fail_restore.clear()

    recovered = DesktopPeekService(tmp_path, api).recover_pending()

    assert recovered["active"] is False
    assert recovered["recovery"] is True
    assert recovered["restored"] == 1
    assert not service.journal_path.exists()


def test_transaction_is_durable_before_window_mutation(tmp_path):
    class InspectingApi(FakeWindowApi):
        def minimize(self, handle: int, original: dict[str, object]) -> bool:
            journal = tmp_path / "runtime" / "desktop-peek-transaction.json"
            saved = json.loads(journal.read_text("utf-8"))
            entry = next(item for item in saved["windows"] if item["identity"]["handle"] == handle)
            assert entry["attempted"] is True
            assert entry["placement"] == original
            return super().minimize(handle, original)

    api = InspectingApi()

    DesktopPeekService(tmp_path, api).peek()

    assert api.minimize_calls == [1, 3]
