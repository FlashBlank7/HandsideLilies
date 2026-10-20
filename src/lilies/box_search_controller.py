"""One coalescing search worker; no model, clipboard, screenshots or shell."""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from .core.box_search import BoxSearchService


class BoxSearchController(QObject):
    changed = Signal()
    routeRequested = Signal(str, object)
    recordsChanged = Signal()
    completedNotice = Signal(str)
    _ready = Signal(int, str, object, str)

    def __init__(self, database, desktop, reminders, *, allowed=lambda: True, parent=None):
        super().__init__(parent)
        self.service = BoxSearchService(database)
        self.desktop, self.reminders = desktop, reminders
        self.allowed = allowed
        self._state = {"open": False, "busy": False, "executing": False, "query": "", "category": "all", "items": [], "preview": {}, "error": ""}
        self._targets = {}
        self._preview = None
        self._generation = 0
        self._stopped = False
        self._pending = None
        self._condition = threading.Condition()
        self._ready.connect(self._receive, Qt.QueuedConnection)
        self._thread = threading.Thread(target=self._worker, name="lilies-box-search", daemon=True)
        self._thread.start()

    @Property("QVariantMap", notify=changed)
    def state(self): return self._state

    @Slot()
    def open(self):
        if self._stopped or not self.allowed(): return
        self._state = {**self._state, "open": True}
        self.search("", "all")

    @Slot()
    def close(self):
        self._generation += 1
        self._targets = {}
        self._preview = None
        self._state = {**self._state, "open": False, "busy": False, "executing": False, "items": [], "preview": {}, "query": "", "error": ""}
        self.changed.emit()

    def _enqueue(self, operation, payload):
        if self._stopped: return
        self._generation += 1
        self._state = {**self._state, "busy": True, "executing": operation != "search", "error": ""}
        self.changed.emit()
        with self._condition:
            self._pending = (self._generation, operation, payload)
            self._condition.notify()

    @Slot(str, str)
    def search(self, query, category="all"):
        if not self._state["open"] or not self.allowed() or self._state["executing"]: return
        self._targets = {}
        self._preview = None
        self._state = {**self._state, "query": query, "category": category, "items": [], "preview": {}}
        self._enqueue("search", (query, category))

    @Slot(str)
    def select(self, token):
        entry = self._targets.get(token)
        if self._state["busy"] or not self._state["open"] or not entry: return
        if time.monotonic() > entry["expires"]:
            self._state = {**self._state, "error": "结果已过期，请重新搜索"}
            self.changed.emit()
            return
        self._enqueue("select", entry)

    @Slot()
    def confirm(self):
        value = self._preview
        if self._state["busy"] or not self._state["open"] or not value: return
        if time.monotonic() > value["expires"]:
            self._state = {**self._state, "error": "预览已过期，请重新选择"}
            self.changed.emit()
            return
        self._preview = None  # consume before any asynchronous side effect; double Enter is inert
        self._enqueue("confirm", value)

    @Slot(str)
    def reportError(self, message):
        self._preview = None
        self._state = {**self._state, "busy": False, "executing": False, "preview": {}, "error": str(message)[:300]}
        self.changed.emit()

    def _worker(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopped or self._pending is not None)
                if self._stopped: return
                generation, operation, payload = self._pending
                self._pending = None
            current = lambda: not self._stopped and generation == self._generation
            if not current(): continue
            try:
                if not self.allowed(): raise ValueError("当前场景已暂停快搜")
                if operation == "search":
                    value = self.service.search(*payload, cancelled=lambda: not current())
                elif operation == "select":
                    data = self.service.resolve(payload["kind"], payload["target"])
                    value = {"data": data, "entry": payload, "expires": time.monotonic() + 120}
                    if data["kind"] == "app":
                        if not current(): continue
                        if not self.allowed(): raise ValueError("当前场景已暂停快搜")
                        warning = self._effect("open-app", "launch", {"itemId": data["record"]["item_id"]},
                            lambda: self.desktop.launch(data["record"]["path"]), current)
                        value = {"done": True, "notice": warning or "应用已交给系统打开"}
                else:
                    data, entry = payload["data"], payload["entry"]
                    if time.monotonic() > payload["expires"]: raise ValueError("预览已过期，请重新选择")
                    if data["kind"] != "new-reminder":
                        data = self.service.resolve(entry["kind"], entry["target"])
                    if not current(): continue
                    if not self.allowed(): raise ValueError("当前场景已暂停快搜")
                    if data["kind"] == "new-reminder":
                        if datetime_from_iso(data["dueAt"]) <= time.time(): raise ValueError("提醒时间已过，请重新预览")
                        warning = self._effect("create-reminder", "mutate", {},
                            lambda: self.reminders.create(data["title"], data["dueAt"], timezone_name=data["timezone"]), current)
                        value = {"done": True, "recordsChanged": True, "notice": warning or "提醒已创建"}
                    else: value = {"route": data}
                error = ""
            except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                value, error = {}, str(exc)[:250] if not isinstance(exc, sqlite3.Error) else "本地记录正忙，请稍后重试"
            except Exception:
                value, error = {}, "这次没有完成，请重新搜索后再试"
            if current(): self._ready.emit(generation, operation, value, error)

    def _effect(self, action, risk, metadata, callback, current):
        db = self.service.database
        audit_id = db.audit("user:box-search", "box-search", action, risk, "confirmed", metadata)
        try:
            if not current() or not self.allowed():
                raise ValueError("操作已取消")
            callback()
        except Exception as exc:
            try: db.complete_audit(audit_id, error={"type": type(exc).__name__})
            except sqlite3.Error: pass
            raise
        try:
            db.complete_audit(audit_id, result={"accepted": True})
        except sqlite3.Error:
            return "操作已执行，但结果记录暂未保存；请勿重复提交"
        return ""

    @Slot(int, str, object, str)
    def _receive(self, generation, operation, value, error):
        if self._stopped or generation != self._generation or not self._state["open"]: return
        if not self.allowed():
            self.close()
            return
        self._state = {**self._state, "busy": False, "executing": False, "error": error}
        if error:
            self._preview = None
            self._state["preview"] = {}
        if not error:
            if operation == "search":
                self._targets, items = {}, []
                for entry in value:
                    token = secrets.token_urlsafe(16)
                    self._targets[token] = {**entry, "expires": time.monotonic() + 120}
                    items.append({key: entry[key] for key in ("kind", "title", "subtitle")} | {"id": token, "hint": entry.get("hint", entry["subtitle"])})
                self._state["items"] = items
            elif value.get("done"):
                if value.get("recordsChanged"): self.recordsChanged.emit()
                if value.get("notice"): self.completedNotice.emit(value["notice"])
                self.close()
                return
            elif "route" in value:
                self.routeRequested.emit(value["route"]["kind"], value["route"])
            elif value["data"]["kind"] in {"chat", "paper"}:
                self.routeRequested.emit(value["data"]["kind"], value["data"])
            else:
                self._preview = value
                self._state["preview"] = {key: value["data"][key] for key in ("kind", "title", "detail", "action")}
        self.changed.emit()

    def shutdown(self):
        with self._condition:
            self._stopped = True
            self._pending = None
            self._condition.notify()


def datetime_from_iso(value):
    from datetime import datetime
    return datetime.fromisoformat(value).timestamp()
