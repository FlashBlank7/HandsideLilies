import json
from collections import deque
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from lilies.connectors.google_calendar import (
    GoogleCalendarConnector,
    LoopbackOAuthReceiver,
    RollingSyncWindow,
    SyncCheckpoint,
)
from lilies.connectors.http import HttpResponse
from lilies.connectors.policy import (
    ActionMode,
    ConnectorPolicy,
    IngressMode,
    PolicyViolationError,
)
from lilies.connectors.security import InMemorySecretBackend, SecretStore


def response(payload, status=200, headers=None):
    return HttpResponse(
        status,
        json.dumps(payload).encode("utf-8"),
        headers or {},
    )


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.popleft()


def connector(transport=None, database=None):
    store = SecretStore("test", backend=InMemorySecretBackend())
    instance = GoogleCalendarConnector(
        client_id="desktop-client-id",
        secret_store=store,
        policy=ConnectorPolicy(
            ingress=IngressMode.SELECTED_CONTENT,
            actions=ActionMode.REQUIRE_CONFIRMATION,
        ),
        transport=transport,
        database=database,
    )
    instance.save_tokens({"access_token": "ya29.test", "refresh_token": "refresh"})
    return instance


def test_google_authorization_url_uses_s256_without_client_secret():
    authorization = GoogleCalendarConnector.create_authorization(
        client_id="desktop-client-id",
        redirect_uri="http://127.0.0.1:43123/oauth/callback",
        state="fixed-state",
    )
    query = parse_qs(urlparse(authorization.authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [authorization.code_challenge]
    assert query["state"] == ["fixed-state"]
    assert "client_secret" not in query
    assert authorization.code_verifier not in authorization.authorization_url


def test_expired_google_token_refreshes_without_losing_refresh_token():
    transport = FakeTransport(response({"access_token": "fresh", "expires_in": 3600}))
    instance = connector(transport)
    instance.save_tokens(
        {
            "access_token": "expired",
            "refresh_token": "refresh-me",
            "expires_at": 1,
        }
    )
    assert instance._access_token() == "fresh"
    saved = instance.load_tokens()
    assert saved["refresh_token"] == "refresh-me"
    assert transport.requests[0][2]["data"]["grant_type"] == "refresh_token"


def test_loopback_receiver_binds_and_validates_state():
    receiver = LoopbackOAuthReceiver(expected_state="state-123").start()
    try:
        with urlopen(receiver.redirect_uri + "?code=one-time-code&state=state-123") as page:
            assert page.status == 200
        callback = receiver.wait(timeout=2)
        assert callback.code == "one-time-code"
        assert callback.error is None
    finally:
        receiver.close()


def test_rolling_sync_pages_projects_selected_content_and_saves_checkpoint(tmp_path):
    transport = FakeTransport(
        response(
            {
                "items": [
                    {
                        "id": "event-1",
                        "etag": "item-etag-1",
                        "summary": "Selected title",
                        "description": "Selected body",
                        "start": {"dateTime": "2026-08-29T01:00:00Z"},
                    }
                ],
                "nextPageToken": "page-2",
            },
            headers={"ETag": "collection-etag"},
        ),
        response(
            {
                "items": [
                    {"id": "event-2", "summary": "Not selected", "status": "confirmed"}
                ],
                "nextSyncToken": "sync-token-2",
            }
        ),
    )
    instance = connector(transport, tmp_path / "connectors.sqlite3")
    result = instance.sync(selected_event_ids={"event-1"})

    assert len(result.events) == 2
    assert result.events[0]["summary"] == "Selected title"
    assert "summary" not in result.events[1]
    assert result.checkpoint.sync_token == "sync-token-2"
    assert result.checkpoint.etag == "collection-etag"
    assert transport.requests[0][2]["params"]["timeMin"].endswith("Z")
    assert transport.requests[1][2]["params"]["pageToken"] == "page-2"
    assert instance.load_checkpoint().sync_token == "sync-token-2"


def test_incremental_sync_honors_if_none_match_and_304():
    transport = FakeTransport(HttpResponse(304))
    instance = connector(transport)
    checkpoint = SyncCheckpoint(sync_token="sync-token", etag="collection-etag")
    result = instance.sync(checkpoint=checkpoint)
    assert result.not_modified
    request = transport.requests[0][2]
    assert request["headers"]["If-None-Match"] == "collection-etag"
    assert request["params"]["syncToken"] == "sync-token"
    assert "timeMin" not in request["params"]


def test_calendar_write_requires_confirmation_and_update_uses_if_match():
    transport = FakeTransport(response({"id": "event-1", "etag": "new-etag"}))
    instance = connector(transport)
    proposal = instance.propose_update(
        "event-1", {"summary": "Changed"}, expected_etag="old-etag"
    )
    with pytest.raises(PolicyViolationError):
        instance.execute(proposal)
    assert transport.requests == []

    result = instance.execute(proposal.confirm())
    assert result.proposal.status.value == "executed"
    method, url, kwargs = transport.requests[0]
    assert method == "PATCH"
    assert url.endswith("/events/event-1")
    assert kwargs["headers"]["If-Match"] == "old-etag"
    assert kwargs["json_body"] == {"summary": "Changed"}
