from __future__ import annotations

"""Domain model for Lilies' opt-in proactive companion bubbles.

Nothing in this module observes the desktop or calls a model.  It provides the
rate, topic, momentum and short-conversation boundaries used by the UI/service
layer, which makes those privacy-sensitive behaviors straightforward to test.
"""

import math
import re
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


class ContentCategory(str, Enum):
    SCIENCE = "科普"
    ROAST = "吐槽"
    JOKE = "笑话"
    PHILOSOPHY = "哲思"
    NEWS = "新闻"
    RESEARCH = "科研进展"
    LORE = "盒中世界"


@dataclass(frozen=True, slots=True)
class FrequencyConfig:
    name: str
    minimum_minutes: int
    daily_limit: int

    def __post_init__(self) -> None:
        if self.name != "off" and not 5 <= self.minimum_minutes <= 180:
            raise ValueError("minimum interval must be between 5 and 180 minutes")
        if self.name != "off" and not 1 <= self.daily_limit <= 50:
            raise ValueError("daily limit must be between 1 and 50")

    @property
    def enabled(self) -> bool:
        return self.name != "off" and self.daily_limit > 0


FREQUENCY_PRESETS: dict[str, FrequencyConfig] = {
    "off": FrequencyConfig("off", 0, 0),
    "quiet": FrequencyConfig("quiet", 45, 6),
    "balanced": FrequencyConfig("balanced", 25, 12),
    "lively": FrequencyConfig("lively", 10, 30),
}


