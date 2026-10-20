from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lilies.connectors import (
    AssistanceMaterialExpiredError,
    AssistanceMaterialUnavailableError,
    AssistanceUnavailableError,
    CalendarRuntime,
    InMemorySecretBackend,
    MAX_ASSISTANCE_CONTENT_CHARS,
    SecretStore,
    SlackRuntime,
)
from lilies.core.database import Database


class NoNetworkTransport:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def request(self, *_args, **_kwargs):
        self.requests.append((_args, _kwargs))
        raise AssertionError("isolated assistance must not make a network request")


def runtime_for(
    tmp_path,
    provider: str = "slack",
    *,
    retention: str = "extended-cache",
    assistance: str = "assist",
):
    database = Database(tmp_path / f"{provider}.db")
    store = SecretStore(
        f"assist-{provider}", backend=InMemorySecretBackend()
    )
    transport = NoNetworkTransport()
    runtime_type = CalendarRuntime if provider == "calendar" else SlackRuntime
    runtime = runtime_type(
        database,
        account_id="personal",
        secret_store=store,
        transport=transport,
    )
    runtime.set_policy(
        {
            "scope": "broad",
            "interruption": "quiet",
            "retention": retention,
            "assistance": assistance,
        }
    )
    return runtime, transport


@pytest.mark.parametrize("provider", ["calendar", "slack"])
def test_runtime_issues_only_the_selected_item_as_untrusted_material(
    tmp_path, provider
):
    runtime, transport = runtime_for(tmp_path, provider)
    content = (
        {"summary": "Review", "description": "Read the draft", "location": "Room 1"}
        if provider == "calendar"
        else {"text": "Please read the draft"}
    )
    runtime._store_item(
        remote_id="event-selected",
        source_id="source-selected",
        occurred_at="2026-08-29T09:30:00+09:00",
        content=content,
    )

    material = runtime.issue_assistance("event-selected")
    payload = runtime.consume_assistance(material)

    assert payload == {
        "provider": provider,
        "remoteId": "event-selected",
        "sourceId": "source-selected",
        "occurredAt": "2026-08-29T09:30:00+09:00",
        "content": (
            "Review\nRead the draft"
            if provider == "calendar"
            else "Please read the draft"
        ),
        "untrusted": True,
    }
    assert transport.requests == []


def test_metadata_retention_refuses_assistance(tmp_path):
    runtime, _transport = runtime_for(tmp_path, retention="metadata")
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "not retained"},
    )

    with pytest.raises(AssistanceUnavailableError, match="metadata retention"):
        runtime.issue_assistance("event-1")


@pytest.mark.parametrize("provider", ["calendar", "slack"])
@pytest.mark.parametrize("retention", ["searchable-summary", "extended-cache"])
def test_item_availability_is_local_and_does_not_issue_or_send_material(tmp_path, monkeypatch, provider, retention):
    runtime, transport = runtime_for(tmp_path, provider, retention=retention)
    content = {"summary": "A review"} if provider == "calendar" else {"text": "A reply"}
    runtime._store_item(remote_id="one", source_id="source", occurred_at="2026-09-01", content=content)
    monkeypatch.setattr(runtime._assistance_materials, "issue", lambda *a, **kw: pytest.fail("projection issued a capability"))
    assert runtime.items()[0]["assistanceState"] == "available"
    assert "text" not in runtime.metadata_items()[0]
    assert "summary" not in runtime.metadata_items()[0]
    assert transport.requests == []


def test_enabling_retention_does_not_promise_old_content_or_trust_metadata(tmp_path):
    runtime, transport = runtime_for(tmp_path, retention="metadata")
    runtime._store_item(remote_id="old", source_id="source", occurred_at="2026-09-01",
                        content={"text": "not kept"},
                        metadata={"assistanceState": "available", "id": "forged"})
    assert runtime.items()[0]["assistanceState"] == "metadata-only"
    runtime.set_policy({**runtime.status()["policyCanonical"], "retention": "searchable-summary"})
    item = runtime.items()[0]
    assert item["id"] == "old"
    assert item["assistanceState"] == "content-unavailable"
    with pytest.raises(AssistanceUnavailableError):
        runtime.issue_assistance("old")
    assert transport.requests == []


