from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtGui import QGuiApplication as QCoreApplication

from lilies.companion_controller import CompanionController
from lilies.core.activity import StagedCapture
from lilies.core.companion import ContentCategory
from lilies.core.database import Database


def _controller(tmp_path: Path) -> CompanionController:
    return CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )


def test_interaction_barrier_stops_and_restores_automatic_timers(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    controller._active = True
    controller._activity_enabled = True
    controller._online_content = True
    controller._smart_observation = True
    controller._timer.start()
    controller._archive_timer.start()
    controller._content_timer.start()
    controller._modality_retry_timer.start(1)
    try:
        controller.set_interaction_suspended(True)
        assert controller._interaction_suspended is True
        assert controller._timer.isActive() is False
        assert controller._archive_timer.isActive() is False
        assert controller._content_timer.isActive() is False
        assert controller._modality_retry_timer.isActive() is False

        controller.set_interaction_suspended(False)
        assert controller._interaction_suspended is False
        assert controller._timer.isActive() is True
        assert controller._archive_timer.isActive() is True
        assert controller._content_timer.isActive() is True
        assert controller._modality_retry_timer.isActive() is True
        # Release must not immediately probe a model using the pre-drag turn.
        assert controller._modality_retry_timer.remainingTime() >= 500
    finally:
        controller.shutdown()
    assert app is not None


def test_heartbeat_and_internal_barrier_do_not_emit_without_visible_change(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    emissions: list[None] = []
    controller.changed.connect(lambda: emissions.append(None))
    try:
        # The first heartbeat publishes the initial disabled/waiting state;
        # subsequent stable/idle clock movement is internal only.
        controller._consider()
        assert len(emissions) == 1
        controller._consider()
        assert len(emissions) == 1

        controller.set_interaction_suspended(True)
        controller.set_interaction_suspended(False)
        assert len(emissions) == 1
    finally:
        controller.shutdown()
    assert app is not None


def test_interaction_barrier_blocks_all_automatic_entry_points(
    tmp_path: Path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    controller._active = True
    controller._smart_observation = True
    controller._online_content = True
    controller.set_interaction_suspended(True)
    starts: list[str] = []
    monkeypatch.setattr(
        controller,
        "_start_worker",
        lambda _target, *, name: starts.append(name) or True,
    )
    try:
        controller._consider()
        controller._probe_modalities()
        controller._consider_archival()
        controller.refreshSource("arxiv")
        controller._refresh_next_source()
        assert controller._start_generation(None, force=False) is False
        assert starts == []
        assert controller._probe_busy is False
        assert controller._archive_busy is False
        assert controller._source_busy is False
        assert controller.refresh_source_component("arxiv", "", 5) == {
            "providerId": "arxiv",
            "state": "suspended",
            "items": [],
            "error": "interaction-suspended",
        }
    finally:
        controller.shutdown()
    assert app is not None


def test_interaction_barrier_cancels_generation_discards_late_result_and_rejects_manual_requests(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    cancel_event = threading.Event()
    inflight_path = tmp_path / "capture-staging" / "inflight.png"
    inflight_path.parent.mkdir(parents=True, exist_ok=True)
    inflight_path.write_bytes(b"inflight")
    inflight = StagedCapture(inflight_path, tmp_path / "capture-library")
    controller._busy = True
    controller._active_generation_token = 27
    controller._active_generation_user_requested = True
    controller._generation_cancel_event = cancel_event
    controller._active_generation_has_capture = True
    controller._set_inflight_capture(inflight)
    try:
        controller.set_interaction_suspended(True)
        assert cancel_event.is_set()
        assert controller._active_generation_token == 0
        assert controller.busy is False
        assert not inflight_path.exists()
        assert "重试" in controller.activityStatus["requestFeedback"]

        late_path = tmp_path / "capture-staging" / "late.png"
        late_path.write_bytes(b"late")
        late_capture = StagedCapture(late_path, tmp_path / "capture-library")
        controller._accept_generation(
            {
                "generationToken": 27,
                "capture": late_capture,
                "result": {"summary": "late", "detail": "late"},
                "category": ContentCategory.LORE,
                "sceneLabel": "stale",
                "force": False,
            }
        )
        assert not late_path.exists()
        assert controller.bubble == {}
        with controller.database.connect() as db:
            assert (
                db.execute("SELECT COUNT(*) FROM proactive_sessions").fetchone()[0]
                == 0
            )

        assert controller.requestNow() is False
        assert controller.requestScreenNow() is False
        assert "没有排队" in controller.activityStatus["requestFeedback"]
    finally:
        controller.shutdown()
    assert app is not None


def test_interaction_barrier_does_not_cancel_an_inflight_bubble_reply(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    # Bubble replies use their own broker task and do not own the proactive
    # generation token/capture.  Raising the drag barrier must not reinterpret
    # that explicit conversation as automatic observation.
    controller._busy = True
    controller._active_generation_token = 0
    controller._generation_cancel_event = None
    try:
        controller.set_interaction_suspended(True)
        assert controller.busy is True
    finally:
        controller.shutdown()
    assert app is not None


def test_late_source_result_is_published_once_after_interaction_release(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    messages: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=messages.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    emissions: list[None] = []
    controller.sourcesChanged.connect(lambda: emissions.append(None))
    controller._source_busy = True
    payload = {"providerId": "arxiv", "state": "refreshed", "items": []}
    try:
        controller.set_interaction_suspended(True)
        controller._accept_source(payload)
        assert controller._source_busy is False
        assert controller._deferred_source_payload is payload
        assert messages == []
        assert emissions == []

        controller.set_interaction_suspended(False)
        assert controller._deferred_source_payload is None
        assert len(messages) == 1
        assert emissions == [None]

        controller.set_interaction_suspended(False)
        assert len(messages) == 1
        assert emissions == [None]
    finally:
        controller.shutdown()
    assert app is not None


def test_interaction_barrier_cooperatively_cancels_archive_worker(
    tmp_path: Path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = _controller(tmp_path)
    cancel_event = threading.Event()
    controller._archive_busy = True
    controller._archive_cancel_event = cancel_event
    try:
        controller.set_interaction_suspended(True)
        assert cancel_event.is_set()
        # Cancelling memory archival is independent from an explicit bubble
        # reply, whose busy state remains intact by contract.
        controller._busy = True
        controller.set_interaction_suspended(False)
        assert controller.busy is True
    finally:
        controller.shutdown()
    assert app is not None
