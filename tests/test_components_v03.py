from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lilies.core.components import ComponentAction, ComponentRegistry, validate_payload
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker, Risk
from lilies.core.pet_habitat import PetHabitatController
from lilies.core.productivity import (
    BoxWorldService,
    FocusService,
    GrowthEngine,
    ReadingSessionService,
    ReminderScheduler,
    TaskService,
    WardrobeService,
)
from lilies.core.v03_components import V03Services, register_v03_components


class _WindowCatalog:
    def list_windows(self):
        return []

    def activate(self, _handle: int) -> bool:
        return True


def _registry(tmp_path, *, calendar=None, slack=None, box_world_enter=None):
    database = Database(tmp_path / "lilies.db")
    growth = GrowthEngine(database)
    services = V03Services(
        tasks=TaskService(database, growth=growth),
        focus=FocusService(database, growth=growth),
        reading=ReadingSessionService(database, growth=growth),
        reminders=ReminderScheduler(database),
        growth=growth,
        wardrobe=WardrobeService(database),
        box_world=BoxWorldService(database),
        window_catalog=_WindowCatalog(),
        pet_habitat=PetHabitatController(stable_seconds=0),
        calendar=calendar,
        slack=slack,
        box_world_enter=box_world_enter,
    )
    return database, register_v03_components(
        ComponentRegistry(database, PermissionBroker(database)), services
    )


def test_v03_component_surface_has_no_grant_submit_or_shell(tmp_path) -> None:
    _database, registry = _registry(tmp_path)
    declared = {(item["componentId"], item["actionId"]) for item in registry.list()}

    assert {("tasks", "create"), ("focus", "start"), ("reading", "finish")} <= declared
    assert {("growth", "status"), ("growth", "history"), ("growth", "unlocks")} <= declared
    assert {("wardrobe", "equip"), ("box-world", "enter"), ("pet-habitat", "detach")} <= declared
    assert ("growth", "grant") not in declared
    assert ("unlocks", "grant") not in declared
    assert ("terminal", "run") not in declared
    assert not any(action in {"execute", "send", "submit"} for _component, action in declared)


def test_box_world_component_uses_ui_entry_callback_when_available(tmp_path) -> None:
    database = Database(tmp_path / "callback-state.db")
    world = BoxWorldService(database)
    calls: list[str] = []

    def enter_with_ui_navigation():
        calls.append("world")
        return world.enter()

    _database, registry = _registry(
        tmp_path / "registry",
        box_world_enter=enter_with_ui_navigation,
    )
    result = registry.invoke(
        "box-world", "enter", {}, origin="test", confirmed=True
    )

    assert calls == ["world"]
    assert result["result"]["entered"] is True


def test_task_component_is_bounded_idempotent_and_audits_result(tmp_path) -> None:
    database, registry = _registry(tmp_path)
    created = registry.invoke(
        "tasks",
        "create",
        {"title": "整理实验记录", "priority": 2, "category": "research"},
        origin="test",
        confirmed=True,
    )
    task_id = created["result"]["task_id"]
    registry.invoke("tasks", "complete", {"taskId": task_id}, confirmed=True)
    duplicate = registry.invoke("tasks", "complete", {"taskId": task_id}, confirmed=True)

    assert duplicate["result"]["alreadyCompleted"] is True
    with database.connect() as connection:
        row = connection.execute(
            "SELECT result_json,error_json,completed_at FROM audit_log WHERE audit_id=?",
            (created["auditId"],),
        ).fetchone()
    assert row is not None and task_id in row["result_json"]
    assert row["error_json"] is None and row["completed_at"]


def test_handler_errors_are_recorded_without_swallowing_exception(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))

    def fail(_payload):
        raise RuntimeError("bounded failure")

    registry.register(
        ComponentAction(
            "probe",
            "fail",
            "失败探针",
            "测试失败结果审计。",
            Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            fail,
        )
    )
    with pytest.raises(RuntimeError, match="bounded failure"):
        registry.invoke("probe", "fail", {}, origin="test")
    with database.connect() as connection:
        row = connection.execute(
            "SELECT error_json,completed_at FROM audit_log ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None and "RuntimeError" in row["error_json"] and row["completed_at"]


def test_recursive_parameter_validation_rejects_nested_unknowns_and_bad_dates() -> None:
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "required": ["at"],
                    "properties": {"at": {"type": "string", "format": "date-time"}},
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    }
    valid = datetime.now(UTC).isoformat()
    validate_payload(schema, {"items": [{"at": valid}]})
    with pytest.raises(ValueError, match="unknown parameter"):
        validate_payload(schema, {"items": [{"at": valid, "shell": "whoami"}]})
    with pytest.raises(ValueError, match="date-time"):
        validate_payload(schema, {"items": [{"at": "tomorrow-ish"}]})


def test_connector_proposal_tool_returns_and_audits_only_redacted_receipt(tmp_path) -> None:
    before_secret = "private old calendar title"
    after_secret = "private new calendar title"

    class Calendar:
        def propose_update(self, event_id, change):
            return {
                "id": "proposal-one",
                "connector": "calendar",
                "action": "update_event",
                "status": "pending",
                "before": {"summary": before_secret},
                "after": {"changes": dict(change)},
            }

        def status(self):
            return {}

        def calendars(self):
            return []

        def metadata_items(self, *, limit=30):
            return []

        def refresh(self):
            return {}

        def open_event(self, _event_id):
            return {}

        def propose_create(self, change):
            return self.propose_update("create", change)

    database, registry = _registry(tmp_path, calendar=Calendar())
    response = registry.invoke(
        "calendar",
        "propose-update",
        {"eventId": "event-one", "change": {"summary": after_secret}},
        origin="model",
        confirmed=True,
    )

    encoded_response = str(response)
    assert response["result"]["proposalId"] == "proposal-one"
    assert response["result"]["changedFields"] == ["summary"]
    assert before_secret not in encoded_response
    assert after_secret not in encoded_response
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json,result_json,error_json FROM audit_log WHERE audit_id=?",
            (response["auditId"],),
        ).fetchone()
    persisted = " ".join(str(value or "") for value in row)
    assert before_secret not in persisted
    assert after_secret not in persisted
    assert "summary" in persisted


def test_slack_proposal_audit_never_persists_reply_body(tmp_path) -> None:
    reply_secret = "reply body must stay out of the audit log"

    class Slack:
        def status(self):
            return {}

        def metadata_items(self, *, limit=30):
            return []

        def propose_reply(self, event_id, text):
            return {
                "id": "proposal-slack",
                "connector": "slack",
                "action": "send_message",
                "status": "pending",
                "after": {"text": text},
            }

    database, registry = _registry(tmp_path, slack=Slack())
    response = registry.invoke(
        "slack",
        "propose-reply",
        {"eventId": "event-slack", "text": reply_secret},
        origin="model",
        confirmed=True,
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json,result_json FROM audit_log WHERE audit_id=?",
            (response["auditId"],),
        ).fetchone()
    assert reply_secret not in str(response)
    assert reply_secret not in " ".join(str(value or "") for value in row)
