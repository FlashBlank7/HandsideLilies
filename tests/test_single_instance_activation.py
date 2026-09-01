from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtWidgets import QApplication

from lilies import app as app_module
import lilies.backend as backend_module
from lilies.backend import Backend
from lilies.core.components import ComponentRegistry
from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker
from lilies.core.socket_server import LocalSocketServer, request_existing_instance


def _registry(root: Path) -> ComponentRegistry:
    database = Database(root / "lilies.db")
    return ComponentRegistry(database, PermissionBroker(database))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _applied_sink(actions: list[str]):
    def apply(action: str) -> dict[str, object]:
        actions.append(action)
        return {
            "accepted": True,
            "applied": True,
            "mode": action if action in {"visual", "compact"} else "compact",
            "surfaceDisposition": "shown",
            "error": "",
        }

    return apply


def _request_over_loopback(
    server: LocalSocketServer,
    action: str,
    *,
    timeout: float = 2.0,
) -> dict[str, object]:
    port = int(server.endpoint.rsplit(":", 1)[1])
    request = {
        "id": f"activation-{action}",
        "auth": server.token,
        "method": "system.activate",
        "params": {"action": action},
    }
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(json.dumps(request).encode("utf-8") + b"\n")
        return json.loads(client.makefile("rb").readline())


def _run_worker_with_qt_events(
    app: QApplication,
    target,
    *,
    timeout: float = 2.0,
) -> None:
    worker = threading.Thread(target=target)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    worker.join(timeout=0.2)
    assert not worker.is_alive()


def test_authenticated_launcher_request_activates_canonical_instance(tmp_path):
    actions: list[str] = []
    port = _free_port()
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(actions),
    )
    server.start()
    try:
        assert request_existing_instance(tmp_path, "visual", port=port) is True
        deadline = time.monotonic() + 1.0
        while not actions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert actions == ["visual"]
    finally:
        server.stop()


def test_activation_rejects_unknown_or_malformed_actions(tmp_path):
    actions: list[str] = []
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=0,
        activation_sink=_applied_sink(actions),
    )
    server.start()
    try:
        with pytest.raises(ValueError):
            request_existing_instance(tmp_path, "shell.exec", port=1)
        invalid = server.dispatch(
            {
                "id": "invalid",
                "auth": server.token,
                "method": "system.activate",
                "params": {"action": "shell.exec"},
            }
        )
        malformed = server.dispatch(
            {
                "id": "extra",
                "auth": server.token,
                "method": "system.activate",
                "params": {"action": "visual", "command": "anything"},
            }
        )
        assert invalid["ok"] is False
        assert malformed["ok"] is False
        assert actions == []
    finally:
        server.stop()


def test_stale_token_cannot_activate_existing_instance(tmp_path):
    actions: list[str] = []
    port = _free_port()
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(actions),
    )
    server.start()
    try:
        server.token_path.write_text("expired-session-token", "utf-8")
        assert request_existing_instance(tmp_path, "compact", port=port) is False
        assert actions == []
    finally:
        server.stop()


def test_occupied_fixed_port_fails_closed_without_replacing_session_token(tmp_path):
    fixed_port = _free_port()
    stale_token = "primary-session-token"
    (tmp_path / "socket-token.txt").write_text(stale_token, "utf-8")
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", fixed_port))
    blocker.listen(1)
    actions: list[str] = []
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=fixed_port,
        activation_sink=_applied_sink(actions),
    )
    try:
        with pytest.raises(OSError):
            server.start()
        assert server.server is None
        assert server.endpoint == ""
        assert server.is_canonical_instance is False
        assert server.token_path.read_text("utf-8") == stale_token
        assert actions == []
    finally:
        server.stop()
        blocker.close()


