from __future__ import annotations

"""Transient, deterministic diversion reminders for explicit focus sessions.

This module observes only an application identity/category supplied by the
foreground-window layer.  It does not record keystrokes, titles, or all-day
application time and it intentionally has no database dependency.
"""

import math
import ntpath
import time
from dataclasses import dataclass
from typing import Callable


_ENTERTAINMENT_PROCESSES = frozenset(
    {
        "steam.exe",
        "steamwebhelper.exe",
        "epicgameslauncher.exe",
        "galaxyclient.exe",
        "goggalaxy.exe",
        "battle.net.exe",
        "xboxpcapp.exe",
        "spotify.exe",
        "cloudmusic.exe",
        "qqmusic.exe",
        "potplayermini64.exe",
        "vlc.exe",
    }
)


def is_entertainment_process(process_name: str, *, is_game: bool = False) -> bool:
    """Return a conservative category signal without inspecting window text."""

    if is_game:
        return True
    executable = ntpath.basename(str(process_name or "").strip()).casefold()
    return executable in _ENTERTAINMENT_PROCESSES


@dataclass(frozen=True, slots=True)
class DiversionReminder:
    reminder_id: str
    session_id: str
    context_key: str
    process_name: str
    stable_seconds: int
    delivery: str
    actions: tuple[str, ...] = ("return", "rest", "finish")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.reminder_id,
            "sessionId": self.session_id,
            "contextKey": self.context_key,
            "processName": self.process_name,
            "stableSeconds": self.stable_seconds,
            "delivery": self.delivery,
            "actions": list(self.actions),
        }


class FocusDiversionMonitor:
    """Emit at most one reminder for one uninterrupted entertainment visit.

    A later visit can emit only after ``cooldown_seconds``.  Sensitive,
    meeting and remote-desktop contexts may mark delivery as deferred; elapsed
    stability is retained and the reminder is emitted after the block clears.
    Full-screen games use the Windows notification channel rather than a
    topmost pet bubble.
    """

    def __init__(
        self,
        *,
        stable_seconds: float = 45.0,
        cooldown_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stable_seconds = max(1.0, float(stable_seconds))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.clock = clock
        self._session_id = ""
        self._focus_running = False
        self._context_key = ""
        self._process_name = ""
        self._entertainment = False
        self._full_screen_game = False
        self._deferred = False
        self._entered_at: float | None = None
        self._reminded_visit = False
        self._last_reminder_at = -math.inf
        self._sequence = 0
        self._last_reminder: DiversionReminder | None = None

    def set_focus(self, session_id: str, state: str, *, now: float | None = None) -> None:
        current = self.clock() if now is None else float(now)
        identity = str(session_id or "")
        running = bool(identity) and str(state).casefold() == "running"
        changed_session = identity != self._session_id
        changed_running = running != self._focus_running
        self._session_id = identity
        self._focus_running = running
        if changed_session:
            self._last_reminder = None
        if changed_session or changed_running:
            self._restart_visit(current)

    def update_foreground(
        self,
        context_key: str,
        process_name: str,
        *,
        entertainment: bool,
        full_screen_game: bool = False,
        defer_reminder: bool = False,
        now: float | None = None,
    ) -> None:
        current = self.clock() if now is None else float(now)
        identity = str(context_key or "")
        category_changed = bool(entertainment) != self._entertainment
        context_changed = identity != self._context_key
        self._context_key = identity
        self._process_name = str(process_name or "")
        self._entertainment = bool(entertainment)
        self._full_screen_game = bool(full_screen_game)
        self._deferred = bool(defer_reminder)
        if context_changed or category_changed:
            self._restart_visit(current)

    def tick(self, now: float | None = None) -> DiversionReminder | None:
        current = self.clock() if now is None else float(now)
        if not self._focus_running or not self._entertainment:
            return None
        if self._entered_at is None:
            self._entered_at = current
            return None
        elapsed = current - self._entered_at
        if elapsed < self.stable_seconds or self._reminded_visit or self._deferred:
            return None
        if current - self._last_reminder_at < self.cooldown_seconds:
            return None
        self._sequence += 1
        reminder = DiversionReminder(
            reminder_id=f"focus-diversion:{self._session_id}:{self._sequence}",
            session_id=self._session_id,
            context_key=self._context_key,
            process_name=self._process_name,
            stable_seconds=max(0, int(elapsed)),
            delivery="windows-notification" if self._full_screen_game else "bubble",
        )
        self._reminded_visit = True
        self._last_reminder_at = current
        self._last_reminder = reminder
        return reminder

    def acknowledge(self, action: str) -> dict[str, object]:
        normalized = str(action or "").casefold()
        if normalized not in {"return", "rest", "finish", "dismiss"}:
            raise ValueError("unknown focus diversion action")
        return {
            "accepted": True,
            "action": normalized,
            "sessionId": self._session_id,
            "reminderId": self._last_reminder.reminder_id if self._last_reminder else "",
        }

    def status(self, now: float | None = None) -> dict[str, object]:
        current = self.clock() if now is None else float(now)
        return {
            "focusRunning": self._focus_running,
            "sessionId": self._session_id,
            "entertainment": self._entertainment,
            "contextKey": self._context_key,
            "stableSeconds": (
                max(0, int(current - self._entered_at)) if self._entered_at is not None else 0
            ),
            "deferred": self._deferred,
            "lastReminder": self._last_reminder.to_dict() if self._last_reminder else None,
        }

    def _restart_visit(self, now: float) -> None:
        self._entered_at = (
            float(now) if self._focus_running and self._entertainment else None
        )
        self._reminded_visit = False


__all__ = [
    "DiversionReminder",
    "FocusDiversionMonitor",
    "is_entertainment_process",
]
