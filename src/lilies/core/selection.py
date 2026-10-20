from __future__ import annotations

import ctypes
import os
import re
import secrets
import threading
import time
from copy import deepcopy
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication

from .codex_subscription import CodexSubscriptionClient
from .database import Database
from .model import _BrokerTaskLease
from .orchestration import ModelTaskBroker, ModelTaskKind
from .activity import GUITHREADINFO, Win32ForegroundContextReader, SensitiveWindowGuard
from .win_event import WinEvent, WinEventKind
from .windows import window_identity
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
    library.GetClipboardOwner.argtypes = []
    library.GetClipboardOwner.restype = wintypes.HWND
    library.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    library.GetGUIThreadInfo.restype = wintypes.BOOL
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


@dataclass(slots=True)
class _SelectionCapture:
    generation: int
    identity: dict[str, Any]
    point: tuple[int, int]
    provenance: dict[str, Any]
    saved_mime: QMimeData | None = None
    saved_had_data: bool = False
    before_sequence: int = 0
    attempts: int = 0


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
        self._privacy_suppressed = False
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
        self.paper_context_provider = lambda: {}
        self._current_provenance: dict[str, Any] = {}
        self._pending_provenance: dict[int, dict[str, Any]] = {}
        self._save_offer: dict[str, Any] | None = None
        self._closed = False
        self._mouse_down = False
        self._press_point = (0, 0)
        self._press_window = 0
        self._press_identity: dict[str, Any] | None = None
        self._press_time = 0.0
        self._capture_id = 0
        self._capture: _SelectionCapture | None = None
        self.capture_identity_provider = self._native_capture_identity
        privacy_guard = SensitiveWindowGuard()
        reader = Win32ForegroundContextReader()
        self.capture_policy = lambda hwnd: privacy_guard.evaluate(reader(hwnd)).can_bubble
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
        if self._privacy_suppressed:
            return "隐私或全屏场景中暂停划词"
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
        if value:
            self._save_offer = None
        # A desktop-pet drag must never be interpreted as a WPS/PDF text
        # selection when the button is released over the reading window.
        self._mouse_down = False
        self._press_identity = None
        self._invalidate_capture()
        self._refresh_monitor()

    def _invalidate_capture(self, expected: _SelectionCapture | None = None) -> None:
        """Retire delayed callbacks without reading/writing the clipboard.

        After cancellation we cannot attribute a later clipboard update to
        our Ctrl+C. Leave its latest value alone, rather than restore stale
        data over a user's newer copy operation.
        """
        if expected is not None and self._capture is not expected:
            return
        self._capture_id += 1
        self._capture = None

    def set_privacy_suppressed(self, value: bool) -> None:
        value = bool(value)
        if value == self._privacy_suppressed:
            return
        self._privacy_suppressed = value
        self._refresh_monitor()
        if value:
            self.dismiss()
            self._pending_requests.clear()
            self._pending_provenance.clear()
            self._current_request = None
            self._current_answer = ""
            self._current_provenance = {}
            self._current_saved_card_id = ""
            self._last_text = ""
        self.settingsChanged.emit()
        self.statusChanged.emit(self.status)

    def handle_window_event(self, event: WinEvent) -> None:
        identity = self._capture.identity if self._capture else self._press_identity
        if not identity:
            return
        hwnd = int(identity["handle"])
        if (event.kind is WinEventKind.FOREGROUND and int(event.hwnd) != hwnd) or (
            int(event.hwnd) == hwnd and event.kind in {
                WinEventKind.DESTROY, WinEventKind.HIDE, WinEventKind.MINIMIZE_START,
            }
        ):
            self._mouse_down = False
            self._press_identity = None
            self._invalidate_capture()

    def dismiss(self) -> None:
        self._save_offer = None
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

        if self._privacy_suppressed:
            return
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
        self._start_request(request, x, y, provenance=self._current_provenance if self._current_request else None)

    def save_current_card(self) -> str:
        """Compatibility/quick save is independent, never inferred provenance."""
        self._save_offer = None
        return self._commit_current_card()

    def prepare_card_save(self) -> dict[str, Any]:
        """Preview a frozen, user-confirmable destination, without saving anything.

        A running paper session suggests a destination only. It cannot identify
        a document in another application's window (or even another reader tab).
        The QML caller returns only an opaque offer id, never its own provenance.
        """
        self._save_offer = None
        if not self._can_save_card() or self._current_saved_card_id:
            raise ValueError("这段结果已关闭、尚未完成或已经保存")
        paper = deepcopy(self._current_provenance.get("paper") or {})
        offer = {
            "id": secrets.token_urlsafe(24), "requestId": self._request_id,
            "expiresAt": time.monotonic() + 120.0, "paper": paper,
        }
        self._save_offer = offer
        return {
            "id": offer["id"], "requestId": offer["requestId"],
            "hasPaper": bool(paper), "paperTitle": str(paper.get("paperTitle", "")),
            "page": paper.get("page", 0), "locator": str(paper.get("locator", "")),
            "sourceUri": str(paper.get("sourceUri", "")),
        }

    def cancel_card_save(self, offer_id: str) -> None:
        if self._save_offer and self._save_offer["id"] == offer_id:
            self._save_offer = None

    def confirm_card_save(self, offer_id: str, destination: str) -> str:
        offer = self._save_offer
        if (not offer or not isinstance(offer_id, str) or offer["id"] != offer_id
                or offer["requestId"] != self._request_id
                or time.monotonic() >= offer["expiresAt"] or not self._can_save_card()):
            raise ValueError("保存预览已失效，请重新打开")
        if not isinstance(destination, str) or destination not in {"paper", "independent"}:
            raise ValueError("请选择本篇或独立保存")
        if destination == "paper" and not offer["paper"]:
            raise ValueError("这段文字没有待确认的论文出处")
        # Consume before writing/signals. Double clicks and reentrant observers
        # cannot reuse confirmation. A failed write requires a fresh preview.
        self._save_offer = None
        return self._commit_current_card(offer["paper"] if destination == "paper" else None)

    def _can_save_card(self) -> bool:
        return bool(self._current_request and self._current_answer.strip()
                    and self._bubble.get("visible") and not self._bubble.get("busy")
                    and not self._bubble.get("error") and not self._privacy_suppressed
                    and not self._interaction_suspended and not self._closed)

    def _commit_current_card(self, paper_context: dict[str, Any] | None = None) -> str:
        """The only caller allowed to supply a paper is explicit confirmation."""

        request = self._current_request
        answer = self._current_answer.strip()
        if not self._can_save_card():
            return ""
        if self._current_saved_card_id:
            return self._current_saved_card_id
        card_id = self._save_card(request, answer, paper_context=paper_context)
        self._current_saved_card_id = card_id
        self._bubble = {
            **self._bubble,
            **reading_bubble_metadata(
                request,
                answer,
                source_app=str(self._current_provenance.get("sourceApp", self._source_app)),
                saved_card_id=card_id,
            ),
            "paperAssociation": "user-confirmed" if paper_context else "independent",
            "autoSaved": False,
        }
        self.bubbleChanged.emit(self.bubble)
        return card_id

    def shutdown(self) -> None:
        self._closed = True
        self._save_offer = None
        self._timer.stop()
        self._subscription_idle.stop()
        self._cancel_active_broker_task("selection-shutdown")
        self._subscription.stop()
        self._request_id += 1
        self._active = False
        self._invalidate_capture()

    def _refresh_monitor(self) -> None:
        should_run = (
            self._active
            and self._enabled
            and self._subscription.ready
            and not self._interaction_suspended
            and not self._privacy_suppressed
        )
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run:
            self._timer.stop()
            self._mouse_down = False
            self._press_identity = None
            self._invalidate_capture()

    @staticmethod
    def _native_capture_identity() -> dict[str, Any] | None:
        user32 = _user32()
        hwnd = int(user32.GetForegroundWindow() or 0)
        identity = window_identity(hwnd)
        if (not identity or identity["processId"] == os.getpid()
                or identity["processName"] not in READING_PROCESSES
                or not identity["processStarted"] or not identity["executableHash"]):
            return None
        process_id = wintypes.DWORD()
        thread_id = int(user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(process_id)))
        focus = GUITHREADINFO()
        focus.cbSize = ctypes.sizeof(focus)
        if (not thread_id or int(process_id.value) != identity["processId"]
                or not user32.GetGUIThreadInfo(thread_id, ctypes.byref(focus)) or not focus.hwndFocus):
            return None
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            return None
        return {**identity, "focusWindow": int(focus.hwndFocus)}

    def _read_identity(self) -> dict[str, Any] | None:
        try:
            value = self.capture_identity_provider()
            if value and self.capture_policy(int(value["handle"])):
                return dict(value)
        except (OSError, RuntimeError, ValueError):
            pass
        return None

    def _capture_is_current(self, capture: _SelectionCapture) -> bool:
        return bool(
            self._capture is capture and capture.generation == self._capture_id
            and self._active and self._enabled and self._timer.isActive()
            and not self._interaction_suspended and not self._privacy_suppressed
            and self._subscription.ready
        )

    def _capture_valid(self, capture: _SelectionCapture) -> bool:
        if not self._capture_is_current(capture) or not self._copy_chord_available():
            return False
        identity = self._read_identity()
        # A synchronous adapter/subscriber can pause monitoring or create a
        # newer selection while identity/policy is being read. Its old return
        # value is not authority to touch the clipboard after that boundary.
        return bool(
            self._capture_is_current(capture) and identity == capture.identity
            and self._copy_chord_available()
        )

    @staticmethod
    def _copy_chord_available() -> bool:
        # This is only a momentary modifier/button guard around an explicit
        # selection, never a keyboard log or a text/activity history.
        return not any(_user32().GetAsyncKeyState(key) & 0x8000
                       for key in (0x01, 0x10, 0x12, 0x5B, 0x5C, 0x43))

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
            self._invalidate_capture()
            self._mouse_down = True
            self._press_point = self._cursor()
            self._press_window = int(user32.GetForegroundWindow() or 0)
            self._press_identity = self._read_identity()
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
        capture_id = self._capture_id
        identity = self._read_identity()
        if capture_id != self._capture_id or not identity or identity != self._press_identity:
            return
        try:
            provenance = {"sourceApp": identity["processName"], "paper": dict(self.paper_context_provider())}
        except (OSError, RuntimeError, ValueError):
            if capture_id == self._capture_id:
                self._invalidate_capture()
            return
        if capture_id != self._capture_id:
            return
        capture = _SelectionCapture(capture_id, identity, release, provenance)
        self._capture = capture
        if not self._capture_valid(capture):
            self._invalidate_capture(capture)
            return
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
    def _clipboard_owner_pid() -> int:
        owner = int(_user32().GetClipboardOwner() or 0)
        return SelectionService._foreground_process_id(owner) if owner else 0

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
        capture = self._capture
        if capture is None or capture_id != capture.generation:
            return
        try:
            if not self._capture_valid(capture):
                self._invalidate_capture(capture)
                return
            capture.before_sequence = self._clipboard_sequence()
            capture.saved_mime, capture.saved_had_data = self._clone_mime()
            # Cloning delayed clipboard formats can take time: revalidate on
            # both sides, and never overwrite a newer clipboard with that copy.
            if (not self._capture_valid(capture)
                    or self._clipboard_sequence() != capture.before_sequence):
                self._invalidate_capture(capture)
                return
            self._send_copy()
            if not self._capture_valid(capture):
                self._invalidate_capture(capture)
                return
            QTimer.singleShot(100, lambda: self._read_capture(capture_id))
        except (OSError, RuntimeError):
            self._invalidate_capture(capture)

    def _read_capture(self, capture_id: int) -> None:
        capture = self._capture
        if capture is None or capture_id != capture.generation:
            return
        try:
            if not self._capture_valid(capture):
                self._invalidate_capture(capture)
                return
            sequence = self._clipboard_sequence()
            if sequence == capture.before_sequence:
                if capture.attempts < 2:
                    capture.attempts += 1
                    QTimer.singleShot(90, lambda: self._read_capture(capture_id))
                else:
                    self._invalidate_capture(capture)
                return
            if self._clipboard_owner_pid() != capture.identity["processId"]:
                self._invalidate_capture(capture)
                return
            clipboard = QGuiApplication.clipboard()
            selected = clipboard.text().strip()
            # A changed sequence/owner or foreground invalidates the result;
            # it is not another selection and must not enter a model prompt.
            if (not self._capture_valid(capture) or self._clipboard_sequence() != sequence
                    or self._clipboard_owner_pid() != capture.identity["processId"]):
                self._invalidate_capture(capture)
                return
            if capture.saved_mime is not None:
                if capture.saved_had_data:
                    clipboard.setMimeData(capture.saved_mime)
                else:
                    clipboard.clear()
                capture.saved_mime = None
            if not self._capture_valid(capture):
                self._invalidate_capture(capture)
                return
        except (OSError, RuntimeError):
            self._invalidate_capture(capture)
            return
        self._invalidate_capture(capture)
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
        self._source_app = str(capture.identity["processName"])
        self._cursor_point = capture.point
        self._start_request(prepare_reading_request(selected, "explain"), *capture.point,
                            provenance=capture.provenance)

    def _explain(self, text: str, x: int, y: int) -> None:
        self._start_request(prepare_reading_request(text, "explain"), x, y)

    def explain_provided_text(self, text: str, x: int, y: int) -> None:
        """Explicit paste only: no clipboard read, selection polling or screenshot.

        Turning off automatic selection monitoring does not disable a deliberate
        single request. It also does not silently turn that monitoring back on.
        """
        if self._privacy_suppressed:
            raise ValueError("当前隐私或全屏场景中暂停处理文字")
        if self._interaction_suspended:
            raise ValueError("请先松开莉莉丝，再发送这段文字")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SELECTION_LENGTH:
            raise ValueError(f"请填写 1–{MAX_SELECTION_LENGTH} 字的文字")
        if self._bubble.get("busy"):
            raise ValueError("上一段文字还在处理，可以先保留这份草稿")
        self._start_request(prepare_reading_request(text, "explain"), int(x), int(y),
            provenance={"sourceApp": "用户提供", "paper": {}, "provided": True})

    def _start_request(self, request: ReadingRequest, x: int, y: int, *, provenance: dict[str, Any] | None = None) -> None:
        self._save_offer = None
        self._subscription_idle.stop()
        self._cancel_active_broker_task("selection-superseded")
        self._request_id += 1
        request_id = self._request_id
        self._pending_requests[request_id] = request
        self._current_provenance = deepcopy(provenance) if provenance is not None else {
            "sourceApp": self._source_app, "paper": dict(self.paper_context_provider()),
        }
        self._pending_provenance[request_id] = deepcopy(self._current_provenance)
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
            **reading_bubble_metadata(request, source_app=str(self._current_provenance.get("sourceApp", "")), busy=True),
            "paperTitle": str(self._current_provenance.get("paper", {}).get("paperTitle", "")),
            "paperPage": int(self._current_provenance.get("paper", {}).get("page", 0)),
            "requestId": request_id,
            "paperAssociation": "unconfirmed" if self._current_provenance.get("paper") else "none",
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
                    "sourceApplication": str(self._current_provenance.get("sourceApp", ""))[:80],
                },
                context_bound=not bool(self._current_provenance.get("provided")),
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
        provenance = self._pending_provenance.pop(request_id, {})
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
        self._current_provenance = provenance
        self._current_request = request
        self._current_answer = answer
        self._current_saved_card_id = ""
        save_error = ""
        # The explicit term action still auto-saves independent cards. With a
        # suggested paper, let the user choose rather than silently misfile it.
        if request.action == "term" and answer and not provenance.get("paper"):
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
                source_app=str(provenance.get("sourceApp", self._source_app)),
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

    def _save_card(self, request: ReadingRequest, answer: str, *, paper_context: dict[str, Any] | None = None) -> str:
        return self.database.save_reading_card(
            request.source_text,
            answer,
            kind=request.action,
            question=request.question,
            title=reading_card_title(request, answer),
            metadata={"sourceApp": str(self._current_provenance.get("sourceApp", self._source_app)), "ephemeral": True,
                      "paperAssociation": "user-confirmed" if paper_context else "independent",
                      "pageOrigin": "user" if paper_context else "none"},
            paper_context=paper_context,
        )
