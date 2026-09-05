"""Failed BEGIN must preserve the real SQLite error and must not roll back another scope."""

import sqlite3
from contextlib import closing

import pytest

from local_control_center.shared.db import immediate_transaction, open_sqlite_connection


def test_busy_begin_keeps_original_error(tmp_path):
    path = tmp_path / "contention.sqlite"
    with closing(open_sqlite_connection(path)) as owner, closing(open_sqlite_connection(path)) as contender:
        owner.execute("CREATE TABLE fixture (value INTEGER)")
        contender.execute("PRAGMA busy_timeout=1")
        with immediate_transaction(owner):
            with pytest.raises(sqlite3.OperationalError) as error, immediate_transaction(contender):
                pytest.fail("A second writer cannot enter the transaction")
            assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert not contender.in_transaction


def test_nested_begin_failure_does_not_rollback_outer_work(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "nested.sqlite")) as connection:
        connection.execute("CREATE TABLE fixture (value INTEGER)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO fixture VALUES (1)")
        with pytest.raises(sqlite3.OperationalError), immediate_transaction(connection):
            pytest.fail("Nested BEGIN is not a savepoint")
        assert connection.in_transaction
        assert connection.execute("SELECT value FROM fixture").fetchone()[0] == 1
        connection.rollback()
