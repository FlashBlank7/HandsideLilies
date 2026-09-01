from __future__ import annotations

from lilies.core.components import (
    ComponentRegistry,
    V02ComponentBindings,
    register_v02_components,
)
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker


def test_v02_component_surface_and_recall_bounds(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    calls: list[tuple[str, object]] = []
    bindings = V02ComponentBindings(
        desktop_peek_status=lambda: {"active": False},
        desktop_peek_toggle=lambda: {"active": True},
        desktop_peek_restore=lambda: {"active": False},
        activity_status=lambda: {"enabled": True},
        activity_set_policy=lambda app, policy: calls.append((app, policy)) or {"ok": True},
        companion_preferences=lambda: {"frequency": "balanced"},
        companion_reply=lambda bubble, text: calls.append((bubble, text)) or {"queued": True},
        companion_another=lambda bubble: {"bubbleId": bubble},
        companion_snooze=lambda minutes: f"in-{minutes}",
        memory_partitions=lambda: [{"partition_id": "identity"}],
        memory_recall=lambda payload: {"count": payload["limit"]},
        memory_reindex=lambda: {"fragments": 0},
        memory_forget=lambda fragment, delete: {"fragment": fragment, "delete": delete},
        content_sources=lambda: [{"id": "arxiv"}],
        content_refresh=lambda provider, query, limit: {"provider": provider, "limit": limit},
    )
    registry = register_v02_components(
        ComponentRegistry(database, PermissionBroker(database)), bindings
    )

    declared = {(item["componentId"], item["actionId"]) for item in registry.list()}
    assert ("desktop-peek", "toggle") in declared
    assert ("activity-context", "set-policy") in declared
    assert ("companion", "another") in declared
    assert ("memory", "recall") in declared
    assert ("content", "refresh") in declared

    result = registry.invoke(
        "memory",
        "recall",
        {"partitionIds": [], "query": "名字", "timeRange": "all", "limit": 6},
        confirmed=True,
    )
    assert result["result"]["count"] == 6

    try:
        registry.invoke(
            "memory",
            "recall",
            {"partitionIds": [], "query": "名字", "timeRange": "all", "limit": 7},
            confirmed=True,
        )
    except ValueError as exc:
        assert "maximum" in str(exc)
    else:
        raise AssertionError("memory.recall accepted an out-of-range limit")
