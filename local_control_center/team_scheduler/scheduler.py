"""TeamScheduler determinista: selecciona los roles que la tarea necesita y resuelve su ejecución.

Lógica pura (sin DB ni I/O) que mapea ``(scope, risk, mode)`` a un plan de equipo estable. Selecciona
solo los roles requeridos por alcance y riesgo —un core fijo más roles dirigidos por scope, riesgo y
modo— en dos pasadas para resolver la dependencia circular de los roles de coordinación (PM/SM dependen
del tamaño del equipo base). Por cada rol resuelve permission profile (de
``security_policy.policy_engine.PROFILE_DEFAULTS``, fuente única de verdad), provider-kind y model tier
(tokens de tier, no ids de modelo concretos —el model gateway resuelve el modelo vigente), runtime,
skills, tools, presupuesto, reviewer y quality gates. El reviewer nunca es ``self`` para roles o
contextos sensibles a seguridad ni con riesgo alto, sin importar el modo.
"""

from __future__ import annotations

from typing import Any

from local_control_center.security_policy.policy_engine import permission_profile_for

SCHEDULER_VERSION = 1

CORE_ROLES = ("product_owner", "technical_lead")
ALL_ROLES = (
    "product_owner",
    "project_manager",
    "scrum_master",
    "architect",
    "technical_lead",
    "backend_engineer",
    "frontend_engineer",
    "mobile_engineer",
    "data_engineer",
    "database_engineer",
    "qa_engineer",
    "security_engineer",
    "pentester",
    "devops_engineer",
    "release_manager",
    "researcher",
)

MODES = ("economy", "balanced", "critical", "maximum")
RISKS = ("low", "medium", "high", "critical")
_RISK_ORDER = {risk: index for index, risk in enumerate(RISKS)}

# Role kinds steer runtime/provider-kind and the budget weight.
_BUILD_ROLES = {
    "backend_engineer",
    "frontend_engineer",
    "mobile_engineer",
    "data_engineer",
    "database_engineer",
    "devops_engineer",
    "pentester",
}
_REVIEW_ROLES = {"qa_engineer", "security_engineer"}

# Scope tag → the engineering role it pulls in.
_SCOPE_ROLES = {
    "backend": "backend_engineer",
    "api": "backend_engineer",
    "frontend": "frontend_engineer",
    "ui": "frontend_engineer",
    "web": "frontend_engineer",
    "mobile": "mobile_engineer",
    "data": "data_engineer",
    "ml": "data_engineer",
    "analytics": "data_engineer",
    "database": "database_engineer",
    "schema": "database_engineer",
    "migration": "database_engineer",
    "infra": "devops_engineer",
    "deploy": "devops_engineer",
    "ci": "devops_engineer",
    "devops": "devops_engineer",
    "research": "researcher",
    "spike": "researcher",
    "unknown": "researcher",
}
# Scopes that pull in security review, and the stricter subset that forbids self-review.
_SECURITY_SCOPES = {"security", "auth", "data", "external", "payments", "compliance", "pii"}
_SECURITY_SENSITIVE_SCOPES = {"payments", "auth", "security", "compliance", "pii"}
_ARCHITECTURE_SCOPES = {"architecture", "new_system", "integration"}

# Each role's default reviewer (None = top of the chain, e.g. the product owner).
_ROLE_REVIEWERS = {
    "product_owner": None,
    "project_manager": "product_owner",
    "scrum_master": "product_owner",
    "architect": "technical_lead",
    "technical_lead": "product_owner",
    "backend_engineer": "technical_lead",
    "frontend_engineer": "technical_lead",
    "mobile_engineer": "technical_lead",
    "data_engineer": "architect",
    "database_engineer": "architect",
    "qa_engineer": "technical_lead",
    "security_engineer": "technical_lead",
    "pentester": "security_engineer",
    "devops_engineer": "technical_lead",
    "release_manager": "technical_lead",
    "researcher": "technical_lead",
}

_ROLE_SKILLS = {
    "product_owner": ["brainstorming", "product-spec"],
    "project_manager": ["planning"],
    "scrum_master": ["planning"],
    "architect": ["architecture-software"],
    "technical_lead": ["engineering-standards"],
    "backend_engineer": ["engineering-standards"],
    "frontend_engineer": ["ui-ux-pro-max"],
    "mobile_engineer": ["engineering-standards"],
    "data_engineer": ["architecture-software"],
    "database_engineer": ["architecture-software"],
    "qa_engineer": ["systematic-debugging", "test-driven-development"],
    "security_engineer": ["security-review"],
    "pentester": ["security-review"],
    "devops_engineer": ["devops"],
    "release_manager": ["git-workflow"],
    "researcher": ["deep-research"],
}

