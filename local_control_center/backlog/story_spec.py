"""Ensambla la HU como spec ejecutable: epica, historia, criterios y responsabilidades por rol.

Vista read-only sobre las tablas ya persistidas del backlog (no duplica reglas de negocio):
compone el spec canonico de una historia y lo renderiza como texto acotado apto para el
prompt de los agentes ejecutores. El texto truncado siempre termina con un marcador
explicito para que el agente sepa que el spec no esta completo.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from .repository import BacklogRepository

STORY_SPEC_PROMPT_CHAR_LIMIT = 6_000
_TRUNCATION_MARKER = "\n[truncated]"


def build_story_spec(backlog: BacklogRepository, story_id: str) -> dict[str, Any]:
    """Compone el spec canonico de una historia desde el backlog persistido.

    Returns:
        Dict con storyId, epica, historia, criterios de aceptacion ordenados y el
        desglose de responsabilidades por rol (goal, reviewer, gates, dependsOn).

    Raises:
        KeyError: si la historia no existe (mismo contrato de ``get_user_story``).
    """
    story = backlog.get_user_story(story_id)
    epic = backlog.get_epic(story["epicId"])
    criteria = backlog.list_acceptance_criteria(story_id=story_id)
    tasks = sorted(
        backlog.list_agent_tasks(story_id=story_id),
        key=lambda task: (str(task.get("role") or ""), str(task.get("id") or "")),
    )
    role_by_task_id = {task["id"]: str(task.get("role") or "") for task in tasks}
    responsibilities: list[dict[str, Any]] = []
    for task in tasks:
        metadata = task.get("metadata") or {}
        depends_on = [
            {
                "taskId": dependency["dependsOnTaskId"],
                "role": role_by_task_id.get(dependency["dependsOnTaskId"], ""),
            }
            for dependency in backlog.list_task_dependencies(task_id=task["id"])
        ]
        responsibilities.append(
            {
                "taskId": task["id"],
                "role": task["role"],
                "title": task["title"],
                "goal": str(metadata.get("goal") or task.get("description") or ""),
                "reviewerRole": str(metadata.get("reviewerRole") or ""),
                "qualityGates": metadata.get("qualityGates") or [],
                "dependsOn": depends_on,
            }
        )
    return {
        "storyId": story["id"],
        "epic": {
            "id": epic["id"],
            "title": epic["title"],
            "description": epic.get("description") or "",
        },
        "story": {
            "title": story["title"],
            "asA": story["asA"],
            "iWant": story["iWant"],
            "soThat": story["soThat"],
            "businessValue": story.get("businessValue") or "",
            "status": story.get("status") or "",
            "priority": story.get("priority") or "",
        },
        "acceptanceCriteria": [
            {
                "id": criterion["id"],
                "sequence": criterion["sequence"],
                "criterion": criterion["criterion"],
                "status": criterion.get("status") or "",
            }
            for criterion in criteria
        ],
        "roleResponsibilities": responsibilities,
    }


def render_story_spec_prompt(
    specs: list[dict[str, Any]],
    *,
    char_limit: int = STORY_SPEC_PROMPT_CHAR_LIMIT,
) -> str:
    """Renderiza specs de historias como markdown compacto y deterministico para prompts.

    El resultado nunca excede ``char_limit``; si el contenido no cabe, se corta y
    termina con el marcador ``[truncated]``.
    """
    sections: list[str] = []
    for spec in specs:
        epic = spec.get("epic") or {}
        story = spec.get("story") or {}
        lines = [
            f"## Story: {story.get('title', '')} (epic: {epic.get('title', '')})",
            f"As a {story.get('asA', '')}, I want {story.get('iWant', '')}, "
            f"so that {story.get('soThat', '')}.",
            "Acceptance criteria:",
        ]
        lines.extend(
            f"- AC{criterion.get('sequence')}: {criterion.get('criterion', '')}"
            for criterion in spec.get("acceptanceCriteria") or []
        )
        responsibilities = spec.get("roleResponsibilities") or []
        if responsibilities:
            lines.append("Role responsibilities:")
            for item in responsibilities:
                dependencies = ", ".join(
                    dependency.get("role") or dependency.get("taskId", "")
                    for dependency in item.get("dependsOn") or []
                )
                suffix = f" (depends on: {dependencies})" if dependencies else ""
                reviewer = item.get("reviewerRole") or ""
                reviewer_text = f" Reviewer: {reviewer}." if reviewer else ""
                lines.append(f"- {item.get('role', '')}: {item.get('goal', '')}{reviewer_text}{suffix}")
        sections.append("\n".join(lines))
    rendered = "\n\n".join(sections)
    if len(rendered) <= char_limit:
        return rendered
    cut = max(char_limit - len(_TRUNCATION_MARKER), 0)
    return rendered[:cut] + _TRUNCATION_MARKER
