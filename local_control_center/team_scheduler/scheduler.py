"""TeamScheduler determinista: selecciona los roles que la tarea necesita y resuelve su ejecución.

Lógica pura (sin DB ni I/O) que mapea ``(scope, risk, mode)`` a un plan de equipo estable. Selecciona
solo los roles requeridos por alcance y riesgo —un core fijo más roles dirigidos por scope, riesgo y
modo— en dos pasadas para resolver la dependencia circular de los roles de coordinación (PM/SM dependen
del tamaño del equipo base). Por cada rol resuelve permission profile (de
``security_policy.policy_engine.PROFILE_DEFAULTS``, fuente única de verdad), provider-kind y model tier
(tokens de tier, no ids de modelo concretos —el model gateway resuelve el modelo vigente), runtime,
skills, tools, presupuesto, reviewer y quality gates. El reviewer nunca es ``self`` para roles o
contextos sensibles a seguridad ni con riesgo alto, sin importar el modo.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.agents.provider_catalog import PROVIDER_CATALOG, PROVIDER_CATALOG_VERSION
from local_control_center.security_policy.policy_engine import permission_profile_for

# Versions the role-selection/scheduling algorithm. Provider/default-candidate changes are
# independently traceable through PROVIDER_CATALOG_VERSION and persisted profile metadata.
SCHEDULER_VERSION = 2

CORE_ROLES = ("product_owner", "technical_lead")
ALL_ROLES = (
    "aido_lead",
    "product_owner",
    "project_manager",
    "scrum_master",
    "architect",
    "technical_lead",
    "backend_engineer",
    "frontend_engineer",
    "mobile_engineer",
    "database_engineer",
    "data_engineer",
    "qa_engineer",
    "security_engineer",
    "pentester",
    "devops_engineer",
    "researcher",
    "release_manager",
)

MODES = ("economy", "balanced", "critical", "maximum")
RISKS = ("low", "medium", "high", "critical")
_RISK_ORDER = {risk: index for index, risk in enumerate(RISKS)}

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
_SECURITY_SCOPES = {"security", "auth", "data", "external", "payments", "compliance", "pii"}
_SECURITY_SENSITIVE_SCOPES = {"payments", "auth", "security", "compliance", "pii"}
_ARCHITECTURE_SCOPES = {"architecture", "new_system", "integration"}
_COORDINATION_SCOPES = {"coordination", "program", "portfolio", "roadmap", "planning", "release"}

_ROLE_REVIEWERS = {
    "aido_lead": None,
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
    "aido_lead": ["engineering-standards", "planning"],
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

_ROLE_NAMES = {
    "aido_lead": "AIDO Lead / Coordinator",
    "product_owner": "Product Owner",
    "project_manager": "Project Manager",
    "scrum_master": "Scrum Master",
    "architect": "Architect",
    "technical_lead": "Technical Lead",
    "backend_engineer": "Backend Engineer",
    "frontend_engineer": "Frontend Engineer",
    "mobile_engineer": "Mobile Engineer",
    "database_engineer": "Database Engineer",
    "data_engineer": "Data Engineer",
    "qa_engineer": "QA Engineer",
    "security_engineer": "Security Engineer",
    "pentester": "Pentester",
    "devops_engineer": "DevOps Engineer",
    "researcher": "Researcher",
    "release_manager": "Release Manager",
}

_ROLE_CAPABILITIES = {
    "aido_lead": ["team_orchestration", "risk_triage", "handoff_review"],
    "product_owner": ["product_discovery", "brief_authoring", "acceptance_criteria"],
    "project_manager": ["delivery_planning", "dependency_tracking", "schedule_review"],
    "scrum_master": ["flow_facilitation", "blocker_tracking", "cadence_review"],
    "architect": ["system_design", "contract_review", "adr_authoring"],
    "technical_lead": ["task_decomposition", "code_review", "quality_strategy"],
    "backend_engineer": ["code_edit", "api_design", "service_implementation"],
    "frontend_engineer": ["code_edit", "ui_implementation", "accessibility"],
    "mobile_engineer": ["code_edit", "mobile_implementation", "device_validation"],
    "database_engineer": ["schema_design", "migration_authoring", "data_integrity"],
    "data_engineer": ["pipeline_design", "analytics_modeling", "data_quality"],
    "qa_engineer": ["test_design", "regression", "evidence_review"],
    "security_engineer": ["security_review", "threat_modeling", "secret_scanning"],
    "pentester": ["adversarial_testing", "exploit_validation", "abuse_case_review"],
    "devops_engineer": ["ci_cd", "deployment", "runtime_operations"],
    "researcher": ["source_research", "citation_review", "option_analysis"],
    "release_manager": ["release_readiness", "rollback_planning", "change_control"],
}

_ROLE_REQUIRED_INPUT_ARTIFACTS = {
    "aido_lead": ["team_schedule", "risk_register", "handoff_log"],
    "product_owner": ["user_message", "project_assessment"],
    "project_manager": ["team_schedule", "delivery_plan"],
    "scrum_master": ["team_schedule", "blocker_log"],
    "architect": ["product_brief", "project_assessment", "constraints"],
    "technical_lead": ["product_owner_output", "product_backlog"],
    "backend_engineer": ["agent_task", "acceptance_criteria", "product_backlog"],
    "frontend_engineer": ["agent_task", "acceptance_criteria", "product_backlog"],
    "mobile_engineer": ["agent_task", "acceptance_criteria", "product_backlog"],
    "database_engineer": ["agent_task", "schema_context", "migration_plan"],
    "data_engineer": ["agent_task", "data_context", "quality_requirements"],
    "qa_engineer": ["agent_task", "acceptance_criteria", "implementation_artifact"],
    "security_engineer": ["agent_task", "threat_context", "implementation_artifact"],
    "pentester": ["security_review", "target_scope", "authorization_context"],
    "devops_engineer": ["agent_task", "runtime_context", "deployment_constraints"],
    "researcher": ["research_question", "source_policy"],
    "release_manager": ["release_candidate", "quality_evidence", "rollback_plan"],
}

_BASE_ROLE_PROVIDER_PREFERENCE = {
    "aido_lead": ["claude_code_cli", "codex_cli", "openai_compatible"],
    "product_owner": [
        "gemini",
        "ollama",
        "codex_cli",
        "claude_code_cli",
        "openai_compatible",
        "openrouter",
        "nvidia_nim",
        "anthropic_api",
    ],
    "project_manager": ["openai_compatible", "ollama"],
    "scrum_master": ["openai_compatible", "ollama"],
    "architect": ["claude_code_cli", "openai_compatible", "nvidia_nim"],
    "technical_lead": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "backend_engineer": ["codex_cli", "claude_code_cli", "openhands", "swe_agent"],
    "frontend_engineer": ["codex_cli", "claude_code_cli", "openhands", "swe_agent"],
    "mobile_engineer": ["codex_cli", "claude_code_cli"],
    "database_engineer": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "data_engineer": ["codex_cli", "openai_compatible", "nvidia_nim"],
    "qa_engineer": ["codex_cli", "claude_code_cli", "openai_compatible"],
    "security_engineer": ["claude_code_cli", "openai_compatible"],
    "pentester": ["claude_code_cli", "openai_compatible"],
    "devops_engineer": ["codex_cli", "claude_code_cli"],
    "researcher": ["openai_compatible", "openrouter", "nvidia_nim", "anthropic_api"],
    "release_manager": ["claude_code_cli", "codex_cli"],
}

# ``PROVIDER_CATALOG`` owns the API/local catalog. The remaining entries are technical runtimes
# already catalogued by the control plane. Keeping them concrete here is intentional: wildcard
# permission belongs in ``allowedProviders``; candidate selection must remain ordered and auditable.
_TECHNICAL_PROVIDER_IDS = (
    "codex_cli",
    "claude_code_cli",
    "openhands",
    "swe_agent",
    "manual",
)
_KNOWN_PROVIDER_IDS = tuple(
    dict.fromkeys([*(entry.id for entry in PROVIDER_CATALOG), *_TECHNICAL_PROVIDER_IDS])
)
_ROLE_PROVIDER_PREFERENCE = {
    role: list(dict.fromkeys([*preference, *_KNOWN_PROVIDER_IDS]))
    for role, preference in _BASE_ROLE_PROVIDER_PREFERENCE.items()
}

_ALL_RUNTIME_SELECTORS = ["*"]

_ROLE_RUNTIME_PREFERENCE = {role: ["cli", "api"] for role in ALL_ROLES}
_ROLE_RUNTIME_PREFERENCE.update(
    {
        "project_manager": ["api", "ollama"],
        "scrum_master": ["api", "ollama"],
        "backend_engineer": ["cli"],
        "frontend_engineer": ["cli"],
        "mobile_engineer": ["cli"],
        "devops_engineer": ["cli"],
        "researcher": ["api"],
        "release_manager": ["cli"],
    }
)

_BUILD_TOOLS = ["shell", "workspace_patch"]
_REVIEW_TOOLS = ["shell"]
_REASON_TOOLS: list[str] = []
_ROLE_EXTRA_TOOLS = {
    "aido_lead": ["policy.evaluate", "evidence.create", "mcp"],
    "product_owner": ["evidence.create", "mcp"],
    "project_manager": ["evidence.create"],
    "scrum_master": ["evidence.create"],
    "architect": ["policy.evaluate", "evidence.create", "mcp"],
    "technical_lead": ["policy.evaluate", "evidence.create"],
    "qa_engineer": ["evidence.create"],
    "security_engineer": ["policy.evaluate", "evidence.create"],
    "pentester": ["policy.evaluate", "evidence.create"],
    "devops_engineer": ["evidence.create"],
    "researcher": ["mcp", "evidence.create"],
    "release_manager": ["policy.evaluate", "evidence.create"],
}

_ROLE_EXTRA_GATES = {
    "security_engineer": ["gitleaks", "semgrep"],
    "pentester": ["pentest", "exploit_validation"],
    "database_engineer": ["migration_check", "schema_review"],
    "release_manager": ["release_checklist", "rollback_plan"],
    "qa_engineer": ["regression"],
    "devops_engineer": ["deploy_dry_run"],
}
_REVIEW_GATES = {"test", "security_scan", "coverage", "e2e", "regression"}

_BUDGET_WEIGHT = {"build": 1.0, "review": 0.75, "reason": 0.5}

_PERMISSION_PROFILE_BY_ROLE = {
    "qa_engineer": "qa",
    "security_engineer": "qa",
    "pentester": "qa",
    "release_manager": "release",
}

_ROLE_COST_LIMITS = {
    "aido_lead": (6.0, 120000, 900, 3.0),
    "product_owner": (4.0, 100000, 900, 2.0),
    "project_manager": (1.5, 60000, 600, 1.0),
    "scrum_master": (1.0, 50000, 600, 1.0),
    "architect": (8.0, 160000, 900, 4.0),
    "technical_lead": (6.0, 140000, 900, 3.0),
    "backend_engineer": (5.0, 120000, 1200, 2.0),
    "frontend_engineer": (5.0, 120000, 1200, 2.0),
    "mobile_engineer": (5.0, 120000, 1200, 2.0),
    "database_engineer": (4.0, 100000, 900, 2.0),
    "data_engineer": (4.0, 100000, 900, 2.0),
    "qa_engineer": (3.0, 80000, 900, 1.5),
    "security_engineer": (5.0, 120000, 900, 2.0),
    "pentester": (5.0, 100000, 900, 2.0),
    "devops_engineer": (4.0, 90000, 900, 2.0),
    "researcher": (4.0, 100000, 900, 2.0),
    "release_manager": (3.0, 80000, 900, 1.5),
}

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

_REVIEWER_MODEL_TIER = {
    "economy": "standard",
    "balanced": "high",
    "critical": "frontier",
    "maximum": "frontier",
}


class TeamScheduleError(ValueError):
    """Se lanza cuando el modo o el riesgo solicitados no pertenecen al catálogo del scheduler."""


def _role_kind(role: str) -> str:
    if role in _BUILD_ROLES:
        return "build"
    if role in _REVIEW_ROLES:
        return "review"
    return "reason"


def _output_artifact_schema(role: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["artifactId", "summary", "status", "evidenceRefs", "qualityGates"],
        "properties": {
            "artifactId": {"type": "string"},
            "summary": {"type": "string"},
            "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
            "role": {"type": "string", "const": role},
            "qualityGates": {"type": "array", "items": {"type": "string"}},
            "quality_gates": {"type": "array", "items": {"type": "string"}},
            "evidenceRefs": {"type": "array", "items": {"type": "string"}},
            "riskNotes": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _reviewer_policy(role: str, *, scope: set[str], risk: str, mode: str) -> dict[str, Any]:
    reviewer = _reviewer(role, scope=scope, risk=risk, mode=mode)
    if reviewer is None:
        return {
            "mode": "none" if mode == "economy" and risk == "low" else MODE_TIERS[mode]["reviewDepth"],
            "reviewerRole": None,
            "requiresDifferentModel": False,
            "reviewerModelTier": None,
        }
    requires_different_model = (
        mode in {"balanced", "critical", "maximum"} or _RISK_ORDER[risk] >= _RISK_ORDER["high"]
    )
    return {
        "mode": MODE_TIERS[mode]["reviewDepth"],
        "reviewerRole": reviewer,
        "requiresDifferentModel": requires_different_model,
        "reviewerModelTier": _REVIEWER_MODEL_TIER[mode]
        if requires_different_model
        else MODE_TIERS[mode]["modelTier"],
    }


def _permission_profile(role: str) -> str:
    return _PERMISSION_PROFILE_BY_ROLE.get(role) or permission_profile_for({"role": role})


def _default_runtime_policy(role: str) -> dict[str, Any]:
    capabilities = _ROLE_CAPABILITIES[role]
    required = ["code_edit"] if "code_edit" in capabilities else ["chat"]
    return {
        "providerCandidates": list(_ROLE_PROVIDER_PREFERENCE[role]),
        "requiredCapabilities": required,
        "selection": "first_executable",
        "fallback": "blocked_with_reason",
    }


def _default_role_record(role: str) -> dict[str, Any]:
    max_cost, max_tokens, max_seconds, approval_usd = _ROLE_COST_LIMITS[role]
    profile = resolve_role(role, scope=set(), risk="medium", mode="balanced")
    return {
        "id": f"base-{role.replace('_', '-')}",
        "name": _ROLE_NAMES[role],
        "role": role,
        "runtimeMode": _ROLE_RUNTIME_PREFERENCE[role][0],
        "allowedProviders": ["*"],
        "allowedRuntimes": list(_ALL_RUNTIME_SELECTORS),
        "allowedTools": profile["toolsAllowed"],
        "allowedSkills": profile["skills"],
        "permissionProfile": _permission_profile(role),
        "maxCostPerRun": max_cost,
        "maxTokensPerRun": max_tokens,
        "maxRuntimeSeconds": max_seconds,
        "requiresApprovalOverUsd": approval_usd,
        "qualityGates": profile["qualityGates"],
        "defaultRuntimePolicy": _default_runtime_policy(role),
        "reviewerPolicy": profile["reviewerPolicy"],
        "outputSchema": profile["outputArtifactSchema"],
        "metadata": {
            "schedulerVersion": SCHEDULER_VERSION,
            "providerCatalogVersion": PROVIDER_CATALOG_VERSION,
            "capabilities": list(_ROLE_CAPABILITIES[role]),
            "requiredInputArtifacts": list(_ROLE_REQUIRED_INPUT_ARTIFACTS[role]),
            "providerPreference": list(_ROLE_PROVIDER_PREFERENCE[role]),
            "runtimePreference": list(_ROLE_RUNTIME_PREFERENCE[role]),
        },
    }


def team_profiles() -> list[dict[str, Any]]:
    """Return the complete available team-profile catalog for persistence and UI read surfaces."""
    rows: list[dict[str, Any]] = []
    for role in ALL_ROLES:
        record = _default_role_record(role)
        rows.append(
            {
                "id": f"team-profile-{role.replace('_', '-')}",
                "role": role,
                "name": record["name"],
                "capabilities": list(_ROLE_CAPABILITIES[role]),
                "runtimePreference": record["metadata"]["runtimePreference"],
                "providerPreference": record["metadata"]["providerPreference"],
                "toolsAllowed": record["allowedTools"],
                "requiredInputArtifacts": record["metadata"]["requiredInputArtifacts"],
                "outputArtifactSchema": record["outputSchema"],
                "reviewerPolicy": record["reviewerPolicy"],
                "qualityGates": record["qualityGates"],
                "metadata": {"schedulerVersion": SCHEDULER_VERSION, "agentProfileId": record["id"]},
            }
        )
    return rows


def team_member_defaults() -> list[dict[str, Any]]:
    """Return the default executable member profile for every available role."""
    return [_default_role_record(role) for role in ALL_ROLES]


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
    if (scope & {"security", "pentest", "pentester"}) or (
        risk == "critical" and mode in {"critical", "maximum"}
    ):
        base.add("pentester")
    if (scope & {"release", "deploy"}) or mode == "maximum":
        base.add("release_manager")
    if (
        (scope & _COORDINATION_SCOPES)
        or risk_level >= high
        or mode in {"critical", "maximum"}
        or len(base) >= 5
    ):
        base.add("aido_lead")
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
    reviewer_policy = _reviewer_policy(role, scope=scope, risk=risk, mode=mode)
    tools = _tools_for(kind, role)
    return {
        "role": role,
        "name": _ROLE_NAMES[role],
        "kind": kind,
        "capabilities": list(_ROLE_CAPABILITIES[role]),
        "permissionProfile": _permission_profile(role),
        "providerKind": provider_kind,
        "providerPreference": list(_ROLE_PROVIDER_PREFERENCE[role]),
        "modelTier": tier["modelTier"],
        "runtime": _RUNTIME_BY_PROVIDER_KIND[provider_kind],
        "runtimePreference": list(_ROLE_RUNTIME_PREFERENCE[role]),
        "skills": list(_ROLE_SKILLS.get(role, [])),
        "tools": tools,
        "toolsAllowed": tools,
        "requiredInputArtifacts": list(_ROLE_REQUIRED_INPUT_ARTIFACTS[role]),
        "outputArtifactSchema": _output_artifact_schema(role),
        "budgetUsd": round(float(tier["budgetUsd"]) * _BUDGET_WEIGHT[kind], 2),
        "reviewer": _reviewer(role, scope=scope, risk=risk, mode=mode),
        "reviewerPolicy": reviewer_policy,
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