# Tools are ToolBroker tool names; build roles touch the workspace, reviewers run shell scanners,
# reasoning roles stay read-only.
_BUILD_TOOLS = ["shell", "workspace_patch"]
_REVIEW_TOOLS = ["shell"]
_REASON_TOOLS: list[str] = []
_ROLE_EXTRA_TOOLS = {"researcher": ["mcp"]}

# Role-specific quality gates layered on top of the mode's base gates.
_ROLE_EXTRA_GATES = {
    "security_engineer": ["gitleaks", "semgrep"],
    "pentester": ["pentest", "exploit_validation"],
    "database_engineer": ["migration_check", "schema_review"],
    "release_manager": ["release_checklist", "rollback_plan"],
    "qa_engineer": ["regression"],
    "devops_engineer": ["deploy_dry_run"],
}
# The base-gate subset that applies to review roles (they verify, they don't build).
_REVIEW_GATES = {"test", "security_scan", "coverage", "e2e", "regression"}

_BUDGET_WEIGHT = {"build": 1.0, "review": 0.75, "reason": 0.5}

MODE_TIERS: dict[str, dict[str, Any]] = {
    "economy": {
        "modelTier": "economy",
        "budgetUsd": 1.0,
        "baseGates": ["lint", "test"],
        "reviewDepth": "self",
    },
    "balanced": {
        "modelTier": "standard",
        "budgetUsd": 4.0,
        "baseGates": ["lint", "test", "typecheck", "build"],
        "reviewDepth": "peer",
    },
    "critical": {
        "modelTier": "high",
        "budgetUsd": 10.0,
        "baseGates": ["lint", "test", "typecheck", "build", "security_scan", "coverage"],
        "reviewDepth": "gated",
    },
    "maximum": {
        "modelTier": "frontier",
        "budgetUsd": 25.0,
        "baseGates": [
            "lint",
            "test",
            "typecheck",
            "build",
            "security_scan",
            "coverage",
            "e2e",
            "performance",
            "pentest",
        ],
        "reviewDepth": "panel",
    },
}

# provider-kind per (mode, role kind): a CLASS of provider, not a concrete provider id, so the gateway
# can pick the healthiest/cheapest one in that class.
_PROVIDER_KIND = {
    "economy": {"build": "local", "review": "local", "reason": "local"},
    "balanced": {"build": "openai_compatible", "review": "openai_compatible", "reason": "openai_compatible"},
    "critical": {"build": "cli", "review": "cli", "reason": "openai_compatible"},
    "maximum": {"build": "cli", "review": "cli", "reason": "cli"},
}
_RUNTIME_BY_PROVIDER_KIND = {
    "local": "ollama",
    "openai_compatible": "openai_compatible",
    "cli": "claude_code_cli",
}


class TeamScheduleError(ValueError):
    """Se lanza cuando el modo o el riesgo solicitados no pertenecen al catálogo del scheduler."""


def _role_kind(role: str) -> str:
    if role in _BUILD_ROLES:
        return "build"
    if role in _REVIEW_ROLES:
        return "review"
    return "reason"


def _normalize_scope(scope: Any) -> set[str]:
    return {str(tag).strip().lower() for tag in (scope or []) if str(tag).strip()}


def select_roles(*, scope: set[str], risk: str, mode: str) -> set[str]:
    """Selecciona, de forma determinista, solo los roles que la tarea necesita por alcance y riesgo.

    Dos pasadas: la primera arma el equipo base (core + roles por scope/riesgo/modo); la segunda añade
    los roles de coordinación (project_manager/scrum_master) según el tamaño del equipo base, evitando
    la dependencia circular de medir un equipo que aún se está formando.
    """
    risk_level = _RISK_ORDER[risk]
    high = _RISK_ORDER["high"]
    base: set[str] = set(CORE_ROLES)
    for tag in scope:
        if tag in _SCOPE_ROLES:
            base.add(_SCOPE_ROLES[tag])
    has_engineer = bool(base & _BUILD_ROLES)
    if has_engineer and not (mode == "economy" and risk == "low"):
        base.add("qa_engineer")
    if (scope & _ARCHITECTURE_SCOPES) or risk_level >= high or mode in {"critical", "maximum"}:
        base.add("architect")
    if (scope & _SECURITY_SCOPES) or risk_level >= high or mode == "maximum":
        base.add("security_engineer")
    if ((scope & {"security", "pentest"}) or risk == "critical") and mode in {"critical", "maximum"}:
        base.add("pentester")
    if (scope & {"release", "deploy"}) or mode == "maximum":
        base.add("release_manager")
    # Second pass: coordination roles depend on the base team size (never on themselves).
    if mode == "maximum" or (risk == "critical" and len(base) >= 5):
        base.add("project_manager")
        base.add("scrum_master")
    return base


