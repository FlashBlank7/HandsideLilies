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


class _MixedDprCache:
    last_failure = "mixed-dpr"

    def __init__(self) -> None:
        self.prepare_calls: list[tuple[str, WindowRect]] = []

    def prepare(self, key: str, root_rect: WindowRect) -> int:
        self.prepare_calls.append((key, root_rect))
        return 0


def test_character_press_hot_path_never_captures_a_new_proxy_frame() -> None:
    qml_source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    press_start = qml_source.index("onCharacterPressStarted:")
    press_end = qml_source.index("onCharacterPointerMoved:", press_start)
    press_handler = qml_source[press_start:press_end]
    native_start = qml_source.index("function tryNativeSystemMove()")
    native_end = qml_source.index("function finishCharacterGesture(", native_start)
    native_handler = qml_source[native_start:native_end]

    assert "petWindow.tryNativeSystemMove()" in press_handler
    assert "grabToImage" not in press_handler
    assert "requestDragProxySnapshot" not in press_handler
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

    assert '"proxy-v2"' in key
    assert 'String(desktop.petPresenceState || "desktop")' in key
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
        assert f"compactLilith.{declared_property}" in key
    # All habitat values come from V03PetBody's declared, normalized public
    # properties.  A missing raw mapping member therefore cannot stringify as
    # `undefined` and accidentally churn or alias the cache key.
    assert "backend.habitatState" not in key
    assert "habitatLayout." not in key

    assert "interval: 850" in timer
    assert "petWindow.manualDragActive" in timer
    assert "petWindow.resizeDragActive" in timer
    assert "petWindow.localGestureDepth > 0" in timer
    assert "compactWindow.expanded" in timer
    assert "requestDragProxySnapshot(" in timer
    assert "petWindow.compactDragSnapshotKey" in timer
    assert "onCompactDragSnapshotKeyChanged: scheduleDragProxySnapshot()" in source
    assert "Component.onCompleted: Qt.callLater(scheduleDragProxySnapshot)" in source

    snapshot_source = (
        ROOT / "src" / "lilies" / "drag_proxy_snapshot.py"
    ).read_text("utf-8")
    request_start = snapshot_source.index("def request(self, key: str)")
    request_end = snapshot_source.index("def _finish_grab(", request_start)
    request_pipeline = snapshot_source[request_start:request_end]
    assert "QTimer.singleShot(0, self._begin_grab)" in request_pipeline
    assert "self.item.grabToImage(target)" in request_pipeline


def test_system_move_attempts_cached_proxy_before_the_real_window() -> None:
    root = _MoveRoot()
    event_filter = CompactPointerEventFilter(root)
    calls: list[str] = []

    def prepare_proxy() -> bool:
        calls.append("proxy")
        return True

    event_filter._prepare_proxy_system_move = prepare_proxy
    root.startSystemMove = lambda: calls.append("root") or True

    assert event_filter.tryStartSystemMove(4801) is True
    assert calls == ["proxy"]
    event_filter.acknowledgeSystemMoveFinished(4801)


def test_mixed_dpr_proxy_failure_explicitly_falls_back_to_system_move(
    monkeypatch,
) -> None:
    root = _MoveRoot()
    event_filter = CompactPointerEventFilter(root)
    event_filter._native_drag_filter = SimpleNamespace(native_window_id=4321)
    cache = _MixedDprCache()
    event_filter._drag_proxy_cache = cache
    root_rect = WindowRect(100, 200, 520, 596)
    monkeypatch.setattr(event_filter, "_native_window_rect", lambda _hwnd: root_rect)

    assert event_filter.tryStartSystemMove(4802) is True
    assert cache.prepare_calls == [("pose|size|box", root_rect)]
    assert event_filter._proxy_fallback_reason == "mixed-dpr"
    assert event_filter._diagnostic_proxy_used is False
    assert root.system_move_starts == 1
    event_filter.acknowledgeSystemMoveFinished(4802)