def test_item_projection_rejects_cross_item_content_and_corrupt_vault(tmp_path, monkeypatch):
    runtime, _ = runtime_for(tmp_path)
    for key in ("one", "two"):
        runtime._store_item(remote_id=key, source_id="source", occurred_at="2026-09-01", content={"text": key})
    runtime.connection.execute("UPDATE connector_external_items SET content_id=? WHERE remote_id='one'",
                               ("slack:personal:two",))
    runtime.connection.commit()
    first = next(item for item in runtime.items() if item["id"] == "one")
    assert first["assistanceState"] == "content-unavailable" and "text" not in first
    from cryptography.exceptions import InvalidTag
    monkeypatch.setattr(runtime.vault, "get", lambda *a, **kw: (_ for _ in ()).throw(InvalidTag()))
    assert all(item["assistanceState"] == "content-unavailable" for item in runtime.items())


def test_availability_is_not_an_execution_capability_and_clearing_rechecks(tmp_path):
    runtime, _ = runtime_for(tmp_path)
    runtime._store_item(remote_id="one", source_id="source", occurred_at="2026-09-01", content={"text": "retained"})
    assert runtime.items()[0]["assistanceState"] == "available"
    runtime.clear_cached_content()
    assert runtime.items()[0]["assistanceState"] == "content-unavailable"
    with pytest.raises(AssistanceUnavailableError):
        runtime.issue_assistance("one")
    runtime.set_policy({**runtime.status()["policyCanonical"], "assistance": "reminder"})
    assert runtime.items()[0]["assistanceState"] == "policy-disabled"


def test_reminder_assistance_tier_refuses_retained_content(tmp_path):
    runtime, _transport = runtime_for(tmp_path, assistance="reminder")
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "retained but not consented"},
    )

    with pytest.raises(AssistanceUnavailableError, match="disabled"):
        runtime.issue_assistance("event-1")


def test_unknown_event_id_is_not_substituted_with_another_item(tmp_path):
    runtime, _transport = runtime_for(tmp_path)
    runtime._store_item(
        remote_id="event-real",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "real body"},
    )

    with pytest.raises(KeyError, match="unavailable"):
        runtime.issue_assistance("event-missing")


def test_material_is_consumable_exactly_once(tmp_path):
    runtime, _transport = runtime_for(tmp_path)
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "single use"},
    )
    material = runtime.issue_assistance("event-1")

    assert material.consume()["content"] == "single use"
    with pytest.raises(AssistanceMaterialUnavailableError, match="already been consumed"):
        material.consume()


def test_material_expires_without_becoming_consumable(tmp_path):
    runtime, _transport = runtime_for(tmp_path)
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "short lived"},
    )
    issued_at = datetime(2026, 8, 29, tzinfo=UTC)
    material = runtime.issue_assistance(
        "event-1", ttl=timedelta(seconds=2), now=issued_at
    )

    with pytest.raises(AssistanceMaterialExpiredError, match="expired"):
        material.consume(now=issued_at + timedelta(seconds=2))
    with pytest.raises(AssistanceMaterialUnavailableError):
        material.consume(now=issued_at + timedelta(seconds=3))


def test_assistance_text_has_a_hard_total_limit(tmp_path):
    runtime, _transport = runtime_for(tmp_path)
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "x" * (MAX_ASSISTANCE_CONTENT_CHARS + 500)},
    )

    payload = runtime.issue_assistance("event-1").consume()

    assert payload["content"] == "x" * MAX_ASSISTANCE_CONTENT_CHARS
    assert len(payload["content"]) == MAX_ASSISTANCE_CONTENT_CHARS


