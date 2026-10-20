"""Qt boundary for explicit work bookmarks. Native work stays off the Qt thread."""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt
from PySide6.QtGui import QGuiApplication

from .core.desktop_peek import NativeDesktopWindowApi
from .core.work_resumption import WorkResumptionService


class EmptyWindowApi(NativeDesktopWindowApi):
    def list_handles(self):
        return []

    def restore(self, handle, placement):
        return False


class WorkResumptionController(QObject):
    changed = Signal()
    completed = Signal(str)
    _finished = Signal(str, object, str)

    def __init__(self, database, task_get, *, window_lookup=lambda handle: None, smoke=False, parent=None):
        super().__init__(parent)
        self._state: dict[str, Any] = {"busy": False, "error": "", "draft": {}, "preview": {},
            "result": {}, "page": {"items": [], "total": 0, "offset": 0, "hasMore": False}}
        self._layout = ""
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self.service = WorkResumptionService(database, api=EmptyWindowApi() if smoke else NativeDesktopWindowApi(),
            opener=(lambda item: False) if smoke else self._open, layout=lambda: self._layout, task_get=task_get,
            should_continue=lambda: not self._closed.is_set(),
            window_label=lambda handle: str(getattr(window_lookup(handle), "title", "")))
        self._finished.connect(self._receive, Qt.QueuedConnection)
        application = QGuiApplication.instance()
        if application:
            application.screenAdded.connect(self._watch_screen)
            application.screenRemoved.connect(self._layout_snapshot)
            for screen in QGuiApplication.screens():
                self._watch_screen(screen)

    def _watch_screen(self, screen):
        screen.geometryChanged.connect(self._layout_snapshot)
        screen.availableGeometryChanged.connect(self._layout_snapshot)
        screen.logicalDotsPerInchChanged.connect(self._layout_snapshot)
        screen.physicalDotsPerInchChanged.connect(self._layout_snapshot)
        self._layout_snapshot()

    def _open(self, item: dict) -> bool:
        if self._closed.is_set():
            return False
        os.startfile(item["source"])
        return True

    @Property("QVariantMap", notify=changed)
    def state(self):
        return self._state

    def _layout_snapshot(self) -> None:
        # Called on Qt's GUI thread, then read as an immutable string by the worker.
        self._layout = json.dumps(sorted((s.name(), s.geometry().x(), s.geometry().y(), s.geometry().width(),
            s.geometry().height(), s.availableGeometry().x(), s.availableGeometry().y(),
            s.availableGeometry().width(), s.availableGeometry().height(), s.devicePixelRatio(),
            round(s.logicalDotsPerInch(), 2)) for s in QGuiApplication.screens()))

    def _run(self, operation: str, callback) -> None:
        if self._closed.is_set() or self._state["busy"]:
            return
        self._layout_snapshot()
        self._state = {**self._state, "busy": True, "error": ""}
        offset = int(self._state["page"]["offset"])
        self.changed.emit()

        def execute():
            try:
                value = callback()
                page, warning = None, ""
                if operation in {"save", "confirm", "delete"}:
                    try:
                        # Even a tiny query can wait on SQLite's lock. Keep it
                        # off the Qt thread along with the native work.
                        page = self.service.list(offset)
                    except Exception:
                        warning = "操作已处理，但记录列表暂未刷新。请重新打开记录。"
                result, error = {"value": value, "page": page, "warning": warning}, ""
            except (ValueError, OSError) as exc:
                result, error = {}, str(exc)[:300]
            except Exception:
                result, error = {}, "这次没有完成，原记录和资料未删除。可以重新打开再试。"
            if not self._closed.is_set():
                self._finished.emit(operation, result, error)

        self._thread = threading.Thread(target=execute, name="lilies-work-resumption", daemon=True)
        self._thread.start()

    @Slot(str, object, str)
    def _receive(self, operation, result, error):
        if self._closed.is_set():
            return
        self._state = {**self._state, "busy": False, "error": error}
        if not error:
            packet = result
            result = packet["value"]
            self._state["error"] = packet["warning"]
            if operation in {"capture", "refreshCapture", "preview", "refresh"}:
                self._state[{"capture": "draft", "refreshCapture": "draft", "preview": "preview", "refresh": "page"}[operation]] = result
            else:
                self._state["result"] = result
                self._state["draft"] = self._state["preview"] = {}
                if packet["page"] is not None:
                    self._state["page"] = packet["page"]
        self.changed.emit()
        if not error:
            self.completed.emit(operation)

    @Slot(int)
    def refresh(self, offset=0):
        self._run("refresh", lambda: self.service.list(offset))

    @Slot()
    def capture(self):
        self._run("capture", self.service.capture)

    @Slot()
    def refreshCapture(self):
        self._run("refreshCapture", self.service.capture)

    @Slot(str)
    def preview(self, record_id):
        self._run("preview", lambda: self.service.preview(record_id))

    @Slot("QVariantMap")
    def save(self, payload):
        value = dict(payload)
        self._run("save", lambda: self.service.save(value.get("captureId", ""), title=value.get("title", ""),
            next_step=value.get("nextStep", ""), task_id=value.get("taskId", ""),
            sources=value.get("sources", []), window_ids=value.get("windowIds", [])))

    @Slot(str, "QVariantList", "QVariantList")
    def confirm(self, token, window_ids, resource_ids):
        self._run("confirm", lambda: self.service.confirm(token, window_ids=window_ids, resource_ids=resource_ids))

    @Slot(str)
    def delete(self, record_id):
        self._run("delete", lambda: self.service.delete(record_id) or {})

    @Slot()
    def cancel(self):
        if not self._state["busy"]:
            self.service.cancel()
            self._state = {**self._state, "draft": {}, "preview": {}, "error": ""}
            self.changed.emit()

    def shutdown(self):
        self._closed.set()
        # No new effects after shutdown; an already submitted native request may
        # finish, but is never repeated on restart. Do not block the Qt thread.
