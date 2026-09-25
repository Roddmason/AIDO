"""Remediaciones de runtimes locales: payload desde el perfil, sin API key inventada, acciones por causa.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.executions import router as execution_router
from local_control_center.executions.router import OperationSpec
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.local_runtime import (
    LOCAL_CAUSE_MESSAGES,
    WSL_HINT_MESSAGE,
    WSL_HINT_MESSAGE_KEY,
    cause_message_key,
    local_runtime_cause,
)
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

CATALOG = Path(__file__).resolve().parents[1] / "local_control_center" / "i18n" / "default_catalog.json"
UNVALIDATED_REJECTION = {"providerId": "llama_cpp", "model": "gemma-a", "reason": "local_model_not_validated"}
UNVALIDATED_BLOCKERS = [
    {"role": "developer", "decision": {"selected": None, "rejected": [UNVALIDATED_REJECTION]}}
]


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _project_and_thread(connection, tmp_path: Path, name: str) -> tuple[dict, dict]:
    project = ProjectsRepository(connection).create_project(
        name=name, path=tmp_path / name, template_id="other"
    )
    thread = ThreadsRepository(connection).create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title=f"{name} thread"
    )
    return project, thread


def _local_account(
    connection,
    provider_id: str,
    catalog_id: str,
    *,
    credential_ref: str = "",
    base_url: str = "http://127.0.0.1:18082/v1",
) -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": provider_id,
            "displayName": provider_id,
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": base_url,
            "credentialRef": credential_ref,
            "enabled": True,
        }
    )
    store.set_provider_catalog_id(provider_id, catalog_id)


def _blocked(connection, tmp_path: Path, name: str, provider_id: str, reason: str) -> list[dict[str, Any]]:
    project, thread = _project_and_thread(connection, tmp_path, name)
    return BlockerRemediationService(connection, root=tmp_path).create_for_blocked_run(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=f"product-loop-{name}",
        stage="runtime",
        reason=reason,
        details={"status": "failed", "reason": reason, "runtimeId": provider_id, "executable": False},
    )


def test_unreachable_local_runtime_offers_retry_wizard_and_model_validation(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "llama-lab", "llama_cpp")
        actions = _blocked(
            connection, tmp_path, "local-down", "llama-lab", "local_server_unreachable: connection refused"
        )
    assert [action["actionType"] for action in actions] == [
        "retry_loop",
        "open_settings_section",
        "revalidate_runtime",
    ]
    setup = actions[1]["payload"]["providerSetup"]
    assert setup["catalogId"] == "llama_cpp"
    assert setup["baseUrl"] == "http://127.0.0.1:18082/v1"
    assert setup["authFields"] == []
    assert setup["optionalAuthFields"] == ["credentialRef"]
    assert "apiKey" not in json.dumps(setup)
    assert setup["localCause"] == "local_server_unreachable"
    assert setup["causeMessageKey"] == "app.remediation.local.cause.local_server_unreachable"
    assert "hintMessageKey" not in setup
    assert actions[1]["payload"]["openWizard"] is True
    assert actions[2]["payload"]["runtimeIds"] == ["llama-lab"]
    assert actions[0]["payload"]["retryTarget"] == "runtime"


def test_auth_failure_opens_the_wizard_first_and_asks_for_the_token(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "lms-auth", "lm_studio")
        actions = _blocked(
            connection, tmp_path, "local-auth", "lms-auth", "local_auth_required: 401 from /v1/models"
        )
    assert [action["actionType"] for action in actions] == [
        "open_settings_section",
        "revalidate_runtime",
        "retry_loop",
    ]
    setup = actions[0]["payload"]["providerSetup"]
    assert setup["authFields"] == ["credentialRef"]
    assert setup["optionalAuthFields"] == []


def test_unreachable_vllm_mentions_wsl_configuration_and_firewall(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "vllm-wsl", "vllm")
        actions = _blocked(
            connection, tmp_path, "local-wsl", "vllm-wsl", "local_server_unreachable: timed out"
        )
    setup = actions[1]["payload"]["providerSetup"]
    assert setup["hintMessageKey"] == WSL_HINT_MESSAGE_KEY
    assert ".wslconfig" in setup["hintMessage"]
    assert "firewall" in setup["hintMessage"]


@pytest.mark.parametrize(
    ("base_url", "hinted"),
    [("http://172.20.0.5:8082/v1", True), ("http://172.20.0.6:8082/v1", False)],
    ids=["current-declaration", "declaration-for-another-host"],
)
def test_the_wsl_hint_follows_the_current_local_declaration(
    tmp_path: Path, base_url: str, hinted: bool
) -> None:
    declaration = {"declaredBy": "operator", "declaredAt": "2026-09-23T00:00:00Z", "host": "172.20.0.5"}
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "llama-wsl", "llama_cpp", base_url=base_url)
        ProviderAccountStore(connection).set_local_declaration("llama-wsl", declaration)
        actions = _blocked(
            connection, tmp_path, "local-wsl-llama", "llama-wsl", "local_server_unreachable: timed out"
        )
    setup = actions[1]["payload"]["providerSetup"]
    assert (setup.get("hintMessageKey") == WSL_HINT_MESSAGE_KEY) is hinted


@pytest.mark.parametrize(
    ("reason", "details", "expected"),
    [
        ("anything", {"localRuntimeCause": "model_loading"}, "model_loading"),
        ("provider failed: local_model_load_failed (out of memory)", {}, "local_model_load_failed"),
        ("provider failed", {"cause": "not_a_local_cause"}, None),
        (
            "No AI resource satisfies the role.",
            {"resourceBlockers": UNVALIDATED_BLOCKERS},
            "local_model_not_validated",
        ),
    ],
)
def test_local_runtime_cause_prefers_structured_details(
    reason: str, details: dict, expected: str | None
) -> None:
    assert local_runtime_cause(reason, details) == expected


def test_an_empty_validated_set_in_the_resource_manager_asks_to_validate_the_model(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "llama_cpp", "llama_cpp")
        project, thread = _project_and_thread(connection, tmp_path, "local-unvalidated")
        actions = BlockerRemediationService(connection, root=tmp_path).create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id="product-loop-local-unvalidated",
            stage="resource_manager",
            reason="No AI resource satisfies the role.",
            details={"resourceBlockers": UNVALIDATED_BLOCKERS},
        )
    assert actions[0]["actionType"] == "revalidate_runtime"
    assert actions[0]["payload"]["runtimeIds"] == ["llama_cpp"]
    assert actions[0]["blockerType"] == "resource_manager_unconfigured"


def test_retesting_a_sealed_model_queues_that_model_and_resumes_only_when_it_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueued: list[dict[str, Any]] = []
    retried: list[str] = []

    def fake_enqueue(platform, spec, arguments, *, result_status_code=200, project_id=None):
        enqueued.append(arguments)
        execution_id = f"exec-{len(enqueued)}"
        return {"executionId": execution_id, "jobId": "job", "operation": spec.name, "status": "queued"}

    def fake_retry(self, *, action, payload=None):
        retried.append(action["id"])
        return {"status": "queued", "action": "retry_loop", "jobId": "job-retry"}

    monkeypatch.setattr(execution_router, "enqueue_registered_operation", fake_enqueue)
    monkeypatch.setattr(BlockerRemediationService, "_retry_loop", fake_retry)
    spec = OperationSpec("models.validate_runtime", "local_model_call")
    platform = SimpleNamespace(execution_handlers={"models.validate_runtime": (spec, lambda **_kwargs: None)})
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _local_account(connection, "llama-team", "llama_cpp")
        for model in ("gemma-a", "qwen-b"):
            ProviderAccountStore(connection).upsert_model(
                {"providerId": "llama-team", "model": model, "enabled": True, "source": "test"}
            )
        record_model_execution(connection, "llama-team", "qwen-b", False, "tool_broker", started_at=_ago(3))
        record_model_execution(connection, "llama-team", "gemma-a", True, "test_prompt", started_at=_ago(1))
        project, thread = _project_and_thread(connection, tmp_path, "sealed-model")
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Sealed model",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "runtime_team",
                        "blockedReason": "Runtimes without a fresh validation: llama-team/qwen-b.",
                    }
                },
            }
        )
        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="runtime_team",
            reason="Runtimes without a fresh validation: llama-team/qwen-b (runtime_validation_failed).",
            details={
                "missingRoles": [],
                "staleRuntimes": [
                    {
                        "providerId": "llama-team",
                        "model": "qwen-b",
                        "status": "failed",
                        "reason": "runtime_validation_failed",
                    }
                ],
                "runtimeIds": ["llama-team"],
            },
        )
        queued = service.execute(actions[0]["id"], platform=platform)
        waiting = service.resume_after_runtime_validation("llama-team")
        record_model_execution(connection, "llama-team", "qwen-b", True, "test_prompt")
        resumed = service.resume_after_runtime_validation("llama-team")
    assert actions[0]["payload"]["runtimeModels"] == {"llama-team": ["qwen-b"]}
    assert queued["execution"]["status"] == "validating"
    assert enqueued == [
        {"provider_id": "llama-team", "body": {"projectId": project["id"], "model": "qwen-b"}}
    ]
    assert waiting == []
    assert [item["execution"]["status"] for item in resumed] == ["queued"]
    assert retried == [actions[1]["id"]]


def test_local_remediation_messages_are_registered_bilingually() -> None:
    translations = json.loads(CATALOG.read_text(encoding="utf-8"))["translations"]
    expected = {cause_message_key(cause): message for cause, message in LOCAL_CAUSE_MESSAGES.items()}
    expected[WSL_HINT_MESSAGE_KEY] = WSL_HINT_MESSAGE
    for key, english in expected.items():
        assert translations[key]["en"] == english, key
        assert translations[key]["es"].strip(), key
        assert translations[key]["es"] != english, key
