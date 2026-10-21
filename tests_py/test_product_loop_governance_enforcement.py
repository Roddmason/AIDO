"""Gobernanza del Product Loop hecha cumplir en el flujo vivo: presupuesto, deadline y medición de consumo.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.product_loop.coordinator import (
    ProductLoopCoordinator,
    _ProductLoopGovernanceStop,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

PAST_DEADLINE = "2000-01-01T00:00:00.000Z"
SELECTED = {"selected": {"providerId": "ollama", "model": "qwen2.5-coder", "runtime": "local"}}


def _seed_model(connection) -> None:
    AIResourceManager(connection).upsert_model_performance(
        {
            "providerId": "ollama",
            "model": "qwen2.5-coder",
            "runtime": "local",
            "capabilities": ["chat", "code"],
            "contextWindow": 128000,
            "maxOutputTokens": 4096,
            "inputPricePerMtok": 0.0,
            "outputPricePerMtok": 0.0,
            "locality": "local",
            "privacyLevel": "local_private",
            "evidence": [{"id": "seed-governance", "kind": "test_seed"}],
        }
    )


def _loop(connection, tmp_path: Path, **governance: Any) -> tuple[ProductLoopCoordinator, dict[str, Any]]:
    project = ProjectsRepository(connection).create_project(
        name="gov", path=tmp_path / "gov", template_id="other"
    )
    coordinator = ProductLoopCoordinator(connection, root=tmp_path)
    return coordinator, coordinator.start(project_id=project["id"], title="X", **governance)


def _advance(coordinator: ProductLoopCoordinator, loop: dict[str, Any]) -> dict[str, Any]:
    return coordinator._transition_run_state(
        loop, to_state="discovering", reason="next phase", trigger="test", actor="test"
    )


def _learn(coordinator: ProductLoopCoordinator, loop_id: str, runtime_result: dict[str, Any]) -> None:
    coordinator._record_resource_decision_learning(
        manager=AIResourceManager(coordinator.connection),
        role="developer",
        decision=SELECTED,
        runtime_result=runtime_result,
        usage_entry=None,
        evidence_ref="evidence-1",
        success=True,
        rework=False,
        quality_score=0.9,
        loop_id=loop_id,
    )


def test_exhausted_budget_blocks_the_loop_at_the_next_checkpoint(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        coordinator, loop = _loop(connection, tmp_path, budget={"costUsd": 1})
        coordinator.record_usage(loop["id"], {"costUsd": 2})

        with pytest.raises(_ProductLoopGovernanceStop) as stopped:
            _advance(coordinator, loop)

        result = stopped.value.result
        assert result["status"] == "blocked"
        assert "budget_exhausted" in result["reason"]
        assert "start a new loop" in result["reason"]
        assert "larger budget or a later deadline" in result["reason"]
        assert coordinator.get(loop["id"])["state"] == "blocked"


def test_passed_deadline_blocks_the_loop_at_the_next_checkpoint(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        coordinator, loop = _loop(
            connection, tmp_path, deadline=PAST_DEADLINE, now="1999-12-31T00:00:00.000Z"
        )

        with pytest.raises(_ProductLoopGovernanceStop) as stopped:
            _advance(coordinator, loop)

        assert "deadline_exceeded" in stopped.value.result["reason"]
        assert coordinator.get(loop["id"])["state"] == "blocked"


def test_loop_without_governance_is_unaffected(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_model(connection)
        coordinator, loop = _loop(connection, tmp_path)
        _learn(coordinator, loop["id"], {"actualCostUsd": 5.0})

        advanced = _advance(coordinator, loop)

        assert advanced["state"] == "discovering"
        assert advanced["context"]["fsm"]["usage"]["consumed"] == {}
        assert advanced["context"]["fsm"]["usage"]["usageSeq"] == 0


def test_reaching_max_rework_rounds_does_not_block_the_checkpoint(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        coordinator, loop = _loop(connection, tmp_path, max_rework_rounds=0)

        assert _advance(coordinator, loop)["state"] == "discovering"


def test_usage_is_recorded_once_per_execution_for_budgeted_keys_only(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_model(connection)
        coordinator, loop = _loop(connection, tmp_path, budget={"costUsd": 10, "agentRuns": 5})

        _learn(coordinator, loop["id"], {"actualCostUsd": 0.5})

        usage = coordinator.get(loop["id"])["context"]["fsm"]["usage"]
        assert usage["consumed"] == {"costUsd": 0.5, "agentRuns": 1.0}
        assert usage["usageSeq"] == 1

        _learn(coordinator, loop["id"], {"actualCostUsd": 0.25})
        usage = coordinator.get(loop["id"])["context"]["fsm"]["usage"]
        assert usage["consumed"] == {"costUsd": 0.75, "agentRuns": 2.0}
