"""Gate estático anti-drift del contrato de evidencia.

Cada valor de artifact ``kind`` y de ``qa_verdict`` que el código productivo persiste debe existir
en los ``Literal`` de ``evidence/models.py``; si falta uno, ``GET /api/v1/overview`` responde 500
con ``ResponseValidationError`` después de una corrida real (tercer incidente del mismo patrón:
``product_owner_output``/``product_brief`` y luego ``product_backlog``/``backlog_generated``).
Este gate recorre el árbol con ``ast`` y falla ANTES del merge en vez de en producción.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from local_control_center.evidence.models import ArtifactKind, QAVerdict

ROOT = Path(__file__).resolve().parents[1] / "local_control_center"

ARTIFACT_KIND_CALLEES = {"create_artifact", "_write_json_artifact"}
QA_VERDICT_CALLEES = {"create_evidence_package"}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _collect_persisted_values() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Recolecta constantes string persistidas como artifact kind y qa_verdict, por archivo."""
    artifact_kinds: dict[str, set[str]] = {}
    qa_verdicts: dict[str, set[str]] = {}
    for source_file in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in source_file.parts:
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        relative = str(source_file.relative_to(ROOT.parent))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = _call_name(node)
                for keyword in node.keywords:
                    if not isinstance(keyword.value, ast.Constant):
                        continue
                    if not isinstance(keyword.value.value, str):
                        continue
                    value = keyword.value.value
                    if keyword.arg == "kind" and callee in ARTIFACT_KIND_CALLEES:
                        artifact_kinds.setdefault(relative, set()).add(value)
                    elif keyword.arg == "qa_verdict" and callee in QA_VERDICT_CALLEES:
                        qa_verdicts.setdefault(relative, set()).add(value)
            # Patrón `qa_verdict = {...}.get(status, default)`: los valores del dict y el default
            # terminan persistidos vía create_evidence_package(qa_verdict=qa_verdict).
            if isinstance(node, ast.Assign):
                targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if "qa_verdict" not in targets:
                    continue
                value_node = node.value
                if not (
                    isinstance(value_node, ast.Call)
                    and isinstance(value_node.func, ast.Attribute)
                    and value_node.func.attr == "get"
                    and isinstance(value_node.func.value, ast.Dict)
                ):
                    continue
                mapped = list(value_node.func.value.values) + list(value_node.args[1:])
                for item in mapped:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        qa_verdicts.setdefault(relative, set()).add(item.value)
    return artifact_kinds, qa_verdicts


def test_persisted_artifact_kinds_are_declared_in_the_literal() -> None:
    """Todo `kind=` persistido por los productores reales debe estar en `ArtifactKind`."""
    artifact_kinds, _ = _collect_persisted_values()
    assert artifact_kinds, "the scan must find real artifact producers"
    declared = set(get_args(ArtifactKind))
    missing = {
        source: sorted(values - declared) for source, values in artifact_kinds.items() if values - declared
    }
    assert not missing, (
        "artifact kinds persisted by real producers are missing from ArtifactKind "
        f"(evidence/models.py) and would 500 /api/v1/overview: {missing}"
    )


def test_persisted_qa_verdicts_are_declared_in_the_literal() -> None:
    """Todo `qa_verdict` persistido por los productores reales debe estar en `QAVerdict`."""
    _, qa_verdicts = _collect_persisted_values()
    assert qa_verdicts, "the scan must find real qa_verdict producers"
    declared = set(get_args(QAVerdict))
    missing = {
        source: sorted(values - declared) for source, values in qa_verdicts.items() if values - declared
    }
    assert not missing, (
        "qa_verdict values persisted by real producers are missing from QAVerdict "
        f"(evidence/models.py) and would 500 /api/v1/overview: {missing}"
    )
