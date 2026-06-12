from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.evidence.artifacts import artifact_hashes, artifact_records_from_ids, artifact_ref
from local_control_center.evidence.quality import evidence_package_contract_errors
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.governance.repository import GovernanceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .architect_agent_contract import (
    ARCHITECT_AGENT_ALLOWED_TOOLS,
    ARCHITECT_AGENT_ID,
    ARCHITECT_AGENT_MODEL_RUNTIMES,
    ARCHITECT_AGENT_VERDICTS,
    architect_agent_contract,
    architect_agent_readiness,
)
from .repository import AgentsRepository
from .runtime_status import RuntimeStatusService
from .tool_broker import ToolBroker


RUNTIME_UNAVAILABLE_STATUS = "runtime_unavailable"
FAILED_VALIDATION_STATUS = "failed_validation"
ARCHITECT_TERMINAL_STATUSES = {"completed", RUNTIME_UNAVAILABLE_STATUS, FAILED_VALIDATION_STATUS, "failed"}
RISK_SEVERITIES = {"low", "medium", "high", "critical"}
MODEL_OUTPUT_LIMIT_CHARS = 120_000
PROMPT_DIFF_LIMIT_CHARS = 40_000
PROMPT_COLLECTION_LIMIT = 20
PROMPT_TEXT_LIMIT_CHARS = 8_000


class ArchitectOutputValidationError(ValueError):
    pass


def _runtime_mode(runtime_id: str) -> str:
    return "ollama" if runtime_id == "ollama" else "api"


def _execution_result_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    return {
        "status": "completed" if tool_call.get("status") == "completed" else "failed",
        "toolCallId": tool_call.get("id"),
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason") or payload.get("decisionReason"),
        "outputArtifactId": execution_result.get("outputArtifactId"),
        "evidencePackageId": execution_result.get("evidencePackageId"),
        "redacted": bool(execution_result.get("redacted", False)),
    }


def _runtime_unavailable_result(reason: str) -> dict[str, Any]:
    return {"status": RUNTIME_UNAVAILABLE_STATUS, "reason": reason, "execution": "not_executed"}


