"""Helpers sin estado para construir, enlazar y validar artefactos de evidencia de issue_to_patch.

Reune las funciones puras del flujo issue_to_patch/issue_to_pr que escriben artefactos de
evidencia (texto, JSON, estado git), resumen diffs, deduplican registros y revalidan el parche,
los hallazgos de seguridad y el veredicto QA antes de una aprobacion o promocion. Las validaciones
lanzan ValueError cuando la evidencia falta, esta vacia o su hash no coincide: es el invariante que
impide aprobar o promover un parche sin evidencia verificable. Reexportadas por issue_to_patch_runner.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from local_control_center.agents.qa_agent import qa_verdict_allows_completion
from local_control_center.evidence.artifacts import write_text_artifact
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.workspaces_projects.cleanup import capture_workspace_snapshot


def _diff_summary(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "changedFiles": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
    }


def _evidence_git_diff_ref(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": diff.get("kind", "git_diff"),
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "statusRaw": diff.get("statusRaw", ""),
        "status": diff.get("status") or [],
        "nameOnly": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
        "patch": diff.get("patch") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
    }


def _workspace_manifest_ref(workspace: dict[str, Any]) -> dict[str, Any] | None:
    manifest = (workspace.get("metadata") or {}).get("workspaceManifest") or {}
    if not manifest:
        return None
    return {"kind": "workspace_manifest", "status": "captured", **manifest}


def _final_diff_refs(workspace: dict[str, Any], diff: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [_evidence_git_diff_ref(diff)]
    manifest = _workspace_manifest_ref(workspace)
    if manifest:
        refs.append(manifest)
    refs.append(capture_workspace_snapshot(workspace["path"]))
    return refs


def _write_text_evidence_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    name: str,
    kind: str,
    suffix: str,
    content: str,
    mime_type: str,
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> dict[str, Any]:
    artifact_id = f"artifact-{uuid.uuid4()}"
    artifact_file = write_text_artifact(root=root, artifact_id=artifact_id, suffix=suffix, content=content)
    return repo.create_artifact(
        artifact_id=artifact_id,
        project_id=project_id,
        evidence_package_id=evidence_id,
        kind=kind,
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        metadata={
            "name": name,
            "source": source,
            "mimeType": mime_type,
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
        },
    )


def _write_json_evidence_artifact(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    name: str,
    kind: str,
    payload: Any,
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> dict[str, Any]:
    return _write_text_evidence_artifact(
        root=root,
        project_id=project_id,
        evidence_id=evidence_id,
        name=name,
        kind=kind,
        suffix=".json",
        content=json_dumps(redact_secrets(payload)),
        mime_type="application/json",
        repo=repo,
        source=source,
    )


def _dedupe_artifact_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        deduped.append(record)
    return deduped


def _artifact_content_bytes(artifact: dict[str, Any]) -> bytes:
    path = Path(str(artifact.get("path") or ""))
    if not path.exists() or not path.is_file():
        raise ValueError(f"Artifact file is missing: {artifact.get('id')}")
    content = path.read_bytes()
    expected_hash = str(artifact.get("hash") or "")
    if not expected_hash:
        raise ValueError(f"Artifact hash is missing: {artifact.get('id')}")
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Artifact hash mismatch: {artifact.get('id')}")
    return content


def _find_linked_artifact(
    artifacts: list[dict[str, Any]],
    *,
    artifact_id: str | None = None,
    kind: str | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    for artifact in artifacts:
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        if artifact_id and artifact.get("id") != artifact_id:
            continue
        if kind and artifact.get("kind") != kind:
            continue
        if name and metadata.get("name") != name:
            continue
        if artifact_id or kind or name:
            return artifact
    return None


def _validate_patch_artifact(evidence: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    diff_summary = evidence.get("diffSummary") if isinstance(evidence.get("diffSummary"), dict) else {}
    patch_artifact = _find_linked_artifact(
        artifacts,
        artifact_id=diff_summary.get("patchArtifactId"),
        kind="git_patch",
    ) or _find_linked_artifact(artifacts, kind="git_patch", name="diff.patch")
    if not patch_artifact:
        raise ValueError("issue_to_patch approval requires a git_patch artifact.")
    patch_content = _artifact_content_bytes(patch_artifact)
    if not patch_content.strip():
        raise ValueError("issue_to_patch approval requires a non-empty patch artifact.")
    return patch_artifact


def _validate_security_findings(evidence: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    diff_summary = evidence.get("diffSummary") if isinstance(evidence.get("diffSummary"), dict) else {}
    findings_artifact = _find_linked_artifact(
        artifacts,
        artifact_id=diff_summary.get("securityFindingsArtifactId"),
        kind="security_findings",
    ) or _find_linked_artifact(artifacts, kind="security_findings", name="security-findings.json")
    if not findings_artifact:
        raise ValueError("issue_to_patch approval requires a security findings artifact.")
    payload = json_loads(_artifact_content_bytes(findings_artifact).decode("utf-8"), {})
    if not isinstance(payload, dict):
        raise ValueError("Security findings artifact must contain a JSON object.")
    if str(payload.get("status") or "").lower() == "blocked":
        raise ValueError("Security findings are blocking; patch cannot be approved for integration.")
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if str(finding.get("decision") or "").lower() in {"deny", "requires_human", "requires_approval"}:
            raise ValueError("Security findings include a blocking policy decision.")
    return payload


def _validate_qa_for_patch_approval(evidence: dict[str, Any], *, action_approved: bool) -> None:
    qa_verdict = str(evidence.get("qaVerdict") or "").lower()
    if qa_verdict == "passed":
        if not qa_verdict_allows_completion("passed", evidence.get("testResults") or []):
            raise ValueError("Passed QA verdict requires real passing QA command evidence.")
        return
    if qa_verdict == "needs_human_review" and action_approved:
        if not qa_verdict_allows_completion("passed", evidence.get("testResults") or []):
            raise ValueError("Human-reviewed patch approval still requires real passing QA command evidence.")
        return
    raise ValueError("issue_to_patch approval requires passed QA or needs_human_review with approval.")


def _write_git_status_artifacts(
    *,
    root: Path,
    project_id: str,
    evidence_id: str,
    diff: dict[str, Any],
    repo: EvidenceRepository,
    source: str = "issue_to_patch",
) -> list[dict[str, Any]]:
    status_payload = {
        "state": diff.get("state"),
        "blockerState": diff.get("blockerState"),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "statusRaw": diff.get("statusRaw", ""),
        "status": diff.get("status") or [],
        "nameOnly": diff.get("nameOnly") or [],
        "diffStat": diff.get("diffStat") or "",
    }
    return [
        _write_text_evidence_artifact(
            root=root,
            project_id=project_id,
            evidence_id=evidence_id,
            name="git-status.txt",
            kind="git_status",
            suffix=".git-status.txt",
            content=str(diff.get("statusRaw") or ""),
            mime_type="text/plain",
            repo=repo,
            source=source,
        ),
        _write_json_evidence_artifact(
            root=root,
            project_id=project_id,
            evidence_id=evidence_id,
            name="git-status.json",
            kind="git_status",
            payload=status_payload,
            repo=repo,
            source=source,
        ),
    ]


def _security_findings_from_policy(policy_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    blocked = False
    for decision in policy_decisions:
        decision_value = str(decision.get("decision") or "").lower()
        risk_level = str(decision.get("riskLevel") or decision.get("risk_level") or "").lower()
        if decision_value in {"deny", "requires_human"}:
            blocked = True
        if decision_value not in {"allow", "allowed"} or risk_level in {"high", "critical"}:
            findings.append(
                {
                    "policyDecisionId": decision.get("id"),
                    "decision": decision.get("decision"),
                    "riskLevel": decision.get("riskLevel") or decision.get("risk_level"),
                    "reason": decision.get("reason"),
                    "agentId": decision.get("agentId"),
                    "tool": decision.get("tool"),
                }
            )
    return {
        "status": "blocked" if blocked else "risk" if findings else "passed",
        "source": "policy_decisions",
        "findings": redact_secrets(findings),
        "policyDecisionIds": [decision.get("id") for decision in policy_decisions if decision.get("id")],
    }
