"""TechnicalLeadPlanner: convierte HU aprobadas en tareas tecnicas por rol.

El planner es deliberadamente deterministico: no ejecuta agentes ni inventa nuevas HU. Consume el
brief, las historias, sus criterios, assessment, clasificacion de intencion, riesgo y equipo
disponible; produce un plan trazable con agent_tasks, dependencias, handoffs, gates y estrategia
branch/worktree.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from typing import Any

from local_control_center.team_scheduler.scheduler import ALL_ROLES, resolve_role

TECHNICAL_LEAD_PLANNER_ID = "technical_lead_planner"

ROLE_ALIASES = {
    "tl": "technical_lead",
    "tech_lead": "technical_lead",
    "backend": "backend_engineer",
    "backend_developer": "backend_engineer",
    "frontend": "frontend_engineer",
    "front_end": "frontend_engineer",
    "qa": "qa_engineer",
    "quality_assurance": "qa_engineer",
    "security": "security_engineer",
    "security_reviewer": "security_engineer",
    "pentest": "pentester",
    "devops": "devops_engineer",
}

ROLE_ORDER = {
    "technical_lead": 5,
    "architect": 8,
    "backend_engineer": 10,
    "database_engineer": 15,
    "data_engineer": 16,
    "frontend_engineer": 20,
    "mobile_engineer": 21,
    "security_engineer": 30,
    "pentester": 35,
    "devops_engineer": 40,
    "qa_engineer": 50,
}

IMPLEMENTATION_ROLES = {
    "backend_engineer",
    "frontend_engineer",
    "mobile_engineer",
    "database_engineer",
    "data_engineer",
}
COORDINATION_ROLES = {"technical_lead", "architect"}
REVIEW_ROLES = {"qa_engineer", "security_engineer", "pentester"}
DEVOPS_ROLE = "devops_engineer"
KNOWN_RISKS = {"low", "medium", "high", "critical"}
HIGH_RISKS = {"high", "critical"}
WORKTREE_CAPABLE_RUNTIMES = {"codex_cli", "claude_code_cli", "openhands", "swe_agent", "cli"}

FRONTEND_HINTS = (
    "frontend",
    "front-end",
    "ui",
    "ux",
    "web",
    "react",
    "vue",
    "component",
    "screen",
    "page",
    "form",
    "browser",
    ".tsx",
    ".jsx",
    ".css",
)
BACKEND_HINTS = (
    "backend",
    "back-end",
    "api",
    "endpoint",
    "service",
    "server",
    "controller",
    "repository",
    "database",
    "persist",
    "store",
    ".py",
    ".java",
    ".sql",
)
SECURITY_HINTS = (
    "auth",
    "login",
    "credential",
    "password",
    "token",
    "secret",
    "permission",
    "pii",
    "personal data",
    "payment",
    "billing",
    "compliance",
    "encryption",
    "security",
    "pentest",
    "threat",
)
EXPOSED_SURFACE_HINTS = (
    "public",
    "external",
    "exposed",
    "internet-facing",
    "webhook",
    "partner system",
    "authenticated portal",
)
ARCHITECTURE_HINTS = (
    "architecture",
    "architectural",
    "adr",
    "migration",
    "modernization",
    "modernize",
    "integration",
    "new system",
    "contract",
)
DEVOPS_HINTS = (
    "build",
    "deploy",
    "deployment",
    "runtime",
    "worker",
    "ci",
    "cd",
    "pipeline",
    "docker",
    "compose",
    "kubernetes",
    "helm",
    "terraform",
    "cloudflare",
    "vercel",
    "netlify",
    "release",
    "rollback",
)
DEVOPS_FILE_HINTS = (
    "dockerfile",
    "docker-compose",
    ".github/",
    ".gitlab-ci",
    "jenkinsfile",
    "package.json",
    "pnpm-lock.yaml",
    "uv.lock",
    "pyproject.toml",
    "vite.config",
    "start_control_center",
    "scripts/",
    "deploy",
    "runtime",
    "worker",
)

ROLE_FILE_HINTS = {
    "technical_lead": ["backlog", "product_loop", "tests_py"],
    "architect": ["architecture", "docs/adr", "contracts"],
    "backend_engineer": ["api", "service", "repository", "models", "tests_py"],
    "frontend_engineer": ["web/src", "components", "features", ".tsx", ".css"],
    "mobile_engineer": ["mobile", "app"],
    "database_engineer": ["migrations", ".sql", "schema"],
    "data_engineer": ["data", "analytics", "pipeline"],
    "qa_engineer": ["tests_py", "tests_web", "playwright"],
    "security_engineer": ["security", "policy", "auth", "credentials"],
    "pentester": ["security", "auth", "api"],
    "devops_engineer": ["scripts", ".github", "docker", "runtime", "deploy"],
}

ROLE_GOALS = {
    "technical_lead": "Turn ProductOwner intent into an executable technical plan and validate handoffs.",
    "architect": "Validate architecture, contract and migration decisions required by the story.",
    "backend_engineer": "Implement backend/API behavior required by the story and its acceptance criteria.",
    "frontend_engineer": "Implement the user-facing UI behavior required by the story and its acceptance criteria.",
    "mobile_engineer": "Implement the mobile client behavior required by the story and its acceptance criteria.",
    "database_engineer": "Apply data model and migration work required by the story.",
    "data_engineer": "Implement data pipeline or analytics work required by the story.",
    "qa_engineer": "Validate the completed story against all referenced acceptance criteria and regressions.",
    "security_engineer": "Review security-sensitive implementation paths and required controls.",
    "pentester": "Run adversarial validation over the approved security scope.",
    "devops_engineer": "Validate build, deploy, runtime and rollback impact for the story.",
}


class TechnicalLeadPlannerError(ValueError):
    """Raised when TechnicalLeadPlanner receives invalid planning input."""


def _canonical_role(role: Any) -> str:
    normalized = str(role or "").strip().lower().replace("-", "_")
    return ROLE_ALIASES.get(normalized, normalized)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "task"


def _text_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for item in value.values() for text in _text_values(item)]
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    if value is None:
        return []
    return [str(value)]


def _haystack(*values: Any) -> str:
    return " ".join(text.lower() for value in values for text in _text_values(value))


def _risk_level(risk: Any, intent: dict[str, Any]) -> str:
    candidates: list[Any] = []
    if isinstance(risk, dict):
        candidates.extend([risk.get("level"), risk.get("severity"), risk.get("risk")])
    else:
        candidates.append(risk)
    candidates.extend([intent.get("risk"), intent.get("riskLevel")])
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if normalized in KNOWN_RISKS:
            return normalized
    return "medium"


def _risk_context(risk: Any, intent: dict[str, Any]) -> dict[str, Any]:
    level = _risk_level(risk, intent)
    if isinstance(risk, dict):
        return {"level": level, "items": risk.get("items") or risk.get("risks") or [], "source": risk}
    return {"level": level, "items": [], "source": risk}


def _team_by_role(available_team: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_role: dict[str, dict[str, Any]] = {}
    for member in available_team:
        if not isinstance(member, dict):
            continue
        role = _canonical_role(member.get("role"))
        if role:
            by_role.setdefault(role, member)
    return by_role


def _available_team_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    team = payload.get("availableTeam")
    if isinstance(team, list) and team:
        return [member for member in team if isinstance(member, dict)]
    schedule = payload.get("teamSchedule")
    if isinstance(schedule, dict) and isinstance(schedule.get("roles"), list):
        return [
            {
                "agentId": role.get("agentId") or role.get("id") or f"planned-{role.get('role')}",
                "role": role.get("role"),
                "allowedTools": role.get("toolsAllowed") or role.get("tools") or [],
                "runtimePreference": role.get("runtimePreference") or [],
                "outputSchema": role.get("outputArtifactSchema") or role.get("outputSchema") or {},
                "reviewerPolicy": role.get("reviewerPolicy") or {},
            }
            for role in schedule["roles"]
            if isinstance(role, dict)
        ]
    return [
        {"agentId": f"planned-{role}", "role": role}
        for role in (
            "technical_lead",
            "architect",
            "backend_engineer",
            "frontend_engineer",
            "qa_engineer",
            "security_engineer",
            "pentester",
            "devops_engineer",
        )
    ]


def _flat_acceptance_criteria(payload: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = payload.get("acceptanceCriteria")
    if isinstance(criteria, list):
        return [item for item in criteria if isinstance(item, dict)]
    backlog = payload.get("backlog")
    flattened: list[dict[str, Any]] = []
    if isinstance(backlog, list):
        for epic_group in backlog:
            if not isinstance(epic_group, dict):
                continue
            for item in epic_group.get("stories") or []:
                if not isinstance(item, dict):
                    continue
                story = item.get("story") if isinstance(item.get("story"), dict) else {}
                story_id = story.get("id")
                for index, criterion in enumerate(item.get("acceptanceCriteria") or [], start=1):
                    if isinstance(criterion, dict):
                        flattened.append(criterion)
                    else:
                        flattened.append(
                            {
                                "id": f"{story_id}-ac-{index}",
                                "storyId": story_id,
                                "criterion": str(criterion),
                            }
                        )
    return flattened


def _criteria_for_story(story: dict[str, Any], criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    story_id = story["id"]
    matched = [criterion for criterion in criteria if str(criterion.get("storyId")) == str(story_id)]
    if matched:
        return matched
    inline = story.get("acceptanceCriteria")
    if not isinstance(inline, list):
        return []
    return [
        {
            "id": f"{story_id}-ac-{index}",
            "storyId": story_id,
            "criterion": str(item.get("criterion") if isinstance(item, dict) else item),
        }
        for index, item in enumerate(inline, start=1)
        if str(item.get("criterion") if isinstance(item, dict) else item).strip()
    ]


def _role_profile(
    role: str, member: dict[str, Any] | None, *, scope: set[str], risk_level: str
) -> dict[str, Any]:
    mode = "critical" if risk_level in HIGH_RISKS else "balanced"
    base = resolve_role(role, scope=scope, risk=risk_level, mode=mode) if role in ALL_ROLES else {}
    member = member or {}
    output_schema = (
        member.get("outputSchema") or member.get("outputArtifactSchema") or base.get("outputArtifactSchema")
    )
    reviewer_policy = member.get("reviewerPolicy") or base.get("reviewerPolicy") or {}
    runtime_preference = (
        member.get("runtimePreference")
        or member.get("allowedProviders")
        or base.get("runtimePreference")
        or []
    )
    if not runtime_preference and base.get("runtime"):
        runtime_preference = [base["runtime"]]
    return {
        "requiredTools": list(
            member.get("allowedTools") or member.get("toolsAllowed") or base.get("tools") or []
        ),
        "runtimePreference": list(runtime_preference),
        "outputSchema": output_schema or _default_output_schema(role),
        "reviewerRole": reviewer_policy.get("reviewerRole") or base.get("reviewer") or "technical_lead",
        "qualityGates": list(member.get("qualityGates") or base.get("qualityGates") or []),
    }


def _default_output_schema(role: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["artifactId", "summary", "status", "evidenceRefs"],
        "properties": {
            "artifactId": {"type": "string"},
            "summary": {"type": "string"},
            "status": {"type": "string", "enum": ["completed", "blocked", "failed"]},
            "role": {"type": "string", "const": role},
            "evidenceRefs": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def _story_scope(
    story: dict[str, Any], product_brief: dict[str, Any], assessment: dict[str, Any]
) -> set[str]:
    text = _haystack(story, product_brief, assessment.get("summary"), assessment.get("changedFiles"))
    scope: set[str] = set()
    if any(hint in text for hint in FRONTEND_HINTS):
        scope.add("frontend")
    if any(hint in text for hint in BACKEND_HINTS):
        scope.add("backend")
    if "mobile" in text or "ios" in text or "android" in text:
        scope.add("mobile")
    if "migration" in text or "schema" in text or ".sql" in text:
        scope.add("database")
    if "pipeline" in text or "analytics" in text or "dataset" in text:
        scope.add("data")
    return scope


def _security_active(
    *,
    story: dict[str, Any],
    criteria: list[dict[str, Any]],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    risk_context: dict[str, Any],
    assessment: dict[str, Any],
) -> bool:
    text = _haystack(story, criteria, product_brief, intent, risk_context, assessment)
    return (
        risk_context["level"] in HIGH_RISKS
        or any(hint in text for hint in SECURITY_HINTS)
        or any(hint in text for hint in EXPOSED_SURFACE_HINTS)
    )


def _pentest_active(
    *,
    story: dict[str, Any],
    criteria: list[dict[str, Any]],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    risk_context: dict[str, Any],
    assessment: dict[str, Any],
) -> bool:
    text = _haystack(story, criteria, product_brief, intent, risk_context, assessment)
    return (
        risk_context["level"] in HIGH_RISKS
        or "pentest" in text
        or any(hint in text for hint in EXPOSED_SURFACE_HINTS)
    )


def _architecture_active(
    *,
    story: dict[str, Any],
    criteria: list[dict[str, Any]],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    risk_context: dict[str, Any],
    assessment: dict[str, Any],
) -> bool:
    text = _haystack(story, criteria, product_brief, intent, risk_context, assessment)
    return risk_context["level"] in HIGH_RISKS or any(hint in text for hint in ARCHITECTURE_HINTS)


def _technical_lead_active(
    *,
    story: dict[str, Any],
    criteria: list[dict[str, Any]],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    risk_context: dict[str, Any],
    assessment: dict[str, Any],
) -> bool:
    return _architecture_active(
        story=story,
        criteria=criteria,
        product_brief=product_brief,
        intent=intent,
        risk_context=risk_context,
        assessment=assessment,
    )


def _devops_active(
    *,
    story: dict[str, Any],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    assessment: dict[str, Any],
) -> bool:
    text = _haystack(story, product_brief, intent.get("intents"), intent.get("requiredGates"))
    files = [str(path).lower().replace("\\", "/") for path in assessment.get("changedFiles") or []]
    return any(hint in text for hint in DEVOPS_HINTS) or any(
        any(hint in path for hint in DEVOPS_FILE_HINTS) for path in files
    )


def _roles_for_story(
    *,
    story: dict[str, Any],
    criteria: list[dict[str, Any]],
    product_brief: dict[str, Any],
    intent: dict[str, Any],
    risk_context: dict[str, Any],
    assessment: dict[str, Any],
    available_roles: set[str],
) -> list[str]:
    explicit_roles = [
        _canonical_role(role)
        for role in [
            *(story.get("roles") or []),
            *(story.get("requiredRoles") or []),
            *(intent.get("requiredRoles") or []),
        ]
    ]
    roles = [role for role in explicit_roles if role in IMPLEMENTATION_ROLES]
    if "technical_lead" in explicit_roles:
        roles.append("technical_lead")
    if "architect" in explicit_roles:
        roles.append("architect")
    scope = _story_scope(story, product_brief, assessment)
    if "backend" in scope:
        roles.append("backend_engineer")
    if "frontend" in scope:
        roles.append("frontend_engineer")
    if "mobile" in scope:
        roles.append("mobile_engineer")
    if "database" in scope:
        roles.append("database_engineer")
    if "data" in scope:
        roles.append("data_engineer")
    available_implementation_roles = [
        role
        for role in roles
        if role in IMPLEMENTATION_ROLES and (not available_roles or role in available_roles)
    ]
    if not available_implementation_roles:
        fallback = (
            "backend_engineer"
            if "backend_engineer" in available_roles
            else next(
                (role for role in ROLE_ORDER if role in available_roles and role in IMPLEMENTATION_ROLES),
                "backend_engineer",
            )
        )
        roles.append(fallback)
    if _technical_lead_active(
        story=story,
        criteria=criteria,
        product_brief=product_brief,
        intent=intent,
        risk_context=risk_context,
        assessment=assessment,
    ):
        roles.append("technical_lead")
    if _architecture_active(
        story=story,
        criteria=criteria,
        product_brief=product_brief,
        intent=intent,
        risk_context=risk_context,
        assessment=assessment,
    ):
        roles.append("architect")
    if any(role in IMPLEMENTATION_ROLES for role in roles):
        roles.append("qa_engineer")
    if _security_active(
        story=story,
        criteria=criteria,
        product_brief=product_brief,
        intent=intent,
        risk_context=risk_context,
        assessment=assessment,
    ):
        roles.append("security_engineer")
        if _pentest_active(
            story=story,
            criteria=criteria,
            product_brief=product_brief,
            intent=intent,
            risk_context=risk_context,
            assessment=assessment,
        ):
            roles.append("pentester")
    if _devops_active(story=story, product_brief=product_brief, intent=intent, assessment=assessment):
        roles.append(DEVOPS_ROLE)
    return sorted(
        [role for role in _dedupe(roles) if not available_roles or role in available_roles],
        key=lambda role: ROLE_ORDER.get(role, 100),
    )


def _files_likely(role: str, assessment: dict[str, Any]) -> list[str]:
    changed = [str(path) for path in assessment.get("changedFiles") or []]
    hints = ROLE_FILE_HINTS.get(role, [])
    matched = [path for path in changed if any(hint in path.lower().replace("\\", "/") for hint in hints)]
    if matched:
        return matched[:8]
    return [f"<project>/{hint}" for hint in hints[:3]]


def _task_title(role: str, story: dict[str, Any]) -> str:
    label = role.replace("_", " ").title()
    return f"{label}: {story['title']}"


def _branch_name(
    payload: dict[str, Any], product_brief: dict[str, Any], stories: list[dict[str, Any]]
) -> str:
    intent = payload.get("intentClassification") or {}
    suggested = str(intent.get("suggestedBranchName") or "").strip()
    if suggested:
        return suggested
    source = product_brief.get("title") or (stories[0].get("title") if stories else "technical-lead-plan")
    return f"codex/{_slug(str(source))[:48]}"


def _quality_gates(tasks: list[dict[str, Any]], intent: dict[str, Any]) -> list[dict[str, Any]]:
    ids = ["acceptance_criteria_traceability", "lint", "unit_tests"]
    roles = {task["role"] for task in tasks}
    if roles & {"backend_engineer", "frontend_engineer", "mobile_engineer", "database_engineer"}:
        ids.extend(["typecheck", "build"])
    if "frontend_engineer" in roles:
        ids.append("accessibility")
    if len(roles & {"backend_engineer", "frontend_engineer", "mobile_engineer"}) >= 2:
        ids.append("integration_tests")
    if "security_engineer" in roles:
        ids.extend(["security_scan", "threat_model"])
    if "pentester" in roles:
        ids.append("pentest")
    if DEVOPS_ROLE in roles:
        ids.extend(["deploy_dry_run", "rollback_plan"])
    ids.extend(str(gate) for gate in intent.get("requiredGates") or [])
    return [{"id": gate, "blocking": True} for gate in _dedupe(ids)]


def _task_dependencies(tasks: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_story: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        by_story.setdefault(task["storyId"], []).append(task)
    dependencies: list[dict[str, str]] = []
    for story_tasks in by_story.values():
        implementation = [task for task in story_tasks if task["role"] in IMPLEMENTATION_ROLES]
        technical_lead = next((task for task in story_tasks if task["role"] == "technical_lead"), None)
        architect = next((task for task in story_tasks if task["role"] == "architect"), None)
        security = next((task for task in story_tasks if task["role"] == "security_engineer"), None)
        pentester = next((task for task in story_tasks if task["role"] == "pentester"), None)
        devops = next((task for task in story_tasks if task["role"] == DEVOPS_ROLE), None)
        qa = next((task for task in story_tasks if task["role"] == "qa_engineer"), None)
        if technical_lead and architect:
            dependencies.append({"taskId": architect["id"], "dependsOnTaskId": technical_lead["id"]})
        if technical_lead:
            dependencies.extend(
                {"taskId": task["id"], "dependsOnTaskId": technical_lead["id"]} for task in implementation
            )
        if architect:
            dependencies.extend(
                {"taskId": task["id"], "dependsOnTaskId": architect["id"]} for task in implementation
            )
        if security:
            dependencies.extend(
                {"taskId": security["id"], "dependsOnTaskId": task["id"]} for task in implementation
            )
        if pentester and security:
            dependencies.append({"taskId": pentester["id"], "dependsOnTaskId": security["id"]})
        if devops:
            dependencies.extend(
                {"taskId": devops["id"], "dependsOnTaskId": task["id"]} for task in implementation
            )
        if qa:
            upstream = [*implementation]
            if security:
                upstream.append(security)
            if pentester:
                upstream.append(pentester)
            if devops:
                upstream.append(devops)
            dependencies.extend({"taskId": qa["id"], "dependsOnTaskId": task["id"]} for task in upstream)
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for dependency in dependencies:
        key = (dependency["taskId"], dependency["dependsOnTaskId"])
        if key not in seen:
            seen.add(key)
            unique.append(dependency)
    return unique


def _handoff_record(
    *,
    from_role: str,
    to_role: str,
    artifact_contract: str,
    phase: str,
    from_task_id: str | None = None,
    to_task_id: str | None = None,
    review_required: bool = False,
) -> dict[str, Any]:
    source = from_task_id or from_role
    target = to_task_id or to_role
    return {
        "id": f"handoff-{_slug(source)}-to-{_slug(target)}-{_slug(phase)}",
        "fromTaskId": from_task_id,
        "toTaskId": to_task_id,
        "fromRole": from_role,
        "toRole": to_role,
        "artifactContract": artifact_contract,
        "phase": phase,
        "status": "planned",
        "reviewRequired": review_required,
    }


def _assignment_handoffs(
    tasks: list[dict[str, Any]], dependencies: list[dict[str, str]]
) -> list[dict[str, Any]]:
    by_id = {task["id"]: task for task in tasks}
    handoffs: list[dict[str, Any]] = []
    for dependency in dependencies:
        upstream = by_id[dependency["dependsOnTaskId"]]
        downstream = by_id[dependency["taskId"]]
        handoffs.append(
            _handoff_record(
                from_task_id=upstream["id"],
                to_task_id=downstream["id"],
                from_role=upstream["role"],
                to_role=downstream["role"],
                artifact_contract="agent_task_output",
                phase="task_dependency",
                review_required=downstream["role"] in REVIEW_ROLES,
            )
        )
    by_story: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        by_story.setdefault(task["storyId"], []).append(task)
    for story_tasks in by_story.values():
        technical_lead = next((task for task in story_tasks if task["role"] == "technical_lead"), None)
        implementation = [task for task in story_tasks if task["role"] in IMPLEMENTATION_ROLES]
        if technical_lead:
            handoffs.append(
                _handoff_record(
                    to_task_id=technical_lead["id"],
                    from_role="product_owner",
                    to_role="technical_lead",
                    artifact_contract="product_owner_output",
                    phase="po_to_tl",
                )
            )
            handoffs.extend(
                [
                    _handoff_record(
                        from_task_id=technical_lead["id"],
                        to_task_id=task["id"],
                        from_role="technical_lead",
                        to_role=task["role"],
                        artifact_contract="technical_plan",
                        phase="tl_to_dev",
                    )
                    for task in implementation
                ]
            )
        for task in story_tasks:
            if task["role"] not in {"qa_engineer", "security_engineer"}:
                continue
            reviewer_role = task.get("reviewerRole") or "technical_lead"
            reviewer_task = next(
                (candidate for candidate in story_tasks if candidate["role"] == reviewer_role), None
            )
            handoffs.append(
                _handoff_record(
                    from_task_id=task["id"],
                    to_task_id=reviewer_task["id"] if reviewer_task else None,
                    from_role=task["role"],
                    to_role=reviewer_role,
                    artifact_contract="review_request",
                    phase="qa_security_to_review",
                    review_required=True,
                )
            )
    seen: set[tuple[str | None, str | None, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for handoff in handoffs:
        key = (
            handoff.get("fromTaskId"),
            handoff.get("toTaskId"),
            handoff["fromRole"],
            handoff["toRole"],
            handoff["phase"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(handoff)
    return unique


def _branch_worktree_plan(
    *,
    branch_name: str,
    tasks: list[dict[str, Any]],
    available_team: list[dict[str, Any]],
) -> dict[str, Any]:
    runtime_values = {
        str(value)
        for member in available_team
        for value in (
            member.get("runtimePreference")
            or member.get("allowedProviders")
            or member.get("allowedRuntimes")
            or []
        )
    }
    worktree_capable = bool(runtime_values & WORKTREE_CAPABLE_RUNTIMES) or not runtime_values
    strategy = "worktree_per_task" if len(tasks) > 1 and worktree_capable else "single_shared_workspace"
    return {
        "branchName": branch_name,
        "baseBranch": "current",
        "strategy": strategy,
        "isolation": strategy != "single_shared_workspace",
        "worktrees": [
            {
                "taskId": task["id"],
                "storyId": task["storyId"],
                "role": task["role"],
                "branchName": f"{branch_name}-{_slug(task['role'])}-{_slug(task['storyId'])[:12]}",
            }
            for task in tasks
        ]
        if strategy == "worktree_per_task"
        else [],
    }


class TechnicalLeadPlanner:
    """Planifica tareas tecnicas por rol a partir de historias de usuario ya aprobadas."""

    def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a role-based technical plan without mutating the backlog."""
        product_brief = payload.get("productBrief") or payload.get("brief") or {}
        if not isinstance(product_brief, dict):
            raise TechnicalLeadPlannerError("TechnicalLeadPlanner requires productBrief to be an object.")
        stories = payload.get("userStories") or payload.get("stories") or []
        if not isinstance(stories, list) or not stories:
            raise TechnicalLeadPlannerError("TechnicalLeadPlanner requires at least one user story.")
        for index, story in enumerate(stories):
            if not isinstance(story, dict) or not story.get("id") or not story.get("title"):
                raise TechnicalLeadPlannerError(f"userStories[{index}] requires id and title.")
        intent = payload.get("intentClassification") or {}
        if not isinstance(intent, dict):
            intent = {}
        assessment = payload.get("projectAssessment") or {}
        if not isinstance(assessment, dict):
            assessment = {}
        risk_context = _risk_context(payload.get("risk"), intent)
        available_team = _available_team_from_payload(payload)
        team_by_role = _team_by_role(available_team)
        available_roles = set(team_by_role)
        criteria = _flat_acceptance_criteria(payload)

        tasks: list[dict[str, Any]] = []
        for story in stories:
            story_criteria = _criteria_for_story(story, criteria)
            acceptance_refs = [
                str(criterion.get("id") or f"{story['id']}-ac-{index}")
                for index, criterion in enumerate(story_criteria, start=1)
            ]
            story_scope = _story_scope(story, product_brief, assessment)
            roles = _roles_for_story(
                story=story,
                criteria=story_criteria,
                product_brief=product_brief,
                intent=intent,
                risk_context=risk_context,
                assessment=assessment,
                available_roles=available_roles,
            )
            for role in roles:
                profile = _role_profile(
                    role, team_by_role.get(role), scope=story_scope, risk_level=risk_context["level"]
                )
                task_id = f"tlp-{_slug(str(story['id']))}-{_slug(role)}"
                tasks.append(
                    {
                        "id": task_id,
                        "storyId": story["id"],
                        "role": role,
                        "title": _task_title(role, story),
                        "goal": ROLE_GOALS.get(role, f"Complete {role} work for the story."),
                        "scope": {
                            "userStory": {
                                key: value
                                for key, value in story.items()
                                if key not in {"role", "roles", "requiredRoles"}
                            },
                            "technicalScope": sorted(story_scope),
                        },
                        "filesLikely": _files_likely(role, assessment),
                        "acceptanceRefs": acceptance_refs,
                        "outputSchema": profile["outputSchema"],
                        "requiredTools": profile["requiredTools"],
                        "runtimePreference": profile["runtimePreference"],
                        "reviewerRole": profile["reviewerRole"],
                        "qualityGates": profile["qualityGates"],
                        "risk": risk_context,
                        "description": ROLE_GOALS.get(role, "Complete role-specific work for the story."),
                        "category": "verification"
                        if role in REVIEW_ROLES
                        else "coordination"
                        if role in COORDINATION_ROLES
                        else "implementation",
                        "priority": story.get("priority") or "medium",
                        "metadata": {
                            "source": TECHNICAL_LEAD_PLANNER_ID,
                            "technicalLeadTaskId": task_id,
                            "acceptanceRefs": acceptance_refs,
                            "filesLikely": _files_likely(role, assessment),
                            "outputSchema": profile["outputSchema"],
                            "requiredTools": profile["requiredTools"],
                            "runtimePreference": profile["runtimePreference"],
                            "reviewerRole": profile["reviewerRole"],
                            "qualityGates": profile["qualityGates"],
                            "risk": risk_context,
                        },
                    }
                )
        dependencies = _task_dependencies(tasks)
        branch_name = _branch_name(payload, product_brief, stories)
        return {
            "agent_tasks": tasks,
            "task_dependencies": dependencies,
            "assignment_handoffs": _assignment_handoffs(tasks, dependencies),
            "quality_gates": _quality_gates(tasks, intent),
            "branch_worktree_plan": _branch_worktree_plan(
                branch_name=branch_name,
                tasks=tasks,
                available_team=available_team,
            ),
        }

    def generate_agent_tasks(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Compatibility adapter for ProductLoopCoordinator technical_lead_runner."""
        return self.plan(payload)["agent_tasks"]
