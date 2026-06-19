from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = ROOT / "scripts" / "productive-truth-scan.py"


def _load_scanner():
    assert SCANNER_PATH.exists(), "scanner script is missing"
    spec = importlib.util.spec_from_file_location("no_mock_productive_scan", SCANNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scanner_rejects_mock_tokens_in_productive_code(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "local_control_center" / "agents" / "runtime_status.py",
        "RUNTIME = 'internal_mock'\n",
    )

    violations = scanner.scan_root(tmp_path)

    assert [violation.rule_id for violation in violations] == ["prohibited-product-token"]
    assert violations[0].relative_path == "local_control_center/agents/runtime_status.py"
    assert violations[0].line_number == 1


def test_scanner_allows_mock_tokens_in_tests_and_docs(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(tmp_path / "tests_py" / "test_runtime.py", "fake = 'internal_mock'\n")
    _write(tmp_path / "docs" / "runtime.md", "mock fake dummy internal_mock\n")

    assert scanner.scan_root(tmp_path) == []


def test_scanner_allows_test_path_references_from_package_scripts(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "package.json",
        '{"scripts":{"quality:architecture":"uv run pytest tests_py/test_internal_mock_product_boundary.py -q"}}\n',
    )

    assert scanner.scan_root(tmp_path) == []


def test_scanner_prunes_ignored_and_allowed_directories_before_walk(tmp_path: Path) -> None:
    scanner = _load_scanner()

    assert scanner._should_prune_directory(tmp_path, tmp_path / "node_modules") is True
    assert scanner._should_prune_directory(tmp_path, tmp_path / "docs") is True
    assert scanner._should_prune_directory(tmp_path, tmp_path / "tests_py") is True
    assert scanner._should_prune_directory(tmp_path, tmp_path / "local_control_center") is False


def test_scanner_rejects_hardcoded_available_true_in_runtime_provider(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "local_control_center" / "agents" / "runtime_status.py",
        "available = True\n",
    )

    violations = scanner.scan_root(tmp_path)

    assert [violation.rule_id for violation in violations] == ["hardcoded-runtime-available"]
    assert "available=True" in violations[0].message


def test_scanner_rejects_quoted_available_true_in_runtime_provider(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "local_control_center" / "agents" / "runtime_status.py",
        "return {'available': True}\n",
    )

    violations = scanner.scan_root(tmp_path)

    assert [violation.rule_id for violation in violations] == ["hardcoded-runtime-available"]


def test_scanner_rejects_hardcoded_available_true_in_mcp_gateway(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "local_control_center" / "integrations" / "mcp_gateway.py",
        "return {'available': True}\n",
    )

    violations = scanner.scan_root(tmp_path)

    assert [violation.rule_id for violation in violations] == ["hardcoded-runtime-available"]
    assert violations[0].relative_path == "local_control_center/integrations/mcp_gateway.py"


def test_scanner_rejects_shell_true_outside_tests(tmp_path: Path) -> None:
    scanner = _load_scanner()
    _write(
        tmp_path / "local_control_center" / "jobs_approvals" / "worker.py",
        "subprocess.run(command, shell=True)\n",
    )

    violations = scanner.scan_root(tmp_path)

    assert [violation.rule_id for violation in violations] == ["shell-true-outside-tests"]


def test_quality_scripts_wire_local_quality_gate_and_scanner() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert "quality:productive-truth" in scripts
    assert "productive-truth-scan.py" in scripts["quality:productive-truth"]
    assert "quality:architecture" in scripts
    assert "test_real_readiness_architecture.py" in scripts["quality:architecture"]
    assert "scripts/quality-local.ps1" in scripts["quality"]
    assert (ROOT / "scripts" / "quality-local.ps1").exists()
