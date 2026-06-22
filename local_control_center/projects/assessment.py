"""Assessment estático y real de un proyecto: detecta su perfil técnico leyendo sus archivos.

Amplía el descubrimiento de ``discovery`` a un assessment por dimensiones (stack, módulos,
arquitectura, endpoints, datos, tests, cobertura, quality commands, deuda, seguridad, documentación,
git history acotado y funcionalidades) y deriva riesgos y gaps. Es estrictamente de solo lectura: no
ejecuta ninguna herramienta (la historia de git se lee del reflog ``.git/logs/HEAD``), por lo que
respeta el límite de no ejecutar nada fuera del ToolBroker. ``run_project_assessment`` persiste el
assessment y un hallazgo por detección en ``project_assessments`` y ``project_findings``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .discovery import discover_project_path
from .repository import ProjectsRepository

FINDING_CATEGORIES = {
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
SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs"}
PRUNE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "target",
    ".next",
    "coverage",
    ".idea",
    ".vscode",
}
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 6000
MAX_ENDPOINT_FINDINGS = 25
MAX_MODULE_FINDINGS = 40
MAX_FEATURE_FINDINGS = 12
MAX_GIT_COMMITS = 10
DEBT_RISK_THRESHOLD = 25
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|[^/]+\.(test|spec)\.(ts|tsx|js|jsx))$")
ENDPOINT_RE = re.compile(
    r"@(?:app|router|api|blueprint)\.(?:get|post|put|patch|delete)\(|"
    r"@app\.route\(|"
    r"\b(?:app|router)\.(?:get|post|put|patch|delete)\(",
    re.IGNORECASE,
)
DEBT_RE = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b")


def _read_bounded(path: Path) -> str:
    try:
        return path.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return ""


def _finding(
    category: str,
    title: str,
    *,
    detail: str = "",
    severity: str = "info",
    evidence: str = "",
    confidence: str = "medium",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "title": title,
        "detail": detail,
        "severity": severity,
        "evidence": evidence,
        "confidence": confidence,
        "metadata": metadata or {},
    }


def _iter_all_files(root: Path) -> list[Path]:
    """Lista las rutas relativas de archivos del proyecto, podando directorios pesados y ocultos."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in PRUNE_DIRS and not name.startswith(".")]
        for filename in filenames:
            files.append((Path(dirpath) / filename).relative_to(root))
            if len(files) >= MAX_FILES:
                return files
    return files


def _scan_source(root: Path, files: list[Path]) -> dict[str, Any]:
    endpoints: dict[str, int] = {}
    debt_count = 0
    debt_files: list[str] = []
    for relative in files:
        if relative.suffix not in SOURCE_EXTS:
            continue
        text = _read_bounded(root / relative)
        if not text:
            continue
        route_hits = len(ENDPOINT_RE.findall(text))
        if route_hits:
            endpoints[relative.as_posix()] = route_hits
        debt_hits = len(DEBT_RE.findall(text))
        if debt_hits:
            debt_count += debt_hits
            if len(debt_files) < 10:
                debt_files.append(relative.as_posix())
    return {"endpoints": endpoints, "debtCount": debt_count, "debtFiles": debt_files}


def _detect_stack(root: Path) -> list[dict[str, Any]]:
    discovery = discover_project_path(root)
    return [
        _finding(
            "stack",
            str(runtime.get("label") or runtime.get("id")),
            detail=f"Detected runtime '{runtime.get('id')}' ({runtime.get('kind')}).",
            evidence=str(runtime.get("manifest") or ""),
            confidence="high",
            metadata={"runtimeId": runtime.get("id"), "kind": runtime.get("kind")},
        )
        for runtime in discovery.get("detectedRuntimes", [])
    ]


