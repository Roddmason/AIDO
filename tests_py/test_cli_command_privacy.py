from __future__ import annotations

import json

from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.security_policy.repository import SecurityPolicyRepository


def test_cli_grant_hides_prompt_but_binds_exact_original_command_and_argv(tmp_path):
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        jobs = JobsRepository(runtime.connection)
        job = jobs.create_job(project_id="test", kind="test", payload={})["job"]
        argv = ["codex", "exec", "--sandbox", "read-only", "--", "PRIVATE_PROMPT_CANARY"]
        command = " ".join(argv)
        action = jobs.create_action_request(
            job_id=job["id"],
            project_id="test",
            action_type="tool.call",
            risk_level="high",
            command=command,
            command_argv=argv,
            payload={"tool": "shell", "agentId": "agent"},
            reason="review",
        )
        assert "PRIVATE_PROMPT_CANARY" not in json.dumps(action)
        policies = SecurityPolicyRepository(runtime.connection)
        grant = policies.create_grant_from_action_request(action_request=action, reason="approved")
        assert "PRIVATE_PROMPT_CANARY" not in json.dumps(grant)
        context = {
            "grant_id": grant["id"],
            "project_id": "test",
            "job_id": job["id"],
            "agent_id": "agent",
            "tool": "shell",
            "path": None,
            "agent_run_id": "run",
        }
        changed = [*argv[:-1], "OTHER_PRIVATE_PROMPT"]
        assert not policies.validate_and_consume_grant(
            **context, command=" ".join(changed), command_argv=changed
        )["valid"]
        assert policies.validate_and_consume_grant(**context, command=command, command_argv=argv)["valid"]
        assert not policies.validate_and_consume_grant(**context, command=command, command_argv=argv)["valid"]
    finally:
        runtime.close()


def test_command_summary_does_not_treat_prompt_as_flags():
    from local_control_center.process_supervision.service import safe_command_summary

    summary = safe_command_summary(["codex", "exec", "--prompt", "--PRIVATEPROMPT"])
    assert "PRIVATEPROMPT" not in json.dumps(summary)
    summary = safe_command_summary(["claude", "--print", "--", "--PRIVATEPROMPT"])
    assert "PRIVATEPROMPT" not in json.dumps(summary)


def test_blocked_command_does_not_expose_rejected_extra_arguments(tmp_path):
    from local_control_center.agents.cli_runtimes.base import RuntimeRequest
    from local_control_center.agents.cli_runtimes.codex_cli import CodexCliRuntime

    runtime = CodexCliRuntime()
    request = RuntimeRequest(
        runtime="codex_cli",
        workspaceId="test",
        workspacePath=str(tmp_path),
        prompt="PRIVATE_PROMPT",
        extraArgs=["--dangerously-bypass-approvals-and-sandbox", "PRIVATE_ARGUMENT"],
    )
    result = runtime.run(request)
    assert result.status == "blocked"
    assert "PRIVATE_" not in json.dumps(result.command)
