"""Four independent consent axes and immutable action proposals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional
from uuid import uuid4


class IngressMode(str, Enum):
    DISABLED = "disabled"
    METADATA_ONLY = "metadata_only"
    SELECTED_CONTENT = "selected_content"


class RetentionMode(str, Enum):
    NONE = "none"
    ENCRYPTED_SESSION = "encrypted_session"
    ENCRYPTED_PERSISTENT = "encrypted_persistent"


class ModelAccessMode(str, Enum):
    DENY = "deny"
    LOCAL_ONLY = "local_only"
    EXPLICIT_REMOTE = "explicit_remote"


class ActionMode(str, Enum):
    DISABLED = "disabled"
    PROPOSE_ONLY = "propose_only"
    REQUIRE_CONFIRMATION = "require_confirmation"


class ProposalStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class PolicyViolationError(PermissionError):
    pass


class ProposalStateError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True)
class ConnectorPolicy:
    """Consent is not a single switch: each capability is evaluated alone."""

    ingress: IngressMode = IngressMode.DISABLED
    retention: RetentionMode = RetentionMode.NONE
    model_access: ModelAccessMode = ModelAccessMode.DENY
    actions: ActionMode = ActionMode.PROPOSE_ONLY

    def allows_content(self, *, explicitly_selected: bool = False) -> bool:
        return self.ingress is IngressMode.SELECTED_CONTENT and explicitly_selected

    def allows_metadata(self) -> bool:
        return self.ingress is not IngressMode.DISABLED

    def may_persist(self) -> bool:
        return self.retention is RetentionMode.ENCRYPTED_PERSISTENT

    def allows_model(self, *, remote: bool, explicit_consent: bool = False) -> bool:
        if self.model_access is ModelAccessMode.DENY:
            return False
        if not remote:
            return True
        return (
            self.model_access is ModelAccessMode.EXPLICIT_REMOTE and explicit_consent
        )

    def can_propose(self) -> bool:
        return self.actions is not ActionMode.DISABLED

    def can_execute(self, proposal: "ActionProposal") -> bool:
        return (
            self.actions is ActionMode.REQUIRE_CONFIRMATION
            and proposal.status is ProposalStatus.CONFIRMED
            and not proposal.is_expired()
        )

    def require_ingress(self) -> None:
        if not self.allows_metadata():
            raise PolicyViolationError("Connector ingress is disabled")

    def require_proposal(self) -> None:
        if not self.can_propose():
            raise PolicyViolationError("Connector actions are disabled")

    def require_execution(self, proposal: "ActionProposal") -> None:
        if not self.can_execute(proposal):
            raise PolicyViolationError(
                "Execution requires the require-confirmation policy and a confirmed proposal"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "ingress": self.ingress.value,
            "retention": self.retention.value,
            "model_access": self.model_access.value,
            "actions": self.actions.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, str]) -> "ConnectorPolicy":
        return cls(
            ingress=IngressMode(value.get("ingress", IngressMode.DISABLED.value)),
            retention=RetentionMode(value.get("retention", RetentionMode.NONE.value)),
            model_access=ModelAccessMode(
                value.get("model_access", ModelAccessMode.DENY.value)
            ),
            actions=ActionMode(value.get("actions", ActionMode.PROPOSE_ONLY.value)),
        )


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    connector_id: str
    action: str
    target: str
    summary: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    source_etag: Optional[str] = None
    status: ProposalStatus = ProposalStatus.PENDING
    requires_confirmation: bool = True
    created_at: datetime = field(default_factory=utc_now)
    expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        # Freeze a defensive deep copy so UI preview and execution see identical data.
        object.__setattr__(self, "payload", _freeze(dict(self.payload)))
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    @classmethod
    def create(
        cls,
        *,
        connector_id: str,
        action: str,
        target: str,
        summary: str,
        payload: Optional[Mapping[str, Any]] = None,
        source_etag: Optional[str] = None,
        ttl: Optional[timedelta] = timedelta(minutes=15),
    ) -> "ActionProposal":
        created_at = utc_now()
        return cls(
            proposal_id=str(uuid4()),
            connector_id=connector_id,
            action=action,
            target=target,
            summary=summary,
            payload=payload or {},
            source_etag=source_etag,
            created_at=created_at,
            expires_at=created_at + ttl if ttl is not None else None,
        )

    def is_expired(self, *, at: Optional[datetime] = None) -> bool:
        return self.expires_at is not None and (at or utc_now()) >= self.expires_at

    def mutable_payload(self) -> dict[str, Any]:
        """Return an execution copy without exposing the immutable preview."""

        return _thaw(self.payload)

    def confirm(self, *, at: Optional[datetime] = None) -> "ActionProposal":
        moment = at or utc_now()
        if self.status is not ProposalStatus.PENDING:
            raise ProposalStateError("Only pending proposals can be confirmed")
        if self.is_expired(at=moment):
            raise ProposalStateError("Expired proposals cannot be confirmed")
        return replace(self, status=ProposalStatus.CONFIRMED, resolved_at=moment)

    def reject(self, *, at: Optional[datetime] = None) -> "ActionProposal":
        if self.status is not ProposalStatus.PENDING:
            raise ProposalStateError("Only pending proposals can be rejected")
        return replace(self, status=ProposalStatus.REJECTED, resolved_at=at or utc_now())

    def expire(self, *, at: Optional[datetime] = None) -> "ActionProposal":
        if self.status not in (ProposalStatus.PENDING, ProposalStatus.CONFIRMED):
            raise ProposalStateError("Resolved proposals cannot expire")
        return replace(self, status=ProposalStatus.EXPIRED, resolved_at=at or utc_now())

    def mark_executed(self, *, at: Optional[datetime] = None) -> "ActionProposal":
        if self.status is not ProposalStatus.CONFIRMED:
            raise ProposalStateError("Only confirmed proposals can be executed")
        if self.is_expired(at=at):
            raise ProposalStateError("Expired proposals cannot be executed")
        return replace(self, status=ProposalStatus.EXECUTED, resolved_at=at or utc_now())