def test_reserved_endpoint_authenticated_lifecycle_allows_immediate_restart(tmp_path):
    """The exclusive fixed port must still support a normal hot update.

    This intentionally performs a real localhost lifecycle on Windows:
    reserve, adopt, authenticate, stop, then reserve the exact same port with
    no sleep in between.  Accepted connections must not leave the activation
    port stuck behind TIME_WAIT.
    """

    port = _free_port()
    first_actions: list[str] = []
    first = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(first_actions),
        prebound_socket=LocalSocketServer.reserve_endpoint(port),
    )
    first.start()
    first_token = first.token
    try:
        assert first.is_canonical_instance is True
        assert request_existing_instance(tmp_path, "show", port=port) is True
        deadline = time.monotonic() + 1.0
        while not first_actions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first_actions == ["show"]
    finally:
        first.stop()

    second_actions: list[str] = []
    second = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(second_actions),
        prebound_socket=LocalSocketServer.reserve_endpoint(port),
    )
    second.start()
    try:
        assert second.token != first_token
        assert second.is_canonical_instance is True
        assert request_existing_instance(tmp_path, "compact", port=port) is True
        deadline = time.monotonic() + 1.0
        while not second_actions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert second_actions == ["compact"]
    finally:
        second.stop()


def test_prebound_backlog_discards_stale_token_then_accepts_current_session(tmp_path):
    port = _free_port()
    (tmp_path / "socket-token.txt").write_text("stale-launch-token", "utf-8")
    reserved = LocalSocketServer.reserve_endpoint(port)
    actions: list[str] = []
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(actions),
        prebound_socket=reserved,
    )

    # The connection succeeds at the kernel backlog, but there is no handler
    # yet.  The competing launcher times out and disconnects before adoption.
    assert (
        request_existing_instance(tmp_path, "visual", port=port, timeout=0.05)
        is False
    )

    server.start()
    try:
        assert request_existing_instance(tmp_path, "show", port=port) is True
        deadline = time.monotonic() + 1.0
        while not actions and time.monotonic() < deadline:
            time.sleep(0.01)
        # The queued stale request is rejected and cannot change shell mode.
        assert actions == ["show"]
    finally:
        server.stop()


def test_concurrent_endpoint_reservation_has_exactly_one_owner():
    port = _free_port()
    barrier = threading.Barrier(3)
    listeners: list[socket.socket] = []
    errors: list[OSError] = []

    def compete() -> None:
        barrier.wait()
        try:
            listeners.append(LocalSocketServer.reserve_endpoint(port))
        except OSError as exc:
            errors.append(exc)

    workers = [threading.Thread(target=compete) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)
    try:
        assert all(not worker.is_alive() for worker in workers)
        assert len(listeners) == 1
        assert len(errors) == 1
    finally:
        for listener in listeners:
            listener.close()


@pytest.mark.parametrize(
    ("argv", "expected_action"),
    [(["--visual"], "visual"), (["--compact"], "compact"), ([], "show")],
)
def test_startup_race_retries_and_forwards_the_exact_launcher_action(
    tmp_path, argv, expected_action
):
    port = _free_port()
    reserved = LocalSocketServer.reserve_endpoint(port)
    actions: list[str] = []
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=_applied_sink(actions),
        prebound_socket=reserved,
    )

    def finish_startup() -> None:
        time.sleep(0.08)
        server.start()

    starter = threading.Thread(target=finish_startup)
    starter.start()
    try:
        assert app_module.wait_for_existing_instance(
            app_module.parse_args(argv),
            root=tmp_path,
            port=port,
            timeout=1.0,
        ) is True
        starter.join(timeout=1)
        assert not starter.is_alive()
        deadline = time.monotonic() + 1.0
        while not actions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert actions == [expected_action]
    finally:
        starter.join(timeout=1)
        server.stop()


def test_unrelated_port_owner_is_not_forwarded_and_startup_stays_fail_closed(
    tmp_path,
):
    port = _free_port()
    (tmp_path / "socket-token.txt").write_text("foreign-listener-token", "utf-8")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(socket.SOMAXCONN)
    listener.settimeout(0.03)
    stop = threading.Event()

    def answer_with_non_lilies_json_until_stopped() -> None:
        while not stop.is_set():
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                try:
                    connection.recv(65536)
                    connection.sendall(b"[]\n")
                except OSError:
                    pass

    worker = threading.Thread(target=answer_with_non_lilies_json_until_stopped)
    worker.start()
    try:
        reserved, forwarded = app_module.reserve_primary_endpoint_or_forward(
            app_module.parse_args(["--compact"]),
            root=tmp_path,
            port=port,
            timeout=0.18,
        )
        assert reserved is None
        assert forwarded is False
    finally:
        stop.set()
        listener.close()
        worker.join(timeout=1)
        assert not worker.is_alive()


