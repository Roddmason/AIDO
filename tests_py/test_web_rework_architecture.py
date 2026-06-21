import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "local-control-center" / "web"
SRC = WEB / "src"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_editorial_design_context_is_documented() -> None:
    context = ROOT / ".impeccable.md"

    assert context.exists()

    source = read(context)
    assert "## Design Context" in source
    assert (
        "small team" in source.lower()
        or "equipo pequeno" in source.lower()
        or "equipo pequeño" in source.lower()
    )
    assert "editorial premium" in source.lower()
    assert "Windows" in source
    assert "traceability" in source.lower() or "trazabilidad" in source.lower()


def test_dashboard_entrypoint_uses_vite_typescript_app() -> None:
    expected_paths = [
        SRC / "main.tsx",
        SRC / "app" / "App.tsx",
        SRC / "api" / "client.ts",
        SRC / "api" / "types.ts",
        SRC / "components" / "primitives.tsx",
        SRC / "motion" / "useControlMotion.ts",
        SRC / "design-system" / "tokens.css",
        SRC / "design-system" / "base.css",
        SRC / "design-system" / "layout.css",
        SRC / "design-system" / "components.css",
        SRC / "design-system" / "motion.css",
    ]
    for path in expected_paths:
        assert path.exists(), path


def test_frontend_feature_slices_are_explicit() -> None:
    expected = [
        "home",
        "memory",
        "agents",
        "workflows",
        "integrations",
    ]

    for feature in expected:
        assert (SRC / "features" / feature).is_dir(), feature


def test_dashboard_reads_v1_overview_without_removed_state_contract() -> None:
    web_sources = "\n".join(
        read(path) for path in SRC.rglob("*") if path.is_file() and path.suffix in {".ts", ".tsx"}
    )
    removed_state_route = "/api/" + "state"
    removed_workspace_key = "workspace" + "State"

    assert removed_state_route not in web_sources
    assert "getLegacyState" not in web_sources
    assert removed_workspace_key not in web_sources


def test_frontend_domain_types_are_generated_openapi_aliases() -> None:
    source = read(SRC / "api" / "types.ts")

    assert "from './generated/openapi'" in source
    assert "export type Overview = OverviewResponse" in source
    assert "export type RuntimeProviders = RuntimeProvidersResponse" in source
    assert "export type Artifact = ArtifactRecord" in source
    assert "export type RetrievalStatus = RetrievalStatusResponse" in source
    assert "export type PolicyRevision = PolicyRevisionRecord" in source
    for stale_manual_type in [
        "export type Project = {",
        "export type Job = {",
        "export type Workflow = {",
        "export type AgentProfile = {",
        "export type Pipeline = {",
        "export type RuntimeProviders = {",
    ]:
        assert stale_manual_type not in source


def test_stable_frontend_artifact_and_retrieval_surfaces_are_not_dictionary_typed() -> None:
    types_source = read(SRC / "api" / "types.ts")
    client_source = read(SRC / "api" / "client.ts")
    hook_source = read(SRC / "hooks" / "useControlPlane.ts")
    artifacts_source = read(SRC / "lib" / "artifacts.ts")
    pages_source = read(SRC / "features" / "pages.tsx")
    # The workflow artifact preview now lives in the run-detail Inspector body, not the launcher page.
    run_detail_source = read(SRC / "features" / "workflows" / "RunDetail.tsx")

    assert "RetrievalStatusResponse" in types_source
    assert "ArtifactRecord" in types_source
    assert (
        "requestGeneratedOperation<'retrieval_status_api_v1_retrieval_status_get', RetrievalStatus>"
        in client_source
    )
    assert "RetrievalStatus" in hook_source
    assert "retrievalStatus: RetrievalStatus | null" in hook_source
    assert "retrievalStatus: Dictionary | null" not in hook_source
    assert "artifact: Artifact" in artifacts_source
    assert "retrievalStatus: RetrievalStatus | null" in pages_source
    assert "useState<Artifact | null>" in pages_source
    assert "useState<Artifact | null>" in run_detail_source
    assert "const openPreview = async (artifact: Artifact)" in pages_source
    assert "const openPreview = async (artifact: Artifact)" in run_detail_source


