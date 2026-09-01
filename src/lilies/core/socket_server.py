from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable

from .companion_delivery import (
    COMPANION_DELIVERY_REASONS,
    COMPANION_DELIVERY_STATES,
)
from .components import ComponentRegistry, ConfirmationRequired


PRIMARY_SOCKET_PORT = 47631
RUNTIME_QT_HEARTBEAT_STALE_MS = 2_000
RUNTIME_QT_HEARTBEAT_MAX_AGE_MS = 86_400_000
ACTIVATION_ACTIONS = frozenset({"visual", "compact", "show"})
ACTIVATION_SURFACE_DISPOSITIONS = frozenset(
    {"shown", "privacy-suppressed", "pending", "not-applied", "timed-out"}
)
_ACTIVATION_RESULT_FIELDS = frozenset(
    {
        "accepted",
        "applied",
        "mode",
        "surfaceDisposition",
        "error",
    }
)
_ACTIVATION_RESPONSE_FIELDS = frozenset(
    {"service", "action", *_ACTIVATION_RESULT_FIELDS}
)
_ACTIVATION_SHELL_MODES = frozenset({"visual", "login", "compact"})
RUNTIME_MODULE_ALLOWLIST = (
    "Qt6Multimedia.dll",
    "Qt6MultimediaQuick.dll",
    "ffmpegmediaplugin.dll",
    "avcodec-61.dll",
)
_RUNTIME_SHELL_MODES = frozenset({"visual", "login", "compact"})
_RUNTIME_RENDERERS = frozenset({"scene2d", "video"})
_RUNTIME_VIDEO_STATES = frozenset(
    {"unloaded", "playing", "paused", "stopped", "error", "unknown"}
)


def _module_loaded(name: str) -> bool:
    """Check one fixed module name without exposing the process module list."""

    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetModuleHandleW(str(name)))
    except (AttributeError, OSError):
        return False


def _runtime_snapshot_result(raw: Any) -> dict[str, Any]:
    """Project an app snapshot onto the deliberately tiny diagnostic schema."""

    if not isinstance(raw, dict):
        raise RuntimeError("runtime snapshot provider returned an invalid value")
    scene = raw.get("scene")
    if not isinstance(scene, dict):
        raise RuntimeError("runtime snapshot scene is unavailable")
    companion = raw.get("companion")
    if not isinstance(companion, dict):
        raise RuntimeError("runtime snapshot companion state is unavailable")

    shell_mode = str(raw.get("shellMode", ""))
    renderer = str(raw.get("renderer", ""))
    heartbeat_age = raw.get("qtHeartbeatAgeMs")
    qt_responsive = raw.get("qtResponsive")
    playback_state = str(scene.get("videoPlaybackState", ""))
    if shell_mode not in _RUNTIME_SHELL_MODES:
        raise RuntimeError("runtime snapshot shell mode is invalid")
    if renderer not in _RUNTIME_RENDERERS:
        raise RuntimeError("runtime snapshot renderer is invalid")
    if playback_state not in _RUNTIME_VIDEO_STATES:
        raise RuntimeError("runtime snapshot video state is invalid")
    if (
        isinstance(heartbeat_age, bool)
        or not isinstance(heartbeat_age, int)
        or not 0 <= heartbeat_age <= RUNTIME_QT_HEARTBEAT_MAX_AGE_MS
    ):
        raise RuntimeError("runtime snapshot Qt heartbeat age is invalid")
    if not isinstance(qt_responsive, bool):
        raise RuntimeError("runtime snapshot Qt responsiveness is invalid")
    if qt_responsive is not (
        heartbeat_age <= RUNTIME_QT_HEARTBEAT_STALE_MS
    ):
        raise RuntimeError("runtime snapshot Qt heartbeat state is inconsistent")

    booleans: dict[str, bool] = {}
    for key in ("active", "scene2dLoaded", "videoLoaded"):
        value = scene.get(key)
        if not isinstance(value, bool):
            raise RuntimeError(f"runtime snapshot scene field is invalid: {key}")
        booleans[key] = value

    companion_booleans: dict[str, bool] = {}
    for key in (
        "enabled",
        "paused",
        "presentationReady",
        "suppressed",
        "busy",
        "ackPending",
        "hasBubble",
    ):
        value = companion.get(key)
        if not isinstance(value, bool):
            raise RuntimeError(f"runtime snapshot companion field is invalid: {key}")
        companion_booleans[key] = value
    unread_count = companion.get("unreadCount")
    if (
        isinstance(unread_count, bool)
        or not isinstance(unread_count, int)
        or not 0 <= unread_count <= 1
    ):
        raise RuntimeError("runtime snapshot companion unread count is invalid")
    delivery_state = str(companion.get("state", ""))
    reason = str(companion.get("reason", ""))
    if delivery_state not in COMPANION_DELIVERY_STATES:
        raise RuntimeError("runtime snapshot companion state is invalid")
    if reason not in COMPANION_DELIVERY_REASONS:
        raise RuntimeError("runtime snapshot companion reason is invalid")
    expires_in = companion.get("expiresInSeconds")
    if (
        isinstance(expires_in, bool)
        or not isinstance(expires_in, (int, float))
        or not 0.0 <= float(expires_in) <= 86_400.0
    ):
        raise RuntimeError("runtime snapshot companion expiry is invalid")

    return {
        "schemaVersion": 1,
        "shellMode": shell_mode,
        "renderer": renderer,
        "qtHeartbeatAgeMs": heartbeat_age,
        "qtResponsive": qt_responsive,
        "scene": {
            **booleans,
            "videoPlaybackState": playback_state,
        },
        "companion": {
            **companion_booleans,
            "unreadCount": unread_count,
            "state": delivery_state,
            "reason": reason,
            "expiresInSeconds": round(float(expires_in), 1),
        },
        "loadedModules": {
            name: _module_loaded(name) for name in RUNTIME_MODULE_ALLOWLIST
        },
    }


