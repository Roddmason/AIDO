from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import anyio
from fastapi import Request
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.shared.db import open_sqlite_connection


def _app(tmp_path: Path) -> tuple[ControlCenterRuntime, Any]:
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, create_app(runtime=runtime, static_dir=None)


def test_each_http_request_owns_a_distinct_short_lived_connection(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)
    connections: list[sqlite3.Connection] = []
    both_entered = threading.Event()
    release = threading.Event()
    guard = threading.Lock()

    @app.get("/api/v1/_p0-test/database-connection")
    async def inspect_connection() -> dict[str, int]:
        connection = runtime.connection
        with guard:
            connections.append(connection)
            if len(connections) == 2:
                both_entered.set()
        await anyio.to_thread.run_sync(release.wait)
        return {"connectionId": id(connection)}

    with TestClient(app) as client:
        responses: list[Any] = []
        threads = [
            threading.Thread(
                target=lambda: responses.append(client.get("/api/v1/_p0-test/database-connection")),
                daemon=True,
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        reached_both_requests = both_entered.wait(timeout=2)
        release.set()
        for thread in threads:
            thread.join(timeout=2)

    runtime.close()
    assert reached_both_requests, "the second request remained serialized behind the first"
    assert all(response.status_code == 200 for response in responses)
    assert len({id(connection) for connection in connections}) == 2
    for connection in connections:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            pass
        else:  # pragma: no cover - mensaje de aserción más útil que pytest.raises dentro del loop
            raise AssertionError("request-scoped SQLite connection remained open after the response")


def test_overview_is_not_serialized_behind_an_unrelated_request(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    @app.get("/api/v1/_p0-test/unrelated-slow-operation")
    async def slow_operation() -> dict[str, str]:
        runtime.connection.execute("SELECT 1")
        entered.set()
        await anyio.to_thread.run_sync(release.wait)
        return {"status": "completed"}

    with TestClient(app) as client:
        thread = threading.Thread(
            target=lambda: client.get("/api/v1/_p0-test/unrelated-slow-operation"),
            daemon=True,
        )
        thread.start()
        assert entered.wait(timeout=2)
        release_timer = threading.Timer(0.75, release.set)
        release_timer.start()
        started = time.perf_counter()
        response = client.get("/api/v1/overview")
        elapsed_seconds = time.perf_counter() - started
        release.set()
        thread.join(timeout=2)
        release_timer.cancel()

    runtime.close()
    assert response.status_code == 200
    assert elapsed_seconds < 0.3


def test_database_diagnostics_expose_wal_and_passive_checkpoint(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)

    with TestClient(app) as client:
        status = client.get("/api/v1/operations/database")
        token = client.get("/api/v1/security/handshake").json()["token"]
        checkpoint = client.post(
            "/api/v1/operations/database/checkpoint",
            headers={"X-Local-Control-Token": token},
        )

    runtime.close()
    assert status.status_code == 200
    assert status.json()["sqliteVersion"]
    assert status.json()["journalMode"].lower() == "wal"
    assert status.json()["busyTimeoutMs"] == 30_000
    assert status.json()["walBytes"] >= 0
    assert status.json()["walAutoCheckpointPages"] > 0
    assert checkpoint.status_code == 200
    assert checkpoint.json()["mode"] == "PASSIVE"
    assert checkpoint.json()["busy"] in {0, 1}
    assert checkpoint.json()["logFrames"] >= 0
    assert checkpoint.json()["checkpointedFrames"] >= 0


def test_database_checkpoint_requires_loopback_write_token(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/v1/operations/database/checkpoint")

    runtime.close()
    assert response.status_code == 403


def test_operation_releases_transaction_before_external_wait(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)
    transaction_state_during_external_call: list[bool] = []

    @app.post("/api/v1/_p0-test/transaction-boundary")
    async def transaction_boundary(request: Request) -> dict[str, str]:
        token = runtime.get_handshake()["token"]
        assert request.headers["X-Local-Control-Token"] == token
        connection = runtime.connection
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("COMMIT")
        await anyio.to_thread.run_sync(
            lambda: transaction_state_during_external_call.append(connection.in_transaction)
        )
        return {"status": "completed"}

    with TestClient(app) as client:
        token = client.get("/api/v1/security/handshake").json()["token"]
        response = client.post(
            "/api/v1/_p0-test/transaction-boundary",
            headers={"X-Local-Control-Token": token},
        )

    runtime.close()
    assert response.status_code == 200
    assert transaction_state_during_external_call == [False]


def test_overview_remains_responsive_while_worker_connection_is_writing(tmp_path: Path) -> None:
    runtime, app = _app(tmp_path)
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def hold_worker_write_lock() -> None:
        with closing(open_sqlite_connection(runtime.db_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            writer_entered.set()
            release_writer.wait(timeout=2)
            connection.execute("ROLLBACK")

    writer = threading.Thread(target=hold_worker_write_lock, daemon=True)
    with TestClient(app) as client:
        writer.start()
        assert writer_entered.wait(timeout=2)
        release_timer = threading.Timer(0.75, release_writer.set)
        release_timer.start()
        started = time.perf_counter()
        response = client.get("/api/v1/overview")
        elapsed_seconds = time.perf_counter() - started
        release_writer.set()
        writer.join(timeout=2)
        release_timer.cancel()

    runtime.close()
    assert response.status_code == 200
    assert elapsed_seconds < 0.3
