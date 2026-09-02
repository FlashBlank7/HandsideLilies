from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.core.companion import ContentCategory
from lilies.core.companion_delivery import (
    COMPANION_DELIVERY_REASONS,
    COMPANION_DELIVERY_STATES,
)
from lilies.core.components import ComponentRegistry
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker
from lilies.core.socket_server import (
    LocalSocketServer,
    RUNTIME_DRAG_PROXY_MODES,
    RUNTIME_DRAG_PROXY_REASONS,
    RUNTIME_MODULE_ALLOWLIST,
)
import lilies.core.socket_server as socket_server_module


def test_qml_reports_loader_and_playback_state_to_the_runtime_snapshot() -> None:
    main_qml = (
        Path(__file__).resolve().parents[1] / "qml" / "Main.qml"
    ).read_text("utf-8")

    assert "function reportRuntimeSceneState()" in main_qml
    assert "backend.reportSceneRuntimeState(" in main_qml
    assert "onDesktopSceneLoadedChanged:" in main_qml
    assert "onDesktopVideoLoadedChanged:" in main_qml
    assert "onDesktopVideoPlaybackStateChanged:" in main_qml


def audit_count(database: Database) -> int:
    with database.connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM audit_log").fetchone()
    return int(row["count"])


def drag_proxy_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "configured": False,
        "ready": False,
        "active": False,
        "rootNativeHidden": False,
        "directMoveCommits": 0,
        "proxyRealGeometryCommits": 0,
        "proxyBitmapWidth": 0,
        "proxyBitmapHeight": 0,
        "proxyCacheAgeMs": 0.0,
        "proxyVisualStale": False,
        "lastMode": "none",
        "fallbackReason": "not-configured",
    }
    state.update(overrides)
    return state


