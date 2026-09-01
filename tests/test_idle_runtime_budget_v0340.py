from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.core.focus_diversion import FocusDiversionMonitor


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _quiet_productivity_sources(backend: Backend, monkeypatch) -> None:
    monkeypatch.setattr(backend.focus, "status", lambda: None)
    monkeypatch.setattr(backend.reading_sessions, "status", lambda: None)
    monkeypatch.setattr(
        backend.reminders,
        "claim_due",
        lambda *, channel, limit: [],
    )
    monkeypatch.setattr(backend.event_outbox, "pending", lambda *, limit: [])
    monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])
    monkeypatch.setattr(backend, "_refresh_focus_diversion", lambda focus, habitat: False)


def test_high_frequency_native_pump_never_queries_productivity_storage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        backend._last_v03_pump_at = time.monotonic()
        monkeypatch.setattr(backend.window_catalog, "pump", lambda: False)
        monkeypatch.setattr(
            backend.focus,
            "status",
            lambda: (_ for _ in ()).throw(
                AssertionError("75 ms pump queried focus SQLite state")
            ),
        )
        monkeypatch.setattr(
            backend.reading_sessions,
            "status",
            lambda: (_ for _ in ()).throw(
                AssertionError("75 ms pump queried reading SQLite state")
            ),
        )

        for _ in range(20):
            backend._pump_v03()
    finally:
        backend.shutdown()
        app.processEvents()


def test_scheduled_idle_productivity_tick_does_not_invalidate_every_qml_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        for _ in range(5):
            backend._refreshProductivityTick()

        assert emissions == []
        assert backend._productivity_tick_in_progress is False
        assert backend._productivity_tick_failures == 0
    finally:
        backend.shutdown()
        app.processEvents()


