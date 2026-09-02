from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

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


class _SizedItem:
    def __init__(self, width: float = 256, height: float = 242) -> None:
        self.width_value = width
        self.height_value = height

    def width(self) -> float:
        return self.width_value

    def height(self) -> float:
        return self.height_value


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


def test_stale_visual_reuses_any_revision_with_same_source_size_and_dpr() -> None:
    parent = QObject()
    item = _SizedItem()
    cache = DragProxySnapshotCache(_Root(), item, parent)
    proxy = _Proxy()
    cache._proxy = proxy
    cache._metadata = DragProxySnapshotMetadata(
        key="current",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.5,
        crop_origin=QPoint(),
        pixel_size=QSize(40, 50),
        geometry_key="body-256x242",
        source_pixel_size=QSize(384, 363),
    )

    assert (
        cache.prepare(
            "new-pose-revision",
            WindowRect(0, 0, 100, 100),
            "body-256x242",
        )
        == proxy.handle
    )
    assert cache.last_prepare_used_stale_visual is True
    cache.complete()

    assert (
        cache.prepare(
            "another-revision",
            WindowRect(0, 0, 100, 100),
            "different-geometry",
        )
        == proxy.handle
    )
    assert cache.last_prepare_used_stale_visual is True
    cache.complete()

    item.width_value = 257
    assert (
        cache.prepare(
            "another-revision",
            WindowRect(0, 0, 100, 100),
            "body-256x242",
        )
        == 0
    )
    assert cache.last_failure == "source-size-changed"

    item.width_value = 256
    cache._uniform_screen_dpr = lambda: False
    assert (
        cache.prepare(
            "new-pose-revision",
            WindowRect(0, 0, 100, 100),
            "body-256x242",
        )
        == 0
    )
    assert cache.last_failure == "mixed-dpr"


def test_stale_visual_requires_recorded_source_size() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), _SizedItem(), parent)
    cache._proxy = _Proxy()
    cache._metadata = DragProxySnapshotMetadata(
        key="old",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.5,
        crop_origin=QPoint(),
        pixel_size=QSize(40, 50),
        geometry_key="old-geometry",
    )

    assert (
        cache.prepare("new", WindowRect(0, 0, 100, 100), "new-geometry") == 0
    )
    assert cache.last_failure == "stale-key"


def test_zero_sized_first_frame_is_not_cached_as_one_pixel_snapshot() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), _SizedItem(0, 0), parent)
    cache._pending_key = "first-frame"

    cache._begin_grab()

    assert cache.metadata is None
    assert cache._grab_result is None
    assert cache.last_failure == "source-not-ready"


def test_exact_key_request_refreshes_after_dpr_or_source_size_change() -> None:
    parent = QObject()
    item = _SizedItem()
    cache = DragProxySnapshotCache(_Root(), item, parent)
    cache._metadata = DragProxySnapshotMetadata(
        key="same-key",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.25,
        crop_origin=QPoint(),
        pixel_size=QSize(40, 50),
        geometry_key="same-geometry",
        source_pixel_size=QSize(320, 303),
    )

    assert cache.request("same-key", "same-geometry") is True
    assert cache.last_failure == "dpr-changed"

    cache._metadata = DragProxySnapshotMetadata(
        key="same-key",
        captured_at=cache._monotonic(),
        device_pixel_ratio=1.5,
        crop_origin=QPoint(),
        pixel_size=QSize(40, 50),
        geometry_key="same-geometry",
        source_pixel_size=QSize(383, 363),
    )
    assert cache.request("same-key", "same-geometry") is True
    assert cache.last_failure == "source-size-changed"
    cache._proxy = _Proxy()
    cache._uniform_screen_dpr = lambda: True
    assert (
        cache.prepare(
            "same-key",
            WindowRect(0, 0, 100, 100),
            "same-geometry",
        )
        == 0
    )
    assert cache.last_failure == "source-size-changed"
    cache.close()


class _GrabResult:
    def __init__(self) -> None:
        self.image_calls = 0

    def image(self):
        self.image_calls += 1
        raise AssertionError("image() must stay behind the gesture fence")


def test_source_size_check_mirrors_logical_then_dpr_qt_rounding() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(
        _Root(),
        _SizedItem(width=385.5, height=363.5),
        parent,
    )

    assert cache._current_source_pixel_size() == QSize(579, 546)


def test_close_invalidates_queued_grab_and_refuses_reopen() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), _SizedItem(), parent)
    cache._pending_key = "queued"
    generation = cache._grab_generation

    cache.close()
    cache._begin_grab()

    assert cache._grab_generation == generation + 1
    assert cache.request("after-close", "same-geometry") is False
    assert cache.metadata is None


def test_grab_ready_during_gesture_never_reads_or_uploads_image() -> None:
    parent = QObject()
    cache = DragProxySnapshotCache(_Root(), _SizedItem(), parent)
    proxy = _Proxy()
    result = _GrabResult()
    cache._proxy = proxy
    cache._grab_generation = 7
    cache._grab_key = "pose-revision"
    cache._grab_geometry_key = "body-256x242"
    cache._grab_pointer = object()
    cache._grab_result = result

    cache.begin_gesture()
    cache._finish_grab(7, 1.5)

    assert result.image_calls == 0
    assert proxy.bitmap is None
    assert cache.metadata is None
    assert cache.last_failure == "grab-deferred-for-gesture"
    assert cache._refresh_after_gesture is True
    assert cache._grab_result is None


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


def test_offscreen_grab_target_is_logical_and_qt_applies_dpr_once() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from PySide6.QtQuick import QQuickItem, QQuickWindow
        from PySide6.QtTest import QTest

        from lilies.drag_proxy_snapshot import DragProxySnapshotCache


        class Proxy:
            handle = 101

            def __init__(self):
                self.bitmap = None

            def upload_bitmap(self, bitmap):
                self.bitmap = bitmap

            def hide(self):
                return True

            def destroy(self):
                return True


        app = QGuiApplication([])
        engine = QQmlEngine()
        component = QQmlComponent(engine)
        component.setData(
            b'import QtQuick; Rectangle { width: 385; height: 363; color: "white" }',
            QUrl(),
        )
        item = component.create()
        assert isinstance(item, QQuickItem), component.errorString()
        window = QQuickWindow()
        window.setWidth(385)
        window.setHeight(363)
        item.setParentItem(window.contentItem())
        proxy = Proxy()
        cache = DragProxySnapshotCache(
            window,
            item,
            window,
            proxy_factory=lambda: proxy,
        )
        window.show()
        try:
            assert cache.request("dpr-once", "385x363")
            for _ in range(200):
                if cache.metadata is not None:
                    break
                QTest.qWait(10)
            metadata = cache.metadata
            assert metadata is not None, cache.last_failure
            print(json.dumps({
                "dpr": metadata.device_pixel_ratio,
                "source": [
                    metadata.source_pixel_size.width(),
                    metadata.source_pixel_size.height(),
                ],
                "bitmap": [proxy.bitmap.width, proxy.bitmap.height],
            }))
        finally:
            cache.close()
            window.hide()
        """
    )
    environment = dict(os.environ)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "QSG_RHI_BACKEND": "software",
            "QT_QUICK_BACKEND": "software",
            "QT_SCALE_FACTOR": "1.5",
            "QT_SCALE_FACTOR_ROUNDING_POLICY": "PassThrough",
            "PYTHONPATH": str(project_root / "src"),
            "PYTHONUTF8": "1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])

    assert outcome["dpr"] == 1.5
    assert outcome["source"] == [578, 545]
    assert outcome["bitmap"] == [578, 545]
