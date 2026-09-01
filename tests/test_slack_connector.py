import json
import hashlib
import hmac
from collections import deque
from urllib.parse import parse_qs, urlparse

import pytest

from lilies.connectors.http import HttpResponse, RateLimitError
from lilies.connectors.policy import (
    ActionMode,
    ConnectorPolicy,
    IngressMode,
    PolicyViolationError,
)
from lilies.connectors.security import InMemorySecretBackend, SecretStore
from lilies.connectors.slack import (
    SlackConnector,
    SlackEventFilter,
    SlackManifestOptions,
    generate_manifest,
)


def response(payload, status=200, headers=None):
    return HttpResponse(status, json.dumps(payload).encode(), headers or {})


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.popleft()


def connector(transport=None, database=None, ingress=IngressMode.SELECTED_CONTENT):
    store = SecretStore("test", backend=InMemorySecretBackend())
    instance = SlackConnector(
        client_id="slack-client-id",
        secret_store=store,
        event_filter=SlackEventFilter(
            authorized_user_id="U-ME",
            allowed_team_ids={"T-ONE"},
            selected_channel_ids={"C-SELECTED"},
        ),
        policy=ConnectorPolicy(
            ingress=ingress,
            actions=ActionMode.REQUIRE_CONFIRMATION,
        ),
        transport=transport,
        database=database,
    )
    instance.save_oauth_tokens(
        {"ok": True, "authed_user": {"access_token": "xoxp-user-token"}}
    )
    return instance


def event(event_id, channel, text, **event_fields):
    body = {
        "type": "message",
        "channel": channel,
        "user": "U-SENDER",
        "text": text,
        "ts": "1720000000.000100",
    }
    body.update(event_fields)
    return {"type": "event_callback", "team_id": "T-ONE", "event_id": event_id, "event": body}


def test_manifest_uses_user_events_and_never_contains_credentials():
    manifest = generate_manifest(
        SlackManifestOptions(
            socket_mode=True,
            redirect_urls=("lilies://oauth/slack",),
            user_mode=True,
        )
    )
    assert "user" in manifest["oauth_config"]["scopes"]
    assert "bot" not in manifest["oauth_config"]["scopes"]
    assert "message.im" in manifest["settings"]["event_subscriptions"]["user_events"]
    assert manifest["oauth_config"]["pkce_enabled"] is True
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert "xapp-" not in json.dumps(manifest)
    with pytest.raises(ValueError):
        SlackManifestOptions(socket_mode=True, marketplace_distribution=True)


