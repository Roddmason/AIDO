"""Multi-agent gated state machine taking an issue to PR through a developer/QA/security/arch/devops DAG.

Runs the issue_to_pr workflow as a directed gate graph: the developer agent implements, then
QA, security, architecture and devops gates each must pass (with bounded rework loops) before
the evidence is aggregated and the run reaches evidence_ready. Builds on IssueToPatchRunner for
the downstream approve -> promote-branch -> create-PR transitions, adapting each result onto the
issue_to_pr steps and timeline. A blocked gate stops the DAG and records why; nothing is promoted
until every required gate has passed and a human approval action exists.

@author Rodrigo Mason
"""

from __future__ import annotations

import itertools
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.architect_agent import ArchitectAgentRunner
from local_control_center.agents.architect_agent_contract import ARCHITECT_AGENT_ID
from local_control_center.agents.developer_agent import DeveloperAgentRunner
from local_control_center.agents.developer_agent_contract import DEVELOPER_AGENT_ID
from local_control_center.agents.devops_agent import DevOpsAgentRunner
from local_control_center.agents.devops_agent_contract import DEVOPS_AGENT_ID
from local_control_center.agents.qa_agent import QA_AGENT_ID, QAAgentRunner, qa_verdict_allows_completion
from local_control_center.agents.repository import AgentsRepository
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.agents.security_agent import SECURITY_AGENT_ID, SecurityAgentRunner
from local_control_center.evidence.artifacts import artifact_hashes, artifact_ref
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now
from local_control_center.workflows.issue_to_patch_runner import (
    APPROVED_FOR_INTEGRATION_STATUS,
    PR_CREATED_STATUS,
    PROMOTED_TO_BRANCH_STATUS,
    IssueToPatchRunner,
    _artifact_content_bytes,
    _diff_summary,
    _final_diff_refs,
    _validate_patch_artifact,
    _validate_qa_for_patch_approval,
    _validate_security_findings,
    _write_json_evidence_artifact,
    _write_text_evidence_artifact,
)
from local_control_center.workflows.repository import ISSUE_TO_PR_STEPS, WorkflowsRepository
from local_control_center.workspaces_projects.git_worktrees import capture_git_diff
from local_control_center.workspaces_projects.repository import WorkspacesRepository

ISSUE_TO_PR_APPROVAL_ACTION = "workflow.issue_to_pr.approve_issue_to_pr"
ISSUE_TO_PR_AGENT_LABELS = {
    DEVELOPER_AGENT_ID: "DeveloperAgent",
    QA_AGENT_ID: "QAAgent",
    SECURITY_AGENT_ID: "SecurityAgent",
    ARCHITECT_AGENT_ID: "ArchitectAgent",
    DEVOPS_AGENT_ID: "DevOpsAgent",
}
ISSUE_TO_PR_DAG = {
    "nodes": ISSUE_TO_PR_STEPS,
    "edges": [[left, right] for left, right in itertools.pairwise(ISSUE_TO_PR_STEPS)],
}
GATE_TO_STEP = {
    "DeveloperAgent": "developer_agent",
    "QAAgent": "qa_validation",
    "SecurityAgent": "security_review",
    "ArchitectAgent": "architecture_review",
    "DevOpsAgent": "devops_validation",
}


def _developer_instruction_for_issue(
    *, title: str, issue_text: str, target_path: str | None, attempt: int
) -> str:
    instruction = (
        "Execute the issue_to_pr DeveloperAgent implementation work in the current workspace only. "
        "Do not commit, push, install dependencies, or modify files outside the workspace.\n\n"
        f"Title: {title}\n\nIssue:\n{issue_text}\n"
    )
    if target_path:
        instruction = f"{instruction}\nTarget path constraint:\n{target_path}\n"
    if attempt > 1:
        instruction = f"{instruction}\nRework attempt: {attempt}. Address prior gate blockers without discarding unrelated work.\n"
    return instruction


def _status_passed(result: dict[str, Any], expected: str) -> bool:
    return str(result.get("status") or "").strip().lower() == expected


def _gate_result(
    name: str, result: dict[str, Any], *, passed: bool, evidence_id: str | None
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed" if passed else "blocked",
        "agentId": {
            "DeveloperAgent": DEVELOPER_AGENT_ID,
            "QAAgent": QA_AGENT_ID,
            "SecurityAgent": SECURITY_AGENT_ID,
            "ArchitectAgent": ARCHITECT_AGENT_ID,
            "DevOpsAgent": DEVOPS_AGENT_ID,
        }[name],
        "resultStatus": result.get("status"),
        "verdict": result.get("verdict"),
        "reason": result.get("reason"),
        "evidencePackageId": evidence_id,
        "jobId": (result.get("job") or {}).get("id"),
        "agentRunId": (result.get("agentRun") or {}).get("id"),
    }


def _copy_patch_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    diff: dict[str, Any],
    repo: EvidenceRepository,
) -> dict[str, Any] | None:
    patch = str(diff.get("patchFull") or "")
    if not patch:
        return None
    return _write_text_evidence_artifact(
        root=root,
        project_id=project_id,
        evidence_id=evidence_id,
        name="diff.patch",
        kind="git_patch",
        suffix=".patch",
        content=patch,
        mime_type="text/x-diff",
        repo=repo,
        source="issue_to_pr",
    )


