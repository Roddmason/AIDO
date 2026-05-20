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
    assert "AIDO_OTEL_SMOKE: \"1\"" in content
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
    assert "export function buildApiPath" in content
    assert "export async function requestGeneratedOperation" in content


def test_openapi_generation_script_documents_no_network_dependency() -> None:
    script = ROOT / "local-control-center" / "scripts" / "generate_openapi_client.py"
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "create_app" in content
    assert "No network access" in content
    assert "openapi.ts" in content
