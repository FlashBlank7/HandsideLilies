from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject

from lilies.app import CompactPointerEventFilter
from lilies.windows_drag_proxy import WindowRect


ROOT = Path(__file__).resolve().parents[1]


class _MoveRoot(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.system_move_starts = 0

    def property(self, key: str):
        if key == "compactDragSnapshotKey":
            return "pose|size|box"
        if key == "compactDragGeometryKey":
            return "proxy-geometry-v2|42000|0|0|1000"
        return super().property(key)

    def startSystemMove(self) -> bool:
        self.system_move_starts += 1
        return True

    def devicePixelRatio(self) -> float:
        return 1.5

    def width(self) -> int:
        return 420

    def height(self) -> int:
        return 396

    def screen(self):
        return None

    def winId(self) -> int:
        return 4321


class _UnavailableCache:
    last_failure = "stale-key"

    def __init__(self) -> None:
        self.prepare_calls: list[tuple[str, WindowRect, str]] = []
        self.gesture_started = False

    def begin_gesture(self) -> None:
        self.gesture_started = True

    def end_gesture(self) -> None:
        self.gesture_started = False

    def prepare(
        self,
        key: str,
        root_rect: WindowRect,
        geometry_key: str,
    ) -> int:
        self.prepare_calls.append((key, root_rect, geometry_key))
        return 0


def test_character_press_hot_path_never_captures_a_new_proxy_frame() -> None:
    qml_source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    prepare_start = qml_source.index("function prepareCharacterGestureAtGlobal(")
    starter_start = qml_source.index("function startPreparedCharacterGesture(")
    native_start = qml_source.index("function tryNativeSystemMove(")
    prepare_handler = qml_source[prepare_start:starter_start]
    starter_handler = qml_source[starter_start:native_start]
    press_start = qml_source.index("onCharacterPressStarted:")
    press_end = qml_source.index("onCharacterPointerMoved:", press_start)
    press_handler = qml_source[press_start:press_end]
    native_end = qml_source.index("function finishCharacterGesture(", native_start)
    native_handler = qml_source[native_start:native_end]

    assert "petWindow.prepareCharacterGestureAtGlobal(" in press_handler
    assert "petWindow.startPreparedCharacterGesture(serial)" in press_handler
    assert "dragLatchedSnapshotKey = compactDragSnapshotKey" in prepare_handler
    assert "dragLatchedGeometryKey = compactDragGeometryKey" in prepare_handler
    assert "tryNativeSystemMove(" in starter_handler
    assert "grabToImage" not in press_handler
    assert "grabToImage" not in prepare_handler
    assert "grabToImage" not in starter_handler
    assert "requestDragProxySnapshot" not in press_handler
    assert "requestDragProxySnapshot" not in prepare_handler
    assert "requestDragProxySnapshot" not in starter_handler
    assert "grabToImage" not in native_handler
    assert "requestDragProxySnapshot" not in native_handler

    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    bridge_start = app_source.index("def tryStartSystemMove(")
    bridge_end = app_source.index("def moveWindowForDrag(", bridge_start)
    bridge = app_source[bridge_start:bridge_end]
    assert "grabToImage" not in bridge
    assert "requestDragProxySnapshot" not in bridge


def test_proxy_snapshot_is_precached_only_from_the_idle_debounce_path() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    key_start = source.index("readonly property string compactDragSnapshotKey:")
    key_end = source.index("property int consumedPointerEventSerial", key_start)
    key = source[key_start:key_end]
    timer_start = source.index("id: dragProxySnapshotDebounce")
    timer_end = source.index("id: dragPresentationSettleTimer", timer_start)
    timer = source[timer_start:timer_end]

    assert '"proxy-v3"' in key
    assert "compactDragGeometryKey" in key
    geometry_start = source.index(
        "readonly property string compactDragGeometryKey:"
    )
    geometry = source[geometry_start:key_start]
    assert '"proxy-geometry-v2"' in geometry
    assert "compactWindow.boxSize" in geometry
    assert "compactWindow.accessoryDx" in geometry
    assert "compactWindow.accessoryDy" in geometry
    assert "compactWindow.accessoryScale" in geometry
    assert 'String(desktop.petPresenceState || "desktop")' in geometry
    for declared_property in (
        "habitatLayoutActive",
        "habitatProfile",
        "habitatStrategy",
        "habitatPoseVariant",
        "habitatMirror",
        "habitatCharacterScale",
        "habitatAnchorNormX",
        "habitatAnchorNormY",
        "habitatContactX",
        "habitatContactY",
        "habitatMotionStyle",
        "habitatMotionPeriod",
        "habitatPeekFraction",
    ):
        assert f"compactLilith.{declared_property}" in geometry
    # All habitat values come from V03PetBody's declared, normalized public
    # properties.  A missing raw mapping member therefore cannot stringify as
    # `undefined` and accidentally churn or alias the cache key.
    assert "backend.habitatState" not in geometry
    assert "habitatLayout." not in geometry

    assert "interval: 180" in timer
    assert "petWindow.manualDragActive" in timer
    assert "petWindow.resizeDragActive" in timer
    assert "petWindow.localGestureDepth > 0" in timer
    assert "compactWindow.expanded" in timer
    assert "requestDragProxySnapshot(" in timer
    assert "petWindow.compactDragSnapshotKey" in timer
    assert "petWindow.compactDragGeometryKey" in timer
    assert "onCompactDragSnapshotKeyChanged: scheduleDragProxySnapshot()" in source
    assert "Component.onCompleted: Qt.callLater(scheduleDragProxySnapshot)" in source

    snapshot_source = (
        ROOT / "src" / "lilies" / "drag_proxy_snapshot.py"
    ).read_text("utf-8")
    request_start = snapshot_source.index(
        'def request(self, key: str, geometry_key: str = "")'
    )
    request_end = snapshot_source.index("def _finish_grab(", request_start)
    request_pipeline = snapshot_source[request_start:request_end]
    assert "QTimer.singleShot(0, self._begin_grab)" in request_pipeline
    assert "self.item.grabToImage(target)" in request_pipeline


def test_system_move_attempts_cached_proxy_before_the_real_window() -> None:
    root = _MoveRoot()
    event_filter = CompactPointerEventFilter(root)
    calls: list[str] = []

    def prepare_proxy(snapshot_key: str, geometry_key: str) -> bool:
        calls.append(f"proxy:{snapshot_key}:{geometry_key}")
        return True

    event_filter._prepare_proxy_system_move = prepare_proxy
    root.startSystemMove = lambda: calls.append("root") or True

    assert event_filter.tryStartSystemMove(4801, "latched-pose", "latched-geometry") is True
    assert calls == ["proxy:latched-pose:latched-geometry"]
    event_filter.acknowledgeSystemMoveFinished(4801)


def test_proxy_cache_failure_explicitly_falls_back_to_system_move(
    monkeypatch,
) -> None:
    root = _MoveRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._native_drag_filter = SimpleNamespace(native_window_id=4321)
    cache = _UnavailableCache()
    event_filter._drag_proxy_cache = cache
    root_rect = WindowRect(100, 200, 520, 596)
    monkeypatch.setattr(event_filter, "_native_window_rect", lambda _hwnd: root_rect)

    assert event_filter.tryStartSystemMove(
        4802,
        "latched-pose",
        "latched-geometry",
    ) is True
    assert cache.gesture_started is True
    assert cache.prepare_calls == [
        ("latched-pose", root_rect, "latched-geometry")
    ]
    assert event_filter._proxy_fallback_reason == "stale-key"
    assert event_filter._diagnostic_proxy_used is False
    assert root.system_move_starts == 1
    event_filter.acknowledgeSystemMoveFinished(4802)


def test_app_bridge_exposes_geometry_and_visual_staleness_contract() -> None:
    app_source = (ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    request_start = app_source.index("def requestDragProxySnapshot(")
    request_end = app_source.index("def _suspend_qt_pointer_filter(", request_start)
    request_bridge = app_source[request_start:request_end]
    assert "geometry_key: str = \"\"" in request_bridge
    assert "cache.request(" in request_bridge
    assert "str(geometry_key or \"\")" in request_bridge

    prepare_start = app_source.index("def _prepare_proxy_system_move(")
    prepare_end = app_source.index("def _commit_proxy_geometry(", prepare_start)
    prepare_bridge = app_source[prepare_start:prepare_end]
    assert "semantic_key: str = \"\"" in prepare_bridge
    assert "geometry_key: str = \"\"" in prepare_bridge
    assert "cache.prepare(semantic_key, root_rect, geometry_key)" in prepare_bridge
    assert (
        "self._proxy_visual_stale = cache.last_prepare_used_stale_visual"
        in prepare_bridge
    )
    assert '"proxyVisualStale": bool(self._proxy_visual_stale)' in app_source
