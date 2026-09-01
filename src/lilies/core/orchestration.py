from __future__ import annotations

"""Thread-safe orchestration primitives for the v0.3 desktop companion.

The classes in this module deliberately know nothing about Qt, Ollama, Codex,
or a particular model client.  They only decide whether Lilies may be present,
which fixed animation intent may run, and which queued model job owns a model.
That keeps privacy and cancellation rules deterministic even when every model
is offline.
"""

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable


class PresenceState(str, Enum):
    NORMAL = "normal"
    SILENT = "silent"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PresenceSignals:
    sensitive: bool = False
    meeting: bool = False
    remote_desktop: bool = False
    locked: bool = False
    uac: bool = False
    fullscreen_game: bool = False

    def blocked_reasons(self) -> tuple[str, ...]:
        values = (
            ("sensitive", self.sensitive),
            ("meeting", self.meeting),
            ("remote-desktop", self.remote_desktop),
            ("locked", self.locked),
            ("uac", self.uac),
        )
        return tuple(name for name, enabled in values if enabled)


@dataclass(frozen=True, slots=True)
class PresenceSnapshot:
    state: PresenceState
    reasons: tuple[str, ...]
    signals: PresenceSignals
    generation: int
    changed_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reasons": list(self.reasons),
            "generation": self.generation,
            "changedAt": self.changed_at,
            "signals": {
                "sensitive": self.signals.sensitive,
                "meeting": self.signals.meeting,
                "remoteDesktop": self.signals.remote_desktop,
                "locked": self.signals.locked,
                "uac": self.signals.uac,
                "fullscreenGame": self.signals.fullscreen_game,
            },
        }


_PRESENCE_SIGNAL_ALIASES = {
    "sensitive": "sensitive",
    "meeting": "meeting",
    "remote": "remote_desktop",
    "remotedesktop": "remote_desktop",
    "remote_desktop": "remote_desktop",
    "locked": "locked",
    "lock": "locked",
    "uac": "uac",
    "fullscreengame": "fullscreen_game",
    "fullscreen_game": "fullscreen_game",
    "game": "fullscreen_game",
}


