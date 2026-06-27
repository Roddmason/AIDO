"""Contratos de QA real: condiciones que un paquete debe cumplir para declararse 'passed'.

Concentra las reglas anti-falso-positivo del slice: un veredicto 'passed' solo es válido si
viene de una fuente real, sin tests fallidos, y con resultados trazados a ejecuciones de
comando reales (modo de ejecución, exitCode 0, toolCall enlazado, hashes de artefacto y una
decisión de política 'allow'). Devuelve listas de errores; no muta ni persiste evidencia.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

FAILED_TEST_STATUSES = {"blocked", "denied", "error", "failed", "timeout", "timed_out"}
REAL_QA_EVIDENCE_SOURCES = {"qa_passed_by_command", "verified_completion"}
QA_EXECUTION_MODES = {"restricted_subprocess", "docker"}
REQUIRED_EVIDENCE_PACKAGE_KEYS = {
    "workflowRunId",
    "jobId",
    "agentRunId",
    "workspaceId",
    "runtimeId",
    "runtimeHealth",
    "modelCalls",
    "toolCalls",
    "policyDecisions",
    "approvals",
    "qaVerdict",
    "artifacts",
    "diffSummary",
    "hashes",
    "createdAt",
}


def _policy_decision_allows(decision: dict[str, Any]) -> bool:
    return str(decision.get("decision") or "").strip().lower() == "allow"


def real_qa_command_errors(evidence: dict[str, Any]) -> list[str]:
    """Reúne todo lo que impide tratar la evidencia como un QA pasado por comando real.

    Exige al menos un testResult con status=passed, exitCode 0 y modo de ejecución
    sandboxizado, cada uno enlazado a un toolCall presente y a una decisión de política
    'allow', más hashes de stdout/stderr/output. La lista vacía significa evidencia válida.
    """
    errors: list[str] = []
    test_results = evidence.get("testResults") or []
    tool_calls = evidence.get("toolCalls") if isinstance(evidence.get("toolCalls"), list) else []
    policy_decisions = (
        evidence.get("policyDecisions") if isinstance(evidence.get("policyDecisions"), list) else []
    )
    hashes = evidence.get("hashes") if isinstance(evidence.get("hashes"), dict) else {}
    tool_call_ids = {str(item.get("id")) for item in tool_calls if isinstance(item, dict) and item.get("id")}
    allowed_policy_ids = {
        str(item.get("id"))
        for item in policy_decisions
        if isinstance(item, dict) and item.get("id") and _policy_decision_allows(item)
    }

    if not isinstance(test_results, list) or not test_results:
        errors.append("QA passed requires at least one command test result.")
        return errors
    if not tool_call_ids:
        errors.append("QA passed requires linked toolCalls.")
    if not hashes:
        errors.append("QA passed requires evidence package hashes.")
    if not allowed_policy_ids:
        errors.append("QA passed requires an allow policy decision.")

    for index, result in enumerate(test_results):
        if not isinstance(result, dict):
            errors.append(f"testResults[{index}] must be an object.")
            continue
        if result.get("status") != "passed":
            errors.append(f"testResults[{index}] must have status=passed.")
        if result.get("exitCode") != 0:
            errors.append(f"testResults[{index}] must have exitCode=0.")
        if result.get("execution") not in QA_EXECUTION_MODES:
            errors.append(f"testResults[{index}] must record a real command execution mode.")
        tool_call_id = str(result.get("toolCallId") or "")
        if not tool_call_id:
            errors.append(f"testResults[{index}] requires toolCallId.")
        elif tool_call_id not in tool_call_ids:
            errors.append(f"testResults[{index}] toolCallId is not linked in toolCalls.")
        artifact_hashes = (
            result.get("artifactHashes") if isinstance(result.get("artifactHashes"), dict) else {}
        )
        errors.extend(
            f"testResults[{index}] requires artifactHashes.{required_hash}."
            for required_hash in ("stdoutHash", "stderrHash", "outputArtifactHash")
            if not artifact_hashes.get(required_hash)
        )
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        policy_decision_id = str(
            metadata.get("permissionDecisionId") or metadata.get("policyDecisionId") or ""
        )
        if not policy_decision_id:
            errors.append(f"testResults[{index}] requires a policy decision reference.")
        elif allowed_policy_ids and policy_decision_id not in allowed_policy_ids:
            errors.append(f"testResults[{index}] policy decision is not an allow decision.")
    return errors


def evidence_has_real_qa_pass(evidence: dict[str, Any]) -> bool:
    """Indica si la evidencia es un 'passed' verdadero: fuente real y sin errores de contrato."""
    return (
        str(evidence.get("qaVerdict") or "").lower() == "passed"
        and str(evidence.get("evidenceSource") or "") in REAL_QA_EVIDENCE_SOURCES
        and not real_qa_command_errors(evidence)
    )


def failed_test_results(test_results: list[dict[str, Any]] | list[Any]) -> list[dict[str, Any]]:
    """Filtra los resultados cuyo status cuenta como fallo (blocked/denied/error/failed/timeout)."""
    failed: list[dict[str, Any]] = []
    for result in test_results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip().lower()
        if status in FAILED_TEST_STATUSES:
            failed.append(result)
    return failed


def qa_passed_without_failed_results(evidence: dict[str, Any]) -> bool:
    """Alias semántico de `evidence_has_real_qa_pass` usado por callers centrados en el gate de aprobación."""
    return evidence_has_real_qa_pass(evidence)


def evidence_package_contract_errors(
    evidence: dict[str, Any],
    *,
    require_runtime_links: bool = False,
    require_workflow_run: bool | None = None,
) -> list[str]:
    """Valida la forma del paquete: campos requeridos, tipos de colección y enlaces de runtime.

    Args:
        require_runtime_links: exige además los enlaces de ejecución (jobId, agentRunId,
            workspaceId, runtimeId), runtimeHealth y hashes/artefactos no vacíos.
        require_workflow_run: si es None, hereda el valor de `require_runtime_links`; cuando es
            verdadero suma `workflowRunId` a los enlaces obligatorios.

    Returns:
        Lista de mensajes de error; vacía si el paquete satisface el contrato pedido.
    """
    errors: list[str] = []
    if require_workflow_run is None:
        require_workflow_run = require_runtime_links
    missing = sorted(key for key in REQUIRED_EVIDENCE_PACKAGE_KEYS if key not in evidence)
    if missing:
        errors.append(f"Evidence package contract is missing fields: {', '.join(missing)}.")

    if require_runtime_links:
        runtime_link_keys = ["jobId", "agentRunId", "workspaceId", "runtimeId"]
        if require_workflow_run:
            runtime_link_keys.insert(0, "workflowRunId")
        errors.extend(
            f"Evidence package contract requires {key}." for key in runtime_link_keys if not evidence.get(key)
        )
        if not isinstance(evidence.get("runtimeHealth"), dict) or not evidence.get("runtimeHealth"):
            errors.append("Evidence package contract requires runtimeHealth.")

    if str(evidence.get("qaVerdict") or "").lower() == "passed" and not (evidence.get("testResults") or []):
        errors.append("Evidence package cannot pass without testResults.")

    errors.extend(
        f"Evidence package {key} must be a list."
        for key in ("modelCalls", "toolCalls", "policyDecisions", "approvals", "artifacts")
        if key in evidence and not isinstance(evidence.get(key), list)
    )

    hashes = evidence.get("hashes")
    if "hashes" in evidence and not isinstance(hashes, dict):
        errors.append("Evidence package hashes must be an object.")
    if require_runtime_links and not hashes:
        errors.append("Evidence package contract requires artifact hashes.")

    artifacts = evidence.get("artifacts") or []
    if require_runtime_links and not artifacts:
        errors.append("Evidence package contract requires artifact refs.")
    if isinstance(artifacts, list):
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, dict):
                errors.append(f"Evidence package artifacts[{index}] must be an object.")
                continue
            if not artifact.get("id"):
                errors.append(f"Evidence package artifacts[{index}] requires id.")
            if not artifact.get("kind"):
                errors.append(f"Evidence package artifacts[{index}] requires kind.")
            if not artifact.get("hash"):
                errors.append(f"Evidence package artifacts[{index}] requires hash.")

    return errors
