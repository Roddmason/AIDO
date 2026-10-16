"""Proyecciones de revisión derivadas del diff del workspace o del resumen que reporta el runtime.

Funciones puras: convierten diffs y resultados de runtime en las vistas compactas que guarda el loop.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from .spec_artifacts import exclude_aido_artifacts


def changed_files_from_diff(diff: dict[str, Any]) -> list[str]:
    """Archivos modificados de un diff, sin artefactos internos de AIDO."""
    names = [str(item) for item in diff.get("nameOnly") or [] if str(item).strip()]
    if names:
        return exclude_aido_artifacts(names)
    changed: list[str] = []
    for item in diff.get("status") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and path not in changed:
            changed.append(path)
    return exclude_aido_artifacts(changed)


def runtime_changed_files(runtime_result: dict[str, Any]) -> list[str]:
    """Archivos modificados que reporta el runtime en su resumen de diff."""
    summary = runtime_result.get("diffSummary") if isinstance(runtime_result.get("diffSummary"), dict) else {}
    return exclude_aido_artifacts(
        [str(item) for item in summary.get("changedFiles") or [] if str(item).strip()]
    )


def review_from_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Proyección de revisión a partir del diff capturado."""
    return {
        "state": diff.get("state"),
        "changedFiles": changed_files_from_diff(diff),
        "branch": diff.get("branch"),
        "headCommit": diff.get("headCommit"),
        "diffStat": diff.get("diffStat") or "",
        "patch": diff.get("patch") or "",
        "patchSizeBytes": diff.get("patchSizeBytes") or 0,
        "truncated": bool(diff.get("truncated", False)),
        "toolCalls": diff.get("toolCalls") or [],
        "policyDecisionIds": diff.get("policyDecisionIds") or [],
    }


def review_from_runtime(runtime_result: dict[str, Any]) -> dict[str, Any]:
    """Proyección de revisión a partir del resumen de diff que reporta el runtime."""
    summary = runtime_result.get("diffSummary") if isinstance(runtime_result.get("diffSummary"), dict) else {}
    return {
        "state": summary.get("state") or "runtime_reported",
        "changedFiles": runtime_changed_files(runtime_result),
        "branch": summary.get("branch"),
        "headCommit": summary.get("headCommit"),
        "diffStat": summary.get("diffStat") or "",
        "patch": summary.get("patch") or "",
        "patchSizeBytes": summary.get("patchSizeBytes") or 0,
        "truncated": bool(summary.get("truncated", False)),
        "toolCalls": [],
        "policyDecisionIds": [],
    }


def compact_runtime_result(runtime_result: dict[str, Any] | None) -> dict[str, Any]:
    """Compacta ``runtimeResult`` del DeveloperAgent antes de guardarlo en ``durableRun``.

    ``runtimeResult["agentRun"]`` es la fila completa de ``agent_runs``: repite el payload de entrada
    del developer (``input``, varios MB con ``teamSchedule``/``agentAssignments``/``agentTasks``) y su
    espejo en ``output``, ambos ya persistidos en ``agent_runs`` (``agents/repository.py``). Nadie lee
    ``agentRun.input``/``agentRun.output`` desde el contexto del loop, así que aquí queda solo la
    referencia (``id``/``status``) que un lector eventual necesitaría. Es la única función de
    compactación: todo escritor de ``runtimeResult`` en ``durableRun`` pasa por ella. No muta
    ``runtime_result``; devuelve una copia superficial.
    """
    if not isinstance(runtime_result, dict):
        return {}
    agent_run = runtime_result.get("agentRun")
    if not isinstance(agent_run, dict):
        return runtime_result
    reference = {key: agent_run[key] for key in ("id", "status") if key in agent_run}
    return {**runtime_result, "agentRun": reference}


def diff_ref_from_review(review: dict[str, Any]) -> dict[str, Any]:
    """Referencia liviana al diff de una revisión."""
    return {
        "kind": "git_diff",
        "state": review.get("state"),
        "files": review.get("changedFiles") or [],
        "branch": review.get("branch"),
        "headCommit": review.get("headCommit"),
        "diffStat": review.get("diffStat") or "",
        "patchSizeBytes": review.get("patchSizeBytes") or 0,
        "truncated": bool(review.get("truncated", False)),
    }


def diff_summary_from_review(review: dict[str, Any]) -> dict[str, Any]:
    """Resumen liviano del diff de una revisión."""
    return {
        "state": review.get("state"),
        "changedFiles": review.get("changedFiles") or [],
        "diffStat": review.get("diffStat") or "",
        "patchSizeBytes": review.get("patchSizeBytes") or 0,
        "truncated": bool(review.get("truncated", False)),
    }