def test_direct_productivity_refresh_still_publishes_mutation_results(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        backend.refreshProductivity()

        assert emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_direct_productivity_refresh_publishes_while_delivery_is_privacy_blocked(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        backend.presence.update(sensitive=True)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        backend.refreshProductivity()

        assert emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_idle_scheduler_defers_then_delivers_due_narrative_and_outbox(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    notices: list[tuple[str, str]] = []
    productivity_emissions: list[bool] = []
    calls: list[tuple[str, str]] = []
    due = {
        "deliveryId": "delivery-1",
        "title": "Stand up",
        "body": "Stretch for a minute",
    }
    outbox = [
        {"outbox_id": "outbox-1"},
        {"outbox_id": "outbox-2"},
    ]
    narrative = {
        "narrative_id": "narrative-1",
        "title": "Milestone",
        "body": "A new keepsake is ready",
    }
    try:
        backend._productivity_timer.stop()
        monkeypatch.setattr(backend.focus, "status", lambda: None)
        monkeypatch.setattr(backend.reading_sessions, "status", lambda: None)
        monkeypatch.setattr(
            backend, "_refresh_focus_diversion", lambda focus, habitat: False
        )
        monkeypatch.setattr(
            backend.reminders,
            "claim_due",
            lambda *, channel, limit: calls.append(("claim", channel)) or [due],
        )
        monkeypatch.setattr(
            backend.reminders,
            "mark_delivery",
            lambda delivery_id, *, delivered: calls.append(
                ("reminder", f"{delivery_id}:{delivered}")
            ),
        )
        monkeypatch.setattr(
            backend.event_outbox,
            "pending",
            lambda *, limit: calls.append(("outbox-pending", str(limit))) or outbox,
        )
        monkeypatch.setattr(
            backend.event_outbox,
            "delivered",
            lambda outbox_id: calls.append(("outbox-delivered", outbox_id)),
        )
        monkeypatch.setattr(
            backend.event_outbox,
            "failed",
            lambda outbox_id, reason: calls.append(("outbox-failed", outbox_id)),
        )
        monkeypatch.setattr(
            backend.narrative,
            "pending",
            lambda *, limit: calls.append(("narrative-pending", str(limit)))
            or [narrative],
        )
        monkeypatch.setattr(
            backend.narrative,
            "acknowledge",
            lambda narrative_id: calls.append(("narrative-ack", narrative_id)),
        )
        def record_notice(title: str, body: str) -> None:
            notices.append((title, body))
            calls.append(("notice", title))

        backend.reminderDue.connect(record_notice)
        backend.productivityChanged.connect(
            lambda: productivity_emissions.append(True)
        )

        backend.presence.update(sensitive=True)
        backend._refreshProductivityTick()
        assert calls == []
        assert notices == []
        assert productivity_emissions == []

        backend.presence.update(sensitive=False)
        backend._refreshProductivityTick()

        assert notices == [
            ("Stand up", "Stretch for a minute"),
            ("Milestone", "A new keepsake is ready"),
        ]
        assert calls == [
            ("claim", "bubble"),
            ("notice", "Stand up"),
            ("reminder", "delivery-1:True"),
            ("outbox-pending", "50"),
            ("narrative-pending", "1"),
            ("notice", "Milestone"),
            ("narrative-ack", "narrative-1"),
            ("outbox-delivered", "outbox-1"),
            ("outbox-delivered", "outbox-2"),
        ]
        assert productivity_emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_refresh_obligation_survives_failure_after_due_reminder_commit(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    notices: list[tuple[str, str]] = []
    productivity_emissions: list[bool] = []
    deliveries: list[str] = []
    claim_count = 0
    outbox_count = 0
    due = {
        "deliveryId": "delivery-recovery",
        "title": "Committed reminder",
        "body": "The UI refresh must survive",
    }

    def claim_due(*, channel, limit):
        nonlocal claim_count
        claim_count += 1
        return [due] if claim_count == 1 else []

    def pending_outbox(*, limit):
        nonlocal outbox_count
        outbox_count += 1
        if outbox_count == 1:
            raise OSError("temporary outbox read failure")
        return []

    try:
        backend._productivity_timer.stop()
        monkeypatch.setattr(backend.focus, "status", lambda: None)
        monkeypatch.setattr(backend.reading_sessions, "status", lambda: None)
        monkeypatch.setattr(
            backend, "_refresh_focus_diversion", lambda focus, habitat: False
        )
        monkeypatch.setattr(backend.reminders, "claim_due", claim_due)
        monkeypatch.setattr(
            backend.reminders,
            "mark_delivery",
            lambda delivery_id, *, delivered: deliveries.append(delivery_id),
        )
        monkeypatch.setattr(backend.event_outbox, "pending", pending_outbox)
        monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])
        backend.reminderDue.connect(
            lambda title, body: notices.append((title, body))
        )
        backend.productivityChanged.connect(
            lambda: productivity_emissions.append(True)
        )

        backend._refreshProductivityTick()

        assert notices == [
            ("Committed reminder", "The UI refresh must survive")
        ]
        assert deliveries == ["delivery-recovery"]
        assert productivity_emissions == []
        assert backend._productivity_tick_failures == 1

        backend._refreshProductivityTick()

        assert claim_count == 2
        assert outbox_count == 2
        assert productivity_emissions == [True]
        assert backend._productivity_tick_failures == 0
        dirty_generation, refresh_pending = backend._productivity_dirty_snapshot()
        assert dirty_generation > 0
        assert refresh_pending is False
    finally:
        backend.shutdown()
        app.processEvents()


def test_registry_productivity_mutation_is_published_by_next_idle_tick(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        envelope = backend.registry.invoke(
            "tasks",
            "create",
            {"title": "Created outside a QML slot"},
            origin="socket",
            confirmed=True,
        )
        assert envelope["result"]["title"] == "Created outside a QML slot"
        assert emissions == []

        backend._refreshProductivityTick()

        assert emissions == [True]
        assert any(
            item["title"] == "Created outside a QML slot"
            for item in backend.taskItems
        )
    finally:
        backend.shutdown()
        app.processEvents()


def test_chat_component_productivity_mutation_refreshes_immediately(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        envelope = backend.registry.invoke(
            "tasks",
            "create",
            {"title": "Created by a model component"},
            origin="model",
            confirmed=True,
        )
        backend.chat.componentInvoked.emit("tasks", "create", envelope)

        assert emissions == [True]
        assert any(
            item["title"] == "Created by a model component"
            for item in backend.taskItems
        )
    finally:
        backend.shutdown()
        app.processEvents()


def test_calendar_reconcile_completion_invalidates_reminder_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    reconciled: list[list[dict[str, object]]] = []
    try:
        backend._productivity_timer.stop()
        _quiet_productivity_sources(backend, monkeypatch)
        assert backend.calendar_connector is not None
        events = [{"id": "calendar-1", "summary": "Review"}]
        monkeypatch.setattr(
            backend.calendar_connector, "upcoming", lambda *, limit: events
        )
        monkeypatch.setattr(
            backend.calendar_reminder_bridge,
            "reconcile",
            lambda values: reconciled.append(list(values)),
        )
        monkeypatch.setattr(backend, "_schedule_calendar_refresh", lambda: None)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        backend._on_connector_operation_finished(
            "calendar", "refresh", True, {"ok": True}
        )

        assert reconciled == [events]
        assert emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_component_signal_unwraps_focus_envelope_for_timer_transition(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)

    class Clock:
        def __init__(self) -> None:
            self.value = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

        def __call__(self) -> datetime:
            return self.value

        def advance(self, *, seconds: int) -> None:
            self.value += timedelta(seconds=seconds)

    clock = Clock()
    try:
        backend._productivity_timer.stop()
        backend.focus.now = clock
        monkeypatch.setattr(
            backend.reminders,
            "claim_due",
            lambda *, channel, limit: [],
        )
        monkeypatch.setattr(backend.event_outbox, "pending", lambda *, limit: [])
        monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])

        started = backend.registry.invoke(
            "focus",
            "start",
            {"minutes": 5},
            origin="model",
            confirmed=True,
        )
        backend.chat.componentInvoked.emit("focus", "start", started)
        session_id = str(started["result"]["session_id"])

        assert backend.focusTransition["kind"] == "started"
        assert backend.focusTransition["sessionId"] == session_id
        assert backend.focusTransition["durationSeconds"] == 300

        clock.advance(seconds=120)
        finished = backend.registry.invoke(
            "focus",
            "finish",
            {"sessionId": session_id},
            origin="model",
            confirmed=True,
        )
        backend.chat.componentInvoked.emit("focus", "finish", finished)

        assert backend.focusTransition["kind"] == "finished"
        assert backend.focusTransition["sessionId"] == session_id
        assert backend.focusTransition["elapsedSeconds"] == 120
        assert backend.focusTransition["durationSeconds"] == 300
    finally:
        backend.shutdown()
        app.processEvents()


def test_scheduled_active_focus_keeps_one_second_ui_clock(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    focus_reads: list[bool] = []
    active = {
        "session_id": "focus-live",
        "state": "running",
        "active_seconds": 10,
        "live_active_seconds": 10,
        "planned_seconds": 1500,
    }
    try:
        backend._productivity_timer.stop()

        def focus_status():
            focus_reads.append(True)
            return dict(active)

        monkeypatch.setattr(backend.focus, "status", focus_status)
        monkeypatch.setattr(backend.reading_sessions, "status", lambda: None)
        monkeypatch.setattr(
            backend,
            "_finish_elapsed_focus_if_due",
            lambda value=None: False,
        )
        monkeypatch.setattr(
            backend.reminders,
            "claim_due",
            lambda *, channel, limit: [],
        )
        monkeypatch.setattr(backend.event_outbox, "pending", lambda *, limit: [])
        monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])
        monkeypatch.setattr(backend, "_refresh_focus_diversion", lambda focus, habitat: False)
        backend.productivityChanged.connect(lambda: emissions.append(True))

        backend._refreshProductivityTick()

        assert focus_reads == [True]
        assert emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_scheduled_active_reading_keeps_one_second_ui_clock(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    emissions: list[bool] = []
    reading_reads: list[bool] = []
    active = {
        "session_id": "reading-live",
        "state": "running",
        "active_seconds": 12,
        "live_active_seconds": 12,
    }
    try:
        backend._productivity_timer.stop()

        def reading_status():
            reading_reads.append(True)
            return dict(active)

        monkeypatch.setattr(backend.focus, "status", lambda: None)
        monkeypatch.setattr(backend.reading_sessions, "status", reading_status)
        monkeypatch.setattr(
            backend.reminders,
            "claim_due",
            lambda *, channel, limit: [],
        )
        monkeypatch.setattr(backend.event_outbox, "pending", lambda *, limit: [])
        monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])
        monkeypatch.setattr(
            backend, "_refresh_focus_diversion", lambda focus, habitat: False
        )
        backend.productivityChanged.connect(lambda: emissions.append(True))

        backend._refreshProductivityTick()

        assert reading_reads == [True]
        assert emissions == [True]
    finally:
        backend.shutdown()
        app.processEvents()


