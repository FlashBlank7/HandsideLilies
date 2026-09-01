import json
import threading
from collections import deque
from urllib.parse import parse_qs, urlparse

import pytest

from lilies.connectors.http import HttpResponse
from lilies.connectors.policy import ProposalStateError
from lilies.connectors.runtime import CalendarRuntime, SlackRuntime
from lilies.connectors.security import InMemorySecretBackend, SecretStore
from lilies.core.database import Database


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected connector network request")
        return self.responses.popleft()


def response(payload, status=200, headers=None):
    return HttpResponse(status, json.dumps(payload).encode("utf-8"), headers or {})


def services(tmp_path, transport=None):
    database = Database(tmp_path / "lilies.db")
    store = SecretStore("v03-test", backend=InMemorySecretBackend())
    transport = transport or FakeTransport()
    return database, store, transport


def test_slack_runtime_matches_qml_contract_and_minimizes_scopes(tmp_path):
    database, store, transport = services(tmp_path)
    runtime = SlackRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    status = runtime.configure(
        {
            "clientId": "client",
            "currentUserId": "U-ME",
            "xappToken": "xapp-local",
            "redirectUri": "http://127.0.0.1:53682/oauth/callback",
            "selectedChannels": ["C-ONE"],
            "policy": {
                "scope": "necessary",
                "interruption": "quiet",
                "retention": "metadata",
                "assistance": "assist",
            },
        }
    )
    assert status["configured"] is True
    assert status["socketReady"] is True
    authorization = runtime.authorization()
    scopes = parse_qs(urlparse(authorization.authorization_url).query)["user_scope"][0]
    assert scopes == "im:history,channels:history"
    assert "chat:write" not in scopes
    manifest = runtime.manifest()
    assert manifest["oauth_config"]["scopes"]["user"] == [
        "im:history",
        "channels:history",
    ]


def test_retention_downgrade_removes_encrypted_content(tmp_path):
    database, store, transport = services(tmp_path)
    runtime = SlackRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.set_policy(
        {
            "scope": "selected",
            "interruption": "quiet",
            "retention": "extended-cache",
            "assistance": "assist",
            "selectedSources": ["C-ONE"],
        }
    )
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-01-01T00:00:00+00:00",
        content={"text": "private message"},
    )
    assert runtime.items()[0]["text"] == "private message"
    runtime.set_policy(
        {
            "scope": "selected",
            "interruption": "quiet",
            "retention": "metadata",
            "assistance": "assist",
            "selectedSources": ["C-ONE"],
        }
    )
    assert "text" not in runtime.items()[0]
    row = runtime.connection.execute(
        "SELECT content_id FROM connector_external_items WHERE remote_id='event-1'"
    ).fetchone()
    assert row == (None,)


def test_extended_retention_downgrade_rewrites_ciphertext_to_summary(tmp_path):
    database, store, transport = services(tmp_path)
    runtime = SlackRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.set_policy(
        {
            "scope": "selected",
            "interruption": "quiet",
            "retention": "extended-cache",
            "assistance": "assist",
            "selectedSources": ["C-ONE"],
        }
    )
    runtime._store_item(
        remote_id="event-summary",
        source_id="C-ONE",
        occurred_at="2026-01-01T00:00:00+00:00",
        content={"text": "private full message", "detail": "extended-only detail"},
    )
    content_id = runtime.connection.execute(
        "SELECT content_id FROM connector_external_items WHERE remote_id='event-summary'"
    ).fetchone()[0]

    runtime.set_policy(
        {
            "scope": "selected",
            "interruption": "quiet",
            "retention": "searchable-summary",
            "assistance": "assist",
            "selectedSources": ["C-ONE"],
        }
    )

    projected = runtime.items()[0]
    assert "text" not in projected
    assert "detail" not in projected
    assert "private full message" in projected["summary"]
    encrypted = runtime.vault.get(content_id, namespace="slack")
    assert encrypted is not None
    decoded = json.loads(encrypted.content.decode("utf-8"))
    assert set(decoded) == {"summary"}
    assert "text" not in decoded
    assert "detail" not in decoded


