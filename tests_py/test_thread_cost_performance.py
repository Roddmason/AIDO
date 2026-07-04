"""Tests del snapshot de costo/rendimiento por hilo: GET /api/v1/threads/{id}/cost-performance.

Cubre el invariante central del slice —un costo o token desconocido se modela como ``None``, jamás como
``0``/``$0``— además del scoping honesto por prefijo de ``task_id`` del loop (una fila de otro hilo no
contamina el total), la vista de ruteo (modelo elegido, razón, alternativa más barata) y el 404 del hilo
inexistente. Siembra datos reales por repositorio; no crea tablas nuevas.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads.repository import ThreadsRepository


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    app = create_app(runtime=runtime, static_dir=None)
    return runtime, TestClient(app)


def _project(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Cost", path=tmp_path / "cost", template_id="other"
    )
    return project["id"]


def _thread(runtime, project_id: str, title: str) -> dict:
    return ThreadsRepository(runtime.connection).create_thread(
        project_id=project_id,
        owner_type="workspace",
        owner_id="workspace-1",
        title=title,
    )


def _loop(runtime, project_id: str, context: dict) -> dict:
    return ProductLoopRepository(runtime.connection).create_loop(
        {
            "projectId": project_id,
            "title": "Loop",
            "state": "reviewing",
            "status": "active",
            "context": context,
        }
    )


def _link_thread_to_loop(runtime, thread_id: str, loop_id: str) -> None:
    ThreadsRepository(runtime.connection).record_event(
        thread_id=thread_id,
        type="runtime_selected",
        agent_role="developer",
        payload={"loopId": loop_id},
    )


def _task_id(loop_id: str) -> str:
    return f"product-loop-{loop_id.replace('product-loop-', '')[:12]}"


def test_unknown_cost_never_renders_as_zero(tmp_path: Path) -> None:
    """Una fila con costo y tokens desconocidos deja el total en None y el estado en 'unknown', no en 0/$0."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    thread = _thread(runtime, project_id, "Unknown cost run")
    loop = _loop(
        runtime,
        project_id,
        {"fsm": {"usage": {"reworkRounds": 2}, "policy": {"maxReworkRounds": 3}}},
    )
    _link_thread_to_loop(runtime, thread["id"], loop["id"])
    UsageLedger(runtime.connection).record_usage(
        provider_id="nvidia_nim",
        model="mystery-model",
        runtime_type="api",
        role="developer",
        task_id=_task_id(loop["id"]),
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=None,
        actual_cost_usd=None,
        raw_usage={"usage_source": "unknown"},
    )

    response = client.get(f"/api/v1/threads/{thread['id']}/cost-performance")
    assert response.status_code == 200
    snapshot = response.json()["costPerformance"]

    assert snapshot["hasData"] is True
    assert snapshot["budgetUsed"]["usedUsd"] is None
    assert snapshot["budgetUsed"]["costStatus"] == "unknown"
    assert snapshot["cost"]["estimatedCostUsd"] is None
    assert snapshot["cost"]["actualCostUsd"] is None
    assert snapshot["tokens"]["tokenStatus"] == "unknown"
    assert snapshot["tokens"]["unknownCalls"] == 1
    assert snapshot["tokens"]["knownCalls"] == 0
    assert snapshot["qualityRework"]["reworkRounds"] == 2
    assert snapshot["qualityRework"]["maxReworkRounds"] == 3


def test_sums_known_cost_and_scopes_by_thread(tmp_path: Path) -> None:
    """Prefiere el costo actual conocido, suma tokens y NO cuenta el gasto de otro loop/hilo."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)

    thread = _thread(runtime, project_id, "Known cost run")
    loop = _loop(runtime, project_id, {"fsm": {"usage": {"reworkRounds": 0}}})
    _link_thread_to_loop(runtime, thread["id"], loop["id"])

    ledger = UsageLedger(runtime.connection)
    ledger.record_usage(
        provider_id="anthropic_api",
        model="claude-opus",
        runtime_type="api",
        role="developer",
        task_id=_task_id(loop["id"]),
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.02,
        actual_cost_usd=0.03,
        raw_usage={"usage_source": "actual"},
    )
    # A different loop (another thread's work) must not leak into this thread's totals.
    other_loop = _loop(runtime, project_id, {"fsm": {"usage": {"reworkRounds": 0}}})
    ledger.record_usage(
        provider_id="anthropic_api",
        model="claude-opus",
        runtime_type="api",
        role="developer",
        task_id=_task_id(other_loop["id"]),
        input_tokens=999,
        output_tokens=999,
        estimated_cost_usd=5.0,
        actual_cost_usd=5.0,
        raw_usage={"usage_source": "actual"},
    )

    RoutingProfileStore(runtime.connection).record_routing_decision(
        {
            "role": "developer",
            "taskType": "code",
            "mode": "balanced_best_value",
            "selectedProvider": "anthropic_api",
            "selectedModel": "claude-opus",
            "selectedRuntime": "api",
            "selectedEffort": "high",
            "taskId": _task_id(loop["id"]),
            "estimatedCostUsd": 0.02,
            "candidates": [
                {
                    "provider": "anthropic_api",
                    "model": "claude-opus",
                    "estimatedCostUsd": 0.02,
                    "priceKnown": True,
                },
                {
                    "provider": "ollama",
                    "model": "llama-local",
                    "runtime": "ollama",
                    "estimatedCostUsd": 0.0,
                    "priceKnown": True,
                },
            ],
            "decisionReason": "Selected anthropic_api/claude-opus for role developer.",
            "policyResult": {"requiresApproval": False},
        }
    )

    snapshot = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"]

    assert snapshot["budgetUsed"]["usedUsd"] == pytest.approx(0.03)
    assert snapshot["budgetUsed"]["costStatus"] == "actual"
    assert snapshot["budgetUsed"]["callCount"] == 1
    assert snapshot["cost"]["estimatedCostUsd"] == pytest.approx(0.02)
    assert snapshot["cost"]["actualCostUsd"] == pytest.approx(0.03)
    assert snapshot["tokens"]["totalTokens"] == 150
    assert snapshot["tokens"]["tokenStatus"] == "actual"
    assert snapshot["modelChosen"]["model"] == "claude-opus"
    assert snapshot["reasonSelected"].startswith("Selected")
    assert snapshot["cheaperAlternative"]["model"] == "llama-local"
    assert snapshot["cheaperAlternative"]["deltaUsd"] == pytest.approx(0.02)


def test_thread_without_runs_reports_no_data_without_fabrication(tmp_path: Path) -> None:
    """Un hilo sin ejecución no inventa gasto: hasData False, totales None y estado 'none'."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    thread = _thread(runtime, project_id, "Untouched objective")

    snapshot = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"]

    assert snapshot["hasData"] is False
    assert snapshot["budgetUsed"]["usedUsd"] is None
    assert snapshot["cost"]["estimatedCostUsd"] is None
    assert snapshot["tokens"]["tokenStatus"] == "none"
    assert snapshot["modelChosen"] is None
    assert snapshot["cheaperAlternative"] is None


def test_missing_thread_returns_404(tmp_path: Path) -> None:
    """Un hilo inexistente responde 404, no un snapshot vacío."""
    _, client = _client(tmp_path)
    assert client.get("/api/v1/threads/thread-does-not-exist/cost-performance").status_code == 404