def _activation_failure(
    action: str,
    error: object,
    *,
    accepted: bool,
    mode: str = "",
    surface_disposition: str = "not-applied",
) -> dict[str, Any]:
    return {
        "service": "lilies-in-the-box",
        "action": action,
        "accepted": bool(accepted),
        "applied": False,
        "mode": mode if mode in _ACTIVATION_SHELL_MODES else "",
        "surfaceDisposition": surface_disposition,
        "error": str(error or "activation was not applied").strip()[:512],
    }


def _activation_result(action: str, raw: Any) -> dict[str, Any]:
    """Project one Backend completion onto the fixed activation schema."""

    if not isinstance(raw, dict) or set(raw) != _ACTIVATION_RESULT_FIELDS:
        return _activation_failure(
            action,
            "activation sink returned an invalid completion",
            accepted=False,
        )

    accepted = raw.get("accepted")
    applied = raw.get("applied")
    mode = raw.get("mode")
    disposition = raw.get("surfaceDisposition")
    error = raw.get("error")
    if not isinstance(accepted, bool) or not isinstance(applied, bool):
        return _activation_failure(
            action,
            "activation completion flags are invalid",
            accepted=False,
        )
    if not isinstance(mode, str) or mode not in _ACTIVATION_SHELL_MODES:
        return _activation_failure(
            action,
            "activation completion mode is invalid",
            accepted=accepted,
        )
    if (
        not isinstance(disposition, str)
        or disposition not in ACTIVATION_SURFACE_DISPOSITIONS
    ):
        return _activation_failure(
            action,
            "activation surface disposition is invalid",
            accepted=accepted,
            mode=mode,
        )
    if not isinstance(error, str):
        return _activation_failure(
            action,
            "activation completion error is invalid",
            accepted=accepted,
            mode=mode,
        )
    if applied and (not accepted or error):
        return _activation_failure(
            action,
            "activation completion is internally inconsistent",
            accepted=accepted,
            mode=mode,
        )
    if disposition == "pending" and (not accepted or applied or error):
        return _activation_failure(
            action,
            "pending activation completion is internally inconsistent",
            accepted=accepted,
            mode=mode,
        )

    return {
        "service": "lilies-in-the-box",
        "action": action,
        "accepted": accepted,
        "applied": applied,
        "mode": mode,
        "surfaceDisposition": disposition,
        "error": error.strip()[:512],
    }


class _ThreadingServer(socketserver.ThreadingTCPServer):
    # The fixed activation port is the process ownership boundary.  Reusing a
    # live listener would defeat single-instance startup on Windows.
    allow_reuse_address = False
    daemon_threads = True


