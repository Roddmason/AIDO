"""Regresiones de la auditoría de dureza de remediaciones (octubre 2026).

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.remediations.service import (
    ACTION_HANDLERS,
    GIT_ACTION_HANDLERS,
    BlockerRemediationService,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


class _FakeLoopRepository:
    """Loop store whose update hook simulates a concurrent worker writing between read and write."""

    def __init__(self, db_path: Path, loop: dict[str, Any]):
        self.db_path = db_path
        self.loop = loop
        self.concurrent_write_blocked: bool | None = None

    def update_loop_context(self, loop_id: str, *, context: dict[str, Any]) -> dict[str, Any]:
        with closing(sqlite3.connect(self.db_path, timeout=0, isolation_level=None)) as other:
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                self.concurrent_write_blocked = True
            else:
                self.concurrent_write_blocked = False
                self.loop["context"] = {**self.loop["context"], "concurrentWorkerKey": "kept"}
                other.execute("ROLLBACK")
        self.loop["context"] = context
        return self.loop


class _FakeCoordinator:
    def __init__(self, repository: _FakeLoopRepository):
        self.repository = repository

    def get(self, loop_id: str) -> dict[str, Any]:
        return {**self.repository.loop, "context": dict(self.repository.loop["context"])}


def test_approve_resource_decision_does_not_lose_concurrent_loop_context_updates(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection:
        initialize_platform_schema(connection)
        loop = {
            "id": "loop-1",
            "state": "blocked",
            "context": {"durableRun": {"blockedStage": "resource_manager"}, "other": 1},
        }
        repository = _FakeLoopRepository(db_path, loop)
        service = BlockerRemediationService(
            connection,
            root=tmp_path,
            coordinator_factory=lambda _connection, _root: _FakeCoordinator(repository),
        )
        action = {
            "id": "action-1",
            "loopId": "loop-1",
            "payload": {
                "role": "developer",
                "providerId": "p",
                "model": "m",
                "runtime": "api",
                "approvalRequired": True,
            },
        }

        result = service._approve_resource_decision(action=action, payload={})

        assert result["status"] == "completed"
        final = repository.loop["context"]
        assert repository.concurrent_write_blocked or final.get("concurrentWorkerKey") == "kept"
        assert final["durableRun"]["resourceApproval"]["status"] == "approved"


def test_coordinator_factory_is_used_for_every_loop_access(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        seen: list[Path | None] = []

        def factory(_connection: sqlite3.Connection, root: Path | None) -> Any:
            seen.append(root)
            raise KeyError("no such loop")

        service = BlockerRemediationService(connection, root=tmp_path, coordinator_factory=factory)
        with pytest.raises(KeyError):
            service._coordinator().get("x")
        assert seen == [tmp_path.resolve(strict=False)]


def test_action_registry_names_resolve_to_methods() -> None:
    for name in (*ACTION_HANDLERS.values(), *GIT_ACTION_HANDLERS.values()):
        assert callable(getattr(BlockerRemediationService, name))


def test_unknown_action_type_keeps_unsupported_error(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        service = BlockerRemediationService(connection, root=tmp_path)
        result = service._dispatch_action(
            {"actionType": "nope"}, payload={}, execution_payload={}, platform=None
        )
        assert result == {
            "status": "blocked",
            "action": "nope",
            "reason": "Unsupported remediation action.",
        }
