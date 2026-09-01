from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication

from lilies.app import configure_quick_window_resource_lifecycle


def test_all_qml_windows_release_graphics_when_hidden() -> None:
    app = QApplication.instance() or QApplication([])
    root = QQuickWindow()
    child = QQuickWindow(root)
    root.setPersistentGraphics(True)
    root.setPersistentSceneGraph(True)
    child.setPersistentGraphics(True)
    child.setPersistentSceneGraph(True)
    try:
        lifecycle = configure_quick_window_resource_lifecycle(root)

        assert lifecycle.windows == (root, child)
        assert root.isPersistentGraphics() is False
        assert root.isPersistentSceneGraph() is False
        assert child.isPersistentGraphics() is False
        assert child.isPersistentSceneGraph() is False
    finally:
        child.close()
        root.close()
        child.deleteLater()
        root.deleteLater()
        app.processEvents()


def test_application_configures_resource_lifecycle_after_qml_load() -> None:
    source = configure_quick_window_resource_lifecycle.__module__
    assert source == "lilies.app"
    app_source = (
        Path(__file__).resolve().parents[1] / "src" / "lilies" / "app.py"
    ).read_text("utf-8")
    assert "app._lilies_quick_window_lifecycle" in app_source
    assert "configure_quick_window_resource_lifecycle(" in app_source
    assert "root_window" in app_source
    assert "QEvent.Type.Hide" in app_source
    assert "QTimer.singleShot(0" in app_source
    assert "window.releaseResources()" in app_source


def test_hidden_world_and_retired_dock_do_not_keep_animation_clocks_running() -> None:
    qml_root = Path(__file__).resolve().parents[1] / "qml"
    world_source = (qml_root / "V03BoxWorldScene.qml").read_text("utf-8")
    main_source = (qml_root / "Main.qml").read_text("utf-8")

    assert world_source.count("running: root.visible") >= 2
    assert "running: dockWindow.visible" in main_source
