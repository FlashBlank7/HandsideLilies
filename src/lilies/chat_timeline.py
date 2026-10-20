from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QTimer, Qt, Signal


class ChatTimeline(QAbstractListModel):
    """Keep completed messages immutable while coalescing the streaming tail."""

    updated = Signal()
    ROLE = int(Qt.ItemDataRole.UserRole) + 1
    TEXT = ROLE + 1
    MESSAGE_ID = TEXT + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict[str, str]] = []
        self._pending: list[str] = []
        self._plain_text_cache: str | None = None
        self._stream_paused = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.flush)

    def roleNames(self):
        return {self.ROLE: b"roleName", self.TEXT: b"messageText", self.MESSAGE_ID: b"messageId"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == self.MESSAGE_ID:
            return row.get("message_id", "")
        return row.get("role" if role == self.ROLE else "content") if role in {
            self.ROLE, self.TEXT, Qt.ItemDataRole.DisplayRole
        } else None

    def plain_text(self) -> str:
        """Lazy compatibility snapshot; reading never flushes or emits signals.

        QML consumes individual rows. Keep only that representation on the
        streaming hot path; a legacy full-text reader pays for formatting only
        when it actually asks. Pending tail text is included without publishing
        a layout update, and unchanged reads reuse the same snapshot.
        """
        if self._plain_text_cache is None:
            self._plain_text_cache = "\n\n".join(
                ("你：" if row["role"] == "user" else "莉莉丝：") + row["content"]
                for row in self._rows
            ) + "".join(self._pending)
        return self._plain_text_cache

    def reset_messages(self, messages=()):
        self._timer.stop()
        self._pending.clear()
        self.beginResetModel()
        self._rows = [dict(role=m["role"], content=m["content"], message_id=m.get("message_id", "")) for m in messages
                      if m.get("role") in {"user", "assistant"}]
        self._plain_text_cache = None
        self.endResetModel()
        self.updated.emit()

    def bind_message_ids(self, messages):
        """Bind committed source identity without resetting layout or moving scroll."""
        saved = [m for m in messages if m.get("role") in {"user", "assistant"}]
        # The database returns a bounded tail, while a long live conversation
        # can keep more rows on screen. Align that tail, not the entire history.
        count = min(len(saved), len(self._rows))
        if not count: return
        first = len(self._rows) - count
        for index, (row, source) in enumerate(zip(self._rows[-count:], saved[-count:]), first):
            if row["role"] == source["role"] and row["content"] == source["content"]:
                row["message_id"] = source.get("message_id", "")
                item = self.index(index)
                self.dataChanged.emit(item, item, [self.MESSAGE_ID])

    def begin_turn(self, text: str):
        self.flush(force=True)
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + 1)
        self._rows.extend([{"role": "user", "content": text},
                           {"role": "assistant", "content": ""}])
        self._plain_text_cache = None
        self.endInsertRows()
        self.updated.emit()

    def begin_retry(self):
        self.flush(force=True)
        if self._rows and self._rows[-1]["role"] == "assistant":
            self._rows[-1]["content"] = ""
            self._plain_text_cache = None
            tail = self.index(len(self._rows) - 1)
            self.dataChanged.emit(tail, tail, [self.TEXT])
        elif self._rows and self._rows[-1]["role"] == "user":
            index = len(self._rows)
            self.beginInsertRows(QModelIndex(), index, index)
            self._rows.append({"role": "assistant", "content": ""})
            self._plain_text_cache = None
            self.endInsertRows()
        self.updated.emit()

    def append_chunk(self, text: str):
        if not self._rows or not text:
            return
        self._pending.append(text)
        self._plain_text_cache = None
        if not self._stream_paused and not self._timer.isActive():
            self._timer.start()

    def set_stream_paused(self, paused: bool) -> None:
        """Defer periodic text layout while the pet owns a held pointer.

        This only pauses presentation, never generation or persistence. Keep
        all deltas and the lazy text snapshot. On release arm one normal tick
        rather than doing a text-document layout inside the release callback.
        Explicit turn/reset/terminal commits retain their immediate semantics.
        """
        self._stream_paused = bool(paused)
        if self._stream_paused:
            self._timer.stop()
        elif self._pending and not self._timer.isActive():
            self._timer.start()

    def flush(self, *, force: bool = False):
        self._timer.stop()
        # A timeout may already be queued when a second gesture stops it.
        if self._stream_paused and not force:
            return
        if not self._pending or not self._rows:
            return
        self._rows[-1]["content"] += "".join(self._pending)
        self._pending.clear()
        tail = self.index(len(self._rows) - 1)
        self.dataChanged.emit(tail, tail, [self.TEXT])
        self.updated.emit()

    def finish(self, authoritative_text: str = ""):
        self.flush(force=True)
        if (authoritative_text and self._rows
                and self._rows[-1]["role"] == "assistant"
                and self._rows[-1]["content"] != authoritative_text):
            self._rows[-1]["content"] = authoritative_text
            self._plain_text_cache = None
            tail = self.index(len(self._rows) - 1)
            self.dataChanged.emit(tail, tail, [self.TEXT])
            self.updated.emit()