def test_slack_pkce_is_user_scope_only():
    authorization = SlackConnector.create_authorization(
        client_id="slack-client-id",
        redirect_uri="lilies://oauth/slack",
        user_scopes=("im:history", "channels:history"),
        state="fixed-state",
    )
    query = parse_qs(urlparse(authorization.authorization_url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["user_scope"] == ["im:history,channels:history"]
    assert "scope" not in query
    assert "client_secret" not in query
    assert authorization.code_verifier not in authorization.authorization_url
    with pytest.raises(ValueError):
        SlackConnector.create_authorization(
            client_id="id",
            redirect_uri="lilies://oauth/slack",
            bot_scopes=("chat:write",),
        )


def test_expired_slack_token_rotates_and_preserves_refresh_token():
    transport = FakeTransport(
        response(
            {
                "ok": True,
                "authed_user": {"access_token": "fresh-user-token"},
                "expires_in": 3600,
            }
        )
    )
    instance = connector(transport)
    instance.save_oauth_tokens(
        {
            "ok": True,
            "authed_user": {
                "access_token": "expired",
                "refresh_token": "rotate-me",
            },
            "expires_in": 1,
            "obtained_at": 1,
        }
    )
    assert instance._access_token() == "fresh-user-token"
    assert instance.load_oauth_tokens()["authed_user"]["refresh_token"] == "rotate-me"
    assert transport.requests[0][2]["data"]["grant_type"] == "refresh_token"


def test_event_filter_allows_dm_mentions_and_selected_channels_then_dedupes(tmp_path):
    instance = connector(database=tmp_path / "connectors.sqlite3")
    dm = event("Ev-DM", "D-DIRECT", "hello", channel_type="im")
    assert instance.ingest_event(dm).text == "hello"
    assert instance.ingest_event(dm) is None

    assert instance.ingest_event(event("Ev-NO", "C-OTHER", "ordinary")) is None
    mention = instance.ingest_event(event("Ev-MENTION", "C-OTHER", "hi <@U-ME>"))
    assert mention.is_mention
    selected = instance.ingest_event(event("Ev-SELECTED", "C-SELECTED", "channel update"))
    assert selected.channel_id == "C-SELECTED"
    assert instance.ingest_event(
        event("Ev-BOT", "D-DIRECT", "bot", bot_id="B-ONE", subtype="bot_message")
    ) is None


def test_app_mention_is_not_treated_as_a_human_user_mention():
    instance = connector()
    payload = event("Ev-APP-MENTION", "C-OTHER", "the app was mentioned")
    payload["event"]["type"] = "app_mention"
    assert instance.ingest_event(payload) is None


def test_metadata_ingress_strips_slack_message_content():
    instance = connector(ingress=IngressMode.METADATA_ONLY)
    projected = instance.ingest_event(event("Ev-META", "D-DIRECT", "private text"))
    assert projected.text == ""
    assert projected.raw_event == {}


def test_slack_send_and_socket_url_have_separate_confirmation_and_tokens():
    transport = FakeTransport(
        response({"ok": True, "channel": "C-SELECTED", "ts": "1.2"}),
        response({"ok": True, "url": "wss://wss-primary.slack.com/link"}),
    )
    instance = connector(transport)
    proposal = instance.propose_message(channel_id="C-SELECTED", text="review me")
    with pytest.raises(PolicyViolationError):
        instance.send_confirmed(proposal)
    assert transport.requests == []

    result = instance.send_confirmed(proposal.confirm())
    assert result.proposal.status.value == "executed"
    send_headers = transport.requests[0][2]["headers"]
    assert send_headers["Authorization"] == "Bearer xoxp-user-token"
    assert transport.requests[0][2]["json_body"]["client_msg_id"] == proposal.proposal_id

    instance.save_app_token("xapp-explicit-app-token")
    assert instance.request_socket_url().startswith("wss://")
    socket_headers = transport.requests[1][2]["headers"]
    assert socket_headers["Authorization"] == "Bearer xapp-explicit-app-token"


def test_slack_429_exposes_retry_after_without_automatic_retry():
    transport = FakeTransport(HttpResponse(429, headers={"Retry-After": "7"}))
    instance = connector(transport)
    proposal = instance.propose_message(channel_id="C-SELECTED", text="one message")
    with pytest.raises(RateLimitError) as error:
        instance.send_confirmed(proposal.confirm())
    assert error.value.retry_after_seconds == 7
    assert len(transport.requests) == 1


def test_http_event_signature_is_verified_before_message_parsing():
    instance = connector()
    instance.save_signing_secret("signing-secret")
    body = b'{"event":{"type":"message","text":"private"}}'
    timestamp = "1700000000"
    base = b"v0:" + timestamp.encode() + b":" + body
    signature = "v0=" + hmac.new(b"signing-secret", base, hashlib.sha256).hexdigest()
    assert instance.verify_http_signature(
        timestamp=timestamp,
        raw_body=body,
        signature=signature,
        now=1700000100,
    )
    assert not instance.verify_http_signature(
        timestamp=timestamp,
        raw_body=body + b" ",
        signature=signature,
        now=1700000100,
    )
    assert not instance.verify_http_signature(
        timestamp=timestamp,
        raw_body=body,
        signature=signature,
        now=1700001000,
    )
