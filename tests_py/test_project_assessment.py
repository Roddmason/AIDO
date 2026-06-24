from __future__ import annotations

from pathlib import Path

from local_control_center.projects.assessment import assess_project, run_project_assessment
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

ASSESSMENT_TABLES = {"project_assessments", "project_findings"}
EXPECTED_CATEGORIES = {
    "stack",
    "module",
    "architecture",
    "endpoint",
    "data",
    "test",
    "coverage",
    "quality_command",
    "debt",
    "security",
    "documentation",
    "git_history",
    "feature",
    "risk",
    "gap",
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_sample_project(root: Path) -> None:
    _write(
        root / "pyproject.toml", "[project]\nname = 'sample'\n\n[tool.ruff]\n\n[tool.pytest.ini_options]\n"
    )
    _write(root / "package.json", '{"name": "sample", "scripts": {"lint": "biome", "test": "vitest"}}')
    _write(root / "biome.json", "{}")
    _write(root / ".coveragerc", "[run]\nbranch = True\n")
    _write(root / ".gitleaks.toml", "title = 'gitleaks'\n")
    _write(root / "SECURITY.md", "# Security policy\n")
    _write(root / ".env", "SECRET=should-not-be-committed\n")
    _write(root / "README.md", "# Sample\n\n## Onboarding\n\nGuided onboarding.\n\n## Billing\n\nInvoices.\n")
    _write(root / "docs" / "guide.md", "# Guide\n")
    _write(root / "schema.sql", "CREATE TABLE users (id TEXT);\n")
    _write(root / "src" / "app" / "__init__.py", '"""App package."""\n')
    _write(
        root / "src" / "app" / "api.py",
        '"""API."""\n\nrouter = object()\n\n\n@router.get("/health")\ndef health():  # TODO: add auth; FIXME later\n    return {}\n',
    )
    _write(root / "src" / "app" / "repository.py", '"""Repo."""\n')
    _write(root / "src" / "app" / "models.py", '"""Models."""\n')
    _write(root / "src" / "app" / "migrations.py", '"""Migrations."""\n')
    _write(root / "tests" / "test_app.py", "def test_ok():\n    assert True\n")
    # A minimal, real git reflog (no git execution needed to read it).
    zero = "0" * 40
    sha1 = "a" * 40
    sha2 = "b" * 40
    _write(
        root / ".git" / "logs" / "HEAD",
        f"{zero} {sha1} Dev <d@x.com> 1718000000 +0000\tcommit (initial): Initial commit\n"
        f"{sha1} {sha2} Dev <d@x.com> 1718000100 +0000\tcommit: Add onboarding\n",
    )


def test_assessment_schema_adds_tables_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase18_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 18"
        ).fetchone()["total"]

    assert tables >= ASSESSMENT_TABLES
    assert phase18_rows == 1


def test_assess_project_detects_every_dimension(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)

    result = assess_project(project_root)

    assert result["source"] == "static_analysis"  # no tools executed; static read-only
    categories = {finding["category"] for finding in result["findings"]}
    assert categories >= EXPECTED_CATEGORIES
    summary = result["summary"]
    assert "Python" in summary["stack"]
    assert summary["hasTests"] is True
    assert summary["hasGitHistory"] is True
    assert summary["totalEndpoints"] >= 1
    assert summary["debtMarkers"] >= 2  # TODO + FIXME


def test_list_project_findings_is_stable_by_insertion_order_on_timestamp_ties(tmp_path: Path) -> None:
    # Every finding of one assessment shares the same millisecond created_at; they must come back in
    # insertion order, not by their random uuid id (the documented "orden de inserción" contract).
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        same_ts = "2026-01-01T00:00:00.000Z"
        expected_order = ["stack", "module", "risk", "gap"]
        for index, category in enumerate(expected_order):
            # The later-inserted row gets a lexically smaller id than the earlier one.
            connection.execute(
                """
                INSERT INTO project_findings
                    (id, assessment_id, project_id, category, title, detail, severity, evidence,
                     confidence, metadata, created_at)
                VALUES (?, 'assessment-1', 'project-1', ?, ?, '', 'info', '', 'medium', '{}', ?)
                """,
                (f"project-finding-{len(expected_order) - index:02d}", category, category, same_ts),
            )

        findings = ProjectsRepository(connection).list_project_findings(assessment_id="assessment-1")
        assert [finding["category"] for finding in findings] == expected_order


def test_run_project_assessment_persists_assessment_and_findings(tmp_path: Path) -> None:
    project_root = tmp_path / "sample"
    build_sample_project(project_root)

    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = ProjectsRepository(connection)
        project = repository.create_project(name="Sample", path=project_root, template_id="other")

        run = run_project_assessment(repository, project_id=project["id"], root_path=project_root)
        assessment = run["assessment"]

        assert assessment["source"] == "static_analysis"
        assert assessment["status"] == "completed"
        assert assessment["riskCount"] >= 1  # committed .env
        assert assessment["gapCount"] >= 1  # package.json without lockfile

        stored = repository.list_project_assessments(project["id"])
        assert [item["id"] for item in stored] == [assessment["id"]]

        all_findings = repository.list_project_findings(project_id=project["id"])
        assert len(all_findings) == assessment["findingsCount"] == len(run["findings"])
        assert all(finding["assessmentId"] == assessment["id"] for finding in all_findings)
        assert {finding["category"] for finding in all_findings} >= EXPECTED_CATEGORIES

        # Findings are queryable per dimension and stay project-scoped.
        git_findings = repository.list_project_findings(
            assessment_id=assessment["id"], category="git_history"
        )
        assert len(git_findings) == 2
        risks = repository.list_project_findings(project_id=project["id"], category="risk")
        assert any("env" in finding["title"].lower() for finding in risks)
        security = repository.list_project_findings(project_id=project["id"], category="security")
        assert any(finding["severity"] == "high" for finding in security)  # committed .env flagged