class PresenceStateMachine:
    """Resolve privacy signals with ``BLOCKED > SILENT > NORMAL`` precedence."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._snapshot = PresenceSnapshot(
            PresenceState.NORMAL,
            (),
            PresenceSignals(),
            0,
            float(clock()),
        )

    @staticmethod
    def _normalize_changes(values: Mapping[str, Any]) -> dict[str, bool]:
        normalized: dict[str, bool] = {}
        for raw_name, value in values.items():
            compact = str(raw_name).replace("-", "").casefold()
            name = _PRESENCE_SIGNAL_ALIASES.get(
                compact,
                _PRESENCE_SIGNAL_ALIASES.get(str(raw_name).casefold()),
            )
            if name is None:
                raise ValueError(f"unknown presence signal: {raw_name}")
            if not isinstance(value, bool):
                raise TypeError(f"presence signal {raw_name} must be bool")
            normalized[name] = value
        return normalized

    @staticmethod
    def _resolve(signals: PresenceSignals) -> tuple[PresenceState, tuple[str, ...]]:
        blocked = signals.blocked_reasons()
        if blocked:
            return PresenceState.BLOCKED, blocked
        if signals.fullscreen_game:
            return PresenceState.SILENT, ("fullscreen-game",)
        return PresenceState.NORMAL, ()

    def update(
        self,
        signals: PresenceSignals | Mapping[str, Any] | None = None,
        **changes: Any,
    ) -> PresenceSnapshot:
        """Replace all signals, or patch the current signals with keyword flags.

        Supplying a :class:`PresenceSignals` value replaces the previous set.
        Supplying a mapping or keyword arguments patches the current set.  This
        distinction prevents an unrelated window event from accidentally
        clearing an active privacy reason.
        """

        with self._lock:
            if isinstance(signals, PresenceSignals):
                if changes:
                    raise ValueError("cannot combine PresenceSignals with keyword changes")
                next_signals = signals
            else:
                patch: dict[str, Any] = {}
                if signals is not None:
                    if not isinstance(signals, Mapping):
                        raise TypeError("signals must be PresenceSignals, a mapping, or None")
                    patch.update(signals)
                patch.update(changes)
                normalized = self._normalize_changes(patch)
                next_signals = replace(self._snapshot.signals, **normalized)

            state, reasons = self._resolve(next_signals)
            previous = self._snapshot
            if next_signals == previous.signals:
                return previous
            self._snapshot = PresenceSnapshot(
                state,
                reasons,
                next_signals,
                previous.generation + 1,
                float(self._clock()),
            )
            return self._snapshot

    set_context = update

    def reset(self) -> PresenceSnapshot:
        return self.update(PresenceSignals())

    def snapshot(self) -> PresenceSnapshot:
        with self._lock:
            return self._snapshot

    def status(self) -> dict[str, Any]:
        return self.snapshot().to_dict()


class PetIntentKind(str, Enum):
    PRAYER = "prayer"
    PERCH_TOP = "perch-top"
    EDGE_PEEK_LEFT = "edge-peek-left"
    EDGE_PEEK_RIGHT = "edge-peek-right"
    LISTEN = "listen"
    READ_PAPER = "read-paper"
    PRESENT = "present"
    HOLD_BOX = "hold-box"
    REST = "rest"
    OPEN_RADIAL_MENU = "open-radial-menu"
    CLOSE_RADIAL_MENU = "close-radial-menu"
    SHOW_BUBBLE = "show-bubble"
    HIDE_BUBBLE = "hide-bubble"
    CELEBRATE_UNLOCK = "celebrate-unlock"


class PetIntentSource(str, Enum):
    SYSTEM = "system"
    USER = "user"
    HABITAT = "habitat"
    FOCUS = "focus"
    READING = "reading"
    REMINDER = "reminder"
    GROWTH = "growth"
    COMPANION = "companion"
    MODEL = "model"


class IntentState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


_POSE_INTENTS = frozenset(
    {
        PetIntentKind.PRAYER,
        PetIntentKind.PERCH_TOP,
        PetIntentKind.EDGE_PEEK_LEFT,
        PetIntentKind.EDGE_PEEK_RIGHT,
        PetIntentKind.LISTEN,
        PetIntentKind.READ_PAPER,
        PetIntentKind.PRESENT,
        PetIntentKind.HOLD_BOX,
        PetIntentKind.REST,
    }
)

_INTENT_PRIORITIES: dict[PetIntentKind, int] = {
    PetIntentKind.HIDE_BUBBLE: 100,
    PetIntentKind.CLOSE_RADIAL_MENU: 90,
    PetIntentKind.SHOW_BUBBLE: 80,
    PetIntentKind.CELEBRATE_UNLOCK: 70,
    PetIntentKind.OPEN_RADIAL_MENU: 60,
    PetIntentKind.LISTEN: 45,
    PetIntentKind.READ_PAPER: 45,
    PetIntentKind.PRESENT: 45,
    PetIntentKind.HOLD_BOX: 40,
    PetIntentKind.PERCH_TOP: 30,
    PetIntentKind.EDGE_PEEK_LEFT: 30,
    PetIntentKind.EDGE_PEEK_RIGHT: 30,
    PetIntentKind.PRAYER: 20,
    PetIntentKind.REST: 10,
}

_FORBIDDEN_INTENT_KEYS = frozenset(
    {
        "x",
        "y",
        "dx",
        "dy",
        "left",
        "top",
        "right",
        "bottom",
        "position",
        "coordinate",
        "coordinates",
        "anchorx",
        "anchory",
        "targetx",
        "targety",
        "script",
        "code",
        "command",
        "shell",
        "exec",
        "executable",
        "expression",
        "javascript",
        "python",
        "qml",
        "callback",
    }
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_BUBBLE_CATEGORIES = frozenset(
    {"科普", "吐槽", "笑话", "哲思", "新闻", "科研进展", "盒中世界", "任务", "提醒", "对话"}
)


def _walk_intent_value(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("intent payload nesting is too deep")
    if isinstance(value, Mapping):
        if len(value) > 16:
            raise ValueError("intent payload has too many fields")
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("intent payload keys must be strings")
            normalized = key.replace("_", "").replace("-", "").casefold()
            if normalized in _FORBIDDEN_INTENT_KEYS:
                raise ValueError(f"intent payload field is forbidden: {key}")
            _walk_intent_value(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > 16:
            raise ValueError("intent payload list is too long")
        for child in value:
            _walk_intent_value(child, depth=depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise TypeError("intent payload must contain only JSON-compatible values")


def _bounded_number(
    value: Any, minimum: float, maximum: float, label: str
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    if not minimum <= float(value) <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return value


def _validated_intent_payload(
    kind: PetIntentKind, payload: Mapping[str, Any] | None
) -> dict[str, Any]:
    if payload is None:
        raw: dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        raw = dict(payload)
    else:
        raise TypeError("intent payload must be a mapping")
    _walk_intent_value(raw)

    if kind in _POSE_INTENTS:
        allowed = {"durationMs", "intensity", "loop"}
    elif kind is PetIntentKind.SHOW_BUBBLE:
        allowed = {"bubbleId", "category", "durationMs", "variant"}
    elif kind is PetIntentKind.CELEBRATE_UNLOCK:
        allowed = {"unlockId", "durationMs", "intensity"}
    else:
        allowed = set()
    unexpected = set(raw) - allowed
    if unexpected:
        raise ValueError(f"unsupported payload fields for {kind.value}: {sorted(unexpected)}")

    if "durationMs" in raw:
        raw["durationMs"] = int(
            _bounded_number(raw["durationMs"], 0, 120_000, "durationMs")
        )
    if "intensity" in raw:
        raw["intensity"] = float(
            _bounded_number(raw["intensity"], 0.0, 1.0, "intensity")
        )
    if "loop" in raw and not isinstance(raw["loop"], bool):
        raise TypeError("loop must be bool")
    if "bubbleId" in raw:
        bubble_id = raw["bubbleId"]
        if not isinstance(bubble_id, str) or not _SAFE_IDENTIFIER.fullmatch(bubble_id):
            raise ValueError("bubbleId must be a safe identifier")
    if "unlockId" in raw:
        unlock_id = raw["unlockId"]
        if not isinstance(unlock_id, str) or not _SAFE_IDENTIFIER.fullmatch(unlock_id):
            raise ValueError("unlockId must be a safe identifier")
    if "category" in raw:
        if raw["category"] not in _BUBBLE_CATEGORIES:
            raise ValueError("unknown bubble category")
    if "variant" in raw:
        if raw["variant"] not in {"compact", "normal", "expanded"}:
            raise ValueError("unknown bubble variant")
    return raw


@dataclass(frozen=True, slots=True)
class PetIntentEvent:
    id: str
    kind: PetIntentKind
    source: PetIntentSource
    payload: Mapping[str, Any]
    priority: int
    state: IntentState
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    cancel_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "source": self.source.value,
            "payload": dict(self.payload),
            "priority": self.priority,
            "state": self.state.value,
            "submittedAt": self.submitted_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "cancelReason": self.cancel_reason,
        }


class IntentArbiter:
    """Admit only fixed, bounded pet intents and serialize their execution."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._events: dict[str, PetIntentEvent] = {}
        self._order: dict[str, int] = {}
        self._next_order = 0
        self._active_id: str | None = None
        self._queued_ids: list[str] = []

    def _activate_locked(self, intent_id: str) -> PetIntentEvent:
        event = replace(
            self._events[intent_id],
            state=IntentState.RUNNING,
            started_at=float(self._clock()),
        )
        self._events[intent_id] = event
        self._active_id = intent_id
        return event

    def _promote_locked(self) -> None:
        if self._active_id is not None or not self._queued_ids:
            return
        self._queued_ids.sort(
            key=lambda item_id: (
                -self._events[item_id].priority,
                self._order[item_id],
            )
        )
        self._activate_locked(self._queued_ids.pop(0))

    def submit(
        self,
        kind: PetIntentKind | str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: PetIntentSource | str = PetIntentSource.SYSTEM,
    ) -> PetIntentEvent:
        try:
            normalized_kind = kind if isinstance(kind, PetIntentKind) else PetIntentKind(kind)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown pet intent: {kind}") from error
        try:
            normalized_source = (
                source if isinstance(source, PetIntentSource) else PetIntentSource(source)
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown pet intent source: {source}") from error
        clean_payload = _validated_intent_payload(normalized_kind, payload)
        now = float(self._clock())

        with self._lock:
            intent_id = uuid.uuid4().hex
            event = PetIntentEvent(
                intent_id,
                normalized_kind,
                normalized_source,
                clean_payload,
                _INTENT_PRIORITIES[normalized_kind],
                IntentState.QUEUED,
                now,
            )
            self._events[intent_id] = event
            self._order[intent_id] = self._next_order
            self._next_order += 1
            active = self._events.get(self._active_id or "")
            if active is None:
                return self._activate_locked(intent_id)
            if event.priority > active.priority:
                self._events[active.id] = replace(
                    active,
                    state=IntentState.CANCELLED,
                    finished_at=now,
                    cancel_reason=f"preempted-by:{intent_id}",
                )
                self._active_id = None
                return self._activate_locked(intent_id)
            self._queued_ids.append(intent_id)
            return event

    def finish(self, intent_id: str) -> PetIntentEvent:
        with self._lock:
            event = self._events.get(intent_id)
            if event is None:
                raise KeyError(intent_id)
            if event.state is not IntentState.RUNNING:
                raise ValueError("only a running intent can finish")
            completed = replace(
                event,
                state=IntentState.COMPLETED,
                finished_at=float(self._clock()),
            )
            self._events[intent_id] = completed
            self._active_id = None
            self._promote_locked()
            return completed

    def cancel(self, intent_id: str, *, reason: str = "cancelled") -> PetIntentEvent:
        if not isinstance(reason, str) or not 1 <= len(reason) <= 160:
            raise ValueError("cancel reason must contain 1 to 160 characters")
        with self._lock:
            event = self._events.get(intent_id)
            if event is None:
                raise KeyError(intent_id)
            if event.state in {IntentState.COMPLETED, IntentState.CANCELLED}:
                return event
            cancelled = replace(
                event,
                state=IntentState.CANCELLED,
                finished_at=float(self._clock()),
                cancel_reason=reason,
            )
            self._events[intent_id] = cancelled
            if event.state is IntentState.RUNNING:
                self._active_id = None
            else:
                self._queued_ids.remove(intent_id)
            self._promote_locked()
            return cancelled

    def get(self, intent_id: str) -> PetIntentEvent | None:
        with self._lock:
            return self._events.get(intent_id)

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._events.get(self._active_id or "")
            queued = sorted(
                (self._events[item_id] for item_id in self._queued_ids),
                key=lambda event: (-event.priority, self._order[event.id]),
            )
            return {
                "active": active.to_dict() if active else None,
                "queued": [event.to_dict() for event in queued],
            }


class ModelTaskKind(str, Enum):
    EXPLICIT_CHAT_REPLY = "explicit-chat-reply"
    PAPER_SELECTION = "paper-selection"
    CONNECTOR_ASSIST = "connector-assist"
    PROACTIVE = "proactive"
    SCREEN_UNDERSTANDING = "screen-understanding"
    MEMORY_ARCHIVE = "memory-archive"


MODEL_TASK_PRIORITIES: dict[ModelTaskKind, int] = {
    ModelTaskKind.EXPLICIT_CHAT_REPLY: 600,
    ModelTaskKind.PAPER_SELECTION: 500,
    ModelTaskKind.CONNECTOR_ASSIST: 400,
    ModelTaskKind.PROACTIVE: 300,
    ModelTaskKind.SCREEN_UNDERSTANDING: 200,
    ModelTaskKind.MEMORY_ARCHIVE: 100,
}

_DEFAULT_CONTEXT_BOUND = frozenset(
    {
        ModelTaskKind.PAPER_SELECTION,
        ModelTaskKind.PROACTIVE,
        ModelTaskKind.SCREEN_UNDERSTANDING,
    }
)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}$")


class ModelTaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ModelTask:
    id: str
    model_id: str
    kind: ModelTaskKind
    payload: Mapping[str, Any]
    priority: int
    state: ModelTaskState
    context_generation: int
    context_bound: bool
    submitted_at: float
    expires_at: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    cancel_reason: str = ""
    result: Any = field(default=None, repr=False, compare=False)

    @property
    def terminal(self) -> bool:
        return self.state in {ModelTaskState.COMPLETED, ModelTaskState.CANCELLED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "modelId": self.model_id,
            "kind": self.kind.value,
            "priority": self.priority,
            "state": self.state.value,
            "contextGeneration": self.context_generation,
            "contextBound": self.context_bound,
            "submittedAt": self.submitted_at,
            "expiresAt": self.expires_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "cancelReason": self.cancel_reason,
        }


class ModelTaskBroker:
    """Serialize work per model and deterministically preempt lower priorities."""

    _TERMINAL_HISTORY_LIMIT = 256

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._tasks: dict[str, ModelTask] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._order: dict[str, int] = {}
        self._next_order = 0
        self._active_by_model: dict[str, str] = {}
        self._queued_by_model: dict[str, list[str]] = {}
        self._terminal_ids: deque[str] = deque()
        self._context_generation = 0
        self._foreground_context: str | None = None

    def _remember_terminal_locked(self, task_id: str) -> None:
        """Keep recent diagnostics without retaining every completed request."""

        self._terminal_ids.append(task_id)
        while len(self._terminal_ids) > self._TERMINAL_HISTORY_LIMIT:
            expired_id = self._terminal_ids.popleft()
            expired = self._tasks.get(expired_id)
            if expired is None or not expired.terminal:
                continue
            self._tasks.pop(expired_id, None)
            self._cancel_events.pop(expired_id, None)
            self._order.pop(expired_id, None)

    @staticmethod
    def _kind(value: ModelTaskKind | str) -> ModelTaskKind:
        try:
            return value if isinstance(value, ModelTaskKind) else ModelTaskKind(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown model task kind: {value}") from error

    @staticmethod
    def _model_id(value: str) -> str:
        if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
            raise ValueError("model_id must be a safe identifier")
        return value

    def _activate_locked(self, task_id: str, now: float | None = None) -> ModelTask:
        task = self._tasks[task_id]
        started = float(self._clock()) if now is None else now
        running = replace(task, state=ModelTaskState.RUNNING, started_at=started)
        self._tasks[task_id] = running
        self._active_by_model[task.model_id] = task_id
        return running

    def _cancel_locked(
        self,
        task_id: str,
        reason: str,
        *,
        now: float,
        promote: bool,
    ) -> ModelTask:
        task = self._tasks[task_id]
        if task.terminal:
            return task
        cancelled = replace(
            task,
            state=ModelTaskState.CANCELLED,
            finished_at=now,
            cancel_reason=reason,
        )
        self._tasks[task_id] = cancelled
        self._cancel_events[task_id].set()
        if task.state is ModelTaskState.RUNNING:
            self._active_by_model.pop(task.model_id, None)
        else:
            queue = self._queued_by_model.get(task.model_id, [])
            if task_id in queue:
                queue.remove(task_id)
        if promote:
            self._promote_locked(task.model_id, now=now)
        self._remember_terminal_locked(task_id)
        return cancelled

    def _promote_locked(self, model_id: str, *, now: float) -> ModelTask | None:
        if model_id in self._active_by_model:
            return None
        queue = self._queued_by_model.setdefault(model_id, [])
        queue.sort(
            key=lambda task_id: (
                -self._tasks[task_id].priority,
                self._order[task_id],
            )
        )
        while queue:
            task_id = queue.pop(0)
            task = self._tasks[task_id]
            if task.expires_at is not None and task.expires_at <= now:
                self._cancel_locked(
                    task_id, "expired", now=now, promote=False
                )
                continue
            if task.context_bound and task.context_generation < self._context_generation:
                self._cancel_locked(
                    task_id, "foreground-context-changed", now=now, promote=False
                )
                continue
            return self._activate_locked(task_id, now)
        self._queued_by_model.pop(model_id, None)
        return None

    def _prune_expired_locked(self, now: float) -> tuple[str, ...]:
        expired = [
            task.id
            for task in self._tasks.values()
            if not task.terminal
            and task.expires_at is not None
            and task.expires_at <= now
        ]
        affected: set[str] = set()
        for task_id in expired:
            affected.add(self._tasks[task_id].model_id)
            self._cancel_locked(task_id, "expired", now=now, promote=False)
        for model_id in affected:
            self._promote_locked(model_id, now=now)
        return tuple(expired)

    def submit(
        self,
        model_id: str,
        kind: ModelTaskKind | str,
        payload: Mapping[str, Any] | None = None,
        *,
        context_bound: bool | None = None,
        expires_at: float | None = None,
    ) -> ModelTask:
        normalized_model = self._model_id(model_id)
        normalized_kind = self._kind(kind)
        if payload is None:
            clean_payload: dict[str, Any] = {}
        elif isinstance(payload, Mapping):
            clean_payload = dict(payload)
        else:
            raise TypeError("model task payload must be a mapping")
        if context_bound is not None and not isinstance(context_bound, bool):
            raise TypeError("context_bound must be bool or None")
        if expires_at is not None and (
            isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
        ):
            raise TypeError("expires_at must be a number or None")

        now = float(self._clock())
        with self._lock:
            self._prune_expired_locked(now)
            task_id = uuid.uuid4().hex
            task = ModelTask(
                id=task_id,
                model_id=normalized_model,
                kind=normalized_kind,
                payload=clean_payload,
                priority=MODEL_TASK_PRIORITIES[normalized_kind],
                state=ModelTaskState.QUEUED,
                context_generation=self._context_generation,
                context_bound=(
                    normalized_kind in _DEFAULT_CONTEXT_BOUND
                    if context_bound is None
                    else context_bound
                ),
                submitted_at=now,
                expires_at=float(expires_at) if expires_at is not None else None,
            )
            self._tasks[task_id] = task
            self._cancel_events[task_id] = threading.Event()
            self._order[task_id] = self._next_order
            self._next_order += 1
            if task.expires_at is not None and task.expires_at <= now:
                return self._cancel_locked(
                    task_id, "expired", now=now, promote=False
                )

            active_id = self._active_by_model.get(normalized_model)
            if active_id is None:
                return self._activate_locked(task_id, now)
            active = self._tasks[active_id]
            if task.priority > active.priority:
                self._cancel_locked(
                    active_id,
                    f"preempted-by:{task_id}",
                    now=now,
                    promote=False,
                )
                return self._activate_locked(task_id, now)
            self._queued_by_model.setdefault(normalized_model, []).append(task_id)
            return task

    def finish(self, task_id: str, *, result: Any = None) -> ModelTask:
        now = float(self._clock())
        with self._lock:
            self._prune_expired_locked(now)
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.state is not ModelTaskState.RUNNING:
                raise ValueError("only a running model task can finish")
            completed = replace(
                task,
                state=ModelTaskState.COMPLETED,
                finished_at=now,
                result=result,
            )
            self._tasks[task_id] = completed
            self._active_by_model.pop(task.model_id, None)
            self._promote_locked(task.model_id, now=now)
            self._remember_terminal_locked(task_id)
            return completed

    def cancel(self, task_id: str, *, reason: str = "cancelled") -> ModelTask:
        if not isinstance(reason, str) or not 1 <= len(reason) <= 160:
            raise ValueError("cancel reason must contain 1 to 160 characters")
        now = float(self._clock())
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return self._cancel_locked(task_id, reason, now=now, promote=True)

    def cancel_expired(self) -> tuple[str, ...]:
        with self._lock:
            return self._prune_expired_locked(float(self._clock()))

    def _advance_context_locked(self, foreground_context: str | None) -> tuple[str, ...]:
        self._context_generation += 1
        self._foreground_context = foreground_context
        now = float(self._clock())
        stale_ids = [
            task.id
            for task in self._tasks.values()
            if not task.terminal
            and task.context_bound
            and task.context_generation < self._context_generation
        ]
        affected: set[str] = set()
        for task_id in stale_ids:
            affected.add(self._tasks[task_id].model_id)
            self._cancel_locked(
                task_id,
                "foreground-context-changed",
                now=now,
                promote=False,
            )
        for model_id in affected:
            self._promote_locked(model_id, now=now)
        return tuple(stale_ids)

    def advance_context(self, foreground_context: str | None = None) -> tuple[str, ...]:
        """Advance the foreground generation and cancel context-bound jobs."""

        if foreground_context is not None and not isinstance(foreground_context, str):
            raise TypeError("foreground_context must be a string or None")
        with self._lock:
            return self._advance_context_locked(foreground_context)

    def set_foreground_context(self, foreground_context: str | None) -> tuple[str, ...]:
        """Advance only when the supplied foreground identity actually changes."""

        if foreground_context is not None and not isinstance(foreground_context, str):
            raise TypeError("foreground_context must be a string or None")
        with self._lock:
            if foreground_context == self._foreground_context:
                return ()
            return self._advance_context_locked(foreground_context)

    def get(self, task_id: str) -> ModelTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancellation_event(self, task_id: str) -> threading.Event:
        with self._lock:
            event = self._cancel_events.get(task_id)
            if event is None:
                raise KeyError(task_id)
            return event

    def status(self, model_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._prune_expired_locked(float(self._clock()))
            if model_id is not None:
                model_ids = [self._model_id(model_id)]
            else:
                model_ids = sorted(
                    set(self._active_by_model) | set(self._queued_by_model)
                )
            models: dict[str, Any] = {}
            for current_model in model_ids:
                active = self._tasks.get(self._active_by_model.get(current_model, ""))
                queue = sorted(
                    (
                        self._tasks[task_id]
                        for task_id in self._queued_by_model.get(current_model, [])
                    ),
                    key=lambda task: (-task.priority, self._order[task.id]),
                )
                models[current_model] = {
                    "active": active.to_dict() if active else None,
                    "queued": [task.to_dict() for task in queue],
                }
            return {
                "contextGeneration": self._context_generation,
                "foregroundContext": self._foreground_context,
                "models": models,
            }


__all__ = [
    "IntentArbiter",
    "IntentState",
    "MODEL_TASK_PRIORITIES",
    "ModelTask",
    "ModelTaskBroker",
    "ModelTaskKind",
    "ModelTaskState",
    "PetIntentEvent",
    "PetIntentKind",
    "PetIntentSource",
    "PresenceSignals",
    "PresenceSnapshot",
    "PresenceState",
    "PresenceStateMachine",
]
