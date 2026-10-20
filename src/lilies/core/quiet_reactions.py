"""Transient, fixed gestures from committed work; never model instructions or prose."""
from __future__ import annotations

from collections import deque
import math
import time


class QuietReactionGate:
    """GUI-thread admission: no backlog, no restart replay, bounded identities."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.sequence = 0
        self._seen = deque(maxlen=256)
        self._last_at = -math.inf

    def accept(self, identity: str, submitted_at: float, *, allowed: bool):
        if not identity or identity in self._seen:
            return None
        self._seen.append(identity)
        now = self.clock()
        age = now - submitted_at
        if not allowed or not math.isfinite(age) or not 0 <= age <= 2.0 or now - self._last_at < 1.5:
            return None
        self.sequence += 1
        self._last_at = now
        # No task title, user text, coordinates or executable values cross this boundary.
        return {"sequence": self.sequence, "kind": "task-completed"}


def completed_task_identity(result) -> str:
    if not isinstance(result, dict) or result.get("alreadyCompleted") is not False:
        return ""
    occurrence = result.get("occurrence") or {}
    if not isinstance(occurrence, dict):
        return ""
    identifier = occurrence.get("occurrence_id")
    version = occurrence.get("completion_version")
    if (occurrence.get("status") != "completed" or not isinstance(identifier, str)
            or not identifier or len(identifier) > 128 or isinstance(version, bool)
            or not isinstance(version, int) or version < 1):
        return ""
    return f"{identifier}:{version}"
