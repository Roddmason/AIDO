"""Tests del snapshot de costo/rendimiento por hilo: GET /api/v1/threads/{id}/cost-performance.

Cubre el invariante central del slice —un costo, token o latencia desconocidos se modelan como ``None``,
jamás como ``0``/``$0``/``0 ms``— además del scoping honesto por prefijo de ``task_id`` del loop (una fila
de otro hilo no contamina el total), la vista de ruteo leída del rastro que el Product Loop realmente
escribe (``ai_routing_decisions``), la política de costo vigente y el 404 del hilo inexistente. Siembra
datos reales por repositorio; no crea tablas nuevas.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.ai_resource_manager import AIResourceManager, AIResourceRequest
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.repository import SettingsRepository
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
    # Una llamada sin latencia registrada tampoco vale 0 ms.
    assert snapshot["latency"]["latencyStatus"] == "unknown"
    assert snapshot["latency"]["p50Ms"] is None
    assert snapshot["latency"]["avgMs"] is None
    assert snapshot["latency"]["maxMs"] is None
    assert snapshot["latency"]["unknownCalls"] == 1


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
    assert snapshot["latency"]["latencyStatus"] == "none"
    assert snapshot["latency"]["p50Ms"] is None
    assert snapshot["modelChosen"] is None
    assert snapshot["cheaperAlternative"] is None


def test_latency_reports_observed_calls_without_counting_unmeasured_ones(tmp_path: Path) -> None:
    """Con latencia parcial, los agregados salen solo de las llamadas medidas; el resto queda desconocido."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    thread = _thread(runtime, project_id, "Latency run")
    loop = _loop(runtime, project_id, {"fsm": {"usage": {"reworkRounds": 0}}})
    _link_thread_to_loop(runtime, thread["id"], loop["id"])

    ledger = UsageLedger(runtime.connection)
    for latency_ms in (400, 1200):
        ledger.record_usage(
            provider_id="ollama_local",
            model="llama-local",
            runtime_type="ollama",
            role="developer",
            task_id=_task_id(loop["id"]),
            input_tokens=10,
            output_tokens=10,
            latency_ms=latency_ms,
            raw_usage={"usage_source": "actual"},
        )
    ledger.record_usage(
        provider_id="ollama_local",
        model="llama-local",
        runtime_type="ollama",
        role="developer",
        task_id=_task_id(loop["id"]),
        input_tokens=10,
        output_tokens=10,
        latency_ms=None,
        raw_usage={"usage_source": "actual"},
    )

    latency = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"][
        "latency"
    ]

    assert latency["latencyStatus"] == "partial"
    assert latency["knownCalls"] == 2
    assert latency["unknownCalls"] == 1
    assert latency["callCount"] == 3
    # Mediana baja de [400, 1200]; el promedio ignora la llamada sin medir en vez de contarla como 0.
    assert latency["p50Ms"] == 400
    assert latency["avgMs"] == 800
    assert latency["maxMs"] == 1200


def test_routing_view_reads_the_trail_the_product_loop_writes(tmp_path: Path) -> None:
    """El Product Loop registra en ``ai_routing_decisions``: el snapshot lo lee en vez de quedar vacío."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    thread = _thread(runtime, project_id, "Real loop run")
    loop = _loop(runtime, project_id, {"fsm": {"usage": {"reworkRounds": 1}}})
    _link_thread_to_loop(runtime, thread["id"], loop["id"])

    manager = AIResourceManager(runtime.connection)
    for provider_id, model, price, quality in (
        ("ollama_local", "llama-premium", 20.0, 0.95),
        ("lmstudio_local", "llama-thrifty", 1.0, 0.40),
    ):
        manager.upsert_model_performance(
            {
                "providerId": provider_id,
                "model": model,
                "runtime": "ollama",
                "capabilities": ["chat"],
                "contextWindow": 128000,
                "maxOutputTokens": 4096,
                "inputPricePerMtok": price,
                "outputPricePerMtok": 0.0,
                "observedSuccessRate": quality,
                "reworkRate": 0.0,
                "qualityScore": quality,
                "locality": "local",
                "privacyLevel": "local_private",
                "evidence": [{"id": f"seed-{model}", "kind": "manual_seed"}],
            }
        )
    decision = manager.select_resource(
        AIResourceRequest(
            project_id=project_id,
            task_id=_task_id(loop["id"]),
            task_type="developer.code",
            context_tokens_estimate=100_000,
            required_capabilities=["chat"],
        ),
        record=True,
    )
    assert decision["selected"]["model"] == "llama-premium", "el modelo caro debe ganar por calidad"

    snapshot = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"]

    assert snapshot["hasData"] is True
    assert snapshot["modelChosen"]["model"] == "llama-premium"
    assert snapshot["modelChosen"]["provider"] == "ollama_local"
    assert snapshot["reasonSelected"].startswith("Selected ollama_local/llama-premium")
    assert snapshot["cheaperAlternative"]["model"] == "llama-thrifty"
    assert snapshot["cheaperAlternative"]["deltaUsd"] == pytest.approx(1.9)
    assert snapshot["qualityRework"]["reworkRounds"] == 1


def test_policy_block_reports_the_operator_standing_cost_decision(tmp_path: Path) -> None:
    """``policy`` refleja los settings vigentes (modo, force local) y el umbral premium del rol."""
    runtime, client = _client(tmp_path)
    project_id = _project(runtime, tmp_path)
    thread = _thread(runtime, project_id, "Policy view")

    default_policy = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"][
        "policy"
    ]
    assert default_policy["mode"] == "balanced"
    assert default_policy["forceLocal"] is False
    assert default_policy["approvalRequired"] is False
    # Umbral sembrado del rol `developer` en role_model_policies.
    assert default_policy["premiumApprovalOverUsd"] == pytest.approx(2.00)

    settings = SettingsRepository(runtime.connection)
    settings.set_value("project.loop.teamMode", "project", project_id, "economy")
    settings.set_value("project.routing.forceLocal", "project", project_id, True)

    policy = client.get(f"/api/v1/threads/{thread['id']}/cost-performance").json()["costPerformance"][
        "policy"
    ]
    assert policy["mode"] == "economy"
    assert policy["forceLocal"] is True


def test_missing_thread_returns_404(tmp_path: Path) -> None:
    """Un hilo inexistente responde 404, no un snapshot vacío."""
    _, client = _client(tmp_path)
    assert client.get("/api/v1/threads/thread-does-not-exist/cost-performance").status_code == 404
