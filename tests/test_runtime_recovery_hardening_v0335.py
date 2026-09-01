from __future__ import annotations

from PySide6.QtWidgets import QApplication

from lilies.backend import Backend


class _CalendarStatusFailure:
    def status(self):
        raise OSError("temporary credential-store lock")

    def close(self):
        return None


class _ConnectedCalendar:
    def status(self):
        return {"connected": True}

    def refresh(self):
        return {"ok": True}

    def close(self):
        return None


def test_shell_tick_keeps_explorer_recovery_alive_when_watchdog_restart_fails(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    explorer_checks: list[str] = []
    try:
        monkeypatch.setattr(
            backend.shell,
            "maintain_recovery_monitor",
            lambda: (_ for _ in ()).throw(OSError("watchdog unavailable")),
        )
        monkeypatch.setattr(
            backend.shell,
            "maintain_explorer",
            lambda: explorer_checks.append("checked") or False,
        )

        backend._monitor_shell()

        assert explorer_checks == ["checked"]
        assert "自动重试" in backend.status
    finally:
        backend.shutdown()
        app.processEvents()


def test_productivity_timer_boundary_retries_after_an_unexpected_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    calls: list[str] = []

    def refresh_once_then_recover() -> None:
        calls.append("tick")
        if len(calls) == 1:
            raise RuntimeError("synthetic timer failure")

    try:
        backend._productivity_timer.stop()
        monkeypatch.setattr(backend, "refreshProductivity", refresh_once_then_recover)

        backend._refreshProductivityTick()
        assert backend._productivity_tick_failures == 1
        assert "下一秒会自动重试" in backend.status

        backend._refreshProductivityTick()
        assert calls == ["tick", "tick"]
        assert backend._productivity_tick_failures == 0
    finally:
        backend.shutdown()
        app.processEvents()


def test_calendar_status_failure_schedules_retry_without_starting_a_worker(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._calendar_sync_timer.stop()
        backend.calendar_connector = _CalendarStatusFailure()

        backend.calendarRefresh()

        assert backend._calendar_refresh_running is False
        assert backend._calendar_sync_timer.isActive() is True
        assert backend._connector_runtime_errors["calendar"] == (
            "calendar-status-unavailable"
        )
        assert "一分钟后自动重试" in backend.status
    finally:
        backend.shutdown()
        app.processEvents()


def test_calendar_thread_start_failure_clears_inflight_and_keeps_retry_timer(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._calendar_sync_timer.stop()
        backend.calendar_connector = _ConnectedCalendar()
        monkeypatch.setattr(
            "lilies.backend.threading.Thread.start",
            lambda _thread: (_ for _ in ()).throw(RuntimeError("start failed")),
        )

        backend.calendarRefresh()

        assert backend._calendar_refresh_running is False
        assert backend._calendar_sync_timer.isActive() is True
        assert backend._connector_threads == set()
        assert backend._connector_runtime_errors["calendar"] == (
            "calendar-worker-start-failed"
        )
        assert "一分钟后自动重试" in backend.status
    finally:
        backend.shutdown()
        app.processEvents()
