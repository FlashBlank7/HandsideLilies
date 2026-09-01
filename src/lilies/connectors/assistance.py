from __future__ import annotations

"""Short-lived, one-item handles for isolated connector assistance.

The store is deliberately process-local.  It is not a model runner, does not
perform I/O, and never persists the plaintext copied out of the encrypted
connector vault.  An :class:`AssistanceMaterial` is an opaque capability: its
payload can be retrieved once, before its deadline, and the capability itself
does not expose that payload.
"""

import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


MAX_ASSISTANCE_CONTENT_CHARS = 6_000
DEFAULT_ASSISTANCE_TTL = timedelta(minutes=5)
MAX_ASSISTANCE_TTL = timedelta(minutes=10)

_PAYLOAD_KEYS = (
    "provider",
    "remoteId",
    "sourceId",
    "occurredAt",
    "content",
    "untrusted",
)


class AssistanceUnavailableError(PermissionError):
    """The current connector policy or retained data cannot permit assistance."""


class AssistanceMaterialUnavailableError(RuntimeError):
    """An assistance material is invalid or has already been consumed."""


class AssistanceMaterialExpiredError(AssistanceMaterialUnavailableError):
    """An assistance material reached its short deadline before consumption."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def bounded_external_content(
    value: Mapping[str, Any],
    *,
    provider: str,
    limit: int = MAX_ASSISTANCE_CONTENT_CHARS,
) -> str:
    """Project retained content into one bounded, text-only assistance body.

    Only known body/summary fields are admitted.  This prevents connector
    metadata, nested API responses, credentials, or unrelated retained fields
    from being serialized into an assistance payload.
    """

    if not 1 <= int(limit) <= MAX_ASSISTANCE_CONTENT_CHARS:
        raise ValueError("assistance content limit is out of range")
    names = (
        ("text", "summary")
        if str(provider) == "slack"
        else ("summary", "description")
    )
    parts: list[str] = []
    for name in names:
        part = value.get(name)
        if not isinstance(part, str) or not part:
            continue
        if part not in parts:
            parts.append(part)
    return "\n".join(parts)[: int(limit)]


@dataclass(frozen=True, slots=True)
class _MaterialRecord:
    payload: Mapping[str, Any]
    expires_at: datetime


class AssistanceMaterial:
    """Opaque single-use handle returned after an explicit item selection."""

    __slots__ = ("_store", "_token", "_expires_at")

    def __init__(
        self,
        store: "AssistanceMaterialStore",
        token: str,
        expires_at: datetime,
    ) -> None:
        self._store = store
        self._token = token
        self._expires_at = expires_at

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    def consume(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Return the isolated payload once, or fail when used/expired."""

        return self._store.consume(self, now=now)

    def __repr__(self) -> str:
        # Neither the capability token nor its plaintext belongs in diagnostics.
        return "AssistanceMaterial(expires_at=%r)" % self._expires_at.isoformat()


class AssistanceMaterialStore:
    """Thread-safe in-memory issuer for short-lived assistance materials."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        default_ttl: timedelta = DEFAULT_ASSISTANCE_TTL,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._default_ttl = self._validated_ttl(default_ttl)
        self._records: dict[str, _MaterialRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validated_ttl(value: timedelta) -> timedelta:
        if not isinstance(value, timedelta):
            raise TypeError("assistance material TTL must be a timedelta")
        if value <= timedelta(0) or value > MAX_ASSISTANCE_TTL:
            raise ValueError("assistance material TTL must be positive and at most 10 minutes")
        return value

    def _now(self, override: datetime | None = None) -> datetime:
        value = override if override is not None else self._clock()
        if not isinstance(value, datetime):
            raise TypeError("assistance clock must return a datetime")
        return _as_utc(value)

    @staticmethod
    def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
        content = str(value.get("content", ""))[:MAX_ASSISTANCE_CONTENT_CHARS]
        if not content:
            raise AssistanceUnavailableError("selected connector item has no retained content")
        return {
            "provider": str(value.get("provider", ""))[:80],
            "remoteId": str(value.get("remoteId", ""))[:2_048],
            "sourceId": str(value.get("sourceId", ""))[:2_048],
            "occurredAt": str(value.get("occurredAt", ""))[:160],
            "content": content,
            "untrusted": True,
        }

    def issue(
        self,
        payload: Mapping[str, Any],
        *,
        ttl: timedelta | None = None,
        now: datetime | None = None,
    ) -> AssistanceMaterial:
        """Issue an opaque capability for an already isolated payload."""

        lifetime = self._default_ttl if ttl is None else self._validated_ttl(ttl)
        issued_at = self._now(now)
        expires_at = issued_at + lifetime
        record = _MaterialRecord(self._payload(payload), expires_at)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked(issued_at)
            self._records[token] = record
        return AssistanceMaterial(self, token, expires_at)

    def consume(
        self,
        material: AssistanceMaterial,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically consume one material and return an exact safe projection."""

        if not isinstance(material, AssistanceMaterial) or material._store is not self:
            raise AssistanceMaterialUnavailableError("assistance material is not valid here")
        consumed_at = self._now(now)
        with self._lock:
            record = self._records.pop(material._token, None)
        if record is None:
            raise AssistanceMaterialUnavailableError(
                "assistance material is invalid or has already been consumed"
            )
        if consumed_at >= record.expires_at:
            raise AssistanceMaterialExpiredError("assistance material has expired")
        # The stored mapping is never returned by reference.
        return {name: record.payload[name] for name in _PAYLOAD_KEYS}

    def _purge_locked(self, now: datetime) -> int:
        expired = [
            token for token, record in self._records.items() if now >= record.expires_at
        ]
        for token in expired:
            self._records.pop(token, None)
        return len(expired)

    def purge_expired(self, *, now: datetime | None = None) -> int:
        with self._lock:
            return self._purge_locked(self._now(now))

    def invalidate(self, *, provider: str = "", remote_id: str = "") -> int:
        """Invalidate matching outstanding material without revealing its body."""

        provider = str(provider)
        remote_id = str(remote_id)
        with self._lock:
            tokens = [
                token
                for token, record in self._records.items()
                if (not provider or record.payload["provider"] == provider)
                and (not remote_id or record.payload["remoteId"] == remote_id)
            ]
            for token in tokens:
                self._records.pop(token, None)
        return len(tokens)

    def invalidate_all(self) -> int:
        with self._lock:
            count = len(self._records)
            self._records.clear()
        return count


__all__ = [
    "AssistanceMaterial",
    "AssistanceMaterialExpiredError",
    "AssistanceMaterialStore",
    "AssistanceMaterialUnavailableError",
    "AssistanceUnavailableError",
    "DEFAULT_ASSISTANCE_TTL",
    "MAX_ASSISTANCE_CONTENT_CHARS",
    "MAX_ASSISTANCE_TTL",
    "bounded_external_content",
]
