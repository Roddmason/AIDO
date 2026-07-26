from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURE_DIR = ROOT / "local-control-center" / "web" / "src" / "features" / "model-gateway"


def test_model_gateway_page_is_split_into_operational_panel_components() -> None:
    required_panels = [
        "ProviderAccountsPanel.tsx",
        "ModelCatalogPanel.tsx",
        "RoutingProfilesPanel.tsx",
        "RoleAssignmentsPanel.tsx",
        "RoutePreviewPanel.tsx",
        "UsageLedgerPanel.tsx",
        "BudgetsPanel.tsx",
        "ProviderLimitsPanel.tsx",
        "RoutingDecisionsPanel.tsx",
        "CliSessionsPanel.tsx",
        "BenchmarksPanel.tsx",
    ]
    page_source = (FEATURE_DIR / "ModelGatewayPage.tsx").read_text(encoding="utf-8")

    for panel in required_panels:
        assert (FEATURE_DIR / panel).exists(), panel
        assert panel.removesuffix(".tsx") in page_source


def test_model_gateway_panels_own_their_tables_and_forms() -> None:
    table_panels = [
        "ProviderAccountsPanel.tsx",
        "ModelCatalogPanel.tsx",
        "RoutingProfilesPanel.tsx",
        "RoleAssignmentsPanel.tsx",
        "UsageLedgerPanel.tsx",
        "BudgetsPanel.tsx",
        "ProviderLimitsPanel.tsx",
        "RoutingDecisionsPanel.tsx",
        "CliSessionsPanel.tsx",
        "BenchmarksPanel.tsx",
    ]
    form_panels = ["RoutePreviewPanel.tsx", "BenchmarksPanel.tsx"]
    page_source = (FEATURE_DIR / "ModelGatewayPage.tsx").read_text(encoding="utf-8")

    for panel in table_panels:
        source = (FEATURE_DIR / panel).read_text(encoding="utf-8")
        assert "DataTable" in source, panel
        assert "<PanelShell" in source, panel
    for panel in form_panels:
        source = (FEATURE_DIR / panel).read_text(encoding="utf-8")
        assert "form-grid" in source, panel
        assert "onSubmit" in source, panel
    assert "<ProviderAccountsPanel>" not in page_source
    assert "<RoutePreviewPanel>" not in page_source
    assert "DataTable rows={gateway.models}" not in page_source
    assert "DataTable rows={gateway.budgetRules}" not in page_source


def test_model_gateway_frontend_surfaces_budget_quota_and_usage_source() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in FEATURE_DIR.glob("*.tsx"))

    assert "budgetResult" in source
    assert "quotaResult" in source
    assert "usageSource" in source
    assert "rawUsage" not in source or "usage_source" not in source


def test_model_gateway_frontend_separates_manual_benchmark_provenance_from_score() -> None:
    benchmark_source = (FEATURE_DIR / "BenchmarksPanel.tsx").read_text(encoding="utf-8")
    route_preview_source = (FEATURE_DIR / "RoutePreviewPanel.tsx").read_text(encoding="utf-8")

    assert "provenance" in benchmark_source
    assert "operator_reported" in benchmark_source
    assert "automated_run" in benchmark_source
    assert "release_validation" in benchmark_source
    assert "Manual/operator-reported" in benchmark_source
    assert "not objective proof" in benchmark_source
    assert "objectiveTasksAttempted" in benchmark_source
    assert "operatorReportedTasks" in benchmark_source
    assert "scoreBreakdown" in route_preview_source
    assert "benchmarkOperatorReportedSampleCount" in route_preview_source


def test_model_gateway_cost_surfaces_preserve_unknown_instead_of_zero() -> None:
    utils_source = (FEATURE_DIR / "utils.tsx").read_text(encoding="utf-8")
    page_source = (FEATURE_DIR / "ModelGatewayPage.tsx").read_text(encoding="utf-8")
    provider_accounts_source = (FEATURE_DIR / "ProviderAccountsPanel.tsx").read_text(encoding="utf-8")
    app_source = (ROOT / "local-control-center" / "web" / "src" / "app" / "App.tsx").read_text(
        encoding="utf-8"
    )
    catalog_source = (ROOT / "local_control_center" / "i18n" / "default_catalog.json").read_text(
        encoding="utf-8"
    )

    assert "Number(value ?? 0)" not in utils_source
    assert "Number(value ?? 0)" not in page_source
    assert "row.amountUsd ?? 0" not in page_source
    assert "row.amountUsd ?? 0" not in app_source
    assert "usdToday" not in app_source
    assert "USD today" not in catalog_source
    assert "money(0)" not in provider_accounts_source
    assert "cost unavailable" in provider_accounts_source


def test_role_policy_token_floor_matches_backend_validator() -> None:
    # El backend acepta maxTokensPerRun=0 ("sin limite", model_router.py convierte 0 -> None);
    # el formulario de role policies no debe imponer un piso mas alto que el validador real.
    page_source = (FEATURE_DIR / "ModelGatewayPage.tsx").read_text(encoding="utf-8")

    assert "tokenLimit < 0 || tokenLimit > 2000000" in page_source
    assert "tokenLimit < 512" not in page_source
    assert 'min="512"' not in page_source