@dataclass(slots=True)
class CompanionPreferences:
    frequency: FrequencyConfig = field(
        default_factory=lambda: FREQUENCY_PRESETS["balanced"]
    )
    category_weights: dict[ContentCategory, int] = field(
        default_factory=lambda: {category: 100 for category in ContentCategory}
    )
    interest_weight: int = 60
    scene_weight: int = 40
    momentum_half_life_minutes: int = 30

    def __post_init__(self) -> None:
        self.interest_weight = _bounded_int(self.interest_weight, 0, 100, "interest")
        self.scene_weight = _bounded_int(self.scene_weight, 0, 100, "scene")
        self.momentum_half_life_minutes = _bounded_int(
            self.momentum_half_life_minutes, 5, 180, "momentum half-life"
        )
        normalized: dict[ContentCategory, int] = {}
        for category in ContentCategory:
            raw = self.category_weights.get(category, self.category_weights.get(category.value, 100))
            normalized[category] = _bounded_int(raw, 0, 100, category.value)
        self.category_weights = normalized

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CompanionPreferences":
        mode = str(values.get("frequency", "balanced")).casefold()
        if mode == "custom":
            frequency = FrequencyConfig(
                "custom",
                int(values.get("minimumMinutes", 25)),
                int(values.get("dailyLimit", 12)),
            )
        elif mode in FREQUENCY_PRESETS:
            frequency = FREQUENCY_PRESETS[mode]
        else:
            raise ValueError(f"unknown frequency mode: {mode}")
        raw_weights = values.get("categoryWeights", {})
        category_weights: dict[ContentCategory, int] = {}
        if isinstance(raw_weights, Mapping):
            for category in ContentCategory:
                value = raw_weights.get(category.value, raw_weights.get(category.name.casefold(), 100))
                category_weights[category] = int(value)
        return cls(
            frequency=frequency,
            category_weights=category_weights,
            interest_weight=int(values.get("interestWeight", 60)),
            scene_weight=int(values.get("sceneWeight", 40)),
            momentum_half_life_minutes=int(values.get("momentumHalfLifeMinutes", 30)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency.name,
            "minimumMinutes": self.frequency.minimum_minutes,
            "dailyLimit": self.frequency.daily_limit,
            "categoryWeights": {
                category.value: weight for category, weight in self.category_weights.items()
            },
            "interestWeight": self.interest_weight,
            "sceneWeight": self.scene_weight,
            "momentumHalfLifeMinutes": self.momentum_half_life_minutes,
        }

    def category_enabled(self, category: ContentCategory | str) -> bool:
        current = category if isinstance(category, ContentCategory) else ContentCategory(category)
        return self.category_weights[current] > 0

    @property
    def normalized_mix(self) -> tuple[float, float]:
        total = self.interest_weight + self.scene_weight
        if total <= 0:
            return 0.5, 0.5
        return self.interest_weight / total, self.scene_weight / total


def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> int:
    current = int(value)
    if not minimum <= current <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return current


class EmissionGate:
    """Per-day and minimum-interval gate with an explicit snooze state."""

    def __init__(self, config: FrequencyConfig) -> None:
        self.config = config
        self._day = ""
        self._daily_count = 0
        self._last_emitted: datetime | None = None
        self._snooze_until: datetime | None = None

    def update(self, config: FrequencyConfig) -> None:
        self.config = config

    def snooze(self, until: datetime) -> None:
        self._snooze_until = _aware_utc(until)

    def restore(self, value: Mapping[str, Any] | None) -> None:
        if not isinstance(value, Mapping):
            return
        self._day = str(value.get("day", ""))[:20]
        try:
            self._daily_count = max(0, min(int(value.get("countToday", 0)), 10_000))
        except (TypeError, ValueError):
            self._daily_count = 0
        for key, attribute in (("lastEmitted", "_last_emitted"), ("snoozeUntil", "_snooze_until")):
            raw = value.get(key)
            if not raw:
                continue
            try:
                setattr(self, attribute, _aware_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00"))))
            except ValueError:
                continue

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = _aware_utc(now or datetime.now(timezone.utc))
        self.can_emit(current)
        return {
            "day": self._day,
            "countToday": self._daily_count,
            "lastEmitted": self._last_emitted.isoformat() if self._last_emitted else "",
            "snoozeUntil": self._snooze_until.isoformat() if self._snooze_until else "",
        }

    def can_emit(self, now: datetime) -> tuple[bool, str]:
        current = _aware_utc(now)
        day = current.astimezone().date().isoformat()
        if day != self._day:
            self._day = day
            self._daily_count = 0
        if not self.config.enabled:
            return False, "frequency-off"
        if self._snooze_until and current < self._snooze_until:
            return False, "snoozed"
        if self._daily_count >= self.config.daily_limit:
            return False, "daily-limit"
        if self._last_emitted is not None:
            elapsed = current - self._last_emitted
            if elapsed < timedelta(minutes=self.config.minimum_minutes):
                return False, "cooldown"
        return True, "allowed"

    def record(self, now: datetime) -> None:
        allowed, reason = self.can_emit(now)
        if not allowed:
            raise RuntimeError(f"cannot record an emission while {reason}")
        self._last_emitted = _aware_utc(now)
        self._daily_count += 1

    def state(self, now: datetime) -> dict[str, Any]:
        current = _aware_utc(now)
        allowed, reason = self.can_emit(current)
        next_allowed: datetime | None = None
        if self._last_emitted is not None and self.config.minimum_minutes > 0:
            next_allowed = self._last_emitted + timedelta(
                minutes=self.config.minimum_minutes
            )
        if self._snooze_until is not None and (
            next_allowed is None or self._snooze_until > next_allowed
        ):
            next_allowed = self._snooze_until
        remaining_seconds = (
            max(0.0, (next_allowed - current).total_seconds())
            if next_allowed is not None
            else 0.0
        )
        return {
            "allowed": allowed,
            "reason": reason,
            "frequency": self.config.name,
            "minimumMinutes": self.config.minimum_minutes,
            "countToday": self._daily_count,
            "dailyLimit": self.config.daily_limit,
            "snoozeUntil": self._snooze_until.isoformat() if self._snooze_until else "",
            "nextAllowedAt": next_allowed.isoformat() if next_allowed else "",
            "remainingSeconds": round(remaining_seconds, 1),
        }


class SceneMomentum:
    """Exponentially decayed scene evidence with hysteresis before switching."""

    def __init__(
        self,
        half_life_minutes: float = 30.0,
        switch_after_seconds: float = 180.0,
        switch_margin: float = 0.25,
        required_samples: int = 2,
    ) -> None:
        if not 5 <= half_life_minutes <= 180:
            raise ValueError("half-life must be between 5 and 180 minutes")
        self.half_life_seconds = float(half_life_minutes) * 60.0
        self.switch_after_seconds = max(0.0, float(switch_after_seconds))
        self.switch_margin = max(0.0, float(switch_margin))
        self.required_samples = max(2, int(required_samples))
        self.scores: dict[str, float] = {}
        self.current = ""
        self._updated_at: float | None = None
        self._candidate = ""
        self._candidate_since = 0.0
        self._candidate_samples = 0

    def observe(self, label: str, confidence: float, now: float) -> str:
        topic = re.sub(r"\s+", " ", str(label)).strip()[:120]
        if not topic:
            return self.current
        confidence = max(0.0, min(float(confidence), 1.0))
        self._decay(float(now))
        self.scores[topic] = self.scores.get(topic, 0.0) + confidence
        if not self.current:
            self.current = topic
            self._clear_candidate()
            return self.current
        if topic == self.current:
            self._clear_candidate()
            return self.current

        current_score = self.scores.get(self.current, 0.0)
        candidate_score = self.scores.get(topic, 0.0)
        threshold = current_score * (1.0 + self.switch_margin)
        if candidate_score <= threshold:
            if self._candidate == topic:
                self._clear_candidate()
            return self.current
        if self._candidate != topic:
            self._candidate = topic
            self._candidate_since = float(now)
            self._candidate_samples = 1
            return self.current
        self._candidate_samples += 1
        stable = float(now) - self._candidate_since >= self.switch_after_seconds
        if stable and self._candidate_samples >= self.required_samples:
            self.current = topic
            self._clear_candidate()
        return self.current

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        if now is not None:
            self._decay(float(now))
        return {
            "current": self.current,
            "scores": dict(sorted(self.scores.items(), key=lambda item: item[1], reverse=True)),
            "candidate": self._candidate,
            "candidateSamples": self._candidate_samples,
        }

    def _decay(self, now: float) -> None:
        if self._updated_at is None:
            self._updated_at = now
            return
        elapsed = max(0.0, now - self._updated_at)
        factor = math.pow(0.5, elapsed / self.half_life_seconds)
        self.scores = {
            label: score * factor
            for label, score in self.scores.items()
            if score * factor >= 0.001
        }
        self._updated_at = now

    def _clear_candidate(self) -> None:
        self._candidate = ""
        self._candidate_since = 0.0
        self._candidate_samples = 0


@dataclass(frozen=True, slots=True)
class BubbleSource:
    name: str
    url: str = ""
    published_at: datetime | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "publishedAt": self.published_at.isoformat() if self.published_at else "",
        }


DEFAULT_BUBBLE_ACTIONS = (
    "another",
    "detail",
    "simple",
    "reply",
    "categories",
    "less-often",
    "snooze-hour",
    "mute-app",
    "open-source",
    "move-to-box",
)


@dataclass(frozen=True, slots=True)
class SpeechBubble:
    id: str
    category: ContentCategory
    summary: str
    detail: str
    source: BubbleSource | None
    actions: tuple[str, ...]
    scene_label: str
    created_at: datetime
    expires_at: datetime
    content_id: str = ""
    session_id: str = ""

    @classmethod
    def create(
        cls,
        *,
        category: ContentCategory | str,
        summary: str,
        detail: str = "",
        source: BubbleSource | None = None,
        scene_label: str = "",
        created_at: datetime | None = None,
        lifetime_seconds: int = 240,
        content_id: str = "",
        actions: Sequence[str] = DEFAULT_BUBBLE_ACTIONS,
    ) -> "SpeechBubble":
        current = _aware_utc(created_at or datetime.now(timezone.utc))
        clean_summary = re.sub(r"\s+", " ", str(summary)).strip()
        if not clean_summary:
            raise ValueError("bubble summary cannot be empty")
        current_category = category if isinstance(category, ContentCategory) else ContentCategory(category)
        if current_category in {ContentCategory.NEWS, ContentCategory.RESEARCH}:
            if source is None or not source.name.strip() or source.published_at is None:
                raise ValueError("news and research bubbles require a source and publication date")
        bubble_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex
        return cls(
            id=bubble_id,
            category=current_category,
            summary=clean_summary[:1000],
            detail=str(detail).strip()[:12000],
            source=source,
            actions=tuple(dict.fromkeys(str(action) for action in actions if action)),
            scene_label=str(scene_label).strip()[:120],
            created_at=current,
            expires_at=current + timedelta(seconds=max(15, min(lifetime_seconds, 3600))),
            content_id=str(content_id),
            session_id=session_id,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return the stable QML/socket bubble contract."""

        return {
            "id": self.id,
            "category": self.category.value,
            "summary": self.summary,
            "detail": self.detail,
            "source": self.source.to_mapping() if self.source else None,
            "actions": list(self.actions),
            "sceneLabel": self.scene_label,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SessionMessage:
    role: str
    text: str
    created_at: datetime

    def to_mapping(self) -> dict[str, str]:
        return {
            "role": self.role,
            "text": self.text,
            "createdAt": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class BubbleSession:
    id: str
    bubble_id: str
    category: ContentCategory
    messages: list[SessionMessage] = field(default_factory=list)
    moved_to_box: bool = False

    def append(self, role: str, text: str, now: datetime) -> SessionMessage:
        current_role = str(role).casefold()
        if current_role not in {"user", "assistant"}:
            raise ValueError("session role must be user or assistant")
        clean = str(text).strip()
        if not clean:
            raise ValueError("session message cannot be empty")
        message = SessionMessage(current_role, clean[:12000], _aware_utc(now))
        self.messages.append(message)
        return message


class CompanionEngine:
    """Creates isolated bubbles and records replies through an injected sink."""

    def __init__(
        self,
        preferences: CompanionPreferences | None = None,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.preferences = preferences or CompanionPreferences()
        self.gate = EmissionGate(self.preferences.frequency)
        self.event_sink = event_sink or (lambda _event: None)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sessions: dict[str, BubbleSession] = {}
        self.bubbles: dict[str, SpeechBubble] = {}
        self._recent_content: deque[str] = deque(maxlen=40)
        self._recent_sources: deque[str] = deque(maxlen=20)

    def update_preferences(self, preferences: CompanionPreferences) -> None:
        self.preferences = preferences
        self.gate.update(preferences.frequency)

    def emit(
        self,
        *,
        category: ContentCategory | str,
        summary: str,
        detail: str = "",
        source: BubbleSource | None = None,
        scene_label: str = "",
        content_id: str = "",
        generation: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> SpeechBubble | None:
        current_category = category if isinstance(category, ContentCategory) else ContentCategory(category)
        current = _aware_utc(self.now())
        if not self.preferences.category_enabled(current_category):
            return None
        if content_id and content_id in self._recent_content and not force:
            return None
        allowed, _reason = self.gate.can_emit(current)
        if not allowed and not force:
            return None
        bubble = SpeechBubble.create(
            category=current_category,
            summary=summary,
            detail=detail,
            source=source,
            scene_label=scene_label,
            created_at=current,
            content_id=content_id,
        )
        self.bubbles[bubble.id] = bubble
        self.sessions[bubble.session_id] = BubbleSession(
            bubble.session_id, bubble.id, current_category
        )
        if not force:
            self.gate.record(current)
        if content_id:
            self._recent_content.append(content_id)
        if source and source.name:
            self._recent_sources.append(source.name.casefold())
        self.event_sink(
            {
                "type": "bubble-created",
                "bubble": bubble.to_mapping(),
                "sessionId": bubble.session_id,
                "generation": dict(generation)
                if isinstance(generation, Mapping)
                else {},
            }
        )
        return bubble

    def reply(self, bubble_id: str, text: str, answer: str = "") -> BubbleSession:
        bubble = self._bubble(bubble_id)
        session = self.sessions[bubble.session_id]
        now = _aware_utc(self.now())
        user_message = session.append("user", text, now)
        self.event_sink(
            {
                "type": "bubble-reply",
                "bubbleId": bubble.id,
                "sessionId": session.id,
                "message": user_message.to_mapping(),
                "memoryEligible": True,
            }
        )
        if str(answer).strip():
            assistant_message = session.append("assistant", answer, now)
            self.event_sink(
                {
                    "type": "bubble-answer",
                    "bubbleId": bubble.id,
                    "sessionId": session.id,
                    "message": assistant_message.to_mapping(),
                    "memoryEligible": True,
                }
            )
        return session

    def answer(self, bubble_id: str, text: str) -> BubbleSession:
        bubble = self._bubble(bubble_id)
        session = self.sessions[bubble.session_id]
        message = session.append("assistant", text, _aware_utc(self.now()))
        self.event_sink(
            {
                "type": "bubble-answer",
                "bubbleId": bubble.id,
                "sessionId": session.id,
                "message": message.to_mapping(),
                "memoryEligible": True,
            }
        )
        return session

    def move_to_box(self, bubble_id: str) -> dict[str, Any]:
        bubble = self._bubble(bubble_id)
        session = self.sessions[bubble.session_id]
        session.moved_to_box = True
        payload = {
            "conversationKind": "companion",
            "bubble": bubble.to_mapping(),
            "sessionId": session.id,
            "messages": [message.to_mapping() for message in session.messages],
        }
        self.event_sink({"type": "bubble-moved-to-box", **payload})
        return payload

    def snooze(self, minutes: int = 60) -> datetime:
        duration = _bounded_int(minutes, 1, 24 * 60, "snooze")
        until = _aware_utc(self.now()) + timedelta(minutes=duration)
        self.gate.snooze(until)
        self.event_sink({"type": "companion-snoozed", "until": until.isoformat()})
        return until

    def is_recent(self, content_id: str = "", source_name: str = "") -> bool:
        return bool(
            (content_id and content_id in self._recent_content)
            or (source_name and source_name.casefold() in self._recent_sources)
        )

    def _bubble(self, bubble_id: str) -> SpeechBubble:
        try:
            return self.bubbles[str(bubble_id)]
        except KeyError as exc:
            raise KeyError(f"unknown bubble: {bubble_id}") from exc


def rank_content(
    items: Iterable[Any],
    *,
    interests: Mapping[str, float] | None,
    scene_label: str,
    preferences: CompanionPreferences,
    recent_ids: Iterable[str] = (),
    recent_sources: Iterable[str] = (),
) -> list[tuple[float, Any]]:
    """Score metadata-only content using the configured interest/scene mix."""

    interest_mix, scene_mix = preferences.normalized_mix
    interest_map = {str(key).casefold(): max(0.0, float(value)) for key, value in (interests or {}).items()}
    scene_tokens = _tokens(scene_label)
    seen_ids = set(recent_ids)
    seen_sources = {str(source).casefold() for source in recent_sources}
    ranked: list[tuple[float, Any]] = []
    for item in items:
        item_id = str(getattr(item, "id", ""))
        # ``recent_ids`` is the strict exclusion window used by “换一个”.
        # Source repetition remains a soft penalty so a productive feed is not
        # silenced merely because several distinct items share a publisher.
        if item_id and item_id in seen_ids:
            continue
        category_value = getattr(item, "category", "")
        try:
            category = category_value if isinstance(category_value, ContentCategory) else ContentCategory(category_value)
        except ValueError:
            continue
        category_weight = preferences.category_weights[category] / 100.0
        if category_weight <= 0:
            continue
        title = str(getattr(item, "title", ""))
        summary = str(getattr(item, "summary", ""))
        topics = set(str(topic).casefold() for topic in getattr(item, "topics", ()) if topic)
        text_tokens = _tokens(f"{title} {summary} {' '.join(topics)}")
        interest_score = sum(
            weight for name, weight in interest_map.items() if name in topics or name in text_tokens
        )
        interest_score = 1.0 - math.exp(-interest_score) if interest_score > 0 else 0.0
        scene_score = len(scene_tokens & text_tokens) / max(1, len(scene_tokens))
        score = category_weight * (interest_mix * interest_score + scene_mix * scene_score)
        source = str(getattr(item, "source", "")).casefold()
        if source and source in seen_sources:
            score *= 0.55
        ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked


def _tokens(text: str) -> set[str]:
    lowered = str(text).casefold()
    latin = re.findall(r"[a-z0-9][a-z0-9.+-]{1,}", lowered)
    chinese = [lowered[index : index + 2] for index in range(max(0, len(lowered) - 1)) if "\u4e00" <= lowered[index] <= "\u9fff"]
    return set(latin + chinese)


def summary_signature(text: str) -> str:
    """Return a content-safe stable signature for recent bubble prose.

    The signature is deliberately based only on Lilies-generated prose, never
    on a captured window or its title.  Normalising punctuation makes cosmetic
    changes unable to bypass the repetition guard.
    """

    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(text).casefold())
    return normalized[:1_000]


def summaries_are_near_duplicates(left: str, right: str) -> bool:
    """Detect essentially repeated bubble summaries without fuzzy guessing.

    A character-sequence ratio catches small rewrites while trigram overlap
    catches reordered clauses.  Very short remarks require an exact match so
    ordinary phrases such as “我看见了” do not suppress unrelated bubbles.
    """

    first = summary_signature(left)
    second = summary_signature(right)
    if not first or not second:
        return False
    if first == second:
        return True
    if min(len(first), len(second)) < 12:
        return False
    ratio = SequenceMatcher(None, first, second, autojunk=False).ratio()
    first_grams = {first[index : index + 3] for index in range(len(first) - 2)}
    second_grams = {second[index : index + 3] for index in range(len(second) - 2)}
    overlap = len(first_grams & second_grams) / max(1, len(first_grams | second_grams))
    return ratio >= 0.80 or overlap >= 0.68


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
