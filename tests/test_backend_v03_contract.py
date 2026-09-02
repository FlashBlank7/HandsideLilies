from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from PySide6.QtWidgets import QApplication

import lilies.backend as backend_module
from lilies.backend import Backend
from lilies.core.activity import ForegroundContext
from lilies.core.orchestration import PresenceState
from lilies.core.win_event import WinEvent, WinEventKind


@dataclass
class _Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **changes: int) -> None:
        self.current += timedelta(**changes)


def test_backend_exposes_v03_connector_and_focus_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        methods = {
            backend.metaObject().method(index).name().data().decode("utf-8")
            for index in range(backend.metaObject().methodCount())
        }
        assert {
            "connectorConfigure",
            "connectorBeginOAuth",
            "connectorConfirmProposal",
            "connectorRejectProposal",
            "connectorReplaceSlackProposal",
            "connectorSelectItem",
            "connectorAssist",
            "calendarProposeCreate",
            "calendarProposeUpdate",
            "slackProposeReply",
            "connectorClearContent",
            "connectorDisconnect",
            "focusDiversionAction",
            "focusCancel",
        } <= methods
        result = backend.connectorConfigure(
            "slack",
            {
                "clientId": "client",
                "currentUserId": "U-ME",
                "xappToken": "xapp-local",
                "redirectUri": "http://127.0.0.1:53682/oauth/callback",
                "selectedChannels": ["C-ONE"],
                "policy": {
                    "scope": "necessary",
                    "interruption": "quiet",
                    "retention": "metadata",
                    "assistance": "assist",
                },
            },
        )
        assert result["ok"] is True
        assert backend.slackStatus["configured"] is True
        assert backend.slackStatus["policyCanonical"]["assistance"] == "assist"
        assert "xapp-" not in backend.slackManifestText
        assert backend.connectorBeginOAuth("slack") is False
    finally:
        backend.shutdown()
        app.processEvents()


