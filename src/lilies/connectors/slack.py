"""Slack connector boundaries for user events, dedupe and confirmed sends.

Socket Mode app tokens (``xapp-``) are deliberately separate from OAuth user
tokens.  This module can request a WebSocket URL through an injected transport,
but never opens a socket, polls, or connects an account on its own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode

from .http import (
    ConnectorHttpError,
    HttpResponse,
    HttpTransport,
    raise_for_status,
    require_transport,
)
from .policy import ActionProposal, ConnectorPolicy, IngressMode
from .schema import DatabaseTarget, ensure_schema
from .security import SecretStore


SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
SLACK_API_URL = "https://slack.com/api"

USER_MESSAGE_SCOPES = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
)
USER_WRITE_SCOPES = ("chat:write",)
USER_MESSAGE_EVENTS = (
    "message.channels",
    "message.groups",
    "message.im",
    "message.mpim",
)


def _urlsafe_without_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _urlsafe_without_padding(secrets.token_bytes(48))
    challenge = _urlsafe_without_padding(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass(frozen=True)
class SlackManifestOptions:
    app_name: str = "Lilies in the box"
    socket_mode: bool = True
    request_url: Optional[str] = None
    redirect_urls: tuple[str, ...] = ()
    user_mode: bool = True
    include_write_scope: bool = True
    pkce_enabled: bool = True
    marketplace_distribution: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "redirect_urls", tuple(self.redirect_urls))
        if not self.app_name.strip():
            raise ValueError("Slack app name is required")
        if self.marketplace_distribution and self.socket_mode:
            raise ValueError("Slack Marketplace apps must use HTTP event delivery")
        if not self.socket_mode and not self.request_url:
            raise ValueError("HTTP event delivery requires request_url")


def generate_manifest(options: Optional[SlackManifestOptions] = None) -> dict[str, Any]:
    """Generate a Slack app manifest without embedding any token or secret."""

    options = options or SlackManifestOptions()
    if options.user_mode:
        scopes = list(USER_MESSAGE_SCOPES)
        if options.include_write_scope:
            scopes.extend(USER_WRITE_SCOPES)
        oauth_scopes: dict[str, list[str]] = {"user": scopes}
        subscriptions: dict[str, Any] = {"user_events": list(USER_MESSAGE_EVENTS)}
        features: dict[str, Any] = {}
    else:
        oauth_scopes = {
            "bot": [
                "app_mentions:read",
                "channels:history",
                "groups:history",
                "im:history",
                "mpim:history",
                "chat:write",
            ]
        }
        subscriptions = {
            "bot_events": [
                "app_mention",
                "message.channels",
                "message.groups",
                "message.im",
                "message.mpim",
            ]
        }
        features = {
            "bot_user": {"display_name": options.app_name, "always_online": False}
        }
    if not options.socket_mode:
        subscriptions["request_url"] = options.request_url
    settings: dict[str, Any] = {
        "event_subscriptions": subscriptions,
        "interactivity": {"is_enabled": False},
        "org_deploy_enabled": False,
        "socket_mode_enabled": options.socket_mode,
        "token_rotation_enabled": True,
    }
    manifest: dict[str, Any] = {
        "_metadata": {"major_version": 2},
        "display_information": {"name": options.app_name},
        "features": features,
        "oauth_config": {
            "scopes": oauth_scopes,
            "pkce_enabled": options.pkce_enabled,
        },
        "settings": settings,
    }
    if options.redirect_urls:
        manifest["oauth_config"]["redirect_urls"] = list(options.redirect_urls)
    return manifest


@dataclass(frozen=True)
class SlackPkceAuthorization:
    authorization_url: str
    redirect_uri: str
    state: str
    code_verifier: str
    code_challenge: str
    user_scopes: tuple[str, ...]


@dataclass(frozen=True)
class SlackEvent:
    event_id: str
    team_id: Optional[str]
    channel_id: str
    user_id: Optional[str]
    text: str
    timestamp: Optional[str]
    thread_timestamp: Optional[str]
    is_direct: bool
    is_mention: bool
    raw_event: Mapping[str, Any]


@dataclass(frozen=True)
class SlackEventFilter:
    """Allow DMs, human mentions, and explicitly selected channels only."""

    authorized_user_id: Optional[str] = None
    allowed_team_ids: frozenset[str] = field(default_factory=frozenset)
    selected_channel_ids: frozenset[str] = field(default_factory=frozenset)
    allow_all_channels: bool = False
    allow_direct_messages: bool = True
    allow_mentions: bool = True
    ignore_self_messages: bool = True
    ignore_bot_messages: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_team_ids", frozenset(self.allowed_team_ids))
        object.__setattr__(
            self, "selected_channel_ids", frozenset(self.selected_channel_ids)
        )

    @staticmethod
    def _unwrap(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        outer = payload
        inner = payload.get("payload")
        if isinstance(inner, Mapping):
            outer = inner
        event = outer.get("event")
        return outer, event if isinstance(event, Mapping) else {}

    @staticmethod
    def _team_id(outer: Mapping[str, Any]) -> Optional[str]:
        team = outer.get("team_id") or outer.get("team")
        if team:
            return str(team)
        authorizations = outer.get("authorizations")
        if isinstance(authorizations, list) and authorizations:
            first = authorizations[0]
            if isinstance(first, Mapping) and first.get("team_id"):
                return str(first["team_id"])
        return None

    def select(self, payload: Mapping[str, Any]) -> Optional[SlackEvent]:
        outer, event = self._unwrap(payload)
        event_type = event.get("type")
        # User Events are message.*. ``app_mention`` means the Slack app/bot was
        # mentioned and must never be confused with the signed-in human user.
        if event_type != "message":
            return None
        subtype = event.get("subtype")
        if subtype not in (None, "file_share", "thread_broadcast"):
            return None
        if self.ignore_bot_messages and (
            event.get("bot_id") or subtype == "bot_message" or event.get("bot_profile")
        ):
            return None
        team_id = self._team_id(outer)
        if self.allowed_team_ids and team_id not in self.allowed_team_ids:
            return None
        channel_id = event.get("channel")
        if not channel_id:
            return None
        channel_id = str(channel_id)
        user_id = str(event["user"]) if event.get("user") else None
        if (
            self.ignore_self_messages
            and self.authorized_user_id
            and user_id == self.authorized_user_id
        ):
            return None
        channel_type = str(event.get("channel_type", ""))
        is_direct = channel_type in ("im", "mpim") or channel_id.startswith("D")
        text = str(event.get("text", ""))
        mention_marker = (
            "<@%s>" % self.authorized_user_id if self.authorized_user_id else None
        )
        is_mention = bool(mention_marker and mention_marker in text)
        selected_channel = self.allow_all_channels or channel_id in self.selected_channel_ids
        allowed = (
            (is_direct and self.allow_direct_messages)
            or selected_channel
            or (is_mention and self.allow_mentions)
        )
        if not allowed:
            return None
        event_id = outer.get("event_id") or payload.get("envelope_id")
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return SlackEvent(
            event_id=str(event_id or content_hash),
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            text=text,
            timestamp=str(event["ts"]) if event.get("ts") else None,
            thread_timestamp=(
                str(event["thread_ts"]) if event.get("thread_ts") else None
            ),
            is_direct=is_direct,
            is_mention=is_mention,
            raw_event=dict(event),
        )


class EventDeduplicator:
    def __init__(
        self,
        *,
        connector_id: str = "slack",
        database: Optional[DatabaseTarget] = None,
        ttl_seconds: float = 24 * 60 * 60,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0 or max_entries <= 0:
            raise ValueError("Dedupe bounds must be positive")
        self.connector_id = connector_id
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._connection = ensure_schema(database) if database is not None else None
        self._memory: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    def accept(self, event_id: str, content_hash: str) -> bool:
        now = self.clock()
        cutoff = now - self.ttl_seconds
        with self._lock:
            if self._connection is None:
                self._memory = {
                    key: value for key, value in self._memory.items() if value[1] >= cutoff
                }
                if event_id in self._memory:
                    return False
                self._memory[event_id] = (content_hash, now)
                if len(self._memory) > self.max_entries:
                    oldest = sorted(self._memory, key=lambda key: self._memory[key][1])[
                        : len(self._memory) - self.max_entries
                    ]
                    for key in oldest:
                        self._memory.pop(key, None)
                return True
            connection = self._connection
            connection.execute(
                "DELETE FROM connector_event_dedupe WHERE connector_id=? AND seen_at<?",
                (self.connector_id, cutoff),
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO connector_event_dedupe(
                    connector_id, event_id, content_hash, seen_at
                ) VALUES (?, ?, ?, ?)
                """,
                (self.connector_id, event_id, content_hash, now),
            )
            connection.execute(
                """
                DELETE FROM connector_event_dedupe
                WHERE rowid IN (
                    SELECT rowid FROM connector_event_dedupe
                    WHERE connector_id=? ORDER BY seen_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (self.connector_id, self.max_entries),
            )
            connection.commit()
            return cursor.rowcount > 0


@dataclass(frozen=True)
class SlackActionExecutionResult:
    proposal: ActionProposal
    response: Mapping[str, Any]


class SlackApiError(ConnectorHttpError):
    def __init__(self, response: HttpResponse, error: str):
        super().__init__(response.status_code, "Slack API error: %s" % error, response)
        self.error = error


class SlackConnector:
    connector_id = "slack"

    def __init__(
        self,
        *,
        client_id: str,
        secret_store: SecretStore,
        event_filter: Optional[SlackEventFilter] = None,
        policy: Optional[ConnectorPolicy] = None,
        transport: Optional[HttpTransport] = None,
        workspace_id: str = "default",
        database: Optional[DatabaseTarget] = None,
    ) -> None:
        if not client_id:
            raise ValueError("Slack OAuth client_id is required")
        self.client_id = client_id
        self.secret_store = secret_store
        self.event_filter = event_filter or SlackEventFilter()
        self.policy = policy or ConnectorPolicy()
        self.transport = transport
        self.workspace_id = workspace_id
        self.deduplicator = EventDeduplicator(database=database)

    @staticmethod
    def generate_manifest(options: Optional[SlackManifestOptions] = None) -> dict[str, Any]:
        return generate_manifest(options)

    manifest = generate_manifest

    @staticmethod
    def create_authorization(
        *,
        client_id: str,
        redirect_uri: str,
        user_scopes: Iterable[str] = USER_MESSAGE_SCOPES + USER_WRITE_SCOPES,
        state: Optional[str] = None,
        bot_scopes: Iterable[str] = (),
    ) -> SlackPkceAuthorization:
        """Create a native PKCE URL containing user scopes only.

        Slack's native PKCE flow does not support requesting bot scopes.  Socket
        Mode's xapp token is separately created in app settings by the app owner.
        """

        if tuple(bot_scopes):
            raise ValueError("Slack PKCE native apps cannot request bot scopes")
        if not redirect_uri or "#" in redirect_uri:
            raise ValueError("A registered redirect URI without a fragment is required")
        scopes = tuple(dict.fromkeys(user_scopes))
        if not scopes:
            raise ValueError("At least one Slack user scope is required")
        verifier, challenge = _pkce_pair()
        state_value = state or secrets.token_urlsafe(32)
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "user_scope": ",".join(scopes),
                "state": state_value,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return SlackPkceAuthorization(
            authorization_url="%s?%s" % (SLACK_AUTHORIZE_URL, query),
            redirect_uri=redirect_uri,
            state=state_value,
            code_verifier=verifier,
            code_challenge=challenge,
            user_scopes=scopes,
        )

    build_authorization_url = create_authorization

    def _oauth_token_key(self) -> str:
        return "slack/%s/oauth-token" % self.workspace_id

    def _app_token_key(self) -> str:
        return "slack/%s/socket-app-token" % self.workspace_id

    def _signing_secret_key(self) -> str:
        return "slack/%s/http-signing-secret" % self.workspace_id

    def save_oauth_tokens(self, payload: Mapping[str, Any]) -> None:
        authed_user = payload.get("authed_user")
        user_token = authed_user.get("access_token") if isinstance(authed_user, Mapping) else None
        if not user_token and not payload.get("access_token"):
            raise ValueError("Slack token response contains no access token")
        value = dict(payload)
        existing = self.load_oauth_tokens()
        current_user = dict(value.get("authed_user") or {})
        existing_user = dict((existing or {}).get("authed_user") or {})
        if not current_user.get("refresh_token") and existing_user.get("refresh_token"):
            current_user["refresh_token"] = existing_user["refresh_token"]
        if current_user:
            value["authed_user"] = current_user
        if not value.get("refresh_token") and (existing or {}).get("refresh_token"):
            value["refresh_token"] = existing["refresh_token"]
        value.setdefault("obtained_at", time.time())
        self.secret_store.set_text(
            self._oauth_token_key(),
            json.dumps(value, separators=(",", ":"), sort_keys=True),
        )

    save_tokens = save_oauth_tokens

    def load_oauth_tokens(self) -> Optional[dict[str, Any]]:
        raw = self.secret_store.get_text(self._oauth_token_key())
        return None if raw is None else json.loads(raw)

    load_tokens = load_oauth_tokens

    def save_app_token(self, app_token: str) -> None:
        if not app_token.startswith("xapp-"):
            raise ValueError("Socket Mode requires a separately issued xapp token")
        self.secret_store.set_text(self._app_token_key(), app_token)

    def save_signing_secret(self, signing_secret: str) -> None:
        if not signing_secret.strip():
            raise ValueError("Slack signing secret must not be empty")
        self.secret_store.set_text(self._signing_secret_key(), signing_secret)

    def verify_http_signature(
        self,
        *,
        timestamp: str,
        raw_body: bytes,
        signature: str,
        now: Optional[float] = None,
        tolerance_seconds: float = 300.0,
    ) -> bool:
        """Verify an HTTP Events request before parsing its private message body."""

        secret = self.secret_store.get_text(self._signing_secret_key())
        if not secret:
            raise PermissionError("No Slack HTTP signing secret has been stored")
        try:
            request_time = int(timestamp)
        except (TypeError, ValueError):
            return False
        if abs((time.time() if now is None else now) - request_time) > tolerance_seconds:
            return False
        base = b"v0:" + str(timestamp).encode("ascii") + b":" + bytes(raw_body)
        expected = "v0=" + hmac.new(
            secret.encode("utf-8"), base, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def disconnect(
        self,
        *,
        remove_app_token: bool = False,
        remove_signing_secret: bool = False,
    ) -> None:
        self.secret_store.delete(self._oauth_token_key())
        if remove_app_token:
            self.secret_store.delete(self._app_token_key())
        if remove_signing_secret:
            self.secret_store.delete(self._signing_secret_key())

    def exchange_code(
        self, *, code: str, authorization: SlackPkceAuthorization
    ) -> dict[str, Any]:
        transport = require_transport(self.transport)
        response = transport.request(
            "POST",
            SLACK_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "code": code,
                "code_verifier": authorization.code_verifier,
                "redirect_uri": authorization.redirect_uri,
            },
        )
        raise_for_status(response, expected=(200,))
        payload = response.json()
        if not payload.get("ok", True):
            raise SlackApiError(response, str(payload.get("error", "oauth_failed")))
        self.save_oauth_tokens(payload)
        return payload

    def _access_token(self) -> str:
        payload = self.load_oauth_tokens()
        if not payload:
            raise PermissionError("Slack workspace is not authorized")
        expires_in = float(payload.get("expires_in", 0) or 0)
        obtained_at = float(payload.get("obtained_at", 0) or 0)
        if expires_in and obtained_at and time.time() >= obtained_at + expires_in - 60:
            authed_user = payload.get("authed_user")
            refresh_token = (
                str(authed_user.get("refresh_token", ""))
                if isinstance(authed_user, Mapping)
                else ""
            ) or str(payload.get("refresh_token", ""))
            if not refresh_token:
                raise PermissionError("Slack authorization expired; reconnect is required")
            transport = require_transport(self.transport)
            response = transport.request(
                "POST",
                SLACK_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            raise_for_status(response, expected=(200,))
            refreshed = dict(response.json())
            if not refreshed.get("ok", True):
                raise SlackApiError(response, str(refreshed.get("error", "token_refresh_failed")))
            refreshed_user = dict(refreshed.get("authed_user") or {})
            if not refreshed_user.get("refresh_token"):
                refreshed_user["refresh_token"] = refresh_token
            refreshed["authed_user"] = refreshed_user
            refreshed["obtained_at"] = time.time()
            self.save_oauth_tokens(refreshed)
            payload = refreshed
        authed_user = payload.get("authed_user")
        if isinstance(authed_user, Mapping) and authed_user.get("access_token"):
            return str(authed_user["access_token"])
        if payload.get("access_token"):
            return str(payload["access_token"])
        raise PermissionError("Slack workspace token is unavailable")

    @staticmethod
    def socket_ack(envelope: Mapping[str, Any]) -> Mapping[str, str]:
        envelope_id = envelope.get("envelope_id")
        if not envelope_id:
            raise ValueError("Socket Mode envelope has no envelope_id")
        return {"envelope_id": str(envelope_id)}

    def request_socket_url(self) -> str:
        """Request one Socket Mode URL; the caller owns socket lifecycle and acks."""

        app_token = self.secret_store.get_text(self._app_token_key())
        if not app_token:
            raise PermissionError("No Socket Mode xapp token has been explicitly stored")
        transport = require_transport(self.transport)
        response = transport.request(
            "POST",
            "%s/apps.connections.open" % SLACK_API_URL,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer %s" % app_token,
            },
        )
        raise_for_status(response, expected=(200,))
        payload = response.json()
        if not payload.get("ok") or not payload.get("url"):
            raise SlackApiError(response, str(payload.get("error", "socket_url_missing")))
        return str(payload["url"])

    def ingest_event(self, payload: Mapping[str, Any]) -> Optional[SlackEvent]:
        self.policy.require_ingress()
        event = self.event_filter.select(payload)
        if event is None:
            return None
        canonical = json.dumps(
            event.raw_event, sort_keys=True, separators=(",", ":"), default=str
        )
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not self.deduplicator.accept(event.event_id, content_hash):
            return None
        if self.policy.ingress is IngressMode.METADATA_ONLY:
            return SlackEvent(
                event_id=event.event_id,
                team_id=event.team_id,
                channel_id=event.channel_id,
                user_id=event.user_id,
                text="",
                timestamp=event.timestamp,
                thread_timestamp=event.thread_timestamp,
                is_direct=event.is_direct,
                is_mention=event.is_mention,
                raw_event={},
            )
        return event

    def propose_message(
        self,
        *,
        channel_id: str,
        text: str,
        thread_timestamp: Optional[str] = None,
    ) -> ActionProposal:
        self.policy.require_proposal()
        if not channel_id or not text.strip():
            raise ValueError("Slack channel and non-empty message are required")
        payload: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_timestamp:
            payload["thread_ts"] = thread_timestamp
        return ActionProposal.create(
            connector_id=self.connector_id,
            action="send_message",
            target=channel_id,
            summary="Send Slack message to %s" % channel_id,
            payload=payload,
        )

    def send_confirmed(self, proposal: ActionProposal) -> SlackActionExecutionResult:
        if proposal.connector_id != self.connector_id or proposal.action != "send_message":
            raise ValueError("Proposal is not a Slack send action")
        self.policy.require_execution(proposal)
        transport = require_transport(self.transport)
        body = proposal.mutable_payload()
        body["client_msg_id"] = proposal.proposal_id
        response = transport.request(
            "POST",
            "%s/chat.postMessage" % SLACK_API_URL,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer %s" % self._access_token(),
                "Content-Type": "application/json; charset=utf-8",
            },
            json_body=body,
        )
        raise_for_status(response, expected=(200,))
        payload = response.json()
        if not payload.get("ok"):
            raise SlackApiError(response, str(payload.get("error", "unknown_error")))
        return SlackActionExecutionResult(proposal.mark_executed(), payload)

    execute = send_confirmed
