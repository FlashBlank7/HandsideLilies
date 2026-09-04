from __future__ import annotations

"""Application-facing, local-only connector runtime.

This layer persists only policy and bounded metadata in the project database;
message/event content is encrypted by :class:`EncryptedContentVault` and its
master key remains in Windows Credential Manager.  Network access happens only
through the explicitly injected transport after the user starts a connector.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping
from urllib.parse import urlencode

from .assistance import (
    AssistanceMaterial,
    AssistanceMaterialStore,
    AssistanceUnavailableError,
    bounded_external_content,
)
from .google_calendar import (
    CALENDAR_READ_SCOPE,
    GoogleCalendarConnector,
    PkceAuthorization,
    RollingSyncWindow,
)
from .http import HttpTransport
from .policy import (
    ActionMode,
    ActionProposal,
    ConnectorPolicy,
    IngressMode,
    ModelAccessMode,
    ProposalStatus,
    RetentionMode,
)
from .schema import ensure_schema
from .security import EncryptedContentVault, SecretStore
from .slack import (
    USER_MESSAGE_SCOPES,
    USER_WRITE_SCOPES,
    SlackConnector,
    SlackEvent,
    SlackEventFilter,
    SlackManifestOptions,
    SlackPkceAuthorization,
)


CALENDAR_LIST_READ_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
CALENDAR_OWNED_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ScopeMode(StrEnum):
    NECESSARY = "necessary"
    SELECTED = "selected"
    BROAD = "broad"


class InterruptionMode(StrEnum):
    QUIET = "quiet"
    PRIORITY = "priority"
    IMMEDIATE = "immediate"


class RetentionTier(StrEnum):
    METADATA = "metadata"
    SEARCHABLE_SUMMARY = "searchable-summary"
    EXTENDED_CACHE = "extended-cache"


class AssistanceMode(StrEnum):
    REMINDER = "reminder"
    ASSIST = "assist"
    CONFIRM_EXECUTE = "confirm-execute"


_LABELS = {
    ScopeMode.NECESSARY.value: "必要",
    ScopeMode.SELECTED.value: "精选",
    ScopeMode.BROAD.value: "广泛",
    InterruptionMode.QUIET.value: "安静",
    InterruptionMode.PRIORITY.value: "优先",
    InterruptionMode.IMMEDIATE.value: "即时",
    RetentionTier.METADATA.value: "元数据",
    RetentionTier.SEARCHABLE_SUMMARY.value: "可搜索摘要",
    RetentionTier.EXTENDED_CACHE.value: "扩展缓存",
    AssistanceMode.REMINDER.value: "提醒",
    AssistanceMode.ASSIST.value: "协助",
    AssistanceMode.CONFIRM_EXECUTE.value: "确认执行",
}


@dataclass(frozen=True, slots=True)
class ConnectorPolicyAxes:
    scope: ScopeMode = ScopeMode.NECESSARY
    interruption: InterruptionMode = InterruptionMode.QUIET
    retention: RetentionTier = RetentionTier.METADATA
    assistance: AssistanceMode = AssistanceMode.ASSIST
    selected_sources: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ConnectorPolicyAxes":
        raw = dict(value or {})
        aliases = {
            "必要": ScopeMode.NECESSARY.value,
            "精选": ScopeMode.SELECTED.value,
            "广泛": ScopeMode.BROAD.value,
            "安静": InterruptionMode.QUIET.value,
            "优先": InterruptionMode.PRIORITY.value,
            "即时": InterruptionMode.IMMEDIATE.value,
            "元数据": RetentionTier.METADATA.value,
            "可搜索摘要": RetentionTier.SEARCHABLE_SUMMARY.value,
            "扩展缓存": RetentionTier.EXTENDED_CACHE.value,
            "提醒": AssistanceMode.REMINDER.value,
            "协助": AssistanceMode.ASSIST.value,
            "确认执行": AssistanceMode.CONFIRM_EXECUTE.value,
        }

        def normalized(name: str, default: str) -> str:
            text = str(raw.get(name, default)).strip()
            return aliases.get(text, text)

        sources = raw.get("selectedSources", raw.get("selected_sources", ()))
        if not isinstance(sources, (list, tuple)):
            sources = ()
        return cls(
            ScopeMode(normalized("scope", ScopeMode.NECESSARY.value)),
            InterruptionMode(normalized("interruption", InterruptionMode.QUIET.value)),
            RetentionTier(normalized("retention", RetentionTier.METADATA.value)),
            AssistanceMode(normalized("assistance", AssistanceMode.ASSIST.value)),
            tuple(dict.fromkeys(str(item)[:160] for item in sources if str(item).strip())),
        )

    def to_dict(self, *, localized: bool = True) -> dict[str, Any]:
        values = {
            "scope": self.scope.value,
            "interruption": self.interruption.value,
            "retention": self.retention.value,
            "assistance": self.assistance.value,
            "selectedSources": list(self.selected_sources),
        }
        if localized:
            for key in ("scope", "interruption", "retention", "assistance"):
                values[key] = _LABELS[values[key]]
        return values

    def connector_policy(self) -> ConnectorPolicy:
        ingress = (
            IngressMode.METADATA_ONLY
            if self.retention is RetentionTier.METADATA
            else IngressMode.SELECTED_CONTENT
        )
        retention = (
            RetentionMode.NONE
            if self.retention is RetentionTier.METADATA
            else RetentionMode.ENCRYPTED_PERSISTENT
        )
        actions = {
            AssistanceMode.REMINDER: ActionMode.DISABLED,
            AssistanceMode.ASSIST: ActionMode.PROPOSE_ONLY,
            AssistanceMode.CONFIRM_EXECUTE: ActionMode.REQUIRE_CONFIRMATION,
        }[self.assistance]
        return ConnectorPolicy(ingress, retention, ModelAccessMode.DENY, actions)


def proposal_public(
    proposal: ActionProposal, *, before: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": proposal.proposal_id,
        "proposalId": proposal.proposal_id,
        "connector": "calendar" if proposal.connector_id == "google-calendar" else proposal.connector_id,
        "action": proposal.action,
        "target": proposal.target,
        "summary": proposal.summary,
        "before": dict(before or {}),
        "after": proposal.mutable_payload(),
        "sourceVersion": proposal.source_etag or "",
        "risk": "mutate",
        "status": proposal.status.value,
        "createdAt": proposal.created_at.isoformat(),
        "expiresAt": proposal.expires_at.isoformat() if proposal.expires_at else "",
    }


class _RuntimeBase:
    connector_id = ""

    def __init__(
        self,
        database: Any,
        *,
        account_id: str,
        secret_store: SecretStore,
        transport: HttpTransport,
    ) -> None:
        self.database = database
        self.account_id = str(account_id)
        self.secret_store = secret_store
        self.transport = transport
        self.connection = ensure_schema(database.path)
        self.vault = EncryptedContentVault(secret_store, database=self.connection)
        self._assistance_materials = AssistanceMaterialStore()
        self._proposals: dict[str, ActionProposal] = {}
        self._proposal_before: dict[str, dict[str, Any]] = {}
        self._proposal_lock = threading.RLock()

    @property
    def axes(self) -> ConnectorPolicyAxes:
        value = self.database.get_setting(f"connector_{self.connector_id}_policy", {})
        return ConnectorPolicyAxes.from_mapping(value if isinstance(value, dict) else {})

    def _configuration_policy(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """A configuration patch cannot silently reset omitted consent axes."""
        policy = self.axes.to_dict(localized=False)
        incoming = value.get("policy")
        if incoming is not None:
            if not isinstance(incoming, Mapping):
                raise ValueError("Connector policy must be an object")
            policy.update(incoming)
            if "selected_sources" in incoming and "selectedSources" not in incoming:
                policy["selectedSources"] = incoming["selected_sources"]
        return ConnectorPolicyAxes.from_mapping(policy).to_dict(localized=False)

    def set_policy(self, value: Mapping[str, Any]) -> dict[str, Any]:
        previous = self.axes
        axes = ConnectorPolicyAxes.from_mapping(value)
        # A policy edit is a new consent boundary.  Outstanding plaintext
        # capabilities must not survive it, including a retention downgrade.
        self._assistance_materials.invalidate_all()
        self.database.set_setting(f"connector_{self.connector_id}_policy", axes.to_dict(localized=False))
        self.connection.execute(
            """INSERT INTO connector_policies
               (connector_id,account_id,scope,interruption,retention,assistance,
                selected_sources_json,updated_at) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(connector_id,account_id) DO UPDATE SET
                 scope=excluded.scope,interruption=excluded.interruption,
                 retention=excluded.retention,assistance=excluded.assistance,
                 selected_sources_json=excluded.selected_sources_json,updated_at=excluded.updated_at""",
            (
                self.connector_id,
                self.account_id,
                axes.scope.value,
                axes.interruption.value,
                axes.retention.value,
                axes.assistance.value,
                json.dumps(list(axes.selected_sources), ensure_ascii=False),
                _now(),
            ),
        )
        self.connection.commit()
        if (
            previous.retention is RetentionTier.EXTENDED_CACHE
            and axes.retention is RetentionTier.SEARCHABLE_SUMMARY
        ):
            self._reduce_cached_content_to_summaries()
        elif (
            previous.retention is not RetentionTier.METADATA
            and axes.retention is RetentionTier.METADATA
        ):
            self.clear_cached_content(keep_metadata=True)
        self._enforce_source_scope(axes)
        return axes.to_dict()

    def _enforce_source_scope(self, axes: ConnectorPolicyAxes) -> None:
        if axes.scope is ScopeMode.BROAD:
            return
        selected = set(axes.selected_sources)
        rows = self.connection.execute(
            """SELECT remote_id,source_id,content_id,metadata_json
               FROM connector_external_items WHERE connector_id=? AND account_id=?""",
            (self.connector_id, self.account_id),
        ).fetchall()
        removed: list[str] = []
        for row in rows:
            source_id = str(row[1] or "")
            try:
                metadata = json.loads(row[3] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if self.connector_id == "calendar":
                allowed = source_id == "primary" if axes.scope is ScopeMode.NECESSARY else source_id in selected
            else:
                essential = bool(metadata.get("isDirect") or metadata.get("isMention"))
                allowed = essential or (
                    axes.scope is ScopeMode.SELECTED and source_id in selected
                )
            if allowed:
                continue
            if row[2]:
                self.vault.delete(str(row[2]), namespace=self.connector_id)
            self._assistance_materials.invalidate(
                provider=self.connector_id, remote_id=str(row[0])
            )
            removed.append(str(row[0]))
        if removed:
            self.connection.executemany(
                """DELETE FROM connector_external_items
                   WHERE connector_id=? AND account_id=? AND remote_id=?""",
                ((self.connector_id, self.account_id, remote_id) for remote_id in removed),
            )
            self.connection.commit()

    def _save_account(
        self,
        *,
        connected: bool,
        display_name: str = "",
        last_sync_at: str | None = None,
        error: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO connector_accounts
               (connector_id,account_id,display_name,connected,last_sync_at,last_error,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(connector_id,account_id) DO UPDATE SET
                 display_name=excluded.display_name,connected=excluded.connected,
                 last_sync_at=COALESCE(excluded.last_sync_at,connector_accounts.last_sync_at),
                 last_error=excluded.last_error,metadata_json=excluded.metadata_json,
                 updated_at=excluded.updated_at""",
            (
                self.connector_id,
                self.account_id,
                str(display_name)[:200],
                int(connected),
                last_sync_at,
                str(error)[:1000],
                json.dumps(dict(metadata or {}), ensure_ascii=False),
                _now(),
            ),
        )
        self.connection.commit()

    def status(self) -> dict[str, Any]:
        self._purge_expired_content()
        row = self.connection.execute(
            "SELECT display_name,connected,last_sync_at,last_error,metadata_json FROM connector_accounts WHERE connector_id=? AND account_id=?",
            (self.connector_id, self.account_id),
        ).fetchone()
        value = {
            "provider": self.connector_id,
            "connected": False,
            "state": "not-configured",
            "lastSyncAt": "",
            "error": "",
            "policy": self.axes.to_dict(),
            "policyCanonical": self.axes.to_dict(localized=False),
        }
        if row:
            value.update(
                displayName=str(row[0] or ""),
                connected=bool(row[1]),
                state="connected" if row[1] else "configured",
                lastSyncAt=str(row[2] or ""),
                error=str(row[3] or ""),
            )
            try:
                value.update(json.loads(row[4] or "{}"))
            except (TypeError, ValueError):
                pass
        return value

    def _store_item(
        self,
        *,
        remote_id: str,
        source_id: str,
        occurred_at: str,
        end_at: str = "",
        state: str = "",
        link: str = "",
        metadata: Mapping[str, Any] | None = None,
        content: Mapping[str, Any] | None = None,
    ) -> None:
        self._purge_expired_content()
        # A handle issued for an older version must not outlive replacement of
        # the selected remote item.
        self._assistance_materials.invalidate(
            provider=self.connector_id, remote_id=str(remote_id)
        )
        previous = self.connection.execute(
            """SELECT content_id FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND remote_id=?""",
            (self.connector_id, self.account_id, remote_id),
        ).fetchone()
        previous_content_id = str(previous[0]) if previous and previous[0] else ""
        content_id: str | None = None
        if content and self.axes.retention is not RetentionTier.METADATA:
            content_id = f"{self.connector_id}:{self.account_id}:{remote_id}"
            protected_content = dict(content)
            if self.axes.retention is RetentionTier.SEARCHABLE_SUMMARY:
                protected_content = {
                    "summary": bounded_external_content(
                        protected_content,
                        provider=self.connector_id,
                        limit=1_200,
                    )
                }
            self.vault.put(
                content_id,
                json.dumps(protected_content, ensure_ascii=False).encode("utf-8"),
                namespace=self.connector_id,
                metadata={"remoteId": remote_id},
            )
        elif previous_content_id:
            self.vault.delete(previous_content_id, namespace=self.connector_id)
        self.connection.execute(
            """INSERT INTO connector_external_items
               (connector_id,account_id,remote_id,source_id,occurred_at,end_at,state,link,
                sensitive_level,content_id,metadata_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(connector_id,account_id,remote_id)
               DO UPDATE SET source_id=excluded.source_id,occurred_at=excluded.occurred_at,
                 end_at=excluded.end_at,state=excluded.state,link=excluded.link,
                 content_id=excluded.content_id,
                 metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (
                self.connector_id, self.account_id, remote_id, source_id, occurred_at,
                end_at, state, link, "normal", content_id,
                json.dumps(dict(metadata or {}), ensure_ascii=False), _now(),
            ),
        )
        self.connection.commit()

    def clear_cached_content(self, *, keep_metadata: bool = True) -> dict[str, int]:
        """Clear sensitive cached content without touching credentials.

        Pending previews also contain encrypted before/after values, so a
        retention downgrade or explicit clear invalidates and removes them
        instead of leaving a second, less visible copy of the content.
        """

        self._assistance_materials.invalidate_all()
        rows = self.connection.execute(
            """SELECT content_id FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND content_id IS NOT NULL""",
            (self.connector_id, self.account_id),
        ).fetchall()
        deleted = 0
        for row in rows:
            if row[0] and self.vault.delete(str(row[0]), namespace=self.connector_id):
                deleted += 1
        if keep_metadata:
            self.connection.execute(
                """UPDATE connector_external_items SET content_id=NULL,updated_at=?
                   WHERE connector_id=? AND account_id=?""",
                (_now(), self.connector_id, self.account_id),
            )
        else:
            self.connection.execute(
                "DELETE FROM connector_external_items WHERE connector_id=? AND account_id=?",
                (self.connector_id, self.account_id),
            )
        removed_proposals = self._delete_pending_proposals()
        self.connection.commit()
        audits_redacted = self.database.redact_connector_audits(self.connector_id)
        return {
            "encryptedRecordsDeleted": deleted,
            "proposalsDeleted": max(0, int(removed_proposals)),
            "auditsRedacted": audits_redacted,
        }

    def _reduce_cached_content_to_summaries(self) -> dict[str, int]:
        """Irreversibly reduce extended cached bodies to bounded summaries.

        A retention downgrade must change the bytes at rest, not merely hide
        fields at the UI boundary.  If an old record cannot be decrypted or
        decoded safely, fail closed by deleting that record's encrypted body.
        Pending proposals and issued assistance capabilities are invalidated
        because they may still contain the former extended representation.
        """

        self._assistance_materials.invalidate_all()
        rows = self.connection.execute(
            """SELECT remote_id,content_id FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND content_id IS NOT NULL""",
            (self.connector_id, self.account_id),
        ).fetchall()
        rewritten = 0
        deleted = 0
        for remote_id, content_id in rows:
            content_key = str(content_id or "")
            if not content_key:
                continue
            entry = self.vault.get(content_key, namespace=self.connector_id)
            protected: dict[str, Any] | None = None
            if entry:
                try:
                    decoded = json.loads(entry.content.decode("utf-8"))
                    if isinstance(decoded, Mapping):
                        protected = {
                            "summary": bounded_external_content(
                                decoded,
                                provider=self.connector_id,
                                limit=1_200,
                            )
                        }
                except (UnicodeError, TypeError, ValueError):
                    protected = None
            if protected is not None:
                self.vault.put(
                    content_key,
                    json.dumps(protected, ensure_ascii=False).encode("utf-8"),
                    namespace=self.connector_id,
                    metadata={"remoteId": str(remote_id)},
                )
                rewritten += 1
                continue
            if self.vault.delete(content_key, namespace=self.connector_id):
                deleted += 1
            self.connection.execute(
                """UPDATE connector_external_items SET content_id=NULL,updated_at=?
                   WHERE connector_id=? AND account_id=? AND remote_id=?""",
                (_now(), self.connector_id, self.account_id, str(remote_id)),
            )
        removed_proposals = self._delete_pending_proposals()
        self.connection.commit()
        audits_redacted = self.database.redact_connector_audits(self.connector_id)
        return {
            "encryptedRecordsRewritten": rewritten,
            "encryptedRecordsDeleted": deleted,
            "proposalsDeleted": max(0, int(removed_proposals)),
            "auditsRedacted": audits_redacted,
        }

    def _delete_pending_proposals(self) -> int:
        with self._proposal_lock:
            proposal_connectors = {
                self.connector_id,
                "google-calendar" if self.connector_id == "calendar" else self.connector_id,
            }
            proposal_ids = [
                proposal_id
                for proposal_id, proposal in self._proposals.items()
                if proposal.connector_id in proposal_connectors
            ]
            for proposal_id in proposal_ids:
                self._proposals.pop(proposal_id, None)
                self._proposal_before.pop(proposal_id, None)
            placeholders = ",".join("?" for _ in proposal_connectors)
            return int(
                self.connection.execute(
                    f"DELETE FROM connector_action_proposals WHERE connector_id IN ({placeholders})",
                    tuple(sorted(proposal_connectors)),
                ).rowcount
            )

    def close(self) -> None:
        self._assistance_materials.invalidate_all()
        self.connection.close()

    def _clear_transport_metadata(self) -> None:
        """Remove account-derived cursors and dedupe rows on full disconnect."""

        connector_ids = (
            ("calendar", "google-calendar")
            if self.connector_id == "calendar"
            else (self.connector_id,)
        )
        marks = ",".join("?" for _ in connector_ids)
        self.connection.execute(
            f"""DELETE FROM connector_sync_state
                WHERE connector_id IN ({marks})
                  AND (account_id=? OR account_id LIKE ?)""",
            (*connector_ids, self.account_id, f"{self.account_id}:%"),
        )
        self.connection.execute(
            f"DELETE FROM connector_event_dedupe WHERE connector_id IN ({marks})",
            connector_ids,
        )
        self.connection.commit()

    def items(self, *, limit: int = 30) -> list[dict[str, Any]]:
        self._purge_expired_content()
        rows = self.connection.execute(
            """SELECT remote_id,source_id,occurred_at,end_at,state,link,content_id,metadata_json
               FROM connector_external_items WHERE connector_id=? AND account_id=?
               ORDER BY occurred_at DESC LIMIT ?""",
            (self.connector_id, self.account_id, max(1, min(int(limit), 100))),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row[7] or "{}")
            content: dict[str, Any] = {}
            if row[6] and self.axes.retention is not RetentionTier.METADATA:
                entry = self.vault.get(str(row[6]), namespace=self.connector_id)
                if entry:
                    try:
                        content = json.loads(entry.content.decode("utf-8"))
                    except (UnicodeError, ValueError):
                        content = {}
            result.append(
                {
                    "id": str(row[0]), "remoteId": str(row[0]), "sourceId": str(row[1]),
                    "occurredAt": str(row[2]), "endAt": str(row[3]), "state": str(row[4]),
                    "link": str(row[5]), **metadata, **content,
                }
            )
        return result

    def _purge_expired_content(self) -> None:
        if self.axes.retention is not RetentionTier.EXTENDED_CACHE:
            return
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        rows = self.connection.execute(
            """SELECT remote_id,content_id FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND content_id IS NOT NULL
                 AND updated_at < ?""",
            (self.connector_id, self.account_id, cutoff),
        ).fetchall()
        for row in rows:
            if row[1]:
                self.vault.delete(str(row[1]), namespace=self.connector_id)
            self._assistance_materials.invalidate(
                provider=self.connector_id, remote_id=str(row[0])
            )
        self.connection.execute(
            """UPDATE connector_external_items SET content_id=NULL
               WHERE connector_id=? AND account_id=? AND updated_at < ?""",
            (self.connector_id, self.account_id, cutoff),
        )
        self.connection.commit()

    def metadata_items(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Return the model-safe projection without decrypted content."""

        sensitive_keys = {
            "text", "summary", "description", "location", "body", "detail", "draft"
        }
        return [
            {key: value for key, value in item.items() if key not in sensitive_keys}
            for item in self.items(limit=limit)
        ]

    def issue_assistance(
        self,
        event_id: str,
        *,
        ttl: timedelta | None = None,
        now: datetime | None = None,
    ) -> AssistanceMaterial:
        """Issue one short-lived handle for one explicitly selected item.

        This path intentionally performs one primary-key lookup instead of
        calling :meth:`items`, so adjacent retained bodies are never decrypted
        or copied into the material store.
        """

        axes = self.axes
        if axes.assistance not in {
            AssistanceMode.ASSIST,
            AssistanceMode.CONFIRM_EXECUTE,
        }:
            raise AssistanceUnavailableError(
                "connector assistance is disabled by the current policy"
            )
        if axes.retention is RetentionTier.METADATA:
            raise AssistanceUnavailableError(
                "connector metadata retention contains no assistance content"
            )

        remote_id = str(event_id)
        if not remote_id or len(remote_id) > 2_048:
            raise KeyError("connector event is unavailable")
        row = self.connection.execute(
            """SELECT source_id,occurred_at,content_id
               FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND remote_id=?""",
            (self.connector_id, self.account_id, remote_id),
        ).fetchone()
        if row is None:
            raise KeyError("connector event is unavailable")

        content_id = str(row[2] or "")
        expected_content_id = f"{self.connector_id}:{self.account_id}:{remote_id}"
        if not content_id or content_id != expected_content_id:
            raise AssistanceUnavailableError(
                "selected connector item has no isolated retained content"
            )
        try:
            entry = self.vault.get(content_id, namespace=self.connector_id)
        except Exception as error:
            raise AssistanceUnavailableError(
                "selected connector item content is unavailable"
            ) from error
        if entry is None or str(entry.metadata.get("remoteId", "")) != remote_id:
            raise AssistanceUnavailableError(
                "selected connector item has no isolated retained content"
            )
        try:
            retained = json.loads(entry.content.decode("utf-8"))
        except (UnicodeError, TypeError, ValueError) as error:
            raise AssistanceUnavailableError(
                "selected connector item content is unavailable"
            ) from error
        if not isinstance(retained, Mapping):
            raise AssistanceUnavailableError(
                "selected connector item content is unavailable"
            )
        content = bounded_external_content(retained, provider=self.connector_id)
        if not content:
            raise AssistanceUnavailableError(
                "selected connector item has no retained text or summary"
            )
        return self._assistance_materials.issue(
            {
                "provider": self.connector_id,
                "remoteId": remote_id,
                "sourceId": str(row[0] or ""),
                "occurredAt": str(row[1] or ""),
                "content": content,
                "untrusted": True,
            },
            ttl=ttl,
            now=now,
        )

    # Explicit long form for callers that prefer the domain noun in the API.
    issue_assistance_material = issue_assistance

    def consume_assistance(
        self,
        material: AssistanceMaterial,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self._assistance_materials.consume(material, now=now)

    def _save_proposal(
        self,
        proposal: ActionProposal,
        *,
        before: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._proposal_lock:
            self._proposals[proposal.proposal_id] = proposal
            before_value = dict(before or {})
            self._proposal_before[proposal.proposal_id] = before_value
            payload = json.dumps(
                {"before": before_value, "after": proposal.mutable_payload()},
                ensure_ascii=False,
            ).encode("utf-8")
            envelope = self.vault.encrypt(
                payload, associated_data=f"proposal:{proposal.proposal_id}"
            )
            persisted_summary = {
                "google-calendar": "Calendar operation preview",
                "calendar": "Calendar operation preview",
                "slack": "Slack reply preview",
            }.get(proposal.connector_id, "Connector operation preview")
            self.connection.execute(
                """INSERT OR REPLACE INTO connector_action_proposals
                   (proposal_id,connector_id,action,target,payload_ciphertext,summary,status,
                    requires_confirmation,source_etag,created_at,expires_at,resolved_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    proposal.proposal_id,
                    proposal.connector_id,
                    proposal.action,
                    proposal.target,
                    envelope,
                    persisted_summary,
                    proposal.status.value,
                    int(proposal.requires_confirmation),
                    proposal.source_etag,
                    proposal.created_at.isoformat(),
                    proposal.expires_at.isoformat() if proposal.expires_at else None,
                ),
            )
            self.connection.commit()
            return proposal_public(proposal, before=before_value)

    def proposal(self, proposal_id: str) -> ActionProposal:
        with self._proposal_lock:
            proposal = self._proposals.get(str(proposal_id))
            if proposal is None:
                raise KeyError("proposal is no longer active")
            return proposal

    def reject(self, proposal_id: str) -> dict[str, Any]:
        with self._proposal_lock:
            proposal = self.proposal(proposal_id).reject()
            self._proposals[proposal_id] = proposal
            self.connection.execute(
                "UPDATE connector_action_proposals SET status=?,resolved_at=? WHERE proposal_id=?",
                (proposal.status.value, proposal.resolved_at.isoformat(), proposal_id),
            )
            self.connection.commit()
            return proposal_public(
                proposal, before=self._proposal_before.get(str(proposal_id), {})
            )

    def _claim_proposal_for_execution(self, proposal_id: str) -> ActionProposal:
        """Atomically claim one preview before any external write begins."""

        with self._proposal_lock:
            confirmed = self.proposal(proposal_id).confirm()
            self._proposals[proposal_id] = confirmed
            self.connection.execute(
                "UPDATE connector_action_proposals SET status=?,resolved_at=? WHERE proposal_id=?",
                (
                    confirmed.status.value,
                    confirmed.resolved_at.isoformat(),
                    proposal_id,
                ),
            )
            self.connection.commit()
            return confirmed

    def _finish_proposal_execution(
        self, proposal_id: str, executed: ActionProposal
    ) -> dict[str, Any]:
        with self._proposal_lock:
            self._proposals[proposal_id] = executed
            self.connection.execute(
                "UPDATE connector_action_proposals SET status=?,resolved_at=? WHERE proposal_id=?",
                (
                    executed.status.value,
                    executed.resolved_at.isoformat() if executed.resolved_at else _now(),
                    proposal_id,
                ),
            )
            self.connection.commit()
            return proposal_public(
                executed, before=self._proposal_before.get(str(proposal_id), {})
            )

    def _fail_claimed_proposal(self, proposal_id: str) -> None:
        """Fail closed after an uncertain network outcome; never retry in place."""

        with self._proposal_lock:
            current = self._proposals.get(str(proposal_id))
            if current is None or current.status is not ProposalStatus.CONFIRMED:
                return
            expired = current.expire()
            self._proposals[proposal_id] = expired
            self.connection.execute(
                "UPDATE connector_action_proposals SET status=?,resolved_at=? WHERE proposal_id=?",
                (
                    expired.status.value,
                    expired.resolved_at.isoformat() if expired.resolved_at else _now(),
                    proposal_id,
                ),
            )
            self.connection.commit()


class CalendarRuntime(_RuntimeBase):
    connector_id = "calendar"

    def _client_id(self) -> str:
        return str(self.database.get_setting("connector_calendar_client_id", ""))

    def _connector(self) -> GoogleCalendarConnector:
        client_id = self._client_id()
        if not client_id:
            raise RuntimeError("Google Desktop OAuth client_id has not been configured")
        return GoogleCalendarConnector(
            client_id=client_id,
            secret_store=self.secret_store,
            policy=self.axes.connector_policy(),
            transport=self.transport,
            account_id=self.account_id,
            database=self.connection,
        )

    def configure(self, value: Mapping[str, Any]) -> dict[str, Any]:
        # ``clientId`` is public OAuth application configuration, not a
        # credential.  Reuse the saved value when the UI submits only a policy
        # edit so reconnecting never requires the user to paste it again.
        client_id = str(value.get("clientId", "")).strip() or self._client_id().strip()
        if not client_id or len(client_id) > 500:
            raise ValueError("Google Desktop OAuth client_id is required")
        policy = self._configuration_policy(value)
        self.database.set_setting("connector_calendar_client_id", client_id)
        if "policy" in value:
            self.set_policy(policy)
        self._save_account(connected=False, metadata={"configured": True})
        return self.status()

    def authorization(self, redirect_uri: str, *, state: str | None = None) -> PkceAuthorization:
        scopes = [CALENDAR_LIST_READ_SCOPE, CALENDAR_READ_SCOPE]
        if self.axes.assistance is AssistanceMode.CONFIRM_EXECUTE:
            scopes.append(CALENDAR_OWNED_WRITE_SCOPE)
        return self._connector().create_authorization(
            client_id=self._client_id(),
            redirect_uri=redirect_uri,
            scopes=tuple(scopes),
            state=state,
        )

    def exchange(self, code: str, authorization: PkceAuthorization) -> dict[str, Any]:
        result = self._connector().exchange_code(code=code, authorization=authorization)
        self._save_account(connected=True, metadata={"configured": True})
        return result

    def disconnect(self) -> dict[str, Any]:
        """Revoke local Calendar access and content, preserving OAuth setup."""

        self._connector().disconnect()
        self.clear_cached_content(keep_metadata=False)
        self._clear_transport_metadata()
        self._save_account(connected=False, metadata={"configured": True})
        return self.status()

    def status(self) -> dict[str, Any]:
        value = super().status()
        client_id = self._client_id()
        configured = bool(client_id)
        connected = False
        if configured:
            try:
                connected = bool(self._connector().load_tokens())
            except Exception:
                connected = False
        # This projection is deliberately allow-listed.  Tokens remain solely
        # in SecretStore and can never be reflected into QML.
        value.update(
            configured=configured,
            connected=connected,
            configuration={"clientId": client_id},
        )
        value["state"] = "connected" if connected else ("configured" if configured else "not-configured")
        return value

    def calendars(self) -> list[dict[str, Any]]:
        cached = self.database.get_setting("connector_calendar_sources", [])
        if isinstance(cached, list) and cached:
            selected = set(self.axes.selected_sources or ("primary",))
            return [
                {**dict(value), "selected": str(value.get("id", "")) in selected}
                for value in cached
                if isinstance(value, Mapping)
            ]
        sources = self.axes.selected_sources or ("primary",)
        return [
            {"id": value, "name": "主日历" if value == "primary" else value, "selected": True}
            for value in sources
        ]

    def refresh(self) -> dict[str, Any]:
        connector = self._connector()
        if self.axes.scope is ScopeMode.BROAD:
            remote_sources = [dict(item) for item in connector.list_calendars()]
            self.database.set_setting(
                "connector_calendar_sources",
                [
                    {
                        "id": str(item.get("id", "")),
                        "name": str(item.get("summary", item.get("id", ""))),
                        "primary": bool(item.get("primary", False)),
                        "accessRole": str(item.get("accessRole", "")),
                        "timeZone": str(item.get("timeZone", "")),
                    }
                    for item in remote_sources
                    if item.get("id")
                ],
            )
            sources = tuple(str(item["id"]) for item in remote_sources if item.get("id"))
        else:
            sources = self.axes.selected_sources or ("primary",)
        total = 0
        not_modified = True
        reset_performed = False
        for source_id in sources:
            checkpoint = connector.load_checkpoint(source_id)
            selected_content = (
                ("*",) if self.axes.retention is not RetentionTier.METADATA else ()
            )
            result = connector.sync(
                calendar_id=source_id,
                checkpoint=checkpoint,
                window=RollingSyncWindow(),
                selected_event_ids=selected_content,
            )
            if result.reset_required:
                reset_performed = True
                self.connection.execute(
                    "DELETE FROM connector_sync_state WHERE connector_id=? AND account_id=?",
                    ("google-calendar", f"{self.account_id}:{source_id}"),
                )
                self.connection.commit()
                result = connector.sync(
                    calendar_id=source_id,
                    checkpoint=None,
                    window=RollingSyncWindow(),
                    selected_event_ids=selected_content,
                )
            not_modified = not_modified and result.not_modified
            total += len(result.events)
            for event in result.events:
                start = event.get("start", {}) if isinstance(event.get("start"), Mapping) else {}
                end = event.get("end", {}) if isinstance(event.get("end"), Mapping) else {}
                remote_id = str(event.get("id", ""))
                if not remote_id:
                    continue
                content = {
                    key: event[key]
                    for key in ("summary", "description", "location", "reminders")
                    if key in event
                }
                self._store_item(
                    remote_id=remote_id,
                    source_id=source_id,
                    occurred_at=str(start.get("dateTime", start.get("date", ""))),
                    end_at=str(end.get("dateTime", end.get("date", ""))),
                    state=str(event.get("status", "")),
                    link=str(event.get("htmlLink", "")),
                    metadata={
                        "etag": event.get("etag", ""),
                        "recurringEventId": event.get("recurringEventId", ""),
                    },
                    content=content,
                )
        self._save_account(connected=True, last_sync_at=_now(), metadata={"configured": True})
        return {
            "items": total,
            "notModified": not_modified,
            "resetPerformed": reset_performed,
            "status": self.status(),
        }

    def upcoming(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.items(limit=limit)

    def open_event(self, event_id: str) -> dict[str, Any]:
        item = next((value for value in self.items(limit=100) if value["id"] == event_id), None)
        if not item or not item.get("link"):
            raise KeyError("calendar event link is unavailable")
        return {"url": item["link"]}

    @staticmethod
    def _validated_event_change(
        change: Mapping[str, Any], *, creating: bool
    ) -> dict[str, Any]:
        allowed = {
            "summary", "description", "location", "start", "end", "reminders",
            "colorId", "transparency", "visibility",
        }
        unknown = set(change) - allowed
        if unknown:
            raise ValueError("Unsupported Calendar fields: %s" % ", ".join(sorted(unknown)))
        result = dict(change)
        if creating and ("start" not in result or "end" not in result):
            raise ValueError("Calendar create requires start and end")
        for key in ("start", "end"):
            if key not in result:
                continue
            value = result[key]
            if not isinstance(value, Mapping):
                raise ValueError("Calendar %s must be an object" % key)
            part = dict(value)
            if set(part) - {"date", "dateTime", "timeZone"}:
                raise ValueError("Calendar %s contains unsupported fields" % key)
            if bool(part.get("date")) == bool(part.get("dateTime")):
                raise ValueError("Calendar %s needs exactly one of date/dateTime" % key)
            if part.get("date") and part.get("timeZone"):
                raise ValueError("All-day Calendar values must not include timeZone")
            result[key] = part
        reminders = result.get("reminders")
        if reminders is not None:
            if not isinstance(reminders, Mapping):
                raise ValueError("Calendar reminders must be an object")
            reminder_value = dict(reminders)
            if set(reminder_value) - {"useDefault", "overrides"}:
                raise ValueError("Calendar reminders contain unsupported fields")
            overrides = reminder_value.get("overrides", [])
            if not isinstance(overrides, list) or len(overrides) > 10:
                raise ValueError("Calendar reminder overrides are invalid")
            for item in overrides:
                if not isinstance(item, Mapping) or set(item) - {"method", "minutes"}:
                    raise ValueError("Calendar reminder override is invalid")
                if str(item.get("method", "")) not in {"popup", "email"}:
                    raise ValueError("Calendar reminder method is invalid")
                minutes = int(item.get("minutes", -1))
                if not 0 <= minutes <= 40320:
                    raise ValueError("Calendar reminder minutes are invalid")
        return result

    def propose_create(self, change: Mapping[str, Any]) -> dict[str, Any]:
        validated = self._validated_event_change(change, creating=True)
        return self._save_proposal(self._connector().propose_create(validated))

    def propose_update(self, event_id: str, change: Mapping[str, Any]) -> dict[str, Any]:
        item = next((value for value in self.items(limit=100) if value["id"] == event_id), None)
        if not item or not item.get("etag"):
            raise KeyError("calendar source ETag is unavailable")
        validated = self._validated_event_change(change, creating=False)
        before = {
            key: item.get(key)
            for key in ("summary", "description", "location", "start", "end", "reminders")
            if key in item
        }
        return self._save_proposal(
            self._connector().propose_update(event_id, validated, expected_etag=str(item["etag"])),
            before=before,
        )

    def confirm_and_execute(self, proposal_id: str) -> dict[str, Any]:
        confirmed = self._claim_proposal_for_execution(proposal_id)
        try:
            result = self._connector().execute(confirmed)
        except BaseException:
            self._fail_claimed_proposal(proposal_id)
            raise
        return self._finish_proposal_execution(proposal_id, result.proposal)


class SlackRuntime(_RuntimeBase):
    connector_id = "slack"

    def _configuration(self) -> dict[str, Any]:
        value = self.database.get_setting("connector_slack_configuration", {})
        return dict(value) if isinstance(value, dict) else {}

    def _connector(self) -> SlackConnector:
        value = self._configuration()
        client_id = str(value.get("clientId", ""))
        if not client_id:
            raise RuntimeError("Slack client_id has not been configured")
        return SlackConnector(
            client_id=client_id,
            secret_store=self.secret_store,
            event_filter=SlackEventFilter(
                authorized_user_id=str(value.get("currentUserId", "")),
                selected_channel_ids=frozenset(
                    self.axes.selected_sources
                    if self.axes.scope is ScopeMode.SELECTED
                    else ()
                ),
                allow_all_channels=self.axes.scope is ScopeMode.BROAD,
            ),
            policy=self.axes.connector_policy(),
            transport=self.transport,
            workspace_id=self.account_id,
            database=self.connection,
        )

    def configure(self, value: Mapping[str, Any]) -> dict[str, Any]:
        previous = self._configuration()
        # As with Calendar, the client ID is non-secret application metadata.
        # A blank policy-only submission must not erase a working setup.
        client_id = str(value.get("clientId", "")).strip() or str(
            previous.get("clientId", "")
        ).strip()
        if not client_id:
            raise ValueError("Slack client_id is required")
        current_user = str(value.get("currentUserId", previous.get("currentUserId", ""))).strip()
        policy = self._configuration_policy(value)
        if "selectedChannels" in value:
            selected = value["selectedChannels"]
            if isinstance(selected, str):
                selected = [part.strip() for part in selected.split(",") if part.strip()]
            if not isinstance(selected, (list, tuple)):
                raise ValueError("Selected Slack channels must be a list")
            policy["selectedSources"] = selected
        self.set_policy(policy)
        configuration = {
            "clientId": client_id,
            "currentUserId": current_user,
            "redirectUri": str(value.get("redirectUri", previous.get("redirectUri", ""))).strip(),
        }
        self.database.set_setting("connector_slack_configuration", configuration)
        connector = self._connector()
        app_token = str(value.get("xappToken", value.get("appToken", ""))).strip()
        if app_token:
            connector.save_app_token(app_token)
        self._save_account(connected=False, metadata={"configured": True})
        return self.status()

    def authorization(self) -> SlackPkceAuthorization:
        value = self._configuration()
        redirect_uri = str(value.get("redirectUri", ""))
        if not redirect_uri:
            raise ValueError("Slack redirect URI must be configured in the custom app")
        scopes = ["im:history", "channels:history"]
        if self.axes.scope is not ScopeMode.NECESSARY:
            scopes.extend(scope for scope in USER_MESSAGE_SCOPES if scope not in scopes)
        if self.axes.assistance is AssistanceMode.CONFIRM_EXECUTE:
            scopes.extend(USER_WRITE_SCOPES)
        return self._connector().create_authorization(
            client_id=str(value["clientId"]),
            redirect_uri=redirect_uri,
            user_scopes=tuple(dict.fromkeys(scopes)),
        )

    def exchange(self, code: str, authorization: SlackPkceAuthorization) -> dict[str, Any]:
        result = self._connector().exchange_code(code=code, authorization=authorization)
        self._save_account(connected=True, metadata={"configured": True})
        return result

    def disconnect(self) -> dict[str, Any]:
        """Remove Slack OAuth/xapp credentials and cache, preserving app setup."""

        self._connector().disconnect(
            remove_app_token=True,
            remove_signing_secret=True,
        )
        self.clear_cached_content(keep_metadata=False)
        self._clear_transport_metadata()
        self._save_account(connected=False, metadata={"configured": True})
        return self.status()

    def status(self) -> dict[str, Any]:
        value = super().status()
        configuration = self._configuration()
        configured = bool(configuration.get("clientId"))
        connected = False
        socket_ready = False
        if configured:
            try:
                connector = self._connector()
                connected = bool(connector.load_oauth_tokens())
                socket_ready = bool(self.secret_store.get_text(connector._app_token_key()))
            except Exception:
                pass
        # Never merge the stored xapp/OAuth material here.  QML receives only
        # the public fields needed to edit or reconnect the custom app.
        value.update(
            configured=configured,
            connected=connected,
            socketReady=socket_ready,
            workspace=self.account_id,
            configuration={
                "clientId": str(configuration.get("clientId", "")),
                "currentUserId": str(configuration.get("currentUserId", "")),
                "redirectUri": str(configuration.get("redirectUri", "")),
            },
        )
        value["state"] = "connected" if connected else ("configured" if configured else "not-configured")
        return value

    def manifest(self) -> dict[str, Any]:
        configuration = self._configuration()
        redirect_uri = str(configuration.get("redirectUri", ""))
        manifest = SlackConnector.generate_manifest(
            SlackManifestOptions(
                include_write_scope=self.axes.assistance is AssistanceMode.CONFIRM_EXECUTE,
                redirect_urls=(redirect_uri,) if redirect_uri else (),
            )
        )
        scopes = ["im:history", "channels:history"]
        if self.axes.scope is not ScopeMode.NECESSARY:
            scopes.extend(scope for scope in USER_MESSAGE_SCOPES if scope not in scopes)
        if self.axes.assistance is AssistanceMode.CONFIRM_EXECUTE:
            scopes.extend(USER_WRITE_SCOPES)
        manifest["oauth_config"]["scopes"]["user"] = list(dict.fromkeys(scopes))
        return manifest

    def socket_url(self) -> str:
        return self._connector().request_socket_url()

    def ingest(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        event = self._connector().ingest_event(payload)
        if event is None:
            return None
        content = {"text": event.text} if event.text else {}
        link = ""
        if event.team_id and event.channel_id and event.timestamp:
            link = "slack://channel?" + urlencode(
                {
                    "team": event.team_id,
                    "id": event.channel_id,
                    "message": event.timestamp,
                }
            )
        self._store_item(
            remote_id=event.event_id,
            source_id=event.channel_id,
            occurred_at=event.timestamp or _now(),
            state="unread",
            link=link,
            metadata={
                "teamId": event.team_id or "", "userId": event.user_id,
                "threadTimestamp": event.thread_timestamp or "",
                "isDirect": event.is_direct, "isMention": event.is_mention,
            },
            content=content,
        )
        return self.items(limit=1)[0]

    def inbox(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.items(limit=limit)

    def open_message(self, event_id: str) -> dict[str, Any]:
        item = next((value for value in self.items(limit=100) if value["id"] == event_id), None)
        if item is None:
            raise KeyError("Slack event is unavailable")
        return item

    def draft_reply(self, event_id: str, text: str) -> dict[str, Any]:
        return {"eventId": event_id, "draft": str(text)[:4000], "sent": False}

    def propose_reply(self, event_id: str, text: str) -> dict[str, Any]:
        item = self.open_message(event_id)
        proposal = self._connector().propose_message(
            channel_id=str(item["sourceId"]),
            text=str(text)[:4000],
            thread_timestamp=str(item.get("threadTimestamp", "")) or None,
        )
        return self._save_proposal(proposal)

    def replace_reply_proposal(self, proposal_id: str, text: str) -> dict[str, Any]:
        """Reject one pending preview and create a new immutable final preview."""

        with self._proposal_lock:
            previous = self.proposal(proposal_id)
            if previous.action != "send_message":
                raise ValueError("Proposal is not a Slack reply")
            body = previous.mutable_payload()
            self.reject(proposal_id)
            replacement = self._connector().propose_message(
                channel_id=str(body.get("channel", previous.target)),
                text=str(text)[:4000],
                thread_timestamp=str(body.get("thread_ts", "")) or None,
            )
            return self._save_proposal(replacement)

    def confirm_and_execute(self, proposal_id: str) -> dict[str, Any]:
        confirmed = self._claim_proposal_for_execution(proposal_id)
        try:
            result = self._connector().send_confirmed(confirmed)
        except BaseException:
            self._fail_claimed_proposal(proposal_id)
            raise
        return self._finish_proposal_execution(proposal_id, result.proposal)


__all__ = [
    "AssistanceMode",
    "CalendarRuntime",
    "ConnectorPolicyAxes",
    "InterruptionMode",
    "RetentionTier",
    "ScopeMode",
    "SlackRuntime",
    "proposal_public",
]