def test_workbench_defaults_tasks_to_daily_and_projects_only_pending_reminders(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._productivity_timer.stop()
        backend.tasksCreate({"title": "收拾桌面", "priority": "normal"})
        assert len(backend.taskItems) == 1
        assert backend.taskItems[0]["category"] == "daily"

        for index in range(2):
            backend.tasksCreate({"title": f"日常事项 {index}", "category": "daily"})
        for task in list(backend.taskItems):
            backend.tasksComplete(str(task["id"]))

        unlocks = {value["item_key"] for value in backend.growthStatus["unlocks"]}
        assert backend.growthStatus["points"] == 30
        assert {"outfit:home-cardigan", "world:living-corner"} <= unlocks

        pending = backend.reminders.create(
            "待处理", datetime.now(UTC) + timedelta(hours=1)
        )
        dismissed = backend.reminders.create(
            "已结束", datetime.now(UTC) + timedelta(hours=2)
        )
        backend.reminders.dismiss(str(dismissed["reminder_id"]))

        projected = backend.reminderItems
        assert [value["id"] for value in projected] == [pending["reminder_id"]]
        assert all(value["state"] == "pending" for value in projected)
    finally:
        backend.shutdown()
        app.processEvents()


def test_slack_open_inbox_rejects_disconnected_and_emits_real_panel_anchor(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    anchors: list[str] = []
    backend.workPanelAnchorRequested.connect(anchors.append)

    class _SlackState:
        def __init__(self, connected: bool) -> None:
            self.connected = connected

        def status(self):
            return {"connected": self.connected}

        def close(self) -> None:
            pass

    try:
        backend.slack_connector = _SlackState(False)
        assert backend.slackOpenInbox() is False
        assert backend.workPanelOpen is False
        assert anchors == []

        backend.slack_connector = _SlackState(True)
        assert backend.slackOpenInbox() is True
        assert backend.workPanelOpen is True
        assert backend.workPanelSection == "connectors"
        assert anchors == ["slack-inbox"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_replay_intro_from_compact_opens_visible_surface_and_closes_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        assert backend.shellMode == "compact"
        backend.setChatOpen(True)

        backend.replayIntro()

        assert backend.shellMode == "visual"
        assert backend.chatOpen is False
        assert backend.introActive is True
    finally:
        backend.shutdown()
        app.processEvents()


def test_activate_window_returns_failure_and_publishes_visible_reason(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        monkeypatch.setattr(
            backend.window_catalog,
            "refresh",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(backend.window_catalog, "activate", lambda _handle: False)
        assert backend.activateWindow(12345) is False
        assert "无法切换" in backend.status

        monkeypatch.setattr(backend.window_catalog, "activate", lambda _handle: True)
        assert backend.activateWindow(12345) is True
        assert backend.status == "已切换窗口"
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_dock_launch_library_includes_pinned_and_unpinned_items(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend.database.upsert_desktop_item(
            {
                "name": "Pinned Papers",
                "path": str(tmp_path / "papers"),
                "source": "custom",
                "kind": "folder",
            }
        )
        backend.database.upsert_desktop_item(
            {
                "name": "Research Report",
                "path": str(tmp_path / "report.pdf"),
                "source": "desktop",
                "kind": "file",
            }
        )
        values = backend.database.desktop_items()
        pinned_id = next(
            value["item_id"] for value in values if value["name"] == "Pinned Papers"
        )
        backend.database.update_desktop_layout(pinned_id, pinned=True)

        launch_items = backend.dockLaunchItems
        by_name = {value["name"]: value for value in launch_items}
        assert {"Pinned Papers", "Research Report"} <= set(by_name)
        assert by_name["Pinned Papers"]["pinned"] is True
        assert by_name["Research Report"]["pinned"] is False
        assert launch_items.index(by_name["Pinned Papers"]) < launch_items.index(
            by_name["Research Report"]
        )

        invocations: list[tuple[str, str, dict, bool]] = []
        monkeypatch.setattr(
            backend.registry,
            "invoke",
            lambda component, action, arguments, confirmed=False: invocations.append(
                (component, action, dict(arguments), bool(confirmed))
            ),
        )
        assert backend.openItem(pinned_id) is True
        assert invocations == [
            (
                "desktop-icons",
                "open",
                {"path": str(tmp_path / "papers")},
                True,
            )
        ]
        assert backend.openItem("missing-item") is False

        def reject_open(*_args, **_kwargs):
            raise OSError("blocked")

        monkeypatch.setattr(backend.registry, "invoke", reject_open)
        assert backend.openItem(pinned_id) is False
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_system_pet_drag_is_default_and_explicit_direct_mode_persists(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        assert backend.petDragMode == "system"

        backend.setPetDragMode("direct")
        assert backend.petDragMode == "direct"
        assert backend.database.get_setting("pet_drag_mode", "") == "direct"

        backend.setPetDragMode("not-a-mode")
        assert backend.petDragMode == "direct"
    finally:
        backend.shutdown()
        app.processEvents()

    restored = Backend(smoke=True, force_compact=True)
    try:
        assert restored.petDragMode == "direct"
    finally:
        restored.shutdown()
        app.processEvents()


def test_backend_invalid_persisted_drag_mode_repairs_to_system_default(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend.database.set_setting("pet_drag_mode", "invalid-old-value")
    finally:
        backend.shutdown()
        app.processEvents()

    restored = Backend(smoke=True, force_compact=True)
    try:
        assert restored.petDragMode == "system"
    finally:
        restored.shutdown()
        app.processEvents()


def test_backend_focus_lifecycle_keeps_pause_resume_and_end_reasons_distinct(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 2, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(5)
        assert backend.focusStatus["state"] == "running"
        assert backend.focusTransition["kind"] == "started"
        started_sequence = backend.focusTransition["sequence"]

        clock.advance(seconds=73)
        backend.focusPause()
        assert backend.focusStatus["state"] == "paused"
        assert backend.focusStatus["elapsedSeconds"] == 73
        assert backend.focusTransition["kind"] == "paused"

        clock.advance(seconds=90)
        assert backend.focusStatus["elapsedSeconds"] == 73
        backend.focusResume()
        assert backend.focusStatus["state"] == "running"
        assert backend.focusTransition["kind"] == "resumed"

        clock.advance(seconds=7)
        backend.focusFinish()
        assert backend.focusStatus["active"] is False
        assert backend.focusTransition["kind"] == "finished"
        assert backend.focusTransition["elapsedSeconds"] == 80
        assert backend.focusTransition["durationSeconds"] == 300
        assert backend.focusTransition["sequence"] == started_sequence + 3

        backend.focusStart(5)
        clock.advance(seconds=40)
        backend.focusCancel()
        assert backend.focusStatus["active"] is False
        assert backend.focusTransition["kind"] == "cancelled"
        assert backend.focusTransition["elapsedSeconds"] == 40
        assert backend.focusTransition["durationSeconds"] == 300
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_focus_deadline_finishes_once_and_emits_completed_transition(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 3, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(5)
        session_id = backend.focusStatus["sessionId"]
        clock.advance(minutes=5)

        backend.refreshProductivity()

        assert backend.focusStatus["active"] is False
        assert backend.focus.status(session_id)["state"] == "finished"
        assert backend.focusTransition["kind"] == "completed"
        assert backend.focusTransition["sessionId"] == session_id
        assert backend.focusTransition["elapsedSeconds"] == 300
        completed_sequence = backend.focusTransition["sequence"]

        backend.refreshProductivity()
        assert backend.focusTransition["sequence"] == completed_sequence
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_focus_long_timer_gap_caps_completion_and_rewards(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 4, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(5)
        session_id = backend.focusStatus["sessionId"]
        clock.advance(hours=1)

        backend.refreshProductivity()

        finished = backend.focus.status(session_id)
        assert finished is not None
        assert finished["state"] == "finished"
        assert finished["active_seconds"] == 300
        assert backend.focusTransition["kind"] == "completed"
        assert backend.focusTransition["elapsedSeconds"] == 300
        assert backend.focusTransition["durationSeconds"] == 300
        assert not [
            value for value in backend.growth.history()
            if value["event_kind"] == "focus-unit"
        ]
        completed_sequence = backend.focusTransition["sequence"]
        history = backend.growth.history()

        backend.refreshProductivity()
        assert backend.focusTransition["sequence"] == completed_sequence
        assert backend.growth.history() == history
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_due_focus_wins_manual_finish_race_once(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 5, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(25)
        session_id = backend.focusStatus["sessionId"]
        clock.advance(minutes=25)

        backend.focusFinish()

        finished = backend.focus.status(session_id)
        assert finished is not None
        assert finished["active_seconds"] == 1500
        assert backend.focusTransition["kind"] == "completed"
        completed_sequence = backend.focusTransition["sequence"]
        history = backend.growth.history()
        units = [value for value in history if value["event_kind"] == "focus-unit"]
        assert len(units) == 1
        assert units[0]["points"] == 8

        backend.focusFinish()
        assert backend.focusTransition["sequence"] == completed_sequence
        assert backend.growth.history() == history
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_due_focus_wins_cancel_race_once(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 5, 30, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(25)
        session_id = backend.focusStatus["sessionId"]
        clock.advance(minutes=25)

        backend.focusCancel()

        finished = backend.focus.status(session_id)
        assert finished is not None
        assert finished["state"] == "finished"
        assert finished["active_seconds"] == 1500
        assert backend.focusTransition["kind"] == "completed"
        assert backend.focusTransition["elapsedSeconds"] == 1500
        completed_sequence = backend.focusTransition["sequence"]
        history = backend.growth.history()
        units = [value for value in history if value["event_kind"] == "focus-unit"]
        assert len(units) == 1
        assert units[0]["points"] == 8

        backend.focusCancel()
        assert backend.focusTransition["sequence"] == completed_sequence
        assert backend.growth.history() == history
    finally:
        backend.shutdown()
        app.processEvents()


def test_focus_diversion_rejects_every_stale_or_unscoped_action(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 5, 45, tzinfo=UTC))
    backend.focus.now = clock
    activated: list[int] = []
    monkeypatch.setattr(
        backend.window_catalog,
        "activate",
        lambda handle: activated.append(int(handle)) or True,
    )
    backend._last_work_window_handle = 4242
    try:
        backend.focusStart(5)
        old_session_id = backend.focusStatus["sessionId"]
        clock.advance(seconds=30)
        backend.focusFinish()
        backend.focusStart(5)
        current_session_id = backend.focusStatus["sessionId"]
        transition = backend.focusTransition
        history = backend.growth.history()

        for action in ("return", "rest", "finish", "dismiss"):
            backend._focus_diversion_bubble = {
                "visible": True,
                "sessionId": old_session_id,
            }
            backend.focusDiversionAction(action, old_session_id)
            assert backend.focusDiversion == {}
            assert backend.focusStatus["sessionId"] == current_session_id
            assert backend.focusStatus["state"] == "running"
            assert backend.focusTransition == transition
            assert backend.growth.history() == history

        backend._focus_diversion_bubble = {
            "visible": True,
            "sessionId": current_session_id,
        }
        backend.focusDiversionAction("finish")
        assert backend.focusDiversion["sessionId"] == current_session_id
        assert backend.focusStatus["state"] == "running"
        assert backend.focusTransition == transition
        assert activated == []
    finally:
        backend.shutdown()
        app.processEvents()


def test_due_focus_wins_diversion_rest_and_finish_once(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 6, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        expected_units = 0
        for action in ("rest", "finish"):
            backend.focusStart(25)
            session_id = backend.focusStatus["sessionId"]
            backend._focus_diversion_bubble = {
                "visible": True,
                "sessionId": session_id,
            }
            clock.advance(minutes=25)

            backend.focusDiversionAction(action, session_id)

            finished = backend.focus.status(session_id)
            assert finished is not None
            assert finished["state"] == "finished"
            assert finished["outcome"] == "focused"
            assert finished["active_seconds"] == 1500
            assert backend.focusTransition["kind"] == "completed"
            assert backend.focusTransition["sessionId"] == session_id
            assert backend.focusDiversion == {}
            expected_units += 1
            history = backend.growth.history()
            assert len([
                value for value in history if value["event_kind"] == "focus-unit"
            ]) == expected_units
            completed_sequence = backend.focusTransition["sequence"]

            backend.focusDiversionAction(action, session_id)
            assert backend.focusTransition["sequence"] == completed_sequence
            assert backend.growth.history() == history
    finally:
        backend.shutdown()
        app.processEvents()


def test_current_focus_diversion_actions_keep_their_normal_semantics(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 7, 0, tzinfo=UTC))
    backend.focus.now = clock
    activated: list[int] = []
    monkeypatch.setattr(
        backend.window_catalog,
        "activate",
        lambda handle: activated.append(int(handle)) or True,
    )
    backend._last_work_window_handle = 616
    try:
        backend.focusStart(5)
        rest_session_id = backend.focusStatus["sessionId"]

        for action in ("return", "dismiss"):
            backend._focus_diversion_bubble = {
                "visible": True,
                "sessionId": rest_session_id,
            }
            backend.focusDiversionAction(action, rest_session_id)
            assert backend.focusDiversion == {}
            assert backend.focusStatus["sessionId"] == rest_session_id
            assert backend.focusStatus["state"] == "running"

        assert activated == [616]
        clock.advance(seconds=60)
        backend._focus_diversion_bubble = {
            "visible": True,
            "sessionId": rest_session_id,
        }
        backend.focusDiversionAction("rest", rest_session_id)
        rested = backend.focus.status(rest_session_id)
        assert rested is not None
        assert rested["state"] == "finished"
        assert rested["outcome"] == "rest"
        assert backend.focusTransition["kind"] == "finished"
        assert backend.focusDiversion == {}

        backend.focusStart(5)
        finish_session_id = backend.focusStatus["sessionId"]
        clock.advance(seconds=60)
        backend._focus_diversion_bubble = {
            "visible": True,
            "sessionId": finish_session_id,
        }
        backend.focusDiversionAction("finish", finish_session_id)
        finished = backend.focus.status(finish_session_id)
        assert finished is not None
        assert finished["state"] == "finished"
        assert finished["outcome"] == "focused"
        assert backend.focusTransition["kind"] == "finished"
        assert backend.focusDiversion == {}
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_component_finish_keeps_completed_edge_idempotent(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    clock = _Clock(datetime(2026, 8, 29, 6, 0, tzinfo=UTC))
    backend.focus.now = clock
    try:
        backend.focusStart(25)
        session_id = backend.focusStatus["sessionId"]
        clock.advance(minutes=30)
        result = backend.focus.finish(session_id)

        backend._on_component_invoked("focus", "finish", result)

        assert backend.focusTransition["kind"] == "completed"
        assert backend.focusTransition["elapsedSeconds"] == 1500
        completed_sequence = backend.focusTransition["sequence"]
        history = backend.growth.history()
        assert len([
            value for value in history if value["event_kind"] == "focus-unit"
        ]) == 1

        duplicate = backend.focus.finish(session_id)
        assert duplicate["alreadyFinished"] is True
        backend._on_component_invoked("focus", "finish", duplicate)

        assert backend.focusTransition["kind"] == "completed"
        assert backend.focusTransition["sequence"] == completed_sequence
        assert backend.growth.history() == history
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_write_entrypoints_only_create_preview_for_selected_item(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend.connectorConfigure(
            "calendar",
            {
                "clientId": "desktop-client.apps.googleusercontent.com",
                "policy": {
                    "scope": "necessary",
                    "interruption": "quiet",
                    "retention": "extended-cache",
                    "assistance": "confirm-execute",
                },
            },
        )
        backend.calendar_connector._store_item(
            remote_id="event-1",
            source_id="primary",
            occurred_at="2026-08-30T10:00:00+09:00",
            metadata={"etag": "etag-1"},
            content={"summary": "Old title"},
        )
        assert backend.connectorSelectItem("calendar", "event-1") is True
        preview = backend.calendarProposeUpdate(
            "event-1", {"title": "New title"}
        )
        assert preview["ok"] is True
        assert preview["proposal"]["before"] == {"summary": "Old title"}
        assert preview["proposal"]["after"]["changes"] == {"summary": "New title"}
        assert backend.connectorProposal["status"] == "pending"
        assert backend.connectorRejectProposal(
            "calendar", preview["proposal"]["id"]
        ) is True
        assert backend.connectorProposal == {}
    finally:
        backend.shutdown()
        app.processEvents()


def test_quick_function_library_keeps_three_core_and_caps_user_choices(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        assert "companion" in {
            value["action"] for value in backend.functionCatalog
        }
        assert "lilies-desktop" in {
            value["action"] for value in backend.functionCatalog
        }
        assert [value["action"] for value in backend.quickActions] == [
            "chat", "world", "settings"
        ]
        for action in ("focus", "letters", "peek"):
            backend.setQuickActionPinned(action, True)
        assert [value["action"] for value in backend.quickActions] == [
            "chat", "world", "settings", "focus", "letters", "peek"
        ]
        backend.setQuickActionPinned("reading", True)
        assert "reading" not in [value["action"] for value in backend.quickActions]
        backend.setQuickActionPinned("letters", False)
        backend.setQuickActionPinned("reading", True)
        assert [value["action"] for value in backend.quickActions[-3:]] == [
            "focus", "peek", "reading"
        ]
        backend.moveQuickAction("reading", -1)
        assert [value["action"] for value in backend.quickActions[-3:]] == [
            "focus", "reading", "peek"
        ]
        backend.clearQuickActions()
        assert len(backend.quickActions) == 3

        shell_mode = backend.shellMode
        navigation_requests: list[str] = []
        anchor_requests: list[str] = []
        presentation_requests: list[bool] = []
        backend.workPanelNavigationRequested.connect(navigation_requests.append)
        backend.workPanelAnchorRequested.connect(anchor_requests.append)
        backend.boxWorldPresentationRequested.connect(
            lambda: presentation_requests.append(True)
        )
        backend.enterBoxWorld()
        assert backend.boxWorldStatus["entered"] is True
        assert backend.boxWorldSceneOpen is True
        assert backend.workPanelOpen is False
        assert navigation_requests == []
        assert presentation_requests == []
        assert backend.shellMode == shell_mode
        # Re-entering a still-open but minimized scene is a presentation
        # request even though the logical open property remains true.
        backend.enterBoxWorld()
        assert presentation_requests == [True]
        backend.setBoxWorldSceneOpen(False)
        assert backend.boxWorldSceneOpen is False
        backend.openWorkPanelSection("focus")
        assert backend.workPanelOpen is True
        assert backend.workPanelSection == "work"
        assert navigation_requests[-1] == "work"
        assert anchor_requests[-1] == "focus"
        backend.openWorkPanelSection("reading")
        assert backend.workPanelSection == "work"
        assert navigation_requests[-1] == "work"
        assert anchor_requests[-1] == "reading"
        backend.openWorkPanelSection("wardrobe")
        assert backend.workPanelOpen is True
        assert backend.workPanelSection == "growth"
        assert navigation_requests[-1] == "growth"
        assert anchor_requests[-1] == "wardrobe"
        backend.setWorkPanelOpen(False)
        backend.openWorkPanelSection("box-world")
        assert backend.workPanelSection == "world"
        assert navigation_requests[-1] == "world"
        backend.setWorkPanelOpen(False)

        component_result = backend.registry.invoke(
            "box-world", "enter", {}, origin="test", confirmed=True
        )
        assert component_result["result"]["entered"] is True
        # Same-thread function-library entry is committed synchronously; only
        # model/socket worker calls need the queued bridge.
        assert backend.boxWorldSceneOpen is True
        assert backend.workPanelOpen is False
        backend.setBoxWorldSceneOpen(False)

        assert backend.shellMode == "compact"
        assert backend.toggleDesktopShell() == "visual"
        assert backend.shellMode == "visual"
        assert backend.toggleDesktopShell() == "compact"
        assert backend.shellMode == "compact"
    finally:
        backend.shutdown()
        app.processEvents()


def test_compact_pet_layout_uses_one_size_contract_and_reports_work_area(
    tmp_path, monkeypatch
):
    """QML and habitat geometry must persist the exact same compact size.

    A 200% scaled 1080p display exposes only about 540 logical pixels.  The
    compact pet therefore needs to accept the 110px safety minimum instead of
    silently jumping back to the old backend-only minimum on restart.
    """

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        # Zero is a legitimate coordinate on the primary display, while a
        # negative origin is normal for a monitor placed left/above it.  Keep
        # both values intact instead of treating either as a missing layout.
        backend.saveBoxLayout(0, 0, 184)
        assert backend.boxLayout() == {"x": 0.0, "y": 0.0, "size": 184.0}

        backend.saveBoxLayout(-840, 24, 20)
        assert backend.boxLayout() == {"x": -840.0, "y": 24.0, "size": 110.0}
        assert backend.pet_habitat.pet_width == 385.0
        assert backend.pet_habitat.pet_height == 363.0

        backend.saveBoxLayout(118, 72, 999)
        assert backend.boxLayout() == {"x": 118.0, "y": 72.0, "size": 320.0}
        assert backend.pet_habitat.pet_width == 1120.0
        assert backend.pet_habitat.pet_height == 1056.0

        # An unusually small logical work area may require a temporary render
        # size below the normal safety floor.  That effective size must reach
        # habitat geometry without weakening the persisted 110px contract.
        backend.setCompactPetEffectiveSize(72)
        assert backend.pet_habitat.pet_width == 252.0
        assert backend.pet_habitat.pet_height == 237.6
        assert backend.boxLayout() == {"x": 118.0, "y": 72.0, "size": 320.0}

        backend.saveBoxLayout(-320, -120, 72)
        assert backend.boxLayout() == {"x": -320.0, "y": -120.0, "size": 110.0}
        assert backend.pet_habitat.pet_width == 385.0
        assert backend.pet_habitat.pet_height == 363.0

        work_area = backend.screenWorkAreaAt(0, 0)
        assert {
            "left",
            "top",
            "right",
            "bottom",
            "width",
            "height",
            "name",
            "devicePixelRatio",
        } <= work_area.keys()
        assert work_area["width"] > 0
        assert work_area["height"] > 0
        assert work_area["right"] == work_area["left"] + work_area["width"]
        assert work_area["bottom"] == work_area["top"] + work_area["height"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_pet_interaction_locks_are_aggregated_by_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._pet_cursor_sample = (10.0, 20.0, 30.0)
        backend.setPetInteractionLock("drag", True)
        backend.setPetInteractionLock("menu", True)
        backend.setPetInteractionLock("drag", True)

        assert backend._pet_interaction_locked is True
        assert backend._pet_interaction_lock_reasons == {"drag", "menu"}
        assert backend._pet_cursor_sample is None
        assert backend.selection._interaction_suspended is True

        # Releasing one overlapping interaction must not enable avoidance
        # while another owner is still active.
        backend.setPetInteractionLock("drag", False)
        assert backend._pet_interaction_locked is True
        assert backend._pet_interaction_lock_reasons == {"menu"}

        # The compatibility slot owns only its legacy reason; it cannot clear
        # a lock held by a newer named caller.
        backend.setPetInteractionLocked(True)
        assert backend._pet_interaction_lock_reasons == {"legacy", "menu"}
        backend.setPetInteractionLocked(False)
        assert backend._pet_interaction_locked is True
        assert backend._pet_interaction_lock_reasons == {"menu"}

        backend.setPetInteractionLock("menu", False)
        assert backend._pet_interaction_locked is False
        assert backend._pet_interaction_lock_reasons == set()
        assert backend._pet_interaction_grace_until > 0.0
        assert backend.selection._interaction_suspended is False

        backend.setPetInteractionLock("resize", True)
        backend.setPetInteractionLock("accessory", True)
        backend.clearPetInteractionLocks()
        assert backend._pet_interaction_locked is False
        assert backend._pet_interaction_lock_reasons == set()
    finally:
        backend.shutdown()
        app.processEvents()


def test_emergency_restore_settles_in_compact_before_shell_monitor_runs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    restored: list[str] = []
    monitored_modes: list[str] = []
    shell_mode_changes: list[str] = []
    try:
        backend.setShellMode("visual")
        backend.shellModeChanged.connect(
            lambda: shell_mode_changes.append(backend.shellMode)
        )
        monkeypatch.setattr(
            backend.shell, "emergency_restore", lambda: restored.append("windows")
        )

        def monitor_without_touching_windows() -> bool:
            monitored_modes.append(backend.shell.mode)
            return False

        monkeypatch.setattr(
            backend.shell, "maintain_explorer", monitor_without_touching_windows
        )

        backend.emergencyRestore()
        backend._monitor_shell()

        assert restored == ["windows"]
        assert backend.shellMode == "compact"
        assert backend.database.get_setting("shell_mode") == "compact"
        assert shell_mode_changes == ["compact"]
        assert monitored_modes == ["compact"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_component_shell_switch_renews_selection_monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    monitor_renewals: list[str] = []
    shell_mode_changes: list[str] = []
    try:
        monkeypatch.setattr(
            backend.selection,
            "ensure_monitor",
            lambda: monitor_renewals.append(backend.shellMode),
        )
        backend.shellModeChanged.connect(
            lambda: shell_mode_changes.append(backend.shellMode)
        )

        backend.shell.mode = "visual"
        backend._on_component_invoked("shell-mode", "switch", {"mode": "visual"})

        assert monitor_renewals == ["visual"]
        assert shell_mode_changes == ["visual"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_explorer_restart_re_presents_the_selected_desktop_surface(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    presentations: list[str] = []
    try:
        monkeypatch.setattr(backend.shell, "maintain_explorer", lambda: True)
        backend.applicationActivationRequested.connect(presentations.append)

        backend._monitor_shell()

        assert backend.shellMode == "visual"
        assert presentations == ["show"]
        assert "Explorer" in backend.status
    finally:
        backend.shutdown()
        app.processEvents()


def test_native_non_game_full_screen_keeps_companion_but_hides_dock_overlays(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.window_catalog,
            "pump",
            lambda *_args, **_kwargs: False,
        )
        backend.selection._bubble = {
            "visible": True,
            "text": "temporary selection",
            "busy": False,
        }
        backend._selection_bubble = dict(backend.selection._bubble)
        backend._focus_diversion_bubble = {
            "visible": True,
            "text": "temporary focus reminder",
        }

        backend.pet_habitat.update_foreground(
            {
                "handle": 9001,
                "visible": True,
                "minimized": False,
                "fullScreen": True,
                "rect": [0, 0, 1920, 1080],
                "workArea": [0, 0, 1920, 1040],
            }
        )
        backend._pump_v03()

        assert backend.habitatState["state"] == "silent"
        assert backend.habitatState["reason"] == "full-screen"
        assert backend.dockSuppressed is True
        assert backend.companionSuppressed is False
        assert backend.companion.presentationSuppressed is False
        assert backend.companion.deliveryStatus["state"] != "suppressed"
        assert backend.selectionBubble["visible"] is False
        assert backend.focusDiversion == {}

        backend.companion._bubble = {
            "id": "late-companion",
            "visible": True,
            "summary": "late result",
        }
        backend.selection._bubble = {
            "visible": True,
            "text": "late selection result",
            "busy": False,
        }
        backend._selection_bubble = dict(backend.selection._bubble)
        backend._focus_diversion_bubble = {
            "visible": True,
            "text": "late focus reminder",
        }
        backend._pump_v03()

        assert backend.companion.bubble["id"] == "late-companion"
        assert backend.selectionBubble["visible"] is False
        assert backend.focusDiversion == {}

        backend.pet_habitat.update_foreground(
            {
                "handle": 9001,
                "visible": True,
                "minimized": False,
                "fullScreen": False,
                "rect": [100, 100, 1400, 900],
                "workArea": [0, 0, 1920, 1040],
            }
        )
        backend._pump_v03()
        assert backend.dockSuppressed is False
        assert backend.companion.bubble["id"] == "late-companion"
        assert backend.selectionBubble["visible"] is False
        assert backend.focusDiversion == {}
    finally:
        backend.shutdown()
        app.processEvents()


def test_foreground_privacy_event_publishes_suppression_without_timer_delay(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.companion,
            "reader",
            lambda hwnd: ForegroundContext(
                hwnd,
                process_id=77,
                process_name="game.exe",
                window_class="UnityWndClass",
                full_screen=True,
                is_game=True,
            ),
        )

        backend._on_unified_win_event(
            WinEvent(WinEventKind.FOREGROUND, 9002, 0)
        )

        assert backend.habitatState["state"] == "silent"
        assert backend.habitatState["reason"] == "full-screen"
        assert backend.habitatState["visible"] is False
        assert backend.dockSuppressed is True
        assert backend.input_pulse.snapshot()["suppressed"] is True
    finally:
        backend.shutdown()
        app.processEvents()


def test_unified_win_event_queue_reaches_companion_on_the_qt_pump_thread(
    tmp_path, monkeypatch
):
    """Exercise the real hub subscription path without native window reads."""

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.companion,
            "reader",
            lambda hwnd: ForegroundContext(
                hwnd,
                process_id=78,
                process_name="wps.exe",
                window_class="KingsoftWriter",
                title="Paper",
                scene_label="论文阅读",
            ),
        )

        assert backend.win_event_hub.publish(
            WinEvent(WinEventKind.FOREGROUND, 9003, 0)
        )
        assert backend.companion.activity.current_context is None
        assert backend.win_event_hub.dispatch_pending() == 1

        context = backend.companion.activity.current_context
        assert context is not None
        assert context.hwnd == 9003
        assert context.process_name == "wps.exe"
        assert backend._presence_context_handle == 9003
        assert backend.presence.snapshot().state is PresenceState.NORMAL
    finally:
        backend.shutdown()
        app.processEvents()


def test_same_window_leaving_full_screen_clears_silent_presence(
    tmp_path, monkeypatch
):
    """WindowCatalog must close the gap left by foreground-only WinEvents."""

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    current = {
        "context": ForegroundContext(
            9101,
            process_id=81,
            process_name="game.exe",
            window_class="UnityWndClass",
            full_screen=True,
            is_game=True,
        )
    }
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.companion, "reader", lambda _hwnd: current["context"]
        )
        backend._on_unified_win_event(
            WinEvent(WinEventKind.FOREGROUND, 9101, 0)
        )
        assert backend.dockSuppressed is True
        assert backend.presence.snapshot().signals.fullscreen_game is True

        current["context"] = ForegroundContext(
            9101,
            process_id=81,
            process_name="game.exe",
            window_class="UnityWndClass",
            full_screen=False,
            is_game=True,
        )
        monkeypatch.setattr(
            backend.window_catalog,
            "list_windows",
            lambda: [
                {
                    "handle": 9101,
                    "active": True,
                    "visible": True,
                    "minimized": False,
                    "fullScreen": False,
                    "rect": [120, 80, 1420, 900],
                    "workArea": [0, 0, 1920, 1040],
                }
            ],
        )
        backend._on_window_catalog([])

        assert backend.presence.snapshot().state is PresenceState.NORMAL
        assert backend.presence.snapshot().signals.fullscreen_game is False
        assert backend.habitatState["visible"] is True
        assert backend.dockSuppressed is False
    finally:
        backend.shutdown()
        app.processEvents()


def test_catalog_full_screen_entry_fails_closed_during_transient_reader_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.companion,
            "reader",
            lambda _hwnd: ForegroundContext(
                9151,
                process_id=86,
                process_name="game.exe",
                window_class="UnityWndClass",
                full_screen=False,
                is_game=True,
            ),
        )
        backend._on_unified_win_event(
            WinEvent(WinEventKind.FOREGROUND, 9151, 0)
        )
        assert backend.presence.snapshot().state is PresenceState.NORMAL

        def transient_reader(_hwnd: int) -> ForegroundContext:
            raise RuntimeError("transient User32 read")

        monkeypatch.setattr(backend.companion, "reader", transient_reader)
        monkeypatch.setattr(
            backend.window_catalog,
            "list_windows",
            lambda: [
                {
                    "handle": 9151,
                    "active": True,
                    "visible": True,
                    "minimized": False,
                    "fullScreen": True,
                    "rect": [0, 0, 1920, 1080],
                    "workArea": [0, 0, 1920, 1040],
                }
            ],
        )
        backend._on_window_catalog([])

        assert backend._presence_retry_handle == 9151
        assert backend.presence.snapshot().state is PresenceState.SILENT
        assert backend.presence.snapshot().signals.fullscreen_game is True
        assert backend.habitatState["state"] == "silent"
        assert backend.habitatState["visible"] is False
        assert backend.dockSuppressed is True
        assert backend.input_pulse.snapshot()["suppressed"] is True
    finally:
        backend.shutdown()
        app.processEvents()


def test_catalog_retries_transient_foreground_reader_failure_fail_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._v03_timer.stop()
        monkeypatch.setattr(
            backend.companion,
            "reader",
            lambda _hwnd: ForegroundContext(
                9201,
                process_id=91,
                process_name="1password.exe",
                title="Vault",
            ),
        )
        backend._on_unified_win_event(
            WinEvent(WinEventKind.FOREGROUND, 9201, 0)
        )
        assert backend.presence.snapshot().state is PresenceState.BLOCKED

        attempts = {"count": 0}

        def transient_reader(_hwnd: int) -> ForegroundContext:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("transient User32 read")
            return ForegroundContext(
                9202,
                process_id=92,
                process_name="writer.exe",
                title="Paper",
            )

        monkeypatch.setattr(backend.companion, "reader", transient_reader)
        backend._on_unified_win_event(
            WinEvent(WinEventKind.FOREGROUND, 9202, 0)
        )
        # An unreadable replacement window must not immediately expose a
        # previously private context.
        assert backend.presence.snapshot().state is PresenceState.BLOCKED
        assert backend._presence_retry_handle == 9202

        monkeypatch.setattr(
            backend.window_catalog,
            "list_windows",
            lambda: [
                {
                    "handle": 9202,
                    "active": True,
                    "visible": True,
                    "minimized": False,
                    "fullScreen": False,
                    "rect": [100, 100, 1300, 850],
                    "workArea": [0, 0, 1920, 1040],
                }
            ],
        )
        backend._on_window_catalog([])

        assert attempts["count"] == 2
        assert backend._presence_retry_handle == 0
        assert backend.presence.snapshot().state is PresenceState.NORMAL
        assert backend.dockSuppressed is False
    finally:
        backend.shutdown()
        app.processEvents()


def test_compact_mode_clears_stale_scene_occlusion_without_native_reads(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    native_reads: list[int] = []
    try:
        monkeypatch.setattr(
            backend_module,
            "window_fully_occluded",
            lambda handle: native_reads.append(int(handle)) or True,
        )
        backend._scene_active = False
        changes: list[bool] = []
        backend.sceneActiveChanged.connect(lambda: changes.append(backend.sceneActive))

        backend.setShellMode("compact")

        assert backend.sceneActive is True
        assert changes == [True]
        assert native_reads == []
        assert backend.window_catalog.status()["available"] is False
    finally:
        backend.shutdown()
        app.processEvents()
