from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QSize, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from lilies.drag_proxy_snapshot import (
    DragProxySnapshotCache,
    DragProxySnapshotMetadata,
    alpha_bounds,
)
from lilies.windows_drag_proxy import DragDelta, DragProxyFinal, WindowRect


class _Root:
    def devicePixelRatio(self) -> float:
        return 1.5


class _Proxy:
    def __init__(self) -> None:
        self.handle = 2**48 + 91
        self.visible = False
        self.rect_value = WindowRect(0, 0, 40, 50)
        self.start_count = 0
        self.hide_count = 0
        self.bitmap = None

    def upload_bitmap(self, bitmap) -> None:
        self.bitmap = bitmap

    def show_at(self, x: int, y: int) -> WindowRect:
        self.visible = True
        self.rect_value = WindowRect(x, y, x + 40, y + 50)
        return self.rect_value

    def start_move(self) -> bool:
        self.start_count += 1
        return True

    def rect(self) -> WindowRect:
        return self.rect_value

    def finalize(self, *, destroy: bool = True) -> DragProxyFinal:
        del destroy
        self.visible = False
        self.hide_count += 1
        return DragProxyFinal(self.rect_value, DragDelta(0, 0))

    def hide(self) -> bool:
        self.visible = False
        self.hide_count += 1
        return True

    def destroy(self) -> bool:
        self.handle = None
        return True


def test_alpha_bounds_is_tight_and_adds_bounded_margin() -> None:
    image = QImage(20, 16, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    for y in range(5, 10):
        for x in range(7, 13):
            image.setPixelColor(x, y, QColor(255, 255, 255, 255))

    tight = alpha_bounds(image)
    padded = alpha_bounds(image, margin=3)
    assert (tight.x(), tight.y(), tight.width(), tight.height()) == (7, 5, 6, 5)
    assert (padded.x(), padded.y(), padded.width(), padded.height()) == (
        4,
        2,
        12,
        11,
    )


def test_alpha_bounds_rejects_an_empty_or_null_frame() -> None:
    empty = QImage(8, 8, QImage.Format.Format_ARGB32_Premultiplied)
    empty.fill(0)

    assert alpha_bounds(empty).isEmpty()
    assert alpha_bounds(QImage()).isEmpty()


def test_exact_cache_key_prepares_tight_proxy_and_reports_physical_delta() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), object(), parent)
    proxy = _Proxy()
    cache._proxy = proxy
    cache._metadata = DragProxySnapshotMetadata(
        key="pose|size|box",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.5,
        crop_origin=QPoint(12, 18),
        pixel_size=QSize(40, 50),
    )
    cache._uniform_screen_dpr = lambda: True

    handle = cache.prepare(
        "pose|size|box",
        WindowRect(-300, 200, 100, 600),
    )

    assert handle == 2**48 + 91
    assert proxy.rect_value == WindowRect(-288, 218, -248, 268)
    assert cache.start_move() is True
    proxy.rect_value = WindowRect(-251, 245, -211, 295)
    final = cache.preview_final()
    assert final is not None
    assert final.delta == (37, 27)

    cache.complete()
    assert cache.active is False
    assert proxy.hide_count == 1


def test_cache_mismatch_and_mixed_dpr_fail_closed() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), object(), parent)
    proxy = _Proxy()
    cache._proxy = proxy
    cache._metadata = DragProxySnapshotMetadata(
        key="current",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.5,
        crop_origin=QPoint(),
        pixel_size=QSize(40, 50),
    )

    assert cache.prepare("stale", WindowRect(0, 0, 100, 100)) == 0
    assert cache.last_failure == "stale-key"
    cache._uniform_screen_dpr = lambda: False
    assert cache.prepare("current", WindowRect(0, 0, 100, 100)) == 0
    assert cache.last_failure == "mixed-dpr"
    assert proxy.visible is False


def test_offscreen_quick_item_is_captured_and_uploaded_before_press() -> None:
    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(
        b'import QtQuick\nRectangle { width: 48; height: 36; '
        b'color: "#ffffff"; Rectangle { x: 6; y: 7; width: 9; height: 11; '
        b'color: "#9f3129" } }',
        QUrl(),
    )
    item = component.create()
    assert isinstance(item, QQuickItem), component.errorString()
    window = QQuickWindow()
    window.setWidth(48)
    window.setHeight(36)
    item.setParentItem(window.contentItem())
    proxy = _Proxy()
    cache = DragProxySnapshotCache(
        window,
        item,
        window,
        proxy_factory=lambda: proxy,
    )
    window.show()
    try:
        assert cache.request("ready-frame") is True
        for _ in range(50):
            if cache.metadata is not None:
                break
            QTest.qWait(10)
        assert cache.metadata is not None, (
            cache.last_failure,
            cache._last_failure_type,
            cache._grab_result,
            cache._pending_key,
        )
        assert cache.metadata.key == "ready-frame"
        assert proxy.bitmap is not None
        assert proxy.bitmap.width > 0 and proxy.bitmap.height > 0
    finally:
        cache.close()
        window.hide()
        item.deleteLater()
        window.deleteLater()
        engine.deleteLater()
