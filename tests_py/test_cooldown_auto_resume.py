"""Reanudación automática de bloqueos de `resource_manager` cuyo cooldown de cuota ya cedió.

Sin esto, un hilo bloqueado porque todos los candidatos ejecutables estaban en cooldown de cuota
espera para siempre a que el operador reintente a mano, aunque el cooldown ya haya vencido.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _details(provider_id: str, *, reason: str) -> dict:
    return {
        "resourceBlockers": [
            {
                "role": "aido_lead",
                "decision": {
                    "selected": None,
                    "approvalRequired": False,
                    "decisionReason": "Jev runtime selection blocked: no_eligible_candidates.",
                    "rejected": [{"providerId": provider_id, "model": "gpt-5.5", "reason": reason}],
                },
            }
        ]
    }


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Cooldown resume", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Cooldown"
        )
        message = threads.append_message(
            thread_id=thread["id"], kind="user", author="operator", content="Fix the flaky build"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Blocked by cooldown",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"], "messageId": message["id"]},
                        "blockedStage": "resource_manager",
                        "blockedReason": "No eligible candidate for aido_lead.",
                        "message": message["content"],
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        service = BlockerRemediationService(connection, root=tmp_path)
        yield connection, project, thread, loop, service


def _block(lane, details: dict) -> dict:
    _connection, project, thread, loop, service = lane
    actions = service.create_for_blocked_run(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        reason="No eligible candidate for aido_lead.",
        details=details,
    )
    return next(action for action in actions if action["actionType"] == "retry_loop")


def _expire_cooldown(connection, provider_id: str) -> None:
    connection.execute(
        "UPDATE provider_limits SET cooldown_until = '2020-01-01T00:00:00+00:00' WHERE provider_id = ?",
        (provider_id,),
    )


def test_resumes_exactly_once_after_the_cooldown_clears(lane):
    connection, _project, thread, _loop, service = lane
    QuotaManager(connection).record_rate_limit(
        provider_id="codex_cli", model="gpt-5.5", retry_after_seconds=3600
    )
    action = _block(lane, _details("codex_cli", reason="provider_authentication_cooldown"))
    assert action["blockerType"] == "resource_manager_unconfigured"

    # Antes de que venza: el primer tick sólo confirma que es un bloqueo puro de cooldown y lo
    # congela; no reanuda todavía.
    assert service.resume_cooldown_expired_blocks() == []
    assert service.repository.get(action["id"])["status"] == "pending"
    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "blocked"

    # Sigue en cooldown: otro tick tampoco reanuda.
    assert service.resume_cooldown_expired_blocks() == []
    assert service.repository.get(action["id"])["status"] == "pending"

    _expire_cooldown(connection, "codex_cli")

    resumed = service.resume_cooldown_expired_blocks()
    assert len(resumed) == 1
    assert resumed[0]["execution"]["status"] == "queued"
    assert service.repository.get(action["id"])["status"] == "resolved"
    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "queued"
    events = [
        event
        for event in ThreadsRepository(connection).list_events(thread["id"])
        if event["type"] == "auto_resumed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["providerIds"] == ["codex_cli"]
    assert "codex_cli" in events[0]["payload"]["reason"]
    assert "cooldown" in events[0]["payload"]["reason"]

    # Un reinicio del worker (proceso nuevo, mismo estado en SQLite) no duplica nada.
    restarted = BlockerRemediationService(connection, root=service.root)
    assert restarted.resume_cooldown_expired_blocks() == []
    events_after_restart = [
        event
        for event in ThreadsRepository(connection).list_events(thread["id"])
        if event["type"] == "auto_resumed"
    ]
    assert len(events_after_restart) == 1


def test_a_block_from_another_cause_is_never_touched(lane):
    connection, _project, thread, _loop, service = lane
    action = _block(lane, _details("other-cli", reason="provider_missing_credentials"))
    assert action["blockerType"] == "provider_missing_credentials"

    assert service.resume_cooldown_expired_blocks() == []
    assert service.repository.get(action["id"])["status"] == "pending"
    assert ThreadsRepository(connection).get_thread(thread["id"])["status"] == "blocked"
    assert service.repository.get(action["id"])["payload"].get("cooldownSnapshot") is None


def test_an_operator_resumed_thread_is_not_resumed_again(lane):
    connection, _project, thread, _loop, service = lane
    QuotaManager(connection).record_rate_limit(
        provider_id="codex_cli", model="gpt-5.5", retry_after_seconds=3600
    )
    action = _block(lane, _details("codex_cli", reason="provider_authentication_cooldown"))

    # El operador reintenta manualmente antes de que expire el cooldown.
    manual = service.execute(action["id"], platform=None)
    assert manual["execution"]["status"] == "queued"
    assert service.repository.get(action["id"])["status"] == "resolved"

    _expire_cooldown(connection, "codex_cli")

    assert service.resume_cooldown_expired_blocks() == []
    events = [
        event
        for event in ThreadsRepository(connection).list_events(thread["id"])
        if event["type"] == "auto_resumed"
    ]
    assert events == []
