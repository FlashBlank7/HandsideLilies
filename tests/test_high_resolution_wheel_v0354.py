from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def accumulator():
    engine = QQmlEngine()
    component = QQmlComponent(
        engine, QUrl.fromLocalFile(str(ROOT / "qml" / "WheelStepAccumulator.qml"))
    )
    item = component.create()
    assert item is not None, [error.toString() for error in component.errors()]
    yield item
    item.deleteLater()


def test_eight_small_packets_make_one_notch(accumulator):
    assert [accumulator.consume(15) for _ in range(8)] == [0] * 7 + [1]
    assert accumulator.property("remainder") == 0


def test_negative_and_multiple_notches_preserve_remainder(accumulator):
    assert accumulator.consume(-270) == -2
    assert accumulator.property("remainder") == -30
    assert accumulator.consume(-90) == -1
    assert accumulator.property("remainder") == 0


def test_direction_reversal_cancels_partial_notch(accumulator):
    assert accumulator.consume(90) == 0
    assert accumulator.consume(-90) == 0
    assert accumulator.consume(-120) == -1


def test_horizontal_and_invalid_input_do_not_scale_or_poison_state(accumulator):
    for value in (0, float("nan"), float("inf"), -float("inf")):
        assert accumulator.consume(value) == 0
    assert accumulator.consume(120) == 1


def test_accumulators_are_independent(accumulator):
    engine = QQmlEngine()
    component = QQmlComponent(
        engine, QUrl.fromLocalFile(str(ROOT / "qml" / "WheelStepAccumulator.qml"))
    )
    other = component.create()
    assert other is not None
    assert accumulator.consume(60) == 0
    assert other.consume(60) == 0
    assert accumulator.consume(60) == 1
    assert other.property("remainder") == 60
    other.deleteLater()


def test_all_scaling_surfaces_consume_notches_before_saving():
    source = (ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    assert "resizeCompactPetFromWheel(steps * 120)" in source
    assert "resizeCompactPetFromWheel(event.angleDelta.y)" in source
    for helper, save in (
        ("componentWheelSteps", "backend.saveComponentLayout"),
        ("accessoryWheelSteps", "backend.saveAccessoryBoxLayout"),
    ):
        start = source.index(f"var steps = {helper}.consume(event.angleDelta.y)")
        end = source.index(save, start)
        assert "if (steps === 0) return" in source[start:end]
        assert "if (nextScale ===" in source[start:end]
    assert "event.angleDelta.y > 0 ?" not in source
