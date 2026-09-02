from __future__ import annotations

from dataclasses import replace
import threading

from PySide6.QtWidgets import QApplication

from lilies.core.win_event import EVENT_SYSTEM_FOREGROUND, WinEvent, WinEventKind
from lilies.core.window_icons import WindowIconCache
from lilies.core.window_catalog import (
    NativeWindowProvider,
    WindowCatalogRefreshCancelled,
    WindowCatalogService,
    WindowRecord,
    WindowRect,
    canonical_app_id,
    frame_covers_monitor,
)


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeProvider:
    available = True

    def __init__(self, values: list[WindowRecord]) -> None:
        self.values = values
        self.activated: list[int] = []

    def enumerate_windows(self):
        return list(self.values)

    def activate(self, handle: int) -> bool:
        self.activated.append(handle)
        return True


def record(
    handle: int,
    title: str,
    path: str,
    *,
    active: bool = False,
    minimized: bool = False,
) -> WindowRecord:
    return WindowRecord(
        handle,
        title,
        process_id=100 + handle,
        process_name=path.rsplit("\\", 1)[-1],
        executable_path=path,
        active=active,
        minimized=minimized,
        rect=WindowRect(100, 100, 900, 700),
        work_area=WindowRect(0, 0, 1920, 1040),
        monitor_id="monitor:1",
    )


def test_app_id_prefers_aumid_then_normalized_windows_executable() -> None:
    assert canonical_app_id(aumid="Contoso.Reader_abc!App", executable_path="x") == (
        "aumid:contoso.reader_abc!app"
    )


def test_monitor_coverage_distinguishes_full_screen_from_work_area_windows() -> None:
    monitor = WindowRect(1920, 0, 3840, 1080)

    assert frame_covers_monitor(WindowRect(1920, 0, 3840, 1080), monitor) is True
    # A tiny DWM/GetWindowRect edge discrepancy is still full-screen.
    assert frame_covers_monitor(WindowRect(1919, 1, 3841, 1079), monitor) is True
    # Maximized-to-work-area leaves the taskbar strip uncovered.
    assert frame_covers_monitor(WindowRect(1920, 0, 3840, 1040), monitor) is False
    assert frame_covers_monitor(WindowRect(2000, 40, 3760, 1040), monitor) is False
    assert frame_covers_monitor(None, monitor) is False


def test_window_record_serializes_native_full_screen_state() -> None:
    value = replace(
        record(8, "Presentation", r"C:\Apps\slides.exe"),
        full_screen=True,
    ).to_dict()

    assert value["fullScreen"] is True
    assert canonical_app_id(executable_path=r"C:\Program Files\WPS\..\WPS\WPS.EXE") == (
        r"exe:c:\program files\wps\wps.exe"
    )


def test_windows_are_grouped_by_app_id_with_legacy_representative_fields() -> None:
    provider = FakeProvider(
        [
            record(1, "Paper A", r"C:\Apps\WPS.exe", active=True),
            record(2, "Paper B", r"c:\apps\wps.EXE", minimized=True),
            record(3, "Research", r"C:\Apps\Browser.exe"),
        ]
    )
    service = WindowCatalogService(provider)

    groups = service.refresh(now=10.0)

    assert len(groups) == 2
    wps = groups[0]
    assert wps["handle"] == 1
    assert wps["title"] == "Paper A"
    assert wps["windowCount"] == 2
    assert wps["active"] is True
    assert wps["minimized"] is False
    assert [item["handle"] for item in wps["windows"]] == [1, 2]
    assert {
        "appId",
        "monitorId",
        "currentVirtualDesktop",
        "fullScreen",
        "dpiScale",
        "titleBarHeight",
        "rect",
        "mruRank",
    } <= set(wps["windows"][0])


def test_foreground_events_are_debounced_and_update_mru() -> None:
    clock = Clock(20.0)
    provider = FakeProvider(
        [
            record(1, "First", r"C:\Apps\one.exe", active=True),
            record(2, "Second", r"C:\Apps\two.exe"),
        ]
    )
    service = WindowCatalogService(
        provider,
        debounce_seconds=0.075,
        safety_refresh_seconds=10.0,
        clock=clock,
    )
    service.refresh()
    provider.values = [
        replace(provider.values[0], active=False),
        replace(provider.values[1], active=True),
    ]
    service.handle_event(
        WinEvent(WinEventKind.FOREGROUND, 2, EVENT_SYSTEM_FOREGROUND)
    )

    clock.value += 0.05
    assert service.tick() is False
    clock.value += 0.03
    assert service.tick() is True
    assert service.list_windows()[0]["handle"] == 2


def test_catalogue_fails_closed_with_an_unavailable_provider() -> None:
    class UnavailableProvider(FakeProvider):
        available = False

    service = WindowCatalogService(UnavailableProvider([]))

    assert service.refresh() == []
    assert service.status()["available"] is False
    assert service.activate(999) is False


def test_icon_resolver_enriches_group_and_nested_window_urls() -> None:
    provider = FakeProvider([record(7, "Paper", r"C:\Apps\Reader.exe", active=True)])
    resolved: list[str] = []

    def resolve_icon(value: WindowRecord) -> str:
        resolved.append(value.executable_path)
        return "file:///F:/private-data/cache/window-icons/reader.png"

    service = WindowCatalogService(provider, icon_resolver=resolve_icon)
    group = service.refresh(now=1.0)[0]

    assert resolved == [r"C:\Apps\Reader.exe"]
    assert group["iconUrl"].endswith("reader.png")
    assert group["windows"][0]["iconUrl"] == group["iconUrl"]