def _detect_modules(files: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for relative in files:
        if relative.name != "__init__.py" or len(relative.parts) > 3:
            continue
        package = relative.parent
        findings.append(
            _finding(
                "module",
                package.as_posix(),
                detail="Python package detected by __init__.py.",
                evidence=relative.as_posix(),
            )
        )
        if len(findings) >= MAX_MODULE_FINDINGS:
            break
    return findings


def _detect_architecture(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if (root / "package.json").exists() and (root / "pyproject.toml").exists():
        findings.append(
            _finding(
                "architecture",
                "Polyglot codebase (JavaScript + Python)",
                detail="Both a package.json and a pyproject.toml are present at the root.",
                evidence="package.json, pyproject.toml",
                confidence="high",
            )
        )
    if (root / "src").is_dir() and (root / "tests").is_dir():
        findings.append(
            _finding(
                "architecture",
                "src/tests layout",
                detail="Source lives under src/ with a sibling tests/ directory.",
                evidence="src/, tests/",
            )
        )
    layered = [
        relative.parent.as_posix()
        for relative in files
        if relative.name == "repository.py"
        and (root / relative.parent / "api.py").exists()
        and (root / relative.parent / "models.py").exists()
    ]
    if layered:
        findings.append(
            _finding(
                "architecture",
                "Layered slices (api/repository/models)",
                detail=f"{len(layered)} module(s) expose an api/repository/models split.",
                evidence=", ".join(layered[:5]),
                confidence="high",
                metadata={"modules": layered[:MAX_MODULE_FINDINGS]},
            )
        )
    return findings


def _endpoint_findings(source_scan: dict[str, Any]) -> list[dict[str, Any]]:
    endpoints: dict[str, int] = source_scan["endpoints"]
    return [
        _finding(
            "endpoint",
            relative_path,
            detail=f"{count} route handler(s) detected.",
            evidence=relative_path,
            metadata={"routeCount": count},
        )
        for relative_path, count in sorted(endpoints.items())[:MAX_ENDPOINT_FINDINGS]
    ]


def _detect_data(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    migrations = [relative.as_posix() for relative in files if relative.name == "migrations.py"][:5]
    if migrations or (root / "migrations").is_dir() or (root / "alembic").is_dir():
        findings.append(
            _finding(
                "data",
                "Schema migrations",
                detail="Migration definitions detected; the project owns an evolving schema.",
                evidence=", ".join(migrations) or "migrations/",
                confidence="high",
            )
        )
    sql = [relative.as_posix() for relative in files if relative.suffix == ".sql"][:5]
    if sql:
        findings.append(
            _finding(
                "data", "SQL assets", detail=f"{len(sql)} SQL file(s) detected.", evidence=", ".join(sql)
            )
        )
    return findings


def _detect_tests(files: list[Path]) -> list[dict[str, Any]]:
    test_files = [relative.as_posix() for relative in files if TEST_FILE_RE.search(relative.as_posix())]
    if not test_files:
        return []
    return [
        _finding(
            "test",
            f"{len(test_files)} test file(s)",
            detail="Automated test files detected by naming convention.",
            evidence=", ".join(test_files[:5]),
            confidence="high",
            metadata={"count": len(test_files)},
        )
    ]


def _detect_coverage(root: Path) -> list[dict[str, Any]]:
    if (root / ".coveragerc").exists() or (root / ".nycrc").exists() or (root / "codecov.yml").exists():
        return [
            _finding(
                "coverage",
                "Coverage configuration",
                detail="A coverage configuration file is present.",
                evidence=".coveragerc/.nycrc/codecov.yml",
            )
        ]
    pyproject = root / "pyproject.toml"
    if pyproject.exists() and "[tool.coverage" in _read_bounded(pyproject):
        return [
            _finding(
                "coverage",
                "Coverage configuration",
                detail="pyproject.toml declares a [tool.coverage] section.",
                evidence="pyproject.toml",
            )
        ]
    return []


def _detect_quality_commands(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    package = root / "package.json"
    if package.exists():
        try:
            scripts = json.loads(_read_bounded(package)).get("scripts") or {}
        except json.JSONDecodeError:
            scripts = {}
        findings.extend(
            _finding(
                "quality_command",
                f"npm script: {name}",
                detail=str(scripts[name]),
                evidence="package.json",
                confidence="high",
            )
            for name in sorted(scripts)
            if re.search(r"lint|test|build|format|typecheck|check|quality", name, re.IGNORECASE)
        )
    for filename, label in (
        ("biome.json", "Biome"),
        (".eslintrc.json", "ESLint"),
        (".pre-commit-config.yaml", "pre-commit"),
    ):
        if (root / filename).exists():
            findings.append(
                _finding(
                    "quality_command", label, detail=f"{label} configuration present.", evidence=filename
                )
            )
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = _read_bounded(pyproject)
        findings.extend(
            _finding(
                "quality_command",
                label,
                detail=f"{label} configured in pyproject.toml.",
                evidence="pyproject.toml",
            )
            for marker, label in (("[tool.ruff", "Ruff"), ("[tool.pytest", "pytest"))
            if marker in text
        )
    return findings


def _debt_findings(source_scan: dict[str, Any]) -> list[dict[str, Any]]:
    count = source_scan["debtCount"]
    if not count:
        return []
    severity = "high" if count >= DEBT_RISK_THRESHOLD else "medium" if count >= 10 else "low"
    return [
        _finding(
            "debt",
            f"{count} debt marker(s)",
            detail="TODO/FIXME/HACK/XXX markers detected in source.",
            severity=severity,
            evidence=", ".join(source_scan["debtFiles"][:5]),
            confidence="high",
            metadata={"count": count},
        )
    ]


def _detect_security(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for filename, title in (
        (".gitleaks.toml", "Secret scanning (gitleaks)"),
        (".semgrep.yml", "Static analysis (semgrep)"),
        ("SECURITY.md", "Security policy"),
    ):
        if (root / filename).exists():
            findings.append(
                _finding(
                    "security", title, detail=f"{filename} present.", evidence=filename, confidence="high"
                )
            )
    if (root / ".env").exists():
        findings.append(
            _finding(
                "security",
                "Committed .env file",
                detail="A .env file is present in the project root and may expose secrets.",
                severity="high",
                evidence=".env",
                confidence="high",
            )
        )
    return findings


def _detect_documentation(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for filename in ("README.md", "README.rst", "README.txt"):
        if (root / filename).exists():
            findings.append(
                _finding("documentation", filename, detail="Project README detected.", evidence=filename)
            )
            break
    if (root / "docs").is_dir():
        findings.append(
            _finding(
                "documentation",
                "docs/ directory",
                detail="Dedicated documentation directory.",
                evidence="docs/",
            )
        )
    findings.extend(
        _finding("documentation", filename, detail=f"{filename} present.", evidence=filename)
        for filename in ("CONTRIBUTING.md", "LICENSE")
        if (root / filename).exists()
    )
    return findings


def _detect_git_history(root: Path) -> list[dict[str, Any]]:
    reflog = root / ".git" / "logs" / "HEAD"
    if not reflog.exists():
        return []
    commits: list[dict[str, str]] = []
    for line in _read_bounded(reflog).splitlines():
        if "\t" not in line:
            continue
        meta, message = line.split("\t", 1)
        if not message.split(":", 1)[0].strip().startswith("commit"):
            continue
        parts = meta.split()
        commits.append(
            {
                "sha": parts[1][:10] if len(parts) > 1 else "",
                "message": message.split(":", 1)[1].strip() if ":" in message else message.strip(),
            }
        )
    recent = commits[-MAX_GIT_COMMITS:]
    return [
        _finding(
            "git_history",
            commit["message"][:80] or commit["sha"],
            detail=f"Commit {commit['sha']}.",
            evidence=".git/logs/HEAD",
            metadata={"sha": commit["sha"]},
        )
        for commit in reversed(recent)
    ]


def _detect_features(root: Path) -> list[dict[str, Any]]:
    readme = next(
        (root / name for name in ("README.md", "README.rst", "README.txt") if (root / name).exists()), None
    )
    if not readme:
        return []
    headings = re.findall(r"^#{2,3}\s+(.+)$", _read_bounded(readme), re.MULTILINE)[:MAX_FEATURE_FINDINGS]
    return [
        _finding(
            "feature",
            heading.strip()[:80],
            detail="Detectable feature inferred from a documentation heading.",
            evidence=readme.name,
            confidence="low",
        )
        for heading in headings
    ]


def _derive_risks_and_gaps(
    root: Path, findings: list[dict[str, Any]], source_scan: dict[str, Any]
) -> list[dict[str, Any]]:
    present = {finding["category"] for finding in findings}
    derived: list[dict[str, Any]] = []
    gap_rules = (
        ("test", "No automated tests detected", "high"),
        ("coverage", "No coverage configuration detected", "medium"),
        ("security", "No security tooling detected", "medium"),
        ("documentation", "No documentation detected", "medium"),
        ("quality_command", "No quality commands detected", "medium"),
    )
    derived.extend(
        _finding("gap", title, detail="Derived from a missing assessment signal.", severity=severity)
        for category, title, severity in gap_rules
        if category not in present
    )
    if (root / "package.json").exists() and not any((root / lockfile).exists() for lockfile in LOCKFILES):
        derived.append(
            _finding(
                "gap",
                "package.json without a dependency lockfile",
                detail="A lockfile keeps installs reproducible across environments.",
                severity="low",
                evidence="package.json",
            )
        )
    if (root / ".env").exists():
        derived.append(
            _finding(
                "risk",
                "Committed .env may expose secrets",
                detail="Secrets in a committed .env file can leak through version control.",
                severity="high",
                evidence=".env",
                confidence="high",
            )
        )
    if source_scan["debtCount"] >= DEBT_RISK_THRESHOLD:
        derived.append(
            _finding(
                "risk",
                "High technical-debt marker density",
                detail=f"{source_scan['debtCount']} debt markers detected across the source.",
                severity="medium",
            )
        )
    if "git_history" not in present:
        derived.append(
            _finding(
                "risk",
                "No detectable git history",
                detail="No readable .git/logs/HEAD reflog.",
                severity="low",
            )
        )
    return derived


def _build_summary(findings: list[dict[str, Any]], source_scan: dict[str, Any]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    for finding in findings:
        by_category[finding["category"]] = by_category.get(finding["category"], 0) + 1
    return {
        "stack": [finding["title"] for finding in findings if finding["category"] == "stack"],
        "countsByCategory": by_category,
        "totalEndpoints": sum(source_scan["endpoints"].values()),
        "debtMarkers": source_scan["debtCount"],
        "riskCount": by_category.get("risk", 0),
        "gapCount": by_category.get("gap", 0),
        "hasTests": "test" in by_category,
        "hasCoverage": "coverage" in by_category,
        "hasSecurityTooling": any(
            finding["category"] == "security" and finding["severity"] == "info" for finding in findings
        ),
        "hasGitHistory": "git_history" in by_category,
    }


def assess_project(root: str | Path) -> dict[str, Any]:
    """Ejecuta el assessment estático de un proyecto y devuelve su resumen y hallazgos por dimensión.

    Lee únicamente archivos (incluido el reflog ``.git/logs/HEAD``); no ejecuta herramientas. Devuelve
    ``{rootPath, status, source, summary, findings}`` con un hallazgo por detección, riesgo o gap.
    """
    root_path = Path(root)
    files = _iter_all_files(root_path)
    source_scan = _scan_source(root_path, files)
    findings: list[dict[str, Any]] = []
    findings.extend(_detect_stack(root_path))
    findings.extend(_detect_modules(files))
    findings.extend(_detect_architecture(root_path, files))
    findings.extend(_endpoint_findings(source_scan))
    findings.extend(_detect_data(root_path, files))
    findings.extend(_detect_tests(files))
    findings.extend(_detect_coverage(root_path))
    findings.extend(_detect_quality_commands(root_path))
    findings.extend(_debt_findings(source_scan))
    findings.extend(_detect_security(root_path))
    findings.extend(_detect_documentation(root_path))
    findings.extend(_detect_git_history(root_path))
    findings.extend(_detect_features(root_path))
    findings.extend(_derive_risks_and_gaps(root_path, findings, source_scan))
    return {
        "rootPath": str(root_path),
        "status": "completed",
        "source": "static_analysis",
        "summary": _build_summary(findings, source_scan),
        "findings": findings,
    }


def run_project_assessment(
    repository: ProjectsRepository, *, project_id: str, root_path: str | Path
) -> dict[str, Any]:
    """Ejecuta el assessment estático y persiste el snapshot y sus hallazgos.

    Inserta un ``project_assessments`` y un ``project_findings`` por hallazgo (project-scoped y
    trazable al assessment). Devuelve ``{assessment, findings}`` ya persistidos.
    """
    result = assess_project(root_path)
    summary = result["summary"]
    assessment = repository.create_project_assessment(
        {
            "projectId": project_id,
            "rootPath": result["rootPath"],
            "status": result["status"],
            "source": result["source"],
            "summary": summary,
            "findingsCount": len(result["findings"]),
            "riskCount": summary["riskCount"],
            "gapCount": summary["gapCount"],
        }
    )
    findings = [
        repository.create_project_finding(
            {"assessmentId": assessment["id"], "projectId": project_id, **finding}
        )
        for finding in result["findings"]
    ]
    return {"assessment": assessment, "findings": findings}
