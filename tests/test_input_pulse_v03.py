from __future__ import annotations

import threading
import time

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


def test_pointer_interaction_suspends_worker_without_recreating_thread() -> None:
    class CountingProvider:
        available = True

        def __init__(self) -> None:
            self.count = 0
            self.sampled = threading.Event()

        def read(self):
            self.count += 1
            self.sampled.set()
            return NativeInputSample(
                self.count,
                self.count,
                self.count,
                self.count,
            )

    provider = CountingProvider()
    pulse = InputPulseSource(provider, sample_interval_seconds=0.05)
    assert pulse.start() is True
    try:
        assert provider.sampled.wait(0.5)
        worker = pulse._thread
        pulse.set_interaction_suspended(True)
        # Allow one sample that may already have crossed the condition edge,
        # then prove the same thread sleeps instead of polling or being joined.
        time.sleep(0.08)
        suspended_count = provider.count
        time.sleep(0.14)
        assert provider.count == suspended_count
        assert pulse._thread is worker
        assert pulse.running is True
        assert pulse.snapshot()["eventCount"] == 0

        pulse.set_interaction_suspended(False)
        deadline = time.monotonic() + 0.5
        while provider.count == suspended_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert provider.count > suspended_count
        assert pulse._thread is worker
    finally:
        pulse.stop()


def test_sample_already_in_provider_is_discarded_across_suspend_generation() -> None:
    class BlockingProvider:
        available = True

        def __init__(self) -> None:
            self.calls = 0
            self.second_read_started = threading.Event()
            self.release_second_read = threading.Event()

        def read(self):
            self.calls += 1
            if self.calls == 1:
                return NativeInputSample(10, 10, 1, 1)
            if self.calls == 2:
                self.second_read_started.set()
                assert self.release_second_read.wait(1.0)
                return NativeInputSample(20, 20, 2, 2)
            return NativeInputSample(20, 20, 2, 2)

    provider = BlockingProvider()
    pulse = InputPulseSource(provider, sample_interval_seconds=0.05)
    assert pulse.start() is True
    try:
        assert provider.second_read_started.wait(0.5)
        pulse.set_interaction_suspended(True)
        provider.release_second_read.set()
        time.sleep(0.08)

        assert pulse.snapshot()["eventCount"] == 0
        assert pulse._last_raw is None

        pulse.set_interaction_suspended(False)
        deadline = time.monotonic() + 0.5
        while pulse._last_raw is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pulse._last_raw is not None
        assert pulse.snapshot()["eventCount"] == 0
    finally:
        provider.release_second_read.set()
        pulse.stop()
