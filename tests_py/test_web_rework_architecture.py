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
    assert "small team" in source.lower() or "equipo pequeno" in source.lower() or "equipo pequeño" in source.lower()
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
        "overview",
        "jobs-approvals",
        "memory",
        "agents",
        "workflows",
        "integrations",
    ]

    for feature in expected:
        assert (SRC / "features" / feature).is_dir(), feature


def test_dashboard_reads_v1_overview_without_removed_state_contract() -> None:
    web_sources = "\n".join(
        read(path)
        for path in SRC.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx"}
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
    for stale_manual_type in [
        "export type Project = {",
        "export type Job = {",
        "export type Workflow = {",
        "export type AgentProfile = {",
        "export type Pipeline = {",
        "export type RuntimeProviders = {",
    ]:
        assert stale_manual_type not in source


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

    side_stripe_pattern = re.compile(r"border-(left|right)\s*:\s*(?!0(?:px)?\b|1px\b)(?:[2-9]|\d{2,})px", re.I)
    assert side_stripe_pattern.search(combined) is None


def test_web_tooling_has_motion_and_visual_smoke_scripts() -> None:
    package = json.loads(read(ROOT / "package.json"))

    assert "gsap" not in package["dependencies"]
    assert "@gsap/react" not in package["dependencies"]
    assert "@playwright/test" in package["devDependencies"]
    assert "vite" in package["devDependencies"]
    assert package["scripts"]["test:web"] == "corepack pnpm@10.24.0 run build:control-center && playwright test"

    playwright_config = ROOT / "playwright.config.mjs"
    assert playwright_config.exists()
    assert "uv run python -m local_control_center" in read(playwright_config)

    smoke = ROOT / "tests_web" / "control-center.spec.js"
    assert smoke.exists()
    smoke_source = read(smoke)
    assert "Jobs & Approvals" in smoke_source
    assert "Memory & Retrieval" in smoke_source
    assert "reduced motion" in smoke_source.lower()
