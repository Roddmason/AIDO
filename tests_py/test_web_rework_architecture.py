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


def test_settings_team_section_renders_full_available_agent_profile_roster() -> None:
    settings_page = read(SRC / "features" / "settings" / "SettingsPage.tsx")
    sections = read(SRC / "features" / "settings" / "sections.tsx")
    project_team = read(SRC / "features" / "settings" / "ProjectTeamPanel.tsx")

    # Default Team (general) renders the full roster from the overview; the project Team
    # section fetches with the project id so per-project overrides are applied. Neither
    # surface may hardcode roles or reuse a generic shared body for Team/Routing/Quality.
    assert "id: 'team'" in sections
    assert "<ProjectTeamPanel projectId={ctx.scopeId}" in sections
    assert "<DefaultTeamBody overview={ctx.overview}" in sections
    assert "AgentsBody" not in sections
    assert "overview.agentProfiles" in settings_page
    assert "runtimeAvailability" in settings_page
    assert "allowedProviders" in settings_page
    assert "mobile_engineer" not in settings_page
    assert "getAgentProfiles(projectId" in project_team
    assert "showOverride" in project_team


def test_settings_registry_has_no_placeholder_sections() -> None:
    sections = read(SRC / "features" / "settings" / "sections.tsx")

    assert "SectionPlaceholder" not in sections
    assert "kind: 'placeholder'" not in sections
    assert not (SRC / "features" / "settings" / "SectionPlaceholder.tsx").exists()


def test_settings_target_sections_render_dedicated_card_bodies() -> None:
    settings_dir = SRC / "features" / "settings"
    sections = read(settings_dir / "sections.tsx")

    # Research, Goal, Internet, Routing and Quality no longer fall back to the generic
    # wired list: each renders its own body built from modern cards, and none of the
    # Team/Routing/Quality surfaces reuse a shared AgentsBody.
    for component in (
        "ResearchBody",
        "GoalBody",
        "InternetBody",
        "RoutingSettings",
        "QualityGateSettings",
    ):
        assert f"<{component} ctx={{ctx}}" in sections, component
        assert (settings_dir / f"{component}.tsx").exists(), component

    assert "AgentsBody" not in sections

    # The headline enum of each policy body is an accessible radio-card grid, not a
    # bare dropdown; the shared selector renders the radiogroup semantics.
    choice_cards = read(settings_dir / "SettingsChoiceCards.tsx")
    assert 'role="radiogroup"' in choice_cards
    assert 'role="radio"' in choice_cards
    for body in ("ResearchBody", "GoalBody", "InternetBody", "RoutingSettings"):
        assert "SettingChoiceCards" in read(settings_dir / f"{body}.tsx"), body


def test_settings_hash_aliases_resolve_to_existing_section_ids() -> None:
    routing = read(SRC / "app" / "routing.ts")
    sections = read(SRC / "features" / "settings" / "sections.tsx")

    # Every legacy `#settings-*` hash must land on a real section id; renaming or
    # removing a section without updating settingsHashToSection silently redirects
    # deep-links to the fallback section.
    map_block = routing.split("settingsHashToSection", 1)[1].split("};", 1)[0]
    targets = set(re.findall(r":\s*'([a-z-]+)'", map_block))
    section_ids = set(re.findall(r"\bid: '([a-z-]+)'", sections))

    assert targets, "settings hash alias map must not be empty"
    assert sorted(targets - section_ids) == []


def test_stable_frontend_artifact_and_retrieval_surfaces_are_not_dictionary_typed() -> None:
    types_source = read(SRC / "api" / "types.ts")
    client_source = read(SRC / "api" / "client.ts")
    hook_source = read(SRC / "hooks" / "useControlPlane.ts")
    artifacts_source = read(SRC / "lib" / "artifacts.ts")
    pages_source = read(SRC / "features" / "pages.tsx")
    # The workflow artifact preview now lives in the run-detail Inspector body, not the launcher page.
    run_detail_source = read(SRC / "features" / "workflows" / "RunDetail.tsx")
    # MemoryPage was extracted out of the pages barrel into its own feature module.
    memory_source = read(SRC / "features" / "memory" / "MemoryPage.tsx")

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
    assert "retrievalStatus: RetrievalStatus | null" in memory_source
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