def test_stable_frontend_policy_revision_surface_is_not_dictionary_typed() -> None:
    types_source = read(SRC / "api" / "types.ts")
    pages_source = read(SRC / "features" / "pages.tsx")

    assert "PolicyRevisionRecord" in types_source
    assert "export type PolicyRevision = PolicyRevisionRecord" in types_source
    assert "PolicyRevision" in pages_source
    assert "useState<PolicyRevision | null>" in pages_source
    assert "useState<Dictionary | null>" not in pages_source


def test_frontend_mutation_helpers_use_generated_request_response_types() -> None:
    client_source = read(SRC / "api" / "client.ts")

    assert "OperationRequestBody" in client_source
    assert (
        "type MutationBody<TOperationId extends ApiOperationId> = OperationRequestBody<TOperationId>"
        in client_source
    )
    assert (
        "requestGeneratedOperation<'approve_action_api_v1_jobs__job_id__actions__action_id__approve_post', Dictionary>"
        not in client_source
    )
    assert (
        "requestGeneratedOperation<'create_workflow_api_v1_workflows_post', Dictionary>" not in client_source
    )
    assert (
        "requestGeneratedOperation<'upsert_agent_profile_api_v1_agent_profiles_post', Dictionary>"
        not in client_source
    )
    assert "body: MutationBody<'create_workflow_api_v1_workflows_post'>" in client_source
    assert "body: MutationBody<'upsert_agent_profile_api_v1_agent_profiles_post'>" in client_source
    assert "body: MutationBody<'create_role_policy_api_v1_model_gateway_role_policies_post'>" in client_source
    assert "body: MutationBody<'register_mcp_server_api_v1_integrations_mcp_register_post'>" in client_source
    assert "body: MutationBody<'update_risk_api_v1_risks__risk_id__patch'>" in client_source


def test_runtime_provider_ui_exposes_healthcheck_state_and_sanitized_reasons() -> None:
    # The runtime provider status table was extracted from the gateway container into its own
    # read-surface panel; the healthcheck/redaction assertions follow it there.
    panel_source = read(SRC / "features" / "model-gateway" / "RuntimeProvidersPanel.tsx")
    generated_types = read(SRC / "api" / "generated" / "openapi.ts")

    assert '"healthStatus"' in generated_types
    assert '"lastError"' in generated_types
    assert "Health status" in panel_source
    assert "Last error" in panel_source
    assert "row.healthStatus" in panel_source
    assert "row.lastError" in panel_source
    assert "redactVisibleSecret(row.reason)" in panel_source
    assert "redactVisibleSecret(row.lastError" in panel_source
    assert "row.healthCheckedAt" in panel_source


def test_agents_page_does_not_fallback_to_unverified_provider_catalogs() -> None:
    source = read(SRC / "features" / "agents" / "AgentsPage.tsx")

    assert "fallbackProviders" not in source
    assert "fallbackRuntimes" not in source
    assert "catalogAvailable" in source
    assert "configuration_required" in source


def test_control_plane_optional_state_does_not_reuse_stale_health_snapshots() -> None:
    source = read(SRC / "hooks" / "useControlPlane.ts")

    assert "retrievalStatus ?? current.retrievalStatus" not in source
    assert "runtimeProviders ?? current.runtimeProviders" not in source
    assert "retrievalStatus," in source
    assert "runtimeProviders," in source


