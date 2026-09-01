"""Google Calendar connector with PKCE, bounded sync and confirmed writes."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

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


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DEFAULT_CALENDAR_SCOPES = (CALENDAR_READ_SCOPE,)


def _urlsafe_without_padding(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _urlsafe_without_padding(secrets.token_bytes(48))
    challenge = _urlsafe_without_padding(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


@dataclass(frozen=True)
class PkceAuthorization:
    authorization_url: str
    redirect_uri: str
    state: str
    code_verifier: str
    code_challenge: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class OAuthCallback:
    code: Optional[str]
    state: Optional[str]
    error: Optional[str] = None


class LoopbackOAuthReceiver:
    """One-shot OAuth callback server bound strictly to IPv4 loopback."""

    def __init__(
        self,
        *,
        expected_state: str,
        port: int = 0,
        callback_path: str = "/oauth/callback",
    ) -> None:
        if not expected_state:
            raise ValueError("expected_state is required")
        if not callback_path.startswith("/"):
            raise ValueError("callback_path must be absolute")
        self.expected_state = expected_state
        self.callback_path = callback_path
        self._event = threading.Event()
        self._callback: Optional[OAuthCallback] = None
        self._closed = False
        receiver = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *args: Any) -> None:
                # OAuth codes and state must never reach an HTTP access log.
                return

            def _write(self, status: int, message: str) -> None:
                body = (
                    "<!doctype html><meta charset=utf-8><title>Lilies</title>"
                    "<p>%s</p>" % message
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                if parsed.path != receiver.callback_path:
                    self._write(404, "Callback path not found.")
                    return
                query = parse_qs(parsed.query, keep_blank_values=True)
                state = query.get("state", [None])[0]
                code = query.get("code", [None])[0]
                error = query.get("error", [None])[0]
                if not secrets.compare_digest(state or "", receiver.expected_state):
                    receiver._callback = OAuthCallback(
                        code=None, state=state, error="state_mismatch"
                    )
                    receiver._event.set()
                    self._write(400, "Authorization state did not match.")
                    return
                if error or not code:
                    receiver._callback = OAuthCallback(
                        code=None, state=state, error=error or "missing_code"
                    )
                    receiver._event.set()
                    self._write(400, "Authorization was not completed.")
                    return
                receiver._callback = OAuthCallback(code=code, state=state)
                receiver._event.set()
                self._write(200, "Authorization received. You may close this window.")

        self._server = ThreadingHTTPServer(("127.0.0.1", port), CallbackHandler)
        self._thread: Optional[threading.Thread] = None

    @property
    def redirect_uri(self) -> str:
        return "http://127.0.0.1:%d%s" % (
            self._server.server_address[1],
            self.callback_path,
        )

    def start(self) -> "LoopbackOAuthReceiver":
        if self._closed:
            raise RuntimeError("Loopback receiver is closed")
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="lilies-oauth-loopback",
                daemon=True,
            )
            self._thread.start()
        return self

    def wait(self, timeout: float = 180.0) -> OAuthCallback:
        if not self._event.wait(timeout):
            raise TimeoutError("Timed out waiting for the OAuth callback")
        assert self._callback is not None
        return self._callback

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2.0)
        self._server.server_close()

    def __enter__(self) -> "LoopbackOAuthReceiver":
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.close()


@dataclass(frozen=True)
class RollingSyncWindow:
    past: timedelta = timedelta(days=30)
    future: timedelta = timedelta(days=365)

    def bounds(self, *, now: Optional[datetime] = None) -> tuple[str, str]:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("Sync window time must be timezone-aware")
        if self.past < timedelta(0) or self.future < timedelta(0):
            raise ValueError("Sync window durations must be non-negative")
        return _rfc3339(moment - self.past), _rfc3339(moment + self.future)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SyncCheckpoint:
    sync_token: Optional[str] = None
    etag: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class CalendarSyncResult:
    events: tuple[Mapping[str, Any], ...]
    checkpoint: Optional[SyncCheckpoint]
    not_modified: bool = False
    reset_required: bool = False


@dataclass(frozen=True)
class ActionExecutionResult:
    proposal: ActionProposal
    response: HttpResponse


class EtagConflictError(ConnectorHttpError):
    def __init__(self, response: HttpResponse):
        super().__init__(412, "Calendar item changed since this proposal was created", response)


class GoogleCalendarConnector:
    connector_id = "google-calendar"

    def __init__(
        self,
        *,
        client_id: str,
        secret_store: SecretStore,
        policy: Optional[ConnectorPolicy] = None,
        transport: Optional[HttpTransport] = None,
        account_id: str = "default",
        database: Optional[DatabaseTarget] = None,
    ) -> None:
        if not client_id:
            raise ValueError("Google OAuth client_id is required")
        self.client_id = client_id
        self.secret_store = secret_store
        self.policy = policy or ConnectorPolicy()
        self.transport = transport
        self.account_id = account_id
        self._connection = ensure_schema(database) if database is not None else None

    @staticmethod
    def create_authorization(
        *,
        client_id: str,
        redirect_uri: str,
        scopes: Iterable[str] = DEFAULT_CALENDAR_SCOPES,
        state: Optional[str] = None,
    ) -> PkceAuthorization:
        if not redirect_uri.startswith("http://127.0.0.1:"):
            raise ValueError("Desktop OAuth redirect must use an IPv4 loopback URI")
        verifier, challenge = _pkce_pair()
        state_value = state or secrets.token_urlsafe(32)
        scopes_tuple = tuple(dict.fromkeys(scopes))
        if not scopes_tuple:
            raise ValueError("At least one OAuth scope is required")
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes_tuple),
                "state": state_value,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "include_granted_scopes": "true",
            }
        )
        return PkceAuthorization(
            authorization_url="%s?%s" % (GOOGLE_AUTHORIZATION_URL, query),
            redirect_uri=redirect_uri,
            state=state_value,
            code_verifier=verifier,
            code_challenge=challenge,
            scopes=scopes_tuple,
        )

    build_authorization_url = create_authorization

    def _token_key(self) -> str:
        return "google-calendar/%s/oauth-token" % self.account_id

    def save_tokens(self, token_payload: Mapping[str, Any]) -> None:
        if not token_payload.get("access_token"):
            raise ValueError("Token response does not contain access_token")
        payload = dict(token_payload)
        existing = self.load_tokens()
        if existing and not payload.get("refresh_token") and existing.get("refresh_token"):
            payload["refresh_token"] = existing["refresh_token"]
        payload.setdefault("obtained_at", datetime.now(timezone.utc).timestamp())
        self.secret_store.set_text(
            self._token_key(),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )

    def load_tokens(self) -> Optional[dict[str, Any]]:
        raw = self.secret_store.get_text(self._token_key())
        return None if raw is None else json.loads(raw)

    def disconnect(self) -> None:
        self.secret_store.delete(self._token_key())

    def exchange_code(self, *, code: str, authorization: PkceAuthorization) -> dict[str, Any]:
        transport = require_transport(self.transport)
        response = transport.request(
            "POST",
            GOOGLE_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "code": code,
                "code_verifier": authorization.code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": authorization.redirect_uri,
            },
        )
        if response.status_code == 400:
            payload = response.json()
            error = str(payload.get("error", "oauth_failed"))
            if error == "invalid_grant":
                raise PermissionError(
                    "Google authorization is expired or revoked (invalid_grant); reconnect is required"
                )
        raise_for_status(response, expected=(200,))
        payload = response.json()
        self.save_tokens(payload)
        return payload

    def _access_token(self) -> str:
        tokens = self.load_tokens()
        if not tokens or not tokens.get("access_token"):
            raise PermissionError("Google Calendar account is not authorized")
        expires_in = float(tokens.get("expires_in", 0) or 0)
        obtained_at = float(tokens.get("obtained_at", 0) or 0)
        expires_at = float(tokens.get("expires_at", 0) or 0)
        expired = bool(expires_at and time.time() >= expires_at - 60)
        expired = expired or bool(expires_in and obtained_at and time.time() >= obtained_at + expires_in - 60)
        if expired:
            refresh_token = str(tokens.get("refresh_token", ""))
            if not refresh_token:
                raise PermissionError("Google Calendar authorization expired; reconnect is required")
            transport = require_transport(self.transport)
            response = transport.request(
                "POST",
                GOOGLE_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            if response.status_code == 400:
                body = response.json()
                if str(body.get("error", "")) == "invalid_grant":
                    raise PermissionError(
                        "Google authorization is expired or revoked (invalid_grant); reconnect is required"
                    )
            raise_for_status(response, expected=(200,))
            refreshed = dict(response.json())
            refreshed["refresh_token"] = refresh_token
            refreshed["obtained_at"] = datetime.now(timezone.utc).timestamp()
            self.save_tokens(refreshed)
            tokens = refreshed
        return str(tokens["access_token"])

    def _calendar_url(self, calendar_id: str, suffix: str = "events") -> str:
        return "%s/calendars/%s/%s" % (
            GOOGLE_CALENDAR_API,
            quote(calendar_id, safe=""),
            suffix,
        )

    def list_calendars(self) -> tuple[Mapping[str, Any], ...]:
        """Read bounded CalendarList metadata; event content is never included."""

        transport = require_transport(self.transport)
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % self._access_token(),
        }
        url = "%s/users/me/calendarList" % GOOGLE_CALENDAR_API
        params: dict[str, Any] = {"maxResults": "250"}
        result: list[Mapping[str, Any]] = []
        while True:
            response = transport.request("GET", url, headers=headers, params=params)
            raise_for_status(response, expected=(200,))
            payload = response.json()
            for item in payload.get("items", []):
                if not isinstance(item, Mapping) or not item.get("id"):
                    continue
                result.append(
                    {
                        key: item[key]
                        for key in ("id", "summary", "primary", "accessRole", "timeZone")
                        if key in item
                    }
                )
            page = payload.get("nextPageToken")
            if not page:
                break
            params = dict(params)
            params["pageToken"] = page
        return tuple(result)

    @staticmethod
    def _header(response: HttpResponse, name: str) -> Optional[str]:
        lower = name.lower()
        for key, value in response.headers.items():
            if key.lower() == lower:
                return value
        return None

    def sync(
        self,
        *,
        calendar_id: str = "primary",
        checkpoint: Optional[SyncCheckpoint] = None,
        window: Optional[RollingSyncWindow] = None,
        selected_event_ids: Iterable[str] = (),
        now: Optional[datetime] = None,
    ) -> CalendarSyncResult:
        self.policy.require_ingress()
        transport = require_transport(self.transport)
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % self._access_token(),
        }
        if checkpoint is not None and checkpoint.etag:
            headers["If-None-Match"] = checkpoint.etag
        params: dict[str, Any] = {
            "maxResults": "2500",
            "showDeleted": "true",
            "singleEvents": "true",
        }
        if checkpoint is not None and checkpoint.sync_token:
            params["syncToken"] = checkpoint.sync_token
        else:
            time_min, time_max = (window or RollingSyncWindow()).bounds(now=now)
            params["timeMin"] = time_min
            params["timeMax"] = time_max

        selected = set(selected_event_ids)
        selected_all = "*" in selected
        events: list[Mapping[str, Any]] = []
        next_sync_token: Optional[str] = None
        etag = checkpoint.etag if checkpoint else None
        while True:
            response = transport.request(
                "GET", self._calendar_url(calendar_id), headers=headers, params=params
            )
            if response.status_code == 304:
                return CalendarSyncResult((), checkpoint, not_modified=True)
            if response.status_code == 410:
                return CalendarSyncResult((), None, reset_required=True)
            raise_for_status(response, expected=(200,))
            payload = response.json()
            etag = self._header(response, "ETag") or payload.get("etag") or etag
            for item in payload.get("items", []):
                events.append(
                    self._project_event(item, selected_all or item.get("id") in selected)
                )
            page_token = payload.get("nextPageToken")
            next_sync_token = payload.get("nextSyncToken") or next_sync_token
            if not page_token:
                break
            params = dict(params)
            params["pageToken"] = page_token
            # An ETag validator is meaningful for the collection's first page only.
            headers = {key: value for key, value in headers.items() if key != "If-None-Match"}

        updated_at = datetime.now(timezone.utc)
        result_checkpoint = SyncCheckpoint(
            sync_token=next_sync_token,
            etag=etag,
            updated_at=updated_at,
        )
        self.save_checkpoint(calendar_id, result_checkpoint)
        return CalendarSyncResult(tuple(events), result_checkpoint)

    def _project_event(self, event: Mapping[str, Any], selected: bool) -> Mapping[str, Any]:
        metadata_keys = (
            "id",
            "etag",
            "status",
            "updated",
            "created",
            "start",
            "end",
            "recurringEventId",
            "htmlLink",
        )
        projected = {key: event[key] for key in metadata_keys if key in event}
        if self.policy.ingress is IngressMode.SELECTED_CONTENT and selected:
            for key in ("summary", "description", "location", "attendees", "organizer"):
                if key in event:
                    projected[key] = event[key]
        return projected

    def _state_account_id(self, calendar_id: str) -> str:
        return "%s:%s" % (self.account_id, calendar_id)

    def save_checkpoint(self, calendar_id: str, checkpoint: SyncCheckpoint) -> None:
        if self._connection is None:
            return
        self._connection.execute(
            """
            INSERT INTO connector_sync_state(
                connector_id, account_id, cursor, etag, last_synced_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, '{}')
            ON CONFLICT(connector_id, account_id) DO UPDATE SET
                cursor=excluded.cursor,
                etag=excluded.etag,
                last_synced_at=excluded.last_synced_at
            """,
            (
                self.connector_id,
                self._state_account_id(calendar_id),
                checkpoint.sync_token,
                checkpoint.etag,
                checkpoint.updated_at.isoformat(),
            ),
        )
        self._connection.commit()

    def load_checkpoint(self, calendar_id: str = "primary") -> Optional[SyncCheckpoint]:
        if self._connection is None:
            return None
        row = self._connection.execute(
            """
            SELECT cursor, etag, last_synced_at FROM connector_sync_state
            WHERE connector_id=? AND account_id=?
            """,
            (self.connector_id, self._state_account_id(calendar_id)),
        ).fetchone()
        if row is None:
            return None
        return SyncCheckpoint(
            sync_token=row[0], etag=row[1], updated_at=datetime.fromisoformat(row[2])
        )

    def propose_create(
        self, event: Mapping[str, Any], *, calendar_id: str = "primary"
    ) -> ActionProposal:
        self.policy.require_proposal()
        return ActionProposal.create(
            connector_id=self.connector_id,
            action="create_event",
            target=calendar_id,
            summary="Create calendar event: %s" % event.get("summary", "(untitled)"),
            payload={"calendar_id": calendar_id, "event": dict(event)},
        )

    def propose_update(
        self,
        event_id: str,
        changes: Mapping[str, Any],
        *,
        expected_etag: str,
        calendar_id: str = "primary",
    ) -> ActionProposal:
        self.policy.require_proposal()
        if not expected_etag:
            raise ValueError("Update proposals require the source ETag")
        return ActionProposal.create(
            connector_id=self.connector_id,
            action="update_event",
            target=event_id,
            summary="Update calendar event %s" % event_id,
            payload={
                "calendar_id": calendar_id,
                "event_id": event_id,
                "changes": dict(changes),
            },
            source_etag=expected_etag,
        )

    def propose_delete(
        self,
        event_id: str,
        *,
        expected_etag: str,
        calendar_id: str = "primary",
    ) -> ActionProposal:
        self.policy.require_proposal()
        if not expected_etag:
            raise ValueError("Delete proposals require the source ETag")
        return ActionProposal.create(
            connector_id=self.connector_id,
            action="delete_event",
            target=event_id,
            summary="Delete calendar event %s" % event_id,
            payload={"calendar_id": calendar_id, "event_id": event_id},
            source_etag=expected_etag,
        )

    def execute(self, proposal: ActionProposal) -> ActionExecutionResult:
        if proposal.connector_id != self.connector_id:
            raise ValueError("Proposal belongs to another connector")
        self.policy.require_execution(proposal)
        transport = require_transport(self.transport)
        token = self._access_token()
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
        }
        payload = proposal.mutable_payload()
        calendar_id = str(payload.get("calendar_id", "primary"))
        if proposal.action == "create_event":
            response = transport.request(
                "POST",
                self._calendar_url(calendar_id),
                headers=headers,
                json_body=payload["event"],
            )
            expected = (200, 201)
        elif proposal.action in ("update_event", "delete_event"):
            event_id = quote(str(payload["event_id"]), safe="")
            url = self._calendar_url(calendar_id, "events/%s" % event_id)
            headers["If-Match"] = str(proposal.source_etag)
            if proposal.action == "update_event":
                response = transport.request(
                    "PATCH", url, headers=headers, json_body=payload["changes"]
                )
                expected = (200,)
            else:
                response = transport.request("DELETE", url, headers=headers)
                expected = (200, 204)
        else:
            raise ValueError("Unknown Google Calendar proposal action")
        if response.status_code == 412:
            raise EtagConflictError(response)
        raise_for_status(response, expected=expected)
        return ActionExecutionResult(proposal.mark_executed(), response)

    execute_confirmed = execute