def _bounded_text(value: Any, limit: int = PROMPT_TEXT_LIMIT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _bounded_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for item in items[:PROMPT_COLLECTION_LIMIT]:
        bounded_item: dict[str, Any] = {}
        for key, value in item.items():
            if isinstance(value, str):
                bounded_item[key] = _bounded_text(value)
            else:
                bounded_item[key] = value
        bounded.append(bounded_item)
    return bounded


def _json_object_from_text(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ArchitectOutputValidationError("ArchitectAgent model output is not valid JSON.") from error
    if not isinstance(payload, dict):
        raise ArchitectOutputValidationError("ArchitectAgent model output must be a JSON object.")
    return payload


def _as_object_list(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ArchitectOutputValidationError(f"ArchitectAgent output schema requires {field} as a list.")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ArchitectOutputValidationError(f"ArchitectAgent output schema requires {field}[{index}] as an object.")
        result.append(item)
    return result


def _string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ArchitectOutputValidationError(f"ArchitectAgent output schema requires {field} as a string list.")
    refs = [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
    if len(refs) != len(value):
        raise ArchitectOutputValidationError(f"ArchitectAgent output schema requires {field} to contain only strings.")
    return refs


def _validate_refs(
    *,
    refs: list[str],
    field: str,
    allowed_refs: set[str],
    grounding_refs: set[str],
    require_grounding: bool,
) -> None:
    if not refs:
        raise ArchitectOutputValidationError(f"{field} must include at least one evidence reference.")
    unknown = sorted(set(refs) - allowed_refs)
    if unknown:
        raise ArchitectOutputValidationError(f"{field} contains unknown evidence references: {', '.join(unknown)}")
    if require_grounding and not set(refs).intersection(grounding_refs):
        raise ArchitectOutputValidationError(f"{field} must cite the diff artifact or test evidence.")


def _validate_review_item(
    item: dict[str, Any],
    *,
    field: str,
    index: int,
    allowed_refs: set[str],
    grounding_refs: set[str],
    require_grounding: bool,
) -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or item.get("summary") or "").strip()
    if not title:
        raise ArchitectOutputValidationError(f"{field}[{index}].title is required.")
    if not description:
        raise ArchitectOutputValidationError(f"{field}[{index}].description is required.")
    refs = _string_list(item.get("evidenceRefs"), field=f"{field}[{index}].evidenceRefs")
    _validate_refs(
        refs=refs,
        field=f"{field}[{index}].evidenceRefs",
        allowed_refs=allowed_refs,
        grounding_refs=grounding_refs,
        require_grounding=require_grounding,
    )
    normalized = {**item, "title": title, "description": description, "evidenceRefs": refs}
    if "severity" in normalized:
        severity = str(normalized.get("severity") or "medium").lower()
        if severity not in RISK_SEVERITIES:
            raise ArchitectOutputValidationError(f"{field}[{index}].severity is invalid.")
        normalized["severity"] = severity
    return normalized


def _validate_architect_output(
    payload: dict[str, Any],
    *,
    allowed_refs: set[str],
    grounding_refs: set[str],
) -> dict[str, Any]:
    missing = [field for field in architect_agent_contract()["outputSchema"]["required"] if field not in payload]
    if missing:
        raise ArchitectOutputValidationError(
            "ArchitectAgent output schema is missing required fields: " + ", ".join(missing)
        )
    verdict = str(payload.get("verdict") or "").strip()
    if verdict not in ARCHITECT_AGENT_VERDICTS:
        raise ArchitectOutputValidationError("ArchitectAgent output verdict is invalid.")
    output_refs = _string_list(payload.get("evidenceRefs"), field="evidenceRefs")
    _validate_refs(
        refs=output_refs,
        field="evidenceRefs",
        allowed_refs=allowed_refs,
        grounding_refs=grounding_refs,
        require_grounding=True,
    )

    findings = [
        _validate_review_item(
            item,
            field="architectureFindings",
            index=index,
            allowed_refs=allowed_refs,
            grounding_refs=grounding_refs,
            require_grounding=True,
        )
        for index, item in enumerate(_as_object_list(payload, "architectureFindings"))
    ]
    risks = [
        _validate_review_item(
            item,
            field="risks",
            index=index,
            allowed_refs=allowed_refs,
            grounding_refs=grounding_refs,
            require_grounding=True,
        )
        for index, item in enumerate(_as_object_list(payload, "risks"))
    ]
    for index, risk in enumerate(risks):
        if not str(risk.get("mitigation") or "").strip():
            raise ArchitectOutputValidationError(f"risks[{index}].mitigation is required.")
        risk["mitigation"] = str(risk["mitigation"]).strip()
        risk.setdefault("severity", "medium")
    required_changes = [
        _validate_review_item(
            item,
            field="requiredChanges",
            index=index,
            allowed_refs=allowed_refs,
            grounding_refs=grounding_refs,
            require_grounding=True,
        )
        for index, item in enumerate(_as_object_list(payload, "requiredChanges"))
    ]

    recommendation = payload.get("approvalRecommendation")
    if not isinstance(recommendation, dict):
        raise ArchitectOutputValidationError("approvalRecommendation must be an object.")
    recommendation_decision = str(recommendation.get("decision") or "").strip()
    recommendation_reason = str(recommendation.get("reason") or "").strip()
    if not recommendation_decision or not recommendation_reason:
        raise ArchitectOutputValidationError("approvalRecommendation requires decision and reason.")
    recommendation_refs = _string_list(recommendation.get("evidenceRefs"), field="approvalRecommendation.evidenceRefs")
    _validate_refs(
        refs=recommendation_refs,
        field="approvalRecommendation.evidenceRefs",
        allowed_refs=allowed_refs,
        grounding_refs=grounding_refs,
        require_grounding=True,
    )

    return {
        "verdict": verdict,
        "architectureFindings": findings,
        "risks": risks,
        "requiredChanges": required_changes,
        "approvalRecommendation": {
            **recommendation,
            "decision": recommendation_decision,
            "reason": recommendation_reason,
            "evidenceRefs": recommendation_refs,
        },
        "evidenceRefs": output_refs,
    }


def _collect_input_evidence_refs(payload: dict[str, Any]) -> set[str]:
    refs = {str(payload["diffArtifactId"])}
    for ref in payload.get("evidenceRefs") or []:
        if isinstance(ref, str) and ref:
            refs.add(ref)
    for result in payload.get("testResults") or []:
        if not isinstance(result, dict):
            continue
        for key in ("id", "evidencePackageId", "outputRef"):
            value = result.get(key)
            if isinstance(value, str) and value:
                refs.add(value)
        for key in ("evidenceRefs", "outputRefs"):
            values = result.get(key)
            if isinstance(values, list):
                refs.update(str(value) for value in values if isinstance(value, str) and value)
    for doc in payload.get("relevantDocs") or []:
        if isinstance(doc, dict) and isinstance(doc.get("id"), str) and doc.get("id"):
            refs.add(str(doc["id"]))
    return refs


def _collect_grounding_refs(payload: dict[str, Any]) -> set[str]:
    refs = {str(payload["diffArtifactId"])}
    for ref in payload.get("evidenceRefs") or []:
        if isinstance(ref, str) and ref:
            refs.add(ref)
    for result in payload.get("testResults") or []:
        if not isinstance(result, dict):
            continue
        for key in ("id", "evidencePackageId", "outputRef"):
            value = result.get(key)
            if isinstance(value, str) and value:
                refs.add(value)
        values = result.get("evidenceRefs")
        if isinstance(values, list):
            refs.update(str(value) for value in values if isinstance(value, str) and value)
    return refs


class ArchitectAgentRunner:
    def __init__(self, connection: sqlite3.Connection, *, root: Path):
        self.connection = connection
        self.root = root
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.governance = GovernanceRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)

    def status(self, *, preferred_runtime: str | None = None) -> dict[str, Any]:
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return architect_agent_readiness(statuses, preferred_runtime=preferred_runtime)

    def _runtime_by_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        if not runtime_id:
            return None
        statuses = RuntimeStatusService(self.connection).list_provider_statuses()
        return next((runtime for runtime in statuses if runtime["id"] == runtime_id), None)

    def _ensure_profile(self, runtime_id: str) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": ARCHITECT_AGENT_ID,
                "name": "AIDO Architect Agent",
                "role": "technical_lead",
                "runtimeMode": _runtime_mode(runtime_id),
                "permissionProfile": "plan",
                "allowedTools": ARCHITECT_AGENT_ALLOWED_TOOLS,
                "allowedProviders": [runtime_id] if runtime_id in ARCHITECT_AGENT_MODEL_RUNTIMES else [],
                "allowedRuntimes": [runtime_id] if runtime_id in ARCHITECT_AGENT_MODEL_RUNTIMES else [],
                "allowRemote": runtime_id == "openai_compatible",
                "allowCli": False,
                "allowApi": runtime_id in ARCHITECT_AGENT_MODEL_RUNTIMES,
                "outputSchema": architect_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("ArchitectAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("ArchitectAgent cannot execute against an archived workspace.")
        return workspace

    def _diff_artifact(self, *, project_id: str, artifact_id: str) -> dict[str, Any]:
        artifact = self.evidence.get_artifact_by_id(artifact_id)
        if artifact["projectId"] != project_id:
            raise ValueError("ArchitectAgent diff artifact does not belong to the project.")
        if artifact["kind"] not in {"git_patch", "git_diff", "diff"}:
            raise ValueError("ArchitectAgent diffArtifactId must reference a diff or patch artifact.")
        return artifact

    def _artifact_text(self, artifact_id: str | None, *, reason: str) -> str:
        if not artifact_id:
            raise ValueError(reason)
        artifact = self.evidence.get_artifact_by_id(artifact_id)
        content = Path(artifact["path"]).read_text(encoding="utf-8")
        if len(content) > MODEL_OUTPUT_LIMIT_CHARS:
            raise ValueError("ArchitectAgent artifact output exceeds the review size limit.")
        return content

    def _messages(self, *, payload: dict[str, Any], diff_text: str) -> list[dict[str, str]]:
        output_schema = architect_agent_contract()["outputSchema"]
        review_context = {
            "workflowContext": payload.get("workflowContext") or {},
            "diffArtifactId": payload["diffArtifactId"],
            "diff": _bounded_text(diff_text, PROMPT_DIFF_LIMIT_CHARS),
            "relevantDocs": _bounded_items(payload.get("relevantDocs") or []),
            "testResults": _bounded_items(payload.get("testResults") or []),
            "riskRegister": _bounded_items(payload.get("riskRegister") or []),
            "allowedEvidenceRefs": sorted(_collect_input_evidence_refs(payload)),
        }
        return [
            {
                "role": "system",
                "content": (
                    "You are ArchitectAgent. Return only valid JSON without markdown fences. "
                    "Review the supplied diff against the architecture docs, test results, and risk register. "
                    "Do not invent evidence refs. Every finding, risk, required change, and recommendation must cite "
                    "allowedEvidenceRefs that are present in the input. Never approve by assumption. "
                    "Use this JSON schema: " + json_dumps(output_schema)
                ),
            },
            {
                "role": "user",
                "content": json_dumps(redact_secrets(review_context)),
            },
        ]

    def _execute_model_runtime(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
        diff_text: str,
    ) -> dict[str, Any]:
        runtime_id = str(runtime["id"])
        model = payload.get("model")
        if runtime_id == "ollama":
            model = model or next(iter(runtime.get("models") or []), None)
        model_eval = broker.evaluate_tool_call(
            project_id=payload["projectId"],
            agent_run_id=agent_run["id"],
            agent_profile=profile,
            job_id=job["id"],
            tool_call={
                "tool": runtime_id,
                "workspaceId": workspace["id"],
                "workspacePath": workspace["path"],
                "path": workspace["path"],
                "operation": "architect_agent_model_call",
                "runtimeId": runtime_id,
                "capability": "chat",
                "input": {
                    "model": model,
                    "messages": self._messages(payload=payload, diff_text=diff_text),
                    "temperature": 0.1,
                },
                "networkRequired": runtime_id == "openai_compatible",
                "secretsRequired": False,
                "approvalGrantId": payload.get("approvalGrantId"),
                "execute": True,
                "timeoutSeconds": 900,
            },
        )
        return _execution_result_from_tool_call(model_eval["toolCall"])

    def _persist_governance(
        self,
        *,
        project_id: str,
        task_id: str,
        output: dict[str, Any],
        runtime_id: str,
        agent_run_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        risk_entries: list[dict[str, Any]] = []
        for risk in output["risks"]:
            risk_entries.append(
                self.governance.create_risk(
                    {
                        "projectId": project_id,
                        "title": risk["title"],
                        "severity": risk.get("severity", "medium"),
                        "status": "open",
                        "description": risk["description"],
                        "mitigation": risk["mitigation"],
                        "owner": ARCHITECT_AGENT_ID,
                        "evidenceRefs": risk["evidenceRefs"],
                        "metadata": {
                            "source": ARCHITECT_AGENT_ID,
                            "taskId": task_id,
                            "agentRunId": agent_run_id,
                            "runtimeId": runtime_id,
                        },
                    }
                )
            )
        decision = self.governance.create_architecture_decision(
            {
                "projectId": project_id,
                "title": f"ArchitectAgent review: {task_id}",
                "status": "proposed",
                "context": "Automated architecture review over supplied diff, docs, tests, and risk register.",
                "decision": json_dumps(
                    {
                        "verdict": output["verdict"],
                        "approvalRecommendation": output["approvalRecommendation"],
                        "requiredChanges": output["requiredChanges"],
                    }
                ),
                "consequences": output["architectureFindings"],
                "linkedRiskIds": [risk["id"] for risk in risk_entries],
                "metadata": {
                    "source": ARCHITECT_AGENT_ID,
                    "taskId": task_id,
                    "agentRunId": agent_run_id,
                    "runtimeId": runtime_id,
                    "evidenceRefs": output["evidenceRefs"],
                },
            }
        )
        return decision, risk_entries

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = str(payload["projectId"])
        task_id = str(payload.get("taskId") or "architect_agent")
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        diff_artifact = self._diff_artifact(project_id=project_id, artifact_id=str(payload["diffArtifactId"]))
        diff_text = Path(diff_artifact["path"]).read_text(encoding="utf-8")

        readiness = self.status(preferred_runtime=payload.get("preferredRuntime"))
        runtime = self._runtime_by_id(readiness.get("selectedRuntimeId")) or {
            "id": readiness.get("selectedRuntimeId") or "unresolved",
            "kind": "unknown",
            "executable": False,
            "reason": readiness["reason"],
            "capabilities": [],
        }
        runtime_id = str(runtime.get("id") or "unresolved")
        profile = self._ensure_profile(runtime_id)
        workflow_context = payload.get("workflowContext") or {}
        job_result = self.jobs.create_job(
            project_id=project_id,
            kind="agent.architect",
            status="running",
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            payload={
                "taskId": task_id,
                "workspaceId": workspace["id"],
                "diffArtifactId": diff_artifact["id"],
                "runtime": runtime,
            },
        )
        job = job_result["job"]
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=redact_secrets({**payload, "workspacePath": workspace["path"]}),
            output_payload={},
            job_id=job["id"],
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            status="running",
        )

        runtime_result = _runtime_unavailable_result(readiness["reason"])
        final_status = RUNTIME_UNAVAILABLE_STATUS
        final_reason = readiness["reason"]
        output: dict[str, Any] | None = None
        architecture_decision: dict[str, Any] | None = None
        risk_entries: list[dict[str, Any]] = []
        artifact_ids = [diff_artifact["id"]]

        if readiness["executable"]:
            broker = ToolBroker(self.connection, artifact_root=self.root)
            runtime_result = self._execute_model_runtime(
                payload=payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                diff_text=diff_text,
            )
            if runtime_result["status"] != "completed":
                final_status = "failed"
                final_reason = str(runtime_result.get("reason") or "ArchitectAgent runtime execution failed.")
            else:
                if runtime_result.get("outputArtifactId"):
                    artifact_ids.append(str(runtime_result["outputArtifactId"]))
                try:
                    raw_output = self._artifact_text(
                        runtime_result.get("outputArtifactId"),
                        reason="ArchitectAgent model execution did not produce an output artifact.",
                    )
                    parsed_output = _json_object_from_text(raw_output)
                    output = _validate_architect_output(
                        parsed_output,
                        allowed_refs=_collect_input_evidence_refs(payload),
                        grounding_refs=_collect_grounding_refs(payload),
                    )
                    architecture_decision, risk_entries = self._persist_governance(
                        project_id=project_id,
                        task_id=task_id,
                        output=output,
                        runtime_id=runtime_id,
                        agent_run_id=agent_run["id"],
                    )
                    final_status = "completed"
                    final_reason = "ArchitectAgent completed real model review and validated grounded output."
                except (ArchitectOutputValidationError, ValueError) as error:
                    final_status = FAILED_VALIDATION_STATUS
                    final_reason = str(error)

        artifact_records = artifact_records_from_ids(self.evidence, artifact_ids)
        tool_calls = [
            tool_call
            for tool_call in self.agents.list_agent_tool_calls()
            if str(tool_call.get("agentRunId")) == agent_run["id"]
        ]
        qa_verdict = (
            "architecture_reviewed"
            if final_status == "completed"
            else "blocked"
            if final_status == RUNTIME_UNAVAILABLE_STATUS
            else "failed"
        )
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=workflow_context.get("workflowRunId"),
            workflow_step_id=workflow_context.get("workflowStepId"),
            agent_id=ARCHITECT_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=runtime_id,
            task_id=task_id,
            test_plan="Execute ArchitectAgent through a configured model runtime and validate output against architecture evidence.",
            acceptance_checklist=[
                "Model runtime is executable.",
                "Diff artifact exists and belongs to the project.",
                "Model output validates against ArchitectAgent schema.",
                "Findings and risks cite input evidence.",
                "ADR and risks are persisted only after validation.",
            ],
            test_results=payload.get("testResults") or [],
            logs=[
                redact_secrets(
                    {
                        "runtime": runtime,
                        "runtimeResult": runtime_result,
                        "status": final_status,
                        "reason": final_reason,
                        "output": output,
                        "architectureDecisionId": (architecture_decision or {}).get("id"),
                        "riskIds": [risk["id"] for risk in risk_entries],
                    }
                )
            ],
            diff_refs=[
                {
                    "kind": "diff_artifact",
                    "artifactId": diff_artifact["id"],
                    "hash": diff_artifact["hash"],
                    "path": diff_artifact["path"],
                }
            ],
            risk_notes=[
                {
                    "severity": "low" if final_status == "completed" else "medium",
                    "description": final_reason,
                    "mitigation": "Configure a real model runtime or fix invalid ArchitectAgent output.",
                }
            ],
            artifact_ids=artifact_ids,
            diff_summary={"inputDiffArtifactId": diff_artifact["id"], "outputArtifactId": runtime_result.get("outputArtifactId")},
            runtime_health={
                "id": runtime_id,
                "status": runtime_result.get("status"),
                "available": bool(runtime.get("available", runtime.get("executable", False))),
                "executable": bool(runtime.get("executable", False)),
                "reason": runtime_result.get("reason") or runtime.get("reason"),
            },
            model_calls=[],
            tool_calls=tool_calls,
            policy_decisions=[],
            approvals=self.jobs.list_action_requests(job["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            evidence_source="evidence_collected",
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            self.evidence.attach_artifact_to_evidence(artifact_id=artifact_id, evidence_package_id=evidence["id"])
        contract_errors = evidence_package_contract_errors(
            evidence,
            require_runtime_links=final_status == "completed",
            require_workflow_run=bool(evidence.get("workflowRunId")),
        )
        if final_status == "completed" and contract_errors:
            final_status = FAILED_VALIDATION_STATUS
            qa_verdict = "failed"
            final_reason = "Evidence package contract is incomplete or unverifiable: " + " ".join(contract_errors)
            evidence = self.evidence.update_evidence_links(
                evidence["id"],
                qa_verdict=qa_verdict,
                risk_notes=[
                    {
                        "severity": "high",
                        "description": final_reason,
                        "mitigation": "Regenerate ArchitectAgent evidence with runtime, artifact refs, and SHA-256 hashes before completion.",
                    }
                ],
            )

        agent_run_status = "completed" if final_status == "completed" else "failed"
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status=agent_run_status,
            output_payload={
                "status": final_status,
                "verdict": (output or {}).get("verdict", final_status),
                "reason": final_reason,
                "runtime": runtime,
                "runtimeResult": runtime_result,
                "architectureDecisionId": (architecture_decision or {}).get("id"),
                "riskIds": [risk["id"] for risk in risk_entries],
                "evidence_refs": [evidence["id"], *artifact_ids],
                "output": output,
            },
        )
        job = self.jobs.update_job_status(
            job["id"],
            status="completed" if final_status == "completed" else "failed",
            metadata={
                "status": final_status,
                "reason": final_reason,
                "evidencePackageId": evidence["id"],
                "architectureDecisionId": (architecture_decision or {}).get("id"),
                "riskIds": [risk["id"] for risk in risk_entries],
            },
        )
        return {
            "status": final_status,
            "reason": final_reason,
            "architectAgent": readiness,
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "runtime": runtime,
            "runtimeResult": runtime_result,
            "output": output,
            "architectureDecision": architecture_decision,
            "riskEntries": risk_entries,
        }