def test_thread_remediation_answer_question_uses_option_payload() -> None:
    card_source = read(SRC / "features" / "shell" / "ThreadBlockerCard.tsx")
    hook_source = read(SRC / "features" / "shell" / "useThreadRemediations.ts")

    assert "function answerOptions(action: BlockerActionModel): string[]" in card_source
    assert "action.remediation?.actionType === 'answer_question' && options.length" in card_source
    assert "<SelectField" in card_source
    assert "runExecute(action, { answer: selectedAnswer })" in card_source
    assert "payload?: JsonObject" in hook_source
    assert "executeRemediation(token, action.remediation?.id ?? '', payload)" in hook_source


def test_thread_remediation_continue_plan_only_is_visible_and_executable() -> None:
    presentation_source = read(SRC / "features" / "shell" / "remediationPresentation.ts")
    hook_source = read(SRC / "features" / "shell" / "useThreadRemediations.ts")

    assert "continue_plan_only: {" in presentation_source
    assert "labelFallback: 'Continue plan-only'" in presentation_source
    assert "kind: 'execute'" in presentation_source
    assert "executeRemediation(token, action.remediation?.id ?? '', payload)" in hook_source


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


def test_runtime_setup_wizard_uses_preconfigured_base_url_for_known_providers() -> None:
    setup_source = read(SRC / "features" / "runtime-setup" / "runtimeSetup.ts")
    wizard_source = read(SRC / "features" / "runtime-setup" / "AddProviderWizard.tsx")

    assert "id: 'nvidia_nim'" in setup_source
    assert "defaultBaseUrl: 'https://integrate.api.nvidia.com/v1'" in setup_source
    assert "needsBaseUrl: false" in setup_source
    assert "entry.needsBaseUrl ? (" in wizard_source
    assert "Preconfigured — no URL needed." in wizard_source
    assert "baseUrl.trim() || entry.defaultBaseUrl || ''" in wizard_source


def test_provider_credentials_remediation_opens_specific_runtime_setup_provider() -> None:
    presentation_source = read(SRC / "features" / "shell" / "remediationPresentation.ts")
    card_source = read(SRC / "features" / "shell" / "ThreadBlockerCard.tsx")
    app_source = read(SRC / "app" / "App.tsx")
    modal_source = read(SRC / "features" / "settings" / "SettingsModal.tsx")
    sections_source = read(SRC / "features" / "settings" / "sections.tsx")
    settings_source = read(SRC / "features" / "settings" / "SettingsPage.tsx")
    runtime_setup_source = read(SRC / "features" / "runtime-setup" / "RuntimeSetupPanel.tsx")

    assert "contextualSettingsRecord" in presentation_source
    assert "remediation: contextualSettingsRecord" in presentation_source
    assert "function settingsProviderId(action: BlockerActionModel): string | undefined" in card_source
    assert "onOpenSettings(action.section, settingsProviderId(action))" in card_source
    assert "const [settingsProviderId, setSettingsProviderId]" in app_source
    assert "openSettings = useCallback((section?: string, providerId?: string)" in app_source
    assert "initialProviderId={settingsProviderId}" in app_source
    assert "initialProviderId?: string | null" in modal_source
    assert "initialProviderId: initialProviderId ?? null" in modal_source
    assert "initialProviderId={ctx.initialProviderId}" in sections_source
    assert "initialProviderId={initialProviderId}" in settings_source
    assert "initialProviderId?: string | null" in runtime_setup_source
    assert "setWizardProviderId(providerId)" in runtime_setup_source


def test_new_thread_composer_checks_similarity_and_records_operator_choice() -> None:
    source = read(SRC / "features" / "shell" / "ThreadConversation.tsx")

    assert "findSimilarThreads(project.id, query, 1, controller.signal)" in source
    assert "top && top.score >= SIMILARITY_THRESHOLD ? top : null" in source
    assert "const dismissedCandidate = candidate" in source
    assert "createThread(mutateToken, {" in source
    assert "postThreadMessage(mutateToken, created.thread.id, {" in source
    assert "mode: 'create_new_anyway'" in source
    assert "similarThreadId: dismissedCandidate.threadId" in source
    assert "markSimilarThread(mutateToken, created.thread.id, dismissedCandidate.threadId" in source
    assert "postThreadMessage(mutateToken, target, { content, metadata: { mode } })" in source


