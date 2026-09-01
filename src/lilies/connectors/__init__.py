"""Security-first, offline-testable external connector primitives.

The package intentionally ships without a concrete network transport.  A caller
must inject one, which keeps account access out of imports, tests and previews.
"""

from .assistance import (
    AssistanceMaterial,
    AssistanceMaterialExpiredError,
    AssistanceMaterialStore,
    AssistanceMaterialUnavailableError,
    AssistanceUnavailableError,
    DEFAULT_ASSISTANCE_TTL,
    MAX_ASSISTANCE_CONTENT_CHARS,
    MAX_ASSISTANCE_TTL,
)
from .calendar_reminders import CalendarReminderBridge
from .google_calendar import (
    CalendarSyncResult,
    GoogleCalendarConnector,
    LoopbackOAuthReceiver,
    PkceAuthorization,
    RollingSyncWindow,
    SyncCheckpoint,
)
from .http import HttpResponse, RateLimitError, TransportNotConfiguredError, UrllibHttpTransport
from .policy import (
    ActionMode,
    ActionProposal,
    ConnectorPolicy,
    IngressMode,
    ModelAccessMode,
    PolicyViolationError,
    ProposalStatus,
    RetentionMode,
)
from .schema import ensure_schema
from .security import (
    DPAPISecretBackend,
    EncryptedContentVault,
    InMemorySecretBackend,
    SecretStore,
    SecretStoreUnavailableError,
    WindowsCredentialManagerBackend,
)
from .runtime import (
    AssistanceMode,
    CalendarRuntime,
    ConnectorPolicyAxes,
    InterruptionMode,
    RetentionTier,
    ScopeMode,
    SlackRuntime,
)
from .slack import (
    EventDeduplicator,
    SlackConnector,
    SlackEventFilter,
    SlackManifestOptions,
    SlackPkceAuthorization,
    generate_manifest,
)

__all__ = [
    "ActionMode",
    "ActionProposal",
    "AssistanceMaterial",
    "AssistanceMaterialExpiredError",
    "AssistanceMaterialStore",
    "AssistanceMaterialUnavailableError",
    "AssistanceMode",
    "AssistanceUnavailableError",
    "CalendarRuntime",
    "CalendarReminderBridge",
    "CalendarSyncResult",
    "ConnectorPolicy",
    "ConnectorPolicyAxes",
    "DPAPISecretBackend",
    "DEFAULT_ASSISTANCE_TTL",
    "EncryptedContentVault",
    "EventDeduplicator",
    "GoogleCalendarConnector",
    "HttpResponse",
    "InMemorySecretBackend",
    "IngressMode",
    "InterruptionMode",
    "LoopbackOAuthReceiver",
    "MAX_ASSISTANCE_CONTENT_CHARS",
    "MAX_ASSISTANCE_TTL",
    "ModelAccessMode",
    "PkceAuthorization",
    "PolicyViolationError",
    "ProposalStatus",
    "RateLimitError",
    "RetentionMode",
    "RetentionTier",
    "RollingSyncWindow",
    "SecretStore",
    "SecretStoreUnavailableError",
    "SlackConnector",
    "SlackEventFilter",
    "SlackManifestOptions",
    "SlackPkceAuthorization",
    "SlackRuntime",
    "ScopeMode",
    "SyncCheckpoint",
    "TransportNotConfiguredError",
    "UrllibHttpTransport",
    "WindowsCredentialManagerBackend",
    "ensure_schema",
    "generate_manifest",
]
