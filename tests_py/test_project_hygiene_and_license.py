from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_open_source_license_and_audit_docs_are_explicit() -> None:
    license_text = read("LICENSE")
    notice = read("NOTICE")
    third_party = read("THIRD_PARTY_NOTICES.md")
    architecture_audit = read("docs/architecture-audit.md")
    license_audit = read("docs/license-audit.md")

    assert "MIT License" in license_text
    assert "Permission is hereby granted" in license_text
    assert "open source" in notice.lower()
    assert "AIDO" in notice
    assert "third-party" in third_party.lower()
    assert "source artifacts" in architecture_audit.lower()
    assert "local-control-center/dist" in architecture_audit
    assert "MIT" in license_audit
    assert "faiss-cpu" in license_audit
    assert "optional" in license_audit.lower()


def test_aido_current_state_docs_exist_and_use_real_gateway_state() -> None:
    current_state = read("docs/aido-current-state-analysis.md")
    architecture = read("docs/aido-architecture-diagram.md")
    gaps = read("docs/aido-gap-analysis.md")
    plan = read("docs/aido-implementation-plan.md")

    assert "Unified Model & Runtime Gateway" in current_state
    assert "203 passed" in current_state
    assert "metadata_json" in current_state
    assert "usage_source" in current_state
    assert "```mermaid" in architecture
    for title in (
        "Diagrama 1",
        "Diagrama 2",
        "Diagrama 3",
        "Diagrama 4",
    ):
        assert title in architecture
    for heading in (
        "Ya existe",
        "Falta crear",
        "Falta ajustar",
        "Falta endurecer",
        "Falta documentar",
        "Falta probar",
    ):
        assert heading in gaps
    assert "codex/aido-control-plane-hardening" in plan
    assert "No reconstruir el gateway" in plan


def test_requirements_python_keeps_faiss_optional() -> None:
    requirements = read("local-control-center/requirements-python.txt")
    required_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert not any(line.lower().startswith("faiss-cpu") for line in required_lines)
    assert "faiss-cpu" in requirements
    assert "uv sync --extra faiss" in requirements


def test_package_metadata_declares_open_source_license() -> None:
    package = json.loads(read("package.json"))

    assert package["private"] is False
    assert package["license"] == "MIT"


def test_frontend_node24_environment_is_documented_and_guarded() -> None:
    package = json.loads(read("package.json"))
    nvmrc = read(".nvmrc").strip()
    readme = read("README.md")
    use_node = read("local-control-center/scripts/use-node.ps1")

    assert package["engines"]["node"] == ">=24.16.0 <25.0.0"
    assert nvmrc == "24.16.0"
    assert "Node >=24.16.0 <25.0.0" in readme
    assert "pnpm@10.24.0" in readme
    assert ".nvmrc" in use_node
    assert "nvm install $nodeVersion" in use_node
    assert "nvm use $nodeVersion" in use_node


def test_open_source_governance_files_require_owner_review() -> None:
    contributing = read("CONTRIBUTING.md")
    security = read("SECURITY.md")
    codeowners = read(".github/CODEOWNERS")
    pull_request_template = read(".github/PULL_REQUEST_TEMPLATE.md")
    bug_report = read(".github/ISSUE_TEMPLATE/bug_report.yml")
    feature_request = read(".github/ISSUE_TEMPLATE/feature_request.yml")

    assert "pull requests" in contributing.lower()
    assert "owner review" in contributing.lower()
    assert "report security issues privately" in security.lower()
    assert codeowners.strip() == "* @Roddmason"
    assert "testing" in pull_request_template.lower()
    assert "no secrets" in pull_request_template.lower()
    assert "labels:" in bug_report
    assert "labels:" in feature_request


def test_gitignore_excludes_generated_artifacts_and_keeps_env_example() -> None:
    gitignore = read(".gitignore")

    required_patterns = [
        "node_modules/",
        "src/",
        "src/node_modules/",
        "local-control-center/dist/",
        "__pycache__/",
        ".pytest_cache/",
        ".tmp/",
        "test-results/",
        ".env",
        "!.env.example",
    ]
    for pattern in required_patterns:
        assert pattern in gitignore


def test_removed_typescript_source_tree_is_not_present_in_clean_workspace() -> None:
    assert not (ROOT / "src").exists()


