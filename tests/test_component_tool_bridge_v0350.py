from __future__ import annotations

from typing import Any

import pytest

from lilies.core.component_tool_bridge import (
    CHAT_COMPONENT_TOOLS,
    ComponentToolBridge,
)
from lilies.core.components import ComponentAction, ComponentRegistry
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker, Risk


EXPECTED_BINDINGS = [
    ("tasks", "list"),
    ("tasks", "create"),
    ("tasks", "update"),
    ("tasks", "complete"),
    ("tasks", "reopen"),
    ("tasks", "archive"),
    ("focus", "status"),
    ("focus", "start"),
    ("focus", "pause"),
    ("focus", "resume"),
    ("focus", "finish"),
    ("focus", "cancel"),
    ("reading", "status"),
    ("reading", "start"),
    ("reading", "finish"),
    ("reminders", "list"),
    ("reminders", "create"),
    ("reminders", "snooze"),
    ("reminders", "dismiss"),
    ("growth", "status"),
    ("growth", "history"),
    ("growth", "unlocks"),
    ("wardrobe", "list"),
    ("wardrobe", "equip"),
    ("box-world", "status"),
    ("box-world", "inspect"),
    ("box-world", "enter"),
]

FORBIDDEN_BINDINGS = {
    ("calendar", "status"),
    ("calendar", "propose-update"),
    ("slack", "inbox"),
    ("slack", "propose-reply"),
    ("reminders", "delete"),
    ("shell", "exec"),
    ("growth", "grant"),
}

EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


@pytest.fixture
def registry(tmp_path) -> ComponentRegistry:
    database = Database(tmp_path / "bridge.db")
    return ComponentRegistry(database, PermissionBroker(database))


def _tool_name(component_id: str, action_id: str) -> str:
    return f"{component_id}__{action_id}".replace("-", "_")


def _list_schema(maximum: int = 200) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": maximum,
            }
        },
        "additionalProperties": False,
    }


def _register_action(
    registry: ComponentRegistry,
    component_id: str,
    action_id: str,
    *,
    risk: Risk = Risk.READ,
    schema: dict[str, Any] | None = None,
    handler=None,
) -> ComponentAction:
    action = ComponentAction(
        component_id=component_id,
        action_id=action_id,
        title=f"{component_id}.{action_id}",
        description="contract fixture",
        risk=risk,
        parameters=schema or dict(EMPTY_SCHEMA),
        handler=handler or (lambda payload: payload),
    )
    registry.register(action)
    return action


def _register_reviewed_surface(registry: ComponentRegistry) -> None:
    for component_id, action_id in EXPECTED_BINDINGS:
        schema = (
            _list_schema()
            if (component_id, action_id)
            in {
                ("tasks", "list"),
                ("reminders", "list"),
                ("growth", "history"),
            }
            else None
        )
        risk = (
            Risk.READ
            if action_id in {"list", "status", "history", "unlocks", "inspect"}
            else Risk.MUTATE
        )
        _register_action(
            registry,
            component_id,
            action_id,
            risk=risk,
            schema=schema,
        )


def test_dynamic_tool_spec_is_exact_bounded_namespace_surface(registry) -> None:
    assert [
        (binding.component_id, binding.action_id)
        for binding in CHAT_COMPONENT_TOOLS
    ] == EXPECTED_BINDINGS

    _register_reviewed_surface(registry)
    for component_id, action_id in FORBIDDEN_BINDINGS:
        _register_action(
            registry,
            component_id,
            action_id,
            risk=Risk.DESTRUCTIVE,
        )

    bridge = ComponentToolBridge(registry)
    spec = bridge.dynamic_tool_spec()

    assert spec is not None
    assert set(spec) == {"type", "name", "description", "tools"}
    assert spec["type"] == "namespace"
    assert spec["name"] == "box"
    assert isinstance(spec["description"], str) and spec["description"]
    tools = spec["tools"]
    expected_names = [
        _tool_name(component_id, action_id)
        for component_id, action_id in EXPECTED_BINDINGS
    ]
    assert bridge.function_count == len(tools) == len(expected_names) <= 32
    assert [tool["name"] for tool in tools] == expected_names
    assert len({tool["name"] for tool in tools}) == len(tools)
    assert all(
        set(tool) == {"type", "name", "description", "inputSchema"}
        and tool["type"] == "function"
        and isinstance(tool["description"], str)
        and tool["description"]
        and tool["inputSchema"]["type"] == "object"
        for tool in tools
    )
    exposed = set(expected_names)
    assert not {
        _tool_name(component_id, action_id)
        for component_id, action_id in FORBIDDEN_BINDINGS
    } & exposed
    for component_id, action_id in FORBIDDEN_BINDINGS:
        with pytest.raises(PermissionError):
            bridge.handle_dynamic_tool(
                _tool_name(component_id, action_id),
                {},
                {"namespace": "box"},
                invoke=lambda *_args: pytest.fail(
                    "excluded actions must never reach the invoker"
                ),
            )


