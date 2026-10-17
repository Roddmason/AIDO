"""El bloqueo por rol sin candidatos nombra la capacidad que falta al runtime sano.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_control_center.agents.local_model_settings import LocalModelSettingsRepository
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.runtime_team.facts import load_runtime_facts
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

PROVIDER = "ollama_remote"
MODEL = "chat-only"
SCHEDULE = {
    "risk": "medium",
    "mode": "balanced",
    "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}],
}
GLOBAL_TEAM_WITHOUT_DEVELOPER = {
    "globalRuntimeTeam": {
        "roleRuntimeOrder": {"product_owner": [PROVIDER], "developer": [], "security": []},
        "roleRuntimes": {"product_owner": PROVIDER, "developer": None, "security": None},
        "allowedRuntimes": [PROVIDER],
        "source": {"developer": "automatic"},
    }
}


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        ResourceRepository(handle).record_sample(ResourceSnapshot.test_snapshot())
        handle.execute("UPDATE model_catalog SET enabled = 0")
        store = ProviderAccountStore(handle)
        store.upsert_provider_account(
            {
                "providerId": PROVIDER,
                "displayName": PROVIDER,
                "providerType": "local",
                "apiFormat": "openai_compatible",
                "providerFamily": "openai_compatible",
                "baseUrl": "http://127.0.0.1:1/v1",
                "enabled": True,
            }
        )
        store.set_provider_catalog_id(PROVIDER, "llama_cpp")
        store.upsert_model(
            {
                "providerId": PROVIDER,
                "model": MODEL,
                "enabled": True,
                "freeTier": True,
                "inputPricePerMtok": 0,
                "outputPricePerMtok": 0,
                "source": "test",
            }
        )
        yield handle


def _advertise_healthy(monkeypatch: pytest.MonkeyPatch, *, healthy: bool = True) -> None:
    status = {
        "id": PROVIDER,
        "kind": "local",
        "providerFamily": "openai_compatible",
        "configured": True,
        "available": True,
        "executable": True,
        "capabilities": ["chat"],
        "healthStatus": "healthy" if healthy else "unknown",
        "healthCheckedAt": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(
        RuntimeStatusService, "list_provider_statuses", lambda _service, *, project_id=None: [status]
    )


def _developer_blockers(connection, tmp_path: Path) -> list[dict]:
    _schedule, blockers = ProductLoopCoordinator(
        connection, root=tmp_path
    )._team_schedule_with_resource_decisions(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=GLOBAL_TEAM_WITHOUT_DEVELOPER,
        team_schedule=SCHEDULE,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
    )
    return blockers


def test_blocked_developer_names_the_capability_the_healthy_runtime_lacks(connection, monkeypatch, tmp_path):
    _advertise_healthy(monkeypatch)
    blocker = _developer_blockers(connection, tmp_path)[0]
    assert "provider_not_allowed_for_agent" in blocker["reason"]
    assert (
        f"{PROVIDER} is healthy but not eligible for developer: declare 'Can edit code' for its model."
        in blocker["reason"]
    )
    assert blocker["capabilityGaps"] == [
        {"providerId": PROVIDER, "teamRole": "developer", "capability": "code_edit"}
    ]


def test_blocked_developer_gives_no_hint_when_the_runtime_is_not_healthy(connection, monkeypatch, tmp_path):
    _advertise_healthy(monkeypatch, healthy=False)
    blocker = _developer_blockers(connection, tmp_path)[0]
    assert "not eligible for developer" not in blocker["reason"]
    assert "capabilityGaps" not in blocker


def test_blocked_developer_gives_no_hint_once_the_model_declares_code_edit(connection, monkeypatch, tmp_path):
    LocalModelSettingsRepository(connection).upsert(PROVIDER, MODEL, actor="operator", code_edit=True)
    _advertise_healthy(monkeypatch)
    assert "developer" in load_runtime_facts(connection, project_id=None)[PROVIDER].eligible_roles
    blocker = _developer_blockers(connection, tmp_path)[0]
    assert "not eligible for developer" not in blocker["reason"]


def test_capability_hint_never_blocks_the_blocker_report(monkeypatch):
    from local_control_center.runtime_team import facts

    def broken(*args, **kwargs):
        raise RuntimeError("runtime status unavailable")

    monkeypatch.setattr(facts, "load_runtime_facts", broken)
    rejected = [{"providerId": "ollama_remote", "reason": "provider_not_allowed_for_agent"}]
    assert facts.role_capability_hint(None, team_role="developer", rejected=rejected, project_id=None) == (
        "",
        [],
    )
