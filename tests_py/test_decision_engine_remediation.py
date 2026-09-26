"""Jev selection failure must not be presented as missing executable runtimes."""

from contextlib import closing

import pytest

from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _details(code="timeout", *, selected=None, rejected_reason="runtime_not_executable:unavailable"):
    return {
        "resourceBlockers": [
            {
                "role": "product_owner",
                "decision": {
                    "selected": selected,
                    "decisionReason": f"Jev runtime selection blocked: {code}. Codex model validation succeeded.",
                    "approvalRequired": selected is not None,
                    "candidates": [{"providerId": "codex_cli", "model": "gpt-5.5", "runtime": "codex_cli"}],
                    "rejected": [{"providerId": "other-provider", "reason": rejected_reason}],
                    "policyResult": {
                        "decisionEngine": {
                            "mode": "runtime_selection",
                            "reasonCode": code,
                            "decisionId": "jev-proof",
                        },
                        "runtimePreflight": {"validated": [{"providerId": "codex_cli", "model": "gpt-5.5"}]},
                    },
                },
            }
        ],
    }


@pytest.fixture
def lane(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Decision remediation", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Jev timeout"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Jev selection",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "resource_manager",
                        "blockedReason": "Jev runtime selection blocked: timeout.",
                        "productOwner": {"resourceDecision": _details()["resourceBlockers"][0]["decision"]},
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        yield connection, project, thread, loop, BlockerRemediationService(connection, root=tmp_path)


@pytest.mark.parametrize("code", ["timeout", "circuit_open", "transport_error", "credential_unavailable"])
def test_jev_failure_precedes_unrelated_rejected_runtimes_and_keeps_validated_evidence(lane, code):
    _, project, thread, loop, service = lane
    actions = service.create_for_blocked_run(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        reason=f"Jev runtime selection blocked: {code}.",
        details=_details(code),
    )
    assert {item["blockerType"] for item in actions} == {"decision_engine_unavailable"}
    assert [item["actionType"] for item in actions] == ["open_settings_section", "retry_loop"]
    assert actions[0]["payload"]["section"] == "routing"
    assert actions[0]["payload"]["decisionEngine"]["reasonCode"] == code
    assert actions[0]["payload"]["decisionEngine"]["decisionId"] == "jev-proof"
    assert actions[0]["payload"]["validatedCandidates"][0]["model"] == "gpt-5.5"
    assert actions[1]["payload"]["retryTarget"] == "resource_manager"


@pytest.mark.parametrize(
    ("code", "rejected", "expected"),
    [
        ("no_eligible_candidates", "runtime_not_executable:unavailable", "runtime_not_executable"),
        ("privacy_blocked", "privacy_blocks_remote", "resource_manager_privacy_blocked"),
        ("model_validation_failed", "runtime_not_executable:unavailable", "runtime_not_executable"),
        # Jev sin confianza para desempatar candidatos ya validados no es un problema de
        # validación ni de política de rol: la acción útil es asignar un runtime al rol.
        ("confidence_below_threshold", "policy_rejected", "runtime_team_validation_expired"),
        ("margin_below_threshold", "policy_rejected", "runtime_team_validation_expired"),
    ],
)
def test_jev_transport_classification_does_not_hide_candidate_or_policy_rejections(
    lane, code, rejected, expected
):
    service = lane[-1]
    assert (
        service._blocker_type(
            stage="resource_manager",
            reason=f"Jev selection blocked: {code}.",
            details=_details(code, rejected_reason=rejected),
        )
        == expected
    )


def test_confidence_below_threshold_outranks_unrelated_runtime_not_executable_text(lane):
    """La confianza baja de Jev no debe leerse como 'runtime no ejecutable' aunque OTRO candidato
    rechazado en el mismo turno sí lo esté (visto en vivo: modelos sin relación con la ambigüedad)."""
    service = lane[-1]
    assert (
        service._blocker_type(
            stage="resource_manager",
            reason="Jev runtime selection blocked: confidence_below_threshold.",
            details=_details(
                "confidence_below_threshold",
                rejected_reason="runtime_not_executable: unrelated model offline",
            ),
        )
        == "runtime_team_validation_expired"
    )


def test_confidence_below_threshold_offers_the_thread_team_action_as_primary(lane):
    """No inventa UI nueva: reutiliza el blocker/acción existente que abre el equipo del hilo."""
    _, project, thread, loop, service = lane
    actions = service.create_for_blocked_run(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        reason="AIResourceManager could not select an approved AI resource for role aido_lead: "
        "Jev runtime selection blocked: confidence_below_threshold.",
        details=_details("confidence_below_threshold"),
    )
    assert {item["blockerType"] for item in actions} == {"runtime_team_validation_expired"}
    assert any(
        item["actionType"] == "retry_loop" and item["payload"].get("retryTarget") == "runtime_team"
        for item in actions
    )


def test_jev_failure_does_not_replace_selected_resource_approval(lane):
    assert (
        lane[-1]._blocker_type(
            stage="resource_manager",
            reason="Selected resource requires approval.",
            details=_details(selected={"providerId": "codex_cli", "model": "gpt-5.5"}),
        )
        == "resource_manager_approval_required"
    )


def test_list_reclassifies_persisted_runtime_card_using_jev_evidence_without_losing_history(lane):
    _, project, thread, loop, service = lane
    old = service.repository.create_action(
        project_id=project["id"],
        thread_id=thread["id"],
        loop_id=loop["id"],
        stage="resource_manager",
        blocker_type="runtime_not_executable",
        action_type="validate_runtime",
        title="No executable runtime",
        description="Legacy incorrect classification.",
        payload={"details": _details()},
    )
    actions = service.list_for_thread(thread_id=thread["id"])
    assert service.repository.get(old["id"])["status"] == "dismissed"
    assert service.repository.get(old["id"])["payload"] == old["payload"]
    pending = [item for item in actions if item["status"] == "pending"]
    assert {item["blockerType"] for item in pending} == {"decision_engine_unavailable"}
    assert service.list_for_thread(thread_id=thread["id"]) == actions