def test_frontend_does_not_invent_cost_or_token_limits_from_null_values() -> None:
    pages_source = read(SRC / "features" / "pages.tsx")
    agents_source = read(SRC / "features" / "agents" / "AgentsPage.tsx")
    # Model-call cost/token rendering moved with the run detail into the Inspector body.
    run_detail_source = read(SRC / "features" / "workflows" / "RunDetail.tsx")

    assert "row.amountUsd ?? 0" not in pages_source
    assert "row.costUsd ?? 0" not in pages_source
    assert "row.costUsd ?? 0" not in run_detail_source
    assert "row.promptTokens ?? 0" not in run_detail_source
    assert "row.completionTokens ?? 0" not in run_detail_source
    # The null-safe label helpers now take a translated fallback arg (i18n), so match
    # "helper(arg," rather than the exact closing paren; the anti-fabrication intent
    # (use the helper, never `?? 0`) is still enforced by the negative checks above.
    assert "tokenLabel(row.promptTokens," in run_detail_source
    assert "costLabel(row.costUsd," in run_detail_source
    assert "maxTokensPerRun ?? 0" not in agents_source
    assert "maxCostPerRun ?? 0" not in agents_source
    assert "numericLabel(row.maxTokensPerRun," in agents_source
    assert "moneyLabel(row.requiresApprovalOverUsd ?? row.maxCostPerRun," in agents_source


def test_governance_surface_has_filtering_and_risk_update_controls() -> None:
    pages_source = read(SRC / "features" / "pages.tsx")

    assert 'id="governance-filter"' in pages_source
    assert 'id="risk-status-filter"' in pages_source
    assert 'id="risk-update-id"' in pages_source
    assert 'id="risk-update-status"' in pages_source
    assert "filteredRisks" in pages_source
    assert "updateRisk(token" in pages_source


def test_visual_guardrails_reject_generic_ai_dashboard_patterns() -> None:
    css_paths = list((SRC / "design-system").glob("*.css"))
    combined = "\n".join(read(path) for path in css_paths if path.exists())

    forbidden_literals = [
        "background-clip: text",
        "-webkit-background-clip: text",
        "radial-gradient",
        "IBM Plex",
        "Inter",
        "#78d8cb",
        "cyan",
        "purple",
    ]
    for literal in forbidden_literals:
        assert literal not in combined, literal

    assert "oklch(" in combined
    assert "Bahnschrift" in combined
    assert "Aptos" in combined
    assert "Cascadia Code" in combined

    side_stripe_pattern = re.compile(
        r"border-(left|right)\s*:\s*(?!0(?:px)?\b|1px\b)(?:[2-9]|\d{2,})px", re.I
    )
    assert side_stripe_pattern.search(combined) is None


def test_web_tooling_has_motion_and_visual_smoke_scripts() -> None:
    package = json.loads(read(ROOT / "package.json"))

    assert "gsap" not in package["dependencies"]
    assert "@gsap/react" not in package["dependencies"]
    assert "@playwright/test" in package["devDependencies"]
    assert "vite" in package["devDependencies"]
    assert package["scripts"]["test:web"] == "node scripts/run-web-tests.mjs"
    assert "corepack" not in package["scripts"]["test:web"]
    assert package["scripts"]["typecheck:web"] == "tsc --noEmit -p local-control-center/web/tsconfig.json"

    web_test_runner = ROOT / "scripts" / "run-web-tests.mjs"
    assert web_test_runner.exists()
    web_test_runner_source = read(web_test_runner)
    assert "cleanup-playwright-webserver.mjs" in web_test_runner_source
    assert "../node_modules/vite/bin/vite.js" in web_test_runner_source
    assert "../node_modules/@playwright/test/cli.js" in web_test_runner_source
    assert "'test'" in web_test_runner_source

    playwright_config = ROOT / "playwright.config.mjs"
    assert playwright_config.exists()
    playwright_source = read(playwright_config)
    assert "-m local_control_center" in playwright_source
    assert "uv run python" in playwright_source

    smoke = ROOT / "tests_web" / "control-center.spec.js"
    assert smoke.exists()
    smoke_source = read(smoke)
    assert "Review board" in smoke_source
    assert "Memory & Retrieval" in smoke_source
    assert "reduced motion" in smoke_source.lower()


def test_web_typescript_config_uses_vite_compatible_resolution() -> None:
    tsconfig = json.loads(read(WEB / "tsconfig.json"))

    compiler_options = tsconfig["compilerOptions"]
    assert compiler_options["strict"] is True
    assert compiler_options["moduleResolution"] == "Bundler"
    assert compiler_options["noEmit"] is True