def _reviewer(role: str, *, scope: set[str], risk: str, mode: str) -> str | None:
    base = _ROLE_REVIEWERS.get(role)
    if base is None:
        return None
    security_sensitive = (
        bool(scope & _SECURITY_SENSITIVE_SCOPES)
        or _RISK_ORDER[risk] >= _RISK_ORDER["high"]
        or role in {"security_engineer", "pentester"}
    )
    # Self-review is only acceptable for low-stakes economy work; security/high-risk always escalate.
    if mode == "economy" and risk == "low" and not security_sensitive:
        return None
    return base


def _quality_gates(role: str, kind: str, tier: dict[str, Any]) -> list[str]:
    extras = _ROLE_EXTRA_GATES.get(role, [])
    if kind == "build":
        base = list(tier["baseGates"])
    elif kind == "review":
        base = [gate for gate in tier["baseGates"] if gate in _REVIEW_GATES]
    else:
        base = ["review"]
    seen: dict[str, None] = {}
    for gate in [*base, *extras]:
        seen.setdefault(gate, None)
    return list(seen)


def _tools_for(kind: str, role: str) -> list[str]:
    base = {"build": _BUILD_TOOLS, "review": _REVIEW_TOOLS}.get(kind, _REASON_TOOLS)
    return [*base, *_ROLE_EXTRA_TOOLS.get(role, [])]


def resolve_role(role: str, *, scope: set[str], risk: str, mode: str) -> dict[str, Any]:
    """Resuelve, para un rol seleccionado, su perfil de ejecución completo en el modo dado."""
    tier = MODE_TIERS[mode]
    kind = _role_kind(role)
    provider_kind = _PROVIDER_KIND[mode][kind]
    return {
        "role": role,
        "kind": kind,
        "permissionProfile": permission_profile_for({"role": role}),
        "providerKind": provider_kind,
        "modelTier": tier["modelTier"],
        "runtime": _RUNTIME_BY_PROVIDER_KIND[provider_kind],
        "skills": list(_ROLE_SKILLS.get(role, [])),
        "tools": _tools_for(kind, role),
        "budgetUsd": round(float(tier["budgetUsd"]) * _BUDGET_WEIGHT[kind], 2),
        "reviewer": _reviewer(role, scope=scope, risk=risk, mode=mode),
        "qualityGates": _quality_gates(role, kind, tier),
    }


def schedule_team(*, scope: Any, risk: str, mode: str) -> dict[str, Any]:
    """Compone el equipo mínimo para la tarea y resuelve la ejecución de cada rol en el modo dado.

    Args:
        scope: tags de alcance de la tarea (p. ej. ``["backend", "payments"]``); se normalizan a
            minúsculas y se deduplican.
        risk: nivel de riesgo (``low``/``medium``/``high``/``critical``).
        mode: modo de operación (``economy``/``balanced``/``critical``/``maximum``).

    Returns:
        Un plan determinista con ``schedulerVersion``, el contexto, el ``modelTier`` del modo, la lista
        de asignaciones por rol (orden estable) y un resumen de conteos.

    Raises:
        TeamScheduleError: si ``mode`` o ``risk`` no pertenecen al catálogo.
    """
    if mode not in MODE_TIERS:
        raise TeamScheduleError(f"Unknown team mode: {mode}. Expected one of {list(MODES)}.")
    if risk not in _RISK_ORDER:
        raise TeamScheduleError(f"Unknown task risk: {risk}. Expected one of {list(RISKS)}.")
    normalized_scope = _normalize_scope(scope)
    roles = select_roles(scope=normalized_scope, risk=risk, mode=mode)
    assignments = [resolve_role(role, scope=normalized_scope, risk=risk, mode=mode) for role in sorted(roles)]
    return {
        "schedulerVersion": SCHEDULER_VERSION,
        "mode": mode,
        "risk": risk,
        "scope": sorted(normalized_scope),
        "modelTier": MODE_TIERS[mode]["modelTier"],
        "reviewDepth": MODE_TIERS[mode]["reviewDepth"],
        "roles": assignments,
        "summary": {
            "roleCount": len(assignments),
            "roles": [assignment["role"] for assignment in assignments],
            "reviewedRoles": sum(1 for assignment in assignments if assignment["reviewer"] is not None),
            "totalBudgetUsd": round(sum(assignment["budgetUsd"] for assignment in assignments), 2),
        },
    }
