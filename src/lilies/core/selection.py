from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from ctypes import wintypes
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication

from .codex_subscription import CodexSubscriptionClient
from .database import Database
from .model import _BrokerTaskLease
from .orchestration import ModelTaskBroker, ModelTaskKind
from .reading import (
    ReadingRequest,
    prepare_reading_request,
    reading_bubble_metadata,
    reading_card_title,
)


MAX_SELECTION_LENGTH = 5000
LUNA_MODEL_NAME = "gpt-5.6-luna"
_BROKER_CANCELLED = "__broker_cancelled__"
READING_PROCESSES = {
    "acrobat.exe",
    "acrord32.exe",
    "arc.exe",
    "brave.exe",
    "cajviewer.exe",
    "chrome.exe",
    "endnote.exe",
    "firefox.exe",
    "foxitpdfreader.exe",
    "foxitreader.exe",
    "mendeleydesktop.exe",
    "mendeleyreferencemanager.exe",
    "msedge.exe",
    "nitropdf.exe",
    "notepad.exe",
    "notepad++.exe",
    "obsidian.exe",
    "onenote.exe",
    "opera.exe",
    "pdfxedit.exe",
    "pdfxview.exe",
    "sumatrapdf.exe",
    "typora.exe",
    "vivaldi.exe",
    "winword.exe",
    "wps.exe",
    "wpspdf.exe",
    "zotero.exe",
}


@lru_cache(maxsize=1)
def _user32():
    library = ctypes.WinDLL("User32.dll", use_last_error=True)
    library.GetAsyncKeyState.argtypes = [ctypes.c_int]
    library.GetAsyncKeyState.restype = ctypes.c_short
    library.GetForegroundWindow.argtypes = []
    library.GetForegroundWindow.restype = wintypes.HWND
    library.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    library.GetWindowThreadProcessId.restype = wintypes.DWORD
    library.GetClipboardSequenceNumber.argtypes = []
    library.GetClipboardSequenceNumber.restype = wintypes.DWORD
    library.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    library.keybd_event.restype = None
    return library


@lru_cache(maxsize=1)
def _kernel32():
    library = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    library.OpenProcess.restype = wintypes.HANDLE
    library.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    library.QueryFullProcessImageNameW.restype = wintypes.BOOL
    library.CloseHandle.argtypes = [wintypes.HANDLE]
    library.CloseHandle.restype = wintypes.BOOL
    return library


