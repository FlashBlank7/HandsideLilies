# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping


MEMORY_PARTITIONS: tuple[dict[str, Any], ...] = (
    {
        "partition_id": "identity",
        "name": "身份与称呼",
        "description": "用户的名字、称呼、身份事实与不可混淆的信息",
        "sort_order": 10,
        "is_core": 1,
    },
    {
        "partition_id": "relationship",
        "name": "关系与共同经历",
        "description": "用户与莉莉丝共同经历、关系变化和彼此的约定",
        "sort_order": 20,
        "is_core": 0,
    },
    {
        "partition_id": "preferences",
        "name": "偏好与习惯",
        "description": "稳定偏好、工作习惯、内容偏好与需要避开的事物",
        "sort_order": 30,
        "is_core": 0,
    },
    {
        "partition_id": "projects",
        "name": "项目与目标",
        "description": "正在进行的项目、目标、决定、里程碑和后续事项",
        "sort_order": 40,
        "is_core": 0,
    },
    {
        "partition_id": "research",
        "name": "论文与研究",
        "description": "论文阅读、研究方向、术语、实验和学术问题",
        "sort_order": 50,
        "is_core": 0,
    },
    {
        "partition_id": "daily",
        "name": "日常生活",
        "description": "日常事件、生活节奏以及值得保留的生活片段",
        "sort_order": 60,
        "is_core": 0,
    },
    {
        "partition_id": "world-lore",
        "name": "莉莉丝世界观",
        "description": "项目既定 Canon 与共同产生的故事记忆，二者严格区分",
        "sort_order": 70,
        "is_core": 0,
    },
    {
        "partition_id": "unfiled",
        "name": "待归档",
        "description": "已经安全保存、尚待后台整理的原始内容",
        "sort_order": 80,
        "is_core": 0,
    },
)


_PARTITION_IDS = frozenset(value["partition_id"] for value in MEMORY_PARTITIONS)
_MEMORY_ARCHIVAL_ATTEMPTS_SETTING = "memory_archival_attempt_versions"
_PROACTIVE_MODEL_IDS = frozenset(
    {
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "verified-source-metadata",
        # Legacy read compatibility only; current generation never emits it.
        "local-safe-fallback",
    }
)


