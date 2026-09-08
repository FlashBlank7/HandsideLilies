from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QTimer, Qt, Signal


class ChatTimeline(QAbstractListModel):
    """Keep completed messages immutable while coalescing the streaming tail."""

    updated = Signal()
    ROLE = int(Qt.ItemDataRole.UserRole) + 1
    TEXT = ROLE + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict[str, str]] = []
        self._pending: list[str] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.flush)

    def roleNames(self):
        return {self.ROLE: b"roleName", self.TEXT: b"messageText"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        return row.get("role" if role == self.ROLE else "content") if role in {
            self.ROLE, self.TEXT, Qt.ItemDataRole.DisplayRole
        } else None

    def reset_messages(self, messages=()):
        self._timer.stop()
        self._pending.clear()
        self.beginResetModel()
        self._rows = [dict(role=m["role"], content=m["content"]) for m in messages
                      if m.get("role") in {"user", "assistant"}]
        self.endResetModel()
        self.updated.emit()

    def begin_turn(self, text: str):
        self.flush()
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + 1)
        self._rows.extend([{"role": "user", "content": text},
                           {"role": "assistant", "content": ""}])
        self.endInsertRows()
        self.updated.emit()

    def append_chunk(self, text: str):
        if not self._rows or not text:
            return
        self._pending.append(text)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self):
        self._timer.stop()
        if not self._pending or not self._rows:
            return
        self._rows[-1]["content"] += "".join(self._pending)
        self._pending.clear()
        tail = self.index(len(self._rows) - 1)
        self.dataChanged.emit(tail, tail, [self.TEXT])
        self.updated.emit()

    def finish(self, authoritative_text: str = ""):
        self.flush()
        if (authoritative_text and self._rows
                and self._rows[-1]["role"] == "assistant"
                and self._rows[-1]["content"] != authoritative_text):
            self._rows[-1]["content"] = authoritative_text
            tail = self.index(len(self._rows) - 1)
            self.dataChanged.emit(tail, tail, [self.TEXT])
            self.updated.emit()
