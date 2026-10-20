"""Coalesce worker text before posting GUI events; no timer, I/O or truncation."""
from __future__ import annotations

from collections.abc import Callable
import threading

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class ChatDelivery(QObject):
    """One queued wake per pending batch, irrespective of its delta count.

    enqueue is a direct, thread-safe receiver: it never touches the timeline or
    any window. drain/flush deliver only on this object's owning GUI thread.
    Model output limits remain with the producers; this buffer never silently
    drops/truncates text. Closing explicitly discards undelivered presentation
    only; it does not cancel generation or change persisted conversation data.
    """

    _ready = Signal(object)

    def __init__(self, deliver: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._deliver = deliver
        self._lock = threading.Lock()
        self._parts: list[str] = []
        self._ticket = 0
        self._notified = False
        self._closed = False
        self._ready.connect(self._drain, Qt.ConnectionType.QueuedConnection)

    @Slot(str)
    def enqueue(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            if self._closed:
                return
            self._parts.append(text)
            if not self._notified:
                self._notified = True
                self._ticket += 1
                # Forced queued connection: no GUI callback runs under this
                # lock, even for a same-thread producer. Emitting before unlock
                # fences close() and prevents an emit after owner destruction.
                self._ready.emit(self._ticket)

    def _assert_owner(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Chat presentation must run on its owning thread")

    @Slot(object)
    def _drain(self, ticket) -> None:
        self._consume(ticket)

    def _consume(self, ticket=None) -> None:
        self._assert_owner()
        with self._lock:
            if self._closed or (ticket is not None and ticket != self._ticket):
                return
            parts, self._parts = self._parts, []
            self._notified = False
        # A new producer may enqueue while this batch is delivered. Its newer
        # ticket makes any old wake left by explicit flush a no-op, not a way to
        # consume/reorder a later turn. No lock is held while calling Qt/model.
        if parts:
            self._deliver("".join(parts))

    def flush(self) -> None:
        """Publish pending chunks before an error or terminal result."""
        self._consume()

    def close(self) -> None:
        self._assert_owner()
        with self._lock:
            self._closed = True
            self._parts.clear()
            self._notified = False
            self._ticket += 1
        self._deliver = None
