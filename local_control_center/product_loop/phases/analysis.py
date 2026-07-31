"""Fase Análisis: consistencia cruzada spec ↔ tareas ↔ diff con el trabajo ya hecho.

Corre después de los gates de QA y Security (el diff real ya existe) y antes de estacionar el run
para aprobación: verifica de forma determinista que cada historia tenga criterios de aceptación,
que cada tarea técnica apunte a una historia existente y que el diff toque archivos coherentes con
el alcance planificado. Es el productor del estado ``quality_review`` (huérfano hasta este módulo) y
es advisory por diseño: los hallazgos se persisten como evidencia visible para la aprobación humana,
pero el veredicto de entrega sigue siendo de QA/Security — este análisis no puede aprobar ni vetar.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

CONSISTENT = "consistent"
FINDINGS = "findings"


def analyze_run_consistency(
    *,
    stories: list[dict[str, Any]],
    acceptance_criteria: list[dict[str, Any]],
    agent_tasks: list[dict[str, Any]],
    changed_files: list[str],
) -> dict[str, Any]:
    """Chequeo determinista de consistencia del run; nunca lanza, siempre devuelve un veredicto.

    Devuelve ``{"status": "consistent"|"findings", "findings": [...]}`` donde cada hallazgo trae
    ``check``/``detail`` accionables. Sin historias (carril rápido o orden técnica directa) el run
    es trivialmente consistente: el análisis compara plan contra trabajo, no inventa exigencias.
    """
    stories_with_criteria = {str(criterion.get("storyId")) for criterion in acceptance_criteria}
    story_ids = {str(story.get("id")) for story in stories}
    findings: list[dict[str, str]] = [
        {
            "check": "story_without_acceptance_criteria",
            "detail": f"Story {story.get('title')!r} has no acceptance criteria to verify against.",
        }
        for story in stories
        if str(story.get("id")) not in stories_with_criteria
    ]
    findings.extend(
        {
            "check": "task_without_story",
            "detail": f"Agent task {task.get('title')!r} references missing story {task.get('storyId')}.",
        }
        for task in agent_tasks
        if str(task.get("storyId") or "") and story_ids and str(task.get("storyId")) not in story_ids
    )
    if not changed_files:
        findings.append(
            {
                "check": "empty_diff",
                "detail": "No changed files survived the diff filter; there is no work to analyze.",
            }
        )
    return {
        "status": FINDINGS if findings else CONSISTENT,
        "findings": findings,
        "checkedStories": len(stories),
        "checkedTasks": len(agent_tasks),
        "changedFileCount": len(changed_files),
    }
