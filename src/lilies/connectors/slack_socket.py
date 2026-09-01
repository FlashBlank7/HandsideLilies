from __future__ import annotations

"""Qt-facing Slack Socket Mode lifecycle with bounded reconnects.

The RFC 6455 transport lives in a plain Python worker.  It acknowledges Slack
envelopes on that worker before this QObject receives their text, so database
work and Qt delivery can never delay the protocol ACK.
"""

import json
import random
import threading
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .runtime import SlackRuntime
from .slack_websocket import SlackWebSocketTransport


class SlackSocketService(QObject):
    statusChanged = Signal(object)
    itemReceived = Signal(object)
    _urlReady = Signal(str, str)
    _transportOpened = Signal(str)
    _transportText = Signal(str, str)
    _transportClosed = Signal(str, object, str)
    _transportError = Signal(str, str)

    def __init__(
        self,
        runtime: SlackRuntime,
        parent: QObject | None = None,
        *,
        transport_factory: Any = SlackWebSocketTransport,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._transport_factory = transport_factory
        self.transport: SlackWebSocketTransport | Any | None = None
        self._transport_error = ""
        self._urlReady.connect(self._on_url_ready)
        self._transportOpened.connect(self._on_transport_opened)
        self._transportText.connect(self._on_transport_text)
        self._transportClosed.connect(self._on_transport_closed)
        self._transportError.connect(self._on_transport_error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._request_url)
        self._enabled = False
        self._requesting = False
        self._intentional_stop = False
        self._attempt = 0
        self._generation = 0
        self._closed_generation: int | None = None
        self._state: dict[str, Any] = {
            "state": "stopped",
            "connected": False,
            "attempt": 0,
            "error": "",
        }

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._state)

    def start(self) -> None:
        if self._enabled:
            return
        self._enabled = True
        self._intentional_stop = False
        self._attempt = 0
        self._schedule(0)

    def stop(self) -> None:
        self._enabled = False
        self._intentional_stop = True
        self._generation += 1
        self._closed_generation = self._generation
        self._requesting = False
        self._timer.stop()
        transport = self.transport
        self.transport = None
        self._transport_error = ""
        if transport is not None:
            transport.close(wait=False)
        self._set_state("stopped", connected=False, error="")

    def _schedule(self, milliseconds: int | None = None) -> None:
        if not self._enabled:
            return
        if milliseconds is None:
            base = min(60_000, 1000 * (2 ** min(self._attempt, 6)))
            milliseconds = int(base * random.uniform(0.8, 1.2))
        self._timer.start(max(0, int(milliseconds)))

    def _request_url(self) -> None:
        if not self._enabled or self._requesting:
            return
        self._requesting = True
        self._generation += 1
        generation = self._generation
        self._closed_generation = None
        self._set_state("connecting", connected=False, error="")

        def worker() -> None:
            try:
                url = self.runtime.socket_url()
                self._urlReady.emit(str(generation), url)
            except Exception as exc:  # credential/network boundary
                self._urlReady.emit(str(generation), "error:" + str(exc))

        threading.Thread(
            target=worker,
            name="lilies-slack-socket-url",
            daemon=True,
        ).start()

    def _on_url_ready(self, generation_text: str, value: str) -> None:
        try:
            generation = int(generation_text)
        except ValueError:
            return
        if generation != self._generation or not self._enabled:
            return
        self._requesting = False
        if value.startswith("error:"):
            self._attempt += 1
            self._set_state("reconnecting", connected=False, error=value[6:])
            self._schedule()
            return
        if not value.startswith("wss://"):
            self._attempt += 1
            self._set_state("reconnecting", connected=False, error="Slack returned a non-WSS URL")
            self._schedule()
            return
        transport: SlackWebSocketTransport | Any | None = None
        previous = self.transport
        self.transport = None
        try:
            if previous is not None:
                previous.close(wait=False)
            self._transport_error = ""
            transport = self._transport_factory(
                on_open=lambda: self._transportOpened.emit(str(generation)),
                on_text=lambda raw: self._transportText.emit(
                    str(generation), str(raw)
                ),
                on_close=lambda code, reason: self._transportClosed.emit(
                    str(generation), code, str(reason)
                ),
                on_error=lambda message: self._transportError.emit(
                    str(generation), str(message)
                ),
            )
            self.transport = transport
            transport.start(value)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if self.transport is transport:
                self.transport = None
            self._attempt += 1
            self._set_state("reconnecting", connected=False, error=str(exc)[:500])
            self._schedule()

    def _decode_generation(self, generation_text: str) -> int | None:
        try:
            return int(generation_text)
        except (TypeError, ValueError):
            return None

    def _on_transport_opened(self, generation_text: str) -> None:
        generation = self._decode_generation(generation_text)
        if generation != self._generation or not self._enabled:
            return
        self._attempt = 0
        self._transport_error = ""
        self._set_state("connected", connected=True, error="")

    def _on_transport_error(self, generation_text: str, message: str) -> None:
        generation = self._decode_generation(generation_text)
        if generation != self._generation or not self._enabled:
            return
        self._transport_error = str(message)[:500]
        self._set_state(
            "reconnecting",
            connected=False,
            error=self._transport_error or "WebSocket transport failed",
        )

    def _on_transport_closed(
        self, generation_text: str, _code: object, reason: str
    ) -> None:
        generation = self._decode_generation(generation_text)
        if generation != self._generation:
            return
        if self._closed_generation == generation:
            return
        self._closed_generation = generation
        self.transport = None
        if self._intentional_stop or not self._enabled:
            return
        self._attempt += 1
        error = self._transport_error or str(reason)[:500] or "connection closed"
        self._set_state(
            "reconnecting",
            connected=False,
            error=error,
        )
        self._schedule()

    def _on_transport_text(self, generation_text: str, raw: str) -> None:
        generation = self._decode_generation(generation_text)
        if generation != self._generation or not self._enabled:
            return
        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(envelope, dict):
            return
        if envelope.get("type") == "disconnect":
            transport = self.transport
            if transport is not None:
                transport.close(
                    code=1000,
                    reason="Slack requested reconnect",
                    wait=False,
                )
            return
        if envelope.get("type") != "events_api":
            return
        try:
            item = self.runtime.ingest(envelope)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._set_state("connected", connected=True, error=str(exc)[:500])
            return
        if item is not None:
            self.itemReceived.emit(item)

    def _set_state(self, state: str, *, connected: bool, error: str) -> None:
        next_value = {
            "state": state,
            "connected": bool(connected),
            "attempt": int(self._attempt),
            "error": str(error),
        }
        if next_value != self._state:
            self._state = next_value
            self.statusChanged.emit(dict(next_value))


__all__ = ["SlackSocketService"]
