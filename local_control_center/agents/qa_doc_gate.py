"""Gate de QA proporcional para historias cuyo cambio es solo documentacion.

El QAAgent descubre el plan completo del proyecto (tests, build, typecheck, lint) para toda
historia, sin mirar que archivos cambio: una historia que solo edita un `.md` corre la misma
suite completa que una que toca codigo, lo que en AIDO toma horas. Este modulo decide, a partir
de los archivos cambiados de la historia, si el cambio es exclusivamente documentacion y, de
serlo, arma un plan reducido: un unico comando real (`git diff --check`, que detecta trailing
whitespace y marcadores de conflicto en el diff) mas la lista de comandos de codigo que el
descubrimiento normal hubiera generado, marcados como *no aplicables* a este cambio para que el
QAAgent los deje fuera del veredicto en vez de correrlos o contarlos como `skipped_with_reason`
(reservado a un comando que si debia correr y no pudo).

No ejecuta nada ni decide politica de seguridad: el comando real sigue pasando por el
ToolBroker y la allowlist de `security_policy.permissions` como cualquier otro comando de QA.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_control_center.projects.toolchain import plan_workspace_commands

DOC_EXTENSIONS: tuple[str, ...] = (".md", ".mdx", ".rst", ".adoc")
"""Extensiones que cuentan como documentacion pura para este gate.

Deliberadamente distinta de `product_loop.intent_classifier.DOC_EXTENSIONS`: esa lista clasifica
la *intencion* de una historia e incluye `.txt`, donde un texto suelto si cuenta como
documentacion. Aca la extension decide si se salta la suite de codigo, y `requirements.txt` /
`constraints.txt` son manifiestos de dependencias con esa extension: nunca deben calificar.
`.mdx` y `.adoc`, que `intent_classifier` no reconoce, si documentan y sí aplican aca.
"""

DOC_ONLY_SKIP_REASON = "cambio solo de documentación"
"""Motivo registrado en cada comando de codigo marcado como no aplicable por este gate."""

_EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (".github/",)
"""Rutas que nunca cuentan como documentacion aunque su extension sea de docs.

`.github/ISSUE_TEMPLATE/*.md` o `.github/PULL_REQUEST_TEMPLATE.md` son configuracion del
repositorio (plantillas que moldean el flujo de issues/PRs), no documentacion de producto.
"""

_DIFF_CHECK_LABEL = "Diff whitespace and conflict markers (git diff --check)"
_DIFF_CHECK_ARGV: tuple[str, ...] = ("git", "diff", "--check")


def _is_doc_path(path: str) -> bool:
    if path.startswith(_EXCLUDED_PATH_PREFIXES):
        return False
    return path.endswith(DOC_EXTENSIONS)


def is_doc_only_change(changed_paths: list[str]) -> bool:
    """Indica si todos los archivos cambiados de la historia son documentacion.

    Fail-safe: una lista vacia (diff indeterminable, o sin cambios capturados) devuelve
    ``False`` para que el llamador conserve el plan de QA completo en vez de asumir que no hay
    nada que verificar.
    """
    if not changed_paths:
        return False
    return all(_is_doc_path(path) for path in changed_paths)


def resolve_doc_only_qa_plan(workspace_path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Arma el plan de QA reducido para un cambio de solo documentacion.

    Devuelve ``(real_commands, not_applicable_commands)``: ``real_commands`` es lo unico que el
    QAAgent ejecuta de verdad (hoy, solo `git diff --check`) y decide el veredicto;
    ``not_applicable_commands`` es el plan que `discover_qa_commands` habria generado para este
    workspace, con el motivo adjunto para que quede visible en la evidencia sin participar del
    veredicto (no son comandos que debian correr y no pudieron: no correspondia correrlos).
    """
    real_commands: list[dict[str, Any]] = [
        {"label": _DIFF_CHECK_LABEL, "argv": list(_DIFF_CHECK_ARGV), "critical": True}
    ]
    not_applicable_commands: list[dict[str, Any]] = [
        {**command.as_command_spec(), "reason": DOC_ONLY_SKIP_REASON}
        for command in plan_workspace_commands(workspace_path)
    ]
    return real_commands, not_applicable_commands
