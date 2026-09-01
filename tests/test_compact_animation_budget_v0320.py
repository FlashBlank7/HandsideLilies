from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hidden_desktop_breath_does_not_follow_compact_pet() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")

    assert "running: desktop.visible && backend.sceneActive" in source
    assert "running: petWindow.visible || (desktop.visible && backend.sceneActive)" not in source


def test_compact_pet_uses_idle_and_interaction_animation_cadences() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")

    assert "readonly property bool highMotion: expanded" in source
    assert "|| compactLilith.characterHovered" in source
    assert "|| backend.boxWorldSceneOpen" in source
    assert "|| !compactWindow.highMotion" in source
    assert "readonly property real quietBreath:" in source


def test_compact_accessory_breath_is_driven_by_the_low_power_pet_clock() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")
    compact_section = source.split("id: compactWindow", 1)[1]

    assert "compactWindow.quietBreath" in compact_section
    assert "desktop.sceneBreath" not in compact_section


def test_chat_restore_clears_the_native_minimized_state_before_presenting() -> None:
    source = (ROOT / "qml" / "Main.qml").read_text("utf-8")

    assert "chatWindow.visibility = Window.Windowed" in source
    assert "(attempt === 4 || attempt === 9)" in source