def request_over_loopback(server: LocalSocketServer, request: dict[str, object]) -> dict:
    port = int(server.endpoint.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        return json.loads(client.makefile("rb").readline())


def test_runtime_snapshot_is_authenticated_strict_projected_and_zero_audit(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    provider_calls: list[str] = []

    def provider() -> dict:
        provider_calls.append("called")
        return {
            "schemaVersion": 99,
            "shellMode": "compact",
            "renderer": "video",
            "qtHeartbeatAgeMs": 125,
            "qtResponsive": True,
            "scene": {
                "active": True,
                "scene2dLoaded": False,
                "videoLoaded": False,
                "videoPlaybackState": "unloaded",
                "forbiddenSceneField": "must-not-leak",
            },
            "companion": {
                "enabled": True,
                "paused": False,
                "presentationReady": True,
                "suppressed": False,
                "busy": False,
                "ackPending": True,
                "hasBubble": True,
                "unreadCount": 0,
                "state": "waiting-present-ack",
                "reason": "generated",
                "expiresInSeconds": 0.0,
                "forbiddenContent": "must-not-leak",
            },
            "dragProxy": drag_proxy_state(
                configured=True,
                ready=True,
                active=True,
                rootNativeHidden=True,
                directMoveCommits=0,
                proxyRealGeometryCommits=1,
                proxyBitmapWidth=640,
                proxyBitmapHeight=720,
                proxyCacheAgeMs=18.25,
                proxyVisualStale=False,
                lastMode="layered-proxy",
                fallbackReason="",
                forbiddenCoordinates=[123, 456],
            ),
            "forbiddenTopLevelField": "must-not-leak",
        }

    loaded = {RUNTIME_MODULE_ALLOWLIST[0], RUNTIME_MODULE_ALLOWLIST[2]}
    monkeypatch.setattr(
        socket_server_module,
        "_module_loaded",
        lambda name: name in loaded,
    )
    server = LocalSocketServer(
        registry,
        tmp_path,
        port=0,
        runtime_snapshot_provider=provider,
    )
    baseline_audits = audit_count(database)
    server.start()
    try:
        denied = server.dispatch(
            {
                "id": "denied",
                "auth": "wrong",
                "method": "system.runtime_snapshot",
                "params": {},
            }
        )
        assert denied == {"id": "denied", "ok": False, "error": "unauthorized"}
        assert provider_calls == []

        invalid_requests = [
            {"id": "missing", "auth": server.token, "method": "system.runtime_snapshot"},
            {
                "id": "null",
                "auth": server.token,
                "method": "system.runtime_snapshot",
                "params": None,
            },
            {
                "id": "list",
                "auth": server.token,
                "method": "system.runtime_snapshot",
                "params": [],
            },
            {
                "id": "fields",
                "auth": server.token,
                "method": "system.runtime_snapshot",
                "params": {"fields": ["shellMode"]},
            },
        ]
        for request in invalid_requests:
            rejected = server.dispatch(request)
            assert rejected["ok"] is False
            assert rejected["error"] == "system.runtime_snapshot requires params={}"
        assert provider_calls == []

        allowed = request_over_loopback(
            server,
            {
                "id": "runtime",
                "auth": server.token,
                "method": "system.runtime_snapshot",
                "params": {},
            },
        )
        assert allowed == {
            "id": "runtime",
            "ok": True,
            "result": {
                "schemaVersion": 1,
                "shellMode": "compact",
                "renderer": "video",
                "qtHeartbeatAgeMs": 125,
                "qtResponsive": True,
                "scene": {
                    "active": True,
                    "scene2dLoaded": False,
                    "videoLoaded": False,
                    "videoPlaybackState": "unloaded",
                },
                "companion": {
                    "enabled": True,
                    "paused": False,
                    "presentationReady": True,
                    "suppressed": False,
                    "busy": False,
                    "ackPending": True,
                    "hasBubble": True,
                    "unreadCount": 0,
                    "state": "waiting-present-ack",
                    "reason": "generated",
                    "expiresInSeconds": 0.0,
                },
                "dragProxy": {
                    "configured": True,
                    "ready": True,
                    "active": True,
                    "rootNativeHidden": True,
                    "proxyVisualStale": False,
                    "directMoveCommits": 0,
                    "proxyRealGeometryCommits": 1,
                    "proxyBitmapWidth": 640,
                    "proxyBitmapHeight": 720,
                    "proxyCacheAgeMs": 18.2,
                    "lastMode": "layered-proxy",
                    "fallbackReason": "",
                },
                "loadedModules": {
                    "Qt6Multimedia.dll": True,
                    "Qt6MultimediaQuick.dll": False,
                    "ffmpegmediaplugin.dll": True,
                    "avcodec-61.dll": False,
                },
            },
        }
        assert provider_calls == ["called"]
        assert "forbidden" not in json.dumps(allowed, ensure_ascii=False).casefold()
        assert audit_count(database) == baseline_audits
    finally:
        server.stop()


def test_runtime_snapshot_fails_closed_on_invalid_provider_schema(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    server = LocalSocketServer(
        registry,
        tmp_path,
        port=0,
        runtime_snapshot_provider=lambda: {
            "shellMode": "compact",
            "renderer": "video",
            "qtHeartbeatAgeMs": 125,
            "qtResponsive": True,
            "scene": {
                "active": "yes",
                "scene2dLoaded": False,
                "videoLoaded": False,
                "videoPlaybackState": "unloaded",
            },
            "companion": {
                "enabled": True,
                "paused": False,
                "presentationReady": True,
                "suppressed": False,
                "busy": False,
                "ackPending": False,
                "hasBubble": False,
                "unreadCount": 0,
                "state": "idle",
                "reason": "",
                "expiresInSeconds": 0.0,
            },
            "dragProxy": drag_proxy_state(),
        },
    )
    baseline_audits = audit_count(database)
    response = server.dispatch(
        {
            "id": 2,
            "auth": server.token,
            "method": "system.runtime_snapshot",
            "params": {},
        }
    )
    assert response["ok"] is False
    assert response["error"] == "runtime snapshot scene field is invalid: active"
    assert audit_count(database) == baseline_audits


def test_runtime_snapshot_uses_the_shared_fixed_delivery_contract() -> None:
    snapshot = {
        "shellMode": "compact",
        "renderer": "scene2d",
        "qtHeartbeatAgeMs": 125,
        "qtResponsive": True,
        "scene": {
            "active": True,
            "scene2dLoaded": False,
            "videoLoaded": False,
            "videoPlaybackState": "unloaded",
        },
        "companion": {
            "enabled": True,
            "paused": False,
            "presentationReady": True,
            "suppressed": False,
            "busy": False,
            "ackPending": False,
            "hasBubble": False,
            "unreadCount": 0,
            "state": "idle",
            "reason": "",
            "expiresInSeconds": 0.0,
        },
        "dragProxy": drag_proxy_state(),
    }

    for state in COMPANION_DELIVERY_STATES:
        snapshot["companion"]["state"] = state
        assert socket_server_module._runtime_snapshot_result(snapshot)["companion"][
            "state"
        ] == state

    snapshot["companion"]["state"] = "idle"
    for reason in COMPANION_DELIVERY_REASONS:
        snapshot["companion"]["reason"] = reason
        assert socket_server_module._runtime_snapshot_result(snapshot)["companion"][
            "reason"
        ] == reason

    snapshot["companion"]["reason"] = r"unexpected F:\private\paper.png"
    try:
        socket_server_module._runtime_snapshot_result(snapshot)
    except RuntimeError as exc:
        assert str(exc) == "runtime snapshot companion reason is invalid"
    else:
        raise AssertionError("arbitrary companion delivery reasons must fail closed")

    snapshot["companion"]["reason"] = ""
    for mode in RUNTIME_DRAG_PROXY_MODES:
        snapshot["dragProxy"]["lastMode"] = mode
        assert socket_server_module._runtime_snapshot_result(snapshot)[
            "dragProxy"
        ]["lastMode"] == mode

    snapshot["dragProxy"]["lastMode"] = "none"
    for reason in RUNTIME_DRAG_PROXY_REASONS:
        snapshot["dragProxy"]["fallbackReason"] = reason
        assert socket_server_module._runtime_snapshot_result(snapshot)[
            "dragProxy"
        ]["fallbackReason"] == reason

    snapshot["dragProxy"]["fallbackReason"] = r"unexpected F:\private\pet.png"
    with pytest.raises(
        RuntimeError, match="runtime snapshot drag proxy reason is invalid"
    ):
        socket_server_module._runtime_snapshot_result(snapshot)
    snapshot["dragProxy"]["fallbackReason"] = "not-configured"
    snapshot["qtHeartbeatAgeMs"] = 2_001
    snapshot["qtResponsive"] = True
    with pytest.raises(
        RuntimeError, match="runtime snapshot Qt heartbeat state is inconsistent"
    ):
        socket_server_module._runtime_snapshot_result(snapshot)
    snapshot["qtResponsive"] = False
    assert (
        socket_server_module._runtime_snapshot_result(snapshot)["qtResponsive"]
        is False
    )


def test_backend_runtime_snapshot_cache_is_defensive_and_thread_safe(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        methods = {
            backend.metaObject().method(index).name().data().decode("utf-8")
            for index in range(backend.metaObject().methodCount())
        }
        assert {
            "reportSceneRuntimeState",
            "reportDragProxyRuntimeState",
            "runtimeSnapshot",
        } <= methods
        assert backend._runtime_heartbeat_timer.thread() == backend.thread()
        assert backend._runtime_heartbeat_timer.isActive() is True
        assert backend._runtime_heartbeat_timer.interval() == 250

        initial = backend.runtimeSnapshot()
        initial_heartbeat = {
            "qtHeartbeatAgeMs": initial.pop("qtHeartbeatAgeMs"),
            "qtResponsive": initial.pop("qtResponsive"),
        }
        assert 0 <= initial_heartbeat["qtHeartbeatAgeMs"] <= 2_000
        assert initial_heartbeat["qtResponsive"] is True
        assert initial == {
            "schemaVersion": 1,
            "shellMode": "compact",
            "renderer": backend.renderer,
            "scene": {
                "active": True,
                "scene2dLoaded": False,
                "videoLoaded": False,
                "videoPlaybackState": "unloaded",
            },
            "companion": {
                "enabled": True,
                "paused": False,
                "presentationReady": True,
                "suppressed": False,
                "busy": False,
                "ackPending": False,
                "hasBubble": False,
                "unreadCount": 0,
                "state": "idle",
                "reason": "",
                "expiresInSeconds": 0.0,
            },
            "dragProxy": drag_proxy_state(),
        }

        backend.reportSceneRuntimeState(False, True, "PLAYING")
        backend.reportDragProxyRuntimeState(
            drag_proxy_state(
                configured=True,
                ready=True,
                active=True,
                rootNativeHidden=True,
                directMoveCommits=0,
                proxyRealGeometryCommits=1,
                proxyBitmapWidth=512,
                proxyBitmapHeight=640,
                proxyCacheAgeMs=12.34,
                proxyVisualStale=True,
                lastMode="layered-proxy",
                fallbackReason="",
            )
        )
        backend.shell.mode = "visual"
        backend._renderer = "video"
        backend._scene_active = False
        backend.shellModeChanged.emit()
        backend.rendererChanged.emit()
        backend.sceneActiveChanged.emit()
        updated = backend.runtimeSnapshot()
        updated_heartbeat = {
            "qtHeartbeatAgeMs": updated.pop("qtHeartbeatAgeMs"),
            "qtResponsive": updated.pop("qtResponsive"),
        }
        assert 0 <= updated_heartbeat["qtHeartbeatAgeMs"] <= 2_000
        assert updated_heartbeat["qtResponsive"] is True
        assert updated == {
            "schemaVersion": 1,
            "shellMode": "visual",
            "renderer": "video",
            "scene": {
                "active": False,
                "scene2dLoaded": False,
                "videoLoaded": True,
                "videoPlaybackState": "playing",
            },
            "companion": {
                "enabled": True,
                "paused": False,
                "presentationReady": True,
                "suppressed": False,
                "busy": False,
                "ackPending": False,
                "hasBubble": False,
                "unreadCount": 0,
                "state": "idle",
                "reason": "",
                "expiresInSeconds": 0.0,
            },
            "dragProxy": drag_proxy_state(
                configured=True,
                ready=True,
                active=True,
                rootNativeHidden=True,
                directMoveCommits=0,
                proxyRealGeometryCommits=1,
                proxyBitmapWidth=512,
                proxyBitmapHeight=640,
                proxyCacheAgeMs=12.3,
                proxyVisualStale=True,
                lastMode="layered-proxy",
                fallbackReason="",
            ),
        }

        updated["scene"]["videoPlaybackState"] = "tampered"
        updated["dragProxy"]["fallbackReason"] = r"F:\private\leak.png"
        updated["shellMode"] = "tampered"
        assert backend.runtimeSnapshot()["scene"]["videoPlaybackState"] == "playing"
        assert backend.runtimeSnapshot()["dragProxy"]["fallbackReason"] == ""
        assert backend.runtimeSnapshot()["shellMode"] == "visual"

        backend.reportSceneRuntimeState(True, False, "future-state")
        assert backend.runtimeSnapshot()["scene"]["videoPlaybackState"] == "unknown"

        errors: list[object] = []

        def reader() -> None:
            for _ in range(300):
                value = backend.runtimeSnapshot()
                if set(value) != {
                    "schemaVersion",
                    "shellMode",
                    "renderer",
                    "qtHeartbeatAgeMs",
                    "qtResponsive",
                    "scene",
                    "companion",
                    "dragProxy",
                }:
                    errors.append(value)
                    return
                if set(value["scene"]) != {
                    "active",
                    "scene2dLoaded",
                    "videoLoaded",
                    "videoPlaybackState",
                }:
                    errors.append(value)
                    return
                if set(value["companion"]) != {
                    "enabled",
                    "paused",
                    "presentationReady",
                    "suppressed",
                    "busy",
                    "ackPending",
                    "hasBubble",
                    "unreadCount",
                    "state",
                    "reason",
                    "expiresInSeconds",
                }:
                    errors.append(value)
                    return
                if set(value["dragProxy"]) != {
                    "configured",
                    "ready",
                    "active",
                    "rootNativeHidden",
                    "directMoveCommits",
                    "proxyRealGeometryCommits",
                    "proxyBitmapWidth",
                    "proxyBitmapHeight",
                    "proxyCacheAgeMs",
                    "proxyVisualStale",
                    "lastMode",
                    "fallbackReason",
                }:
                    errors.append(value)
                    return

        readers = [threading.Thread(target=reader) for _ in range(4)]
        for thread in readers:
            thread.start()
        for index in range(120):
            backend.reportSceneRuntimeState(
                index % 2 == 0,
                index % 2 == 1,
                "playing" if index % 2 else "paused",
            )
            backend.reportDragProxyRuntimeState(
                drag_proxy_state(
                    configured=True,
                    ready=index % 2 == 0,
                    active=index % 3 == 0,
                    rootNativeHidden=index % 3 == 0,
                    directMoveCommits=index,
                    proxyRealGeometryCommits=index // 2,
                    proxyBitmapWidth=512,
                    proxyBitmapHeight=640,
                    proxyCacheAgeMs=float(index),
                    proxyVisualStale=index % 2 == 1,
                    lastMode="layered-proxy",
                    fallbackReason="" if index % 2 == 0 else "stale-key",
                )
            )
        for thread in readers:
            thread.join(timeout=3)
            assert not thread.is_alive()
        assert errors == []

        baseline_audits = audit_count(backend.database)
        socket_response = backend.socket.dispatch(
            {
                "id": "backend-runtime",
                "auth": backend.socket.token,
                "method": "system.runtime_snapshot",
                "params": {},
            }
        )
        assert socket_response["ok"] is True
        assert set(socket_response["result"]) == {
            "schemaVersion",
            "shellMode",
            "renderer",
            "qtHeartbeatAgeMs",
            "qtResponsive",
            "scene",
            "companion",
            "dragProxy",
            "loadedModules",
        }
        assert tuple(socket_response["result"]["loadedModules"]) == (
            RUNTIME_MODULE_ALLOWLIST
        )
        assert audit_count(backend.database) == baseline_audits
    finally:
        backend.shutdown()
        assert backend._runtime_heartbeat_timer.isActive() is False
        app.processEvents()


def test_backend_runtime_snapshot_reports_stale_qt_heartbeat_without_content(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend._runtime_heartbeat_timer.stop()
        with backend._runtime_snapshot_lock:
            backend._runtime_snapshot_state["qtHeartbeatMonotonic"] -= 10.0

        snapshot = backend.runtimeSnapshot()
        assert snapshot["qtResponsive"] is False
        assert snapshot["qtHeartbeatAgeMs"] >= 10_000
        assert set(snapshot) == {
            "schemaVersion",
            "shellMode",
            "renderer",
            "qtHeartbeatAgeMs",
            "qtResponsive",
            "scene",
            "companion",
            "dragProxy",
        }

        response = backend.socket.dispatch(
            {
                "id": "stale-heartbeat",
                "auth": backend.socket.token,
                "method": "system.runtime_snapshot",
                "params": {},
            }
        )
        assert response["ok"] is True
        assert response["result"]["qtResponsive"] is False
        assert response["result"]["qtHeartbeatAgeMs"] >= 10_000
        assert "title" not in json.dumps(response, ensure_ascii=False).casefold()
        assert "path" not in json.dumps(response, ensure_ascii=False).casefold()

        backend._runtime_heartbeat_timer.start()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            app.processEvents()
            if backend.runtimeSnapshot()["qtResponsive"]:
                break
            time.sleep(0.01)
        recovered = backend.runtimeSnapshot()
        assert recovered["qtResponsive"] is True
        assert recovered["qtHeartbeatAgeMs"] <= 2_000
    finally:
        backend.shutdown()
        app.processEvents()


def test_backend_runtime_snapshot_tracks_delivery_without_bubble_content(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    companion = backend.companion
    companion._foreground_provider = lambda: 0
    sensitive_probe = "private-prose-must-never-enter-runtime-snapshot"
    try:
        companion._busy = True
        companion._accept_generation(
            {
                "result": {
                    "summary": sensitive_probe,
                    "detail": sensitive_probe + " detail",
                    "model": "gpt-5.6-luna",
                    "contextType": "application-signal",
                },
                "category": ContentCategory.LORE,
                "sceneLabel": "synthetic",
                "force": True,
            }
        )
        waiting = backend.runtimeSnapshot()["companion"]
        assert waiting["state"] == "waiting-present-ack"
        assert waiting["ackPending"] is True
        assert waiting["hasBubble"] is True
        assert waiting["unreadCount"] == 0
        assert sensitive_probe not in json.dumps(
            backend.runtimeSnapshot(), ensure_ascii=False
        )

        companion._presentation_ack_timed_out()
        unread = backend.runtimeSnapshot()["companion"]
        assert unread["state"] == "unread"
        assert unread["ackPending"] is False
        assert unread["hasBubble"] is False
        assert unread["unreadCount"] == 1

        response = backend.socket.dispatch(
            {
                "id": "delivery-runtime",
                "auth": backend.socket.token,
                "method": "system.runtime_snapshot",
                "params": {},
            }
        )
        assert response["ok"] is True
        assert response["result"]["companion"] == unread
        assert sensitive_probe not in json.dumps(response, ensure_ascii=False)
    finally:
        backend.shutdown()
        app.processEvents()
