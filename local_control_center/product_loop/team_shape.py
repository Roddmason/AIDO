"""Forma del equipo de un pedido: modo, riesgo y alcances técnicos que alimentan al scheduler.

Funciones puras sobre la metadata del pedido, la intención clasificada y la salida del Product Owner.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.team_scheduler.scheduler import MODES, RISKS

_ROLE_TO_SCOPE = {
    "developer": "backend",
    "implementer": "backend",
    "backend_engineer": "backend",
    "frontend_engineer": "frontend",
    "mobile_engineer": "mobile",
    "database_engineer": "database",
    "data_engineer": "data",
    "devops": "infra",
    "devops_engineer": "infra",
    "security_reviewer": "security",
    "security_engineer": "security",
    "pentester": "security",
    "qa": "tests",
    "qa_reviewer": "tests",
    "qa_engineer": "tests",
    "researcher": "research",
    "release_manager": "release",
    "architect": "architecture",
    "software_architect": "architecture",
}
_TECHNICAL_TEAM_SCOPES = {
    "api",
    "auth",
    "backend",
    "ci",
    "data",
    "database",
    "deploy",
    "devops",
    "external",
    "frontend",
    "infra",
    "migration",
    "ml",
    "mobile",
    "payments",
    "pii",
    "schema",
    "security",
    "ui",
    "web",
}


def team_mode(request_meta: dict[str, Any]) -> str:
    """Modo de equipo pedido en la metadata del pedido, normalizado a un modo conocido."""
    mode = (
        str(
            request_meta.get("teamMode")
            or request_meta.get("team_mode")
            or request_meta.get("mode")
            or "balanced"
        )
        .strip()
        .lower()
    )
    return mode if mode in MODES else "balanced"


def team_risk(
    *,
    request_meta: dict[str, Any],
    intent: dict[str, Any],
    output: dict[str, Any],
    message: str,
) -> str:
    """Riesgo del pedido: el declarado si es válido o una heurística por palabras clave."""
    risk = str(request_meta.get("risk") or intent.get("risk") or "").strip().lower()
    if risk in RISKS:
        return risk
    text = " ".join(
        [
            message,
            str(output.get("summary") or ""),
            " ".join(str(item) for item in output.get("risks") or []),
        ]
    ).lower()
    if any(marker in text for marker in ("critical", "prod", "production", "payment", "pii")):
        return "high"
    return "medium"


def team_scope(
    *,
    message: str,
    intent: dict[str, Any],
    output: dict[str, Any],
    agent_tasks: list[dict[str, Any]],
) -> list[str]:
    """Alcances técnicos inferidos de la intención, el texto del pedido y los roles de las tareas."""
    scope: dict[str, None] = {}
    for intent_name in intent.get("intents") or []:
        value = str(intent_name).strip().lower()
        if value:
            scope.setdefault(value, None)
    for role in intent.get("requiredRoles") or []:
        mapped = _ROLE_TO_SCOPE.get(str(role).strip().lower())
        if mapped:
            scope.setdefault(mapped, None)
    text = " ".join(
        [
            message,
            str(output.get("summary") or ""),
            str((output.get("productBriefPatch") or {}).get("scope") or ""),
            " ".join(str(item) for item in output.get("risks") or []),
        ]
    ).lower()
    keyword_scope = {
        "backend": ("backend", "api", "server", "fastapi", "service"),
        "frontend": ("frontend", "ui", "web", "react", "screen"),
        "mobile": ("mobile", "ios", "android", "react native"),
        "database": ("database", "schema", "migration", "sqlite", "sql"),
        "data": ("data", "analytics", "pipeline", "ml"),
        "infra": ("infra", "devops", "deploy", "ci", "kubernetes", "docker"),
        "security": ("security", "auth", "login", "token", "secret", "pentest", "exploit"),
        "research": ("research", "investigate", "source", "benchmark"),
        "release": ("release", "publish", "rollback"),
        "refactor": ("refactor", "restructure", "decouple"),
    }
    for scope_name, markers in keyword_scope.items():
        if any(marker in text for marker in markers):
            scope.setdefault(scope_name, None)
    for task in agent_tasks:
        role = str(task.get("role") or "").strip().lower()
        mapped = _ROLE_TO_SCOPE.get(role)
        if mapped:
            scope.setdefault(mapped, None)
    if not scope or not any(scope_name in _TECHNICAL_TEAM_SCOPES for scope_name in scope):
        scope["backend"] = None
    return list(scope)


def failed_team_schedule(
    *,
    phase: str,
    reason: str,
    previous_schedule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Marcador de plan de equipo fallido, conservando el plan previo si lo había."""
    failed = {
        "status": "failed",
        "phase": phase,
        "reason": reason,
    }
    if previous_schedule is not None:
        failed["previousSchedule"] = previous_schedule
    return failed
