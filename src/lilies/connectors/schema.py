"""Connector-owned SQLite schema, kept separate from the application database."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Union


SCHEMA_VERSION = 3


_DDL = """
CREATE TABLE IF NOT EXISTS connector_schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS connector_encrypted_content (
    content_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, content_id)
);

CREATE TABLE IF NOT EXISTS connector_sync_state (
    connector_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    cursor TEXT,
    etag TEXT,
    last_synced_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (connector_id, account_id)
);

CREATE TABLE IF NOT EXISTS connector_event_dedupe (
    connector_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    seen_at REAL NOT NULL,
    PRIMARY KEY (connector_id, event_id)
);

CREATE INDEX IF NOT EXISTS connector_event_dedupe_seen_at
    ON connector_event_dedupe(seen_at);

CREATE TABLE IF NOT EXISTS connector_action_proposals (
    proposal_id TEXT PRIMARY KEY,
    connector_id TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    payload_ciphertext BLOB,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    requires_confirmation INTEGER NOT NULL,
    source_etag TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS connector_accounts (
    connector_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    connected INTEGER NOT NULL DEFAULT 0,
    last_sync_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connector_id, account_id)
);

CREATE TABLE IF NOT EXISTS connector_policies (
    connector_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'necessary',
    interruption TEXT NOT NULL DEFAULT 'quiet',
    retention TEXT NOT NULL DEFAULT 'metadata',
    assistance TEXT NOT NULL DEFAULT 'assist',
    selected_sources_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connector_id, account_id)
);

CREATE TABLE IF NOT EXISTS connector_external_items (
    connector_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT '',
    end_at TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    sensitive_level TEXT NOT NULL DEFAULT 'normal',
    content_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (connector_id, account_id, remote_id)
);

CREATE INDEX IF NOT EXISTS connector_external_items_time
    ON connector_external_items(connector_id, account_id, occurred_at);
"""


DatabaseTarget = Union[str, Path, sqlite3.Connection]


def ensure_schema(target: DatabaseTarget) -> sqlite3.Connection:
    """Create connector tables and return a usable connection.

    If a path is supplied the caller owns the returned connection.  If an
    existing connection is supplied it remains open and is simply returned.
    """

    connection = (
        target
        if isinstance(target, sqlite3.Connection)
        else sqlite3.connect(str(Path(target)), check_same_thread=False)
    )
    connection.executescript(_DDL)
    row = connection.execute("SELECT version FROM connector_schema_meta LIMIT 1").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO connector_schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
        )
    elif int(row[0]) in {1, 2}:
        # ``_DDL`` above has already added the newer tables.  v3 also removes
        # human content from the plaintext proposal summary column; the full
        # preview remains exclusively inside ``payload_ciphertext``.
        connection.execute(
            """UPDATE connector_action_proposals
               SET summary=CASE
                   WHEN connector_id IN ('calendar','google-calendar')
                       THEN 'Calendar operation preview'
                   WHEN connector_id='slack' THEN 'Slack reply preview'
                   ELSE 'Connector operation preview'
               END"""
        )
        connection.execute("UPDATE connector_schema_meta SET version=?", (SCHEMA_VERSION,))
    elif int(row[0]) != SCHEMA_VERSION:
        raise RuntimeError("Unsupported connector schema version: %s" % row[0])
    connection.commit()
    return connection
