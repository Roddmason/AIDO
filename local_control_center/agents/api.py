"""Router HTTP de agentes: ejecuta runs por rol, perfiles, runtimes y skills con validación de entrada.

Expone los endpoints de status/run de cada agente (developer/architect/qa/devops/security/research), el CRUD de
perfiles, la creación de agent runs con brokering de tool calls, y los catálogos de runtimes y skills.
Cada payload se valida estrictamente (ids compactos, argv estructurados, límites) antes de tocar dominio.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.event_bus import EventBus

from .architect_agent import ArchitectAgentRunner
from .autonomy_profiles import AutonomyProfile, AutonomyValidationError
from .contracts import (
    AgentProfileResponse,
    AgentProfilesListResponse,
    AgentProfileUpsertRequest,
    AgentRunCreateRequest,
    AgentRunResponse,
    AgentRunsListResponse,
    ArchitectAgentRunRequest,
    ArchitectAgentRunResponse,
    ArchitectAgentStatusResponse,
    DeveloperAgentRunRequest,
    DeveloperAgentRunResponse,
    DeveloperAgentStatusResponse,
    DevOpsAgentRunRequest,
    DevOpsAgentRunResponse,
    DevOpsAgentStatusResponse,
    ProductOwnerAgentRunRequest,
    ProductOwnerAgentRunResponse,
    ProductOwnerAgentStatusResponse,
    QAAgentRunRequest,
    QAAgentRunResponse,
    ResearchAgentRunRequest,
    ResearchAgentRunResponse,
    ResearchAgentStatusResponse,
    RuntimeProviderConfigurationResponse,
    RuntimeProvidersResponse,
    SecurityAgentRunRequest,
    SecurityAgentRunResponse,
    SecurityAgentStatusResponse,
    SkillsListResponse,
    SkillsSyncRequest,
    SkillsSyncResponse,
)
from .developer_agent import DeveloperAgentRunner
from .devops_agent import DevOpsAgentRunner
from .product_owner_agent import ProductOwnerAgentRunner
from .qa_agent import QAAgentRunner
from .repository import AgentsRepository
from .research_agent import ResearchAgentRunner
from .runtime_provider_config import list_runtime_provider_configurations
from .runtime_status import RUNTIME_MODES, RuntimeStatusService
from .security_agent import SecurityAgentRunner
from .skills import SkillRegistry
from .tool_broker import ToolBroker

EXECUTION_MODES_WITH_EVIDENCE = {"restricted_subprocess", "docker", "runtime_adapter:mcp"}
ID_RE = re.compile(r"^[a-z0-9_-]{3,64}$")
TOOL_ID_RE = re.compile(r"^[a-z0-9_.:-]{2,80}$")
CATALOG_ID_RE = re.compile(r"^[a-z0-9_.:-]{2,96}$")
VALID_AGENT_ROLES = {
    "analyst",
    "product_owner",
    "technical_lead",
    "technical_lead_shadow",
    "developer",
    "backend_engineer",
    "frontend_engineer",
    "implementer",
    "devops",
    "devops_engineer",
    "qa",
    "qa_reviewer",
    "security_reviewer",
    "release_manager",
}
VALID_PERMISSION_PROFILES = {"plan", "dev_safe", "qa", "release"}
REMOTE_API_RUNTIMES = {"openai_compatible", "openrouter", "nvidia_nim", "anthropic_api"}
MODEL_AGENT_RUNTIMES = REMOTE_API_RUNTIMES | {"ollama"}
DEVELOPER_AGENT_RUNTIMES = {"codex_cli", "claude_code_cli"} | MODEL_AGENT_RUNTIMES
ARCHITECT_AGENT_RUNTIMES = MODEL_AGENT_RUNTIMES
SECURITY_AGENT_RUNTIMES = MODEL_AGENT_RUNTIMES
PRODUCT_OWNER_AGENT_RUNTIMES = {
    "codex_cli",
    "claude_code_cli",
    "ollama",
    "openai_compatible",
    "openrouter",
    "nvidia_nim",
    "anthropic_api",
}


def _require_id(value: Any, *, label: str) -> str:
    candidate = str(value or "")
    if not ID_RE.match(candidate):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must use lowercase letters, numbers, dashes or underscores.",
        )
    return candidate


def validate_agent_profile_body(body: dict[str, Any]) -> dict[str, Any]:
    """Valida un perfil de agente contra los catálogos permitidos y los rangos numéricos.

    Raises:
        HTTPException: 422 si rol/runtime/perfil no están catalogados, los ids no son compactos o
            los límites numéricos quedan fuera de rango.
    """
    _require_id(body.get("id"), label="Agent profile id")
    role = str(body.get("role") or "implementer")
    runtime_mode = str(body.get("runtimeMode") or body.get("runtimeType") or "hybrid")
    permission_profile = str(body.get("permissionProfile") or "plan")
    allowed_tools = body.get("allowedTools") or []
    allowed_providers = body.get("allowedProviders") or []
    allowed_runtimes = body.get("allowedRuntimes") or []
    if role not in VALID_AGENT_ROLES:
        raise HTTPException(status_code=422, detail="Agent role is not in the allowed catalog.")
    if runtime_mode not in RUNTIME_MODES:
        raise HTTPException(status_code=422, detail="Runtime mode is not in the allowed catalog.")
    if permission_profile not in VALID_PERMISSION_PROFILES:
        raise HTTPException(status_code=422, detail="Permission profile is not in the allowed catalog.")
    if not isinstance(allowed_tools, list) or not all(
        isinstance(item, str) and TOOL_ID_RE.match(item) for item in allowed_tools
    ):
        raise HTTPException(status_code=422, detail="Allowed tools must be catalog ids, not free-form JSON.")
    if not isinstance(allowed_providers, list) or not all(
        isinstance(item, str) and CATALOG_ID_RE.match(item) for item in allowed_providers
    ):
        raise HTTPException(status_code=422, detail="Allowed providers must be compact catalog ids.")
    if not isinstance(allowed_runtimes, list) or not all(
        isinstance(item, str) and CATALOG_ID_RE.match(item) for item in allowed_runtimes
    ):
        raise HTTPException(status_code=422, detail="Allowed runtimes must be compact catalog ids.")
    for field in ("routingProfileId", "roleModelPolicyId"):
        if body.get(field) and not CATALOG_ID_RE.match(str(body[field])):
            raise HTTPException(status_code=422, detail=f"{field} must be a compact catalog id.")
    try:
        max_tokens = int(body.get("maxTokensPerRun") or 0)
        approval_threshold = body.get("requiresApprovalOverUsd")
        approval_value = None if approval_threshold in {None, ""} else float(approval_threshold)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Agent routing numeric fields are invalid.") from error
    if max_tokens < 0 or max_tokens > 200000:
        raise HTTPException(status_code=422, detail="maxTokensPerRun must be between 0 and 200000.")
    if approval_value is not None and approval_value < 0:
        raise HTTPException(status_code=422, detail="requiresApprovalOverUsd must be zero or positive.")
    for field in ("allowRemote", "allowCli", "allowApi"):
        if field in body and not isinstance(body[field], bool):
            raise HTTPException(status_code=422, detail=f"{field} must be a boolean.")
    return body


def validate_developer_agent_run_body(body: DeveloperAgentRunRequest) -> dict[str, Any]:
    """Valida un run de DeveloperAgent (instrucción, runtime permitido, qaCommands, costo).

    Raises:
        HTTPException: 422 si falta la instrucción, excede el largo, el runtime no es permitido,
            los qaCommands no son argv válidos o maxCostUsd es negativo.
    """
    payload = body.model_dump(by_alias=True)
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="DeveloperAgent instruction is required.")
    if len(instruction) > 20000:
        raise HTTPException(
            status_code=422, detail="DeveloperAgent instruction must be 20000 characters or fewer."
        )
    preferred_runtime = payload.get("preferredRuntime")
    if preferred_runtime and preferred_runtime not in DEVELOPER_AGENT_RUNTIMES:
        raise HTTPException(
            status_code=422, detail=f"DeveloperAgent runtime is not allowed: {preferred_runtime}"
        )
    qa_commands = payload.get("qaCommands") or []
    for index, argv in enumerate(qa_commands):
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(
                status_code=422, detail=f"qaCommands[{index}] must be a non-empty structured argv list."
            )
    max_cost = payload.get("maxCostUsd")
    if max_cost is not None and float(max_cost) < 0:
        raise HTTPException(status_code=422, detail="maxCostUsd must be zero or positive.")
    return payload


def validate_qa_agent_run_body(body: QAAgentRunRequest) -> dict[str, Any]:
    """Valida un run de QAAgent: taskId y comandos con argv estructurado y timeout acotado.

    Raises:
        HTTPException: 422 si falta taskId, un comando usa string en vez de argv o el timeout no
            está entre 1 y 300 segundos.
    """
    payload = body.model_dump(by_alias=True)
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="QAAgent taskId is required.")
    commands = payload.get("commands") or []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise HTTPException(
                status_code=422, detail=f"commands[{index}] must be an object with structured argv."
            )
        if isinstance(command.get("command"), str):
            raise HTTPException(status_code=422, detail=f"commands[{index}] must not use a command string.")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(
                status_code=422, detail=f"commands[{index}].argv must be a non-empty structured argv list."
            )
        timeout = command.get("timeoutSeconds")
        if timeout is not None:
            try:
                timeout_int = int(timeout)
            except (TypeError, ValueError) as error:
                raise HTTPException(
                    status_code=422, detail=f"commands[{index}].timeoutSeconds must be an integer."
                ) from error
            if timeout_int < 1 or timeout_int > 300:
                raise HTTPException(
                    status_code=422, detail=f"commands[{index}].timeoutSeconds must be between 1 and 300."
                )
    return payload


def validate_devops_agent_run_body(body: DevOpsAgentRunRequest) -> dict[str, Any]:
    """Valida un run de DevOpsAgent: taskId y scripts de build/quality como nombres compactos acotados.

    Raises:
        HTTPException: 422 si falta taskId, hay más de 20 scripts o alguno no es un nombre compacto.
    """
    payload = body.model_dump(by_alias=True)
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="DevOpsAgent taskId is required.")
    build_scripts = payload.get("buildScripts") or []
    if len(build_scripts) > 20:
        raise HTTPException(status_code=422, detail="DevOpsAgent accepts at most 20 build scripts.")
    if not all(isinstance(script, str) and CATALOG_ID_RE.match(script) for script in build_scripts):
        raise HTTPException(status_code=422, detail="DevOpsAgent buildScripts must be compact script names.")
    quality_scripts = payload.get("qualityScripts")
    if quality_scripts is not None:
        if len(quality_scripts) > 20:
            raise HTTPException(status_code=422, detail="DevOpsAgent accepts at most 20 quality scripts.")
        if not all(isinstance(script, str) and CATALOG_ID_RE.match(script) for script in quality_scripts):
            raise HTTPException(
                status_code=422, detail="DevOpsAgent qualityScripts must be compact script names."
            )
    return payload


def validate_security_agent_run_body(body: SecurityAgentRunRequest) -> dict[str, Any]:
    """Valida un run de SecurityAgent: taskId, escáneres candidatos, rutas y runtime permitido.

    Raises:
        HTTPException: 422 si falta taskId, se exceden los límites de candidatos/rutas, un comando
            usa string en vez de argv o el runtime no es permitido.
    """
    payload = body.model_dump(by_alias=True)
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="SecurityAgent taskId is required.")
    command_candidates = payload.get("commandCandidates") or []
    if len(command_candidates) > 50:
        raise HTTPException(status_code=422, detail="SecurityAgent accepts at most 50 command candidates.")
    for index, command in enumerate(command_candidates):
        if not isinstance(command, dict):
            raise HTTPException(
                status_code=422, detail=f"commandCandidates[{index}] must be an object with structured argv."
            )
        if isinstance(command.get("command"), str):
            raise HTTPException(
                status_code=422, detail=f"commandCandidates[{index}] must not use a command string."
            )
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise HTTPException(
                status_code=422,
                detail=f"commandCandidates[{index}].argv must be a non-empty structured argv list.",
            )
    paths_to_check = payload.get("pathsToCheck") or []
    if len(paths_to_check) > 100:
        raise HTTPException(status_code=422, detail="SecurityAgent accepts at most 100 path candidates.")
    if not all(isinstance(item, str) and item.strip() for item in paths_to_check):
        raise HTTPException(
            status_code=422, detail="SecurityAgent pathsToCheck must be non-empty path strings."
        )
    preferred_runtime = payload.get("preferredRuntime")
    if preferred_runtime and preferred_runtime not in SECURITY_AGENT_RUNTIMES:
        raise HTTPException(
            status_code=422, detail=f"SecurityAgent runtime is not allowed: {preferred_runtime}"
        )
    return payload


def validate_research_agent_run_body(body: ResearchAgentRunRequest) -> dict[str, Any]:
    """Valida un run de ResearchAgent: fuentes, conclusiones y claims acotados y referenciables.

    Raises:
        HTTPException: 422 si falta taskId, no hay fuentes, se exceden los límites o claims/citas no
            tienen strings válidos.
    """
    payload = body.model_dump(by_alias=True)
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="ResearchAgent taskId is required.")
    sources = payload.get("sources") or []
    if not sources:
        raise HTTPException(status_code=422, detail="ResearchAgent requires at least one source.")
    if len(sources) > 50:
        raise HTTPException(status_code=422, detail="ResearchAgent accepts at most 50 sources.")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise HTTPException(status_code=422, detail=f"sources[{index}] must be an object.")
        if not str(source.get("url") or "").strip():
            raise HTTPException(status_code=422, detail=f"sources[{index}].url is required.")
        if not str(source.get("publisher") or "").strip():
            raise HTTPException(status_code=422, detail=f"sources[{index}].publisher is required.")
        content = source.get("content")
        if content is not None and len(str(content).encode("utf-8")) > 1_000_000:
            raise HTTPException(status_code=422, detail=f"sources[{index}].content exceeds 1000000 bytes.")
    conclusions = payload.get("conclusions") or []
    if len(conclusions) > 100:
        raise HTTPException(status_code=422, detail="ResearchAgent accepts at most 100 conclusions.")
    for index, conclusion in enumerate(conclusions):
        if not isinstance(conclusion, dict):
            raise HTTPException(status_code=422, detail=f"conclusions[{index}] must be an object.")
        if not str(conclusion.get("statement") or "").strip():
            raise HTTPException(status_code=422, detail=f"conclusions[{index}].statement is required.")
        citations = conclusion.get("citations") or []
        if not isinstance(citations, list) or not all(isinstance(item, str) and item for item in citations):
            raise HTTPException(
                status_code=422, detail=f"conclusions[{index}].citations must be a string list."
            )
    claims = payload.get("claims") or []
    if len(claims) > 100:
        raise HTTPException(status_code=422, detail="ResearchAgent accepts at most 100 claims.")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise HTTPException(status_code=422, detail=f"claims[{index}] must be an object.")
        for field in ("topic", "value", "sourceUrl"):
            if not str(claim.get(field) or "").strip():
                raise HTTPException(status_code=422, detail=f"claims[{index}].{field} is required.")
    return payload


def validate_architect_agent_run_body(body: ArchitectAgentRunRequest) -> dict[str, Any]:
    """Valida un run de ArchitectAgent: taskId, diff, contexto de workflow, runtime y listas de soporte.

    Raises:
        HTTPException: 422 si falta taskId/diffArtifactId, el workflowContext no es objeto, el runtime
            no es permitido o las listas de docs/tests/riesgos/evidencia no cumplen forma o tamaño.
    """
    payload = body.model_dump(by_alias=True)
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="ArchitectAgent taskId is required.")
    if not str(payload.get("diffArtifactId") or "").strip():
        raise HTTPException(status_code=422, detail="ArchitectAgent diffArtifactId is required.")
    workflow_context = payload.get("workflowContext")
    if not isinstance(workflow_context, dict):
        raise HTTPException(status_code=422, detail="ArchitectAgent workflowContext must be an object.")
    preferred_runtime = payload.get("preferredRuntime")
    if preferred_runtime and preferred_runtime not in ARCHITECT_AGENT_RUNTIMES:
        raise HTTPException(
            status_code=422, detail=f"ArchitectAgent runtime is not allowed: {preferred_runtime}"
        )
    for field in ("relevantDocs", "testResults", "riskRegister"):
        value = payload.get(field) or []
        if (
            not isinstance(value, list)
            or len(value) > 50
            or not all(isinstance(item, dict) for item in value)
        ):
            raise HTTPException(
                status_code=422,
                detail=f"ArchitectAgent {field} must be a list of objects with at most 50 items.",
            )
    evidence_refs = payload.get("evidenceRefs") or []
    if not isinstance(evidence_refs, list) or not all(
        isinstance(item, str) and item for item in evidence_refs
    ):
        raise HTTPException(status_code=422, detail="ArchitectAgent evidenceRefs must be a string list.")
    return payload


def _execution_test_result(tool_call: dict[str, Any]) -> dict[str, Any]:
    payload = tool_call.get("payload") or {}
    execution_result = payload.get("executionResult") or {}
    tool_status = str(tool_call.get("status") or "")
    runtime_status = str(execution_result.get("status") or "")
    evidence_status = (
        "skipped_with_reason"
        if tool_status in {"configuration_required", "unavailable"}
        or runtime_status in {"configuration_required", "unavailable"}
        else tool_status
    )
    output_refs = [
        artifact_id
        for artifact_id in (
            execution_result.get("stdoutArtifactId"),
            execution_result.get("stderrArtifactId"),
        )
        if artifact_id
    ]
    return {
        "command": payload.get("command") or execution_result.get("operation") or tool_call.get("toolName"),
        "status": evidence_status,
        "execution": payload.get("execution"),
        "returnCode": execution_result.get("returnCode"),
        "timedOut": bool(execution_result.get("timedOut", False)),
        "durationMs": execution_result.get("durationMs"),
        "blocked": bool(execution_result.get("blocked", False)),
        "reason": execution_result.get("reason"),
        "toolCallId": tool_call.get("id"),
        "outputRef": output_refs[0] if output_refs else None,
        "outputRefs": output_refs,
        "metadata": {"toolCallStatus": tool_status, "runtimeStatus": runtime_status},
    }


def _execution_artifact_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    artifact_ids: list[str] = []
    for tool_call in tool_calls:
        payload = tool_call.get("payload") or {}
        execution_result = payload.get("executionResult") or {}
        for key in ("stdoutArtifactId", "stderrArtifactId"):
            artifact_id = execution_result.get(key)
            if isinstance(artifact_id, str) and artifact_id:
                artifact_ids.append(artifact_id)
    return artifact_ids


def _input_evidence_refs(input_payload: dict[str, Any]) -> list[str]:
    for key in ("evidenceRefs", "evidence_refs", "evidencePackageIds"):
        refs = input_payload.get(key)
        if isinstance(refs, list):
            return [str(ref) for ref in refs if isinstance(ref, str) and ref]
    return []


def _requested_skill_refs(input_payload: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("skillIds", "skills", "skillRefs"):
        value = input_payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                refs.append(item.strip())
            elif isinstance(item, dict):
                ref = item.get("id") or item.get("name") or item.get("skillId")
                if isinstance(ref, str) and ref.strip():
                    refs.append(ref.strip())
    return list(dict.fromkeys(refs))


def _blocked_skill_resolution_output(
    *, profile: dict[str, Any], task_id: str, reason: str
) -> dict[str, Any]:
    return {
        "agent_id": profile["id"],
        "task_id": task_id,
        "verdict": "blocked",
        "summary": reason,
        "evidence_refs": [],
        "skills": [],
        "risks": [
            {
                "severity": "high",
                "description": "Agent run requested a skill that cannot be bound to an immutable catalog version.",
                "mitigation": "Sync the skill catalog and add the skill id or name to the agent profile allowedSkills list.",
            }
        ],
        "next_actions": ["Resolve skill configuration before executing tool calls."],
    }


def _is_technical_review_run(*, profile: dict[str, Any], task_id: str, input_payload: dict[str, Any]) -> bool:
    markers = {
        str(task_id),
        str(input_payload.get("stage") or ""),
        str(input_payload.get("workflowStepName") or ""),
        str(input_payload.get("workflowStep") or ""),
    }
    return profile.get("role") == "technical_lead" and any("technical_review" in marker for marker in markers)


def _blocked_technical_review_output(*, profile: dict[str, Any], task_id: str) -> dict[str, Any]:
    return {
        "agent_id": profile["id"],
        "task_id": task_id,
        "verdict": "blocked",
        "summary": "Technical review requires at least one evidence package reference.",
        "evidence_refs": [],
        "risks": [
            {
                "severity": "high",
                "description": "Technical review attempted without evidence package references.",
                "mitigation": "Attach QA evidence package IDs before issuing a technical review verdict.",
            }
        ],
        "next_actions": ["Attach evidenceRefs to the agent run input."],
    }


def _create_execution_evidence(
    *,
    platform: Any,
    project_id: str,
    workflow_run_id: str | None,
    agent_id: str,
    task_id: str,
    tool_calls: list[dict[str, Any]],
    skills: list[dict[str, Any]] | None = None,
) -> list[str]:
    executed_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if (tool_call.get("payload") or {}).get("execution") in EXECUTION_MODES_WITH_EVIDENCE
    ]
    if not executed_tool_calls:
        return []

    test_results = [_execution_test_result(tool_call) for tool_call in executed_tool_calls]
    failed = any(
        result["status"] in {"denied", "failed", "blocked", "skipped_with_reason", "error", "timed_out"}
        or result["returnCode"] not in {0, None}
        for result in test_results
    )
    skill_refs = [
        {
            "kind": "skill_instruction",
            "skillId": skill["id"],
            "name": skill["name"],
            "version": skill.get("version"),
            "instructionsHash": skill.get("instructionsHash"),
            "path": skill.get("path"),
            "riskLevel": skill.get("riskLevel"),
        }
        for skill in skills or []
    ]
    skill_hashes = {
        f"skill:{skill['id']}": str(skill["instructionsHash"])
        for skill in skills or []
        if skill.get("instructionsHash")
    }
    evidence_repo = EvidenceRepository(platform.connection)
    evidence = evidence_repo.create_evidence_package(
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        agent_id=agent_id,
        task_id=task_id,
        test_plan="Capture policy-gated agent tool-call execution.",
        test_results=test_results,
        risk_notes=[
            {
                "severity": "low",
                "description": "Tool execution was mediated by policy and sandbox adapters.",
                "mitigation": "Keep command stdout/stderr redacted and attach larger logs as artifacts in a later hardening pass.",
            }
        ],
        artifacts=skill_refs,
        hashes=skill_hashes,
        evidence_source="evidence_collected",
        qa_verdict="failed" if failed else "evidence_collected",
    )
    for artifact_id in _execution_artifact_ids(executed_tool_calls):
        evidence_repo.attach_artifact_to_evidence(
            artifact_id=artifact_id,
            evidence_package_id=evidence["id"],
        )
    EventBus(platform.connection).record_event(
        project_id=project_id,
        event_type="qa.evidence.created",
        payload={
            "evidencePackageId": evidence["id"],
            "source": "agent_tool_execution",
            "workflowRunId": workflow_run_id,
        },
    )
    return [evidence["id"]]


def validate_product_owner_agent_run_body(body: ProductOwnerAgentRunRequest) -> dict[str, Any]:
    """Valida un run de ProductOwnerAgent: taskId, idea o initiativeId, runtime y umbral de completitud.

    Raises:
        HTTPException: 422 si falta taskId, no se entrega idea ni initiativeId, el runtime no es
            permitido o el umbral de completitud está fuera de [0, 100].
    """
    payload = body.model_dump(by_alias=True)
    if not str(payload.get("taskId") or "").strip():
        raise HTTPException(status_code=422, detail="ProductOwnerAgent taskId is required.")
    idea = str(payload.get("idea") or "").strip()
    initiative_id = str(payload.get("initiativeId") or "").strip()
    if not idea and not initiative_id:
        raise HTTPException(status_code=422, detail="ProductOwnerAgent requires an idea or an initiativeId.")
    preferred_runtime = payload.get("preferredRuntime")
    if preferred_runtime and preferred_runtime not in PRODUCT_OWNER_AGENT_RUNTIMES:
        raise HTTPException(
            status_code=422, detail=f"ProductOwnerAgent runtime is not allowed: {preferred_runtime}"
        )
    threshold = payload.get("completenessThreshold")
    if threshold is not None and (not isinstance(threshold, int | float) or not 0 <= threshold <= 100):
        raise HTTPException(
            status_code=422, detail="ProductOwnerAgent completenessThreshold must be between 0 and 100."
        )
    autonomy = payload.get("autonomy")
    if autonomy is not None:
        try:
            AutonomyProfile.from_dict(autonomy)
        except AutonomyValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return payload


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye el APIRouter de agentes, cableado a la conexión/cwd del platform y al guard de escritura."""
    router = APIRouter()

    def repository() -> AgentsRepository:
        return AgentsRepository(platform.connection)

    def skill_registry() -> SkillRegistry:
        return SkillRegistry(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    @router.get("/api/v1/agents/devops/status", response_model=DevOpsAgentStatusResponse)
    async def devops_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del DevOpsAgent."""
        return {"devopsAgent": DevOpsAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/devops/runs", status_code=202, response_model=DevOpsAgentRunResponse)
    async def run_devops_agent(body: DevOpsAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el DevOpsAgent sobre un workspace y emite el evento del veredicto."""
        require_write(request)
        payload = validate_devops_agent_run_body(body)
        try:
            result = DevOpsAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workspace"]["projectId"],
            event_type=f"agent.devops.{result['verdict']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "configArtifactId": result["configArtifact"]["id"],
            },
        )
        return result

    @router.get("/api/v1/agents/security/status", response_model=SecurityAgentStatusResponse)
    async def security_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del SecurityAgent."""
        return {"securityAgent": SecurityAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/security/runs", status_code=202, response_model=SecurityAgentRunResponse)
    async def run_security_agent(body: SecurityAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el SecurityAgent sobre un workspace y emite el evento del veredicto."""
        require_write(request)
        payload = validate_security_agent_run_body(body)
        try:
            result = SecurityAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workspace"]["projectId"],
            event_type=f"agent.security.{result['verdict']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "findingsArtifactId": result["findingsArtifact"]["id"],
            },
        )
        return result

    @router.get("/api/v1/agents/research/status", response_model=ResearchAgentStatusResponse)
    async def research_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del ResearchAgent."""
        return {"researchAgent": ResearchAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/research/runs", status_code=202, response_model=ResearchAgentRunResponse)
    async def run_research_agent(body: ResearchAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el ResearchAgent y emite un evento con el resultado de política de fuentes."""
        require_write(request)
        payload = validate_research_agent_run_body(body)
        try:
            result = ResearchAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workspace"]["projectId"],
            event_type=f"agent.research.{result['status']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "reportArtifactId": result["reportArtifact"]["id"],
                "sourceArtifactIds": [source["artifactId"] for source in result["sources"]],
            },
        )
        return result

    @router.post("/api/v1/agents/qa/runs", status_code=202, response_model=QAAgentRunResponse)
    async def run_qa_agent(body: QAAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el QAAgent sobre un workspace y emite el evento del veredicto."""
        require_write(request)
        payload = validate_qa_agent_run_body(body)
        try:
            result = QAAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["workspace"]["projectId"],
            event_type=f"agent.qa.{result['verdict']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
            },
        )
        return result

    @router.get("/api/v1/agents/developer/status", response_model=DeveloperAgentStatusResponse)
    async def developer_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del DeveloperAgent."""
        return {"developerAgent": DeveloperAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/developer/runs", status_code=202, response_model=DeveloperAgentRunResponse)
    async def run_developer_agent(body: DeveloperAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el DeveloperAgent sobre un workspace y emite el evento del estado resultante."""
        require_write(request)
        payload = validate_developer_agent_run_body(body)
        try:
            result = DeveloperAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["agentRun"]["projectId"],
            event_type=f"agent.developer.{result['status']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
            },
        )
        return result

    @router.get("/api/v1/agents/architect/status", response_model=ArchitectAgentStatusResponse)
    async def architect_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del ArchitectAgent."""
        return {"architectAgent": ArchitectAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post("/api/v1/agents/architect/runs", status_code=202, response_model=ArchitectAgentRunResponse)
    async def run_architect_agent(body: ArchitectAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el ArchitectAgent sobre un workspace y emite el evento del estado resultante."""
        require_write(request)
        payload = validate_architect_agent_run_body(body)
        try:
            result = ArchitectAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["agentRun"]["projectId"],
            event_type=f"agent.architect.{result['status']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "architectureDecisionId": (result.get("architectureDecision") or {}).get("id"),
                "riskIds": [risk["id"] for risk in result.get("riskEntries") or []],
            },
        )
        return result

    @router.get("/api/v1/agents/product-owner/status", response_model=ProductOwnerAgentStatusResponse)
    async def product_owner_agent_status() -> dict[str, Any]:
        """Devuelve el readiness del ProductOwnerAgent."""
        return {"productOwnerAgent": ProductOwnerAgentRunner(platform.connection, root=platform.cwd).status()}

    @router.post(
        "/api/v1/agents/product-owner/runs",
        status_code=202,
        response_model=ProductOwnerAgentRunResponse,
    )
    async def run_product_owner_agent(body: ProductOwnerAgentRunRequest, request: Request) -> dict[str, Any]:
        """Ejecuta el ProductOwnerAgent sobre una idea o assessment y emite el evento del estado resultante."""
        require_write(request)
        payload = validate_product_owner_agent_run_body(body)
        try:
            result = ProductOwnerAgentRunner(platform.connection, root=platform.cwd).run(payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        event_bus().record_event(
            project_id=result["agentRun"]["projectId"],
            event_type=f"agent.product_owner.{result['status']}",
            payload={
                "agentRunId": result["agentRun"]["id"],
                "workspaceId": result["workspace"]["id"],
                "runtimeId": result["runtime"]["id"],
                "evidencePackageId": result["evidencePackage"]["id"],
                "initiativeId": (result.get("initiative") or {}).get("id"),
            },
        )
        return result

    @router.get("/api/v1/agent-profiles", response_model=AgentProfilesListResponse)
    async def list_agent_profiles() -> dict[str, Any]:
        """Lista todos los perfiles de agente."""
        return {"agentProfiles": repository().list_agent_profiles()}

    @router.post("/api/v1/agent-profiles", status_code=201, response_model=AgentProfileResponse)
    async def upsert_agent_profile(body: AgentProfileUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza un perfil de agente tras validarlo contra los catálogos."""
        require_write(request)
        payload = validate_agent_profile_body(body.model_dump(by_alias=True))
        profile = repository().upsert_agent_profile(payload)
        event_bus().record_event(
            event_type="agent.profile.upserted", payload={"agentProfileId": profile["id"]}
        )
        return {"agentProfile": profile}

    @router.get("/api/v1/agent-runs", response_model=AgentRunsListResponse)
    async def list_agent_runs() -> dict[str, Any]:
        """Lista todos los runs de agente registrados."""
        return {"agentRuns": repository().list_agent_runs()}

    @router.post("/api/v1/agent-runs", status_code=202, response_model=AgentRunResponse)
    async def create_agent_run(body: AgentRunCreateRequest, request: Request) -> AgentRunResponse:
        """Crea un agent run genérico, brokerea sus tool calls y deriva estado/veredicto y evidencia.

        Bloquea runtimes fuera del catálogo y revisiones técnicas sin evidencia; cuando hay tool calls,
        las media el ToolBroker y el resultado se reduce a un estado/veredicto coherente con los permisos.
        """
        require_write(request)
        payload = body.model_dump(by_alias=True)
        repo = repository()
        profile = repo.get_agent_profile(payload["agentProfileId"])
        task_id = payload.get("taskId", "task")
        input_payload = payload.get("input") or {}
        tool_calls = input_payload.get("toolCalls") or []
        requested_skill_refs = _requested_skill_refs(input_payload)
        resolved_skills, skill_error = skill_registry().resolve_for_agent_run(
            requested_refs=requested_skill_refs,
            allowed_refs=profile.get("allowedSkills") or [],
        )
        if skill_error:
            output = _blocked_skill_resolution_output(
                profile=profile,
                task_id=task_id,
                reason=skill_error,
            )
            status = "failed"
            tool_calls = []
        elif profile["runtimeMode"] not in RUNTIME_MODES:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "blocked",
                "summary": f"Runtime {profile['runtimeMode']} is not a supported product runtime.",
                "evidence_refs": [],
                "skills": resolved_skills,
                "risks": [
                    {
                        "severity": "high",
                        "description": "Agent profile references a runtime outside the product catalog.",
                        "mitigation": "Update the profile to a configured product runtime before running it.",
                    }
                ],
                "next_actions": [],
            }
            status = "failed"
        elif tool_calls:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "evaluating_tools",
                "summary": f"Runtime {profile['runtimeMode']} requested policy-gated tool calls.",
                "evidence_refs": [],
                "skills": resolved_skills,
                "risks": [],
                "next_actions": [],
            }
            status = "running"
        elif _is_technical_review_run(
            profile=profile, task_id=task_id, input_payload=input_payload
        ) and not _input_evidence_refs(input_payload):
            output = _blocked_technical_review_output(profile=profile, task_id=task_id)
            status = "failed"
        else:
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": "blocked",
                "summary": f"Runtime {profile['runtimeMode']} has no executable adapter configured for this run.",
                "evidence_refs": [],
                "skills": resolved_skills,
                "risks": [
                    {
                        "severity": "medium",
                        "description": "Runtime adapter unavailable.",
                        "mitigation": "Route tool calls through the broker or install the adapter explicitly.",
                    }
                ],
                "next_actions": [],
            }
            status = "failed"
        run = repo.create_agent_run(
            project_id=payload["projectId"],
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=input_payload,
            output_payload=output,
            job_id=payload.get("jobId"),
            workflow_run_id=payload.get("workflowRunId"),
            workflow_step_id=payload.get("workflowStepId"),
            status=status,
        )
        if resolved_skills:
            skill_registry().bind_skills_for_run(
                skills=resolved_skills,
                agent_profile_id=profile["id"],
                workflow_id=payload.get("workflowRunId"),
                agent_run_id=run["id"],
                task_id=task_id,
            )
        if tool_calls:
            broker_results = ToolBroker(platform.connection, artifact_root=platform.cwd).evaluate_tool_calls(
                project_id=payload["projectId"],
                agent_run_id=run["id"],
                agent_profile=profile,
                tool_calls=tool_calls,
                job_id=payload.get("jobId"),
            )
            decisions = [result["decision"]["decision"] for result in broker_results]
            tool_statuses = [result["toolCall"]["status"] for result in broker_results]
            tool_call_records = [result["toolCall"] for result in broker_results]
            executed = any(
                result["toolCall"]["payload"].get("execution") in EXECUTION_MODES_WITH_EVIDENCE
                for result in broker_results
            )
            evidence_refs = _create_execution_evidence(
                platform=platform,
                project_id=payload["projectId"],
                workflow_run_id=payload.get("workflowRunId"),
                agent_id=profile["id"],
                task_id=task_id,
                tool_calls=tool_call_records,
                skills=resolved_skills,
            )
            if any(decision == "deny" for decision in decisions) or any(
                tool_status in {"denied", "failed", "blocked"} for tool_status in tool_statuses
            ):
                status = "failed"
                verdict = "blocked"
                summary = "At least one tool call was denied by policy or failed sandbox execution."
            elif any(
                tool_status in {"configuration_required", "unavailable"} for tool_status in tool_statuses
            ):
                status = "runtime_unavailable"
                verdict = "blocked"
                summary = "At least one runtime adapter is unavailable or missing required configuration."
            elif any(decision in {"requires_approval", "requires_human"} for decision in decisions):
                status = "awaiting_permission"
                verdict = "awaiting_permission"
                summary = "Tool calls are waiting for granular approval."
            elif executed:
                status = "completed"
                verdict = "approved_with_risks"
                summary = "Tool calls were executed through sandboxed adapters."
            else:
                status = "failed"
                verdict = "blocked"
                summary = "Tool calls were policy-allowed but not executed, so no completion evidence exists."
            output = {
                "agent_id": profile["id"],
                "task_id": task_id,
                "verdict": verdict,
                "summary": summary,
                "evidence_refs": evidence_refs,
                "skills": resolved_skills,
                "risks": [
                    {
                        "severity": "low",
                        "description": "Broker records decisions before execution.",
                        "mitigation": "Execute only through sandboxed adapters after evidence capture.",
                    }
                ],
                "next_actions": [],
                "tool_calls": tool_call_records,
            }
            run = repo.update_agent_run_status(run["id"], status=status, output_payload=output)
            for result in broker_results:
                decision = result["decision"]
                event_type = {
                    "allow": "tool.call.allowed",
                    "deny": "tool.call.denied",
                    "requires_approval": "approval.created",
                    "requires_human": "approval.created",
                }.get(decision["decision"], "tool.call.requested")
                event_bus().record_event(
                    project_id=payload["projectId"],
                    event_type=event_type,
                    payload={
                        "agentRunId": run["id"],
                        "toolCallId": result["toolCall"]["id"],
                        "permissionDecisionId": decision["id"],
                        "actionRequestId": result["actionRequest"]["id"] if result["actionRequest"] else None,
                    },
                )
        event_bus().record_event(
            project_id=payload["projectId"],
            event_type=f"agent.run.{status}",
            payload={"agentRunId": run["id"], "agentProfileId": profile["id"]},
        )
        return AgentRunResponse(agentRun=run)

    @router.get("/api/v1/runtime/providers", response_model=RuntimeProvidersResponse)
    async def list_runtime_providers() -> dict[str, Any]:
        """Devuelve el estado agregado de los runtime providers."""
        return RuntimeStatusService(platform.connection).runtime_provider_status()

    @router.get("/api/v1/runtime/provider-configuration", response_model=RuntimeProviderConfigurationResponse)
    async def list_runtime_provider_configuration() -> dict[str, Any]:
        """Devuelve el estado de configuración (qué falta) de cada runtime provider."""
        return {"providers": list_runtime_provider_configurations()}

    @router.get("/api/v1/skills", response_model=SkillsListResponse)
    async def list_skills() -> dict[str, Any]:
        """Lista las skills catalogadas."""
        return {"skills": skill_registry().list_skills()}

    @router.post("/api/v1/skills/sync", status_code=202, response_model=SkillsSyncResponse)
    async def sync_skills(body: SkillsSyncRequest, request: Request) -> SkillsSyncResponse:
        """Sincroniza el catálogo de skills desde el path indicado y emite el evento de sincronización."""
        require_write(request)
        count = skill_registry().sync(body.skills_path)
        event_bus().record_event(event_type="skills.synced", payload={"synced": count})
        return SkillsSyncResponse(synced=count, skills=skill_registry().list_skills())

    return router