def _copy_security_findings_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    source_artifact: dict[str, Any] | None,
    repo: EvidenceRepository,
) -> dict[str, Any] | None:
    if not source_artifact:
        return None
    content = _artifact_content_bytes(source_artifact).decode("utf-8")
    return _write_text_evidence_artifact(
        root=root,
        project_id=project_id,
        evidence_id=evidence_id,
        name="security-findings.json",
        kind="security_findings",
        suffix=".json",
        content=content,
        mime_type="application/json",
        repo=repo,
        source="issue_to_pr",
    )


class IssueToPrRunner:
    """Drives the issue_to_pr gate DAG and its integration transitions for one project.

    Holds the repositories it coordinates (workflows, jobs, agents, workspaces, evidence) over a
    shared connection rooted at ``root``, and delegates branch promotion and PR creation to an
    IssueToPatchRunner while mapping the outcomes onto the issue_to_pr steps and events.
    """

    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.workflows = WorkflowsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.agents = AgentsRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)
        self.evidence = EvidenceRepository(connection)

    def _steps_by_name(self, run_id: str) -> dict[str, dict[str, Any]]:
        return {step["name"]: step for step in self.workflows.list_workflow_steps(workflow_run_id=run_id)}

    def _record_event(
        self,
        *,
        workflow: dict[str, Any],
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        step_id: str | None = None,
        severity: str = "info",
    ) -> None:
        self.workflows.record_workflow_event(
            workflow_id=workflow["id"],
            workflow_run_id=run_id,
            step_id=step_id,
            project_id=workflow["projectId"],
            event_type=event_type,
            payload=payload,
            severity=severity,
        )
        EventBus(self.connection).record_event(
            project_id=workflow["projectId"],
            event_type=event_type,
            payload={**payload, "workflowId": workflow["id"], "workflowRunId": run_id},
        )

    def _ensure_aggregator_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": "issue_to_pr_aggregator",
                "name": "AIDO Issue-to-PR Evidence Aggregator",
                "role": "technical_lead",
                "runtimeMode": "manual",
                "permissionProfile": "qa",
                "allowedTools": [],
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": False,
                "allowApi": False,
            }
        )

    def _update_steps_from_gates(
        self,
        *,
        steps: dict[str, dict[str, Any]],
        gate_results: list[dict[str, Any]],
        aggregate_evidence_id: str | None,
        final_status: str,
    ) -> None:
        for gate in gate_results:
            step_name = GATE_TO_STEP[gate["name"]]
            step = steps.get(step_name)
            if not step:
                continue
            self.workflows.update_workflow_step(
                step["id"],
                status="completed" if gate["status"] == "passed" else "blocked",
                output={
                    **(step.get("output") or {}),
                    "gate": gate,
                    "aggregateEvidencePackageId": aggregate_evidence_id,
                },
                metadata={**(step.get("metadata") or {}), "gateState": gate["status"]},
            )
        if steps.get("evidence_aggregation"):
            self.workflows.update_workflow_step(
                steps["evidence_aggregation"]["id"],
                status="completed" if aggregate_evidence_id else "blocked",
                output={
                    **(steps["evidence_aggregation"].get("output") or {}),
                    "aggregateEvidencePackageId": aggregate_evidence_id,
                    "status": final_status,
                },
            )
        if steps.get("approval"):
            self.workflows.update_workflow_step(
                steps["approval"]["id"],
                status="ready" if final_status == "evidence_ready" else "blocked",
                output={
                    **(steps["approval"].get("output") or {}),
                    "aggregateEvidencePackageId": aggregate_evidence_id,
                    "status": final_status,
                },
                metadata={
                    **(steps["approval"].get("metadata") or {}),
                    "gateState": "approval_required" if final_status == "evidence_ready" else final_status,
                },
            )

    def _aggregate_evidence(
        self,
        *,
        workflow: dict[str, Any],
        run_id: str,
        steps: dict[str, dict[str, Any]],
        workspace: dict[str, Any],
        gate_results: list[dict[str, Any]],
        agent_results: dict[str, dict[str, Any]],
        max_rework_attempts: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        all_gates_passed = all(
            gate["status"] == "passed" and gate.get("evidencePackageId") for gate in gate_results
        )
        blocked_gate = next((gate["name"] for gate in gate_results if gate["status"] != "passed"), None)
        diff = capture_git_diff(
            Path(workspace["path"]),
            connection=self.connection,
            root=self.root,
            project_id=workflow["projectId"],
            workspace_id=workspace["id"],
            task_id="issue_to_pr.evidence_aggregation",
        )
        completion = {
            "allGatesPassed": all_gates_passed,
            "blockedGate": GATE_TO_STEP.get(blocked_gate or "", blocked_gate),
            "requiredGates": [gate["name"] for gate in gate_results],
            "status": "evidence_ready" if all_gates_passed else "blocked",
        }
        profile = self._ensure_aggregator_profile()
        job = self.jobs.create_job(
            project_id=workflow["projectId"],
            kind="workflow.issue_to_pr.evidence_aggregation",
            workflow_run_id=run_id,
            workflow_step_id=steps["evidence_aggregation"]["id"],
            status="running",
            payload={
                "workflowRunId": run_id,
                "dag": ISSUE_TO_PR_DAG,
                "gateResults": gate_results,
                "completion": completion,
            },
        )["job"]
        agent_run = self.agents.create_agent_run(
            project_id=workflow["projectId"],
            agent_profile_id=profile["id"],
            task_id="issue_to_pr.evidence_aggregation",
            input_payload=redact_secrets({"workflowRunId": run_id, "gateResults": gate_results}),
            output_payload={},
            job_id=job["id"],
            workflow_run_id=run_id,
            workflow_step_id=steps["evidence_aggregation"]["id"],
            status="running",
        )
        qa_result = agent_results.get("QAAgent") or {}
        security_result = agent_results.get("SecurityAgent") or {}
        evidence = self.evidence.create_evidence_package(
            project_id=workflow["projectId"],
            workflow_run_id=run_id,
            workflow_step_id=steps["evidence_aggregation"]["id"],
            agent_id="issue_to_pr_aggregator",
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id="workflow.issue_to_pr.dag",
            task_id="issue_to_pr",
            test_plan="Aggregate evidence from DeveloperAgent, QAAgent, SecurityAgent, ArchitectAgent, and DevOpsAgent before approval.",
            acceptance_checklist=[
                "DAG is explicit and acyclic.",
                "DeveloperAgent produced a non-empty patch.",
                "QAAgent produced real command evidence.",
                "SecurityAgent produced deterministic findings evidence.",
                "ArchitectAgent produced real model review evidence.",
                "DevOpsAgent produced deterministic release readiness evidence.",
                "Human approval remains required before branch promotion.",
            ],
            test_results=(qa_result.get("results") or []),
            logs=[
                redact_secrets(
                    {
                        "dag": ISSUE_TO_PR_DAG,
                        "gateResults": gate_results,
                        "completion": completion,
                        "rework": {"maxAttempts": max_rework_attempts, "attemptsUsed": 0},
                    }
                )
            ],
            diff_refs=_final_diff_refs(workspace, diff),
            diff_summary={
                **_diff_summary(diff),
                "agentEvidencePackageIds": {
                    gate["name"]: gate.get("evidencePackageId")
                    for gate in gate_results
                    if gate.get("evidencePackageId")
                },
                "gateStatuses": {gate["name"]: gate["status"] for gate in gate_results},
            },
            runtime_health={
                "id": "workflow.issue_to_pr.dag",
                "status": completion["status"],
                "available": all_gates_passed,
                "executable": all_gates_passed,
                "reason": "All issue_to_pr gates passed."
                if all_gates_passed
                else f"Blocked gate: {completion['blockedGate']}.",
            },
            approvals=[],
            evidence_source="verified_completion" if all_gates_passed else "evidence_collected",
            qa_verdict="passed" if all_gates_passed else "blocked",
            risk_notes=[
                {
                    "severity": "low" if all_gates_passed else "high",
                    "description": "All issue_to_pr gates passed."
                    if all_gates_passed
                    else f"Gate blocked: {completion['blockedGate']}.",
                    "mitigation": "Fix the blocked gate and rerun issue_to_pr before approval.",
                }
            ],
        )
        aggregate_artifacts: list[dict[str, Any]] = []
        patch_artifact = _copy_patch_artifact(
            root=self.root,
            project_id=workflow["projectId"],
            evidence_id=evidence["id"],
            diff=diff,
            repo=self.evidence,
        )
        if patch_artifact:
            aggregate_artifacts.append(patch_artifact)
        security_copy = _copy_security_findings_artifact(
            root=self.root,
            project_id=workflow["projectId"],
            evidence_id=evidence["id"],
            source_artifact=security_result.get("findingsArtifact"),
            repo=self.evidence,
        )
        if security_copy:
            aggregate_artifacts.append(security_copy)
        manifest = _write_json_evidence_artifact(
            root=self.root,
            project_id=workflow["projectId"],
            evidence_id=evidence["id"],
            name="issue-to-pr-evidence.json",
            kind="evidence_manifest",
            payload={
                "workflowRunId": run_id,
                "status": completion["status"],
                "dag": ISSUE_TO_PR_DAG,
                "gateResults": gate_results,
                "completion": completion,
            },
            repo=self.evidence,
            source="issue_to_pr",
        )
        aggregate_artifacts.append(manifest)
        diff_summary = dict(evidence.get("diffSummary") or {})
        if patch_artifact:
            diff_summary["patchArtifactId"] = patch_artifact["id"]
            diff_summary["patchArtifactHash"] = patch_artifact["hash"]
        if security_copy:
            diff_summary["securityFindingsArtifactId"] = security_copy["id"]
            diff_summary["securityFindingsArtifactHash"] = security_copy["hash"]
        diff_summary["manifestArtifactId"] = manifest["id"]
        related_agent_run_ids = {
            str((result.get("agentRun") or {}).get("id"))
            for result in agent_results.values()
            if (result.get("agentRun") or {}).get("id")
        }
        related_agent_run_ids.add(agent_run["id"])
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) in related_agent_run_ids
        ]
        policy_decisions: list[dict[str, Any]] = []
        for result in agent_results.values():
            package = result.get("evidencePackage") or {}
            policy_decisions.extend(package.get("policyDecisions") or [])
        evidence = self.evidence.update_evidence_links(
            evidence["id"],
            artifact_ids=[artifact["id"] for artifact in aggregate_artifacts],
            diff_summary=diff_summary,
            tool_calls=tool_calls,
            policy_decisions=policy_decisions,
            artifacts=[artifact_ref(artifact) for artifact in aggregate_artifacts],
            hashes=artifact_hashes(aggregate_artifacts),
        )
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=all_gates_passed,
            require_workflow_run=True,
        )
        if all_gates_passed and contract_errors:
            completion = {
                **completion,
                "allGatesPassed": False,
                "blockedGate": "evidence_aggregation",
                "status": "blocked",
            }
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict="blocked",
                risk_notes=[
                    {
                        "severity": "high",
                        "description": "Aggregated evidence package contract is incomplete: "
                        + "; ".join(contract_errors),
                        "mitigation": "Regenerate issue_to_pr aggregate evidence with linked artifacts and hashes.",
                    }
                ],
            )
        final_status = "evidence_ready" if completion["allGatesPassed"] else "blocked"
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status="completed" if final_status == "evidence_ready" else "failed",
            output_payload={
                "status": final_status,
                "summary": "issue_to_pr aggregate evidence ready."
                if final_status == "evidence_ready"
                else "issue_to_pr gates blocked.",
                "completion": completion,
                "evidence_refs": [evidence["id"], *[artifact["id"] for artifact in aggregate_artifacts]],
            },
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="approval_required" if final_status == "evidence_ready" else "failed",
            metadata={"status": final_status, "completion": completion, "evidencePackageId": evidence["id"]},
        )
        return evidence, job, agent_run

    def _create_approval_action(
        self,
        *,
        workflow: dict[str, Any],
        run_id: str,
        evidence: dict[str, Any],
        job: dict[str, Any],
        gate_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.jobs.create_action_request(
            job_id=job["id"],
            project_id=workflow["projectId"],
            action_type=ISSUE_TO_PR_APPROVAL_ACTION,
            risk_level="high",
            command="approve issue_to_pr evidence for branch promotion",
            payload={
                "workflowRunId": run_id,
                "evidencePackageId": evidence["id"],
                "dag": ISSUE_TO_PR_DAG,
                "gateResults": gate_results,
            },
            reason="issue_to_pr requires human approval after all agent gates pass and before branch promotion.",
        )

    def _response(
        self,
        *,
        status: str,
        reason: str,
        workflow: dict[str, Any],
        workflow_run: dict[str, Any],
        workspace: dict[str, Any],
        job: dict[str, Any],
        agent_run: dict[str, Any],
        evidence: dict[str, Any],
        gate_results: list[dict[str, Any]],
        rework: dict[str, Any],
        completion: dict[str, Any],
        runtime_result: dict[str, Any] | None = None,
        pull_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "workflow": workflow,
            "workflowRun": workflow_run,
            "workflowSteps": self.workflows.list_workflow_steps(workflow_run_id=workflow_run["id"]),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": evidence.get("runtimeHealth") or {},
            "runtimeResult": runtime_result or {},
            "qaResults": evidence.get("testResults") or [],
            "diffSummary": evidence.get("diffSummary") or {},
            "pullRequest": pull_request,
            "dag": ISSUE_TO_PR_DAG,
            "gateResults": gate_results,
            "rework": rework,
            "completion": completion,
            "timeline": self._timeline(workflow_run["id"]),
        }

    def _timeline(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT type, payload, severity, created_at FROM workflow_events WHERE workflow_run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        return [
            {
                "type": row["type"],
                "payload": json_loads(row["payload"], {}),
                "severity": row["severity"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the full issue_to_pr gate DAG and return its terminal status and per-gate results.

        Creates and starts the workflow, runs the developer agent (retrying up to
        ``maxReworkAttempts``), then the QA/security/architecture/devops gates, and aggregates a
        contract-checked evidence package. Returns ``evidence_ready`` (seeding the approval action)
        when every gate passes, otherwise ``blocked`` naming the first failing gate.

        Raises:
            ValueError: if a requested ``preferredRuntime`` is not in the product catalog.
        """
        preferred_runtime = str(payload.get("preferredRuntime") or "").strip()
        if preferred_runtime:
            runtime_statuses = RuntimeStatusService(self.connection).list_provider_statuses()
            if not any(status["id"] == preferred_runtime for status in runtime_statuses):
                raise ValueError(f"Runtime provider is not in the product catalog: {preferred_runtime}")
        max_rework_attempts = int(payload.get("maxReworkAttempts", 1))
        metadata = {
            "steps": [
                {
                    "name": step_name,
                    "taskType": step_name,
                    "metadata": {
                        "dependsOn": [edge[0] for edge in ISSUE_TO_PR_DAG["edges"] if edge[1] == step_name]
                    },
                }
                for step_name in ISSUE_TO_PR_STEPS
            ],
            "dag": ISSUE_TO_PR_DAG,
            "rework": {"maxAttempts": max_rework_attempts},
            "optionalPr": bool(payload.get("createPullRequest", False)),
        }
        workflow = self.workflows.create_workflow(
            project_id=payload["projectId"],
            kind="issue_to_pr",
            title=payload["title"],
            metadata=metadata,
        )
        started = self.workflows.start_workflow(workflow["id"], reason="issue_to_pr requested")
        workflow = started["workflow"]
        workflow_run = started["workflowRun"]
        run_id = workflow_run["id"]
        steps = self._steps_by_name(run_id)
        self._record_event(
            workflow=workflow,
            run_id=run_id,
            event_type="workflow.issue_to_pr.dag.started",
            payload={"dag": ISSUE_TO_PR_DAG, "maxReworkAttempts": max_rework_attempts},
            step_id=steps["developer_agent"]["id"],
        )
        workspace = self.workspaces.allocate_workspace(
            project_id=workflow["projectId"],
            task_id=f"issue-to-pr-{run_id}-{uuid.uuid4().hex[:8]}",
            agent_id=DEVELOPER_AGENT_ID,
            reason="issue_to_pr DeveloperAgent workspace",
            isolation_type="git_worktree",
            workflow_run_id=run_id,
            workflow_step_id=steps["developer_agent"]["id"],
        )

        attempts: list[dict[str, Any]] = []
        agent_results: dict[str, dict[str, Any]] = {}
        for attempt in range(1, max_rework_attempts + 2):
            attempts.append({"attempt": attempt, "startedAt": utc_now()})
            self.workflows.update_workflow_step(steps["developer_agent"]["id"], status="running")
            developer = DeveloperAgentRunner(self.connection, root=self.root).run(
                {
                    "projectId": workflow["projectId"],
                    "workspaceId": workspace["id"],
                    "taskId": f"issue_to_pr.developer.attempt_{attempt}",
                    "instruction": _developer_instruction_for_issue(
                        title=payload["title"],
                        issue_text=payload["issueText"],
                        target_path=payload.get("targetPath"),
                        attempt=attempt,
                    ),
                    "preferredRuntime": payload.get("preferredRuntime"),
                    "qaCommands": payload.get("qaCommands") or [],
                    "requireApproval": False,
                    "maxCostUsd": payload.get("maxCostUsd"),
                    "workflowRunId": run_id,
                    "workflowStepId": steps["developer_agent"]["id"],
                    "qaWorkflowStepId": steps["developer_agent"]["id"],
                }
            )
            agent_results["DeveloperAgent"] = developer
            developer_passed = _status_passed(developer, "completed")
            if not developer_passed:
                break

            self.workflows.update_workflow_step(steps["qa_validation"]["id"], status="running")
            qa = QAAgentRunner(self.connection, root=self.root).run(
                {
                    "projectId": workflow["projectId"],
                    "workspaceId": workspace["id"],
                    "taskId": f"issue_to_pr.qa.attempt_{attempt}",
                    "commands": payload.get("qaCommands") or [],
                    "workflowRunId": run_id,
                    "workflowStepId": steps["qa_validation"]["id"],
                    "metadata": {"source": "issue_to_pr", "attempt": attempt},
                }
            )
            agent_results["QAAgent"] = qa
            if not _status_passed(qa, "passed") or not qa_verdict_allows_completion(
                "passed", qa.get("results") or []
            ):
                if attempt <= max_rework_attempts:
                    self._record_event(
                        workflow=workflow,
                        run_id=run_id,
                        event_type="workflow.issue_to_pr.rework_required",
                        payload={
                            "attempt": attempt,
                            "blockedGate": "qa_validation",
                            "reason": qa.get("reason"),
                        },
                        step_id=steps["qa_validation"]["id"],
                        severity="warning",
                    )
                    continue
                break

            patch_artifact_id = str((developer.get("diffSummary") or {}).get("patchArtifactId") or "")
            self.workflows.update_workflow_step(steps["security_review"]["id"], status="running")
            security = SecurityAgentRunner(self.connection, root=self.root).run(
                {
                    "projectId": workflow["projectId"],
                    "workspaceId": workspace["id"],
                    "taskId": f"issue_to_pr.security.attempt_{attempt}",
                    "diffArtifactId": patch_artifact_id or None,
                    "workflowRunId": run_id,
                    "workflowStepId": steps["security_review"]["id"],
                }
            )
            agent_results["SecurityAgent"] = security

            self.workflows.update_workflow_step(steps["architecture_review"]["id"], status="running")
            architect = ArchitectAgentRunner(self.connection, root=self.root).run(
                {
                    "projectId": workflow["projectId"],
                    "workspaceId": workspace["id"],
                    "taskId": f"issue_to_pr.architect.attempt_{attempt}",
                    "diffArtifactId": patch_artifact_id or None,
                    "workflowContext": {
                        "workflowKind": "issue_to_pr",
                        "workflowRunId": run_id,
                        "workflowStepId": steps["architecture_review"]["id"],
                        # El DAG completo no viaja al agente: el prompt solo whitelistea
                        # workflowKind/attempt/title y el DAG ya queda en la metadata del workflow.
                        "attempt": attempt,
                    },
                    "testResults": qa.get("results") or [],
                }
            )
            agent_results["ArchitectAgent"] = architect

            self.workflows.update_workflow_step(steps["devops_validation"]["id"], status="running")
            devops = DevOpsAgentRunner(self.connection, root=self.root).run(
                {
                    "projectId": workflow["projectId"],
                    "workspaceId": workspace["id"],
                    "taskId": f"issue_to_pr.devops.attempt_{attempt}",
                    "workflowRunId": run_id,
                    "workflowStepId": steps["devops_validation"]["id"],
                    "buildScripts": payload.get("buildScripts") or [],
                    "qualityScripts": payload.get("qualityScripts") or [],
                    "dockerHealthcheck": bool(payload.get("dockerHealthcheck", False)),
                }
            )
            agent_results["DevOpsAgent"] = devops

            retryable_block = next(
                (
                    gate
                    for gate, result, expected in [
                        ("security_review", security, "passed"),
                        ("devops_validation", devops, "passed"),
                    ]
                    if not _status_passed(result, expected)
                ),
                None,
            )
            if retryable_block and attempt <= max_rework_attempts:
                self._record_event(
                    workflow=workflow,
                    run_id=run_id,
                    event_type="workflow.issue_to_pr.rework_required",
                    payload={"attempt": attempt, "blockedGate": retryable_block},
                    step_id=steps[retryable_block]["id"],
                    severity="warning",
                )
                continue
            break

        gate_results = [
            _gate_result(
                "DeveloperAgent",
                agent_results.get("DeveloperAgent") or {},
                passed=_status_passed(agent_results.get("DeveloperAgent") or {}, "completed"),
                evidence_id=((agent_results.get("DeveloperAgent") or {}).get("evidencePackage") or {}).get(
                    "id"
                ),
            ),
            _gate_result(
                "QAAgent",
                agent_results.get("QAAgent") or {},
                passed=_status_passed(agent_results.get("QAAgent") or {}, "passed")
                and qa_verdict_allows_completion(
                    "passed", (agent_results.get("QAAgent") or {}).get("results") or []
                ),
                evidence_id=((agent_results.get("QAAgent") or {}).get("evidencePackage") or {}).get("id"),
            ),
            _gate_result(
                "SecurityAgent",
                agent_results.get("SecurityAgent") or {},
                passed=_status_passed(agent_results.get("SecurityAgent") or {}, "passed"),
                evidence_id=((agent_results.get("SecurityAgent") or {}).get("evidencePackage") or {}).get(
                    "id"
                ),
            ),
            _gate_result(
                "ArchitectAgent",
                agent_results.get("ArchitectAgent") or {},
                passed=_status_passed(agent_results.get("ArchitectAgent") or {}, "completed"),
                evidence_id=((agent_results.get("ArchitectAgent") or {}).get("evidencePackage") or {}).get(
                    "id"
                ),
            ),
            _gate_result(
                "DevOpsAgent",
                agent_results.get("DevOpsAgent") or {},
                passed=_status_passed(agent_results.get("DevOpsAgent") or {}, "passed"),
                evidence_id=((agent_results.get("DevOpsAgent") or {}).get("evidencePackage") or {}).get("id"),
            ),
        ]
        evidence, job, agent_run = self._aggregate_evidence(
            workflow=workflow,
            run_id=run_id,
            steps=steps,
            workspace=workspace,
            gate_results=gate_results,
            agent_results=agent_results,
            max_rework_attempts=max_rework_attempts,
        )
        completion = {
            "allGatesPassed": all(
                gate["status"] == "passed" and gate.get("evidencePackageId") for gate in gate_results
            ),
            "blockedGate": next(
                (GATE_TO_STEP[gate["name"]] for gate in gate_results if gate["status"] != "passed"),
                None,
            ),
            "requiredGates": [gate["name"] for gate in gate_results],
        }
        final_status = "evidence_ready" if completion["allGatesPassed"] else "blocked"
        reason = (
            "issue_to_pr evidence is ready and requires approval before branch promotion."
            if final_status == "evidence_ready"
            else f"issue_to_pr blocked at gate: {completion['blockedGate']}."
        )
        action = None
        if final_status == "evidence_ready":
            action = self._create_approval_action(
                workflow=workflow,
                run_id=run_id,
                evidence=evidence,
                job=job,
                gate_results=gate_results,
            )
            evidence = self.evidence.update_evidence_links(
                evidence["id"], approvals=self.jobs.list_action_requests(job["id"])
            )
            job = self.jobs.update_job_status(
                job["id"],
                status="approval_required",
                metadata={
                    "status": final_status,
                    "evidencePackageId": evidence["id"],
                    "actionRequestId": action["id"],
                },
            )
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=final_status,
            metadata={
                **(workflow_run.get("metadata") or {}),
                "dag": ISSUE_TO_PR_DAG,
                "gateResults": gate_results,
                "evidencePackageId": evidence["id"],
                "jobId": job["id"],
                "agentRunId": agent_run["id"],
                "workspaceId": workspace["id"],
                "rework": {
                    "maxAttempts": max_rework_attempts,
                    "attempts": attempts,
                    "attemptsUsed": len(attempts),
                },
                "completion": completion,
                "approvalActionRequestId": (action or {}).get("id"),
            },
            completed=False,
            clear_completed=True,
        )
        workflow = self.workflows.update_workflow_status(workflow["id"], status=final_status, reason=reason)
        self._update_steps_from_gates(
            steps=steps,
            gate_results=gate_results,
            aggregate_evidence_id=evidence["id"],
            final_status=final_status,
        )
        self._record_event(
            workflow=workflow,
            run_id=run_id,
            event_type="workflow.issue_to_pr.evidence_aggregated",
            payload={
                "status": final_status,
                "evidencePackageId": evidence["id"],
                "gateResults": gate_results,
            },
            step_id=steps["evidence_aggregation"]["id"],
            severity="info" if final_status == "evidence_ready" else "warning",
        )
        if final_status != "evidence_ready":
            self._record_event(
                workflow=workflow,
                run_id=run_id,
                event_type="workflow.issue_to_pr.gate.blocked",
                payload={
                    "status": final_status,
                    "blockedGate": completion["blockedGate"],
                    "gateResults": gate_results,
                },
                severity="warning",
            )
        return self._response(
            status=final_status,
            reason=reason,
            workflow=workflow,
            workflow_run=workflow_run,
            workspace=workspace,
            job=job,
            agent_run=agent_run,
            evidence=evidence,
            gate_results=gate_results,
            rework={"maxAttempts": max_rework_attempts, "attempts": attempts, "attemptsUsed": len(attempts)},
            completion=completion,
            runtime_result={
                "agentResults": {name: result.get("status") for name, result in agent_results.items()}
            },
        )

    def approve_issue_to_pr(self, run_id: str, *, reason: str, actor: str = "operator") -> dict[str, Any]:
        """Transition an evidence_ready issue_to_pr run to approved_for_integration.

        Requires a non-empty reason, an approved action request for this run's evidence package,
        and a complete evidence contract (runtime + workflow-run links) before approving.

        Raises:
            ValueError: if the reason is empty, the run is the wrong kind/status, the evidence or
                its links are missing/mismatched, or the evidence contract is incomplete.
        """
        clean_reason = str(redact_secrets(reason or "")).strip()
        if not clean_reason:
            raise ValueError("Approval reason is required.")
        workflow_run = self.workflows.get_workflow_run(run_id)
        workflow = self.workflows.get_workflow(workflow_run["workflowId"])
        if workflow["kind"] != "issue_to_pr":
            raise ValueError("Only issue_to_pr workflow runs can use this approval transition.")
        if workflow_run["status"] != "evidence_ready":
            raise ValueError("issue_to_pr approval requires an evidence_ready workflow run.")
        run_metadata = workflow_run.get("metadata") or {}
        evidence_id = str(run_metadata.get("evidencePackageId") or "")
        evidence = self.evidence.get_evidence_package(evidence_id)
        if evidence["workflowRunId"] != run_id:
            raise ValueError("Evidence package does not belong to this workflow run.")
        job_id = str(evidence.get("jobId") or run_metadata.get("jobId") or "")
        agent_run_id = str(evidence.get("agentRunId") or run_metadata.get("agentRunId") or "")
        workspace_id = str(evidence.get("workspaceId") or run_metadata.get("workspaceId") or "")
        if not job_id or not agent_run_id or not workspace_id:
            raise ValueError("issue_to_pr approval requires linked job, agent run, and workspace records.")
        approval_actions = [
            action
            for action in self.jobs.list_action_requests(job_id)
            if action["actionType"] == ISSUE_TO_PR_APPROVAL_ACTION
            and action["status"] == "approved"
            and (action.get("payload") or {}).get("workflowRunId") == run_id
            and (action.get("payload") or {}).get("evidencePackageId") == evidence_id
        ]
        if not approval_actions:
            raise ValueError(
                "issue_to_pr approval requires an approved action request for this evidence package."
            )
        evidence = self.evidence.update_evidence_links(
            evidence_id, approvals=self.jobs.list_action_requests(job_id)
        )
        contract_errors = evidence_package_contract_errors(
            evidence, require_runtime_links=True, require_workflow_run=True
        )
        if contract_errors:
            raise ValueError("Evidence package contract is incomplete: " + "; ".join(contract_errors))
        artifacts = self.evidence.list_artifacts(evidence_id)
        _validate_patch_artifact(evidence, artifacts)
        _validate_qa_for_patch_approval(evidence, action_approved=True)
        _validate_security_findings(evidence, artifacts)
        transition_payload = {
            "status": APPROVED_FOR_INTEGRATION_STATUS,
            "reason": clean_reason,
            "approvedAt": utc_now(),
            "approvedBy": actor,
            "actionRequestId": approval_actions[0]["id"],
            "evidencePackageId": evidence_id,
            "jobId": job_id,
            "agentRunId": agent_run_id,
            "workspaceId": workspace_id,
        }
        workflow_run = self.workflows.update_workflow_run_status(
            run_id,
            status=APPROVED_FOR_INTEGRATION_STATUS,
            metadata={**run_metadata, **transition_payload},
            completed=False,
            clear_completed=True,
        )
        workflow = self.workflows.update_workflow_status(
            workflow["id"], status=APPROVED_FOR_INTEGRATION_STATUS, reason=clean_reason
        )
        job = self.jobs.update_job_status(job_id, status="approved", metadata=transition_payload)
        agent_run = self.agents.update_agent_run_status(
            agent_run_id,
            status="approved",
            output_payload={
                **(self.agents.get_agent_run(agent_run_id).get("output") or {}),
                "approvedForIntegration": True,
                "approvalActionRequestId": approval_actions[0]["id"],
                "evidence_refs": [evidence_id],
            },
        )
        steps = self._steps_by_name(run_id)
        if steps.get("approval"):
            self.workflows.update_workflow_step(
                steps["approval"]["id"],
                status="completed",
                output={
                    "approvedForIntegration": True,
                    "reason": clean_reason,
                    "evidencePackageId": evidence_id,
                },
                metadata={
                    **(steps["approval"].get("metadata") or {}),
                    "gateState": APPROVED_FOR_INTEGRATION_STATUS,
                },
            )
        self._record_event(
            workflow=workflow,
            run_id=run_id,
            event_type=f"workflow.issue_to_pr.{APPROVED_FOR_INTEGRATION_STATUS}",
            payload=transition_payload,
            step_id=(steps.get("approval") or {}).get("id"),
        )
        return self._response(
            status=APPROVED_FOR_INTEGRATION_STATUS,
            reason=clean_reason,
            workflow=workflow,
            workflow_run=workflow_run,
            workspace=self.workspaces.get_workspace(workspace_id),
            job=job,
            agent_run=agent_run,
            evidence=self.evidence.get_evidence_package(evidence_id),
            gate_results=run_metadata.get("gateResults") or [],
            rework=run_metadata.get("rework") or {},
            completion={
                "allGatesPassed": True,
                "blockedGate": None,
                "requiredGates": [gate["name"] for gate in run_metadata.get("gateResults") or []],
            },
        )

    def promote_branch(
        self,
        run_id: str,
        *,
        reason: str,
        branch_name: str | None = None,
        evidence_package_id: str | None = None,
        qa_commands: list[list[str]] | None = None,
    ) -> dict[str, Any]:
        """Promote the approved patch to a branch and mirror the outcome onto the issue_to_pr steps.

        Delegates to ``IssueToPatchRunner.promote_patch_to_branch`` then updates the
        ``branch_promotion`` step, records the timeline event and returns the issue_to_pr response
        with gate/rework/completion context.
        """
        result = IssueToPatchRunner(self.connection, root=self.root).promote_patch_to_branch(
            run_id,
            reason=reason,
            branch_name=branch_name,
            evidence_package_id=evidence_package_id,
            qa_commands=qa_commands,
        )
        workflow = result["workflow"]
        workflow_run = result["workflowRun"]
        run_metadata = workflow_run.get("metadata") or {}
        steps = self._steps_by_name(run_id)
        branch_step = steps.get("branch_promotion")
        if branch_step:
            self.workflows.update_workflow_step(
                branch_step["id"],
                status="completed" if result["status"] == PROMOTED_TO_BRANCH_STATUS else "blocked",
                output={
                    **(branch_step.get("output") or {}),
                    "promotionStatus": result["status"],
                    "promotionEvidencePackageId": result["evidencePackage"]["id"],
                    "branchName": result["diffSummary"].get("branch"),
                    "reason": result["reason"],
                },
                metadata={**(branch_step.get("metadata") or {}), "gateState": result["status"]},
            )
        self._record_event(
            workflow=workflow,
            run_id=run_id,
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "status": result["status"],
                "reason": result["reason"],
                "promotionEvidencePackageId": result["evidencePackage"]["id"],
                "branchName": result["diffSummary"].get("branch"),
            },
            step_id=(branch_step or {}).get("id"),
            severity="info" if result["status"] == PROMOTED_TO_BRANCH_STATUS else "warning",
        )
        completion = {
            "allGatesPassed": result["status"] == PROMOTED_TO_BRANCH_STATUS,
            "blockedGate": None if result["status"] == PROMOTED_TO_BRANCH_STATUS else "branch_promotion",
            "requiredGates": [gate["name"] for gate in run_metadata.get("gateResults") or []],
        }
        return self._response(
            status=result["status"],
            reason=result["reason"],
            workflow=workflow,
            workflow_run=self.workflows.get_workflow_run(run_id),
            workspace=result["workspace"],
            job=result["job"],
            agent_run=result["agentRun"],
            evidence=result["evidencePackage"],
            gate_results=run_metadata.get("gateResults") or [],
            rework=run_metadata.get("rework") or {},
            completion=completion,
            runtime_result=result.get("runtimeResult") or {},
        )

    def create_pull_request(
        self,
        run_id: str,
        *,
        reason: str,
        title: str | None = None,
        base_branch: str | None = None,
    ) -> dict[str, Any]:
        """Open the PR from the promoted branch and mirror the outcome onto the issue_to_pr steps.

        Delegates to ``IssueToPatchRunner.create_pull_request_from_promoted_branch`` then updates
        the ``pr_creation`` step, records the timeline event and returns the issue_to_pr response.
        """
        result = IssueToPatchRunner(self.connection, root=self.root).create_pull_request_from_promoted_branch(
            run_id,
            reason=reason,
            title=title,
            base_branch=base_branch,
        )
        workflow = result["workflow"]
        workflow_run = result["workflowRun"]
        run_metadata = workflow_run.get("metadata") or {}
        steps = self._steps_by_name(run_id)
        pr_step = steps.get("pr_creation")
        if pr_step:
            self.workflows.update_workflow_step(
                pr_step["id"],
                status="completed" if result["status"] == PR_CREATED_STATUS else "blocked",
                output={
                    **(pr_step.get("output") or {}),
                    "pullRequestStatus": result["status"],
                    "pullRequestEvidencePackageId": result["evidencePackage"]["id"],
                    "pullRequest": result.get("pullRequest"),
                    "reason": result["reason"],
                },
                metadata={**(pr_step.get("metadata") or {}), "gateState": result["status"]},
            )
        self._record_event(
            workflow=workflow,
            run_id=run_id,
            event_type=f"workflow.issue_to_pr.{result['status']}",
            payload={
                "status": result["status"],
                "reason": result["reason"],
                "pullRequestEvidencePackageId": result["evidencePackage"]["id"],
                "pullRequest": result.get("pullRequest"),
            },
            step_id=(pr_step or {}).get("id"),
            severity="info" if result["status"] == PR_CREATED_STATUS else "warning",
        )
        completion = {
            "allGatesPassed": result["status"] == PR_CREATED_STATUS,
            "blockedGate": None if result["status"] == PR_CREATED_STATUS else "pr_creation",
            "requiredGates": [gate["name"] for gate in run_metadata.get("gateResults") or []],
        }
        return self._response(
            status=result["status"],
            reason=result["reason"],
            workflow=workflow,
            workflow_run=self.workflows.get_workflow_run(run_id),
            workspace=result["workspace"],
            job=result["job"],
            agent_run=result["agentRun"],
            evidence=result["evidencePackage"],
            gate_results=run_metadata.get("gateResults") or [],
            rework=run_metadata.get("rework") or {},
            completion=completion,
            runtime_result=result.get("runtimeResult") or {},
            pull_request=result.get("pullRequest"),
        )
