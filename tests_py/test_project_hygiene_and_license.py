from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_private_license_and_audit_docs_are_explicit() -> None:
    license_text = read("LICENSE")
    notice = read("NOTICE")
    third_party = read("THIRD_PARTY_NOTICES.md")
    architecture_audit = read("docs/architecture-audit.md")
    license_audit = read("docs/license-audit.md")

    assert "AIDO" in license_text
    assert "No Commercial Use" in license_text
    assert "not open source" in license_text.lower()
    assert "AIDO" in notice
    assert "third-party" in third_party.lower()
    assert "source artifacts" in architecture_audit.lower()
    assert "local-control-center/dist" in architecture_audit
    assert "faiss-cpu" in license_audit
    assert "optional" in license_audit.lower()


def test_package_metadata_declares_private_noncommercial_license() -> None:
    package = json.loads(read("package.json"))

    assert package["private"] is True
    assert package["license"] == "UNLICENSED"


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
        "quality",
    ):
        assert script in scripts

    assert "ruff check" in scripts["lint:py"]
    assert "python -X utf8 -m piplicenses" in scripts["security:licenses:py"]
    assert "licenses list" in scripts["security:licenses:js"]
    assert "gitleaks detect" in scripts["security:secrets"]
    assert "semgrep scan" in scripts["security:sast"]


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
