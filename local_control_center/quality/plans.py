"""Planes de calidad por propósito, sin ejecutar comandos ni inferir resultados.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from local_control_center.host_resources.models import WorkloadClass

ARCHITECTURE_TESTS = (
    "tests_py/test_real_readiness_architecture.py",
    "tests_py/test_internal_mock_product_boundary.py",
    "tests_py/test_execution_boundary_architecture.py",
    "tests_py/test_vertical_slices_architecture.py",
    "tests_py/test_web_rework_architecture.py",
)


@dataclass(frozen=True)
class QualityStep:
    """Un único árbol productivo, con límite temporal y clase de admisión explícitos."""

    name: str
    argv: tuple[str, ...]
    workload_class: WorkloadClass = "qa_light"
    timeout_seconds: int = 900


def iteration_scripts(scripts: list[str], *, tier: str) -> list[str]:
    """Mantiene checks propios del proyecto y reserva los gates completos para entrega."""
    if tier not in {"fast", "story"}:
        raise ValueError("Iteration quality tier must be fast or story.")
    return list(
        dict.fromkeys(
            f"quality:{tier}" if script in {"quality", "quality:pr", "quality:release"} else script
            for script in scripts
        )
    )


def _paths(root: Path, paths: list[str], *, prefix: str = "") -> list[str]:
    selected = []
    for value in paths:
        path = (root / value).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Quality path must be an existing repository file: {value}")
        relative = path.relative_to(root.resolve()).as_posix()
        if prefix and not relative.startswith(prefix):
            raise ValueError(f"Expected a test file under {prefix}: {value}")
        selected.append(relative)
    return sorted(set(selected))


def related_python_tests(root: Path, changed: list[str]) -> list[str]:
    """Selecciona tests modificados y consumidores del módulo/slice, sin suite global implícita."""
    selected = {path for path in changed if path.startswith("tests_py/test_") and path.endswith(".py")}
    modules = {
        ".".join(Path(path).with_suffix("").parts[:-1])
        for path in changed
        if path.startswith("local_control_center/") and path.endswith(".py")
    }
    # Un cambio en bootstrap toca todo: el operador debe seleccionar el slice en fast/story.
    modules.discard("local_control_center")
    for test in (root / "tests_py").glob("test_*.py"):
        text = test.read_text(encoding="utf-8")
        if any(module in text for module in modules):
            selected.add(test.relative_to(root).as_posix())
    return sorted(selected)


def build_plan(
    root: Path,
    tier: str,
    *,
    changed_files: list[str] | None = None,
    python_tests: list[str] | None = None,
    web_tests: list[str] | None = None,
) -> list[QualityStep]:
    """Conserva todos los gates PR; fast/story requieren selección verificable del slice."""
    if tier not in {"fast", "story", "pr", "release"}:
        raise ValueError("Unknown quality tier.")
    py = sys.executable
    node = "node"
    build = QualityStep(
        "build",
        (
            node,
            "node_modules/vite/bin/vite.js",
            "build",
            "--config",
            "local-control-center/web/vite.config.ts",
        ),
        "build_heavy",
    )
    typecheck = QualityStep(
        "typecheck",
        (node, "node_modules/typescript/bin/tsc", "--noEmit", "-p", "local-control-center/web/tsconfig.json"),
    )
    diff = QualityStep("diff-check", ("git", "diff", "--check"))
    if tier == "release":
        # The operational verifier includes quality:pr once; no recursive release gate.
        return [
            QualityStep(
                "operational-verification",
                (
                    "powershell",
                    "-NoProfile",
                    "-File",
                    "scripts/verify-operational-hardening.ps1",
                ),
                "capture_session",
                21600,
            ),
            QualityStep(
                "release-install-upgrade-backup",
                (py, "-m", "local_control_center.quality.release"),
                "build_heavy",
                3600,
            ),
        ]
    if tier == "pr":
        return [
            QualityStep("productive-truth", (py, "scripts/productive-truth-scan.py")),
            QualityStep(
                "python",
                (
                    py,
                    "-m",
                    "pytest",
                    "tests_py",
                    "--ignore=tests_py/test_launcher_capture_http.py",
                    "-v",
                    "--tb=short",
                    "--junitxml=.tmp/operational-hardening-p0/quality-pr-python.xml",
                ),
                "build_heavy",
                timeout_seconds=7200,
            ),
            QualityStep(
                "python-capture-session",
                (py, "-m", "pytest", "-q", "tests_py/test_launcher_capture_http.py"),
                "capture_session",
            ),
            QualityStep("web", (node, "scripts/run-web-tests.mjs"), "browser_test", 14400),
            build,
            typecheck,
            QualityStep("ruff", ("uv", "run", "--extra", "dev", "ruff", "check", ".")),
            QualityStep("format", ("uv", "run", "--extra", "dev", "ruff", "format", "--check", ".")),
            QualityStep(
                "biome", (node, "node_modules/@biomejs/biome/bin/biome", "check", "local-control-center/web")
            ),
            QualityStep("architecture", (py, "-m", "pytest", *ARCHITECTURE_TESTS, "-q")),
            QualityStep(
                "secrets",
                ("gitleaks", "detect", "--no-git", "--source", ".", "--config", ".gitleaks.toml", "--redact"),
            ),
            QualityStep(
                "semgrep",
                (
                    "uv",
                    "run",
                    "--extra",
                    "dev",
                    "semgrep",
                    # Windows' new CLI falls back through extra launchers and exhausts qa_light's
                    # process quota before starting the scanner. Same Python driver/core, no bypass.
                    *(("--legacy",) if sys.platform == "win32" else ()),
                    "scan",
                    "--jobs",
                    "1",
                    "--error",
                    "--metrics",
                    "off",
                    "--disable-version-check",
                    "--config",
                    ".semgrep.yml",
                    "--no-git-ignore",
                    "local_control_center",
                    "tests_py",
                    "local-control-center/web/src",
                    "tests_web",
                ),
                timeout_seconds=1800,
            ),
            diff,
        ]
    changed = _paths(root, changed_files or [])
    tests = _paths(root, python_tests or related_python_tests(root, changed), prefix="tests_py/")
    web = _paths(
        root,
        web_tests
        or [path for path in changed if path.startswith("tests_web/") and path.endswith(".spec.js")],
        prefix="tests_web/",
    )
    python_files = [path for path in changed if path.endswith(".py")]
    ui_changed = any(
        path.startswith("local-control-center/web/") or path == "package.json" for path in changed
    )
    if python_files and not tests:
        raise ValueError("selection_required: pass --python-test for the affected Python slice.")
    if tier == "story" and ui_changed and not web:
        raise ValueError("selection_required: pass --web-test for the affected UI slice.")
    steps = [diff]
    if python_files:
        steps.append(
            QualityStep("ruff-changed", ("uv", "run", "--extra", "dev", "ruff", "check", "--", *python_files))
        )
    # The API + OS worker + dispatcher + pytest tree reached the 8 GiB cap and raised
    # MemoryError (1803dac5). Reserve the existing 16 GiB aggregate profile before spawn;
    # small backend regressions retain qa_light and never depend on this admission.
    native_pipeline = [path for path in tests if path == "tests_py/test_watchdog_http_pipeline.py"]
    session_pipeline = [path for path in tests if path == "tests_py/test_launcher_capture_http.py"]
    light_tests = [path for path in tests if path not in native_pipeline and path not in session_pipeline]
    if light_tests:
        steps.append(QualityStep("python-focused", (py, "-m", "pytest", "-q", *light_tests)))
    if native_pipeline:
        steps.append(
            QualityStep("python-native-pipeline", (py, "-m", "pytest", "-q", *native_pipeline), "build_heavy")
        )
    if session_pipeline:
        # The joint launcher profile includes control plane, execution and collector.
        # It is not qa_light: the outer runner remains an additional native ancestor.
        steps.append(
            QualityStep(
                "python-capture-session", (py, "-m", "pytest", "-q", *session_pipeline), "capture_session"
            )
        )
    if ui_changed:
        steps.append(typecheck)
    if tier == "story":
        steps.append(QualityStep("architecture", (py, "-m", "pytest", "-q", *ARCHITECTURE_TESTS)))
        if web:
            steps.extend(
                [
                    build,
                    QualityStep(
                        "web-focused", (node, "scripts/run-web-tests.mjs", *web), "browser_test", 3600
                    ),
                ]
            )
    steps.extend(
        [
            QualityStep(
                "secrets-working",
                ("gitleaks", "protect", "--source", ".", "--config", ".gitleaks.toml", "--redact"),
            ),
            QualityStep(
                "secrets-staged",
                (
                    "gitleaks",
                    "protect",
                    "--staged",
                    "--source",
                    ".",
                    "--config",
                    ".gitleaks.toml",
                    "--redact",
                ),
            ),
        ]
    )
    return steps
