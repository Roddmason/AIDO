"""Regresiones de la auditoria de seguridad de backend (2026-10).

Cada test fija un hallazgo reproducido: la politica autorizaba un string distinto del argv que el
sandbox ejecuta, ``shell`` sin comando caia en ``allow`` y las reglas criticas del clasificador no
cubrian las variantes reales de force-push y ``rm``.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.security_policy.command_classifier import classify_command
from local_control_center.security_policy.policy_engine import evaluate_action


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push -f origin main",
        "git push origin main --force-with-lease",
        "git push --force-with-lease=main origin main",
        "git push origin +main",
        "git push origin +HEAD:refs/heads/main",
        "git push -fu origin main",
        "rm -rf /tmp/x",
        "rm -fr /tmp/x",
        "rm -Rf /tmp/x",
        "rm -r -f /tmp/x",
        "rm --recursive --force /tmp/x",
    ],
)
def test_destructive_variants_are_critical(command: str) -> None:
    assert classify_command(command)["riskLevel"] == "critical"


@pytest.mark.parametrize(
    "command", ["git push origin main", "rm file.txt", "rm -f file.txt", "git push -u origin feat"]
)
def test_non_destructive_variants_are_not_critical(command: str) -> None:
    assert classify_command(command)["riskLevel"] != "critical"


def _shell_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "agentId": "qa_agent",
        "tool": "shell",
        "permissionProfile": "dev_safe",
        "workspaceId": "workspace-1",
        "workspacePath": None,
        "execute": True,
    }
    payload.update(overrides)
    return payload


def test_policy_judges_the_argv_that_will_execute_not_the_display_command() -> None:
    result = evaluate_action(
        _shell_payload(command="pytest --version", commandArgv=["git", "push", "--force", "origin", "main"])
    )
    assert result["decision"] != "allow"
    assert result["riskLevel"] == "critical"


def test_shell_with_empty_command_and_argv_is_derived_from_argv() -> None:
    result = evaluate_action(_shell_payload(command="", commandArgv=["rm", "-rf", "build"]))
    assert result["decision"] == "requires_human"


def test_plan_profile_shell_with_empty_command_is_not_allowed() -> None:
    result = evaluate_action(_shell_payload(permissionProfile="plan", command="", commandArgv=["whoami"]))
    assert result["decision"] == "deny"


def test_shell_without_command_or_argv_does_not_fall_through_to_allow() -> None:
    result = evaluate_action(_shell_payload(execute=False, permissionProfile="dev_safe", command=""))
    assert result["decision"] != "allow"


def test_unrecognized_opaque_action_requires_approval() -> None:
    result = evaluate_action(
        {
            "agentId": "x",
            "tool": "custom_tool",
            "permissionProfile": "dev_safe",
            "command": "frobnicate --everything",
        }
    )
    assert result["decision"] == "requires_approval"


def _git_read_payload(*args: str) -> dict[str, object]:
    return {
        "agentId": "git_workspace_agent",
        "tool": "shell",
        "permissionProfile": "dev_safe",
        "operation": "git_workspace_command",
        "workspaceId": "workspace-1",
        "workspacePath": str(Path.cwd()),
        "agentRunId": "run-1",
        "execute": True,
        "command": "git " + " ".join(args),
        "commandArgv": ["git", *args],
    }


@pytest.mark.parametrize(
    "args",
    [
        ("diff", "--output=/tmp/x"),
        ("diff", "--output", "/tmp/x"),
        ("log", "--no-index", "a", "b"),
        ("diff", "--ext-diff"),
        ("diff", "--textconv"),
        ("diff", "-Oorderfile"),
    ],
)
def test_git_read_commands_reject_file_writing_or_exec_flags(args: tuple[str, ...]) -> None:
    assert evaluate_action(_git_read_payload(*args))["decision"] == "deny"


@pytest.mark.parametrize(
    "args",
    [("diff", "--no-ext-diff", "HEAD", "--"), ("diff", "--stat", "--", "."), ("status", "--porcelain=v1")],
)
def test_git_read_commands_keep_flags_aido_uses(args: tuple[str, ...]) -> None:
    assert evaluate_action(_git_read_payload(*args))["decision"] == "allow"


@pytest.fixture
def decision_db(tmp_path):
    from contextlib import closing

    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(tmp_path / "audit.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield connection


def test_revalidation_crash_is_not_counted_as_a_provider_failure(decision_db) -> None:
    import asyncio

    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from tests_py.test_decision_engine import FakeJev, request_for

    def explode(_: str) -> str | None:
        raise RuntimeError("catalog offline")

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=FakeJev()
    )
    receipt = asyncio.run(engine.select_runtime(request_for(effective_decision=None), revalidate=explode))
    assert receipt["reasonCode"] == "revalidation_error"
    assert receipt["effectiveDecision"] is None
    assert not receipt.get("providerFailed")


def test_record_resource_outcome_skips_receipts_without_effective_decision(decision_db) -> None:
    import asyncio

    from local_control_center.decision_engine.config import DecisionConfig
    from local_control_center.decision_engine.observers import record_resource_outcome
    from local_control_center.decision_engine.repository import DecisionRepository
    from local_control_center.decision_engine.service import ShadowDecisionEngine
    from tests_py.test_decision_engine import FakeJev, request_for

    engine = ShadowDecisionEngine(
        decision_db, config=DecisionConfig(mode="runtime_selection"), provider=FakeJev()
    )
    blocked = asyncio.run(
        engine.select_runtime(
            request_for(effective_decision=None, source_decision_id="routing-1"), revalidate=lambda _: "no"
        )
    )
    chosen = asyncio.run(
        engine.select_runtime(
            request_for(effective_decision=None, source_decision_id="routing-1"), revalidate=lambda _: None
        )
    )
    assert blocked["effectiveDecision"] is None and chosen["effectiveDecision"] == "claude"
    outcome = {"evidence_ref": "e1", "execution_succeeded": True}
    record_resource_outcome(decision_db, source_decision_id="routing-1", outcome=outcome)
    assert DecisionRepository(decision_db).get(chosen["decisionId"]) is not None
    stored = decision_db.execute("SELECT decision_id FROM decision_outcomes").fetchall()
    assert [row["decision_id"] for row in stored] == [chosen["decisionId"]]


def _run_kwargs(db_path: str) -> dict[str, object]:
    from local_control_center.agents import cli_session_stream as stream

    return {
        "db_path": db_path,
        "session_id": "sess-1",
        "project_id": "p",
        "workspace_id": "w",
        "workspace_path": None,
        "runtime": "codex_cli",
        "argv": ["codex"],
        "agent_id": None,
        "agent_run_id": "run-1",
        "timeout_seconds": 5,
        "branch_name": None,
        "worktree_id": None,
        "handle": stream._RunningHandle(),
        "process_opener": lambda **_: None,
    }


def test_cli_session_registry_entry_and_connection_are_released_when_setup_fails(
    monkeypatch, tmp_path
) -> None:
    from local_control_center.agents import cli_session_stream as stream

    closed: list[bool] = []

    class FakeConnection:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(stream, "open_sqlite_connection", lambda _path: FakeConnection())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("root lookup exploded")

    monkeypatch.setattr(stream, "_registered_workspace_root", boom)
    stream._RUNNING["sess-1"] = stream._RunningHandle()
    stream._run(**_run_kwargs(str(tmp_path / "x.db")))
    assert "sess-1" not in stream._RUNNING
    assert closed == [True]


@pytest.mark.parametrize("tool_calls", ["shell", {"tool": "shell"}, ["shell"], [{"tool": "shell"}, 3]])
def test_malformed_tool_calls_are_rejected_before_the_run_is_inserted(
    tmp_path: Path, monkeypatch, tool_calls: object
) -> None:
    from contextlib import closing

    from local_control_center.app import create_app
    from tests_py.control_plane_fixture import ControlPlaneFixture
    from tests_py.execution_client import CompletedExecutionClient as TestClient

    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    with closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")) as store:
        store.init()
        project = store.create_project(name="Runtime", path=tmp_path / "proj", template_id="other")
        with TestClient(create_app(runtime=store, static_dir=None)) as client:
            token = client.get("/api/v1/security/handshake").json()["token"]
            headers = {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}
            profile = client.post(
                "/api/v1/agent-profiles",
                json={
                    "id": "audit_cli",
                    "name": "Audit CLI",
                    "role": "implementer",
                    "runtimeMode": "cli",
                    "permissionProfile": "dev_safe",
                    "allowedTools": ["shell"],
                },
                headers=headers,
            )
            assert profile.status_code == 201
            response = client.post(
                "/api/v1/agent-runs",
                json={
                    "projectId": project["id"],
                    "agentProfileId": "audit_cli",
                    "taskId": "t",
                    "input": {"toolCalls": tool_calls},
                },
                headers=headers,
            )
            assert response.status_code == 422
            runs = client.get("/api/v1/overview").json().get("agentRuns") or []
            assert not [run for run in runs if run.get("taskId") == "t" or run.get("status") == "running"]


def _workspace_scan(tmp_path: Path, monkeypatch=None):
    from local_control_center.agents.security_agent import SecurityAgentRunner

    runner = SecurityAgentRunner.__new__(SecurityAgentRunner)
    findings: list[dict[str, object]] = []
    files, _ = runner._scan_workspace_files(str(tmp_path), findings)
    return files, findings


def test_security_scan_still_detects_secrets_in_non_utf8_files(tmp_path: Path) -> None:
    fake_value = b"abcdefghij" + b"klmnop1234"
    (tmp_path / "legacy.txt").write_bytes(b"caf\xe9 token = " + fake_value + b"\n")
    _, findings = _workspace_scan(tmp_path)
    assert any(item["checkId"] == "secret_scan" for item in findings)


def test_security_scan_does_not_descend_into_ignored_directories(tmp_path: Path, monkeypatch) -> None:
    import os

    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "a.js").write_text("x", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("hello", encoding="utf-8")
    visited: list[str] = []
    real_walk = os.walk

    def spying_walk(top, *args, **kwargs):
        for root, dirs, files in real_walk(top, *args, **kwargs):
            visited.append(Path(root).name)
            yield root, dirs, files

    monkeypatch.setattr(os, "walk", spying_walk)
    files, _ = _workspace_scan(tmp_path)
    assert [item["path"] for item in files] == ["keep.txt"]
    assert "pkg" not in visited and "node_modules" not in visited[1:]


def test_security_scan_does_not_load_oversized_files_into_memory(tmp_path: Path, monkeypatch) -> None:
    from local_control_center.agents import security_agent

    big = tmp_path / "big.bin"
    big.write_bytes(b"a" * (security_agent.MAX_FILE_BYTES + 10))
    real_read_bytes = Path.read_bytes

    def guarded(self: Path) -> bytes:
        assert self.name != "big.bin", "oversized file must not be read in full"
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    files, findings = _workspace_scan(tmp_path)
    assert files[0]["sizeBytes"] == security_agent.MAX_FILE_BYTES + 10
    assert any(item["checkId"] == "file_scan_limit" for item in findings)


def test_deny_wins_over_requires_human_for_scoped_git_workspace_force_push() -> None:
    payload = _git_read_payload("push", "--force", "origin", "feature/x")
    payload.update(gitOperation="delete_remote_branch", networkRequired=True, role="devops_engineer")
    assert evaluate_action(payload)["decision"] == "deny"


def test_classifier_treats_oversized_commands_as_critical_without_regex_scan():
    from local_control_center.security_policy.command_classifier import (
        MAX_CLASSIFIED_COMMAND_CHARS,
        classify_command,
    )

    result = classify_command("rm " * (MAX_CLASSIFIED_COMMAND_CHARS // 3 + 1))

    assert result == {"riskLevel": "critical", "categories": ["oversized_command"]}


def test_force_push_rule_does_not_span_chained_commands():
    from local_control_center.security_policy.command_classifier import classify_command

    assert "force_push" not in classify_command("git push origin x; rm -f y")["categories"]
    assert "force_push" not in classify_command("git push && ls -f")["categories"]
    assert classify_command("git push -f origin x")["categories"] == ["force_push"]
