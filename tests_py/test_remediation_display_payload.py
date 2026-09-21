"""Large remediation evidence stays persisted while display reads remain bounded."""

import json
import uuid
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations import repository as remediation_repository
from local_control_center.remediations.api import create_router
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


@pytest.fixture
def lane(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Remediation display", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Display evidence"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Current loop",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "resource_manager",
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        yield SimpleNamespace(
            connection=connection,
            project=project,
            thread=thread,
            threads=threads,
            loop=loop,
            root=tmp_path,
            service=BlockerRemediationService(connection, root=tmp_path),
        )


def _large_details():
    return {
        "auditEvidence": "verified model evidence " * 16000,
        "resourceBlockers": [
            {
                "role": "aido_lead",
                "decision": {
                    "selected": None,
                    "decisionReason": "No candidate satisfies the role policy.",
                    "rejected": [
                        {"providerId": "codex_cli", "model": "gpt-5.5", "reason": "role_blocks_candidate"}
                    ],
                    "policyResult": {
                        "decisionEngine": {
                            "mode": "runtime_selection",
                            "reasonCode": "no_eligible_candidates",
                        }
                    },
                },
            }
        ],
    }


def _action(
    lane,
    *,
    details=None,
    section="routing",
    stage="resource_manager",
    blocker_type="resource_manager_unconfigured",
    status="pending",
):
    action_id = f"remediation-{uuid.uuid4()}"
    payload = {
        "section": section,
        "options": ["review", "retry"],
        "decisionId": "persisted-decision",
        "providerSetup": {"providerId": "configured-account", "requiredFields": ["apiKey"]},
        "details": details if details is not None else _large_details(),
    }
    # Seed persisted historical evidence directly; creating actions is not the read-path under test.
    lane.connection.execute(
        """INSERT INTO remediation_actions
           (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
            action_type, payload_json, technical_reason, is_primary, is_destructive,
            confirmation_required, status, created_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, 'Review routing', 'Review the reported cause.',
                   'open_settings_section', ?, 'Role policy rejected the candidate.', 1, 0, 0, ?, ?, NULL)
        """,
        (
            action_id,
            lane.project["id"],
            lane.thread["id"],
            lane.loop["id"],
            stage,
            blocker_type,
            json.dumps(payload),
            status,
            utc_now(),
        ),
    )
    return action_id, payload


def _http_app(lane):
    app = FastAPI()
    platform = SimpleNamespace(connection=lane.connection, cwd=lane.root, execution_handlers={})
    app.include_router(create_router(platform=platform, require_write=lambda _request: None))
    return app


def test_http_list_projects_large_details_without_losing_history_or_functional_fields(lane):
    originals = dict(_action(lane, status=status) for status in ["pending", "resolved", "dismissed"] * 2)
    raw_before = list(lane.connection.execute("SELECT id, payload_json FROM remediation_actions ORDER BY id"))
    with TestClient(_http_app(lane)) as client:
        response = client.get(f"/api/v1/threads/{lane.thread['id']}/remediations")
    assert response.status_code == 200
    assert len(response.content) < 100 * 1024
    displayed = response.json()["remediations"]
    assert {item["id"] for item in displayed} == set(originals)
    for item in displayed:
        original = originals[item["id"]]
        assert item["payload"] == {
            **{key: value for key, value in original.items() if key != "details"},
            "detailsEvidence": {"actionId": item["id"], "stored": True},
        }
        assert lane.service.repository.get(item["id"])["payload"] == original
    assert (
        list(lane.connection.execute("SELECT id, payload_json FROM remediation_actions ORDER BY id"))
        == raw_before
    )


def test_summary_projection_happens_before_row_mapping_and_redaction(lane, monkeypatch):
    action_id, payload = _action(lane)
    mapper = remediation_repository.row_to_remediation
    mapped = []

    def bounded_mapper(row):
        mapped.append(row["id"])
        assert len(row["payload_json"].encode()) < 100 * 1024
        assert "auditEvidence" not in row["payload_json"]
        return mapper(row)

    with monkeypatch.context() as patch:
        patch.setattr(remediation_repository, "row_to_remediation", bounded_mapper)
        summary = lane.service.repository.list_for_thread(lane.thread["id"], summary=True)
    assert mapped == [action_id]
    assert summary[0]["payload"]["detailsEvidence"]["actionId"] == action_id
    assert lane.service.repository.list_for_thread(lane.thread["id"])[0]["payload"] == payload


@pytest.mark.parametrize("blocker_type", ["resource_manager_unconfigured", "decision_engine_unavailable"])
def test_current_selection_cards_do_not_reload_full_evidence_on_poll(lane, monkeypatch, blocker_type):
    action_id, _ = _action(lane, blocker_type=blocker_type)
    calls = []
    original_get = lane.service.repository.get

    def counted_get(identifier):
        calls.append(identifier)
        return original_get(identifier)

    monkeypatch.setattr(lane.service.repository, "get", counted_get)
    first = lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True)
    second = lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True)
    assert calls == []
    assert first == second
    assert first[0]["id"] == action_id
    assert "details" not in first[0]["payload"]


def test_legacy_selection_card_reloads_evidence_once_then_poll_is_bounded(lane, monkeypatch):
    old_id, original_payload = _action(lane, section="providers-cli")
    calls = []
    original_get = lane.service.repository.get

    def counted_get(identifier):
        calls.append(identifier)
        return original_get(identifier)

    monkeypatch.setattr(lane.service.repository, "get", counted_get)
    first = lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True)
    assert old_id in calls
    assert next(item for item in first if item["id"] == old_id)["status"] == "dismissed"
    pending = [item for item in first if item["status"] == "pending"]
    assert (
        next(item for item in pending if item["actionType"] == "open_settings_section")["payload"]["section"]
        == "routing"
    )
    assert len(json.dumps(first).encode()) < 100 * 1024
    calls.clear()
    assert lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True) == first
    assert calls == []
    assert original_get(old_id)["payload"] == original_payload


def test_terminal_reconciliation_does_not_hydrate_discarded_action_results(lane, monkeypatch):
    action_id, original_payload = _action(lane)
    lane.connection.execute(
        "UPDATE product_loops SET state = 'cancelled', status = 'cancelled' WHERE id = ?", (lane.loop["id"],)
    )
    lane.threads.set_status(lane.thread["id"], "resolved")

    def forbid_full_get(_identifier):
        raise AssertionError("Display reconciliation must not hydrate full action evidence.")

    with monkeypatch.context() as patch:
        patch.setattr(lane.service.repository, "get", forbid_full_get)
        actions = lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True)
    assert actions[0]["id"] == action_id
    assert actions[0]["status"] == "resolved"
    assert lane.service.repository.get(action_id)["payload"] == original_payload


@pytest.mark.parametrize("stage", ["runtime", "git", "worker"])
def test_summary_keeps_small_details_unchanged(lane, stage):
    details = {"selectedRuntimeId": "configured-cli", "jobId": "job-evidence", "reason": "Reported cause."}
    action_id, payload = _action(lane, details=details, stage=stage, blocker_type="runtime_not_executable")
    display = lane.service.list_for_thread(thread_id=lane.thread["id"], summary=True)
    assert display[0]["id"] == action_id
    assert display[0]["payload"] == payload


def test_execution_still_receives_full_persisted_payload(lane):
    action_id, payload = _action(
        lane, stage="runtime", blocker_type="runtime_not_executable", section="providers-cli"
    )
    result = lane.service.execute(action_id, platform=object())
    assert result["execution"]["status"] == "completed"
    assert result["execution"]["section"] == "providers-cli"
    assert result["remediation"]["payload"] == payload