def test_list_schemas_are_copied_narrowed_and_enforced(registry) -> None:
    tasks = _register_action(
        registry, "tasks", "list", schema=_list_schema(maximum=200)
    )
    _register_action(
        registry, "reminders", "list", schema=_list_schema(maximum=25)
    )
    _register_action(
        registry, "growth", "history", schema=_list_schema(maximum=200)
    )
    bridge = ComponentToolBridge(registry)

    spec = bridge.dynamic_tool_spec()
    assert spec is not None
    tools = {tool["name"]: tool for tool in spec["tools"]}
    assert tools["tasks__list"]["inputSchema"]["properties"]["limit"]["maximum"] == 40
    assert tools["reminders__list"]["inputSchema"]["properties"]["limit"]["maximum"] == 25
    assert tools["growth__history"]["inputSchema"]["properties"]["limit"]["maximum"] == 50
    assert tasks.parameters["properties"]["limit"]["maximum"] == 200

    # Callers cannot weaken a cached schema by mutating a published spec.
    tools["tasks__list"]["inputSchema"]["properties"]["limit"]["maximum"] = 999
    fresh_spec = bridge.dynamic_tool_spec()
    assert fresh_spec is not None
    fresh_tasks = next(
        tool for tool in fresh_spec["tools"] if tool["name"] == "tasks__list"
    )
    assert fresh_tasks["inputSchema"]["properties"]["limit"]["maximum"] == 40

    calls: list[tuple[str, str, dict[str, Any]]] = []

    def invoke(component_id: str, action_id: str, payload: dict[str, Any]):
        calls.append((component_id, action_id, payload))
        return {"accepted": True}

    with pytest.raises(ValueError, match="above maximum"):
        bridge.handle_dynamic_tool(
            "tasks__list",
            {"limit": 41},
            {"namespace": "box"},
            invoke=invoke,
        )
    assert calls == []
    assert bridge.handle_dynamic_tool(
        "tasks__list",
        {"limit": 40},
        {"namespace": "box"},
        invoke=invoke,
    ) == {"accepted": True}
    assert calls == [("tasks", "list", {"limit": 40})]


def test_dispatch_uses_exact_registered_binding_and_callback(registry) -> None:
    def must_not_run(_payload):
        raise AssertionError("the bridge must dispatch through its invoke callback")

    _register_action(
        registry,
        "tasks",
        "create",
        risk=Risk.MUTATE,
        schema={
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        handler=must_not_run,
    )
    bridge = ComponentToolBridge(registry)
    payload = {"title": "Read the paper"}
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def invoke(component_id: str, action_id: str, clean: dict[str, Any]):
        calls.append((component_id, action_id, dict(clean)))
        clean["title"] = "mutated callback copy"
        return "receipt"

    result = bridge.handle_dynamic_tool(
        "tasks__create",
        payload,
        {"namespace": "box"},
        invoke=invoke,
    )

    assert result == "receipt"
    assert calls == [("tasks", "create", {"title": "Read the paper"})]
    assert payload == {"title": "Read the paper"}


def test_rejects_wrong_namespace_unknown_tool_and_non_object_arguments(registry) -> None:
    _register_action(registry, "tasks", "list", schema=_list_schema())
    bridge = ComponentToolBridge(registry)

    def must_not_invoke(*_args):
        raise AssertionError("rejected calls must not reach the invoker")

    for context in (None, {}, {"namespace": "tasks"}, {"namespace": "Box"}):
        with pytest.raises(PermissionError):
            bridge.handle_dynamic_tool(
                "tasks__list", {}, context, invoke=must_not_invoke
            )

    with pytest.raises(PermissionError):
        bridge.handle_dynamic_tool(
            "tasks__delete",
            {},
            {"namespace": "box"},
            invoke=must_not_invoke,
        )

    for arguments in (None, [], "{}", 1):
        with pytest.raises(ValueError):
            bridge.handle_dynamic_tool(
                "tasks__list",
                arguments,  # type: ignore[arg-type]
                {"namespace": "box"},
                invoke=must_not_invoke,
            )


def test_missing_or_colliding_registry_actions_fail_closed(registry) -> None:
    empty_bridge = ComponentToolBridge(registry)
    assert empty_bridge.function_count == 0
    assert empty_bridge.dynamic_tool_spec() is None
    with pytest.raises(PermissionError):
        empty_bridge.handle_dynamic_tool(
            "tasks__list",
            {},
            {"namespace": "box"},
            invoke=lambda *_args: None,
        )

    # This unreviewed component normalizes to the same public name as
    # box-world.status.  Exact component/action matching must still reject it.
    _register_action(registry, "box_world", "status")
    collision_bridge = ComponentToolBridge(registry)
    assert collision_bridge.function_count == 0
    assert collision_bridge.dynamic_tool_spec() is None

    _register_action(registry, "tasks", "list", schema=_list_schema())
    partial_bridge = ComponentToolBridge(registry)
    spec = partial_bridge.dynamic_tool_spec()
    assert spec is not None
    assert [tool["name"] for tool in spec["tools"]] == ["tasks__list"]
    with pytest.raises(PermissionError):
        partial_bridge.handle_dynamic_tool(
            "focus__status",
            {},
            {"namespace": "box"},
            invoke=lambda *_args: None,
        )