def test_calendar_scopes_preview_validation_and_executed_ledger(tmp_path):
    transport = FakeTransport(response({"id": "event-1", "etag": "new"}, 200))
    database, store, _ = services(tmp_path, transport)
    runtime = CalendarRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.configure(
        {
            "clientId": "google-client",
            "policy": {
                "scope": "necessary",
                "interruption": "quiet",
                "retention": "extended-cache",
                "assistance": "confirm-execute",
            },
        }
    )
    authorization = runtime.authorization("http://127.0.0.1:12345/oauth/callback")
    assert "calendar.calendarlist.readonly" in " ".join(authorization.scopes)
    assert "calendar.events.owned" in " ".join(authorization.scopes)
    runtime._connector().save_tokens({"access_token": "token"})
    runtime._store_item(
        remote_id="event-1",
        source_id="primary",
        occurred_at="2026-01-01T09:00:00+09:00",
        end_at="2026-01-01T10:00:00+09:00",
        metadata={"etag": "old"},
        content={"summary": "Before"},
    )
    with pytest.raises(ValueError):
        runtime.propose_update("event-1", {"attendees": [{"email": "x@example.com"}]})
    proposal = runtime.propose_update("event-1", {"summary": "After"})
    assert proposal["before"]["summary"] == "Before"
    assert proposal["after"]["changes"]["summary"] == "After"
    result = runtime.confirm_and_execute(proposal["id"])
    assert result["status"] == "executed"
    row = runtime.connection.execute(
        "SELECT status,summary FROM connector_action_proposals WHERE proposal_id=?",
        (proposal["id"],),
    ).fetchone()
    assert row == ("executed", "Calendar operation preview")
    assert transport.requests[0][2]["headers"]["If-Match"] == "old"


def test_calendar_proposal_is_atomically_claimed_before_network_write(tmp_path):
    class BlockingTransport:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.requests = []

        def request(self, method, url, **kwargs):
            self.requests.append((method, url, kwargs))
            self.started.set()
            assert self.release.wait(3), "test did not release connector request"
            return response({"id": "created-once", "etag": "new"}, 200)

    transport = BlockingTransport()
    database, store, _ = services(tmp_path, transport)
    runtime = CalendarRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.configure(
        {
            "clientId": "google-client",
            "policy": {
                "scope": "necessary",
                "interruption": "quiet",
                "retention": "metadata",
                "assistance": "confirm-execute",
            },
        }
    )
    runtime._connector().save_tokens({"access_token": "token"})
    proposal = runtime.propose_create(
        {
            "summary": "Create once",
            "start": {"dateTime": "2026-08-29T09:00:00+09:00"},
            "end": {"dateTime": "2026-08-29T10:00:00+09:00"},
        }
    )
    completed = []
    errors = []

    def first_confirmation():
        try:
            completed.append(runtime.confirm_and_execute(proposal["id"]))
        except Exception as exc:  # pragma: no cover - assertion captures it
            errors.append(exc)

    worker = threading.Thread(target=first_confirmation)
    worker.start()
    assert transport.started.wait(3)
    with pytest.raises(ProposalStateError, match="Only pending"):
        runtime.confirm_and_execute(proposal["id"])
    transport.release.set()
    worker.join(3)

    assert not worker.is_alive()
    assert errors == []
    assert completed[0]["status"] == "executed"
    assert len(transport.requests) == 1


def test_model_safe_metadata_projection_never_returns_decrypted_body(tmp_path):
    database, store, transport = services(tmp_path)
    runtime = SlackRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.set_policy(
        {
            "scope": "selected",
            "retention": "extended-cache",
            "interruption": "quiet",
            "assistance": "assist",
            "selectedSources": ["C-ONE"],
        }
    )
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-01-01T00:00:00+00:00",
        content={"text": "must not reach the model"},
    )
    assert runtime.open_message("event-1")["text"] == "must not reach the model"
    assert "text" not in runtime.metadata_items()[0]


def test_slack_scope_change_discards_unselected_channel_but_keeps_dm(tmp_path):
    database, store, transport = services(tmp_path)
    runtime = SlackRuntime(
        database, account_id="personal", secret_store=store, transport=transport
    )
    runtime.set_policy(
        {
            "scope": "broad",
            "retention": "extended-cache",
            "interruption": "quiet",
            "assistance": "assist",
        }
    )
    runtime._store_item(
        remote_id="channel-event",
        source_id="C-OLD",
        occurred_at="2026-01-01T00:00:00+00:00",
        metadata={"isDirect": False, "isMention": False},
        content={"text": "discard"},
    )
    runtime._store_item(
        remote_id="dm-event",
        source_id="D-DM",
        occurred_at="2026-01-01T00:00:00+00:00",
        metadata={"isDirect": True, "isMention": False},
        content={"text": "keep"},
    )
    runtime.set_policy(
        {
            "scope": "selected",
            "retention": "extended-cache",
            "interruption": "quiet",
            "assistance": "assist",
            "selectedSources": ["C-NEW"],
        }
    )
    assert [item["id"] for item in runtime.items()] == ["dm-event"]