class LocalSocketServer:
    def __init__(
        self,
        registry: ComponentRegistry,
        data_directory: Path,
        port: int = PRIMARY_SOCKET_PORT,
        *,
        activation_sink: Callable[[str], dict[str, Any]] | None = None,
        runtime_snapshot_provider: Callable[[], dict[str, Any]] | None = None,
        prebound_socket: socket.socket | None = None,
    ) -> None:
        self.registry = registry
        self.data_directory = data_directory
        self.port = port
        self.activation_sink = activation_sink
        self.runtime_snapshot_provider = runtime_snapshot_provider
        self.prebound_socket = prebound_socket
        self.server: _ThreadingServer | None = None
        self.thread: threading.Thread | None = None
        self.token_path = data_directory / "socket-token.txt"
        self.token = secrets.token_urlsafe(32)
        self.endpoint = ""
        # True only when this process owns a requested, non-ephemeral
        # activation endpoint. Diagnostics opt into ``port=0`` explicitly and
        # must never be mistaken for the canonical desktop application.
        self.is_canonical_instance = False

    @staticmethod
    def reserve_endpoint(port: int = PRIMARY_SOCKET_PORT) -> socket.socket:
        """Exclusively reserve the activation endpoint before Backend starts.

        Keeping this listening socket open closes the launch race between the
        initial forward attempt and construction of the full Qt/backend graph.
        ``start()`` later adopts the same socket; no second bind is involved.
        """

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
            listener.bind(("127.0.0.1", int(port)))
            listener.listen(socket.SOMAXCONN)
            return listener
        except Exception:
            listener.close()
            raise

    def _persist_token(self) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True)
        # A new token is issued for every application session.  Possession of
        # yesterday's local token must never authorize today's socket client.
        self.token_path.write_text(self.token, "utf-8")
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass

    def start(self) -> None:
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                for raw in self.rfile:
                    try:
                        request = json.loads(raw.decode("utf-8"))
                        response = owner.dispatch(request)
                    except Exception as exc:
                        response = {"id": None, "ok": False, "error": str(exc)}
                    try:
                        self.wfile.write(
                            json.dumps(response, ensure_ascii=False).encode("utf-8")
                            + b"\n"
                        )
                    except OSError:
                        # A competing launcher can time out while this socket
                        # is still only a pre-bound backlog.  Its stale request
                        # is harmless once Backend adopts the listener; do not
                        # turn the already-disconnected client into a noisy
                        # worker traceback or terminate the service.
                        return

        if self.prebound_socket is not None:
            prebound = self.prebound_socket
            self.prebound_socket = None
            self.server = _ThreadingServer(
                ("127.0.0.1", int(prebound.getsockname()[1])),
                Handler,
                bind_and_activate=False,
            )
            self.server.socket.close()
            self.server.socket = prebound
            self.server.server_address = prebound.getsockname()
            self.server.server_name = str(self.server.server_address[0])
            self.server.server_port = int(self.server.server_address[1])
        else:
            # No implicit random-port fallback: a process that cannot own its
            # requested endpoint is not allowed to create another desktop UI.
            self.server = _ThreadingServer(("127.0.0.1", self.port), Handler)
        actual = int(self.server.server_address[1])
        self.endpoint = f"tcp://127.0.0.1:{actual}"
        self.is_canonical_instance = self.port != 0 and actual == int(self.port)
        try:
            self._persist_token()
        except OSError:
            self.server.server_close()
            self.server = None
            self.endpoint = ""
            self.is_canonical_instance = False
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, name="lilies-local-socket", daemon=True)
        self.thread.start()

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        if not secrets.compare_digest(str(request.get("auth", "")), self.token):
            return {"id": request_id, "ok": False, "error": "unauthorized"}
        method = request.get("method")
        try:
            if method == "system.ping":
                result = {"service": "lilies-in-the-box", "transport": "loopback", "authenticated": True}
            elif method == "system.runtime_snapshot":
                params = request.get("params")
                if not isinstance(params, dict) or params:
                    raise ValueError("system.runtime_snapshot requires params={}")
                if self.runtime_snapshot_provider is None:
                    raise RuntimeError("runtime snapshot is unavailable")
                # This path deliberately bypasses ComponentRegistry: it is an
                # authenticated, fixed-schema observation and must not create
                # a component audit record or accept caller-selected fields.
                result = _runtime_snapshot_result(self.runtime_snapshot_provider())
            elif method == "system.activate":
                params = request.get("params")
                if not isinstance(params, dict) or set(params) != {"action"}:
                    raise ValueError("system.activate requires only the action field")
                action = str(params.get("action", "")).strip().casefold()
                if action not in ACTIVATION_ACTIONS:
                    raise ValueError("unsupported activation action")
                if self.activation_sink is None:
                    result = _activation_failure(
                        action,
                        "instance activation is unavailable",
                        accepted=False,
                    )
                else:
                    # The sink owns the bounded worker-to-Qt handshake.  It
                    # reports either the main-thread outcome or an explicit
                    # ``pending`` acknowledgement when a synchronous scene
                    # transition takes longer than the handshake budget.
                    try:
                        completion = self.activation_sink(action)
                    except Exception as exc:
                        completion = {
                            "accepted": True,
                            "applied": False,
                            "mode": "",
                            "surfaceDisposition": "not-applied",
                            "error": str(exc),
                        }
                    result = _activation_result(action, completion)
            elif method == "components.list":
                result = self.registry.list()
            elif method == "components.invoke":
                params = request.get("params") or {}
                result = self.registry.invoke(
                    str(params.get("componentId", "")),
                    str(params.get("actionId", "")),
                    params.get("payload") or {},
                    origin="socket",
                    # A socket caller cannot forge a UI confirmation bit.
                    confirmed=False,
                )
            else:
                raise KeyError(f"unsupported method: {method}")
            return {"id": request_id, "ok": True, "result": result}
        except ConfirmationRequired as exc:
            return {
                "id": request_id,
                "ok": False,
                "confirmationRequired": True,
                "risk": exc.risk.value,
                "error": exc.reason,
            }
        except Exception as exc:
            return {"id": request_id, "ok": False, "error": str(exc)}

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        elif self.prebound_socket is not None:
            self.prebound_socket.close()
            self.prebound_socket = None


