"""Opt-in, bounded application-side drag timings; never display latency.

No coordinates, input contents, window identity, frames or raw sample history
are accepted. The GUI owner supplies monotonic times. Aggregation is constant
space and does no I/O; the existing post-gesture writer owns persistence.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_left
from collections.abc import Callable


class _Distribution:
    # Upper-inclusive, disjoint buckets. None represents the overflow bucket.
    BOUNDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.667, 33.333, 50.0, 100.0, 250.0, 1000.0)

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.maximum: float | None = None
        self.buckets = [0] * (len(self.BOUNDS) + 1)

    def add(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0.0:
            return
        milliseconds = seconds * 1000.0
        self.count += 1
        self.total += milliseconds
        self.maximum = max(self.maximum or 0.0, milliseconds)
        self.buckets[bisect_left(self.BOUNDS, milliseconds)] += 1

    def snapshot(self) -> dict[str, object]:
        return {
            "count": self.count,
            "meanMs": round(self.total / self.count, 4) if self.count else None,
            "maxMs": round(self.maximum, 4) if self.maximum is not None else None,
            "buckets": [
                {"upperInclusiveMs": bound, "count": count}
                for bound, count in zip((*self.BOUNDS, None), self.buckets)
            ],
        }


class DragTimingProbe:
    """Single-GUI-thread measurement, explicitly enabled for one process.

    Timing ends after ten minutes, 32 character gestures, 30 seconds in one
    gesture, or 100,000 observations in a gesture (whichever happens first).
    Limits bound collection, not the actual user's drag. Only Qt delivery to
    bridge consumption and synchronous setPosition call costs are measured.
    """

    MAX_GESTURES = 32
    MAX_SESSION_SECONDS = 600.0
    MAX_GESTURE_SECONDS = 30.0
    MAX_OBSERVATIONS = 100_000

    def __init__(
        self, *, enabled: bool = False, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.enabled = bool(enabled)
        self._clock = clock
        self._deadline = clock() + self.MAX_SESSION_SECONDS if enabled else 0.0
        self._gestures = 0
        self._generation = 0
        self._active = False
        self._open = False
        self._status = "armed" if enabled else "disabled"
        self._clear()

    @property
    def active(self) -> bool:
        return self._active

    def _clear(self) -> None:
        self._started_at = 0.0
        self._observations = 0
        self._latest_serial = 0
        self._consumed_serial = 0
        self._delivered_at: float | None = None
        self._last_pull_at: float | None = None
        self._last_submission_at: float | None = None
        self._pointer_events = 0
        self._submission_starts = 0
        self._failed_submissions = 0
        self._delivery_to_consumption = _Distribution()
        self._pull_gaps = _Distribution()
        self._submission_calls = _Distribution()
        self._submission_gaps = _Distribution()

    def begin(self) -> None:
        if not self.enabled or self._open:
            return
        self._open = True
        now = self._clock()
        self._generation += 1
        self._clear()
        if now >= self._deadline:
            self._status = "session-expired"
        elif self._gestures >= self.MAX_GESTURES:
            self._status = "gesture-limit"
        else:
            self._gestures += 1
            self._started_at = now
            self._active = True
            self._status = "collecting"

    def _observe(self) -> float | None:
        if not self._active:
            return None
        now = self._clock()
        if now >= self._deadline:
            self._status = "session-expired"
        elif now - self._started_at >= self.MAX_GESTURE_SECONDS:
            self._status = "gesture-time-limit"
        elif self._observations >= self.MAX_OBSERVATIONS:
            self._status = "observation-limit"
        else:
            self._observations += 1
            return now
        self._active = False
        return None

    def pointer_delivered(self, serial: int) -> None:
        now = self._observe()
        if now is None or serial <= self._latest_serial:
            return
        self._latest_serial = serial
        self._delivered_at = now
        self._pointer_events += 1

    def pointer_pulled(self, serial: int, *, available: bool) -> None:
        now = self._observe()
        if now is None:
            return
        if self._last_pull_at is not None:
            self._pull_gaps.add(now - self._last_pull_at)
        self._last_pull_at = now
        if (
            available
            and self._delivered_at is not None
            and serial == self._latest_serial
            and serial > self._consumed_serial
        ):
            self._delivery_to_consumption.add(now - self._delivered_at)
            self._consumed_serial = serial

    def submission_started(self) -> tuple[int, float] | None:
        now = self._observe()
        if now is None:
            return None
        self._submission_starts += 1
        if self._last_submission_at is not None:
            self._submission_gaps.add(now - self._last_submission_at)
        self._last_submission_at = now
        return (self._generation, now)

    def submission_finished(
        self, token: tuple[int, float] | None, *, succeeded: bool
    ) -> None:
        # setPosition can synchronously re-enter Qt. Do not attribute a late
        # return to a finished gesture, or to a new press after it.
        if token is None or token[0] != self._generation:
            return
        now = self._observe()
        if now is None:
            return
        self._submission_calls.add(now - token[1])
        if not succeeded:
            self._failed_submissions += 1

    def finish(self) -> dict[str, object]:
        if self._active:
            self._observe()  # Mark a quiet gesture that exceeded its time limit.
            if self._active:
                self._status = "finished"
            self._active = False
        self._open = False
        return {
            "version": 1,
            "enabled": self.enabled,
            "state": self._status,
            "scope": "application-direct-path-only",
            "measuresPresentedFrames": False,
            "measuresInputQueueDelay": False,
            "gestureNumber": self._gestures,
            "observations": self._observations,
            "pointerEvents": self._pointer_events,
            "unconsumedOrCoalescedEvents": (
                self._pointer_events - self._delivery_to_consumption.count
            ),
            "failedSubmissions": self._failed_submissions,
            "submissionStarts": self._submission_starts,
            "submissionsWithoutTimedReturn": (
                self._submission_starts - self._submission_calls.count
            ),
            "bridgeDeliveryToConsumption": self._delivery_to_consumption.snapshot(),
            "bridgePullGap": self._pull_gaps.snapshot(),
            "setPositionCall": self._submission_calls.snapshot(),
            "setPositionStartGap": self._submission_gaps.snapshot(),
        }

    def cancel(self) -> None:
        if self._active:
            self._status = "cancelled"
        self._active = False
        self._open = False
