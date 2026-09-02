from __future__ import annotations

import queue
import sqlite3
import threading

import pytest

from lilies.core.database import Database


def _create_probe_table(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session_probe(value TEXT NOT NULL)"
        )


def _probe_values(database: Database) -> list[str]:
    with database.connect() as connection:
        return [
            str(row["value"])
            for row in connection.execute(
                "SELECT value FROM session_probe ORDER BY rowid"
            ).fetchall()
        ]


def test_session_reuses_one_connection_while_each_connect_commits(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "session.db")
    opened: list[sqlite3.Connection] = []
    original_open = database._open_connection

    def counted_open() -> sqlite3.Connection:
        connection = original_open()
        opened.append(connection)
        return connection

    monkeypatch.setattr(database, "_open_connection", counted_open)

    with database.connection_session():
        _create_probe_table(database)
        with database.connect() as first:
            first.execute("INSERT INTO session_probe(value) VALUES('first')")
        with database.connect() as second:
            second.execute("INSERT INTO session_probe(value) VALUES('second')")
        assert first is second

        # A separate reader observes both per-operation commits before the
        # reusable session itself ends.
        reader = sqlite3.connect(database.path)
        try:
            count = reader.execute("SELECT COUNT(*) FROM session_probe").fetchone()[0]
            assert count == 2
        finally:
            reader.close()

    assert len(opened) == 1
    assert _probe_values(database) == ["first", "second"]


def test_failed_operation_rolls_back_without_poisoning_reused_session(tmp_path) -> None:
    database = Database(tmp_path / "rollback.db")
    with database.connection_session():
        _create_probe_table(database)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO session_probe(value) VALUES('rolled-back')"
                )
                raise RuntimeError("synthetic failure")
        with database.connect() as connection:
            connection.execute("INSERT INTO session_probe(value) VALUES('kept')")

    assert _probe_values(database) == ["kept"]


def test_nested_connect_success_does_not_escape_outer_rollback(tmp_path) -> None:
    database = Database(tmp_path / "nested-operation-rollback.db")
    with database.connection_session():
        _create_probe_table(database)
        with pytest.raises(RuntimeError, match="outer failure"):
            with database.connect() as outer:
                outer.execute(
                    "INSERT INTO session_probe(value) VALUES('outer')"
                )
                with database.connect() as inner:
                    assert inner is outer
                    inner.execute(
                        "INSERT INTO session_probe(value) VALUES('inner')"
                    )
                raise RuntimeError("outer failure")

    assert _probe_values(database) == []


def test_nested_connect_failure_rolls_back_savepoint_and_outer_can_commit(
    tmp_path,
) -> None:
    database = Database(tmp_path / "nested-operation-savepoint.db")
    with database.connection_session():
        _create_probe_table(database)
        with database.connect() as outer:
            outer.execute(
                "INSERT INTO session_probe(value) VALUES('before')"
            )
            with pytest.raises(RuntimeError, match="inner failure"):
                with database.connect() as inner:
                    inner.execute(
                        "INSERT INTO session_probe(value) VALUES('discarded')"
                    )
                    raise RuntimeError("inner failure")
            outer.execute(
                "INSERT INTO session_probe(value) VALUES('after')"
            )

    assert _probe_values(database) == ["before", "after"]


def test_session_cannot_close_while_connect_operation_is_active(tmp_path) -> None:
    database = Database(tmp_path / "active-operation.db")
    session = database.connection_session()
    session.__enter__()
    with database.connect() as connection:
        connection.execute("CREATE TABLE active_probe(value INTEGER)")
        with pytest.raises(RuntimeError, match="active operation"):
            session.close()
        connection.execute("INSERT INTO active_probe VALUES(1)")
    session.close()

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM active_probe"
        ).fetchone()[0]
    assert count == 1


def test_nested_sessions_reuse_connection_and_close_only_after_outer_exit(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "nested.db")
    opened = 0
    original_open = database._open_connection

    def counted_open() -> sqlite3.Connection:
        nonlocal opened
        opened += 1
        return original_open()

    monkeypatch.setattr(database, "_open_connection", counted_open)
    with database.connection_session():
        with database.connect() as outer_connection:
            outer_connection.execute("CREATE TABLE nested_probe(value INTEGER)")
        with database.connection_session():
            with database.connect() as inner_connection:
                inner_connection.execute("INSERT INTO nested_probe VALUES(1)")
        with database.connect() as final_connection:
            count = final_connection.execute(
                "SELECT COUNT(*) FROM nested_probe"
            ).fetchone()[0]
            assert count == 1
        assert outer_connection is inner_connection is final_connection
    assert opened == 1


