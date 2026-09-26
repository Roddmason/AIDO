"""Tests: compactacion de teamSchedule.roles/resourceBlockers antes de persistir una remediacion,
retencion de terminales antiguos, y backfill idempotente de filas ya infladas.

@author Rodrigo Mason
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.remediations.compaction import (
    MAX_CANDIDATE_ENTRIES,
    MAX_STRING_LENGTH,
    RESOURCE_BLOCKERS_BUDGET_BYTES,
    compact_remediation_payload,
)
from local_control_center.remediations.repository import (
    DISPLAY_DETAILS_LIMIT_BYTES,
    RemediationActionsRepository,
)
from local_control_center.remediations.retention import (
    REMEDIATION_TERMINAL_RETENTION_SECONDS,
    backfill_oversized_remediation_payloads,
    prune_terminal_remediation_actions,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads


def _iso(days_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        with handle:
            initialize_platform_schema(handle)
        yield handle


def _big_team_schedule(role_count: int = 12) -> dict:
    return {
        "schedulerVersion": 3,
        "mode": "critical",
        "roles": [
            {
                "role": f"role-{index}",
                "runtime": "codex_cli",
                "selectedRuntimeId": f"runtime-{index}",
                "status": "planned",
                "reason": "Selected by scheduler policy. " * 40,
                "toolsAllowed": [f"tool-{n}" for n in range(80)],
                "qualityGates": [f"gate-{n}" for n in range(40)],
                "outputArtifactSchema": {"type": "object", "properties": {f"p{n}": {} for n in range(60)}},
            }
            for index in range(role_count)
        ],
        "summary": {"roleCount": role_count},
    }


def _big_resource_blockers(count: int = 30) -> list[dict]:
    return [
        {
            "role": f"role-{index}",
            "reason": "x" * 2000,
            "decision": {
                "selected": None,
                "decisionReason": "y" * 2000,
                "rejected": [{"providerId": "codex_cli", "reason": "role_blocks_candidate"}],
                "policyResult": {"decisionEngine": {"mode": "runtime_selection", "reasonCode": "timeout"}},
            },
        }
        for index in range(count)
    ]


def test_oversized_team_schedule_and_resource_blockers_are_compacted_below_64kib(connection) -> None:
    repository = RemediationActionsRepository(connection)
    payload = {
        "section": "routing",
        "details": {
            "teamSchedule": _big_team_schedule(),
            "resourceBlockers": _big_resource_blockers(),
        },
    }
    raw_size = len(json_dumps(payload).encode())
    assert raw_size > DISPLAY_DETAILS_LIMIT_BYTES, "el fixture debe ser mas grande que el limite objetivo"

    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="resource_manager",
        blocker_type="resource_manager_unconfigured",
        title="t",
        description="d",
        action_type="open_settings_section",
        payload=payload,
    )

    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = ?", (action["id"],)
    ).fetchone()["payload_json"]
    assert len(stored.encode()) < DISPLAY_DETAILS_LIMIT_BYTES


def test_team_schedule_role_summary_keeps_role_runtime_status_and_bounded_reason(connection) -> None:
    repository = RemediationActionsRepository(connection)
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="team_scheduler",
        blocker_type="team_scheduler_failed",
        title="t",
        description="d",
        action_type="retry_loop",
        payload={"details": {"teamSchedule": _big_team_schedule(role_count=2)}},
    )
    roles = action["payload"]["details"]["teamSchedule"]["roles"]
    assert len(roles) == 2
    assert roles[0] == {
        "role": "role-0",
        "runtime": "runtime-0",
        "status": "planned",
        "reason": ("Selected by scheduler policy. " * 40)[:MAX_STRING_LENGTH],
    }
    # Los campos hermanos de teamSchedule (fuera de "roles") no se tocan.
    assert action["payload"]["details"]["teamSchedule"]["schedulerVersion"] == 3
    assert action["payload"]["details"]["teamSchedule"]["summary"] == {"roleCount": 2}


def test_team_schedule_nested_deep_inside_details_is_also_compacted(connection) -> None:
    """Reproduce lo medido en produccion: un blocker de ejecucion guarda el snapshot completo del
    input enviado al agente, que a su vez trae su propio `teamSchedule` sin comprimir."""
    repository = RemediationActionsRepository(connection)
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="worker",
        blocker_type="runtime_output_invalid",
        title="t",
        description="d",
        action_type="retry_loop",
        payload={
            "details": {
                "runtimeResult": {"agentRun": {"input": {"teamSchedule": _big_team_schedule(role_count=3)}}}
            }
        },
    )
    nested = action["payload"]["details"]["runtimeResult"]["agentRun"]["input"]["teamSchedule"]
    assert len(nested["roles"]) == 3
    assert nested["roles"][0]["role"] == "role-0"
    assert "toolsAllowed" not in nested["roles"][0]


def test_agent_assignments_role_list_is_compacted_like_team_schedule_roles(connection) -> None:
    repository = RemediationActionsRepository(connection)
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="worker",
        blocker_type="runtime_output_invalid",
        title="t",
        description="d",
        action_type="retry_loop",
        payload={
            "details": {
                "runtimeResult": {
                    "agentRun": {"input": {"agentAssignments": _big_team_schedule(role_count=3)["roles"]}}
                }
            }
        },
    )
    nested = action["payload"]["details"]["runtimeResult"]["agentRun"]["input"]["agentAssignments"]
    assert nested == [
        {
            "role": f"role-{i}",
            "runtime": f"runtime-{i}",
            "status": "planned",
            "reason": ("Selected by scheduler policy. " * 40)[:MAX_STRING_LENGTH],
        }
        for i in range(3)
    ]


def test_resource_blockers_are_capped_and_string_fields_truncated_but_structure_survives(connection) -> None:
    repository = RemediationActionsRepository(connection)
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="resource_manager",
        blocker_type="resource_manager_unconfigured",
        title="t",
        description="d",
        action_type="open_settings_section",
        payload={"details": {"resourceBlockers": _big_resource_blockers(count=30)}},
    )
    blockers = action["payload"]["details"]["resourceBlockers"]
    # Cada blocker de este fixture es liviano: casi todos caben en el presupuesto de bytes.
    assert len(blockers) == 29
    assert len(blockers[0]["reason"]) == MAX_STRING_LENGTH
    assert len(blockers[0]["decision"]["decisionReason"]) == MAX_STRING_LENGTH
    # decision_engine_failure() sigue pudiendo leer la estructura anidada tras compactar.
    from local_control_center.remediations.payloads import decision_engine_failure

    assert decision_engine_failure({"resourceBlockers": blockers}) is not None


def test_resource_blockers_are_trimmed_by_byte_budget_when_each_blocker_is_rich(connection) -> None:
    """12 blockers "ricos" (policyResult completo) no caben enteros bajo el presupuesto de bytes."""
    repository = RemediationActionsRepository(connection)
    rich_blockers = [_rich_resource_blocker(f"role-{i}") for i in range(12)]
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="resource_manager",
        blocker_type="resource_manager_unconfigured",
        title="t",
        description="d",
        action_type="open_settings_section",
        payload={"details": {"resourceBlockers": rich_blockers}},
    )
    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = ?", (action["id"],)
    ).fetchone()["payload_json"]
    assert len(stored.encode()) < DISPLAY_DETAILS_LIMIT_BYTES
    blockers = action["payload"]["details"]["resourceBlockers"]
    assert 0 < len(blockers) < 12
    assert action["payload"]["details"]["resourceBlockersTotal"] == 12
    assert sum(len(json_dumps(b).encode()) for b in blockers) <= RESOURCE_BLOCKERS_BUDGET_BYTES + 4096


def _fingerprinted_entries(prefix: str, *, reason_count: int, per_reason: int) -> list[dict]:
    """Reproduce el peso real: cada entrada trae un `configurationFingerprint` de 64 hex chars."""
    return [
        {
            "providerId": f"{prefix}-{reason_index}-{copy_index}",
            "model": "some-model-name",
            "configurationFingerprint": f"{prefix}{reason_index}{copy_index}".rjust(64, "a"),
            "reason": f"{prefix}_reason_{reason_index}",
        }
        for reason_index in range(reason_count)
        for copy_index in range(per_reason)
    ]


def _rich_resource_blocker(role: str) -> dict:
    """Reproduce el `policyResult` real: varias listas de candidatos + varios campos escalares."""
    return {
        "role": role,
        "taskId": f"task-{role}",
        "reason": "No candidate satisfies the role policy.",
        "decision": {
            "selected": None,
            "decisionReason": "Automatic validation could not find an eligible candidate.",
            "candidates": _fingerprinted_entries("cand", reason_count=6, per_reason=3),
            "rejected": _fingerprinted_entries("rej", reason_count=6, per_reason=3),
            "policyResult": {
                "decisionEngine": {"mode": "runtime_selection", "reasonCode": "no_eligible_candidates"},
                "roleExecutionPolicy": {"allowApi": True, "allowCli": True, "rolePolicyId": "default"},
                "runtimePreflight": {
                    "attempts": 3,
                    "deferred": _fingerprinted_entries("def", reason_count=6, per_reason=3),
                    "rejected": _fingerprinted_entries("pfr", reason_count=6, per_reason=3),
                    "validated": [{"providerId": "codex_cli", "model": "gpt-5.5"}],
                    "deferredReasonCounts": {"preflight_unknown_cost_requires_approval": 9},
                },
            },
        },
    }


def _huge_rejected_list(count: int = 3454) -> list[dict]:
    """Reproduce lo medido en produccion: miles de rechazos, un puñado de `reason` distintos."""
    reasons = ["role_blocks_candidate", "runtime_not_executable:unavailable", "runtime_policy_denied"]
    return [
        {"providerId": f"provider-{index}", "reason": reasons[index % len(reasons)]} for index in range(count)
    ]


def _resource_blocker_with_huge_rejected(role: str = "aido_lead") -> dict:
    return {
        "role": role,
        "decision": {
            "selected": None,
            "decisionReason": "No candidate satisfies the role policy.",
            "rejected": _huge_rejected_list(),
            "policyResult": {
                "decisionEngine": {"mode": "runtime_selection", "reasonCode": "no_eligible_candidates"}
            },
        },
    }


def test_a_blocker_with_thousands_of_rejected_entries_is_compacted_below_64kib(connection) -> None:
    repository = RemediationActionsRepository(connection)
    details = {"resourceBlockers": [_resource_blocker_with_huge_rejected(f"role-{i}") for i in range(12)]}
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="resource_manager",
        blocker_type="resource_manager_unconfigured",
        title="t",
        description="d",
        action_type="open_settings_section",
        payload={"details": details},
    )
    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = ?", (action["id"],)
    ).fetchone()["payload_json"]
    assert len(stored.encode()) < DISPLAY_DETAILS_LIMIT_BYTES


def test_diagnosis_is_identical_before_and_after_compacting_a_huge_rejected_list() -> None:
    """El motivo real de un rechazo masivo no puede depender de si la fila fue compactada."""
    from local_control_center.remediations.payloads import resource_selection_constraint_failure

    details = {"resourceBlockers": [_resource_blocker_with_huge_rejected()]}
    before = resource_selection_constraint_failure(details)
    compacted_details = compact_remediation_payload({"details": details})["details"]
    after = resource_selection_constraint_failure(compacted_details)

    assert before is not None
    assert after is not None
    assert before["decisionReason"] == after["decisionReason"]
    decision = compacted_details["resourceBlockers"][0]["decision"]
    assert len(decision["rejected"]) <= MAX_CANDIDATE_ENTRIES
    assert decision["rejectedTotal"] == 3454
    assert set(decision["rejectedByReason"]) == {
        "role_blocks_candidate",
        "runtime_not_executable:unavailable",
        "runtime_policy_denied",
    }
    assert sum(decision["rejectedByReason"].values()) == 3454


def test_hard_cap_guarantees_the_limit_when_several_huge_patterns_combine(connection) -> None:
    """Un blocker de ejecucion real combina roles + candidatos + listas genericas a la vez."""
    repository = RemediationActionsRepository(connection)
    payload = {
        "details": {
            "teamSchedule": _big_team_schedule(role_count=10),
            "resourceBlockers": [_rich_resource_blocker(f"role-{i}") for i in range(10)],
            "runtimeResult": {
                "agentRun": {
                    "input": {
                        "teamSchedule": _big_team_schedule(role_count=10),
                        "agentAssignments": _big_team_schedule(role_count=10)["roles"],
                    }
                },
                "evidencePackage": {
                    "fileManifest": {
                        "files": [{"path": f"file-{n}.py", "sha256": "a" * 64} for n in range(500)]
                    }
                },
            },
        }
    }
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="worker",
        blocker_type="runtime_output_invalid",
        title="t",
        description="d",
        action_type="retry_loop",
        payload=payload,
    )
    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = ?", (action["id"],)
    ).fetchone()["payload_json"]
    assert len(stored.encode()) < DISPLAY_DETAILS_LIMIT_BYTES


def test_small_details_are_not_modified(connection) -> None:
    repository = RemediationActionsRepository(connection)
    details = {"selectedRuntimeId": "configured-cli", "jobId": "job-1", "reason": "Reported cause."}
    action = repository.create_action(
        project_id="p-1",
        thread_id="t-1",
        loop_id="",
        stage="runtime",
        blocker_type="runtime_not_executable",
        title="t",
        description="d",
        action_type="retry_loop",
        payload={"details": details},
    )
    assert action["payload"]["details"] == details


def test_compact_remediation_payload_is_idempotent() -> None:
    payload = {
        "details": {"teamSchedule": _big_team_schedule(), "resourceBlockers": _big_resource_blockers()}
    }
    once = compact_remediation_payload(payload)
    twice = compact_remediation_payload(once)
    assert once == twice


def _insert_remediation(
    connection, *, identifier, status, resolved_days_ago=None, payload=None, created_days_ago=60
):
    connection.execute(
        """INSERT INTO remediation_actions
           (id, project_id, thread_id, loop_id, stage, blocker_type, title, description,
            action_type, payload_json, technical_reason, is_primary, is_destructive,
            confirmation_required, status, created_at, resolved_at)
           VALUES (?, 'p-1', 't-1', '', 'worker', 'worker_not_running', 'title', 'desc',
                   'run_worker_once', ?, '', 0, 0, 0, ?, ?, ?)""",
        (
            identifier,
            json_dumps(payload or {}),
            status,
            _iso(created_days_ago),
            _iso(resolved_days_ago) if resolved_days_ago is not None else None,
        ),
    )


def test_pruning_removes_only_old_terminal_actions(connection) -> None:
    with connection:
        _insert_remediation(connection, identifier="old-resolved", status="resolved", resolved_days_ago=31)
        _insert_remediation(connection, identifier="recent-resolved", status="resolved", resolved_days_ago=1)
        _insert_remediation(connection, identifier="old-pending", status="pending", resolved_days_ago=None)

    removed = prune_terminal_remediation_actions(
        connection, retention_seconds=REMEDIATION_TERMINAL_RETENTION_SECONDS
    )

    assert removed == 1
    remaining = {row["id"] for row in connection.execute("SELECT id FROM remediation_actions")}
    assert remaining == {"recent-resolved", "old-pending"}


def test_pending_actions_are_never_pruned_no_matter_how_old(connection) -> None:
    with connection:
        _insert_remediation(connection, identifier="ancient-pending", status="pending", created_days_ago=400)

    removed = prune_terminal_remediation_actions(connection, retention_seconds=1)

    assert removed == 0
    assert connection.execute("SELECT COUNT(*) FROM remediation_actions").fetchone()[0] == 1


def test_backfill_compacts_oversized_rows_inserted_before_the_change(connection) -> None:
    oversized_payload = {
        "details": {"teamSchedule": _big_team_schedule(), "resourceBlockers": _big_resource_blockers()}
    }
    with connection:
        _insert_remediation(
            connection, identifier="legacy-oversized", status="pending", payload=oversized_payload
        )

    compacted = backfill_oversized_remediation_payloads(
        connection, size_limit_bytes=DISPLAY_DETAILS_LIMIT_BYTES
    )

    assert compacted == 1
    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = 'legacy-oversized'"
    ).fetchone()["payload_json"]
    assert len(stored.encode()) < DISPLAY_DETAILS_LIMIT_BYTES
    assert json_loads(stored, {})["details"]["teamSchedule"]["roles"][0]["role"] == "role-0"

    again = backfill_oversized_remediation_payloads(connection, size_limit_bytes=DISPLAY_DETAILS_LIMIT_BYTES)
    assert again == 0


def test_backfill_processes_at_most_max_rows_per_pass(connection) -> None:
    oversized_payload = {
        "details": {
            "resourceBlockers": [_resource_blocker_with_huge_rejected(f"role-{i}") for i in range(12)]
        }
    }
    with connection:
        for index in range(5):
            _insert_remediation(
                connection, identifier=f"legacy-{index}", status="pending", payload=oversized_payload
            )

    first_pass = backfill_oversized_remediation_payloads(
        connection, max_rows_per_pass=2, max_seconds_per_pass=60
    )
    second_pass = backfill_oversized_remediation_payloads(
        connection, max_rows_per_pass=2, max_seconds_per_pass=60
    )
    third_pass = backfill_oversized_remediation_payloads(
        connection, max_rows_per_pass=2, max_seconds_per_pass=60
    )

    assert (first_pass, second_pass, third_pass) == (2, 2, 1)


def test_backfill_stops_early_when_the_time_budget_is_exhausted(connection, monkeypatch) -> None:
    oversized_payload = {
        "details": {
            "resourceBlockers": [_resource_blocker_with_huge_rejected(f"role-{i}") for i in range(12)]
        }
    }
    with connection:
        for index in range(20):
            _insert_remediation(
                connection, identifier=f"slow-{index}", status="pending", payload=oversized_payload
            )

    import local_control_center.remediations.retention as retention_module

    original_compact = retention_module.compact_remediation_payload

    def _slow_compact(payload):
        time.sleep(0.3)
        return original_compact(payload)

    monkeypatch.setattr(retention_module, "compact_remediation_payload", _slow_compact)

    compacted = backfill_oversized_remediation_payloads(
        connection, max_rows_per_pass=20, max_seconds_per_pass=1.0
    )

    assert 0 < compacted < 20


def test_backfill_is_idempotent_and_ignores_rows_already_small(connection) -> None:
    small_payload = {"details": {"reason": "short"}}
    with connection:
        _insert_remediation(connection, identifier="already-small", status="pending", payload=small_payload)

    first = backfill_oversized_remediation_payloads(connection, size_limit_bytes=DISPLAY_DETAILS_LIMIT_BYTES)
    second = backfill_oversized_remediation_payloads(connection, size_limit_bytes=DISPLAY_DETAILS_LIMIT_BYTES)

    assert first == 0
    assert second == 0
    stored = connection.execute(
        "SELECT payload_json FROM remediation_actions WHERE id = 'already-small'"
    ).fetchone()["payload_json"]
    assert json_loads(stored, {}) == small_payload


def test_each_rediagnosis_keeps_its_first_blocker_even_behind_a_full_budget() -> None:
    """Forma real medida: seis blockers de restriccion llenaban el presupuesto y los de riesgo, que
    venian despues, se descartaban; cada re-diagnostico debe devolver la misma decision."""
    from local_control_center.remediations.payloads import (
        decision_engine_failure,
        resource_selection_constraint_failure,
        runtime_risk_review_failure,
    )

    def blocker(decision_id: str, reason_code: str) -> dict:
        return {
            "role": decision_id,
            "decision": {
                "selected": None,
                "rejected": [{"reason": "role_blocks_candidate", "resourceId": f"r-{n}"} for n in range(40)],
                "policyResult": {
                    "decisionEngine": {
                        "mode": "runtime_selection",
                        "reasonCode": reason_code,
                        "decisionId": decision_id,
                    },
                    **{f"note{n}": "x" * 480 for n in range(8)},
                },
                "padding": [{"k": "y" * 480} for _ in range(3)],
            },
        }

    blockers = [blocker(f"constraint-{n}", "no_eligible_candidates") for n in range(10)]
    blockers += [blocker("risk-1", "risk_requires_review"), blocker("risk-2", "risk_requires_review")]
    original = {"details": {"resourceBlockers": blockers}}

    compacted = compact_remediation_payload(original)["details"]

    for check in (
        decision_engine_failure,
        runtime_risk_review_failure,
        resource_selection_constraint_failure,
    ):
        before, after = check(original["details"]), check(compacted)
        assert (before is None) == (after is None)
        if before is not None:
            decision_id = before["policyResult"]["decisionEngine"]["decisionId"]
            assert after["policyResult"]["decisionEngine"]["decisionId"] == decision_id
    assert len(json_dumps(compacted).encode()) <= 64 * 1024
