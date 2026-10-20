"""Owned offscreen surfaces only; no native input, model or private data."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QSignalSpy, QTest
from shiboken6 import isValid

from lilies.app import configure_quick_window_resource_lifecycle
from lilies.backend import Backend


@pytest.fixture
def surface(tmp_path, monkeypatch, request):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    root = QQuickWindow()
    pet = QQuickWindow(root)
    pet.setObjectName("petWindow")
    pet.setProperty("manualDragActive", False)
    pet.setProperty("resizeDragActive", False)
    panel = QQuickWindow(root)
    if getattr(request, "param", False):
        backend.setPetInteractionLock("accessory-drag", True)
    root.show()
    pet.show()
    panel.show()
    QCoreApplication.processEvents()
    lifecycle = configure_quick_window_resource_lifecycle(root, backend)
    lifecycle._RELEASE_QUIET_MS = 30
    releases = []
    monkeypatch.setattr(panel, "releaseResources", lambda: releases.append("panel"))
    try:
        yield backend, lifecycle, panel, releases
    finally:
        backend.clearPetInteractionLocks()
        backend.shutdown()
        for window in (panel, pet, root):
            if isValid(window):
                window.close()
                window.deleteLater()
        backend.deleteLater()
        QCoreApplication.processEvents()


@pytest.mark.parametrize("reason", [
    "accessory-tap", "accessory-drag", "component-chat", "component-settings",
    "character", "resize", "legacy",
])
def test_component_pointer_ownership_defers_hidden_resource_release(surface, reason):
    backend, lifecycle, panel, releases = surface
    backend.setPetInteractionLock(reason, True)
    panel.hide()
    QTest.qWait(90)
    assert releases == [], "hidden graphics cleanup ran during a held component gesture"
    assert all(not timer.isActive() for timer in lifecycle._release_timers.values())
    backend.setPetInteractionLock(reason, False)
    wait_for_release(releases)


def wait_for_release(releases):
    for _ in range(100):
        if releases:
            break
        QTest.qWait(10)
    assert releases == ["panel"]


@pytest.mark.parametrize("reason", ["menu", "companion-unread", "desktop-mode-tab"])
def test_menu_only_owners_do_not_hold_graphics_forever(surface, reason):
    backend, lifecycle, panel, releases = surface
    backend.setPetInteractionLock(reason, True)
    panel.hide()
    wait_for_release(releases)
    assert not backend.pet_pointer_interaction_active
    assert panel not in lifecycle._pending_releases


def test_nested_owners_stop_old_timer_and_release_starts_full_quiet_interval(surface):
    backend, lifecycle, panel, releases = surface
    panel.hide()
    old_timer = lifecycle._release_timers[panel]
    assert old_timer.isActive()
    backend.setPetInteractionLock("accessory-tap", True)
    backend.setPetInteractionLock("accessory-drag", True)
    assert not old_timer.isActive()
    backend.setPetInteractionLock("accessory-tap", False)
    QTest.qWait(80)
    assert releases == [] and not old_timer.isActive()
    lifecycle._RELEASE_QUIET_MS = 1000
    backend.setPetInteractionLock("accessory-drag", False)
    assert old_timer.isActive() and old_timer.interval() == 1000
    # Deliver a previously posted timeout now; a fresh quiet interval must win.
    lifecycle._release_after_quiet_period(panel)
    assert releases == [] and old_timer.isActive()
    lifecycle._release_deadlines[panel] = 0
    lifecycle._release_after_quiet_period(panel)
    assert releases == ["panel"] and not old_timer.isActive()


def test_regrab_fences_timeout_and_no_repeat_release_until_hidden_again(surface):
    backend, lifecycle, panel, releases = surface
    panel.hide()
    backend.setPetInteractionLock("accessory-drag", True)
    backend.setPetInteractionLock("accessory-drag", False)
    backend.setPetInteractionLock("component-chat", True)
    lifecycle._release_after_quiet_period(panel)
    assert releases == []
    assert not lifecycle._release_timers[panel].isActive()
    backend.clearPetInteractionLocks()
    wait_for_release(releases)
    backend.setPetInteractionLock("accessory-drag", True)
    backend.setPetInteractionLock("accessory-drag", False)
    lifecycle._release_after_quiet_period(panel)
    QTest.qWait(70)
    assert releases == ["panel"]
    assert panel not in lifecycle._pending_releases
    panel.show()
    panel.hide()
    releases.clear()
    wait_for_release(releases)


def test_show_while_held_cancels_obligation_and_old_timeout(surface):
    backend, lifecycle, panel, releases = surface
    panel.hide()
    backend.setPetInteractionLock("accessory-drag", True)
    panel.show()
    backend.clearPetInteractionLocks()
    lifecycle._release_after_quiet_period(panel)
    QTest.qWait(70)
    assert releases == []
    assert panel not in lifecycle._pending_releases
    assert not lifecycle._release_timers[panel].isActive()


@pytest.mark.parametrize("surface", [True], indirect=True)
def test_already_held_backend_is_honored_at_lifecycle_construction(surface):
    backend, lifecycle, panel, releases = surface
    assert lifecycle._pointer_interaction_active
    panel.hide()
    assert panel in lifecycle._pending_releases
    QTest.qWait(70)
    assert releases == []
    backend.clearPetInteractionLocks()
    wait_for_release(releases)


def test_destroyed_hidden_surface_is_dropped_on_last_release(surface):
    from shiboken6 import delete

    backend, lifecycle, panel, releases = surface
    backend.setPetInteractionLock("accessory-drag", True)
    panel.hide()
    delete(panel)
    backend.clearPetInteractionLocks()
    QTest.qWait(70)
    assert releases == []
    assert panel not in lifecycle._pending_releases


def test_backend_emits_only_aggregate_pointer_edges_not_samples_or_menu_changes(surface):
    backend, lifecycle, panel, releases = surface
    edges = QSignalSpy(backend.petPointerInteractionChanged)
    backend.setPetInteractionLock("menu", True)
    for _ in range(500):
        backend.setPetInteractionLock("accessory-tap", True)
    backend.setPetInteractionLock("accessory-drag", True)
    backend.setPetInteractionLock("accessory-tap", False)
    backend.setPetInteractionLock("unknown-absent-owner", False)
    assert edges.count() == 1 and edges.at(0) == [True]
    backend.setPetInteractionLock("accessory-drag", False)
    assert edges.count() == 2 and edges.at(1) == [False]
    assert backend._pet_interaction_locked  # menu remains open
    backend.clearPetInteractionLocks()
    assert edges.count() == 2


def test_production_startup_passes_real_backend_to_resource_lifecycle():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src/lilies/app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    startup = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "_run_application")
    calls = [node for node in ast.walk(startup) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "configure_quick_window_resource_lifecycle"]
    assert len(calls) == 1
    assert [arg.id for arg in calls[0].args] == ["root_window", "backend"]
