import json
from itertools import product

import pytest

from lilies.backend import Backend
from lilies.connectors.runtime import SlackRuntime


REDIRECT = "http://127.0.0.1:53682/oauth/callback"


class NoServices:
    def __getattr__(self, name):
        raise AssertionError(f"A pure preview must not access backend service: {name}")


@pytest.mark.parametrize("scope,assistance", product(
    ("necessary", "selected", "broad"), ("reminder", "assist", "confirm-execute")))
def test_preview_scopes_events_and_writes_match_draft_without_account(scope, assistance):
    response = Backend.slackGenerateManifest(NoServices(), {
        "policy": {"scope": scope, "assistance": assistance}, "redirectUri": REDIRECT,
    })
    assert response["ok"] is True
    manifest = json.loads(response["manifest"])
    oauth = manifest["oauth_config"]
    scopes = oauth["scopes"]["user"]
    events = manifest["settings"]["event_subscriptions"]["user_events"]
    assert set(oauth["scopes"]) == {"user"}, "desktop PKCE must not request bot scopes"
    assert oauth["pkce_enabled"] and oauth["redirect_urls"] == [REDIRECT]
    assert ("chat:write" in scopes) is (assistance == "confirm-execute")
    if scope == "necessary":
        assert set(scopes) == {"im:history", "channels:history"} | (
            {"chat:write"} if assistance == "confirm-execute" else set())
        assert set(events) == {"message.im", "message.channels"}
    else:
        assert set(events) == {"message.im", "message.channels", "message.groups", "message.mpim"}
    assert all(event.removeprefix("message.") + ":history" in scopes for event in events)
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert manifest["settings"]["token_rotation_enabled"] is True
    assert "clientId" not in response["manifest"]


@pytest.mark.parametrize("draft", [
    None, [], "bad", {"xappToken": "xapp-must-never-echo"},
    {"clientId": "not-needed"}, {"policy": None}, {"policy": []},
    {"policy": {"selectedSources": ["C-NOT-NEEDED"]}},
    {"policy": {"scope": {"secret": "xapp-must-never-echo"}}},
    {"policy": {"scope": "xapp-must-never-echo"}},
    {"policy": {"assistance": "automatic"}},
    {"redirectUri": 123}, {"redirectUri": "x" * 513},
])
def test_invalid_draft_fails_closed_without_echoing_secrets(draft):
    response = Backend.slackGenerateManifest(NoServices(), draft)
    assert response["ok"] is False
    assert "manifest" not in response
    assert "xapp-" not in json.dumps(response)


@pytest.mark.parametrize("redirect", [
    "https://example.org/callback", "custom://callback", "http://0.0.0.0:53682/callback",
    "http://127.0.0.1/callback", "http://127.0.0.1:0/callback",
    "http://127.0.0.1:65536/callback", "http://127.0.0.1:not-a-port/callback",
    "http://user:secret@127.0.0.1:53682/callback", REDIRECT + "?secret=hidden",
    REDIRECT + "#fragment", REDIRECT + ";parameters", REDIRECT + "\n",
    " http://127.0.0.1:53682/callback", "http://127.0.0.1:53682",
    "http://127.0.0.1:53682\\callback", "http://[127.0.0.1:53682/callback",
])
def test_preview_does_not_offer_redirects_the_local_receiver_cannot_handle(redirect):
    response = Backend.slackGenerateManifest(NoServices(), {"redirectUri": redirect})
    assert response["ok"] is False
    assert redirect not in response["error"]


def test_empty_bootstrap_can_be_generated_and_each_preview_is_independent():
    first = SlackRuntime.preview_manifest({})
    assert "redirect_urls" not in first["oauth_config"]
    first["oauth_config"]["scopes"]["user"].append("chat:write")
    second = SlackRuntime.preview_manifest({"policy": {"scope": "必要", "assistance": "协助"}})
    assert "chat:write" not in second["oauth_config"]["scopes"]["user"]