class SelectionService(QObject):
    bubbleChanged = Signal(object)
    settingsChanged = Signal()
    statusChanged = Signal(str)
    _requestFinished = Signal(int, str, str)

    def __init__(
        self,
        database: Database,
        active: bool = True,
        model_broker: ModelTaskBroker | None = None,
    ) -> None:
        super().__init__()
        self.database = database
        self._model_broker = model_broker
        self._active_broker_task_id = ""
        self._active = active and os.name == "nt"
        self._enabled = bool(database.get_setting("selection_monitor_enabled", True))
        self._interaction_suspended = False
        self._subscription = CodexSubscriptionClient(database.path.parent / "codex-selection")
        self._bubble: dict[str, Any] = {"visible": False, "text": "", "busy": False, "x": 0, "y": 0}
        self._request_id = 0
        self._last_text = ""
        self._last_text_at = 0.0
        self._source_app = ""
        self._current_request: ReadingRequest | None = None
        self._current_answer = ""
        self._current_saved_card_id = ""
        self._pending_requests: dict[int, ReadingRequest] = {}
        self._mouse_down = False
        self._press_point = (0, 0)
        self._press_window = 0
        self._press_time = 0.0
        self._capture_id = 0
        self._saved_mime: QMimeData | None = None
        self._saved_had_data = False
        self._copy_sequence = 0
        self._copy_attempts = 0
        self._cursor_point = (0, 0)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll_mouse)
        self._subscription_idle = QTimer(self)
        self._subscription_idle.setSingleShot(True)
        self._subscription_idle.setInterval(300_000)
        self._subscription_idle.timeout.connect(self._subscription.stop)
        self._requestFinished.connect(self._finish_request)
        self._refresh_monitor()

    @property
    def bubble(self) -> dict[str, Any]:
        return dict(self._bubble)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def subscription_ready(self) -> bool:
        return self._subscription.ready

    @property
    def status(self) -> str:
        if not self._active:
            return "当前环境未启用全局划词监听"
        if not self._enabled:
            return "已暂停"
        if not self._subscription.available:
            return "未检测到 Codex 运行时"
        if not self._subscription.signed_in:
            return "请先在 ChatGPT/Codex 中登录订阅"
        plan = self._subscription.plan_type.upper() if self._subscription.plan_type else "GPT 订阅"
        return f"监听中 · {plan} · Luna-medium · 无上下文"

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        self.database.set_setting("selection_monitor_enabled", self._enabled)
        self._refresh_monitor()
        self.settingsChanged.emit()
        self.statusChanged.emit(self.status)

    def refresh_subscription(self) -> None:
        self._subscription.refresh()
        self._refresh_monitor()
        self.settingsChanged.emit()
        self.statusChanged.emit(self.status)

    def ensure_monitor(self) -> None:
        """Re-arm global selection polling after a desktop mode transition."""

        self._refresh_monitor()
        self.settingsChanged.emit()
        self.statusChanged.emit(self.status)

    def set_interaction_suspended(self, suspended: bool) -> None:
        """Keep global selection polling out of pointer-critical gestures."""

        value = bool(suspended)
        if value == self._interaction_suspended:
            return
        self._interaction_suspended = value
        # A desktop-pet drag must never be interpreted as a WPS/PDF text
        # selection when the button is released over the reading window.
        self._mouse_down = False
        self._capture_id += 1
        self._refresh_monitor()

    def dismiss(self) -> None:
        self._cancel_active_broker_task("selection-dismissed")
        self._request_id += 1
        self._bubble = {**self._bubble, "visible": False, "busy": False}
        self.bubbleChanged.emit(self.bubble)

    def request_action(self, action: str, question: str = "") -> None:
        """Run one isolated action over the current selection.

        No prior answer, chat message, or memory is supplied to the model.  An
        ``ask`` request contains only the selected source and this call's
        question.
        """

        source = self._current_request.source_text if self._current_request else self._last_text
        try:
            request = prepare_reading_request(source, action, question)
        except ValueError as exc:
            self._bubble = {
                **self._bubble,
                "visible": True,
                "text": str(exc),
                "busy": False,
                "error": True,
            }
            self.bubbleChanged.emit(self.bubble)
            return
        x = int(self._bubble.get("x", self._cursor_point[0]))
        y = int(self._bubble.get("y", self._cursor_point[1]))
        self._start_request(request, x, y)

    def save_current_card(self) -> str:
        """Save the visible successful result and return its stable card id."""

        request = self._current_request
        answer = self._current_answer.strip()
        if request is None or not answer or self._bubble.get("busy") or self._bubble.get("error"):
            return ""
        if self._current_saved_card_id:
            return self._current_saved_card_id
        card_id = self._save_card(request, answer)
        self._current_saved_card_id = card_id
        self._bubble = {
            **self._bubble,
            **reading_bubble_metadata(
                request,
                answer,
                source_app=self._source_app,
                saved_card_id=card_id,
            ),
        }
        self.bubbleChanged.emit(self.bubble)
        return card_id

    def shutdown(self) -> None:
        self._timer.stop()
        self._subscription_idle.stop()
        self._cancel_active_broker_task("selection-shutdown")
        self._subscription.stop()
        self._request_id += 1
        self._capture_id += 1

    def _refresh_monitor(self) -> None:
        should_run = (
            self._active
            and self._enabled
            and self._subscription.ready
            and not self._interaction_suspended
        )
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run:
            self._timer.stop()

    @staticmethod
    def _cursor() -> tuple[int, int]:
        # Qt converts native cursor coordinates to device-independent pixels,
        # keeping the bubble aligned on high-DPI displays.
        point = QCursor.pos()
        return point.x(), point.y()

    @staticmethod
    def _foreground_process_id(window: int) -> int:
        process_id = wintypes.DWORD()
        _user32().GetWindowThreadProcessId(wintypes.HWND(window), ctypes.byref(process_id))
        return int(process_id.value)

    @staticmethod
    def _process_name(process_id: int) -> str:
        kernel32 = _kernel32()
        process = kernel32.OpenProcess(0x1000, False, process_id)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return ""
            return os.path.basename(buffer.value).casefold()
        finally:
            kernel32.CloseHandle(process)

    def _poll_mouse(self) -> None:
        user32 = _user32()
        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        if down and not self._mouse_down:
            self._mouse_down = True
            self._press_point = self._cursor()
            self._press_window = int(user32.GetForegroundWindow() or 0)
            self._press_time = time.monotonic()
            return
        if down or not self._mouse_down:
            return
        self._mouse_down = False
        release = self._cursor()
        elapsed = time.monotonic() - self._press_time
        distance = ((release[0] - self._press_point[0]) ** 2 + (release[1] - self._press_point[1]) ** 2) ** 0.5
        foreground = int(user32.GetForegroundWindow() or 0)
        if distance < 7 or elapsed < 0.05 or elapsed > 20 or foreground != self._press_window:
            return
        process_id = self._foreground_process_id(foreground)
        if process_id == os.getpid():
            return
        # Ctrl+C compatibility capture is intentionally limited to reading apps,
        # so mouse drags in games and other desktop software are never disturbed.
        process_name = self._process_name(process_id)
        if process_name not in READING_PROCESSES:
            return
        self._source_app = process_name
        self._cursor_point = release
        self._capture_id += 1
        capture_id = self._capture_id
        QTimer.singleShot(90, lambda: self._begin_capture(capture_id))

    @staticmethod
    def _clone_mime() -> tuple[QMimeData, bool]:
        clipboard = QGuiApplication.clipboard()
        source = clipboard.mimeData()
        clone = QMimeData()
        formats = list(source.formats()) if source is not None else []
        for mime_type in formats:
            clone.setData(mime_type, source.data(mime_type))
        return clone, bool(formats)

    @staticmethod
    def _clipboard_sequence() -> int:
        return int(_user32().GetClipboardSequenceNumber())

    @staticmethod
    def _send_copy() -> None:
        user32 = _user32()
        ctrl_down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
        if not ctrl_down:
            user32.keybd_event(0x11, 0, 0, 0)
        user32.keybd_event(0x43, 0, 0, 0)
        user32.keybd_event(0x43, 0, 0x0002, 0)
        if not ctrl_down:
            user32.keybd_event(0x11, 0, 0x0002, 0)

    def _begin_capture(self, capture_id: int) -> None:
        if capture_id != self._capture_id or not self._timer.isActive():
            return
        self._saved_mime, self._saved_had_data = self._clone_mime()
        before = self._clipboard_sequence()
        self._copy_attempts = 0
        self._send_copy()
        self._copy_sequence = before
        QTimer.singleShot(100, lambda: self._read_capture(capture_id))

    def _read_capture(self, capture_id: int) -> None:
        if capture_id != self._capture_id:
            return
        current_sequence = self._clipboard_sequence()
        if current_sequence == self._copy_sequence and self._copy_attempts < 2:
            self._copy_attempts += 1
            QTimer.singleShot(90, lambda: self._read_capture(capture_id))
            return
        clipboard = QGuiApplication.clipboard()
        selected = clipboard.text().strip() if current_sequence != self._copy_sequence else ""
        copied_sequence = current_sequence
        if self._saved_mime is not None and self._clipboard_sequence() == copied_sequence:
            if self._saved_had_data:
                clipboard.setMimeData(self._saved_mime)
            else:
                clipboard.clear()
        self._saved_mime = None
        selected = re.sub(r"[ \t]+", " ", selected)
        selected = re.sub(r"\n{3,}", "\n\n", selected).strip()
        if len(selected) < 2 or not any(value.isalnum() for value in selected):
            return
        if len(selected) > MAX_SELECTION_LENGTH:
            selected = selected[:MAX_SELECTION_LENGTH]
        now = time.monotonic()
        if selected == self._last_text and now - self._last_text_at < 4:
            return
        self._last_text = selected
        self._last_text_at = now
        self._explain(selected, *self._cursor_point)

    def _explain(self, text: str, x: int, y: int) -> None:
        self._start_request(prepare_reading_request(text, "explain"), x, y)

    def _start_request(self, request: ReadingRequest, x: int, y: int) -> None:
        self._subscription_idle.stop()
        self._cancel_active_broker_task("selection-superseded")
        self._request_id += 1
        request_id = self._request_id
        self._pending_requests[request_id] = request
        self._current_request = request
        self._current_answer = ""
        self._current_saved_card_id = ""
        self._bubble = {
            "visible": True,
            "text": "……让我看看。",
            "busy": True,
            "error": False,
            "x": x,
            "y": y,
            **reading_bubble_metadata(request, source_app=self._source_app, busy=True),
        }
        self.bubbleChanged.emit(self.bubble)

        broker_task_id = ""
        if self._model_broker is not None:
            broker_task = self._model_broker.submit(
                LUNA_MODEL_NAME,
                ModelTaskKind.PAPER_SELECTION,
                {
                    "requestId": request_id,
                    "action": request.action,
                    "sourceApplication": self._source_app[:80],
                },
                context_bound=True,
                expires_at=time.monotonic() + 90.0,
            )
            broker_task_id = broker_task.id
            self._active_broker_task_id = broker_task_id

        def worker() -> None:
            lease = _BrokerTaskLease(
                self._model_broker,
                broker_task_id or None,
                LUNA_MODEL_NAME,
                abort=self._abort_subscription,
            )
            try:
                if not lease.acquire():
                    self._requestFinished.emit(request_id, "", _BROKER_CANCELLED)
                    return
                if lease.cancelled:
                    self._requestFinished.emit(request_id, "", _BROKER_CANCELLED)
                    return
                if request.action == "explain":
                    # Keep the original adapter entry point working for callers
                    # that supplied a small test/offline subscription client.
                    result = self._subscription.explain(request.source_text)
                else:
                    result = self._subscription.reading_action(
                        request.source_text,
                        request.action,
                        request.question,
                    )
                if lease.commit(result={"completed": True}):
                    self._requestFinished.emit(request_id, result, "")
                else:
                    self._requestFinished.emit(request_id, "", _BROKER_CANCELLED)
            except Exception as exc:
                self._requestFinished.emit(
                    request_id,
                    "",
                    _BROKER_CANCELLED if lease.cancelled else str(exc),
                )
            finally:
                lease.close(result={"completed": not lease.cancelled})
                if (
                    broker_task_id
                    and self._active_broker_task_id == broker_task_id
                ):
                    self._active_broker_task_id = ""

        threading.Thread(target=worker, name="lilies-luna-selection", daemon=True).start()

    def _finish_request(self, request_id: int, text: str, error: str) -> None:
        request = self._pending_requests.pop(request_id, None)
        if request_id != self._request_id:
            return
        if request is None:
            return
        if error == _BROKER_CANCELLED:
            self._current_answer = ""
            self._current_saved_card_id = ""
            self._bubble = {**self._bubble, "visible": False, "busy": False}
            self.bubbleChanged.emit(self.bubble)
            self._subscription_idle.start()
            self.settingsChanged.emit()
            return
        answer = text.strip() if not error else ""
        self._current_request = request
        self._current_answer = answer
        self._current_saved_card_id = ""
        save_error = ""
        if request.action == "term" and answer:
            try:
                self._current_saved_card_id = self._save_card(request, answer)
            except Exception as exc:
                save_error = str(exc)
        self._bubble = {
            **self._bubble,
            "visible": True,
            "text": text if text else error,
            "busy": False,
            "error": bool(error),
            **reading_bubble_metadata(
                request,
                answer,
                source_app=self._source_app,
                saved_card_id=self._current_saved_card_id,
                error=bool(error),
            ),
        }
        if save_error:
            self._bubble["saveError"] = save_error
        self.bubbleChanged.emit(self.bubble)
        self._subscription_idle.start()
        self.settingsChanged.emit()

    def _abort_subscription(self) -> None:
        abort = getattr(self._subscription, "abort", None)
        if callable(abort):
            abort()

    def _cancel_active_broker_task(self, reason: str) -> None:
        if self._model_broker is None or not self._active_broker_task_id:
            return
        task = self._model_broker.get(self._active_broker_task_id)
        if task is not None and not task.terminal:
            try:
                self._model_broker.cancel(self._active_broker_task_id, reason=reason)
            except (KeyError, ValueError):
                pass

    def _save_card(self, request: ReadingRequest, answer: str) -> str:
        return self.database.save_reading_card(
            request.source_text,
            answer,
            kind=request.action,
            question=request.question,
            title=reading_card_title(request, answer),
            metadata={"sourceApp": self._source_app, "ephemeral": True},
        )