def test_due_tick_can_be_observed_without_refreshing_synchronously() -> None:
    clock = Clock(20.0)
    provider = FakeProvider([record(1, "Paper", r"C:\Apps\Reader.exe")])
    service = WindowCatalogService(
        provider,
        safety_refresh_seconds=10.0,
        clock=clock,
    )
    service.refresh(now=0.0)
    provider.enumerate_windows = lambda: (_ for _ in ()).throw(
        AssertionError("GUI tick performed a synchronous enumeration")
    )

    assert service.tick(now=20.0, refresh=False) is True


def test_refresh_does_not_clear_an_event_that_arrived_during_enumeration() -> None:
    provider = FakeProvider([record(1, "Paper", r"C:\Apps\Reader.exe")])
    service = WindowCatalogService(provider)
    original_enumerate = provider.enumerate_windows
    injected = False

    def enumerate_with_event():
        nonlocal injected
        values = original_enumerate()
        if not injected:
            injected = True
            service.handle_event(
                WinEvent(WinEventKind.FOREGROUND, 1, EVENT_SYSTEM_FOREGROUND)
            )
        return values

    provider.enumerate_windows = enumerate_with_event
    service.refresh(now=10.0)

    assert service.status()["dirty"] is True


def test_refresh_does_not_clear_activation_that_arrived_during_enumeration() -> None:
    provider = FakeProvider(
        [
            record(1, "First", r"C:\Apps\one.exe", active=True),
            record(2, "Second", r"C:\Apps\two.exe"),
        ]
    )
    service = WindowCatalogService(provider)
    service.refresh(now=1.0)
    stale_values = list(provider.values)
    started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def blocked_enumeration():
        started.set()
        assert release.wait(1.0)
        return list(stale_values)

    provider.enumerate_windows = blocked_enumeration

    def refresh_in_background() -> None:
        try:
            service.refresh(now=2.0)
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    worker = threading.Thread(target=refresh_in_background)
    worker.start()
    assert started.wait(1.0)
    assert service.activate(2) is True
    provider.values = [
        replace(stale_values[0], active=False),
        replace(stale_values[1], active=True),
    ]
    release.set()
    worker.join(1.0)

    assert not worker.is_alive()
    assert errors == []
    assert service.status()["dirty"] is True

    provider.enumerate_windows = lambda: list(provider.values)
    service.refresh(now=3.0)
    assert service.status()["dirty"] is False
    assert service.list_windows()[0]["handle"] == 2


def test_native_interruptible_enumeration_discards_partial_handle_snapshot(
    monkeypatch,
) -> None:
    provider = NativeWindowProvider()
    cancellation = threading.Event()
    observed_probes: list[object] = []

    monkeypatch.setattr(
        NativeWindowProvider,
        "available",
        property(lambda _self: True),
    )
    monkeypatch.setattr(
        "lilies.core.window_catalog.windows.foreground_window",
        lambda: 0,
    )

    def interrupted_handles(*_args, should_cancel=None, **_kwargs):
        observed_probes.append(should_cancel)
        cancellation.set()
        # Simulate EnumWindows returning the prefix gathered before its
        # callback noticed the cancellation request.
        return [101]

    monkeypatch.setattr(
        "lilies.core.window_catalog.windows.enumerate_manageable_window_handles",
        interrupted_handles,
    )

    try:
        provider.enumerate_windows_interruptibly(cancellation.is_set)
    except WindowCatalogRefreshCancelled:
        pass
    else:  # pragma: no cover - the cancellation contract is asserted here
        raise AssertionError("partial native enumeration was published")

    assert observed_probes == [cancellation.is_set]


def test_cancelled_refresh_keeps_safety_obligation_due() -> None:
    clock = Clock(20.0)

    class InterruptibleProvider(FakeProvider):
        def enumerate_windows_interruptibly(self, should_cancel):
            assert should_cancel() is True
            raise WindowCatalogRefreshCancelled

    service = WindowCatalogService(
        InterruptibleProvider([record(1, "Paper", r"C:\Apps\Reader.exe")]),
        safety_refresh_seconds=10.0,
        clock=clock,
    )

    try:
        service.refresh(now=20.0, should_cancel=lambda: True)
    except WindowCatalogRefreshCancelled:
        pass
    else:  # pragma: no cover - asserted above
        raise AssertionError("cancelled refresh did not propagate")

    # Cancellation is unlike a provider failure: it must not advance the
    # safety clock and hide the refresh obligation after drag release.
    assert service.tick(now=20.0, refresh=False) is True
    assert service.status()["lastError"] == ""


def test_window_icon_worker_uses_cache_only_and_never_creates_qt_provider(
    tmp_path,
) -> None:
    QApplication.instance() or QApplication([])
    executable = tmp_path / "reader.exe"
    executable.write_bytes(b"not-a-real-executable")
    value = record(7, "Paper", str(executable), active=True)
    cache = WindowIconCache(tmp_path / "icons")
    entry = cache._cache_entry(value)
    assert entry is not None
    _path, _digest, destination = entry
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"cached-png")
    results: list[str] = []

    worker = threading.Thread(target=lambda: results.append(cache.lookup(value)))
    worker.start()
    worker.join(1.0)

    assert not worker.is_alive()
    assert results == [destination.as_uri()]
    assert cache._provider is None

    destination.unlink()
    cache._memory.clear()
    miss_results: list[str] = []
    miss_worker = threading.Thread(
        target=lambda: miss_results.append(cache.resolve(value))
    )
    miss_worker.start()
    miss_worker.join(1.0)
    assert miss_results == [""]
    assert cache._provider is None
