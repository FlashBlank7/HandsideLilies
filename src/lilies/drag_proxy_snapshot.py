from __future__ import annotations

"""Idle snapshot cache for the Windows layered drag proxy.

The GPU-to-CPU grab is deliberately separated from the press path.  A press
can use only an exact, already uploaded semantic key; every miss falls back to
the ordinary Qt/Windows move path.
"""

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem, QQuickWindow

from .windows_drag_proxy import (
    ArgbPremultipliedBitmap,
    DragDelta,
    DragProxyFinal,
    WindowRect,
    WindowsDragProxy,
    WindowsDragProxyError,
)


@dataclass(frozen=True, slots=True)
class DragProxySnapshotMetadata:
    key: str
    captured_at: float
    device_pixel_ratio: float
    crop_origin: QPoint
    pixel_size: QSize


def alpha_bounds(image: QImage, *, margin: int = 0) -> QRect:
    """Return the non-zero-alpha bounds without walking pixels in Python."""

    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        return QRect()
    converted = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    raw = bytes(converted.constBits())
    stride = converted.bytesPerLine()
    width = converted.width()
    height = converted.height()
    alpha_offset = 3 if sys.byteorder == "little" else 0
    left = width
    top = height
    right = -1
    bottom = -1
    for row_index in range(height):
        row_start = row_index * stride + alpha_offset
        alpha = raw[row_start : row_start + width * 4 : 4]
        stripped_left = alpha.lstrip(b"\x00")
        if not stripped_left:
            continue
        first = width - len(stripped_left)
        last_exclusive = len(alpha.rstrip(b"\x00"))
        left = min(left, first)
        right = max(right, last_exclusive - 1)
        top = min(top, row_index)
        bottom = row_index
    if right < left or bottom < top:
        return QRect()
    padding = max(0, int(margin))
    return QRect(
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + 1 + padding) - max(0, left - padding),
        min(height, bottom + 1 + padding) - max(0, top - padding),
    )