def _compact_text(value: str) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def _normalize_proactive_generation(
    generation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the only generation metadata allowed into persistent storage."""

    if not isinstance(generation, Mapping) or not generation:
        return {}
    context_type = str(generation.get("contextType", ""))[:40]
    if context_type not in {
        "application-signal",
        "active-window-image",
        # Provenance only.  The generated anchor itself is intentionally not
        # part of this receipt and is never written to SQLite.
        "retained-image-anchor",
    }:
        return {}
    model = _compact_text(str(generation.get("model", "")))[:80]
    if model not in _PROACTIVE_MODEL_IDS:
        model = ""
    image_grounded = bool(
        generation.get("imageGrounded", False) is True
        and context_type == "active-window-image"
    )
    confidence = str(
        generation.get("evidenceConfidence", "none")
    ).casefold()[:16]
    if not image_grounded or confidence not in {"medium", "high"}:
        confidence = "none"
    return {
        "schemaVersion": 1,
        "contextType": context_type,
        "imageGrounded": image_grounded,
        "model": model,
        "evidenceConfidence": confidence,
    }


def infer_memory_partition(title: str, content: str, category: str = "") -> str:
    """Conservative local routing used until Luna supplies richer metadata."""

    haystack = f"{title}\n{category}\n{content}".casefold()
    rules = (
        ("identity", ("称呼", "姓名", "名字", "叫我", "我叫", "身份")),
        ("research", ("论文", "研究", "arxiv", "pubmed", "实验", "学术", "课题")),
        ("preferences", ("偏好", "喜欢", "不喜欢", "习惯", "希望以后", "常用")),
        ("projects", ("项目", "目标", "计划", "里程碑", "待办", "lilies in the box")),
        ("world-lore", ("世界观", "canon", "方舟", "盒中世界", "初遇", "莉莉丝的父亲")),
        ("relationship", ("共同经历", "我们一起", "关系", "约定", "发现莉莉丝")),
        ("daily", ("日常", "今天", "生活", "作息", "吃饭", "睡眠")),
    )
    for partition_id, markers in rules:
        if any(marker in haystack for marker in markers):
            return partition_id
    return "unfiled"


def memory_search_tokens(value: str, max_tokens: int = 128) -> list[str]:
    """Return bounded Chinese character n-grams plus case-folded word tokens."""

    normalized = unicodedata.normalize("NFKC", _compact_text(value)).casefold()[:12_000]
    ordered: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9][a-z0-9_.+-]{1,63}", normalized):
        ordered[f"w:{word}"] = None
    for sequence in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        if len(sequence) == 1:
            ordered[f"c:{sequence}"] = None
            continue
        for size in (2, 3):
            for index in range(max(0, len(sequence) - size + 1)):
                ordered[f"c:{sequence[index:index + size]}"] = None
                if len(ordered) >= max_tokens:
                    return list(ordered)
    return list(ordered)[:max_tokens]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


_PRODUCTIVITY_MIGRATION_VERSION = 3
_PRODUCTIVITY_MIGRATION_NAME = "v0.3-productivity-growth"
_AUDIT_RESULT_MIGRATION_VERSION = 4
_AUDIT_RESULT_MIGRATION_NAME = "v0.3-component-audit-results"
_AUDIT_PRIVACY_MIGRATION_VERSION = 5
_AUDIT_PRIVACY_MIGRATION_NAME = "v0.3-connector-audit-privacy"
_PROACTIVE_PROVENANCE_MIGRATION_VERSION = 6
_PROACTIVE_PROVENANCE_MIGRATION_NAME = "v0.3-proactive-generation-provenance"
_PRODUCTIVITY_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'inbox',
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','completed','archived')),
        priority INTEGER NOT NULL DEFAULT 1 CHECK(priority BETWEEN 0 AND 3),
        due_at TEXT,
        timezone TEXT NOT NULL DEFAULT 'UTC',
        recurrence_json TEXT NOT NULL DEFAULT '{}',
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE task_occurrences (
        occurrence_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        scheduled_for TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','completed','skipped')),
        completion_version INTEGER NOT NULL DEFAULT 0 CHECK(completion_version >= 0),
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(task_id, scheduled_for),
        FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE focus_sessions (
        session_id TEXT PRIMARY KEY,
        task_id TEXT,
        state TEXT NOT NULL CHECK(state IN ('running','paused','finished','cancelled')),
        planned_seconds INTEGER NOT NULL CHECK(planned_seconds BETWEEN 300 AND 10800),
        active_seconds INTEGER NOT NULL DEFAULT 0 CHECK(active_seconds >= 0),
        idle_seconds INTEGER NOT NULL DEFAULT 0 CHECK(idle_seconds >= 0),
        outcome TEXT NOT NULL DEFAULT 'focused'
            CHECK(outcome IN ('focused','rest')),
        started_at TEXT NOT NULL,
        last_resumed_at TEXT,
        paused_at TEXT,
        ended_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE reading_sessions (
        session_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL CHECK(state IN ('running','paused','finished','cancelled')),
        active_seconds INTEGER NOT NULL DEFAULT 0 CHECK(active_seconds >= 0),
        started_at TEXT NOT NULL,
        last_resumed_at TEXT,
        paused_at TEXT,
        ended_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE reminders (
        reminder_id TEXT PRIMARY KEY,
        task_id TEXT,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        fire_at TEXT NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'UTC',
        recurrence_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending','dismissed','completed')),
        snoozed_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE reminder_deliveries (
        delivery_id TEXT PRIMARY KEY,
        reminder_id TEXT NOT NULL,
        scheduled_for TEXT NOT NULL,
        channel TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'claimed'
            CHECK(status IN ('claimed','delivered','failed')),
        delivered_at TEXT,
        error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(reminder_id, scheduled_for, channel),
        FOREIGN KEY(reminder_id) REFERENCES reminders(reminder_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE growth_events (
        event_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        event_kind TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_id TEXT NOT NULL,
        points INTEGER NOT NULL,
        requested_points INTEGER NOT NULL,
        compensation_for TEXT UNIQUE,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        occurred_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(compensation_for) REFERENCES growth_events(event_id)
    )
    """,
    """
    CREATE TABLE growth_state (
        state_id TEXT PRIMARY KEY CHECK(state_id = 'default'),
        total_points INTEGER NOT NULL DEFAULT 0 CHECK(total_points >= 0),
        stage TEXT NOT NULL DEFAULT '初遇',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE unlocks (
        unlock_id TEXT PRIMARY KEY,
        item_key TEXT NOT NULL UNIQUE,
        item_kind TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        source_event_id TEXT,
        unlocked_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(source_event_id) REFERENCES growth_events(event_id)
    )
    """,
    """
    CREATE TABLE character_loadout (
        loadout_id TEXT PRIMARY KEY CHECK(loadout_id = 'default'),
        outfit_id TEXT NOT NULL,
        pose_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE world_state (
        object_id TEXT PRIMARY KEY,
        object_kind TEXT NOT NULL,
        display_name TEXT NOT NULL,
        unlocked INTEGER NOT NULL DEFAULT 0 CHECK(unlocked IN (0,1)),
        placed INTEGER NOT NULL DEFAULT 0 CHECK(placed IN (0,1)),
        position_json TEXT NOT NULL DEFAULT '{}',
        state_json TEXT NOT NULL DEFAULT '{}',
        source_event_id TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(source_event_id) REFERENCES growth_events(event_id)
    )
    """,
    """
    CREATE TABLE narrative_events (
        narrative_id TEXT PRIMARY KEY,
        event_key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','acknowledged')),
        source_event_id TEXT,
        created_at TEXT NOT NULL,
        acknowledged_at TEXT,
        FOREIGN KEY(source_event_id) REFERENCES growth_events(event_id)
    )
    """,
    """
    CREATE TABLE event_outbox (
        outbox_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending','delivered','failed')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        available_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        delivered_at TEXT,
        last_error TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX tasks_status_due ON tasks(archived,status,due_at)",
    "CREATE INDEX task_occurrences_open ON task_occurrences(task_id,status,scheduled_for)",
    "CREATE INDEX focus_sessions_state ON focus_sessions(state,started_at DESC)",
    "CREATE INDEX reading_sessions_state ON reading_sessions(state,started_at DESC)",
    "CREATE INDEX reminders_due ON reminders(state,fire_at,snoozed_until)",
    "CREATE INDEX growth_events_occurred ON growth_events(occurred_at,event_kind)",
    "CREATE INDEX unlocks_kind ON unlocks(item_kind,unlocked_at)",
    "CREATE INDEX narratives_status ON narrative_events(status,created_at)",
    "CREATE INDEX outbox_pending ON event_outbox(state,available_at,created_at)",
)


class _ConnectionSessionState:
    """One reusable SQLite connection owned by exactly one Python thread."""

    def __init__(
        self, owner_thread: threading.Thread, connection: sqlite3.Connection
    ) -> None:
        self.owner_thread = owner_thread
        self.connection = connection
        self.tokens: list[object] = []
        self.operation_depth = 0
        self.savepoint_sequence = 0


class _ConnectionSession:
    """Explicit context manager controlling a thread-local connection lifetime."""

    def __init__(self, database: "Database") -> None:
        self._database = database
        self._owner_thread: threading.Thread | None = None
        self._token = object()
        self._entered = False

    def __enter__(self) -> "_ConnectionSession":
        if self._entered:
            raise RuntimeError("database connection session is already active")
        owner_thread = threading.current_thread()
        self._database._enter_connection_session(owner_thread, self._token)
        self._owner_thread = owner_thread
        self._entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._entered or self._owner_thread is None:
            raise RuntimeError("database connection session is not active")
        if threading.current_thread() is not self._owner_thread:
            # Leave the handle active so its owner can still close it safely.
            raise RuntimeError(
                "database connection session must be closed by its owner thread"
            )
        self._database._exit_connection_session(self._owner_thread, self._token)
        self._entered = False
        self._owner_thread = None

    def close(self) -> None:
        """Deterministically end a manually-entered session on its owner thread."""

        self.__exit__(None, None, None)


class Database:
    """Small SQLite store with opt-in, thread-owned reusable connections."""

    def __init__(self, path: Path | str) -> None:
        raw_path = str(path)
        self._memory_database = raw_path == ":memory:"
        self.path = Path(raw_path)
        self._uri = self._memory_database
        self._dsn = (
            f"file:lilies-recovery-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._memory_database
            else str(self.path)
        )
        self._anchor: sqlite3.Connection | None = None
        if self._memory_database:
            # Each public operation intentionally owns a connection. A shared
            # in-memory URI plus this anchor keeps recovery-mode state alive
            # between those short-lived connections without touching disk.
            self._anchor = sqlite3.connect(self._dsn, uri=True, timeout=10)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_lock = threading.Lock()
        self._session_lock = threading.RLock()
        self._connection_sessions: dict[
            threading.Thread, _ConnectionSessionState
        ] = {}
        self._migrate()

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._dsn, timeout=10, uri=self._uri)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if not self._memory_database:
            connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def connection_session(self) -> _ConnectionSession:
        """Return an opt-in reusable connection scope for the calling thread.

        Existing ``connect()`` calls made on the same thread borrow the session
        connection. Each of those operation scopes still commits on success and
        rolls back on failure. Nested sessions reuse the same connection and
        must be exited in LIFO order.
        """

        return _ConnectionSession(self)

    def _enter_connection_session(
        self, owner_thread: threading.Thread, token: object
    ) -> None:
        if threading.current_thread() is not owner_thread:
            raise RuntimeError("database connection session owner changed during entry")
        with self._session_lock:
            state = self._connection_sessions.get(owner_thread)
            if state is not None:
                if state.owner_thread is not owner_thread:
                    raise RuntimeError(
                        "database connection session crossed thread ownership"
                    )
                state.tokens.append(token)
                return

        # Opening a database on F: can take tens of milliseconds. Do not hold
        # the registry lock and stall an already-established GUI session while
        # another thread creates its own connection.
        connection = self._open_connection()
        with self._session_lock:
            state = self._connection_sessions.get(owner_thread)
            if state is not None:
                connection.close()
                if state.owner_thread is not owner_thread:
                    raise RuntimeError(
                        "database connection session crossed thread ownership"
                    )
            else:
                state = _ConnectionSessionState(owner_thread, connection)
                self._connection_sessions[owner_thread] = state
            if state.owner_thread is not owner_thread:
                raise RuntimeError("database connection session crossed thread ownership")
            state.tokens.append(token)

    def _exit_connection_session(
        self, owner_thread: threading.Thread, token: object
    ) -> None:
        if threading.current_thread() is not owner_thread:
            raise RuntimeError(
                "database connection session must be closed by its owner thread"
            )
        with self._session_lock:
            state = self._connection_sessions.get(owner_thread)
            if state is None or not state.tokens:
                raise RuntimeError("database connection session is not active")
            if state.tokens[-1] is not token:
                raise RuntimeError(
                    "nested database connection sessions must close in LIFO order"
                )
            if len(state.tokens) == 1 and state.operation_depth:
                raise RuntimeError(
                    "database connection session cannot close during an active operation"
                )
            state.tokens.pop()
            if state.tokens:
                return
            del self._connection_sessions[owner_thread]
            connection = state.connection
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        owner_thread = threading.current_thread()
        with self._session_lock:
            state = self._connection_sessions.get(owner_thread)
            connection = state.connection if state is not None else None
            savepoint = ""
            if state is not None:
                state.operation_depth += 1
                if state.operation_depth > 1:
                    state.savepoint_sequence += 1
                    savepoint = f"lilies_operation_{state.savepoint_sequence}"
        owns_connection = connection is None
        if connection is None:
            connection = self._open_connection()
        savepoint_open = False
        try:
            if savepoint:
                connection.execute(f"SAVEPOINT {savepoint}")
                savepoint_open = True
            yield connection
            if savepoint:
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                savepoint_open = False
            else:
                connection.commit()
        except BaseException:
            if savepoint_open:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            elif not savepoint:
                connection.rollback()
            raise
        finally:
            if owns_connection:
                connection.close()
            elif state is not None:
                with self._session_lock:
                    state.operation_depth = max(0, state.operation_depth - 1)

    def close(self) -> None:
        with self._session_lock:
            if self._connection_sessions:
                current_thread = threading.current_thread()
                qualifier = (
                    " on the current thread"
                    if current_thread in self._connection_sessions
                    else " on another thread"
                )
                raise RuntimeError(
                    f"cannot close database while a connection session is active{qualifier}"
                )
        anchor = self._anchor
        self._anchor = None
        if anchor is not None:
            anchor.close()

    def _migrate(self) -> None:
        with self._migration_lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_items (
                    item_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    group_name TEXT NOT NULL DEFAULT '未分组',
                    x REAL NOT NULL DEFAULT 40,
                    y REAL NOT NULL DEFAULT 72,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_layouts (
                    layout_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_layout_items (
                    layout_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    group_name TEXT NOT NULL DEFAULT '未分组',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(layout_id, item_id),
                    FOREIGN KEY(layout_id) REFERENCES desktop_layouts(layout_id) ON DELETE CASCADE,
                    FOREIGN KEY(item_id) REFERENCES desktop_items(item_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_cards (
                    memory_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '事实',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reading_cards (
                    card_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    question TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id TEXT PRIMARY KEY,
                    origin TEXT NOT NULL,
                    component_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_partitions (
                    partition_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL,
                    is_core INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_fragments (
                    fragment_id TEXT PRIMARY KEY,
                    partition_id TEXT NOT NULL DEFAULT 'unfiled',
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    conversation_id TEXT,
                    role TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    entities_json TEXT NOT NULL DEFAULT '[]',
                    importance REAL NOT NULL DEFAULT 0.5,
                    canon_kind TEXT NOT NULL DEFAULT 'none',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    forgotten INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_recalled_at TEXT,
                    UNIQUE(source_type, source_id),
                    FOREIGN KEY(partition_id) REFERENCES memory_partitions(partition_id)
                );
                CREATE TABLE IF NOT EXISTS memory_ngrams (
                    fragment_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    PRIMARY KEY(fragment_id, token),
                    FOREIGN KEY(fragment_id) REFERENCES memory_fragments(fragment_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_recall_log (
                    recall_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL DEFAULT '',
                    partition_ids_json TEXT NOT NULL DEFAULT '[]',
                    result_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proactive_sessions (
                    session_id TEXT PRIMARY KEY,
                    bubble_id TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    generation_json TEXT NOT NULL DEFAULT '{}',
                    scene_label TEXT NOT NULL DEFAULT '',
                    moved_to_box INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proactive_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES proactive_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS content_cache (
                    cache_key TEXT PRIMARY KEY,
                    items_json TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_peek_log (
                    event_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation_created
                    ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS desktop_items_visible
                    ON desktop_items(hidden, source, name);
                CREATE INDEX IF NOT EXISTS reading_cards_kind_updated
                    ON reading_cards(kind, updated_at DESC);
                CREATE INDEX IF NOT EXISTS memory_fragments_partition_updated
                    ON memory_fragments(partition_id, enabled, forgotten, updated_at DESC);
                CREATE INDEX IF NOT EXISTS memory_fragments_source
                    ON memory_fragments(source_type, source_id);
                CREATE INDEX IF NOT EXISTS memory_ngrams_token
                    ON memory_ngrams(token, fragment_id);
                CREATE INDEX IF NOT EXISTS memory_recall_log_created
                    ON memory_recall_log(created_at DESC);
                CREATE INDEX IF NOT EXISTS proactive_sessions_updated
                    ON proactive_sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS proactive_messages_session_created
                    ON proactive_messages(session_id,created_at);
                CREATE INDEX IF NOT EXISTS desktop_peek_log_created
                    ON desktop_peek_log(created_at DESC);
                """
            )
            now = utc_now()
            for partition in MEMORY_PARTITIONS:
                db.execute(
                    """INSERT INTO memory_partitions
                       (partition_id,name,description,summary,sort_order,is_core,enabled,updated_at)
                       VALUES(?,?,?,'',?,?,1,?)
                       ON CONFLICT(partition_id) DO UPDATE SET
                         name=excluded.name,description=excluded.description,
                         sort_order=excluded.sort_order,is_core=excluded.is_core""",
                    (
                        partition["partition_id"],
                        partition["name"],
                        partition["description"],
                        partition["sort_order"],
                        partition["is_core"],
                        now,
                    ),
                )
            db.execute(
                "INSERT OR IGNORE INTO desktop_layouts(layout_id,name,created_at,updated_at) VALUES('default','默认布局',?,?)",
                (now, now),
            )
            db.execute(
                """INSERT OR IGNORE INTO desktop_layout_items(layout_id,item_id,x,y,group_name,pinned,hidden)
                   SELECT 'default',item_id,x,y,group_name,pinned,hidden FROM desktop_items"""
            )
            self._backfill_memory_index(db)
            # ``executescript`` manages its own transaction. Finish legacy
            # bootstrapping before starting the versioned, all-or-nothing v0.3
            # migration below.
            db.commit()
            self._apply_productivity_migration(db)
            self._apply_audit_result_migration(db)
            self._apply_audit_privacy_migration(db)
            self._apply_proactive_provenance_migration(db)

    @staticmethod
    def _apply_productivity_migration(db: sqlite3.Connection) -> None:
        applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (_PRODUCTIVITY_MIGRATION_VERSION,),
        ).fetchone()
        if applied is not None:
            return

        now = utc_now()
        try:
            db.execute("BEGIN IMMEDIATE")
            for statement in _PRODUCTIVITY_SCHEMA:
                db.execute(statement)
            db.execute(
                "INSERT INTO growth_state(state_id,total_points,stage,updated_at) VALUES('default',0,'初遇',?)",
                (now,),
            )
            db.execute(
                "INSERT INTO character_loadout(loadout_id,outfit_id,pose_id,updated_at) "
                "VALUES('default','first-encounter','idle-prayer',?)",
                (now,),
            )
            db.executemany(
                """INSERT INTO unlocks
                   (unlock_id,item_key,item_kind,reason,source_event_id,unlocked_at,metadata_json)
                   VALUES(?,?,?,?,NULL,?,'{}')""",
                (
                    (uuid.uuid4().hex, "outfit:first-encounter", "outfit", "initial", now),
                    (uuid.uuid4().hex, "pose:idle-prayer", "pose", "initial", now),
                ),
            )
            db.execute(
                "INSERT INTO world_state"
                "(object_id,object_kind,display_name,unlocked,placed,position_json,state_json,source_event_id,updated_at) "
                "VALUES('box-core','room','莉莉丝的盒子',1,1,'{}','{}',NULL,?)",
                (now,),
            )
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (_PRODUCTIVITY_MIGRATION_VERSION, _PRODUCTIVITY_MIGRATION_NAME, now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _apply_audit_result_migration(db: sqlite3.Connection) -> None:
        """Add bounded execution outcomes to the component audit trail.

        Existing v0.1/v0.2 databases only recorded the decision and payload.
        The migration is re-entrant because a freshly created database already
        has the columns in its bootstrap schema, while an upgraded database
        needs three ALTER TABLE statements.
        """

        applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (_AUDIT_RESULT_MIGRATION_VERSION,),
        ).fetchone()
        if applied is not None:
            return
        known = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(audit_log)").fetchall()
        }
        try:
            db.execute("BEGIN IMMEDIATE")
            if "result_json" not in known:
                db.execute("ALTER TABLE audit_log ADD COLUMN result_json TEXT")
            if "error_json" not in known:
                db.execute("ALTER TABLE audit_log ADD COLUMN error_json TEXT")
            if "completed_at" not in known:
                db.execute("ALTER TABLE audit_log ADD COLUMN completed_at TEXT")
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (_AUDIT_RESULT_MIGRATION_VERSION, _AUDIT_RESULT_MIGRATION_NAME, utc_now()),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _apply_audit_privacy_migration(db: sqlite3.Connection) -> None:
        """Erase connector content accidentally copied by pre-v0.3 audits."""

        applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (_AUDIT_PRIVACY_MIGRATION_VERSION,),
        ).fetchone()
        if applied is not None:
            return
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE audit_log
                   SET payload_json='{"redacted":true,"migration":"v0.3"}',
                       result_json=CASE WHEN result_json IS NULL THEN NULL
                           ELSE '{"redacted":true,"migration":"v0.3"}' END,
                       error_json=CASE WHEN error_json IS NULL THEN NULL
                           ELSE '{"redacted":true,"migration":"v0.3"}' END
                   WHERE component_id IN ('calendar','slack')"""
            )
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (
                    _AUDIT_PRIVACY_MIGRATION_VERSION,
                    _AUDIT_PRIVACY_MIGRATION_NAME,
                    utc_now(),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _apply_proactive_provenance_migration(db: sqlite3.Connection) -> None:
        """Add privacy-bounded generation provenance to proactive sessions.

        The receipt deliberately excludes pixels, window titles, process
        identities, paths and captured text.  It only lets a saved bubble be
        audited as image-grounded (or application-signal), together with the
        model and evidence confidence reported by the generation runtime.
        """

        try:
            db.execute("BEGIN IMMEDIATE")
            # Another Database instance may have migrated this file while we
            # waited for the write lock.  Re-check both facts inside the same
            # transaction before attempting ALTER TABLE.
            applied = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (_PROACTIVE_PROVENANCE_MIGRATION_VERSION,),
            ).fetchone()
            if applied is not None:
                db.commit()
                return
            known = {
                str(row["name"])
                for row in db.execute(
                    "PRAGMA table_info(proactive_sessions)"
                ).fetchall()
            }
            if "generation_json" not in known:
                db.execute(
                    "ALTER TABLE proactive_sessions "
                    "ADD COLUMN generation_json TEXT NOT NULL DEFAULT '{}'"
                )
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (
                    _PROACTIVE_PROVENANCE_MIGRATION_VERSION,
                    _PROACTIVE_PROVENANCE_MIGRATION_NAME,
                    utc_now(),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _replace_fragment_tokens(
        db: sqlite3.Connection,
        fragment_id: str,
        content: str,
        enabled: bool = True,
        forgotten: bool = False,
    ) -> None:
        db.execute("DELETE FROM memory_ngrams WHERE fragment_id=?", (fragment_id,))
        if not enabled or forgotten:
            return
        db.executemany(
            "INSERT OR IGNORE INTO memory_ngrams(fragment_id,token) VALUES(?,?)",
            ((fragment_id, token) for token in memory_search_tokens(content)),
        )

    def _upsert_memory_fragment(
        self,
        db: sqlite3.Connection,
        *,
        source_type: str,
        source_id: str,
        content: str,
        partition_id: str = "unfiled",
        conversation_id: str | None = None,
        role: str = "",
        summary: str = "",
        keywords: list[str] | None = None,
        entities: list[str] | None = None,
        importance: float = 0.5,
        canon_kind: str = "none",
        enabled: bool = True,
        created_at: str | None = None,
    ) -> str:
        safe_partition = partition_id if partition_id in _PARTITION_IDS else "unfiled"
        safe_canon = canon_kind if canon_kind in {"none", "canon", "shared"} else "none"
        compact = _compact_text(content)[:20_000]
        if not compact:
            raise ValueError("记忆片段不能为空")
        now = utc_now()
        existing = db.execute(
            "SELECT fragment_id,forgotten FROM memory_fragments WHERE source_type=? AND source_id=?",
            (source_type, source_id),
        ).fetchone()
        fragment_id = str(existing["fragment_id"]) if existing else uuid.uuid4().hex
        forgotten = bool(existing["forgotten"]) if existing else False
        db.execute(
            """INSERT INTO memory_fragments
               (fragment_id,partition_id,source_type,source_id,conversation_id,role,content,summary,
                keywords_json,entities_json,importance,canon_kind,enabled,forgotten,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_type,source_id) DO UPDATE SET
                 partition_id=excluded.partition_id,conversation_id=excluded.conversation_id,
                 role=excluded.role,content=excluded.content,summary=excluded.summary,
                 keywords_json=excluded.keywords_json,entities_json=excluded.entities_json,
                 importance=excluded.importance,canon_kind=excluded.canon_kind,
                 enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (
                fragment_id,
                safe_partition,
                source_type[:40],
                source_id[:160],
                conversation_id,
                role[:20],
                compact,
                _compact_text(summary)[:2_000],
                json.dumps(keywords or [], ensure_ascii=False),
                json.dumps(entities or [], ensure_ascii=False),
                min(1.0, max(0.0, float(importance))),
                safe_canon,
                int(enabled),
                int(forgotten),
                created_at or now,
                now,
            ),
        )
        self._replace_fragment_tokens(db, fragment_id, compact, enabled, forgotten)
        return fragment_id

    def _backfill_memory_index(self, db: sqlite3.Connection) -> None:
        """Idempotently index v0.1 messages/cards without changing their source rows."""

        for row in db.execute(
            "SELECT message_id,conversation_id,role,content,created_at FROM messages ORDER BY created_at"
        ).fetchall():
            if db.execute(
                "SELECT 1 FROM memory_fragments WHERE source_type='message' AND source_id=?",
                (row["message_id"],),
            ).fetchone():
                continue
            self._upsert_memory_fragment(
                db,
                source_type="message",
                source_id=str(row["message_id"]),
                conversation_id=str(row["conversation_id"]),
                role=str(row["role"]),
                content=str(row["content"]),
                partition_id="unfiled",
                importance=0.35,
                created_at=str(row["created_at"]),
            )
        for row in db.execute(
            "SELECT memory_id,title,content,category,enabled,created_at FROM memory_cards"
        ).fetchall():
            if db.execute(
                "SELECT 1 FROM memory_fragments WHERE source_type='memory-card' AND source_id=?",
                (row["memory_id"],),
            ).fetchone():
                continue
            partition_id = infer_memory_partition(row["title"], row["content"], row["category"])
            category = str(row["category"]).casefold()
            canon_kind = "canon" if "canon" in category or "既定" in category else "none"
            self._upsert_memory_fragment(
                db,
                source_type="memory-card",
                source_id=str(row["memory_id"]),
                content=f"{row['title']}：{row['content']}",
                summary=str(row["title"]),
                partition_id=partition_id,
                importance=0.85,
                canon_kind=canon_kind,
                enabled=bool(row["enabled"]),
                created_at=str(row["created_at"]),
            )
        for row in db.execute(
            "SELECT card_id,title,source_text,answer,question,created_at FROM reading_cards"
        ).fetchall():
            if db.execute(
                "SELECT 1 FROM memory_fragments WHERE source_type='reading-card' AND source_id=?",
                (row["card_id"],),
            ).fetchone():
                continue
            content = f"{row['title']}。原文：{row['source_text']}。莉莉丝的说明：{row['answer']}"
            if row["question"]:
                content += f"。追问：{row['question']}"
            self._upsert_memory_fragment(
                db,
                source_type="reading-card",
                source_id=str(row["card_id"]),
                content=content,
                summary=str(row["title"]),
                partition_id="research",
                importance=0.65,
                created_at=str(row["created_at"]),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except (TypeError, ValueError):
            return default

    def reconcile_settings(
        self,
        keys: tuple[str, ...],
        reconcile: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Read related settings and apply repairs in one transaction.

        ``reconcile`` must be a pure in-memory function.  An immediate
        transaction is opened before the read so another writer cannot place
        the rows in different generations between the snapshot and a repair.
        Missing or malformed JSON rows are omitted from the mapping passed to
        the callback.
        """

        normalized_keys = tuple(dict.fromkeys(str(key) for key in keys))
        if not normalized_keys:
            return {}
        placeholders = ",".join("?" for _ in normalized_keys)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"SELECT key,value_json FROM settings WHERE key IN ({placeholders})",
                normalized_keys,
            ).fetchall()
            values: dict[str, Any] = {}
            for row in rows:
                try:
                    values[str(row["key"])] = json.loads(row["value_json"])
                except (TypeError, ValueError):
                    continue

            repairs = dict(reconcile(dict(values)))
            unexpected = set(repairs).difference(normalized_keys)
            if unexpected:
                raise ValueError(
                    "reconciliation returned unrequested settings: "
                    + ", ".join(sorted(unexpected))
                )
            encoded_repairs = [
                (str(key), json.dumps(value, ensure_ascii=False))
                for key, value in repairs.items()
            ]
            if encoded_repairs:
                updated_at = utc_now()
                db.executemany(
                    """INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?)
                       ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                    [
                        (key, encoded, updated_at)
                        for key, encoded in encoded_repairs
                    ],
                )
                values.update(repairs)
        return values

    def set_settings(self, values: Mapping[str, Any]) -> None:
        """Persist a group of settings atomically.

        Serialize every value before opening the transaction so a bad value
        cannot leave an earlier key committed.  The shared connection then
        makes SQLite roll the whole group back if any UPSERT or commit fails.
        """

        rows = [
            (str(key), json.dumps(value, ensure_ascii=False))
            for key, value in values.items()
        ]
        if not rows:
            return
        updated_at = utc_now()
        with self.connect() as db:
            db.executemany(
                """INSERT INTO settings(key, value_json, updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                [(key, encoded, updated_at) for key, encoded in rows],
            )

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def upsert_desktop_item(self, value: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as db:
            existing = db.execute(
                "SELECT item_id,x,y,group_name,pinned,hidden FROM desktop_items WHERE path=?",
                (value["path"],),
            ).fetchone()
            if existing:
                db.execute(
                    """UPDATE desktop_items SET name=?,source=?,kind=?,updated_at=? WHERE path=?""",
                    (value["name"], value["source"], value["kind"], now, value["path"]),
                )
                return
            db.execute(
                """INSERT INTO desktop_items
                   (item_id,name,path,source,kind,group_name,x,y,pinned,hidden,updated_at)
                   VALUES(?,?,?,?,?,'未分组',?,?,0,0,?)""",
                (
                    value.get("item_id") or uuid.uuid5(uuid.NAMESPACE_URL, value["path"]).hex,
                    value["name"],
                    value["path"],
                    value["source"],
                    value["kind"],
                    float(value.get("x", 40)),
                    float(value.get("y", 72)),
                    now,
                ),
            )
            item_id = value.get("item_id") or uuid.uuid5(uuid.NAMESPACE_URL, value["path"]).hex
            db.execute(
                """INSERT OR IGNORE INTO desktop_layout_items(layout_id,item_id,x,y,group_name,pinned,hidden)
                   SELECT layout_id,?,?,?,'未分组',0,0 FROM desktop_layouts""",
                (item_id, float(value.get("x", 40)), float(value.get("y", 72))),
            )

    def desktop_items(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        active = str(self.get_setting("active_desktop_layout", "default"))
        query = """SELECT d.item_id,d.name,d.path,d.source,d.kind,
                          COALESCE(l.group_name,d.group_name) AS group_name,
                          COALESCE(l.x,d.x) AS x,COALESCE(l.y,d.y) AS y,
                          COALESCE(l.pinned,d.pinned) AS pinned,
                          COALESCE(l.hidden,d.hidden) AS hidden,d.updated_at
                   FROM desktop_items d
                   LEFT JOIN desktop_layout_items l ON l.item_id=d.item_id AND l.layout_id=?"""
        parameters: list[Any] = [active]
        if not include_hidden:
            query += " WHERE COALESCE(l.hidden,d.hidden)=0"
        query += " ORDER BY pinned DESC, source, lower(name)"
        with self.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def update_desktop_layout(self, item_id: str, **changes: Any) -> None:
        allowed = {"x", "y", "group_name", "pinned", "hidden"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return
        active = str(self.get_setting("active_desktop_layout", "default"))
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO desktop_layout_items(layout_id,item_id,x,y,group_name,pinned,hidden)
                   SELECT ?,item_id,x,y,group_name,pinned,hidden FROM desktop_items WHERE item_id=?""",
                (active, item_id),
            )
            db.execute(
                f"UPDATE desktop_layout_items SET {assignments} WHERE layout_id=? AND item_id=?",
                (*values.values(), active, item_id),
            )
            db.execute("UPDATE desktop_layouts SET updated_at=? WHERE layout_id=?", (utc_now(), active))

    def desktop_layouts(self) -> list[dict[str, Any]]:
        active = str(self.get_setting("active_desktop_layout", "default"))
        with self.connect() as db:
            rows = db.execute(
                "SELECT layout_id,name,created_at,updated_at FROM desktop_layouts ORDER BY created_at"
            ).fetchall()
        return [{**dict(row), "active": row["layout_id"] == active} for row in rows]

    def create_desktop_layout(self, name: str) -> str:
        layout_id = uuid.uuid4().hex
        active = str(self.get_setting("active_desktop_layout", "default"))
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO desktop_layouts(layout_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                (layout_id, name[:80] or "新布局", now, now),
            )
            db.execute(
                """INSERT INTO desktop_layout_items(layout_id,item_id,x,y,group_name,pinned,hidden)
                   SELECT ?,d.item_id,COALESCE(l.x,d.x),COALESCE(l.y,d.y),
                          COALESCE(l.group_name,d.group_name),COALESCE(l.pinned,d.pinned),COALESCE(l.hidden,d.hidden)
                   FROM desktop_items d LEFT JOIN desktop_layout_items l
                     ON l.item_id=d.item_id AND l.layout_id=?""",
                (layout_id, active),
            )
        self.set_setting("active_desktop_layout", layout_id)
        return layout_id

    def activate_desktop_layout(self, layout_id: str) -> bool:
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM desktop_layouts WHERE layout_id=?", (layout_id,)).fetchone()
        if not exists:
            return False
        self.set_setting("active_desktop_layout", layout_id)
        return True

    def delete_desktop_layout(self, layout_id: str) -> bool:
        if layout_id == "default":
            return False
        with self.connect() as db:
            cursor = db.execute("DELETE FROM desktop_layouts WHERE layout_id=?", (layout_id,))
        if cursor.rowcount:
            if self.get_setting("active_desktop_layout", "default") == layout_id:
                self.set_setting("active_desktop_layout", "default")
            return True
        return False

    def ensure_conversation(self, conversation_id: str | None = None) -> str:
        value = conversation_id or uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO conversations(conversation_id,title,created_at,updated_at)
                   VALUES(?,?,?,?)""",
                (value, "与莉莉丝的对话", now, now),
            )
        return value

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO messages(message_id,conversation_id,role,content,metadata_json,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (message_id, conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            self._upsert_memory_fragment(
                db,
                source_type="message",
                source_id=message_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
                partition_id="unfiled",
                importance=0.35,
                created_at=now,
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
                (now, conversation_id),
            )
        return message_id

    def recent_messages(self, conversation_id: str, limit: int = 24) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT role,content,metadata_json,created_at FROM messages
                   WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?""",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def search_messages(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        needle = query.strip()
        if not needle:
            return []
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.connect() as db:
            rows = db.execute(
                """SELECT m.message_id,m.conversation_id,m.role,m.content,m.created_at,c.title
                   FROM messages m JOIN conversations c ON c.conversation_id=m.conversation_id
                   WHERE m.content LIKE ? ESCAPE '\\'
                   ORDER BY m.created_at DESC LIMIT ?""",
                (f"%{escaped}%", limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_cards(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_cards"
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY updated_at DESC"
        with self.connect() as db:
            return [dict(row) for row in db.execute(query).fetchall()]

    def save_memory(
        self,
        title: str,
        content: str,
        category: str = "事实",
        memory_id: str | None = None,
        enabled: bool = True,
        partition_id: str | None = None,
        canon_kind: str = "none",
    ) -> str:
        value = memory_id or uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO memory_cards(memory_id,title,content,category,enabled,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(memory_id) DO UPDATE SET
                     title=excluded.title,content=excluded.content,category=excluded.category,
                     enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (value, title[:120], content[:4000], category[:40], int(enabled), now, now),
            )
            selected_partition = partition_id or infer_memory_partition(title, content, category)
            selected_canon = canon_kind
            if selected_canon == "none" and ("canon" in category.casefold() or "既定" in category):
                selected_canon = "canon"
            fragment_id = self._upsert_memory_fragment(
                db,
                source_type="memory-card",
                source_id=value,
                content=f"{title}：{content}",
                summary=title,
                partition_id=selected_partition,
                importance=0.85,
                canon_kind=selected_canon,
                enabled=enabled,
                created_at=now,
            )
            if enabled:
                db.execute("UPDATE memory_fragments SET forgotten=0 WHERE fragment_id=?", (fragment_id,))
                self._replace_fragment_tokens(db, fragment_id, f"{title}：{content}", True, False)
        return value

    def delete_memory(self, memory_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM memory_fragments WHERE source_type='memory-card' AND source_id=?",
                (memory_id,),
            )
            db.execute("DELETE FROM memory_cards WHERE memory_id=?", (memory_id,))

    def reading_cards(
        self,
        query: str = "",
        kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return saved paper-selection cards, newest first.

        Search is deliberately local and literal: SQL wildcard characters in a
        user's query do not broaden the result set.
        """

        safe_limit = max(0, min(int(limit), 500))
        if safe_limit == 0:
            return []
        clauses: list[str] = []
        parameters: list[Any] = []
        card_kind = str(kind).strip().casefold()
        if card_kind:
            clauses.append("kind=?")
            parameters.append(card_kind)
        needle = str(query).strip()
        if needle:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                "(title LIKE ? ESCAPE '\\' OR source_text LIKE ? ESCAPE '\\' "
                "OR answer LIKE ? ESCAPE '\\' OR question LIKE ? ESCAPE '\\')"
            )
            parameters.extend([f"%{escaped}%"] * 4)
        sql = "SELECT * FROM reading_cards"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(safe_limit)
        with self.connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            try:
                value["metadata"] = json.loads(value.pop("metadata_json"))
            except (TypeError, ValueError):
                value.pop("metadata_json", None)
                value["metadata"] = {}
            results.append(value)
        return results

    def save_reading_card(
        self,
        source_text: str,
        answer: str,
        kind: str = "explain",
        question: str = "",
        title: str = "",
        metadata: dict[str, Any] | None = None,
        card_id: str | None = None,
    ) -> str:
        source = str(source_text).strip()
        response = str(answer).strip()
        if not source or not response:
            raise ValueError("论文卡片需要原文和回答")
        card_kind = str(kind).strip().casefold() or "explain"
        value = card_id or uuid.uuid4().hex
        now = utc_now()
        card_title = str(title).strip()
        if not card_title:
            compact = " ".join(source.split())
            card_title = compact[:72] + ("…" if len(compact) > 72 else "")
        with self.connect() as db:
            db.execute(
                """INSERT INTO reading_cards
                   (card_id,kind,title,source_text,answer,question,metadata_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(card_id) DO UPDATE SET
                     kind=excluded.kind,title=excluded.title,source_text=excluded.source_text,
                     answer=excluded.answer,question=excluded.question,
                     metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (
                    value,
                    card_kind[:32],
                    card_title[:160],
                    source[:20_000],
                    response[:12_000],
                    str(question).strip()[:4_000],
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            index_content = f"{card_title}。原文：{source}。莉莉丝的说明：{response}"
            if question:
                index_content += f"。追问：{str(question).strip()}"
            self._upsert_memory_fragment(
                db,
                source_type="reading-card",
                source_id=value,
                content=index_content,
                summary=card_title,
                partition_id="research",
                importance=0.65,
                created_at=now,
            )
        return value

    def delete_reading_card(self, card_id: str) -> bool:
        with self.connect() as db:
            db.execute(
                "DELETE FROM memory_fragments WHERE source_type='reading-card' AND source_id=?",
                (str(card_id),),
            )
            cursor = db.execute("DELETE FROM reading_cards WHERE card_id=?", (str(card_id),))
        return bool(cursor.rowcount)

    def memory_partitions(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_partitions"
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY sort_order,partition_id"
        with self.connect() as db:
            rows = db.execute(query).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                value = dict(row)
                counts = db.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(CASE WHEN forgotten=0 AND enabled=1 THEN 1 ELSE 0 END) AS available
                       FROM memory_fragments WHERE partition_id=?""",
                    (row["partition_id"],),
                ).fetchone()
                value["total"] = int(counts["total"] or 0)
                value["available"] = int(counts["available"] or 0)
                result.append(value)
        return result

    def update_memory_partition_summary(self, partition_id: str, summary: str) -> bool:
        if partition_id not in _PARTITION_IDS:
            return False
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE memory_partitions SET summary=?,updated_at=? WHERE partition_id=?",
                (_compact_text(summary)[:1_200], utc_now(), partition_id),
            )
        return bool(cursor.rowcount)

    def save_memory_fragment(
        self,
        *,
        source_type: str,
        source_id: str,
        content: str,
        partition_id: str = "unfiled",
        conversation_id: str | None = None,
        role: str = "",
        summary: str = "",
        keywords: list[str] | None = None,
        entities: list[str] | None = None,
        importance: float = 0.5,
        canon_kind: str = "none",
        enabled: bool = True,
        created_at: str | None = None,
    ) -> str:
        with self.connect() as db:
            return self._upsert_memory_fragment(
                db,
                source_type=source_type,
                source_id=source_id,
                content=content,
                partition_id=partition_id,
                conversation_id=conversation_id,
                role=role,
                summary=summary,
                keywords=keywords,
                entities=entities,
                importance=importance,
                canon_kind=canon_kind,
                enabled=enabled,
                created_at=created_at,
            )

    def pending_memory_fragments(self, limit: int = 24) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as db:
            setting = db.execute(
                "SELECT value_json FROM settings WHERE key=?",
                (_MEMORY_ARCHIVAL_ATTEMPTS_SETTING,),
            ).fetchone()
            try:
                decoded_attempts = json.loads(setting["value_json"]) if setting else {}
            except (TypeError, ValueError):
                decoded_attempts = {}
            attempts = (
                {str(key): str(value) for key, value in decoded_attempts.items()}
                if isinstance(decoded_attempts, dict)
                else {}
            )
            rows = db.execute(
                """SELECT * FROM memory_fragments
                   WHERE partition_id='unfiled' AND enabled=1 AND forgotten=0
                   ORDER BY created_at LIMIT ?""",
                (safe_limit + len(attempts),),
            ).fetchall()
        pending = [
            self._decode_memory_row(row)
            for row in rows
            if attempts.get(str(row["fragment_id"])) != str(row["updated_at"])
        ]
        return pending[:safe_limit]

    def memory_fragment(self, fragment_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM memory_fragments WHERE fragment_id=?",
                (str(fragment_id),),
            ).fetchone()
        return self._decode_memory_row(row) if row is not None else None

    @staticmethod
    def _decode_memory_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        for source, target in (("keywords_json", "keywords"), ("entities_json", "entities")):
            try:
                value[target] = json.loads(value.pop(source))
            except (TypeError, ValueError):
                value.pop(source, None)
                value[target] = []
        return value

    def classify_memory_fragment(
        self,
        fragment_id: str,
        *,
        partition_id: str,
        summary: str = "",
        keywords: list[str] | None = None,
        entities: list[str] | None = None,
        importance: float = 0.5,
        canon_kind: str = "none",
    ) -> bool:
        if partition_id not in _PARTITION_IDS:
            raise ValueError("未知记忆分区")
        if canon_kind not in {"none", "canon", "shared"}:
            raise ValueError("未知世界观记忆类型")
        if partition_id != "world-lore" and canon_kind != "none":
            raise ValueError("仅世界观分区可标记 Canon 或共同故事")
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE memory_fragments SET
                     partition_id=?,summary=?,keywords_json=?,entities_json=?,importance=?,canon_kind=?,updated_at=?
                   WHERE fragment_id=? AND forgotten=0""",
                (
                    partition_id,
                    _compact_text(summary)[:2_000],
                    json.dumps(keywords or [], ensure_ascii=False),
                    json.dumps(entities or [], ensure_ascii=False),
                    min(1.0, max(0.0, float(importance))),
                    canon_kind,
                    now,
                    fragment_id,
                ),
            )
        return bool(cursor.rowcount)

    def classify_pending_memory_fragment(
        self,
        fragment_id: str,
        *,
        expected_updated_at: str,
        partition_id: str,
        summary: str = "",
        keywords: list[str] | None = None,
        entities: list[str] | None = None,
        importance: float = 0.5,
        canon_kind: str = "none",
    ) -> bool:
        """CAS-classify a still-pending fragment and refresh its summary atomically."""

        if partition_id not in _PARTITION_IDS:
            raise ValueError("未知记忆分区")
        if canon_kind not in {"none", "canon", "shared"}:
            raise ValueError("未知世界观记忆类型")
        if partition_id != "world-lore" and canon_kind != "none":
            raise ValueError("仅世界观分区可标记 Canon 或共同故事")
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE memory_fragments SET
                     partition_id=?,summary=?,keywords_json=?,entities_json=?,importance=?,canon_kind=?,updated_at=?
                   WHERE fragment_id=? AND partition_id='unfiled'
                     AND enabled=1 AND forgotten=0 AND updated_at=?""",
                (
                    partition_id,
                    _compact_text(summary)[:2_000],
                    json.dumps(keywords or [], ensure_ascii=False),
                    json.dumps(entities or [], ensure_ascii=False),
                    min(1.0, max(0.0, float(importance))),
                    canon_kind,
                    now,
                    str(fragment_id),
                    str(expected_updated_at),
                ),
            )
            if cursor.rowcount != 1:
                return False

            rows = db.execute(
                """SELECT summary FROM memory_fragments
                   WHERE partition_id=? AND enabled=1 AND forgotten=0
                   ORDER BY importance DESC,updated_at DESC LIMIT 12""",
                (partition_id,),
            ).fetchall()
            summaries: list[str] = []
            for row in rows:
                candidate = _compact_text(str(row["summary"] or ""))
                if candidate and candidate not in summaries:
                    summaries.append(candidate[:180])
                if len(summaries) >= 4:
                    break
            partition_summary = "；".join(summaries)[:720]
            if not partition_summary:
                partition_summary = next(
                    (
                        str(value["description"])
                        for value in MEMORY_PARTITIONS
                        if value["partition_id"] == partition_id
                    ),
                    "",
                )
            summary_cursor = db.execute(
                "UPDATE memory_partitions SET summary=?,updated_at=? WHERE partition_id=?",
                (_compact_text(partition_summary)[:1_200], now, partition_id),
            )
            if summary_cursor.rowcount != 1:
                raise RuntimeError("记忆分区摘要刷新失败")

            setting = db.execute(
                "SELECT value_json FROM settings WHERE key=?",
                (_MEMORY_ARCHIVAL_ATTEMPTS_SETTING,),
            ).fetchone()
            try:
                decoded_attempts = json.loads(setting["value_json"]) if setting else {}
            except (TypeError, ValueError):
                decoded_attempts = {}
            attempts = (
                {str(key): str(value) for key, value in decoded_attempts.items()}
                if isinstance(decoded_attempts, dict)
                else {}
            )
            if partition_id == "unfiled":
                attempts[str(fragment_id)] = now
            else:
                attempts.pop(str(fragment_id), None)
            db.execute(
                """INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (
                    _MEMORY_ARCHIVAL_ATTEMPTS_SETTING,
                    json.dumps(attempts, ensure_ascii=False),
                    now,
                ),
            )
        return True

    def memory_fragments(
        self,
        partition_id: str | None = None,
        *,
        include_forgotten: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if partition_id:
            if partition_id not in _PARTITION_IDS:
                return []
            clauses.append("partition_id=?")
            parameters.append(partition_id)
        if not include_forgotten:
            clauses.append("enabled=1 AND forgotten=0")
        sql = "SELECT * FROM memory_fragments"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY importance DESC,updated_at DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 1000)))
        with self.connect() as db:
            rows = db.execute(sql, parameters).fetchall()
        return [self._decode_memory_row(row) for row in rows]

    def move_memory_fragment(self, fragment_id: str, partition_id: str) -> bool:
        """Move an indexed fragment without rewriting its source or summary."""

        if partition_id not in _PARTITION_IDS:
            raise ValueError("未知记忆分区")
        with self.connect() as db:
            row = db.execute(
                "SELECT canon_kind FROM memory_fragments WHERE fragment_id=?",
                (str(fragment_id),),
            ).fetchone()
            if row is None:
                return False
            canon_kind = str(row["canon_kind"] or "none") if partition_id == "world-lore" else "none"
            cursor = db.execute(
                """UPDATE memory_fragments
                   SET partition_id=?,canon_kind=?,updated_at=?
                   WHERE fragment_id=?""",
                (partition_id, canon_kind, utc_now(), str(fragment_id)),
            )
        return bool(cursor.rowcount)

    def recall_memory_candidates(
        self,
        query: str,
        partition_ids: list[str] | None = None,
        *,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        selected = [value for value in (partition_ids or []) if value in _PARTITION_IDS]
        clauses = ["f.enabled=1", "f.forgotten=0", "p.enabled=1"]
        parameters: list[Any] = []
        if selected:
            clauses.append("f.partition_id IN (" + ",".join("?" for _ in selected) + ")")
            parameters.extend(selected)
        if start_at:
            clauses.append("f.created_at>=?")
            parameters.append(start_at)
        if end_at:
            clauses.append("f.created_at<=?")
            parameters.append(end_at)
        safe_limit = max(1, min(int(limit), 100))
        tokens = memory_search_tokens(query, max_tokens=96)
        with self.connect() as db:
            if tokens:
                token_marks = ",".join("?" for _ in tokens)
                sql = f"""SELECT f.*,p.name AS partition_name,COUNT(DISTINCT n.token) AS match_count
                          FROM memory_fragments f
                          JOIN memory_partitions p ON p.partition_id=f.partition_id
                          JOIN memory_ngrams n ON n.fragment_id=f.fragment_id
                          WHERE {' AND '.join(clauses)} AND n.token IN ({token_marks})
                          GROUP BY f.fragment_id
                          ORDER BY match_count DESC,f.importance DESC,f.created_at DESC LIMIT ?"""
                rows = db.execute(sql, (*parameters, *tokens, safe_limit)).fetchall()
            else:
                sql = f"""SELECT f.*,p.name AS partition_name,0 AS match_count
                          FROM memory_fragments f
                          JOIN memory_partitions p ON p.partition_id=f.partition_id
                          WHERE {' AND '.join(clauses)}
                          ORDER BY f.importance DESC,f.created_at DESC LIMIT ?"""
                rows = db.execute(sql, (*parameters, safe_limit)).fetchall()
        return [self._decode_memory_row(row) for row in rows]

    def message_pair(self, fragment_id: str) -> list[dict[str, Any]]:
        """Return a recalled message plus one adjacent opposite-role message."""

        with self.connect() as db:
            fragment = db.execute(
                """SELECT source_id,conversation_id,role,created_at FROM memory_fragments
                   WHERE fragment_id=? AND source_type='message'""",
                (fragment_id,),
            ).fetchone()
            if not fragment or not fragment["conversation_id"]:
                return []
            current = db.execute(
                """SELECT message_id,role,content,created_at FROM messages WHERE message_id=?""",
                (fragment["source_id"],),
            ).fetchone()
            if not current:
                return []
            neighbor = db.execute(
                """SELECT message_id,role,content,created_at FROM messages
                   WHERE conversation_id=? AND message_id<>? AND role<>?
                   ORDER BY ABS(julianday(created_at)-julianday(?)) LIMIT 1""",
                (
                    fragment["conversation_id"],
                    fragment["source_id"],
                    fragment["role"],
                    fragment["created_at"],
                ),
            ).fetchone()
        rows = [dict(current)]
        if neighbor:
            rows.append(dict(neighbor))
            rows.sort(key=lambda value: value["created_at"])
        return rows

    def log_memory_recall(
        self,
        *,
        turn_id: str,
        reason: str,
        query: str,
        partition_ids: list[str],
        result_ids: list[str],
    ) -> str:
        recall_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO memory_recall_log
                   (recall_id,turn_id,reason,query,partition_ids_json,result_ids_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    recall_id,
                    str(turn_id)[:160],
                    str(reason)[:240],
                    _compact_text(query)[:2_000],
                    json.dumps(partition_ids, ensure_ascii=False),
                    json.dumps(result_ids, ensure_ascii=False),
                    now,
                ),
            )
            if result_ids:
                marks = ",".join("?" for _ in result_ids)
                db.execute(
                    f"UPDATE memory_fragments SET last_recalled_at=? WHERE fragment_id IN ({marks})",
                    (now, *result_ids),
                )
        return recall_id

    def memory_recall_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_recall_log ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            for source, target in (
                ("partition_ids_json", "partition_ids"),
                ("result_ids_json", "result_ids"),
            ):
                try:
                    value[target] = json.loads(value.pop(source))
                except (TypeError, ValueError):
                    value.pop(source, None)
                    value[target] = []
            result.append(value)
        return result

    def forget_memory_fragment(self, fragment_id: str, delete_source: bool = False) -> dict[str, Any]:
        """Exclude from retrieval; optionally remove the explicitly linked source."""

        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM memory_fragments WHERE fragment_id=?",
                (fragment_id,),
            ).fetchone()
            if not row:
                return {"forgotten": False, "sourceDeleted": False}
            db.execute(
                "UPDATE memory_fragments SET forgotten=1,enabled=0,updated_at=? WHERE fragment_id=?",
                (utc_now(), fragment_id),
            )
            db.execute("DELETE FROM memory_ngrams WHERE fragment_id=?", (fragment_id,))
            source_deleted = False
            if row["source_type"] == "memory-card":
                if delete_source:
                    db.execute("DELETE FROM memory_cards WHERE memory_id=?", (row["source_id"],))
                    db.execute("DELETE FROM memory_fragments WHERE fragment_id=?", (fragment_id,))
                    source_deleted = True
                else:
                    db.execute("UPDATE memory_cards SET enabled=0,updated_at=? WHERE memory_id=?", (utc_now(), row["source_id"]))
            elif row["source_type"] == "reading-card" and delete_source:
                db.execute("DELETE FROM reading_cards WHERE card_id=?", (row["source_id"],))
                db.execute("DELETE FROM memory_fragments WHERE fragment_id=?", (fragment_id,))
                source_deleted = True
            elif row["source_type"] == "message" and delete_source and row["conversation_id"]:
                # "同时删除原对话" deliberately removes the whole short/local
                # conversation, never just one half of a dialogue pair.
                conversation_id = str(row["conversation_id"])
                message_ids = [
                    str(value["message_id"])
                    for value in db.execute(
                        "SELECT message_id FROM messages WHERE conversation_id=?", (conversation_id,)
                    ).fetchall()
                ]
                db.execute("DELETE FROM conversations WHERE conversation_id=?", (conversation_id,))
                if message_ids:
                    marks = ",".join("?" for _ in message_ids)
                    db.execute(
                        f"DELETE FROM memory_fragments WHERE source_type='message' AND source_id IN ({marks})",
                        message_ids,
                    )
                source_deleted = True
            elif row["source_type"] == "companion-message" and delete_source:
                session = db.execute(
                    "SELECT session_id FROM proactive_messages WHERE message_id=?",
                    (row["source_id"],),
                ).fetchone()
                if session:
                    session_id = str(session["session_id"])
                    message_ids = [
                        str(value["message_id"])
                        for value in db.execute(
                            "SELECT message_id FROM proactive_messages WHERE session_id=?",
                            (session_id,),
                        ).fetchall()
                    ]
                    db.execute("DELETE FROM proactive_sessions WHERE session_id=?", (session_id,))
                    if message_ids:
                        marks = ",".join("?" for _ in message_ids)
                        db.execute(
                            f"DELETE FROM memory_fragments "
                            f"WHERE source_type='companion-message' AND source_id IN ({marks})",
                            message_ids,
                        )
                    source_deleted = True
            elif delete_source:
                # Synthetic observations have no second source row.  Removing
                # their fragment is the complete destructive operation.
                db.execute("DELETE FROM memory_fragments WHERE fragment_id=?", (fragment_id,))
                source_deleted = True
        return {"forgotten": True, "sourceDeleted": source_deleted}

    def reindex_memories(self) -> dict[str, int]:
        with self.connect() as db:
            self._backfill_memory_index(db)
            rows = db.execute(
                "SELECT fragment_id,content,enabled,forgotten FROM memory_fragments"
            ).fetchall()
            for row in rows:
                self._replace_fragment_tokens(
                    db,
                    str(row["fragment_id"]),
                    str(row["content"]),
                    bool(row["enabled"]),
                    bool(row["forgotten"]),
                )
            token_count = db.execute("SELECT COUNT(*) AS value FROM memory_ngrams").fetchone()["value"]
        return {"fragments": len(rows), "tokens": int(token_count)}

    def integrity_check(self) -> str:
        with self.connect() as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"

    def save_proactive_session(
        self,
        *,
        session_id: str,
        bubble: dict[str, Any],
        generation: Mapping[str, Any] | None = None,
        moved_to_box: bool = False,
    ) -> str:
        now = utc_now()
        bubble_id = str(bubble.get("id", ""))[:160]
        if not session_id or not bubble_id:
            raise ValueError("主动气泡会话缺少标识")
        source = bubble.get("source") if isinstance(bubble.get("source"), dict) else {}
        generation_receipt = _normalize_proactive_generation(generation)
        with self.connect() as db:
            db.execute(
                """INSERT INTO proactive_sessions
                   (session_id,bubble_id,category,summary,detail,source_json,generation_json,
                    scene_label,moved_to_box,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     summary=excluded.summary,detail=excluded.detail,source_json=excluded.source_json,
                     scene_label=excluded.scene_label,moved_to_box=excluded.moved_to_box,
                     updated_at=excluded.updated_at""",
                (
                    str(session_id)[:160],
                    bubble_id,
                    str(bubble.get("category", ""))[:40],
                    _compact_text(str(bubble.get("summary", "")))[:4_000],
                    str(bubble.get("detail", ""))[:12_000],
                    json.dumps(source, ensure_ascii=False),
                    json.dumps(
                        generation_receipt,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    _compact_text(str(bubble.get("sceneLabel", "")))[:160],
                    int(moved_to_box),
                    str(bubble.get("createdAt") or now)[:80],
                    now,
                ),
            )
        return str(session_id)

    def add_proactive_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        memory_eligible: bool = True,
    ) -> str:
        current_role = str(role).casefold()
        if current_role not in {"user", "assistant"}:
            raise ValueError("主动会话角色无效")
        clean = _compact_text(content)[:12_000]
        if not clean:
            raise ValueError("主动会话消息不能为空")
        message_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as db:
            known = db.execute(
                "SELECT 1 FROM proactive_sessions WHERE session_id=?", (str(session_id),)
            ).fetchone()
            if not known:
                raise KeyError("主动气泡会话不存在")
            db.execute(
                """INSERT INTO proactive_messages(message_id,session_id,role,content,created_at)
                   VALUES(?,?,?,?,?)""",
                (message_id, str(session_id), current_role, clean, now),
            )
            db.execute(
                "UPDATE proactive_sessions SET updated_at=? WHERE session_id=?",
                (now, str(session_id)),
            )
            if memory_eligible:
                self._upsert_memory_fragment(
                    db,
                    source_type="companion-message",
                    source_id=message_id,
                    content=clean,
                    partition_id="unfiled",
                    role=current_role,
                    importance=0.55 if current_role == "user" else 0.4,
                    created_at=now,
                )
        return message_id

    def save_proactive_generation(
        self,
        session_id: str,
        generation: Mapping[str, Any] | None,
    ) -> None:
        """Attach a bounded, content-safe generation receipt to one bubble.

        This is intentionally a strict allowlist.  Callers cannot accidentally
        persist a screenshot, HWND, title, path, raw activity metadata or model
        exception through this audit channel.
        """

        receipt = _normalize_proactive_generation(generation)
        if not receipt:
            raise ValueError("主动气泡生成凭据无效")
        encoded = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE proactive_sessions SET generation_json=?,updated_at=? "
                "WHERE session_id=? AND generation_json='{}'",
                (
                    encoded,
                    utc_now(),
                    str(session_id)[:160],
                ),
            )
            if cursor.rowcount == 1:
                return
            existing = db.execute(
                "SELECT generation_json FROM proactive_sessions WHERE session_id=?",
                (str(session_id)[:160],),
            ).fetchone()
            if existing is None:
                raise KeyError("主动气泡会话不存在")
            if str(existing["generation_json"]) != encoded:
                raise RuntimeError("主动气泡生成凭据不可覆盖")

    def proactive_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM proactive_sessions WHERE session_id=?", (str(session_id),)
            ).fetchone()
            if not row:
                return None
            messages = db.execute(
                "SELECT message_id,role,content,created_at FROM proactive_messages "
                "WHERE session_id=? ORDER BY created_at",
                (str(session_id),),
            ).fetchall()
        value = dict(row)
        try:
            value["source"] = json.loads(value.pop("source_json"))
        except (TypeError, ValueError):
            value.pop("source_json", None)
            value["source"] = {}
        try:
            parsed_generation = json.loads(value.pop("generation_json"))
            value["generation"] = (
                parsed_generation if isinstance(parsed_generation, dict) else {}
            )
        except (TypeError, ValueError):
            value.pop("generation_json", None)
            value["generation"] = {}
        value["messages"] = [dict(item) for item in messages]
        return value

    def recent_proactive_summaries(self, limit: int = 12) -> list[str]:
        """Return recent Lilies-authored summaries, oldest first.

        This reuses the existing proactive session store, so repetition
        suppression survives a restart without adding another prose cache.
        Only generated bubble summaries are returned; captured pixels, window
        titles and user replies never enter this path.
        """

        bounded = max(1, min(int(limit), 40))
        with self.connect() as db:
            rows = db.execute(
                "SELECT summary FROM proactive_sessions "
                "WHERE summary <> '' ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [str(row["summary"]) for row in reversed(rows) if str(row["summary"]).strip()]

    def recent_proactive_prose(self, limit: int = 12) -> list[dict[str, str]]:
        """Return recent generated bubble prose, oldest first.

        ``proactive_sessions`` already stores the generated summary and detail,
        so combined-prose novelty can survive a restart without another cache
        or schema migration.  User replies live in ``proactive_messages`` and
        captured pixels/window metadata are not read by this API.
        """

        bounded = max(1, min(int(limit), 40))
        with self.connect() as db:
            rows = db.execute(
                "SELECT summary,detail FROM proactive_sessions "
                "WHERE summary <> '' ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [
            {
                "summary": str(row["summary"]),
                "detail": str(row["detail"] or ""),
            }
            for row in reversed(rows)
            if str(row["summary"]).strip()
        ]

    def content_cache_get(self, cache_key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT items_json,stored_at FROM content_cache WHERE cache_key=?",
                (str(cache_key),),
            ).fetchone()
        if not row:
            return None
        try:
            items = json.loads(row["items_json"])
        except (TypeError, ValueError):
            return None
        return {"items": items if isinstance(items, list) else [], "storedAt": row["stored_at"]}

    def content_cache_put(self, cache_key: str, items: list[dict[str, Any]], stored_at: str) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO content_cache(cache_key,items_json,stored_at,updated_at)
                   VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                     items_json=excluded.items_json,stored_at=excluded.stored_at,updated_at=excluded.updated_at""",
                (str(cache_key)[:160], json.dumps(items, ensure_ascii=False), str(stored_at), utc_now()),
            )

    def log_desktop_peek(self, action: str, result: dict[str, Any]) -> str:
        event_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO desktop_peek_log(event_id,action,result_json,created_at) VALUES(?,?,?,?)",
                (event_id, str(action)[:40], json.dumps(result, ensure_ascii=False), utc_now()),
            )
        return event_id

    def backup_to(self, destination: Path | str) -> Path:
        if self._memory_database:
            raise RuntimeError("受限恢复数据库不写入磁盘备份")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as output:
            source.backup(output)
        return target

    def audit(
        self,
        origin: str,
        component_id: str,
        action_id: str,
        risk: str,
        decision: str,
        payload: dict[str, Any],
    ) -> str:
        value = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                """INSERT INTO audit_log
                   (audit_id,origin,component_id,action_id,risk,decision,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    value,
                    origin,
                    component_id,
                    action_id,
                    risk,
                    decision,
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )
        return value

    def complete_audit(
        self,
        audit_id: str,
        *,
        result: Any = None,
        error: BaseException | dict[str, Any] | str | None = None,
    ) -> bool:
        """Attach a size-limited handler outcome without leaking credentials."""

        def encoded(value: Any) -> str:
            try:
                raw = json.dumps(value, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                raw = json.dumps({"type": type(value).__name__}, ensure_ascii=False)
            # Component results are diagnostics, not an alternate content
            # store.  Keep enough for review while bounding private content.
            return raw[:65_536]

        error_value: Any = None
        if isinstance(error, BaseException):
            error_value = {"type": type(error).__name__, "message": str(error)[:2000]}
        elif error is not None:
            error_value = error
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE audit_log SET result_json=?,error_json=?,completed_at=?
                   WHERE audit_id=?""",
                (
                    None if error_value is not None else encoded(result),
                    encoded(error_value) if error_value is not None else None,
                    utc_now(),
                    str(audit_id),
                ),
            )
        return bool(cursor.rowcount)

    def redact_connector_audits(self, connector_id: str) -> int:
        """Retain audit decisions while removing any connector content copy."""

        normalized = (
            "calendar"
            if connector_id in {"calendar", "google-calendar"}
            else str(connector_id)
        )
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE audit_log
                   SET payload_json='{"redacted":true,"reason":"content-cleared"}',
                       result_json=CASE WHEN result_json IS NULL THEN NULL
                           ELSE '{"redacted":true,"reason":"content-cleared"}' END,
                       error_json=CASE WHEN error_json IS NULL THEN NULL
                           ELSE '{"redacted":true,"reason":"content-cleared"}' END
                   WHERE component_id=?""",
                (normalized,),
            )
        return max(0, int(cursor.rowcount))