@pytest.mark.parametrize("retention", ["searchable-summary", "extended-cache"])
def test_selected_material_cannot_leak_neighbor_or_unapproved_fields(
    tmp_path, retention
):
    runtime, transport = runtime_for(tmp_path, retention=retention)
    runtime._store_item(
        remote_id="event-selected",
        source_id="C-ONE",
        occurred_at="2026-08-29T10:00:00+00:00",
        metadata={"access_token": "METADATA-CREDENTIAL"},
        content={
            "text": "SELECTED-BODY",
            "credential": "CONTENT-CREDENTIAL",
            "messages": ["NEIGHBOR-AS-NESTED-LIST"],
        },
    )
    runtime._store_item(
        remote_id="event-neighbor",
        source_id="C-TWO",
        occurred_at="2026-08-29T09:00:00+00:00",
        content={"text": "NEIGHBOR-SECRET"},
    )

    payload = runtime.issue_assistance("event-selected").consume()

    assert set(payload) == {
        "provider",
        "remoteId",
        "sourceId",
        "occurredAt",
        "content",
        "untrusted",
    }
    assert payload["content"] == "SELECTED-BODY"
    assert "NEIGHBOR" not in repr(payload)
    assert "CREDENTIAL" not in repr(payload)
    assert transport.requests == []


def test_status_exposes_localized_and_canonical_policy(tmp_path):
    runtime, _transport = runtime_for(
        tmp_path, retention="searchable-summary", assistance="confirm-execute"
    )

    status = runtime.status()

    assert status["policy"]["assistance"] == "确认执行"
    assert status["policyCanonical"]["assistance"] == "confirm-execute"
    assert status["policyCanonical"]["retention"] == "searchable-summary"


def test_clearing_content_also_removes_sensitive_pending_proposals(tmp_path):
    runtime, _transport = runtime_for(
        tmp_path, provider="slack", assistance="confirm-execute"
    )
    runtime.database.set_setting(
        "connector_slack_configuration",
        {
            "clientId": "slack-client",
            "currentUserId": "U-ME",
            "redirectUri": "http://127.0.0.1/callback",
        },
    )
    runtime._store_item(
        remote_id="event-1",
        source_id="C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content={"text": "private message"},
    )
    proposal = runtime.propose_reply("event-1", "private reply")

    result = runtime.clear_cached_content(keep_metadata=True)

    assert result["proposalsDeleted"] == 1
    assert runtime.connection.execute(
        "SELECT COUNT(*) FROM connector_action_proposals WHERE proposal_id=?",
        (proposal["id"],),
    ).fetchone() == (0,)
    with pytest.raises(KeyError):
        runtime.proposal(proposal["id"])


@pytest.mark.parametrize("provider", ["calendar", "slack"])
def test_disconnect_removes_credentials_content_and_outstanding_material(
    tmp_path, provider
):
    runtime, _transport = runtime_for(tmp_path, provider)
    if provider == "calendar":
        runtime.database.set_setting("connector_calendar_client_id", "google-client")
        connector = runtime._connector()
        connector.save_tokens({"access_token": "calendar-token"})
    else:
        runtime.database.set_setting(
            "connector_slack_configuration",
            {
                "clientId": "slack-client",
                "currentUserId": "U-ME",
                "redirectUri": "http://127.0.0.1/callback",
            },
        )
        connector = runtime._connector()
        connector.save_oauth_tokens(
            {"ok": True, "authed_user": {"access_token": "slack-token"}}
        )
        connector.save_app_token("xapp-local")
    runtime._save_account(connected=True, metadata={"configured": True})
    runtime._store_item(
        remote_id="event-1",
        source_id="primary" if provider == "calendar" else "C-ONE",
        occurred_at="2026-08-29T00:00:00+00:00",
        content=(
            {"summary": "private event"}
            if provider == "calendar"
            else {"text": "private message"}
        ),
    )
    material = runtime.issue_assistance("event-1")

    status = runtime.disconnect()

    assert status["configured"] is True
    assert status["connected"] is False
    assert status["state"] == "configured"
    assert runtime.connection.execute(
        "SELECT COUNT(*) FROM connector_external_items WHERE connector_id=?",
        (provider,),
    ).fetchone() == (0,)
    assert runtime.connection.execute(
        "SELECT COUNT(*) FROM connector_encrypted_content WHERE namespace=?",
        (provider,),
    ).fetchone() == (0,)
    with pytest.raises(AssistanceMaterialUnavailableError):
        material.consume()
    if provider == "calendar":
        assert connector.load_tokens() is None
        assert runtime.database.get_setting("connector_calendar_client_id", "") == "google-client"
    else:
        assert connector.load_oauth_tokens() is None
        assert runtime.secret_store.get_text(connector._app_token_key()) is None
        assert runtime.database.get_setting("connector_slack_configuration", {})[
            "clientId"
        ] == "slack-client"