def test_session_connections_are_isolated_by_thread(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "threads.db")
    _create_probe_table(database)
    opened_threads: list[int] = []
    original_open = database._open_connection

    def counted_open() -> sqlite3.Connection:
        opened_threads.append(threading.get_ident())
        return original_open()

    monkeypatch.setattr(database, "_open_connection", counted_open)
    barrier = threading.Barrier(2)
    connection_ids: queue.Queue[int] = queue.Queue()

    def worker(value: str) -> None:
        with database.connection_session():
            with database.connect() as connection:
                connection_ids.put(id(connection))
            barrier.wait(timeout=5)
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO session_probe(value) VALUES(?)", (value,)
                )

    threads = [
        threading.Thread(target=worker, args=("one",)),
        threading.Thread(target=worker, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert len(set(connection_ids.get_nowait() for _ in threads)) == 2
    assert len(opened_threads) == 2
    assert len(set(opened_threads)) == 2
    assert sorted(_probe_values(database)) == ["one", "two"]


def test_session_handle_rejects_cross_thread_close_and_owner_can_recover(
    tmp_path,
) -> None:
    database = Database(tmp_path / "ownership.db")
    session = database.connection_session()
    session.__enter__()
    errors: queue.Queue[BaseException] = queue.Queue()

    def close_from_wrong_thread() -> None:
        try:
            session.close()
        except BaseException as error:
            errors.put(error)

    thread = threading.Thread(target=close_from_wrong_thread)
    thread.start()
    thread.join(timeout=5)

    error = errors.get_nowait()
    assert isinstance(error, RuntimeError)
    assert "owner thread" in str(error)
    with database.connect() as connection:
        connection.execute("CREATE TABLE ownership_probe(value INTEGER)")
    session.close()


def test_distinct_thread_objects_cannot_alias_when_numeric_ident_is_reused(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "thread-token.db")
    first_owner = threading.Thread(name="expired-owner-token")
    second_owner = threading.Thread(name="replacement-owner-token")
    current_owner = [first_owner]
    numeric_ident = threading.get_ident()
    monkeypatch.setattr(threading, "current_thread", lambda: current_owner[0])

    first_session = database.connection_session()
    first_session.__enter__()
    with database.connect() as first_connection:
        pass

    # This emulates an OS/Python numeric thread ID being recycled: execution
    # still has the same get_ident(), but the lifetime token is a new Thread.
    current_owner[0] = second_owner
    assert threading.get_ident() == numeric_ident
    second_session = database.connection_session()
    second_session.__enter__()
    with database.connect() as second_connection:
        pass

    assert first_connection is not second_connection
    assert set(database._connection_sessions) == {first_owner, second_owner}

    second_session.close()
    current_owner[0] = first_owner
    first_session.close()


def test_database_close_fails_explicitly_during_active_session(tmp_path) -> None:
    database = Database(tmp_path / "close.db")
    with database.connection_session():
        with pytest.raises(RuntimeError, match="current thread"):
            database.close()
        database.set_setting("still_usable", True)
    database.close()


def test_memory_database_session_preserves_state_and_nested_semantics() -> None:
    database = Database(":memory:")
    with database.connection_session():
        database.set_setting("session-memory", {"value": 1})
        with database.connection_session():
            assert database.get_setting("session-memory") == {"value": 1}
    assert database.get_setting("session-memory") == {"value": 1}
    database.close()


def test_without_session_connect_retains_short_connection_semantics(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "short.db")
    opened = 0
    original_open = database._open_connection

    def counted_open() -> sqlite3.Connection:
        nonlocal opened
        opened += 1
        return original_open()

    monkeypatch.setattr(database, "_open_connection", counted_open)
    with database.connect() as first:
        first.execute("CREATE TABLE short_probe(value INTEGER)")
    with database.connect() as second:
        second.execute("INSERT INTO short_probe VALUES(1)")

    assert opened == 2
    assert first is not second