def request_existing_instance(
    data_directory: Path,
    action: str,
    *,
    port: int = PRIMARY_SOCKET_PORT,
    timeout: float = 1.0,
) -> bool:
    """Ask the canonical local instance to activate, without starting Backend.

    Only a response carrying the exact fixed completion schema is accepted.
    A completed mode switch must report the requested durable mode; an explicit
    ``pending`` ACK instead proves that the canonical instance owns the exact
    queued action, so the launcher must not retry it or construct a competitor.
    A completed ``show`` may be privacy-suppressed: that still proves the
    authenticated canonical instance owns the request and has queued the
    surface for replay when the protected/full-screen state ends.  Returning
    false in that case makes the launcher attempt a second instance and show a
    misleading port-ownership warning.
    Diagnostics use an explicitly requested ephemeral port and are never found
    through this helper.
    """

    normalized = str(action or "").strip().casefold()
    if normalized not in ACTIVATION_ACTIONS:
        raise ValueError("unsupported activation action")
    token_path = Path(data_directory) / "socket-token.txt"
    try:
        token = token_path.read_text("utf-8").strip()
    except OSError:
        return False
    if not token or len(token) > 256:
        return False
    request = {
        "id": secrets.token_hex(8),
        "auth": token,
        "method": "system.activate",
        "params": {"action": normalized},
    }
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as client:
            client.settimeout(timeout)
            client.sendall(json.dumps(request).encode("utf-8") + b"\n")
            with client.makefile("rb") as response_stream:
                response_line = response_stream.readline(65537)
    except (OSError, TimeoutError):
        return False
    if not response_line or len(response_line) > 65536:
        return False
    try:
        response = json.loads(response_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(response, dict):
        return False
    result = response.get("result")
    if not (
        response.get("id") == request["id"]
        and response.get("ok") is True
        and isinstance(result, dict)
        and set(result) == _ACTIVATION_RESPONSE_FIELDS
        and result.get("service") == "lilies-in-the-box"
        and result.get("action") == normalized
        and result.get("accepted") is True
        and isinstance(result.get("applied"), bool)
        and isinstance(result.get("mode"), str)
        and result.get("mode") in _ACTIVATION_SHELL_MODES
        and result.get("surfaceDisposition") in ACTIVATION_SURFACE_DISPOSITIONS
        and result.get("error") == ""
    ):
        return False
    # A pending response is a positive ownership/queue acknowledgement, not a
    # claim that the old mode already matches.  The canonical Qt thread keeps
    # the same request alive and applies it once the current synchronous QML
    # work yields.  Treating this as failure would make launchers retry the
    # action or construct a competing desktop while the first request is still
    # in flight.
    if result.get("surfaceDisposition") == "pending":
        return result.get("applied") is False
    if result.get("applied") is not True:
        return False
    if normalized in {"visual", "compact"}:
        return bool(result.get("mode") == normalized)
    return bool(
        result.get("surfaceDisposition") in {"shown", "privacy-suppressed"}
    )
