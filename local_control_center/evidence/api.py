from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..governance.signals import record_governance_risk
from .repository import EvidenceRepository


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def repository() -> EvidenceRepository:
        return EvidenceRepository(platform.connection)

    @router.get("/api/v1/evidence")
    async def list_evidence() -> dict[str, Any]:
        return {"evidencePackages": repository().list_evidence_packages()}

    @router.post("/api/v1/evidence", status_code=201)
    async def create_evidence(request: Request) -> dict[str, Any]:
        require_write(request)
        body = await request.json()
        if body.get("qaVerdict") == "passed" and not (
            body.get("testResults") or body.get("diffRefs") or body.get("screenshotRefs")
        ):
            raise HTTPException(
                status_code=422,
                detail="QA cannot pass without test results, diff refs, or screenshot/artifact refs.",
            )
        evidence = repository().create_evidence_package(
            project_id=body["projectId"],
            workflow_run_id=body.get("workflowRunId"),
            agent_id=body.get("agentId"),
            task_id=body.get("taskId", "task"),
            test_plan=body.get("testPlan", ""),
            acceptance_checklist=body.get("acceptanceChecklist") or [],
            test_results=body.get("testResults") or [],
            logs=body.get("logs") or [],
            diff_refs=body.get("diffRefs") or [],
            screenshot_refs=body.get("screenshotRefs") or [],
            risk_notes=body.get("riskNotes") or [],
            qa_verdict=body.get("qaVerdict", "not_started"),
        )
        platform.record_event(
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
                platform.record_event(
                    project_id=risk["projectId"],
                    event_type="risk.created",
                    payload={"riskId": risk["id"], "sourceType": "qa_verdict"},
                )
        return {"evidencePackage": evidence}

    @router.get("/api/v1/evidence/{evidence_id}")
    async def get_evidence(evidence_id: str) -> dict[str, Any]:
        try:
            repo = repository()
            return {
                "evidencePackage": repo.get_evidence_package(evidence_id),
                "testResultRecords": repo.list_test_results(evidence_id),
            }
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
