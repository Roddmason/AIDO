from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quality_workflow_has_default_and_optional_smoke_profiles() -> None:
    workflow = ROOT / ".github" / "workflows" / "quality.yml"
    assert workflow.exists()
    content = workflow.read_text(encoding="utf-8")
    assert "pull_request:" in content
    assert "push:" in content
    assert "workflow_dispatch:" in content
    assert "runtime-smoke:" in content
    assert "otel-smoke:" in content
    assert "AIDO_RUNTIME_SMOKE: \"1\"" in content
    assert "AIDO_RUNTIME_ISSUE_TO_PATCH_SMOKE:" in content
    assert "AIDO_OTEL_SMOKE: \"1\"" in content
    assert "issue_to_patch_smoke:" in content
    assert "if: ${{ github.event_name == 'workflow_dispatch'" in content
    assert "smoke-runtime-adapters.ps1" in content
    assert "smoke-otel-exporter.ps1" in content


def test_generated_openapi_client_is_checked_in_and_v1_only() -> None:
    generated = ROOT / "local-control-center" / "web" / "src" / "api" / "generated" / "openapi.ts"
    assert generated.exists()
    content = generated.read_text(encoding="utf-8")
    assert "Generated from FastAPI OpenAPI" in content
    assert "/api/v1/workflows" in content
    assert "/api/v1/agent-profiles" in content
    assert "/api/v1/integrations/mcp/register" in content
    assert ("/api/" + "state") not in content
    assert "export type ApiPath" in content
    assert "export type ApiEndpoint" in content
    assert "export const OPERATIONS_BY_ID" in content
    assert "export type OperationById" in content
    assert "export type OperationResponse" in content
    assert "export type OperationRequestBody" in content
    assert "export type OperationResponseBodies" in content
    assert '"healthz_healthz_get": HealthResponse' in content
    assert '"handshake_api_v1_security_handshake_get": HandshakeResponse' in content
    assert '"retrieval_status_api_v1_retrieval_status_get": RetrievalStatusResponse' in content
    assert "export function buildApiPath" in content
    assert "export async function requestGeneratedOperation" in content

    api_client = (ROOT / "local-control-center" / "web" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "requestGeneratedOperation" in api_client
    assert "overview_api_v1_overview_get" in api_client
    assert '"/api/v1/overview"' not in api_client


def test_openapi_generation_script_documents_no_network_dependency() -> None:
    script = ROOT / "local-control-center" / "scripts" / "generate_openapi_client.py"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "create_app" in content
    assert "No network access" in content
    assert "openapi.ts" in content


def test_playwright_uses_isolated_state_for_mutating_e2e() -> None:
    config = ROOT / "playwright.config.mjs"
    assert config.exists()
    content = config.read_text(encoding="utf-8")
    assert "playwright-control-center-${process.pid}.sqlite" in content
    assert "workers: 1" in content
    assert "reuseExistingServer: false" in content
