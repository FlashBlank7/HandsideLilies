"""User-owned shared-world records, with all SQLite work off the GUI thread."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from .core.shared_world import SharedWorldService


class SharedWorldController(QObject):
    changed = Signal()
    notice = Signal(str)
    sourceRequested = Signal(object)
    _ready = Signal(int, str, object, str)

    def __init__(self, database, *, allowed=lambda: True, parent=None):
        super().__init__(parent)
        self.service = SharedWorldService(database)
        self._allowed = allowed
        self._state = {"open": False, "busy": False, "error": "", "detail": {},
            "page": {"items": [], "shelf": "papers", "hidden": False, "offset": 0, "total": 0, "limit": 20, "hasMore": False, "counts": {}}}
        self._generation = 0
        self._stopped = False
        self._thread = None
        self._ready.connect(self._receive, Qt.QueuedConnection)

    @Property("QVariantMap", notify=changed)
    def state(self): return self._state

    def _run(self, action, callback):
        if self._stopped or not self._allowed():
            self.close()
            return
        if self._state["busy"]:
            self.notice.emit("正在收好上一份记录，稍等一下")
            return
        self._generation += 1
        generation = self._generation
        self._state = {**self._state, "busy": True, "error": ""}
        self.changed.emit()
        def work():
            try: result, error = callback(), ""
            except (ValueError, KeyError) as exc: result, error = {}, str(exc)[:250]
            except Exception: result, error = {}, "本地记录暂时未能打开，请稍后重试"
            if not self._stopped: self._ready.emit(generation, action, result, error)
        self._thread = threading.Thread(target=work, name="lilies-shared-world", daemon=True)
        self._thread.start()

    @Slot(str, bool, int)
    def browse(self, shelf="papers", hidden=False, offset=0):
        if self._state["busy"] or self._stopped or not self._allowed(): return
        self._state = {**self._state, "open": True, "detail": {}}
        def fetch():
            self.service.sync()
            return self.service.page(shelf, hidden, offset)
        self._run("browse", fetch)

    @Slot(str)
    def inspect(self, identifier):
        self._run("inspect", lambda: self.service.detail(identifier))

    @Slot()
    def refreshShown(self):
        # Returning from another window must re-read the original, including
        # deletion or a reopened task, instead of reviving a cached keepsake.
        if not self._state["open"] or self._state["busy"]: return
        detail = self._state["detail"]
        if detail.get("id"): self.inspect(detail["id"])
        else:
            page = self._state["page"]
            self.browse(page["shelf"], page["hidden"], page["offset"])

    @Slot(str, str)
    def saveLetter(self, message_id, expected_text):
        self._run("save", lambda: self.service.save_letter(message_id, expected_text))

    @Slot(str, str)
    def changeVisibility(self, identifier, visibility):
        page = dict(self._state["page"])
        def change():
            self.service.set_visibility(identifier, visibility)
            return self.service.page(page["shelf"], page["hidden"], page["offset"])
        self._run("change", change)

    @Slot()
    def openSource(self):
        shown = dict(self._state["detail"])
        if not shown.get("route"): return
        def check():
            current = self.service.detail(shown["id"])
            if not current["available"] or current["sourceVersion"] != shown["sourceVersion"]:
                raise ValueError("原记录已变化，请重新打开这份记录")
            return current["route"]
        self._run("source", check)

    @Slot()
    def close(self):
        # Reads finishing after closure cannot re-show the window. An accepted local
        # save remains a save, and is acknowledged by notice without opening a panel.
        self._state = {**self._state, "open": False, "detail": {}, "error": "",
            "page": {**self._state["page"], "items": [], "counts": {}, "total": 0}}
        self.changed.emit()

    @Slot(str)
    def reportError(self, error):
        self._state = {**self._state, "error": str(error)[:250]}
        self.changed.emit()

    @Slot(int, str, object, str)
    def _receive(self, generation, action, result, error):
        if self._stopped or generation != self._generation: return
        self._state = {**self._state, "busy": False, "error": error}
        if not self._allowed():
            self.close()
            return
        if error:
            if action == "inspect": self._state["detail"] = {}
            self.notice.emit(error)
        elif action == "save":
            self.notice.emit("这句话收进小信笺了")
        elif self._state["open"]:
            if action in {"browse", "change"}:
                self._state = {**self._state, "page": result, "detail": {}}
            elif action == "inspect": self._state["detail"] = result
            elif action == "source": self.sourceRequested.emit(result)
        self.changed.emit()

    def shutdown(self): self._stopped = True