class DragProxySnapshotCache(QObject):
    """Own one pre-rendered proxy bitmap and its short visible session."""

    def __init__(
        self,
        root: QQuickWindow,
        item: QQuickItem,
        parent: QObject | None = None,
        *,
        proxy_factory: Callable[[], WindowsDragProxy] = WindowsDragProxy,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent or root)
        self.root = root
        self.item = item
        self._proxy_factory = proxy_factory
        self._monotonic = monotonic
        self._proxy: WindowsDragProxy | None = None
        self._metadata: DragProxySnapshotMetadata | None = None
        self._pending_key = ""
        self._grab_key = ""
        self._grab_pointer: object | None = None
        self._grab_result: QObject | None = None
        self._grab_generation = 0
        self._active = False
        self._prepared_root_rect: WindowRect | None = None
        self._prepared_proxy_rect: WindowRect | None = None
        self._last_failure = "cache-miss"
        self._last_failure_type = ""

    @property
    def proxy_handle(self) -> int:
        proxy = self._proxy
        return int(proxy.handle or 0) if proxy is not None else 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def last_failure(self) -> str:
        return self._last_failure

    @property
    def metadata(self) -> DragProxySnapshotMetadata | None:
        return self._metadata

    @property
    def cache_age_ms(self) -> float:
        metadata = self._metadata
        if metadata is None:
            return 0.0
        return max(0.0, (self._monotonic() - metadata.captured_at) * 1000.0)

    def _uniform_screen_dpr(self) -> bool:
        values = {
            round(float(screen.devicePixelRatio() or 1.0), 4)
            for screen in QGuiApplication.screens()
        }
        return len(values) <= 1

    def request(self, key: str) -> bool:
        normalized = str(key or "").strip()[:512]
        if not normalized or self._active:
            return False
        self._pending_key = normalized
        metadata = self._metadata
        if metadata is not None and metadata.key == normalized:
            return True
        if self._grab_result is None:
            QTimer.singleShot(0, self._begin_grab)
        return True

    def _begin_grab(self) -> None:
        if self._grab_result is not None or self._active:
            return
        key = self._pending_key
        if not key:
            return
        try:
            dpr = max(0.25, float(self.root.devicePixelRatio() or 1.0))
            target = QSize(
                max(1, round(float(self.item.width()) * dpr)),
                max(1, round(float(self.item.height()) * dpr)),
            )
            pointer = self.item.grabToImage(target)
            result = pointer.data()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._last_failure = "grab-rejected"
            return
        if result is None:
            self._last_failure = "grab-rejected"
            return
        self._grab_generation += 1
        generation = self._grab_generation
        self._grab_key = key
        self._grab_pointer = pointer
        self._grab_result = result
        result.ready.connect(
            lambda current=generation, ratio=dpr: self._finish_grab(current, ratio)
        )

    def _finish_grab(self, generation: int, dpr: float) -> None:
        if generation != self._grab_generation or self._grab_result is None:
            return
        result = self._grab_result
        # The QSharedPointer is the lifetime owner of QQuickItemGrabResult.
        # Keep a local reference until image() has copied the completed frame.
        pointer = self._grab_pointer
        captured_key = self._grab_key
        try:
            image = result.image()
            converted = image.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied
            )
            bounds = alpha_bounds(converted, margin=max(1, round(7 * dpr)))
            if bounds.isEmpty():
                self._last_failure = "empty-alpha"
                return
            cropped = converted.copy(bounds)
            bitmap = ArgbPremultipliedBitmap.from_qt_premultiplied(
                cropped.width(),
                cropped.height(),
                cropped.constBits(),
                stride=cropped.bytesPerLine(),
            )
            proxy = self._proxy or self._proxy_factory()
            proxy.upload_bitmap(bitmap)
        except (
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
            WindowsDragProxyError,
        ) as error:
            self._last_failure = "upload-failed"
            self._last_failure_type = type(error).__name__
        else:
            self._proxy = proxy
            self._metadata = DragProxySnapshotMetadata(
                key=captured_key,
                captured_at=self._monotonic(),
                device_pixel_ratio=dpr,
                crop_origin=bounds.topLeft(),
                pixel_size=bounds.size(),
            )
            self._last_failure = ""
            self._last_failure_type = ""
        finally:
            del pointer
            self._grab_result = None
            self._grab_pointer = None
            self._grab_key = ""
        if self._pending_key and self._pending_key != captured_key:
            QTimer.singleShot(0, self._begin_grab)

    def can_prepare(self, key: str) -> bool:
        metadata = self._metadata
        if self._active:
            self._last_failure = "already-active"
            return False
        if self._proxy is None or metadata is None:
            self._last_failure = "cache-miss"
            return False
        if metadata.key != str(key or ""):
            self._last_failure = "stale-key"
            return False
        current_dpr = max(0.25, float(self.root.devicePixelRatio() or 1.0))
        if abs(current_dpr - metadata.device_pixel_ratio) > 0.001:
            self._last_failure = "dpr-changed"
            return False
        if not self._uniform_screen_dpr():
            self._last_failure = "mixed-dpr"
            return False
        return True

    def prepare(self, key: str, root_rect: WindowRect) -> int:
        if not self.can_prepare(key):
            return 0
        proxy = self._proxy
        metadata = self._metadata
        assert proxy is not None and metadata is not None
        x = root_rect.left + metadata.crop_origin.x()
        y = root_rect.top + metadata.crop_origin.y()
        try:
            proxy_rect = proxy.show_at(x, y)
        except WindowsDragProxyError:
            self._last_failure = "show-failed"
            return 0
        self._prepared_root_rect = root_rect
        self._prepared_proxy_rect = proxy_rect
        self._active = True
        self._last_failure = ""
        return int(proxy.handle or 0)

    def start_move(self) -> bool:
        proxy = self._proxy
        if not self._active or proxy is None:
            return False
        try:
            started = bool(proxy.start_move())
        except WindowsDragProxyError:
            started = False
        if not started:
            self.cancel()
            self._last_failure = "move-request-failed"
        return started

    def preview_final(self) -> DragProxyFinal | None:
        proxy = self._proxy
        origin = self._prepared_proxy_rect
        if not self._active or proxy is None or origin is None:
            return None
        current = proxy.rect()
        if current is None:
            self._last_failure = "final-rect-unavailable"
            return None
        return DragProxyFinal(
            current,
            DragDelta(current.left - origin.left, current.top - origin.top),
        )

    def complete(self) -> None:
        proxy = self._proxy
        if proxy is not None and self._active:
            try:
                proxy.finalize(destroy=False)
            except WindowsDragProxyError:
                try:
                    proxy.hide()
                except WindowsDragProxyError:
                    pass
        self._active = False
        self._prepared_root_rect = None
        self._prepared_proxy_rect = None

    def cancel(self) -> None:
        proxy = self._proxy
        if proxy is not None:
            cancel_move = getattr(proxy, "cancel_move", None)
            try:
                if callable(cancel_move):
                    cancel_move()
            except (AttributeError, RuntimeError, WindowsDragProxyError):
                pass
            try:
                proxy.hide()
            except WindowsDragProxyError:
                pass
        self._active = False
        self._prepared_root_rect = None
        self._prepared_proxy_rect = None

    def cancel_native_move(self) -> bool:
        """Request cancellation while leaving final geometry readable."""

        proxy = self._proxy
        if not self._active or proxy is None:
            return False
        cancel_move = getattr(proxy, "cancel_move", None)
        if not callable(cancel_move):
            return False
        try:
            return bool(cancel_move())
        except (AttributeError, RuntimeError, WindowsDragProxyError):
            return False

    def close(self) -> None:
        self.cancel()
        proxy = self._proxy
        self._proxy = None
        self._metadata = None
        if proxy is not None and proxy.handle is not None:
            try:
                proxy.destroy()
            except WindowsDragProxyError:
                pass


__all__ = [
    "DragProxySnapshotCache",
    "DragProxySnapshotMetadata",
    "alpha_bounds",
]