def test_unrelated_fixed_port_response_is_never_treated_as_lilies(tmp_path):
    port = _free_port()
    (tmp_path / "socket-token.txt").write_text("not-for-the-other-server", "utf-8")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    def answer_with_non_lilies_json() -> None:
        connection, _address = listener.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(b"[]\n")

    worker = threading.Thread(target=answer_with_non_lilies_json)
    worker.start()
    try:
        assert request_existing_instance(tmp_path, "show", port=port) is False
        worker.join(timeout=1)
        assert not worker.is_alive()
    finally:
        listener.close()


def test_socket_worker_only_queues_shell_change_for_qt_main_thread(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    main_thread = threading.get_ident()
    applied_threads: list[int] = []
    backend.applicationActivationRequested.connect(
        lambda _action: applied_threads.append(threading.get_ident())
    )
    response: dict[str, object] = {}
    try:
        worker = threading.Thread(
            target=lambda: response.update(_request_over_loopback(backend.socket, "compact"))
        )
        worker.start()
        time.sleep(0.03)
        assert worker.is_alive()
        assert backend.shellMode == "visual"

        deadline = time.monotonic() + 2.0
        while worker.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        worker.join(timeout=0.2)

        assert not worker.is_alive()
        assert response == {
            "id": "activation-compact",
            "ok": True,
            "result": {
                "service": "lilies-in-the-box",
                "action": "compact",
                "accepted": True,
                "applied": True,
                "mode": "compact",
                "surfaceDisposition": "shown",
                "error": "",
            },
        }
        assert backend.shellMode == "compact"
        assert backend.database.get_setting("shell_mode") == "compact"
        assert applied_threads == [main_thread]
    finally:
        backend.shutdown()
        app.processEvents()


@pytest.mark.parametrize(
    ("action", "completion", "expected"),
    [
        (
            "compact",
            {
                "accepted": True,
                "applied": True,
                "mode": "compact",
                "surfaceDisposition": "privacy-suppressed",
                "error": "",
            },
            True,
        ),
        (
            "compact",
            {
                "accepted": True,
                "applied": False,
                "mode": "compact",
                "surfaceDisposition": "not-applied",
                "error": "database is locked",
            },
            False,
        ),
        (
            "visual",
            {
                "accepted": True,
                "applied": True,
                "mode": "compact",
                "surfaceDisposition": "shown",
                "error": "",
            },
            False,
        ),
        (
            "show",
            {
                "accepted": True,
                "applied": True,
                "mode": "compact",
                "surfaceDisposition": "privacy-suppressed",
                "error": "",
            },
            True,
        ),
        (
            "show",
            {
                "accepted": True,
                "applied": True,
                "mode": "visual",
                "surfaceDisposition": "shown",
                "error": "",
            },
            True,
        ),
        (
            "visual",
            {
                "accepted": True,
                "applied": False,
                "mode": "compact",
                "surfaceDisposition": "pending",
                "error": "",
            },
            True,
        ),
    ],
)
def test_launcher_requires_applied_matching_activation_completion(
    tmp_path,
    action,
    completion,
    expected,
):
    port = _free_port()
    server = LocalSocketServer(
        _registry(tmp_path),
        tmp_path,
        port=port,
        activation_sink=lambda _action: dict(completion),
    )
    server.start()
    try:
        assert request_existing_instance(tmp_path, action, port=port) is expected
    finally:
        server.stop()


def test_privacy_suppression_is_explicit_and_launcher_accepts_owned_deferred_show(
    tmp_path, monkeypatch
):
    data_directory = tmp_path / "private-data-privacy"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    monkeypatch.setattr(
        backend.pet_habitat,
        "status",
        lambda: {"state": "silent", "visible": False},
    )
    backend._habitat_status = {"state": "silent", "visible": False}
    port = int(backend.socket.endpoint.rsplit(":", 1)[1])
    compact_response: dict[str, object] = {}
    show_response: dict[str, object] = {}
    forwarded: dict[str, bool] = {}
    try:
        _run_worker_with_qt_events(
            app,
            lambda: compact_response.update(
                _request_over_loopback(backend.socket, "compact")
            ),
        )
        assert compact_response["result"] == {
            "service": "lilies-in-the-box",
            "action": "compact",
            "accepted": True,
            "applied": True,
            "mode": "compact",
            "surfaceDisposition": "privacy-suppressed",
            "error": "",
        }

        _run_worker_with_qt_events(
            app,
            lambda: show_response.update(_request_over_loopback(backend.socket, "show")),
        )
        assert show_response["result"] == {
            "service": "lilies-in-the-box",
            "action": "show",
            "accepted": True,
            "applied": True,
            "mode": "compact",
            "surfaceDisposition": "privacy-suppressed",
            "error": "",
        }

        _run_worker_with_qt_events(
            app,
            lambda: forwarded.update(
                compact=request_existing_instance(
                    data_directory, "compact", port=port
                )
            ),
        )
        _run_worker_with_qt_events(
            app,
            lambda: forwarded.update(
                show=request_existing_instance(data_directory, "show", port=port)
            ),
        )
        # The second launcher exits because the authenticated canonical
        # instance accepted the request.  "True" is ownership/forwarding
        # success here; the explicit disposition above remains the source of
        # truth that presentation is deferred rather than currently visible.
        assert forwarded == {"compact": True, "show": True}
    finally:
        backend.shutdown()
        app.processEvents()


def test_database_failure_returns_not_applied_completion(tmp_path, monkeypatch):
    data_directory = tmp_path / "private-data-database-failure"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    original_set_setting = backend.database.set_setting
    activations: list[str] = []
    backend.applicationActivationRequested.connect(activations.append)

    def fail_shell_mode_write(key: str, value: object) -> None:
        if key == "shell_mode":
            raise sqlite3.OperationalError("database is locked")
        original_set_setting(key, value)

    monkeypatch.setattr(backend.database, "set_setting", fail_shell_mode_write)
    response: dict[str, object] = {}
    forwarded: dict[str, bool] = {}
    port = int(backend.socket.endpoint.rsplit(":", 1)[1])
    try:
        _run_worker_with_qt_events(
            app,
            lambda: response.update(_request_over_loopback(backend.socket, "compact")),
        )
        result = response["result"]
        assert result["accepted"] is True
        assert result["applied"] is False
        assert result["mode"] == "compact"
        assert result["surfaceDisposition"] == "not-applied"
        assert "database is locked" in result["error"]
        assert activations == []
        assert backend.database.get_setting("shell_mode", "visual") == "visual"

        _run_worker_with_qt_events(
            app,
            lambda: forwarded.update(
                value=request_existing_instance(
                    data_directory, "compact", port=port
                )
            ),
        )
        assert forwarded == {"value": False}
    finally:
        backend.shutdown()
        app.processEvents()


def test_activation_wait_is_bounded_and_pending_request_is_applied_once(
    tmp_path, monkeypatch
):
    data_directory = tmp_path / "private-data-timeout"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    monkeypatch.setattr(backend_module, "_ACTIVATION_APPLY_TIMEOUT_SECONDS", 0.05)
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    activations: list[str] = []
    backend.applicationActivationRequested.connect(activations.append)
    try:
        started = time.monotonic()
        response = _request_over_loopback(backend.socket, "compact", timeout=0.5)
        elapsed = time.monotonic() - started

        assert elapsed < 0.4
        assert response["result"] == {
            "service": "lilies-in-the-box",
            "action": "compact",
            "accepted": True,
            "applied": False,
            "mode": "visual",
            "surfaceDisposition": "pending",
            "error": "",
        }
        assert backend.shellMode == "visual"
        assert backend.database.get_setting("shell_mode", "visual") == "visual"

        # The pending ACK does not discard the action.  Once the Qt thread is
        # allowed to process its queued request, the exact mode switch applies
        # once and is durably persisted.
        deadline = time.monotonic() + 1.0
        while backend.shellMode != "compact" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert backend.shellMode == "compact"
        assert backend.database.get_setting("shell_mode", "visual") == "compact"
        assert activations == ["compact"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_claimed_slow_activation_reports_pending_without_false_failure(
    tmp_path, monkeypatch
):
    """A Qt action already in progress may finish after its socket budget."""

    data_directory = tmp_path / "private-data-slow-apply"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    monkeypatch.setattr(backend_module, "_ACTIVATION_APPLY_TIMEOUT_SECONDS", 0.02)
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    original_consumer = backend._consumeExternalActivation
    activations: list[str] = []
    response: dict[str, object] = {}

    def slow_consumer(action: str) -> dict[str, object]:
        # Synchronous QML Loader work blocks this same Qt-thread call in the
        # real application.  Reproduce that ordering without loading a GUI.
        time.sleep(0.06)
        return original_consumer(action)

    monkeypatch.setattr(backend, "_consumeExternalActivation", slow_consumer)
    backend.applicationActivationRequested.connect(activations.append)
    try:
        _run_worker_with_qt_events(
            app,
            lambda: response.update(
                _request_over_loopback(backend.socket, "compact", timeout=0.5)
            ),
        )
        assert response["result"] == {
            "service": "lilies-in-the-box",
            "action": "compact",
            "accepted": True,
            "applied": False,
            "mode": "visual",
            "surfaceDisposition": "pending",
            "error": "",
        }
        # Depending on whether the worker deadline wins just before or just
        # after Qt claims the queued call, the pending response can arrive
        # while the deliberately slow consumer is still finishing.  Pending
        # promises eventual single application, not immediate application.
        deadline = time.monotonic() + 1.0
        while backend.shellMode != "compact" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert backend.shellMode == "compact"
        assert backend.database.get_setting("shell_mode") == "compact"
        assert activations == ["compact"]
    finally:
        backend.shutdown()
        app.processEvents()


def test_visual_argument_and_duplicate_forwarding_contract(tmp_path, monkeypatch):
    visual = app_module.parse_args(["--visual"])
    compact = app_module.parse_args(["--compact"])
    normal = app_module.parse_args([])
    assert app_module.activation_action(visual) == "visual"
    assert app_module.activation_action(compact) == "compact"
    assert app_module.activation_action(normal) == "show"
    with pytest.raises(SystemExit):
        app_module.parse_args(["--visual", "--compact"])

    forwarded: list[str] = []
    monkeypatch.setattr(app_module, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        app_module,
        "request_existing_instance",
        lambda _root, action: forwarded.append(action) or True,
    )
    assert app_module.forward_to_existing_instance(visual) is True
    assert forwarded == ["visual"]

    monkeypatch.setattr(
        app_module,
        "Backend",
        lambda *args, **kwargs: pytest.fail("duplicate launch constructed Backend"),
    )
    assert app_module.main(["--visual"]) == 0


def test_tray_primary_gestures_only_reveal_current_surface():
    reason = app_module.QSystemTrayIcon.ActivationReason
    assert app_module.tray_activation_shows_surface(reason.Trigger) is True
    assert app_module.tray_activation_shows_surface(reason.DoubleClick) is True
    assert app_module.tray_activation_shows_surface(reason.Context) is False
    assert app_module.tray_activation_shows_surface(reason.MiddleClick) is False
    assert app_module.tray_activation_shows_surface(reason.Unknown) is False


@pytest.mark.parametrize(
    ("habitat", "focus", "expected"),
    [
        (
            {"state": "silent", "reason": "full-screen", "visible": False},
            {
                "active": True,
                "paused": False,
                "planned_seconds": 1500,
                "elapsedSeconds": 62,
            },
            "全屏界面中静默 · 专注 23:58",
        ),
        (
            {"state": "blocked", "reason": "sensitive-window", "visible": False},
            {
                "active": True,
                "paused": True,
                "planned_seconds": 5400,
                "elapsedSeconds": 120,
            },
            "受保护界面中隐藏 · 专注已暂停 1:28:00",
        ),
        (
            {
                "state": "attached",
                "reason": "stable-host",
                "visible": True,
                "windowSizeClass": "small",
            },
            {"active": False},
            "在小窗口边缘栖息",
        ),
        (
            {"state": "avoiding", "reason": "pointer-avoidance", "visible": True},
            {},
            "正在避开鼠标",
        ),
        (
            {"state": "desktop", "reason": "no-host", "visible": True},
            {},
            "桌面停驻",
        ),
    ],
)
def test_tray_status_is_live_concise_and_never_uses_window_titles(
    habitat, focus, expected
):
    habitat["windowTitle"] = "绝不应进入托盘的论文标题"
    assert app_module.format_tray_status(habitat, focus) == expected
    tooltip = app_module.format_tray_tooltip(habitat, focus)
    assert tooltip == f"Lilies in the box · {expected}"
    assert "绝不应进入" not in tooltip


@pytest.mark.parametrize("mode", ["compact", "visual"])
def test_show_activation_preserves_shell_preference_and_desktop_peek(
    tmp_path, monkeypatch, mode
):
    data_directory = tmp_path / f"private-data-{mode}"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    app = QApplication.instance() or QApplication([])
    backend = Backend(
        smoke=True,
        force_compact=mode == "compact",
        force_visual=mode == "visual",
    )
    monitor_renewals: list[str] = []
    activations: list[str] = []
    shell_notifications: list[str] = []
    try:
        backend.enter_initial_mode()
        assert backend.socket.port == 0
        assert backend.socket.is_canonical_instance is False
        backend._desktop_peek_status = {
            "active": True,
            "minimized": 4,
            "transactionId": "test-peek",
        }
        peek_before = dict(backend.desktopPeekStatus)
        persisted_before = backend.database.get_setting("shell_mode")
        monkeypatch.setattr(
            backend.selection,
            "ensure_monitor",
            lambda: monitor_renewals.append(backend.shellMode),
        )
        backend.applicationActivationRequested.connect(activations.append)
        backend.shellModeChanged.connect(
            lambda: shell_notifications.append(backend.shellMode)
        )

        backend.showCurrentSurface()

        assert backend.shellMode == mode
        assert backend.database.get_setting("shell_mode") == persisted_before == mode
        assert backend.desktopPeekStatus == peek_before
        assert monitor_renewals == [mode]
        assert shell_notifications == [mode]
        assert activations == ["show"]
    finally:
        backend._desktop_peek_status = backend.desktop_peek.status()
        backend.shutdown()
        app.processEvents()


def test_explicit_shortcut_switches_shell_without_consuming_active_peek(
    tmp_path, monkeypatch
):
    data_directory = tmp_path / "private-data-shortcuts"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        backend.enter_initial_mode()
        backend._desktop_peek_status = {
            "active": True,
            "minimized": 3,
            "transactionId": "active-peek-kept",
        }
        peek_before = dict(backend.desktopPeekStatus)

        backend._consumeExternalActivation("visual")
        assert backend.shellMode == "visual"
        assert backend.database.get_setting("shell_mode") == "visual"
        assert backend.desktopPeekStatus == peek_before

        backend._consumeExternalActivation("compact")
        assert backend.shellMode == "compact"
        assert backend.database.get_setting("shell_mode") == "compact"
        assert backend.desktopPeekStatus == peek_before
    finally:
        backend._desktop_peek_status = backend.desktop_peek.status()
        backend.shutdown()
        app.processEvents()


def test_force_visual_overrides_persisted_compact_for_explicit_launch(
    tmp_path, monkeypatch
):
    data_directory = tmp_path / "private-data"
    monkeypatch.setenv("LILIES_DATA_DIR", str(data_directory))
    Database(data_directory / "lilies.db").set_setting("shell_mode", "compact")
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_visual=True)
    try:
        assert backend.shellMode == "visual"
        backend.enter_initial_mode()
        assert backend.database.get_setting("shell_mode") == "visual"
    finally:
        backend.shutdown()
        app.processEvents()
