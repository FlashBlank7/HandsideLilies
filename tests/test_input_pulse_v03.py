from __future__ import annotations

from lilies.core.input_pulse import InputPulseSource, NativeInputSample


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeProvider:
    available = True

    def __init__(self, values: list[NativeInputSample]) -> None:
        self.values = list(values)

    def read(self):
        return self.values.pop(0) if self.values else None


def test_pulse_exposes_only_rolling_aggregates() -> None:
    clock = Clock(10.0)
    provider = FakeProvider(
        [
            NativeInputSample(1000, 1000, 50, 50),
            NativeInputSample(1050, 1050, 60, 50),
            NativeInputSample(1100, 1100, 60, 50),
        ]
    )
    pulse = InputPulseSource(provider, clock=clock, window_seconds=3.0)
    pulse.sample()
    clock.value += 0.1
    pulse.sample()
    clock.value += 0.1
    result = pulse.sample()

    assert result["eventCount"] == 2
    assert result["pointerEvents"] == 1
    assert result["stationaryEvents"] == 1
    assert result["cursorDirection"] == "east"
    assert result["cursorDistance"] == 10.0
    assert "cursorX" not in result
    assert "cursorY" not in result
    assert "lastInputTick" not in result


def test_old_events_expire_and_sensitive_suppression_erases_memory() -> None:
    clock = Clock(0.0)
    provider = FakeProvider(
        [
            NativeInputSample(1, 1, 0, 0),
            NativeInputSample(2, 2, 5, 5),
            NativeInputSample(3, 3, 10, 10),
        ]
    )
    pulse = InputPulseSource(provider, clock=clock, window_seconds=3.0)
    pulse.sample()
    clock.value = 0.1
    assert pulse.sample()["eventCount"] == 1

    pulse.set_suppressed(True)
    suppressed = pulse.snapshot()
    assert suppressed["state"] == "suppressed"
    assert suppressed["eventCount"] == 0
    assert suppressed["idleSeconds"] is None

    pulse.set_suppressed(False)
    clock.value = 4.0
    assert pulse.sample()["eventCount"] == 0  # new privacy baseline


def test_unavailable_provider_degrades_without_starting_a_thread() -> None:
    class UnavailableProvider:
        available = False

        def read(self):
            raise AssertionError("must not read when unavailable")

    pulse = InputPulseSource(UnavailableProvider())

    assert pulse.start() is False
    assert pulse.sample()["state"] == "unavailable"
    assert pulse.running is False