def test_thread_remediation_presentation_has_specific_copy_for_product_loop_blockers() -> None:
    source = read(SRC / "features" / "shell" / "remediationPresentation.ts")

    for blocker_type in (
        "resource_manager_unconfigured",
        "resource_manager_approval_required",
        "team_scheduler_failed",
        "technical_lead_planning_failed",
        "product_owner_output_invalid",
        "research_required",
        "workspace_root_missing",
        "workspace_allocation_failed",
        "review_diff_unavailable",
        "approval_unavailable",
        "resource_learning_failed",
        "git_status_failed",
        "project_assessment_failed",
        "functionality_memory_decision_required",
        "thread_similarity_decision_required",
        "thread_intake_decision_required",
    ):
        assert f"{blocker_type}:" in source


def test_thread_remediation_presentation_is_typed_against_backend_contract() -> None:
    source = read(SRC / "features" / "shell" / "remediationPresentation.ts")

    assert "type BlockerType = RemediationActionRecord['blockerType'];" in source
    assert "type ActionType = RemediationActionRecord['actionType'];" in source
    assert "export const BLOCKER_COPY: Record<BlockerType, BlockerCopy>" in source
    assert "export const ACTION_COPY: Record<ActionType, ActionCopy>" in source


def test_workbench_review_panel_wires_traceable_product_loop_delivery_feedback() -> None:
    client_source = read(SRC / "api" / "client.ts")
    page_source = read(SRC / "features" / "workbench" / "WorkbenchPage.tsx")
    panel_source = read(SRC / "features" / "workbench" / "panels" / "WorkbenchEvidencePanel.tsx")

    assert "export type ProductLoopFeedbackRequest" in client_source
    assert "export function applyProductLoopFeedback" in client_source
    assert "applyProductLoopFeedback(token, project.id, activeProductLoop.id" in page_source
    assert "handleAcceptDelivery" in page_source
    assert "handleRequestDeliveryChanges" in page_source
    assert "handleContinueDelivery" in page_source
    assert "onAcceptDelivery={handleAcceptDelivery}" in page_source
    assert "onRequestChanges={handleRequestDeliveryChanges}" in page_source
    assert "onContinueDelivery={handleContinueDelivery}" in page_source
    assert "action: 'continue'" in page_source
    assert "targetType: 'task'" in page_source
    assert "targetId: taskId" in page_source
    assert "selectedTaskId" in panel_source
    assert "SelectField" in panel_source
    assert "onRequestChanges?.(selectedTaskId" in panel_source
    assert "onContinueDelivery?.(" in panel_source
    assert "disabled={!canContinueDelivery" in panel_source
    assert "app.workbench.evidence.continueDelivery" in panel_source
    assert "disabled={!canRequestChanges" in panel_source
    assert "activeProductLoop?.state === 'awaiting_feedback'" in panel_source
    assert "activeProductLoop?.state === 'awaiting_approval' &&" in panel_source


def test_agents_page_does_not_fallback_to_unverified_provider_catalogs() -> None:
    source = read(SRC / "features" / "agents" / "AgentsPage.tsx")

    assert "fallbackProviders" not in source
    assert "fallbackRuntimes" not in source
    assert "catalogAvailable" in source
    assert "configuration_required" in source


def test_agents_page_exposes_team_panel_runtime_blockers_and_project_overrides() -> None:
    source = read(SRC / "features" / "agents" / "AgentsPage.tsx")
    api_client = read(SRC / "api" / "client.ts")
    generated = read(SRC / "api" / "generated" / "openapi.ts")

    assert "Team panel" in source
    assert "runtimeAvailability" in source
    assert "blockedReason" in source
    assert "projectOverride" in source
    assert "selectedProjectId" in source
    assert "upsertAgentProfileProjectOverride" in source
    assert (
        "agent_profile_override_api_v1_projects__project_id__agent_profile_overrides__profile_id__put"
        in generated
    )
    assert "upsertAgentProfileProjectOverride" in api_client


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
