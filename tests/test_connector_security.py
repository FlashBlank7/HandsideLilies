import os
import sqlite3
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag

from lilies.connectors.policy import (
    ActionMode,
    ActionProposal,
    ConnectorPolicy,
    IngressMode,
    ModelAccessMode,
    PolicyViolationError,
    ProposalStatus,
    RetentionMode,
)
from lilies.connectors.schema import ensure_schema
from lilies.connectors.security import (
    EncryptedContentVault,
    InMemorySecretBackend,
    SecretStore,
    SecretStoreUnavailableError,
)


def test_secret_store_requires_explicit_non_windows_backend():
    if os.name != "nt":
        with pytest.raises(SecretStoreUnavailableError):
            SecretStore("test")


def test_explicit_memory_secret_store_round_trip():
    store = SecretStore("test", backend=InMemorySecretBackend())
    store.set_text("oauth", "never-write-this-token")
    assert store.get_text("oauth") == "never-write-this-token"
    store.delete("oauth")
    assert store.get_text("oauth") is None


def test_vault_authenticates_context_and_persists_only_ciphertext(tmp_path):
    store = SecretStore("test", backend=InMemorySecretBackend())
    path = tmp_path / "connectors.sqlite3"
    vault = EncryptedContentVault(store, database=path)

    plaintext = "private calendar description"
    envelope = vault.encrypt(plaintext, associated_data="calendar:one")
    assert plaintext.encode() not in envelope
    assert vault.decrypt(envelope, associated_data="calendar:one") == plaintext.encode()
    with pytest.raises(InvalidTag):
        vault.decrypt(envelope, associated_data="calendar:two")

    vault.put(
        "event-1",
        plaintext,
        namespace="calendar",
        metadata={"private-note": "also secret"},
    )
    entry = vault.get("event-1", namespace="calendar")
    assert entry is not None
    assert entry.content == plaintext.encode()
    assert entry.metadata == {"private-note": "also secret"}
    row = vault._connection.execute(  # verify the storage boundary itself
        "SELECT ciphertext FROM connector_encrypted_content WHERE content_id='event-1'"
    ).fetchone()
    assert plaintext.encode() not in row[0]
    assert b"also secret" not in row[0]
    vault.put("event-1", "a separate namespace", namespace="slack")
    assert vault.get("event-1", namespace="calendar").content == plaintext.encode()
    assert vault.get("event-1", namespace="slack").content == b"a separate namespace"


def test_connector_schema_is_independent_and_idempotent(tmp_path):
    connection = ensure_schema(tmp_path / "connectors.sqlite3")
    assert ensure_schema(connection) is connection
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "connector_encrypted_content",
        "connector_sync_state",
        "connector_event_dedupe",
        "connector_action_proposals",
    } <= tables


def test_connector_schema_v3_redacts_legacy_plaintext_proposal_summary(tmp_path):
    path = tmp_path / "connectors.sqlite3"
    connection = ensure_schema(path)
    connection.execute("UPDATE connector_schema_meta SET version=2")
    connection.execute(
        """INSERT INTO connector_action_proposals
           (proposal_id,connector_id,action,target,payload_ciphertext,summary,status,
            requires_confirmation,source_etag,created_at,expires_at,resolved_at)
           VALUES('legacy','google-calendar','create_event','primary',NULL,?,
                  'pending',1,NULL,'2026-08-29T00:00:00+00:00',NULL,NULL)""",
        ("Create calendar event: private title",),
    )
    connection.commit()

    ensure_schema(connection)

    row = connection.execute(
        "SELECT summary FROM connector_action_proposals WHERE proposal_id='legacy'"
    ).fetchone()
    assert row == ("Calendar operation preview",)


def test_four_axis_policy_and_immutable_confirmation_boundary():
    safe = ConnectorPolicy()
    assert not safe.allows_metadata()
    assert not safe.may_persist()
    assert not safe.allows_model(remote=False)
    assert safe.can_propose()

    policy = ConnectorPolicy(
        ingress=IngressMode.SELECTED_CONTENT,
        retention=RetentionMode.ENCRYPTED_PERSISTENT,
        model_access=ModelAccessMode.EXPLICIT_REMOTE,
        actions=ActionMode.REQUIRE_CONFIRMATION,
    )
    proposal = ActionProposal.create(
        connector_id="test",
        action="write",
        target="item",
        summary="Write item",
        payload={"nested": {"value": 1}},
        ttl=timedelta(minutes=1),
    )
    assert not policy.can_execute(proposal)
    with pytest.raises(TypeError):
        proposal.payload["nested"]["value"] = 2
    confirmed = proposal.confirm()
    assert confirmed.status is ProposalStatus.CONFIRMED
    assert policy.can_execute(confirmed)
    assert confirmed.mutable_payload() == {"nested": {"value": 1}}

    propose_only = ConnectorPolicy(actions=ActionMode.PROPOSE_ONLY)
    with pytest.raises(PolicyViolationError):
        propose_only.require_execution(confirmed)
