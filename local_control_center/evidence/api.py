"""Endpoints HTTP del slice de evidencia: crear, listar, exportar y gestionar artefactos.

Expone el router FastAPI que valida cada paquete contra las reglas de QA real antes de
persistirlo, promueve logs/diffs/screenshots grandes a artefactos en disco, emite eventos y
auditoría, y abre riesgos de gobernanza ante veredictos fallidos o artefactos expirados. Las
descargas de artefactos verifican el hash y confinan las rutas al root de artefactos.

@author Rodrigo Mason
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from ..agents.model_benchmarks import ModelBenchmarkStore
from ..governance.signals import record_governance_risk
from ..shared.event_bus import EventBus
from ..shared.redaction import redact_secrets
from ..shared.time import utc_now
from .artifacts import (
    cleanup_unreferenced_artifacts,
    promote_large_logs,
    promote_screenshots,
    resolved_artifact_root,
    write_binary_artifact,
    write_text_artifact,
)
from .image_validation import ImageValidationError, read_validated_image
from .models import (
    ArtifactCleanupRequest,
    ArtifactCleanupResponse,
    ArtifactIngestRequest,
    ArtifactKind,
    ArtifactResponse,
    ArtifactRetentionActionRequest,
    ArtifactRetentionActionResponse,
    ArtifactRetentionPlanRequest,
    ArtifactRetentionPlanResponse,
    EvidenceCreateRequest,
    EvidenceDetailResponse,
    EvidenceListResponse,
    EvidencePackageResponse,
)
from .qa_reports import build_markdown_report
from .quality import REAL_QA_EVIDENCE_SOURCES, failed_test_results, real_qa_command_errors
from .repository import EvidenceRepository
from .test_results import TestReportError, normalize_test_result_reports


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Construye el APIRouter de evidencia ligado a la plataforma y al guard de escritura.

    `require_write` se invoca en cada ruta mutante o sensible (creación, ingesta, limpieza,
    retención, exportación y descarga de artefactos) para imponer el control de acceso.
    """
    router = APIRouter()
    allowed_artifact_kinds = set(get_args(ArtifactKind))
    max_ingested_artifact_bytes = 2_000_000

    def repository() -> EvidenceRepository:
        return EvidenceRepository(platform.connection)

    def event_bus() -> EventBus:
        return EventBus(platform.connection)

    def artifact_file_response(
        artifact: dict[str, Any],
        *,
        generated_image_only: bool = False,
    ) -> Response:
        """Build a hash-verified, root-confined artifact response."""
        artifact_root = resolved_artifact_root(platform.cwd)
        artifact_path = Path(artifact["path"]).resolve(strict=False)
        try:
            artifact_path.relative_to(artifact_root)
        except ValueError as error:
            raise HTTPException(
                status_code=403,
                detail="Artifact path is outside the evidence artifact root.",
            ) from error
        if not artifact_path.exists() or not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is missing.")
        if generated_image_only:
            try:
                content, detected_media_type, _suffix, _width, _height = read_validated_image(
                    artifact_path
                )
            except ImageValidationError as error:
                raise HTTPException(status_code=409, detail=error.code) from error
        else:
            content = artifact_path.read_bytes()
            detected_media_type = None
        expected_hash = artifact.get("hash")
        if generated_image_only and not expected_hash:
            raise HTTPException(status_code=409, detail="Generated image has no recorded hash.")
        if expected_hash and hashlib.sha256(content).hexdigest() != expected_hash:
            raise HTTPException(
                status_code=409,
                detail="Artifact hash does not match the recorded metadata.",
            )

        metadata = artifact.get("metadata") or {}
        media_type = str(metadata.get("mimeType") or "text/plain")
        if generated_image_only and (
            artifact.get("kind") != "generated_image" or media_type != detected_media_type
        ):
            raise HTTPException(status_code=409, detail="Artifact is not a valid generated image.")
        filename = str(metadata.get("name") or artifact_path.name)
        response: Response
        if generated_image_only:
            response = Response(content=content, media_type=media_type)
        else:
            response = FileResponse(path=artifact_path, media_type=media_type, filename=filename)
        response.headers["X-AIDO-Artifact-Id"] = artifact["id"]
        response.headers["X-AIDO-Artifact-Hash"] = expected_hash or ""
        return response

    @router.get("/api/v1/evidence", response_model=EvidenceListResponse)
    async def list_evidence() -> dict[str, Any]:
        return {"evidencePackages": repository().list_evidence_packages()}

    @router.post(
        "/api/v1/evidence/artifacts/cleanup", status_code=202, response_model=ArtifactCleanupResponse
    )
    async def cleanup_artifacts(body: ArtifactCleanupRequest, request: Request) -> ArtifactCleanupResponse:
        require_write(request)
        dry_run = body.dry_run
        repo = repository()
        referenced_paths = {artifact["path"] for artifact in repo.list_all_artifacts()}
        result = cleanup_unreferenced_artifacts(
            root=platform.cwd,
            referenced_paths=referenced_paths,
            dry_run=dry_run,
        )
        event_bus().record_event(
            event_type="evidence.artifacts.cleanup",
            payload={
                "dryRun": dry_run,
                "orphanFiles": len(result["orphanFiles"]),
                "deletedFiles": len(result["deletedFiles"]),
                "artifactRoot": result["artifactRoot"],
            },
        )
        event_bus().record_audit(
            action="evidence.artifacts.cleanup",
            target=result["artifactRoot"],
            payload={
                "dryRun": dry_run,
                "orphanFiles": len(result["orphanFiles"]),
                "deletedFiles": len(result["deletedFiles"]),
            },
        )
        return ArtifactCleanupResponse(
            dryRun=result["dryRun"],
            artifactRoot=result["artifactRoot"],
            orphanFiles=result["orphanFiles"],
            deletedFiles=result["deletedFiles"],
            keptReferencedFiles=result["keptReferencedFiles"],
        )

    @router.post(
        "/api/v1/evidence/artifacts/retention",
        status_code=202,
        response_model=ArtifactRetentionPlanResponse,
    )
    async def plan_artifact_retention(
        body: ArtifactRetentionPlanRequest, request: Request
    ) -> ArtifactRetentionPlanResponse:
        require_write(request)
        dry_run = body.dry_run
        now_iso = str(body.now or utc_now())
        expired_artifacts = repository().list_expired_referenced_artifacts(now_iso=now_iso)
        project_ids = sorted(
            {artifact["projectId"] for artifact in expired_artifacts if artifact.get("projectId")}
        )
        created_risk_ids: list[str] = []
        for project_id in project_ids:
            project_artifacts = [
                artifact for artifact in expired_artifacts if artifact["projectId"] == project_id
            ]
            risk = record_governance_risk(
                platform.connection,
                project_id=project_id,
                title="Expired evidence artifacts require retention review",
                source_type="artifact_retention",
                source_id=",".join(artifact["id"] for artifact in project_artifacts[:10]),
                severity="medium",
                description="Referenced evidence artifacts have passed their retention expiry and need an explicit keep/delete decision.",
                mitigation="Review the evidence packages, export anything required, then delete only through an audited retention action.",
                owner="qa_reviewer",
                metadata={
                    "artifactIds": [artifact["id"] for artifact in project_artifacts],
                    "dryRun": dry_run,
                    "now": now_iso,
                },
            )
            if risk:
                created_risk_ids.append(risk["id"])
                event_bus().record_event(
                    project_id=project_id,
                    event_type="risk.created",
                    payload={"riskId": risk["id"], "sourceType": "artifact_retention"},
                )
        event_bus().record_event(
            event_type="evidence.artifacts.retention_reviewed",
            payload={
                "dryRun": dry_run,
                "expiredArtifacts": len(expired_artifacts),
                "riskIds": created_risk_ids,
            },
        )
        event_bus().record_audit(
            action="evidence.artifacts.retention",
            target="artifact_retention",
            payload={
                "dryRun": dry_run,
                "expiredArtifacts": len(expired_artifacts),
                "riskIds": created_risk_ids,
            },
        )
        return ArtifactRetentionPlanResponse(
            dryRun=dry_run,
            now=now_iso,
            expiredArtifacts=expired_artifacts,
            riskIds=created_risk_ids,
        )

    @router.post(
        "/api/v1/evidence/artifacts/retention/actions",
        status_code=202,
        response_model=ArtifactRetentionActionResponse,
    )
    async def apply_artifact_retention_action(
        body: ArtifactRetentionActionRequest, request: Request
    ) -> ArtifactRetentionActionResponse:
        require_write(request)
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(status_code=422, detail="Retention action reason is required.")
        action = body.action.strip().lower()
        if action not in {"export", "delete"}:
            raise HTTPException(status_code=422, detail="Retention action must be export or delete.")
        artifact_ids = body.artifact_ids
        if (
            not isinstance(artifact_ids, list)
            or not artifact_ids
            or not all(isinstance(item, str) for item in artifact_ids)
        ):
            raise HTTPException(
                status_code=422, detail="artifactIds must be a non-empty list of artifact IDs."
            )
        now_iso = str(body.now or utc_now())

        repo = repository()
        artifact_root = resolved_artifact_root(platform.cwd)
        acted: list[dict[str, Any]] = []
        for artifact_id in artifact_ids:
            try:
                artifact = repo.get_artifact_by_id(artifact_id)
            except KeyError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            if not artifact.get("evidencePackageId"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Artifact is not referenced by an evidence package: {artifact_id}",
                )
            metadata = artifact.get("metadata") or {}
            expires_at = metadata.get("expiresAt")
            if not isinstance(expires_at, str) or expires_at > now_iso:
                raise HTTPException(status_code=422, detail=f"Artifact is not expired: {artifact_id}")

            artifact_path = Path(artifact["path"]).resolve(strict=False)
            try:
                artifact_path.relative_to(artifact_root)
            except ValueError as error:
                raise HTTPException(
                    status_code=403, detail="Artifact path is outside the evidence artifact root."
                ) from error

            deleted = False
            if action == "delete" and artifact_path.exists():
                if not artifact_path.is_file():
                    raise HTTPException(status_code=409, detail=f"Artifact path is not a file: {artifact_id}")
                expected_hash = artifact.get("hash")
                if expected_hash and hashlib.sha256(artifact_path.read_bytes()).hexdigest() != expected_hash:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Artifact hash does not match recorded metadata: {artifact_id}",
                    )
                artifact_path.unlink()
                deleted = True

            retention_action = {
                "action": action,
                "reason": reason,
                "actedAt": now_iso,
                "physicalFileDeleted": deleted,
            }
            updated = repo.update_artifact_metadata(
                artifact_id=artifact_id,
                metadata={**metadata, "retentionAction": retention_action},
            )
            acted.append({**updated, "retentionAction": retention_action})
            event_bus().record_event(
                project_id=updated["projectId"],
                event_type=f"evidence.artifact.retention_{action}",
                payload={"artifactId": artifact_id, "evidencePackageId": updated["evidencePackageId"]},
            )

        event_bus().record_audit(
            action=f"evidence.artifact.retention.{action}",
            target=",".join(artifact_ids),
            payload={"reason": reason, "artifactIds": artifact_ids, "now": now_iso},
        )
        return ArtifactRetentionActionResponse(action=action, artifacts=acted)

    @router.post("/api/v1/evidence", status_code=201, response_model=EvidencePackageResponse)
    async def create_evidence(body: EvidenceCreateRequest, request: Request) -> EvidencePackageResponse:
        require_write(request)
        payload = body.model_dump(by_alias=True)
        try:
            normalized_report_results = normalize_test_result_reports(payload.get("testResultReports") or [])
        except TestReportError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        test_results = [*(payload.get("testResults") or []), *normalized_report_results]
        evidence_source = str(payload.get("evidenceSource") or "operator_attested")
        if payload.get("qaVerdict") == "passed" and failed_test_results(test_results):
            raise HTTPException(
                status_code=422,
                detail="QA cannot pass when any failed test result is present.",
            )
        if payload.get("qaVerdict") == "passed":
            if evidence_source not in REAL_QA_EVIDENCE_SOURCES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "qaVerdict=passed requires evidenceSource=qa_passed_by_command or "
                        "verified_completion."
                    ),
                )
            real_qa_errors = real_qa_command_errors(
                {
                    "evidenceSource": evidence_source,
                    "qaVerdict": payload.get("qaVerdict"),
                    "testResults": test_results,
                    "toolCalls": payload.get("toolCalls") or [],
                    "policyDecisions": payload.get("policyDecisions") or [],
                    "artifacts": payload.get("artifacts") or [],
                    "hashes": payload.get("hashes") or {},
                }
            )
            if real_qa_errors:
                raise HTTPException(
                    status_code=422,
                    detail="QA passed requires real command execution evidence: " + "; ".join(real_qa_errors),
                )
        logs, log_artifacts = promote_large_logs(
            root=platform.cwd, logs=redact_secrets(payload.get("logs") or [])
        )
        screenshot_refs, screenshot_artifacts = promote_screenshots(
            root=platform.cwd,
            screenshot_refs=redact_secrets(payload.get("screenshotRefs") or []),
        )
        repo = repository()
        generated_artifact_ids = [artifact["id"] for artifact in [*log_artifacts, *screenshot_artifacts]]
        generated_artifact_refs = [
            {
                "id": artifact["id"],
                "kind": artifact["kind"],
                "hash": artifact["hash"],
                "name": (artifact.get("metadata") or {}).get("name") or artifact["id"],
                "sizeBytes": (artifact.get("metadata") or {}).get("sizeBytes"),
            }
            for artifact in [*log_artifacts, *screenshot_artifacts]
        ]
        payload_artifacts = [item for item in (payload.get("artifacts") or []) if isinstance(item, dict)]
        payload_hashes = {
            str(key): str(value)
            for key, value in (payload.get("hashes") or {}).items()
            if isinstance(value, str)
        }
        generated_hashes = {
            str(artifact["id"]): str(artifact["hash"])
            for artifact in [*log_artifacts, *screenshot_artifacts]
            if artifact.get("hash")
        }
        evidence = repo.create_evidence_package(
            project_id=payload["projectId"],
            workflow_run_id=payload.get("workflowRunId"),
            workflow_step_id=payload.get("workflowStepId"),
            agent_id=payload.get("agentId"),
            agent_run_id=payload.get("agentRunId"),
            job_id=payload.get("jobId"),
            workspace_id=payload.get("workspaceId"),
            runtime_id=payload.get("runtimeId"),
            task_id=payload.get("taskId", "task"),
            test_plan=payload.get("testPlan", ""),
            acceptance_checklist=payload.get("acceptanceChecklist") or [],
            test_results=test_results,
            logs=logs,
            diff_refs=payload.get("diffRefs") or [],
            screenshot_refs=screenshot_refs,
            risk_notes=payload.get("riskNotes") or [],
            artifact_ids=[*(payload.get("artifactIds") or []), *generated_artifact_ids],
            diff_summary=payload.get("diffSummary") or {},
            runtime_health=payload.get("runtimeHealth") or {},
            model_calls=payload.get("modelCalls") or [],
            tool_calls=payload.get("toolCalls") or [],
            policy_decisions=payload.get("policyDecisions") or [],
            approvals=payload.get("approvals") or [],
            artifacts=[*payload_artifacts, *generated_artifact_refs],
            hashes={**payload_hashes, **generated_hashes},
            evidence_source=evidence_source,
            qa_verdict=payload.get("qaVerdict", "not_started"),
        )
        for artifact in [*log_artifacts, *screenshot_artifacts]:
            repo.create_artifact(
                project_id=evidence["projectId"],
                evidence_package_id=evidence["id"],
                kind=artifact["kind"],
                path=artifact["path"],
                content_hash=artifact["hash"],
                metadata=artifact["metadata"],
                artifact_id=artifact["id"],
            )
        event_bus().record_event(
            project_id=evidence["projectId"],
            event_type="qa.evidence.created",
            payload={"evidencePackageId": evidence["id"], "qaVerdict": evidence["qaVerdict"]},
        )
        if evidence["qaVerdict"] in {"failed", "blocked", "needs_human_review"}:
            risk = record_governance_risk(
                platform.connection,
                project_id=evidence["projectId"],
                title=f"QA verdict requires follow-up: {evidence['taskId']}",
                source_type="qa_verdict",
                source_id=evidence["id"],
                severity="high" if evidence["qaVerdict"] == "failed" else "medium",
                description="QA evidence did not pass the acceptance gate.",
                mitigation="Review the evidence package, classify the failure, and create a remediation step before approval.",
                owner=evidence.get("agentId") or "qa_reviewer",
                metadata={"qaVerdict": evidence["qaVerdict"], "riskNotes": evidence.get("riskNotes", [])},
            )
            if risk:
                event_bus().record_event(
                    project_id=risk["projectId"],
                    event_type="risk.created",
                    payload={"riskId": risk["id"], "sourceType": "qa_verdict"},
                )
        outcome = ModelBenchmarkStore(platform.connection).record_evidence_outcome(
            evidence=evidence,
            payload=payload,
            test_results=test_results,
        )
        if outcome:
            event_bus().record_audit(
                project_id=evidence["projectId"],
                action="model_gateway.benchmark_outcome.ingest",
                target=outcome["id"],
                payload={
                    "evidencePackageId": evidence["id"],
                    "usageLedgerId": outcome.get("usageLedgerId"),
                    "providerId": outcome["providerId"],
                    "model": outcome["model"],
                },
            )
        return EvidencePackageResponse(evidencePackage=evidence)

    @router.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDetailResponse)
    async def get_evidence(evidence_id: str) -> dict[str, Any]:
        try:
            repo = repository()
            return {
                "evidencePackage": repo.get_evidence_package(evidence_id),
                "testResultRecords": repo.list_test_results(evidence_id),
                "artifacts": repo.list_artifacts(evidence_id),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/api/v1/evidence/{evidence_id}/report")
    async def export_evidence_report(evidence_id: str, request: Request) -> PlainTextResponse:
        require_write(request)
        try:
            repo = repository()
            evidence = repo.get_evidence_package(evidence_id)
            test_results = repo.list_test_results(evidence_id)
            artifacts = repo.list_artifacts(evidence_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        report = build_markdown_report(evidence=evidence, test_results=test_results, artifacts=artifacts)
        event_bus().record_audit(
            project_id=evidence["projectId"],
            action="evidence.report.export",
            target=evidence["id"],
            payload={
                "format": "markdown",
                "artifactCount": len(artifacts),
                "testResultCount": len(test_results),
            },
        )
        response = PlainTextResponse(report, media_type="text/markdown")
        response.headers["X-AIDO-Evidence-Id"] = evidence["id"]
        return response

    @router.post("/api/v1/evidence/{evidence_id}/artifacts", status_code=201, response_model=ArtifactResponse)
    async def ingest_artifact(
        evidence_id: str, body: ArtifactIngestRequest, request: Request
    ) -> ArtifactResponse:
        require_write(request)
        payload = body.model_dump(by_alias=True, exclude_none=True)
        kind = str(payload.get("kind") or "").strip()
        if kind not in allowed_artifact_kinds:
            raise HTTPException(status_code=422, detail="Unsupported artifact kind.")
        name = str(payload.get("name") or f"{kind}.artifact").strip()
        if not name or any(separator in name for separator in ("/", "\\", ":")):
            raise HTTPException(status_code=422, detail="Artifact name must be a simple filename.")
        repo = repository()
        try:
            evidence = repo.get_evidence_package(evidence_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        artifact_id = f"artifact-{uuid.uuid4()}"
        if isinstance(payload.get("content"), str):
            content = payload["content"]
            if len(content.encode("utf-8")) > max_ingested_artifact_bytes:
                raise HTTPException(status_code=422, detail="Artifact content exceeds maximum accepted size.")
            suffix = Path(name).suffix or ".txt"
            artifact_file = write_text_artifact(
                root=platform.cwd, artifact_id=artifact_id, suffix=suffix, content=content
            )
            mime_type = str(payload.get("mimeType") or "text/plain")
        elif isinstance(payload.get("contentBase64"), str):
            try:
                content_bytes = base64.b64decode(payload["contentBase64"], validate=True)
            except ValueError as error:
                raise HTTPException(status_code=422, detail="contentBase64 is not valid base64.") from error
            if len(content_bytes) > max_ingested_artifact_bytes:
                raise HTTPException(status_code=422, detail="Artifact content exceeds maximum accepted size.")
            suffix = Path(name).suffix or ".bin"
            artifact_file = write_binary_artifact(
                root=platform.cwd,
                artifact_id=artifact_id,
                suffix=suffix,
                content=content_bytes,
            )
            mime_type = str(payload.get("mimeType") or "application/octet-stream")
        else:
            raise HTTPException(status_code=422, detail="Artifact requires content or contentBase64.")

        artifact = repo.create_artifact(
            artifact_id=artifact_id,
            project_id=evidence["projectId"],
            evidence_package_id=evidence["id"],
            kind=kind,
            path=artifact_file["path"],
            content_hash=artifact_file["hash"],
            metadata={
                "name": name,
                "source": "artifact_ingestion",
                "mimeType": mime_type,
                "sizeBytes": artifact_file["sizeBytes"],
            },
        )
        event_bus().record_event(
            project_id=evidence["projectId"],
            event_type="evidence.artifact.ingested",
            payload={"evidencePackageId": evidence["id"], "artifactId": artifact["id"], "kind": kind},
        )
        event_bus().record_audit(
            project_id=evidence["projectId"],
            action="evidence.artifact.ingest",
            target=artifact["id"],
            payload={"evidencePackageId": evidence["id"], "kind": kind, "name": name},
        )
        return ArtifactResponse(artifact=artifact)

    @router.get("/api/v1/evidence/{evidence_id}/artifacts/{artifact_id}")
    async def get_artifact(evidence_id: str, artifact_id: str, request: Request) -> FileResponse:
        require_write(request)
        try:
            artifact = repository().get_artifact(evidence_id=evidence_id, artifact_id=artifact_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

        return artifact_file_response(artifact)

    @router.get("/api/v1/projects/{project_id}/artifacts/{artifact_id}")
    async def get_project_generated_image(
        project_id: str,
        artifact_id: str,
        request: Request,
    ) -> Response:
        """Download one generated image owned by the exact requested project."""
        require_write(request)
        try:
            artifact = repository().get_project_artifact(
                project_id=project_id,
                artifact_id=artifact_id,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return artifact_file_response(artifact, generated_image_only=True)

    return router
