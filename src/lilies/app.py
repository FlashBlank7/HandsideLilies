from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from ctypes import wintypes
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen, QPixmap, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .backend import Backend
from .core.shell import restore_from_backup
from .core.socket_server import (
    LocalSocketServer,
    PRIMARY_SOCKET_PORT,
    request_existing_instance,
)
from .paths import (
    APP_NAME,
    WINDOWS_PRIVATE_DATA_ROOT,
    DataRootUnavailableError,
    configure_qt_cache_environment,
    data_root,
    disable_qt_disk_caches_for_recovery,
    qml_path,
)


class CompactHitTestFilter(QAbstractNativeEventFilter):
    """Let clicks pass through the invisible part of the compact tool window."""

    WM_NCHITTEST = 0x0084
    WM_EXITSIZEMOVE = 0x0232
    HTCLIENT = 1
    HTTRANSPARENT = -1

    def __init__(
        self,
        root: QQuickWindow,
        backend: Backend,
        native_move_controller: CompactPointerEventFilter | None = None,
        *,
        native_window_id: int,
    ) -> None:
        super().__init__()
        self.root = root
        self.backend = backend
        self.native_move_controller = native_move_controller
        # The caller must resolve the HWND before this filter is installed.
        # Keeping winId() entirely outside this class makes the native callback
        # boundary mechanically auditable: neither nativeEventFilter() nor a
        # helper it owns can enter QWindowPrivate::create() while User32 is
        # already dispatching a message.
        self.native_window_id = int(native_window_id)
        self.native_dispatch_count = 0
        self.native_hit_test_count = 0

    @staticmethod
    def _inside(px: float, py: float, left: float, top: float, width: float, height: float) -> bool:
        return left <= px <= left + width and top <= py <= top + height

    @staticmethod
    def _logical_point(px: float, py: float, device_pixel_ratio: float) -> tuple[float, float]:
        """Convert the physical WM_NCHITTEST point into QML logical coordinates."""
        scale = device_pixel_ratio if device_pixel_ratio > 0 else 1.0
        return px / scale, py / scale

    @staticmethod
    def _visual_descendants(root: QQuickItem) -> list[QQuickItem]:
        """Return the QQuickItem visual tree, including Repeater delegates.

        Repeater-created delegates live in the QQuickItem visual tree but are
        not guaranteed to be QObject children of the window.  Using
        QObject.findChildren() here made every radial action button fall
        through to the application behind Lilies on Windows.
        """

        descendants: list[QQuickItem] = []
        pending = list(root.childItems())
        while pending:
            item = pending.pop()
            descendants.append(item)
            pending.extend(item.childItems())
        return descendants

    def accepts_point(self, px: float, py: float) -> bool:
        character = (
            float(self.root.property("compactCharacterLeft") or 0),
            float(self.root.property("compactCharacterTop") or 0),
            float(self.root.property("compactCharacterWidth") or 0),
            float(self.root.property("compactCharacterHeight") or 0),
        )
        box_left = float(self.root.property("compactAccessoryLeft") or 0)
        box_top = float(self.root.property("compactAccessoryTop") or 0)
        box_width = float(self.root.property("compactAccessoryWidth") or 0)

        # Controls are checked before the character. A pill may overlap the
        # character's rectangular frame while still sitting outside its
        # silhouette; returning early from the mask used to make that pill
        # click-through even though it was fully visible.
        content_item = getattr(self.root, "contentItem", None)
        actions_interactive = bool(self.root.property("compactActionsInteractive"))
        if callable(content_item):
            try:
                parent_item = content_item()
                for item in self._visual_descendants(parent_item):
                    name = item.objectName()
                    is_action = name.startswith("desktopPetAction_")
                    is_resize = name == "desktopPetResizeHandle"
                    is_desktop_mode = name == "desktopPetDesktopModeTab"
                    is_companion_unread = name == "desktopPetCompanionUnreadCue"
                    if (
                        not (
                            is_action
                            or is_resize
                            or is_desktop_mode
                            or is_companion_unread
                        )
                        or (is_action and not actions_interactive)
                    ):
                        continue
                    if not item.isVisible() or item.opacity() <= 0.05 or item.width() <= 0 or item.height() <= 0:
                        continue
                    local_point = item.mapFromItem(parent_item, QPointF(px, py))
                    if item.contains(local_point):
                        return True
            except (RuntimeError, TypeError):
                pass

        cx, cy = box_left + box_width / 2, box_top + box_width / 2
        if box_width > 0 and (px - cx) ** 2 + (py - cy) ** 2 <= (box_width / 2) ** 2:
            return True

        if self._inside(px, py, *character):
            mask = getattr(self.root, "characterContains", None)
            if callable(mask):
                try:
                    return bool(mask(px, py))
                except (RuntimeError, TypeError, ValueError):
                    # A transient QML relayout must not make the visible
                    # character impossible to recover with the mouse.
                    return True
            return True
        return False

    def nativeEventFilter(self, event_type: bytes, message: int) -> tuple[bool, int]:
        if os.name != "nt":
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):
            return False, 0
        if self.native_window_id <= 0 or int(msg.hWnd) != self.native_window_id:
            return False, 0
        self.native_dispatch_count += 1
        if msg.message == self.WM_EXITSIZEMOVE:
            # QWindowsWindow releases native capture before entering the
            # system move loop.  Depending on the final cursor position, Qt
            # may finish that loop with a non-client release which a QML
            # MouseArea never receives.  Deliver completion on the next event
            # turn so Qt's normal synthesized client release gets first chance
            # to finish the same gesture.
            controller = self.native_move_controller
            if controller is not None:
                controller.queueSystemMoveFinished()
            return False, 0
        if msg.message != self.WM_NCHITTEST:
            return False, 0
        self.native_hit_test_count += 1
        try:
            drag_active = bool(self.root.property("manualDragActive")) or bool(
                self.root.property("nativeSystemMoveActive")
            )
        except (AttributeError, KeyError, RuntimeError, TypeError):
            drag_active = False
        if drag_active:
            # The press already proved that this gesture began on an
            # interactive island. Re-walking the full QQuickItem tree for
            # every hit test while the transparent window moves can consume a
            # material part of the GUI frame budget on high-rate mice.
            return True, self.HTCLIENT
        packed = int(msg.lParam)
        screen_x = ctypes.c_short(packed & 0xFFFF).value
        screen_y = ctypes.c_short((packed >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(int(msg.hWnd), ctypes.byref(rect)):
            return False, 0
        # WM_NCHITTEST supplies physical screen pixels, while QML item geometry is
        # expressed in device-independent pixels.  Without this conversion a
        # 125%/150% high-DPI desktop makes the visible character click-through.
        device_pixel_ratio = float(self.root.devicePixelRatio() or 1.0)
        logical_x, logical_y = self._logical_point(
            screen_x - rect.left,
            screen_y - rect.top,
            device_pixel_ratio,
        )
        if self.accepts_point(logical_x, logical_y):
            # Do not ask the layered tool window's default procedure to infer
            # the result again. A visible interaction island is explicitly a
            # client target, which keeps real hardware input routing aligned
            # with the same geometry used by the QML controls.
            return True, self.HTCLIENT
        return True, self.HTTRANSPARENT


class CompactPointerEventFilter(QObject):
    """Expose event-time global mouse coordinates to the compact QML window.

    Qt's MouseArea API exposes only item-local x/y.  Those coordinates may
    have been calculated against the previous native window origin; combining
    them with a QWindow position changed by the same gesture produces a
    feedback overshoot.  QMouseEvent.globalPosition() is the stable logical
    desktop coordinate specifically provided for moving windows.
    """

    _MOUSE_EVENTS = frozenset(
        {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        }
    )

    def __init__(self, root: QQuickWindow) -> None:
        super().__init__(root)
        self.root = root
        self.serial = 0
        self._active_system_move_serial = 0
        self._last_direct_position: QPoint | None = None
        self._latest_global_x = 0.0
        self._latest_global_y = 0.0

    @Slot(int, result=bool)
    def tryStartSystemMove(self, gesture_serial: int) -> bool:
        """Start the compositor-owned move and preserve its support result.

        Keeping this tiny bridge in Python gives QML one unambiguous boolean
        authority across Qt minor versions.  When it returns true the manual
        cursor polling path must remain completely idle for that gesture.
        """

        gesture_serial = int(gesture_serial)
        if gesture_serial <= 0:
            return False
        try:
            started = bool(self.root.startSystemMove())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        self._active_system_move_serial = gesture_serial if started else 0
        return started

    @Slot(float, float, result=bool)
    def moveWindowForDrag(self, x: float, y: float) -> bool:
        """Move both window axes in one platform geometry transaction.

        Assigning ``Window.x`` and ``Window.y`` separately asks the Windows
        platform plugin to move the transparent tool window twice for every
        pointer sample.  Besides extra compositor work, the intermediate
        one-axis position can produce another mouse frame and make a diagonal
        drag look as if it is wobbling behind the cursor.  QWindow.setPosition
        commits the logical desktop point atomically and remains correct on
        negative-coordinate and mixed-DPI screens.
        """

        try:
            target_x = float(x)
            target_y = float(y)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(target_x) or not math.isfinite(target_y):
            return False
        target = QPoint(round(target_x), round(target_y))
        # A moving transparent QQuickWindow can receive several equivalent
        # pointer samples between two compositor frames.  Re-submitting the
        # same native geometry needlessly wakes DWM and is visible as a small
        # pause on high-polling-rate mice.  The press event resets this cache,
        # so every new gesture still owns its first requested position.
        if target == self._last_direct_position:
            return True
        try:
            self.root.setPosition(target)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        self._last_direct_position = target
        return True

    @Slot(int)
    def acknowledgeSystemMoveFinished(self, gesture_serial: int) -> None:
        """Make a later queued native completion a no-op after QML release."""

        if int(gesture_serial) == self._active_system_move_serial:
            self._active_system_move_serial = 0

    @Slot(int, result="QVariantMap")
    def takeLatestPointerEvent(self, after_serial: int) -> dict[str, object]:
        """Return the newest native pointer sample without waking QML per event.

        ``eventFilter`` only updates these Python fields. QML consumes the
        newest point on its 16 ms drag frame, turning a 500/1000 Hz mouse into
        at most one QWindow geometry submission per display frame.
        """

        after = int(after_serial)
        if self.serial <= after:
            return {"available": False, "serial": self.serial}
        return {
            "available": True,
            "serial": self.serial,
            "x": self._latest_global_x,
            "y": self._latest_global_y,
        }

    def queueSystemMoveFinished(self) -> None:
        """Queue WM_EXITSIZEMOVE completion after ordinary Qt mouse release."""

        gesture_serial = self._active_system_move_serial
        if gesture_serial <= 0:
            return
        QTimer.singleShot(
            0,
            lambda serial=gesture_serial: self._deliverSystemMoveFinished(serial),
        )

    def _deliverSystemMoveFinished(self, gesture_serial: int) -> None:
        if gesture_serial != self._active_system_move_serial:
            return
        self._active_system_move_serial = 0
        callback = getattr(self.root, "finishNativeSystemMove", None)
        if not callable(callback):
            return
        try:
            callback(gesture_serial)
        except (RuntimeError, TypeError):
            pass

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is not self.root or event.type() not in self._MOUSE_EVENTS:
            return False
        if not isinstance(event, QMouseEvent):
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            self._last_direct_position = None
        point = event.globalPosition()
        self.serial += 1
        self._latest_global_x = float(point.x())
        self._latest_global_y = float(point.y())
        return False


class QuickWindowResourceLifecycle(QObject):
    """Release hidden QML windows' scene graphs and graphics allocations.

    Lilies intentionally keeps its chat, box-world, Dock popups and connector
    panels as independent QQuickWindows.  On the Windows threaded render loop,
    a window that was shown once can otherwise retain a render thread and a
    D3D device allocation after it is hidden.  The compact pet is the only
    surface that should stay resident; visible windows recreate their scene
    graph normally when opened again.
    """

    def __init__(self, root: QQuickWindow) -> None:
        super().__init__(root)
        self.windows = (root, *tuple(root.findChildren(QQuickWindow)))
        for window in self.windows:
            self._configure(window)
            window.installEventFilter(self)
            if not window.isVisible():
                self._schedule_release(window)

    @staticmethod
    def _configure(window: QQuickWindow) -> None:
        window.setPersistentGraphics(False)
        window.setPersistentSceneGraph(False)

    def _schedule_release(self, window: QQuickWindow) -> None:
        QTimer.singleShot(0, lambda: self._release_if_still_hidden(window))

    @staticmethod
    def _release_if_still_hidden(window: QQuickWindow) -> None:
        try:
            if not window.isVisible():
                # The persistent properties are documented as hints.  An
                # explicit release on the next GUI event turn is what asks the
                # Windows render loop and D3D driver to relinquish the hidden
                # window's resources deterministically.
                window.releaseResources()
        except RuntimeError:
            # The QML engine may destroy a transient window before this queued
            # callback runs.  There is then nothing left to release.
            return

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if isinstance(watched, QQuickWindow):
            if event.type() == QEvent.Type.Show:
                self._configure(watched)
            elif event.type() == QEvent.Type.Hide:
                self._schedule_release(watched)
        return False


class CompanionBubblePresentationProbe(QObject):
    """Bridge a QML presentation request to native ``QWindow`` evidence.

    QML's ``visible`` property only proves that the declarative binding asked
    for a window.  A retained ``Qt.Tool`` can still be hidden or not exposed by
    the platform compositor.  This GUI-thread probe waits across a few bounded
    event turns and acknowledges delivery only after the actual QQuickWindow
    reports visible, non-hidden and exposed.  It carries identifiers only;
    bubble text and foreground-window metadata never cross this bridge.
    """

    _RETRY_DELAYS_MS = (0, 40, 100, 220, 450, 800, 1300, 1900)

    def __init__(
        self,
        window: QQuickWindow,
        controller: QObject,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self.window = window
        self.controller = controller
        self._request_serial = 0

    @Slot(str, int)
    def requestAck(self, bubble_id: str, presentation_revision: int) -> None:
        clean_id = str(bubble_id)[:160]
        if not clean_id:
            return
        self._request_serial += 1
        request_serial = self._request_serial
        revision = max(0, int(presentation_revision))
        for delay_ms in self._RETRY_DELAYS_MS:
            QTimer.singleShot(
                delay_ms,
                lambda serial=request_serial, identifier=clean_id, rev=revision: self._probe(
                    serial, identifier, rev
                ),
            )

    @Slot()
    def cancelPending(self) -> None:
        """Invalidate retries when QML enters a privacy-suppressed state."""

        self._request_serial += 1

    def _probe(self, request_serial: int, bubble_id: str, revision: int) -> None:
        if request_serial != self._request_serial:
            return
        try:
            current_revision = int(
                self.window.property("presentationRevision") or 0
            )
            visible = bool(self.window.isVisible())
            exposed = bool(self.window.isExposed())
            not_hidden = self.window.visibility() != QWindow.Visibility.Hidden
        except (AttributeError, RuntimeError, TypeError):
            return
        if current_revision != revision:
            return
        if not (visible and exposed and not_hidden):
            return
        acknowledge = getattr(self.controller, "ackPresented", None)
        if not callable(acknowledge):
            return
        try:
            accepted = bool(acknowledge(bubble_id, visible, exposed, revision))
        except (RuntimeError, TypeError, ValueError):
            return
        if accepted:
            # Invalidate the remaining scheduled probes for this request.
            self._request_serial += 1


class DesktopSurfacePresentationProbe(QObject):
    """Bounded, non-activating verification for the full desktop surface.

    A declarative ``visible`` value is not enough after Show Desktop, an
    Explorer WorkerW rebuild, or a visual/compact round trip.  This probe runs
    only on explicit presentation boundaries, waits for native exposure, and
    performs at most two content-free recovery steps: ``showNormal()`` once,
    then one ``hide()``/``show()`` remap.  It never requests keyboard focus and
    never polls indefinitely.

    On qwindows the evidence also rejects an iconic or DWM-cloaked HWND and,
    when Explorer's wallpaper hosts can be enumerated safely, requires Lilies
    to precede the intersecting shell-owned Progman/WorkerW windows in the
    top-to-bottom z-order.  Unknown native evidence is not treated as failure;
    the Qt exposure contract remains authoritative on other platforms and in
    offscreen verification.
    """

    _RETRY_DELAYS_MS = (0, 40, 100, 220, 450, 800, 1300, 1900)
    _DWM_CLOAKED_ATTRIBUTE = 14
    _WALLPAPER_CLASSES = frozenset({"Progman", "WorkerW"})

    def __init__(
        self,
        window: QQuickWindow,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self.window = window
        self._request_serial = 0
        self._show_normal_used = False
        self._remap_used = False
        self._remap_hidden = False
        self.attempt_count = 0
        self.success_count = 0
        self.show_normal_count = 0
        self.remap_count = 0
        self.last_evidence: dict[str, object] = {}

    @Slot(result=int)
    def requestPresentation(self) -> int:
        """Start one finite verification generation and cancel older retries."""

        if self._remap_hidden:
            self._recover_remap()
        self._request_serial += 1
        request_serial = self._request_serial
        self._show_normal_used = False
        self._remap_used = False
        for attempt, delay_ms in enumerate(self._RETRY_DELAYS_MS):
            QTimer.singleShot(
                delay_ms,
                lambda serial=request_serial, index=attempt: self._probe(serial, index),
            )
        return request_serial

    @Slot(bool)
    def cancelPending(self, recover_remap: bool = True) -> None:
        self._request_serial += 1
        if recover_remap:
            self._recover_remap()
        else:
            # The owning QML surface is deliberately transitioning to compact
            # or hidden.  In that case its visible binding, not the probe,
            # owns the final state.
            self._remap_hidden = False

    @staticmethod
    def _rectangles_intersect(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> bool:
        return (
            min(first[2], second[2]) > max(first[0], second[0])
            and min(first[3], second[3]) > max(first[1], second[1])
        )

    @classmethod
    def _worker_layer_is_valid(
        cls,
        *,
        target_handle: int,
        target_pid: int,
        shell_pid: int,
        target_rect: tuple[int, int, int, int],
        rows: list[dict[str, object]],
    ) -> bool | None:
        """Evaluate already-enumerated top-to-bottom rows without Win32 calls."""

        target_index: int | None = None
        wallpaper_indices: list[int] = []
        unknown_wallpaper_indices: list[int] = []
        for index, row in enumerate(rows):
            handle = int(row.get("handle", 0) or 0)
            if handle == target_handle:
                if int(row.get("pid", 0) or 0) != target_pid:
                    return False
                target_index = index
                continue
            if (
                int(row.get("pid", 0) or 0) != shell_pid
                or str(row.get("class", "")) not in cls._WALLPAPER_CLASSES
                or not bool(row.get("visible", False))
                or bool(row.get("iconic", False))
                or row.get("cloaked") is True
            ):
                continue
            row_rect = row.get("rect")
            if not isinstance(row_rect, tuple) or len(row_rect) != 4:
                continue
            if cls._rectangles_intersect(target_rect, row_rect):
                if row.get("cloaked") is None:
                    unknown_wallpaper_indices.append(index)
                else:
                    wallpaper_indices.append(index)
        if target_index is None:
            return None
        if any(index < target_index for index in wallpaper_indices):
            return False
        if any(index < target_index for index in unknown_wallpaper_indices):
            return None
        if not wallpaper_indices:
            return None
        return True

    @staticmethod
    def _platform_is_qwindows() -> bool:
        if os.name != "nt":
            return False
        try:
            return str(QApplication.platformName()).casefold() == "windows"
        except (AttributeError, RuntimeError, TypeError):
            return False

    @staticmethod
    def _configure_user32_readonly(user32: object) -> None:
        """Declare pointer-sized signatures for the read-only Win32 probe."""

        user32.GetShellWindow.argtypes = []
        user32.GetShellWindow.restype = wintypes.HWND
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        user32.GetWindowRect.restype = wintypes.BOOL

    @classmethod
    def _dwm_cloaked(cls, hwnd: int) -> bool | None:
        try:
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
            value = wintypes.DWORD(0)
            result = int(
                dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    wintypes.DWORD(cls._DWM_CLOAKED_ATTRIBUTE),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
            )
            return bool(value.value) if result == 0 else None
        except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
            return None

    @classmethod
    def _enumerate_native_rows(
        cls,
        user32: object,
    ) -> tuple[list[dict[str, object]], int]:
        shell_handle = int(user32.GetShellWindow() or 0)
        shell_process = wintypes.DWORD(0)
        if shell_handle:
            user32.GetWindowThreadProcessId(
                wintypes.HWND(shell_handle), ctypes.byref(shell_process)
            )
        rows: list[dict[str, object]] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        def collect(hwnd: int, _lparam: int) -> bool:
            handle = int(hwnd)
            process = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
            class_buffer = ctypes.create_unicode_buffer(96)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            rect = wintypes.RECT()
            has_rect = bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
            rows.append(
                {
                    "handle": handle,
                    "pid": int(process.value),
                    "class": class_buffer.value,
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "iconic": bool(user32.IsIconic(hwnd)),
                    "cloaked": cls._dwm_cloaked(handle),
                    "rect": (
                        int(rect.left),
                        int(rect.top),
                        int(rect.right),
                        int(rect.bottom),
                    )
                    if has_rect
                    else None,
                }
            )
            return True

        callback = callback_type(collect)
        if not bool(user32.EnumWindows(callback, 0)):
            return [], int(shell_process.value)
        return rows, int(shell_process.value)

    def _native_evidence(self) -> dict[str, object]:
        evidence: dict[str, object] = {
            "available": False,
            "validHandle": None,
            "pidMatches": None,
            "visible": None,
            "iconic": None,
            "cloaked": None,
            "workerLayerOk": None,
            "passed": True,
        }
        if not self._platform_is_qwindows():
            return evidence
        # Once qwindows is known, a failed native read is unknown evidence,
        # not permission to claim an immediate Qt-only success.  The bounded
        # probe may degrade only on its final scheduled attempt.
        evidence["available"] = True
        evidence["passed"] = None
        try:
            handle = int(self.window.winId())
            user32 = ctypes.windll.user32
            self._configure_user32_readonly(user32)
            valid_handle = bool(user32.IsWindow(wintypes.HWND(handle)))
            evidence["validHandle"] = valid_handle
            if not valid_handle:
                evidence["passed"] = False
                return evidence
            process = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(
                wintypes.HWND(handle), ctypes.byref(process)
            )
            pid_matches = int(process.value) == os.getpid()
            native_visible = bool(user32.IsWindowVisible(wintypes.HWND(handle)))
            iconic = bool(user32.IsIconic(wintypes.HWND(handle)))
            cloaked = self._dwm_cloaked(handle)
            target_rect_value = wintypes.RECT()
            has_rect = bool(
                user32.GetWindowRect(
                    wintypes.HWND(handle), ctypes.byref(target_rect_value)
                )
            )
            worker_layer_ok: bool | None = None
            if has_rect and pid_matches:
                rows, shell_pid = self._enumerate_native_rows(user32)
                if shell_pid > 0 and rows:
                    worker_layer_ok = self._worker_layer_is_valid(
                        target_handle=handle,
                        target_pid=int(process.value),
                        shell_pid=shell_pid,
                        target_rect=(
                            int(target_rect_value.left),
                            int(target_rect_value.top),
                            int(target_rect_value.right),
                            int(target_rect_value.bottom),
                        ),
                        rows=rows,
                    )
            evidence.update(
                {
                    "pidMatches": pid_matches,
                    "visible": native_visible,
                    "iconic": iconic,
                    "cloaked": cloaked,
                    "workerLayerOk": worker_layer_ok,
                    "passed": self._combine_native_presentation_state(
                        pid_matches=pid_matches,
                        visible=native_visible,
                        iconic=iconic,
                        cloaked=cloaked,
                        worker_layer_ok=worker_layer_ok,
                    ),
                }
            )
        except (
            AttributeError,
            ctypes.ArgumentError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            # The native layer is optional evidence.  A failed read must not
            # manufacture a false positive/negative or mutate Explorer.
            return evidence
        return evidence

    @staticmethod
    def _combine_native_presentation_state(
        *,
        pid_matches: bool,
        visible: bool,
        iconic: bool,
        cloaked: bool | None,
        worker_layer_ok: bool | None,
    ) -> bool | None:
        """Combine fail/unknown/pass without turning unknown into success."""

        if (
            not pid_matches
            or not visible
            or iconic
            or cloaked is True
            or worker_layer_ok is False
        ):
            return False
        if cloaked is None or worker_layer_ok is None:
            return None
        return bool(worker_layer_ok)

    def _recover_remap(self) -> None:
        if not self._remap_hidden:
            return
        try:
            self.window.show()
        except (AttributeError, RuntimeError, TypeError):
            return
        self._remap_hidden = False

    def _finish_remap(self, request_serial: int) -> None:
        if request_serial != self._request_serial:
            return
        self._recover_remap()

    def _probe(self, request_serial: int, attempt: int) -> None:
        if request_serial != self._request_serial:
            return
        self.attempt_count += 1
        try:
            visible = bool(self.window.isVisible())
            exposed = bool(self.window.isExposed())
            visibility = self.window.visibility()
            windowed = visibility not in {
                QWindow.Visibility.Hidden,
                QWindow.Visibility.Minimized,
            }
        except (AttributeError, RuntimeError, TypeError):
            return
        native = self._native_evidence()
        qt_ready = bool(visible and exposed and windowed)
        native_pending = bool(
            native.get("available", False) and native.get("passed") is None
        )
        final_attempt = attempt >= len(self._RETRY_DELAYS_MS) - 1
        degraded = bool(qt_ready and native_pending and final_attempt)
        passed = bool(
            qt_ready
            and (
                native.get("passed", True) is True
                or degraded
            )
        )
        self.last_evidence = {
            "visible": visible,
            "exposed": exposed,
            "windowed": windowed,
            "native": native,
            "attempt": int(attempt),
            "nativePending": native_pending,
            "degraded": degraded,
            "passed": passed,
        }
        if passed:
            self.success_count += 1
            self._request_serial += 1
            return
        if qt_ready and native_pending:
            # Explorer can temporarily rebuild its WorkerW/Progman hosts.
            # Unknown z-order evidence is a bounded wait, not either a false
            # success or a reason to remap an otherwise healthy Qt surface.
            return
        if not self._show_normal_used:
            self._show_normal_used = True
            try:
                self.window.showNormal()
                self.show_normal_count += 1
            except (AttributeError, RuntimeError, TypeError):
                pass
            return
        if attempt >= 2 and not self._remap_used:
            self._remap_used = True
            try:
                self.window.hide()
                self._remap_hidden = True
                self.remap_count += 1
            except (AttributeError, RuntimeError, TypeError):
                return
            QTimer.singleShot(
                0, lambda serial=request_serial: self._finish_remap(serial)
            )


def configure_quick_window_resource_lifecycle(
    root: QQuickWindow,
) -> QuickWindowResourceLifecycle:
    """Install and return the app-owned hidden-window lifecycle controller."""

    return QuickWindowResourceLifecycle(root)


PACKAGED_SELF_TEST_EXPECTED_QUICK_WINDOW_COUNT = 16


def packaged_compact_startup_contract(
    lifecycle: QuickWindowResourceLifecycle,
    compact_lilith: QObject | None,
    runtime_snapshot: dict[str, object],
) -> dict[str, object]:
    """Project the compact cold-start resource invariants into safe JSON data.

    The packaged report intentionally records only stable window persistence,
    animation-budget and loader fields.  It never copies the full backend
    snapshot, module inventory or another mutable implementation detail.
    Missing or ill-typed values fail closed instead of being coerced true.
    """

    persistent_hints: list[dict[str, object]] = []
    for index, window in enumerate(tuple(lifecycle.windows)):
        try:
            object_name = str(window.objectName() or "").strip()
        except (AttributeError, RuntimeError, TypeError):
            object_name = ""
        try:
            persistent_graphics_value = window.isPersistentGraphics()
            persistent_graphics = (
                persistent_graphics_value
                if isinstance(persistent_graphics_value, bool)
                else None
            )
        except (AttributeError, RuntimeError, TypeError):
            persistent_graphics = None
        try:
            persistent_scene_graph_value = window.isPersistentSceneGraph()
            persistent_scene_graph = (
                persistent_scene_graph_value
                if isinstance(persistent_scene_graph_value, bool)
                else None
            )
        except (AttributeError, RuntimeError, TypeError):
            persistent_scene_graph = None
        persistent_hints.append(
            {
                "objectName": object_name
                or ("desktopRootWindow" if index == 0 else f"unnamedWindow{index}"),
                "persistentGraphics": persistent_graphics,
                "persistentSceneGraph": persistent_scene_graph,
            }
        )

    expected_window_count = PACKAGED_SELF_TEST_EXPECTED_QUICK_WINDOW_COUNT
    window_count = len(persistent_hints)
    persistent_hints_disabled = bool(
        window_count == expected_window_count
        and all(
            hint["persistentGraphics"] is False
            and hint["persistentSceneGraph"] is False
            for hint in persistent_hints
        )
    )
    quick_windows = {
        "expectedWindowCount": expected_window_count,
        "windowCount": window_count,
        "persistentHintsDisabled": persistent_hints_disabled,
        "persistentHints": persistent_hints,
        "passed": persistent_hints_disabled,
    }

    low_power: bool | None = None
    target_fps: int | None = None
    if compact_lilith is not None:
        try:
            low_power_value = compact_lilith.property("lowPower")
            if isinstance(low_power_value, bool):
                low_power = low_power_value
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            target_fps_value = compact_lilith.property("targetFps")
            if isinstance(target_fps_value, (int, float)) and not isinstance(
                target_fps_value, bool
            ):
                target_fps = int(target_fps_value)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    shell_mode_value = runtime_snapshot.get("shellMode")
    shell_mode = shell_mode_value if isinstance(shell_mode_value, str) else None
    compact_idle_passed = bool(
        shell_mode == "compact" and low_power is True and target_fps == 15
    )
    compact_idle = {
        "lowPower": low_power,
        "targetFps": target_fps,
        "passed": compact_idle_passed,
    }

    scene_value = runtime_snapshot.get("scene")
    scene = scene_value if isinstance(scene_value, dict) else {}
    scene2d_value = scene.get("scene2dLoaded")
    scene2d_loaded = scene2d_value if isinstance(scene2d_value, bool) else None
    video_value = scene.get("videoLoaded")
    video_loaded = video_value if isinstance(video_value, bool) else None
    playback_value = scene.get("videoPlaybackState")
    video_playback_state = playback_value if isinstance(playback_value, str) else None
    scene_loaders_passed = bool(
        shell_mode == "compact"
        and scene2d_loaded is False
        and video_loaded is False
        and video_playback_state == "unloaded"
    )
    scene_loaders = {
        "scene2dLoaded": scene2d_loaded,
        "videoLoaded": video_loaded,
        "videoPlaybackState": video_playback_state,
        "passed": scene_loaders_passed,
    }

    passed = bool(
        quick_windows["passed"]
        and compact_idle["passed"]
        and scene_loaders["passed"]
    )
    return {
        "shellMode": shell_mode,
        "quickWindows": quick_windows,
        "compactIdle": compact_idle,
        "sceneLoaders": scene_loaders,
        "passed": passed,
    }


def tray_icon() -> QIcon:
    pixmap = QPixmap(96, 96)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#9b9487"), 2))
    painter.setBrush(QColor("#fffdf7"))
    painter.drawEllipse(8, 8, 80, 80)
    painter.setPen(QPen(QColor("#b9b1a4"), 2))
    painter.drawArc(23, 23, 50, 50, 22 * 16, 300 * 16)
    painter.setBrush(QColor("#9f3129"))
    painter.setPen(QPen(QColor("#9f3129"), 1))
    painter.drawEllipse(68, 44, 10, 10)
    painter.end()
    return QIcon(pixmap)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Lilies in the box")
    parser.add_argument("--smoke", action="store_true", help="render without modifying Explorer")
    shell_mode = parser.add_mutually_exclusive_group()
    shell_mode.add_argument("--visual", action="store_true", help="start with the full Lilies desktop")
    shell_mode.add_argument("--compact", action="store_true", help="start as compact box")
    shell_mode.add_argument("--login-shell", action="store_true", help="start from experimental login shell")
    parser.add_argument("--restore", action="store_true", help="restore Windows shell state and exit")
    parser.add_argument("--benchmark", metavar="JSON_PATH", help="measure renderer FPS and write a JSON report")
    parser.add_argument("--benchmark-seconds", type=float, default=8.0, help=argparse.SUPPRESS)
    parser.add_argument("--benchmark-renderer", choices=("scene2d", "video"), help=argparse.SUPPRESS)
    parser.add_argument("--self-test", metavar="JSON_PATH", help="run packaged identity and QML checks")
    parser.add_argument(
        "--windows-startup-probe",
        metavar="JSON_PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--watchdog", nargs=3, metavar=("PID", "BACKUP", "MARKER"), help=argparse.SUPPRESS)
    parser.add_argument("--peek-data-dir", help=argparse.SUPPRESS)
    parser.add_argument(
        "--native-capture-helper",
        nargs=3,
        metavar=("HWND", "PID", "DESTINATION"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--native-capture-max-edge",
        type=int,
        default=1600,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def activation_action(args: argparse.Namespace) -> str:
    if bool(args.visual):
        return "visual"
    if bool(args.compact):
        return "compact"
    return "show"


def tray_activation_shows_surface(
    reason: QSystemTrayIcon.ActivationReason,
) -> bool:
    """Return whether one tray gesture should reveal the current surface."""

    return reason in {
        QSystemTrayIcon.ActivationReason.Trigger,
        QSystemTrayIcon.ActivationReason.DoubleClick,
    }


def format_tray_status(
    habitat_status: object,
    focus_status: object,
) -> str:
    """Return a short, title-free runtime status for the tray surface."""

    habitat = habitat_status if isinstance(habitat_status, dict) else {}
    focus = focus_status if isinstance(focus_status, dict) else {}
    state = str(habitat.get("state", "")).strip().casefold()
    reason = str(habitat.get("reason", "")).strip().casefold()
    if state == "silent" or reason == "full-screen":
        presence = "全屏界面中静默"
    elif state == "blocked" or reason == "sensitive-window":
        presence = "受保护界面中隐藏"
    elif state == "avoiding":
        presence = "正在避开鼠标"
    elif state == "attached":
        size_label = {
            "tiny": "极小窗口",
            "small": "小窗口",
            "medium": "中等窗口",
            "large": "大窗口",
        }.get(str(habitat.get("windowSizeClass", "")).casefold(), "窗口")
        presence = f"在{size_label}边缘栖息"
    elif state == "waiting":
        presence = "等待窗口稳定"
    elif state == "detached":
        presence = "自由站立"
    elif state == "desktop" and reason == "no-host":
        presence = "桌面停驻"
    elif state == "desktop" and reason == "host-unavailable":
        presence = "保持原位"
    elif bool(habitat.get("visible", True)):
        presence = "莉莉丝已显示"
    else:
        presence = "莉莉丝暂时隐藏"

    if not bool(focus.get("active", False)):
        return presence
    try:
        planned = max(
            0,
            int(
                focus.get(
                    "planned_seconds",
                    int(focus.get("durationMinutes", 25)) * 60,
                )
            ),
        )
        elapsed = max(0, int(focus.get("elapsedSeconds", 0)))
    except (TypeError, ValueError):
        planned = 0
        elapsed = 0
    remaining = max(0, planned - elapsed)
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    time_text = (
        f"{hours:d}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes:02d}:{seconds:02d}"
    )
    focus_label = "专注已暂停" if bool(focus.get("paused", False)) else "专注"
    return f"{presence} · {focus_label} {time_text}"


def format_tray_tooltip(
    habitat_status: object,
    focus_status: object,
) -> str:
    return f"{APP_NAME} · {format_tray_status(habitat_status, focus_status)}"


def wait_for_existing_instance(
    args: argparse.Namespace,
    *,
    root: Path,
    port: int = PRIMARY_SOCKET_PORT,
    timeout: float = 1.2,
) -> bool:
    """Retry one startup-race activation without constructing a Backend."""

    deadline = time.monotonic() + max(0.05, float(timeout))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if request_existing_instance(
            root,
            activation_action(args),
            port=port,
            # The canonical socket reports either the applied result or an
            # explicit pending ACK.  Both prove that it owns this exact action;
            # a pending ACK prevents duplicate launchers while QML finishes a
            # heavier desktop transition.
            timeout=min(1.0, remaining),
        ):
            return True
        time.sleep(min(0.04, max(0.0, deadline - time.monotonic())))


def reserve_primary_endpoint_or_forward(
    args: argparse.Namespace,
    *,
    root: Path,
    port: int = PRIMARY_SOCKET_PORT,
    timeout: float = 1.2,
) -> tuple[object | None, bool]:
    """Reserve the fixed endpoint, or forward after a short startup wait."""

    try:
        return LocalSocketServer.reserve_endpoint(port), False
    except OSError:
        return None, wait_for_existing_instance(
            args,
            root=root,
            port=port,
            timeout=timeout,
        )


def forward_to_existing_instance(args: argparse.Namespace) -> bool:
    """Forward normal launcher invocations before constructing a second Backend."""

    if (
        args.smoke
        or args.benchmark
        or args.self_test
        or args.windows_startup_probe
        or args.login_shell
    ):
        return False
    try:
        root = data_root()
    except DataRootUnavailableError:
        return False
    return request_existing_instance(root, activation_action(args))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.native_capture_helper:
        # This branch deliberately runs before QApplication, cache setup,
        # single-instance forwarding or any visible Lilies surface.
        from .core.native_capture_helper import run_native_capture_helper

        return run_native_capture_helper(
            args.native_capture_helper[0],
            args.native_capture_helper[1],
            args.native_capture_helper[2],
            max_edge=args.native_capture_max_edge,
        )
    if args.watchdog:
        from .watchdog import watch

        return watch(
            int(args.watchdog[0]),
            Path(args.watchdog[1]),
            Path(args.watchdog[2]),
            Path(args.peek_data_dir) if args.peek_data_dir else None,
        )
    if args.restore:
        try:
            restore_root = data_root()
        except DataRootUnavailableError:
            restore_root = WINDOWS_PRIVATE_DATA_ROOT
        restore_from_backup(restore_root / "shell-backup.json")
        return 0

    # A normal, desktop-only or pet-only shortcut activates the already
    # running process.  This happens before Backend creates a new socket token,
    # database connection, tray icon or desktop window.
    if forward_to_existing_instance(args):
        return 0

    # Smoke tests, benchmarks and packaged probes must never reuse or mutate
    # the person's real desktop mode, chat history or memory database.
    diagnostic_data: tempfile.TemporaryDirectory[str] | None = None
    if (
        args.smoke
        or args.benchmark
        or args.self_test
        or args.windows_startup_probe
    ) and not os.environ.get("LILIES_DATA_DIR"):
        diagnostic_data = tempfile.TemporaryDirectory(prefix="lilies-diagnostic-")
        os.environ["LILIES_DATA_DIR"] = diagnostic_data.name

    # Qt derives its QML bytecode and scene-graph pipeline caches from
    # LOCALAPPDATA unless these paths are fixed before QApplication exists.
    # Establish the same fail-closed F: boundary used by the database first.
    startup_data_error: DataRootUnavailableError | None = None
    qt_cache_routing: dict[str, object] = {
        "passed": False,
        "error": "Qt cache routing was not configured",
    }
    try:
        qt_cache_routing = configure_qt_cache_environment(data_root())
    except DataRootUnavailableError as exc:
        startup_data_error = exc
        qt_cache_routing = {
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        disable_qt_disk_caches_for_recovery()

    if args.self_test:
        # A packaged self-test is an unattended release probe, not a visual
        # acceptance run.  Make that safety property intrinsic to the EXE so
        # a caller cannot accidentally flash Lilies on the person's desktop
        # or make the result depend on the real screen/compositor.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["QT_QPA_OFFSCREEN_SIZE"] = "1200x900"
        os.environ["QSG_RHI_BACKEND"] = "software"
        os.environ["QT_QUICK_BACKEND"] = "software"
    elif args.windows_startup_probe:
        # Exercise qwindows and a real HWND without publishing any visible
        # desktop surface.  The ordinary packaged self-test deliberately uses
        # offscreen, so it cannot detect native-message startup recursion.
        os.environ["QT_QPA_PLATFORM"] = "windows"
        os.environ["QSG_RHI_BACKEND"] = "software"
        os.environ["QT_QUICK_BACKEND"] = "software"
    else:
        os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")
    # Lilies supplies its own paper surfaces, labels and indicators throughout
    # Qt Quick Controls.  Native Windows controls intentionally reject several
    # of those custom content/background delegates and then render a mixture of
    # native and Lilies chrome.  Select the supported customizable style before
    # QApplication exists so production and packaged probes use the same UI.
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([sys.argv[0], *(argv or sys.argv[1:])])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Lilies in the box")
    app.setApplicationVersion("0.3.42")
    app.setWindowIcon(tray_icon())

    if startup_data_error is not None:
        restore_from_backup(WINDOWS_PRIVATE_DATA_ROOT / "shell-backup.json")
        QMessageBox.critical(
            None,
            "Lilies · 受限恢复模式",
            f"{startup_data_error}\n\n已恢复 Windows 桌面和任务栏。重新连接 F 盘后再启动；"
            "没有数据或 Qt 缓存写入 C 盘。",
        )
        return 3

    diagnostic_run = bool(
        args.smoke
        or args.benchmark
        or args.self_test
        or args.windows_startup_probe
    )
    activation_socket = None
    if not diagnostic_run:
        try:
            startup_root = data_root()
        except DataRootUnavailableError as exc:
            restore_from_backup(WINDOWS_PRIVATE_DATA_ROOT / "shell-backup.json")
            QMessageBox.critical(
                None,
                "Lilies · 受限恢复模式",
                f"{exc}\n\n已恢复 Windows 桌面和任务栏。重新连接 F 盘后再启动；"
                "没有数据写入 C 盘。",
            )
            return 3
        activation_socket, forwarded = reserve_primary_endpoint_or_forward(
            args,
            root=startup_root,
        )
        if forwarded:
            return 0
        if activation_socket is None:
            QMessageBox.warning(
                None,
                "Lilies in the box",
                "另一个本地进程占用了 Lilies 的单实例入口，"
                "但没有通过身份校验。为避免打开两套桌面，本次启动已停止。",
            )
            return 4

    try:
        backend = Backend(
            smoke=(
                args.smoke
                or bool(args.benchmark)
                or bool(args.self_test)
                or bool(args.windows_startup_probe)
            ),
            force_compact=(
                args.compact
                or bool(args.self_test)
                or bool(args.windows_startup_probe)
            ),
            force_visual=(
                args.visual
                and not bool(args.self_test)
                and not bool(args.windows_startup_probe)
            ),
            force_login=args.login_shell,
            activation_socket=activation_socket,
        )
    except DataRootUnavailableError as exc:
        if activation_socket is not None:
            activation_socket.close()
        # Restricted recovery mode deliberately owns no database, cache or
        # runtime directory. It only restores Explorer chrome and explains why
        # Lilies refused to fall back to the system drive.
        restore_from_backup(WINDOWS_PRIVATE_DATA_ROOT / "shell-backup.json")
        QMessageBox.critical(
            None,
            "Lilies · 受限恢复模式",
            f"{exc}\n\n已恢复 Windows 桌面和任务栏。重新连接 F 盘后再启动；没有数据写入 C 盘。",
        )
        return 3
    except OSError as exc:
        if activation_socket is not None:
            activation_socket.close()
        QMessageBox.warning(
            None,
            "Lilies in the box",
            f"Lilies 的单实例入口没有成功建立：{exc}",
        )
        return 4
    except Exception:
        if activation_socket is not None:
            activation_socket.close()
        raise
    # Keep QML diagnostics as release evidence.  Qt otherwise writes these
    # warnings only to stderr, where a hidden packaged probe can appear to
    # pass even though a binding, signal handler or resource failed at
    # runtime.  Capture before ``load`` so construction-time warnings are not
    # lost; the packaged self-test requires the complete list to remain empty.
    qml_warning_messages: list[str] = []
    qml_warning_count = 0

    def record_qml_warnings(warnings: object) -> None:
        nonlocal qml_warning_count
        try:
            warning_items = tuple(warnings)  # type: ignore[arg-type]
        except TypeError:
            warning_items = (warnings,)
        for warning in warning_items:
            qml_warning_count += 1
            formatter = getattr(warning, "toString", None)
            try:
                message = str(formatter() if callable(formatter) else warning).strip()
            except (RuntimeError, TypeError, ValueError):
                message = str(warning).strip()
            if not message:
                message = "unknown QML warning"
            # Bound diagnostic growth if a repeating binding emits a warning
            # every frame.  The separate total still proves any warning
            # occurred, while the report retains at most 128 distinct samples.
            message = message[:4000]
            if (
                message not in qml_warning_messages
                and len(qml_warning_messages) < 128
            ):
                qml_warning_messages.append(message)

    engine = QQmlApplicationEngine()
    engine.warnings.connect(record_qml_warnings)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty(
        "diagnosticWindowProbe", bool(args.windows_startup_probe)
    )
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        backend.shutdown()
        raise RuntimeError(f"could not load QML: {qml_path()}")
    root_window = engine.rootObjects()[0]
    if args.windows_startup_probe:
        # QML suppresses the two normally visible startup surfaces before
        # construction. Hide every retained auxiliary window as a second,
        # fail-closed boundary before qwindows begins the event-loop probe.
        for diagnostic_window in (
            root_window,
            *tuple(root_window.findChildren(QQuickWindow)),
        ):
            diagnostic_window.hide()
    quick_window_lifecycle = configure_quick_window_resource_lifecycle(root_window)
    app._lilies_quick_window_lifecycle = quick_window_lifecycle
    pet_window = root_window.findChild(QQuickWindow, "petWindow")
    if pet_window is None:
        backend.shutdown()
        raise RuntimeError("could not load independent pet window")
    companion_bubble_window = root_window.findChild(
        QQuickWindow, "companionBubbleWindow"
    )
    if companion_bubble_window is None:
        backend.shutdown()
        raise RuntimeError("could not load companion bubble window")
    companion_presentation_probe = CompanionBubblePresentationProbe(
        companion_bubble_window,
        backend.companion,
        app,
    )
    companion_bubble_window.setProperty(
        "nativePresentationController", companion_presentation_probe
    )
    app._lilies_companion_presentation_probe = companion_presentation_probe
    desktop_presentation_probe = DesktopSurfacePresentationProbe(root_window, app)
    root_window.setProperty(
        "nativeDesktopPresentationController", desktop_presentation_probe
    )
    app._lilies_desktop_presentation_probe = desktop_presentation_probe
    backend.set_desktop_window_handle(int(root_window.winId()))
    pointer_event_filter = CompactPointerEventFilter(pet_window)
    pet_window.setProperty("nativeMoveController", pointer_event_filter)
    pet_window.installEventFilter(pointer_event_filter)
    app._lilies_pointer_event_filter = pointer_event_filter
    # Materialize and cache the HWND before installing the application-wide
    # native filter. Never resolve winId() from inside the filter callback.
    pet_native_window_id = int(pet_window.winId())
    hit_test_filter = CompactHitTestFilter(
        pet_window,
        backend,
        native_move_controller=pointer_event_filter,
        native_window_id=pet_native_window_id,
    )
    app.installNativeEventFilter(hit_test_filter)
    app._lilies_hit_test_filter = hit_test_filter

    tray = QSystemTrayIcon(tray_icon(), app)
    menu = QMenu()
    tray_status = menu.addAction("状态：正在启动")
    tray_status.setEnabled(False)
    unread_companion = menu.addAction("未读陪伴：0")
    unread_companion.setVisible(False)
    menu.addSeparator()
    visual = menu.addAction("展开 Lilies 桌面")
    compact = menu.addAction("收进盒子")
    world = menu.addAction("进入盒中世界")
    system_drawer = menu.addAction("临时显示 Windows 系统栏")
    menu.addSeparator()
    restore = menu.addAction("紧急恢复 Windows")
    exit_action = menu.addAction("退出并恢复 Windows")
    visual.triggered.connect(lambda: backend.setShellMode("visual"))
    compact.triggered.connect(lambda: backend.setShellMode("compact"))
    world.triggered.connect(backend.enterBoxWorld)
    system_drawer.triggered.connect(backend.revealSystemDrawer)
    restore.triggered.connect(backend.emergencyRestore)
    exit_action.triggered.connect(backend.exitAndRestore)
    unread_companion.triggered.connect(backend.companion.reopenUnread)
    tray.setContextMenu(menu)

    def refresh_tray_status() -> None:
        status_text = format_tray_status(backend.habitatState, backend.focusStatus)
        tray_status.setText(f"状态：{status_text}")
        unread_count = int(
            backend.companion.deliveryStatus.get("unreadCount", 0) or 0
        )
        unread_companion.setText(f"未读陪伴：{unread_count}")
        unread_companion.setVisible(unread_count > 0)
        unread_companion.setEnabled(
            unread_count > 0 and not backend.companionSuppressed
        )
        tray.setToolTip(format_tray_tooltip(backend.habitatState, backend.focusStatus))

    backend.habitatChanged.connect(refresh_tray_status)
    backend.productivityChanged.connect(refresh_tray_status)
    backend.companion.changed.connect(refresh_tray_status)
    refresh_tray_status()
    tray.activated.connect(
        lambda reason: backend.showCurrentSurface()
        if tray_activation_shows_surface(reason)
        else None
    )
    if not args.windows_startup_probe:
        tray.show()
    backend.reminderDue.connect(
        lambda title, body: tray.showMessage(
            f"莉莉丝 · {title}",
            body or "到时间了。",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )
    )

    app.aboutToQuit.connect(tray.hide)
    app.aboutToQuit.connect(backend.shutdown)
    backend.enter_initial_mode()
    if args.windows_startup_probe:
        output = Path(args.windows_startup_probe).resolve()
        diagnostic_pet = root_window.findChild(QQuickItem, "desktopPet")
        if diagnostic_pet is not None:
            # Keep every native window hidden, but materialize the same radial
            # delegate geometry that production uses.  The later User32
            # SendMessage calls then traverse the installed application-wide
            # native event filter instead of calling accepts_point directly.
            diagnostic_pet.setProperty("expanded", True)
        probe_state: dict[str, object] = {
            "eventLoopTicks": 0,
            "hitTestCountBefore": int(hit_test_filter.native_hit_test_count),
            "hitTestCountAfter": int(hit_test_filter.native_hit_test_count),
            "hitTestResult": None,
            "radialWorldHitResult": None,
            "desktopModeTabHitResult": None,
            "transparentCornerHitResult": None,
            "radialWorldActionFound": False,
            "desktopModeTabFound": False,
            "dispatchError": "",
        }
        heartbeat = QTimer(app)
        heartbeat.setInterval(25)

        def record_windows_startup_heartbeat() -> None:
            probe_state["eventLoopTicks"] = int(probe_state["eventLoopTicks"]) + 1

        heartbeat.timeout.connect(record_windows_startup_heartbeat)
        heartbeat.start()

        def dispatch_windows_startup_hit_test() -> None:
            try:
                rect = wintypes.RECT()
                user32 = ctypes.windll.user32
                get_window_rect = user32.GetWindowRect
                get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
                get_window_rect.restype = wintypes.BOOL
                if not get_window_rect(pet_native_window_id, ctypes.byref(rect)):
                    raise ctypes.WinError()
                send_message = user32.SendMessageW
                send_message.argtypes = (
                    wintypes.HWND,
                    wintypes.UINT,
                    wintypes.WPARAM,
                    wintypes.LPARAM,
                )
                send_message.restype = wintypes.LPARAM

                device_pixel_ratio = float(pet_window.devicePixelRatio() or 1.0)

                def send_local_hit(logical_x: float, logical_y: float) -> int:
                    screen_x = int(round(rect.left + logical_x * device_pixel_ratio))
                    screen_y = int(round(rect.top + logical_y * device_pixel_ratio))
                    packed_point = (screen_x & 0xFFFF) | ((screen_y & 0xFFFF) << 16)
                    return int(
                        send_message(
                            pet_native_window_id,
                            CompactHitTestFilter.WM_NCHITTEST,
                            0,
                            packed_point,
                        )
                    )

                probe_state["hitTestResult"] = send_local_hit(
                    max(1.0, pet_window.width() / 2.0),
                    max(1.0, pet_window.height() / 2.0),
                )
                parent_item = pet_window.contentItem()
                world_action = next(
                    (
                        item
                        for item in hit_test_filter._visual_descendants(parent_item)
                        if item.objectName() == "desktopPetAction_world"
                    ),
                    None,
                )
                probe_state["radialWorldActionFound"] = world_action is not None
                if world_action is not None:
                    action_center = world_action.mapToItem(
                        parent_item,
                        QPointF(world_action.width() / 2.0, world_action.height() / 2.0),
                    )
                    probe_state["radialWorldHitResult"] = send_local_hit(
                        action_center.x(), action_center.y()
                    )
                # The permanent shell-form tab is deliberately hidden while
                # the radial menu is expanded.  Collapse that menu after its
                # native hit, then exercise the tab through the same real
                # WM_NCHITTEST dispatch used by production qwindows.
                if diagnostic_pet is not None:
                    diagnostic_pet.setProperty("expanded", False)
                desktop_mode_tab = next(
                    (
                        item
                        for item in hit_test_filter._visual_descendants(parent_item)
                        if item.objectName() == "desktopPetDesktopModeTab"
                    ),
                    None,
                )
                probe_state["desktopModeTabFound"] = desktop_mode_tab is not None
                if desktop_mode_tab is not None:
                    tab_center = desktop_mode_tab.mapToItem(
                        parent_item,
                        QPointF(
                            desktop_mode_tab.width() / 2.0,
                            desktop_mode_tab.height() / 2.0,
                        ),
                    )
                    probe_state["desktopModeTabHitResult"] = send_local_hit(
                        tab_center.x(), tab_center.y()
                    )
                probe_state["transparentCornerHitResult"] = send_local_hit(1.0, 1.0)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                probe_state["dispatchError"] = f"{type(exc).__name__}: {exc}"
            finally:
                probe_state["hitTestCountAfter"] = int(
                    hit_test_filter.native_hit_test_count
                )

        def finish_windows_startup_probe() -> None:
            heartbeat.stop()
            windows = tuple(quick_window_lifecycle.windows)
            visible_windows: list[str] = []
            for index, window in enumerate(windows):
                try:
                    if window.isVisible():
                        visible_windows.append(
                            str(window.objectName() or f"quickWindow{index}")
                        )
                except RuntimeError:
                    visible_windows.append(f"destroyedWindow{index}")
            platform_name = str(app.platformName()).casefold()
            is_window = ctypes.windll.user32.IsWindow
            is_window.argtypes = (wintypes.HWND,)
            is_window.restype = wintypes.BOOL
            native_window_created = bool(
                pet_native_window_id > 0
                and is_window(pet_native_window_id)
            )
            root_native_window_id = int(root_window.winId())
            get_window_long = getattr(
                ctypes.windll.user32,
                "GetWindowLongPtrW",
                ctypes.windll.user32.GetWindowLongW,
            )
            get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
            get_window_long.restype = ctypes.c_ssize_t
            ws_ex_noactivate = 0x08000000
            root_no_activate_style = bool(
                int(get_window_long(root_native_window_id, -20))
                & ws_ex_noactivate
            )
            pet_no_activate_style = bool(
                int(get_window_long(pet_native_window_id, -20))
                & ws_ex_noactivate
            )
            hit_test_dispatched = bool(
                int(probe_state["hitTestCountAfter"])
                > int(probe_state["hitTestCountBefore"])
            )
            event_loop_responsive = int(probe_state["eventLoopTicks"]) >= 4
            tray_published = bool(tray.isVisible())
            native_radial_world_hit = bool(
                probe_state["radialWorldActionFound"]
                and int(probe_state["radialWorldHitResult"] or 0)
                == CompactHitTestFilter.HTCLIENT
            )
            native_desktop_mode_tab_hit = bool(
                probe_state["desktopModeTabFound"]
                and int(probe_state["desktopModeTabHitResult"] or 0)
                == CompactHitTestFilter.HTCLIENT
            )
            native_transparent_corner_pass = bool(
                int(probe_state["transparentCornerHitResult"] or 0)
                == CompactHitTestFilter.HTTRANSPARENT
            )
            passed = bool(
                platform_name == "windows"
                and native_window_created
                and hit_test_filter.native_window_id == pet_native_window_id
                and hit_test_dispatched
                and native_radial_world_hit
                and native_desktop_mode_tab_hit
                and native_transparent_corner_pass
                and root_no_activate_style
                and pet_no_activate_style
                and event_loop_responsive
                and not visible_windows
                and not tray_published
                and not probe_state["dispatchError"]
                and bool(qt_cache_routing.get("passed", False))
            )
            result = {
                "schemaVersion": 1,
                "applicationVersion": app.applicationVersion(),
                "executableSha256": hashlib.sha256(
                    Path(sys.executable).read_bytes()
                ).hexdigest().upper(),
                "probeKind": "hidden-qwindows-cold-start",
                "diagnosticPlatform": platform_name,
                "qmlLoaded": True,
                "nativeWindowCreated": native_window_created,
                "nativeWindowIdCached": bool(
                    hit_test_filter.native_window_id == pet_native_window_id
                ),
                "nativeHitTestDispatched": hit_test_dispatched,
                "nativeDispatchCount": int(hit_test_filter.native_dispatch_count),
                "nativeHitTestCount": int(hit_test_filter.native_hit_test_count),
                "hitTestResult": probe_state["hitTestResult"],
                "nativeRadialWorldHit": native_radial_world_hit,
                "nativeDesktopModeTabHit": native_desktop_mode_tab_hit,
                "nativeDesktopModeTabHitResult": probe_state[
                    "desktopModeTabHitResult"
                ],
                "nativeTransparentCornerPass": native_transparent_corner_pass,
                "rootNoActivateStyle": root_no_activate_style,
                "petNoActivateStyle": pet_no_activate_style,
                "radialWorldHitResult": probe_state["radialWorldHitResult"],
                "transparentCornerHitResult": probe_state[
                    "transparentCornerHitResult"
                ],
                "eventLoopTicks": int(probe_state["eventLoopTicks"]),
                "eventLoopResponsive": event_loop_responsive,
                "quickWindowCount": len(windows),
                "visibleQuickWindowCount": len(visible_windows),
                "visibleQuickWindows": visible_windows,
                "trayPublished": tray_published,
                "dispatchError": str(probe_state["dispatchError"]),
                "qtCacheRouting": qt_cache_routing,
                "passed": passed,
                "capturedAt": datetime.now(UTC).isoformat(),
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
            )
            app.exit(0 if passed else 1)

        QTimer.singleShot(720, dispatch_windows_startup_hit_test)
        QTimer.singleShot(1050, finish_windows_startup_probe)
    if args.self_test:
        output = Path(args.self_test).resolve()
        finished = {"value": False}
        self_test_started = {"value": False}

        def self_test_identity() -> dict[str, object]:
            return {
                "schemaVersion": 1,
                "applicationVersion": app.applicationVersion(),
                "executableSha256": hashlib.sha256(
                    Path(sys.executable).read_bytes()
                ).hexdigest().upper(),
                "capturedAt": datetime.now(UTC).isoformat(),
            }

        def finish_self_test(reply: str) -> None:
            if finished["value"] or self_test_started["value"]:
                return
            self_test_started["value"] = True
            compact_lilith = root_window.findChild(QObject, "compactLilith")
            compact_startup = packaged_compact_startup_contract(
                quick_window_lifecycle,
                compact_lilith,
                dict(backend.runtimeSnapshot()),
            )
            pet = root_window.findChild(QObject, "desktopPet")
            if pet is not None:
                pet.setProperty("expanded", True)

            box_world_scene = root_window.findChild(
                QQuickWindow, "boxWorldSceneWindow"
            )
            companion_bubble = root_window.findChild(
                QQuickWindow, "companionBubbleWindow"
            )
            focus_timer_aura = root_window.findChild(
                QQuickWindow, "v03FocusTimerAura"
            )
            focus_aura_surface = (
                focus_timer_aura.findChild(QQuickItem, "focusTimerAuraSurface")
                if focus_timer_aura is not None
                else None
            )
            focus_timer_animation: dict[str, object] = {
                "windowFound": focus_timer_aura is not None,
                "surfaceFound": focus_aura_surface is not None,
                "passed": False,
            }
            native_move_controller = pet_window.property("nativeMoveController")
            drag_probe: dict[str, object] = {
                "previewMode": bool(backend.previewMode),
                "nativeSystemMovePathPresent": bool(
                    callable(getattr(pet_window, "tryNativeSystemMove", None))
                    and native_move_controller is not None
                    and callable(
                        getattr(native_move_controller, "tryStartSystemMove", None)
                    )
                ),
                # An offscreen platform has no Windows compositor move loop.
                # Never promote a fallback result into a claim that the real
                # startSystemMove() path succeeded on Windows hardware.
                "nativeSystemMoveRuntimeVerified": False,
                "fallbackMovedWindow": False,
            }
            try:
                start_x = float(pet_window.x())
                start_y = float(pet_window.y())
                grab_x = max(1.0, float(pet_window.width()) * 0.5)
                grab_y = max(1.0, float(pet_window.height()) * 0.45)
                cursor_x = start_x + grab_x
                cursor_y = start_y + grab_y
                pet_window.setProperty("dragGrabOffsetX", grab_x)
                pet_window.setProperty("dragGrabOffsetY", grab_y)
                pet_window.setProperty("dragStartCursorX", cursor_x)
                pet_window.setProperty("dragStartCursorY", cursor_y)
                pet_window.setProperty("dragMoved", True)
                pet_window.setProperty("nativeSystemMoveActive", False)
                pet_window.setProperty("nativeSystemMoveAttempted", False)
                pet_window.setProperty("manualDragActive", True)
                pet_window.followPointerAt(cursor_x + 24.0, cursor_y + 16.0)
                end_x = float(pet_window.x())
                end_y = float(pet_window.y())
                delta_x = end_x - start_x
                delta_y = end_y - start_y
                drag_probe.update(
                    {
                        "start": [start_x, start_y],
                        "end": [end_x, end_y],
                        "delta": [delta_x, delta_y],
                        "nativeSystemMoveAttempted": bool(
                            pet_window.property("nativeSystemMoveAttempted")
                        ),
                        "nativeSystemMoveActive": bool(
                            pet_window.property("nativeSystemMoveActive")
                        ),
                        "fallbackMovedWindow": bool(
                            abs(delta_x - 24.0) <= 2.0
                            and abs(delta_y - 16.0) <= 2.0
                        ),
                    }
                )
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                drag_probe["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                pet_window.setProperty("manualDragActive", False)
                pet_window.setProperty("nativeSystemMoveActive", False)

            try:
                backend.enterBoxWorld()
            except (RuntimeError, TypeError, ValueError) as exc:
                drag_probe["boxWorldSetupError"] = f"{type(exc).__name__}: {exc}"

            if companion_bubble is not None:
                try:
                    backend.companion._bubble = {
                        "id": "packaged-self-test-proactive",
                        "visible": True,
                        "busy": False,
                        "category": "self-test",
                        "sceneLabel": "synthetic-offscreen",
                        "summary": "packaged proactive bubble probe",
                        "detail": "packaged proactive bubble probe",
                        "model": "synthetic-self-test",
                        "contextType": "application-signal",
                        "conversation": [],
                    }
                    backend.companion.bubbleChanged.emit()
                except (RuntimeError, TypeError, ValueError) as exc:
                    drag_probe["companionSetupError"] = (
                        f"{type(exc).__name__}: {exc}"
                    )

            def write_result() -> None:
                if finished["value"]:
                    return
                finished["value"] = True

                def visual_item(
                    window: QQuickWindow | None, object_name: str
                ) -> QQuickItem | None:
                    if window is None:
                        return None
                    try:
                        return next(
                            (
                                item
                                for item in hit_test_filter._visual_descendants(
                                    window.contentItem()
                                )
                                if item.objectName() == object_name
                            ),
                            None,
                        )
                    except (RuntimeError, TypeError, ValueError):
                        return None

                world_stage = visual_item(box_world_scene, "boxWorldSceneStage")
                world_presentation = {
                    "backendOpen": bool(backend.boxWorldSceneOpen),
                    "windowFound": box_world_scene is not None,
                    "windowVisible": bool(
                        box_world_scene is not None and box_world_scene.isVisible()
                    ),
                    "windowExposed": bool(
                        box_world_scene is not None and box_world_scene.isExposed()
                    ),
                    "requestedVisible": bool(
                        box_world_scene is not None
                        and box_world_scene.property("requestedVisible")
                    ),
                    "presentationCount": int(
                        box_world_scene.property("presentationCount") or 0
                    )
                    if box_world_scene is not None
                    else 0,
                    "stageFound": world_stage is not None,
                    "stageVisible": bool(
                        world_stage is not None
                        and world_stage.isVisible()
                        and world_stage.width() > 0
                        and world_stage.height() > 0
                        and world_stage.opacity() > 0.05
                    ),
                }
                box_world_presentation_passed = bool(
                    world_presentation["backendOpen"]
                    and world_presentation["windowFound"]
                    and world_presentation["windowVisible"]
                    and world_presentation["windowExposed"]
                    and world_presentation["requestedVisible"]
                    and int(world_presentation["presentationCount"]) >= 1
                    and world_presentation["stageFound"]
                    and world_presentation["stageVisible"]
                )

                bubble_body = visual_item(companion_bubble, "companionBodyText")
                proactive_bubble = {
                    "windowFound": companion_bubble is not None,
                    "windowVisible": bool(
                        companion_bubble is not None
                        and companion_bubble.isVisible()
                    ),
                    "windowExposed": bool(
                        companion_bubble is not None
                        and companion_bubble.isExposed()
                    ),
                    "presentationRevision": int(
                        companion_bubble.property("presentationRevision") or 0
                    )
                    if companion_bubble is not None
                    else 0,
                    "summary": str(
                        companion_bubble.property("summaryText") or ""
                    )
                    if companion_bubble is not None
                    else "",
                    "bodyFound": bubble_body is not None,
                    "bodyVisible": bool(
                        bubble_body is not None
                        and bubble_body.isVisible()
                        and bubble_body.width() > 0
                        and bubble_body.height() > 0
                        and bubble_body.opacity() > 0.05
                    ),
                }
                synthetic_proactive_bubble_visible = bool(
                    proactive_bubble["windowFound"]
                    and proactive_bubble["windowVisible"]
                    and proactive_bubble["windowExposed"]
                    and int(proactive_bubble["presentationRevision"]) >= 1
                    and proactive_bubble["summary"]
                    == "packaged proactive bubble probe"
                    and proactive_bubble["bodyFound"]
                    and proactive_bubble["bodyVisible"]
                )
                drag_fallback_verified = bool(
                    str(app.platformName()).casefold() == "offscreen"
                    and drag_probe["previewMode"]
                    and drag_probe["nativeSystemMovePathPresent"]
                    and not drag_probe.get("nativeSystemMoveActive", False)
                    and drag_probe["fallbackMovedWindow"]
                )
                timezone_result: dict[str, object]
                action_hit_results: dict[str, bool] = {}
                try:
                    content = pet_window.contentItem()
                    for item in hit_test_filter._visual_descendants(content):
                        name = item.objectName()
                        if not name.startswith("desktopPetAction_"):
                            continue
                        center = item.mapToItem(
                            content,
                            QPointF(item.width() / 2, item.height() / 2),
                        )
                        action_hit_results[name.removeprefix("desktopPetAction_")] = (
                            hit_test_filter.accepts_point(center.x(), center.y())
                        )
                except (RuntimeError, TypeError, ValueError):
                    action_hit_results = {}
                try:
                    tokyo = ZoneInfo("Asia/Tokyo")
                    new_york = ZoneInfo("America/New_York")
                    winter = datetime(2026, 1, 15, 12, tzinfo=new_york).utcoffset()
                    summer = datetime(2026, 7, 15, 12, tzinfo=new_york).utcoffset()
                    timezone_result = {
                        "timezoneDataPassed": bool(
                            tokyo.key == "Asia/Tokyo"
                            and new_york.key == "America/New_York"
                            and winter is not None
                            and summer is not None
                            and winter != summer
                        ),
                        "timezoneKeys": [tokyo.key, new_york.key],
                        "dstOffsets": [str(winter), str(summer)],
                    }
                except Exception as exc:
                    timezone_result = {
                        "timezoneDataPassed": False,
                        "timezoneError": f"{type(exc).__name__}: {exc}",
                    }
                result = {
                    **self_test_identity(),
                    "qmlLoaded": True,
                    "qmlWarningCount": qml_warning_count,
                    "qmlWarnings": list(qml_warning_messages),
                    "qmlWarningsPassed": qml_warning_count == 0,
                    "identityPassed": (
                        reply == "……你好。我是莉莉丝。"
                        or (reply.startswith("……你好，") and reply.endswith("。我是莉莉丝。"))
                    ),
                    "reply": reply,
                    "hasBackslash": "\\" in reply,
                    "desktopPetLoaded": bool(root_window.property("compactCharacterWidth")),
                    "independentPetWindowLoaded": pet_window is not None,
                    "petWindowAcceptsInput": not bool(
                        pet_window.flags() & Qt.WindowType.WindowTransparentForInput
                    ),
                    "petFloatMode": backend.petFloatMode,
                    "functionCount": int(root_window.property("compactActionCount") or 0),
                    "functionsVisible": bool(root_window.property("compactActionsVisible")),
                    "radialActionHitTests": action_hit_results,
                    "radialActionsAcceptInput": (
                        len(action_hit_results)
                        == int(root_window.property("compactActionCount") or 0)
                        and len(action_hit_results) >= 3
                        and all(action_hit_results.values())
                    ),
                    "diagnosticPlatform": str(app.platformName()).casefold(),
                    "boxWorldPresentation": world_presentation,
                    "boxWorldPresentationPassed": box_world_presentation_passed,
                    "syntheticProactiveBubble": proactive_bubble,
                    "syntheticProactiveBubbleVisible": (
                        synthetic_proactive_bubble_visible
                    ),
                    "dragProbe": drag_probe,
                    "nativeSystemMovePathPresent": bool(
                        drag_probe["nativeSystemMovePathPresent"]
                    ),
                    "nativeSystemMoveRuntimeVerified": False,
                    "dragFallbackVerified": drag_fallback_verified,
                    "compactStartup": compact_startup,
                    "compactStartupPassed": bool(compact_startup["passed"]),
                    "focusTimerAnimation": focus_timer_animation,
                    "focusTimerAnimationPassed": bool(
                        focus_timer_animation.get("passed", False)
                    ),
                    "qtCacheRouting": qt_cache_routing,
                    "model": backend.modelStatus,
                    **timezone_result,
                }
                result["passed"] = bool(
                    result["qmlLoaded"]
                    and result["qmlWarningsPassed"]
                    and result["identityPassed"]
                    and not result["hasBackslash"]
                    and result["desktopPetLoaded"]
                    and result["independentPetWindowLoaded"]
                    and result["petWindowAcceptsInput"]
                    and result["radialActionsAcceptInput"]
                    and result["diagnosticPlatform"] == "offscreen"
                    and result["boxWorldPresentationPassed"]
                    and result["syntheticProactiveBubbleVisible"]
                    and result["nativeSystemMovePathPresent"]
                    and not result["nativeSystemMoveRuntimeVerified"]
                    and result["dragFallbackVerified"]
                    and result["compactStartupPassed"]
                    and result["focusTimerAnimationPassed"]
                    and bool(dict(result["qtCacheRouting"]).get("passed", False))
                    and result["timezoneDataPassed"]
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
                app.exit(0 if result["passed"] else 1)

            focus_chain_done = {"value": False}
            focus_started_motion = {"ticks": 0}
            focus_paused_motion = {"ticks": 0, "scale": 1.0}
            focus_resumed_motion = {"ticks": 0}

            def fail_focus_probe(stage: str, exc: Exception | str) -> None:
                if focus_chain_done["value"]:
                    return
                focus_chain_done["value"] = True
                focus_timer_animation.update(
                    {
                        "failedStage": str(stage),
                        "error": (
                            str(exc)
                            if isinstance(exc, str)
                            else f"{type(exc).__name__}: {exc}"
                        ),
                        "passed": False,
                    }
                )
                try:
                    if bool(dict(backend.focusStatus).get("active", False)):
                        backend.focusCancel()
                except (RuntimeError, TypeError, ValueError) as cleanup_error:
                    focus_timer_animation["cleanupError"] = (
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                QTimer.singleShot(120, write_result)

            def focus_stage_snapshot() -> tuple[dict[str, object], dict[str, object]]:
                return dict(backend.focusStatus), dict(backend.focusTransition)

            def finish_focus_probe() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None or focus_aura_surface is None:
                        raise RuntimeError("focus timer aura or surface is missing")
                    status, transition = focus_stage_snapshot()
                    finished_stage = {
                        "backendState": str(status.get("state", "")),
                        "backendActive": bool(status.get("active", False)),
                        "transitionKind": str(transition.get("kind", "")),
                        "sequence": int(transition.get("sequence", 0) or 0),
                        "visualState": str(
                            focus_timer_aura.property("visualState") or ""
                        ),
                        "label": str(
                            focus_timer_aura.property("stateLabel") or ""
                        ),
                        "completionVisible": bool(
                            focus_timer_aura.property("completionVisible")
                        ),
                        "windowVisible": focus_timer_aura.isVisible(),
                        "windowExposed": focus_timer_aura.isExposed(),
                        "startPulse": float(
                            focus_timer_aura.property("startPulse") or 0.0
                        ),
                    }
                    finished_stage["passed"] = bool(
                        not finished_stage["backendActive"]
                        and finished_stage["transitionKind"] == "finished"
                        and finished_stage["visualState"] == "finished"
                        and finished_stage["label"] == "已结束"
                        and finished_stage["completionVisible"]
                        and finished_stage["windowVisible"]
                        and finished_stage["windowExposed"]
                        and abs(float(finished_stage["startPulse"])) < 0.001
                    )
                    focus_timer_animation["finished"] = finished_stage
                    sequences = [
                        int(dict(focus_timer_animation[name])["sequence"])
                        for name in ("started", "paused", "resumed", "finished")
                    ]
                    sequence_ordered = all(
                        later > earlier
                        for earlier, later in zip(sequences, sequences[1:])
                    )
                    focus_timer_animation["transitionSequences"] = sequences
                    focus_timer_animation["sequencesStrictlyIncreasing"] = (
                        sequence_ordered
                    )
                    focus_timer_animation["passed"] = bool(
                        focus_timer_animation["windowFound"]
                        and focus_timer_animation["surfaceFound"]
                        and all(
                            bool(dict(focus_timer_animation[name])["passed"])
                            for name in ("started", "paused", "resumed", "finished")
                        )
                        and sequence_ordered
                    )
                    focus_chain_done["value"] = True
                    QTimer.singleShot(120, write_result)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("finished", exc)

            def begin_focus_finish() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    backend.focusFinish()
                    QTimer.singleShot(120, finish_focus_probe)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("finish-call", exc)

            def capture_focus_resumed() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None:
                        raise RuntimeError("focus timer aura is missing")
                    status, transition = focus_stage_snapshot()
                    resumed_stage = {
                        "backendState": str(status.get("state", "")),
                        "transitionKind": str(transition.get("kind", "")),
                        "sequence": int(transition.get("sequence", 0) or 0),
                        "visualState": str(
                            focus_timer_aura.property("visualState") or ""
                        ),
                        "label": str(
                            focus_timer_aura.property("stateLabel") or ""
                        ),
                        "breathing": bool(
                            focus_timer_aura.property("breathing")
                        ),
                        "startAcknowledgementActive": bool(
                            focus_timer_aura.property("startAcknowledgementActive")
                        ),
                        "startPulse": float(
                            focus_timer_aura.property("startPulse") or 0.0
                        ),
                        "targetFps": int(
                            focus_timer_aura.property("targetFps") or 0
                        ),
                        "motionTickBefore": int(focus_resumed_motion["ticks"]),
                        "motionTickAfter": int(
                            focus_timer_aura.property("motionTickCount") or 0
                        ),
                    }
                    resumed_stage["passed"] = bool(
                        resumed_stage["backendState"] == "running"
                        and resumed_stage["transitionKind"] == "resumed"
                        and resumed_stage["visualState"] == "running"
                        and resumed_stage["label"] == "专注中"
                        and resumed_stage["breathing"]
                        and not resumed_stage["startAcknowledgementActive"]
                        and abs(float(resumed_stage["startPulse"])) < 0.001
                        and int(resumed_stage["targetFps"]) in {15, 60}
                        and int(resumed_stage["motionTickAfter"])
                        > int(resumed_stage["motionTickBefore"])
                    )
                    focus_timer_animation["resumed"] = resumed_stage
                    begin_focus_finish()
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("resumed", exc)

            def begin_focus_resume() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None:
                        raise RuntimeError("focus timer aura is missing")
                    backend.focusResume()
                    focus_resumed_motion["ticks"] = int(
                        focus_timer_aura.property("motionTickCount") or 0
                    )
                    QTimer.singleShot(180, capture_focus_resumed)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("resume-call", exc)

            def capture_focus_paused() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None or focus_aura_surface is None:
                        raise RuntimeError("focus timer aura or surface is missing")
                    status, transition = focus_stage_snapshot()
                    paused_stage = {
                        "backendState": str(status.get("state", "")),
                        "transitionKind": str(transition.get("kind", "")),
                        "sequence": int(transition.get("sequence", 0) or 0),
                        "visualState": str(
                            focus_timer_aura.property("visualState") or ""
                        ),
                        "label": str(
                            focus_timer_aura.property("stateLabel") or ""
                        ),
                        "breathing": bool(
                            focus_timer_aura.property("breathing")
                        ),
                        "startAcknowledgementActive": bool(
                            focus_timer_aura.property("startAcknowledgementActive")
                        ),
                        "startPulse": float(
                            focus_timer_aura.property("startPulse") or 0.0
                        ),
                        "targetFps": int(
                            focus_timer_aura.property("targetFps") or 0
                        ),
                        "motionTickBefore": int(focus_paused_motion["ticks"]),
                        "motionTickAfter": int(
                            focus_timer_aura.property("motionTickCount") or 0
                        ),
                        "surfaceScaleBefore": float(focus_paused_motion["scale"]),
                        "surfaceScaleAfter": float(
                            focus_aura_surface.property("scale") or 0.0
                        ),
                    }
                    paused_stage["passed"] = bool(
                        paused_stage["backendState"] == "paused"
                        and paused_stage["transitionKind"] == "paused"
                        and paused_stage["visualState"] == "paused"
                        and paused_stage["label"] == "已暂停"
                        and not paused_stage["breathing"]
                        and not paused_stage["startAcknowledgementActive"]
                        and abs(float(paused_stage["startPulse"])) < 0.001
                        and int(paused_stage["targetFps"]) == 0
                        and int(paused_stage["motionTickAfter"])
                        == int(paused_stage["motionTickBefore"])
                        and abs(
                            float(paused_stage["surfaceScaleAfter"])
                            - float(paused_stage["surfaceScaleBefore"])
                        )
                        < 0.0005
                    )
                    focus_timer_animation["paused"] = paused_stage
                    begin_focus_resume()
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("paused", exc)

            def capture_focus_pause_baseline() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None or focus_aura_surface is None:
                        raise RuntimeError("focus timer aura or surface is missing")
                    focus_paused_motion["ticks"] = int(
                        focus_timer_aura.property("motionTickCount") or 0
                    )
                    focus_paused_motion["scale"] = float(
                        focus_aura_surface.property("scale") or 0.0
                    )
                    QTimer.singleShot(160, capture_focus_paused)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("pause-baseline", exc)

            def begin_focus_pause() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    backend.focusPause()
                    QTimer.singleShot(80, capture_focus_pause_baseline)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("pause-call", exc)

            def capture_focus_started() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None:
                        raise RuntimeError("focus timer aura is missing")
                    status, transition = focus_stage_snapshot()
                    started_stage = {
                        "backendState": str(status.get("state", "")),
                        "transitionKind": str(transition.get("kind", "")),
                        "sequence": int(transition.get("sequence", 0) or 0),
                        "visualState": str(
                            focus_timer_aura.property("visualState") or ""
                        ),
                        "label": str(
                            focus_timer_aura.property("stateLabel") or ""
                        ),
                        "windowVisible": focus_timer_aura.isVisible(),
                        "windowExposed": focus_timer_aura.isExposed(),
                        "startAcknowledgementActive": bool(
                            focus_timer_aura.property("startAcknowledgementActive")
                        ),
                        "startPulse": float(
                            focus_timer_aura.property("startPulse") or 0.0
                        ),
                        "targetFps": int(
                            focus_timer_aura.property("targetFps") or 0
                        ),
                        "motionTickBefore": int(focus_started_motion["ticks"]),
                        "motionTickAfter": int(
                            focus_timer_aura.property("motionTickCount") or 0
                        ),
                    }
                    started_stage["passed"] = bool(
                        started_stage["backendState"] == "running"
                        and started_stage["transitionKind"] == "started"
                        and started_stage["visualState"] == "running"
                        and started_stage["label"] == "专注中"
                        and started_stage["windowVisible"]
                        and started_stage["windowExposed"]
                        and started_stage["startAcknowledgementActive"]
                        and float(started_stage["startPulse"]) > 0.01
                        and int(started_stage["targetFps"]) == 60
                        and int(started_stage["motionTickAfter"])
                        > int(started_stage["motionTickBefore"])
                    )
                    focus_timer_animation["started"] = started_stage
                    begin_focus_pause()
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("started", exc)

            def begin_focus_probe() -> None:
                if focus_chain_done["value"]:
                    return
                try:
                    if focus_timer_aura is None or focus_aura_surface is None:
                        raise RuntimeError("focus timer aura or surface is missing")
                    focus_started_motion["ticks"] = int(
                        focus_timer_aura.property("motionTickCount") or 0
                    )
                    backend.focusStart(5)
                    QTimer.singleShot(180, capture_focus_started)
                except (RuntimeError, TypeError, ValueError) as exc:
                    fail_focus_probe("start-call", exc)

            QTimer.singleShot(80, begin_focus_probe)

        def fail_self_test() -> None:
            if finished["value"]:
                return
            finished["value"] = True
            result = {
                **self_test_identity(),
                "qmlLoaded": True,
                "qmlWarningCount": qml_warning_count,
                "qmlWarnings": list(qml_warning_messages),
                "qmlWarningsPassed": qml_warning_count == 0,
                "identityPassed": False,
                "passed": False,
                "error": "timeout",
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
            )
            app.exit(1)

        backend.chat.responseFinished.connect(finish_self_test)
        backend.newConversation()
        QTimer.singleShot(100, lambda: backend.sendMessage("你好"))
        QTimer.singleShot(8000, fail_self_test)
    if args.benchmark:
        backend.setShellMode("visual")
        if args.benchmark_renderer:
            backend.setRenderer(args.benchmark_renderer)
        samples: list[float] = []
        backend.frameRateChanged.connect(lambda: samples.append(float(backend.frameRate)))

        def finish_benchmark() -> None:
            stable = [value for value in samples[2:] if value > 0]
            snapshot = dict(backend.performanceSnapshot())
            snapshot.update(
                {
                    "averageFps": round(sum(stable) / len(stable), 2) if stable else 0.0,
                    "minimumFps": round(min(stable), 2) if stable else 0.0,
                    "samples": samples,
                    "durationSeconds": max(3.0, float(args.benchmark_seconds)),
                    "capturedAt": datetime.now(UTC).isoformat(),
                }
            )
            output = Path(args.benchmark).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), "utf-8")
            app.quit()

        QTimer.singleShot(int(max(3.0, float(args.benchmark_seconds)) * 1000), finish_benchmark)
    if args.smoke and os.environ.get("LILIES_SMOKE_HOLD") != "1":
        QTimer.singleShot(2200, app.quit)
    exit_code = app.exec()
    if diagnostic_data is not None:
        diagnostic_data.cleanup()
    return exit_code