def test_one_second_scheduler_advances_focus_diversion_once_after_threshold(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    diversion_emissions: list[bool] = []
    notification_emissions: list[tuple[str, str]] = []

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    active = {
        "session_id": "focus-diversion-live",
        "state": "running",
        "active_seconds": 10,
        "live_active_seconds": 10,
        "planned_seconds": 1500,
    }
    try:
        backend._productivity_timer.stop()
        backend.focus_diversion = FocusDiversionMonitor(clock=clock)
        monkeypatch.setattr(backend.focus, "status", lambda: dict(active))
        monkeypatch.setattr(backend.reading_sessions, "status", lambda: None)
        monkeypatch.setattr(
            backend, "_finish_elapsed_focus_if_due", lambda value=None: False
        )
        monkeypatch.setattr(
            backend.reminders,
            "claim_due",
            lambda *, channel, limit: [],
        )
        monkeypatch.setattr(backend.event_outbox, "pending", lambda *, limit: [])
        monkeypatch.setattr(backend.narrative, "pending", lambda *, limit: [])
        backend._habitat_status = {"state": "desktop"}
        backend.focusDiversionChanged.connect(
            lambda: diversion_emissions.append(True)
        )
        backend.reminderDue.connect(
            lambda title, body: notification_emissions.append((title, body))
        )

        # Seed the session before the entertainment visit, as focusStart's
        # direct refresh does in the live application.
        backend._refreshProductivityTick()
        backend.focus_diversion.update_foreground(
            "game:1", "game.exe", entertainment=True
        )

        clock.value = 44.9
        backend._refreshProductivityTick()
        assert backend.focusDiversion == {}

        clock.value = 45.0
        backend._refreshProductivityTick()
        assert backend.focusDiversion["sessionId"] == "focus-diversion-live"
        assert backend.focusDiversion["stableSeconds"] == 45

        clock.value = 120.0
        backend._refreshProductivityTick()
        assert diversion_emissions == [True]
        assert notification_emissions == []
    finally:
        backend.shutdown()
        app.processEvents()


def test_pet_geometry_heartbeat_is_not_a_frame_rate_poll() -> None:
    qml = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    geometry_call = qml.index("backend.updatePetGeometry({")
    timer_start = qml.rfind("Timer {", 0, geometry_call)
    timer_contract = qml[timer_start:geometry_call]

    assert "interval: 500" in timer_contract
    assert "interval: 150" not in timer_contract
    assert "repeat: true" in timer_contract
    assert "running: petWindow.visible" in timer_contract