def test_quality_and_security_scripts_are_declared() -> None:
    package = json.loads(read("package.json"))
    scripts = package["scripts"]

    for script in (
        "lint",
        "lint:py",
        "security:licenses:py",
        "security:licenses:js",
        "security:secrets",
        "security:sast",
        "security:credentials:preflight",
        "quality",
    ):
        assert script in scripts

    assert "ruff check" in scripts["lint:py"]
    assert "python -X utf8 -m piplicenses" in scripts["security:licenses:py"]
    assert "licenses list" in scripts["security:licenses:js"]
    assert "gitleaks detect" in scripts["security:secrets"]
    assert "semgrep scan" in scripts["security:sast"]
    assert "check-credentials.py" in scripts["security:credentials:preflight"]


def test_quality_scripts_include_web_typecheck() -> None:
    package = json.loads(read("package.json"))
    scripts = package["scripts"]
    quality_local = read("scripts/quality-local.ps1")

    assert "typecheck:web" in scripts
    assert "typecheck:web" in scripts["test:all"]
    assert "typecheck:web" in quality_local
    assert "security:secrets" in quality_local
    assert "security:sast" in quality_local


def test_native_process_start_command_is_cross_platform() -> None:
    package = json.loads(read("package.json"))
    scripts = package["scripts"]
    launcher = read("local-control-center/scripts/start_control_center.py")
    readme = read("README.md")

    assert "start" in scripts
    assert "start:py" in scripts
    assert "start:windows" in scripts
    assert "start_control_center.py" in scripts["start"]
    assert "start_control_center.py" in scripts["start:py"]
    assert "powershell" not in scripts["start"].lower()
    assert "start-control-center.ps1" in scripts["start:windows"]
    assert "sys.path.insert(0, str(ROOT))" in launcher
    assert "AIDO_ENABLE_REAL_PROVIDER_CALLS" in launcher
    assert "AIDO_ENABLE_CLI_RUNTIMES" in launcher
    assert "--no-build" in launcher
    assert "Windows, Linux and macOS" in readme
    assert "uv run python local-control-center/scripts/start_control_center.py" in readme


def test_runtime_smoke_scripts_are_declared_for_release_validation() -> None:
    package = json.loads(read("package.json"))
    scripts = package["scripts"]

    assert "smoke:runtime:preflight" in scripts
    assert "smoke-runtime-adapters.ps1 -PreflightOnly" in scripts["smoke:runtime:preflight"]
    assert "-ReportPath .tmp/runtime-validation/preflight.json" in scripts["smoke:runtime:preflight"]
    assert "smoke:runtime:release:preflight" in scripts
    assert "AIDO_RUNTIME_RELEASE_VALIDATION='1'" in scripts["smoke:runtime:release:preflight"]
    assert "smoke-runtime-adapters.ps1' -PreflightOnly" in scripts["smoke:runtime:release:preflight"]
    assert (
        "-ReportPath .tmp/runtime-validation/release-preflight.json"
        in scripts["smoke:runtime:release:preflight"]
    )
    assert "smoke:runtime:release" in scripts
    assert "AIDO_RUNTIME_SMOKE='1'" in scripts["smoke:runtime:release"]
    assert "AIDO_RUNTIME_RELEASE_VALIDATION='1'" in scripts["smoke:runtime:release"]
    assert "smoke-runtime-adapters.ps1" in scripts["smoke:runtime:release"]
    assert " -PreflightOnly " in scripts["smoke:runtime:release"]
    assert "-ReportPath .tmp/runtime-validation/release-preflight.json" in scripts["smoke:runtime:release"]
    assert "-ReportPath .tmp/runtime-validation/release.json" in scripts["smoke:runtime:release"]


def test_python_quality_tooling_is_declared_for_uv() -> None:
    import tomllib

    pyproject = tomllib.loads(read("pyproject.toml"))
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    for dependency in ("ruff", "pre-commit", "pip-licenses", "semgrep"):
        assert any(item.startswith(dependency) for item in optional_dependencies["dev"]), dependency

    assert "ruff" in pyproject["tool"]


def test_semgrep_local_rules_cover_core_security_risks() -> None:
    semgrep = read(".semgrep.yml")

    for rule_id in (
        "python-dangerous-shell-without-policy-comment",
        "python-subprocess-shell-true",
        "python-plaintext-secret-persistence",
        "python-runtime-adapter-bypass",
    ):
        assert f"id: {rule_id}" in semgrep
