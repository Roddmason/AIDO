"""Setting descriptor registry for the AIDO settings plane: keys, types, defaults, and validation.

Defines the ``SettingDescriptor`` dataclass, the ``REGISTRY`` of wired settings (autonomy,
sandbox, budget, runtime/worker toggles, research/internet policy, security posture, and
project goal/loop/quality defaults), and ``validate_value`` / ``descriptor_for`` helpers that
enforce type constraints at trust boundaries before any persistence occurs. Enum members are
grounded in runtime concepts: ``security.shell.profile`` mirrors the policy-engine permission
profiles, ``project.loop.teamMode``/``project.loop.risk`` mirror the team-scheduler MODES/RISKS,
and ``research.internetPolicy`` mirrors the conservative source-policy allowlist posture.

``project.loop.teamMode`` (economy/balanced/critical/maximum) and ``project.routing.forceLocal``
are the operator's standing cost decision: ``ThreadCoordinator`` stamps both onto every queued
Product Loop run, so a change here governs the next run rather than only describing it.

@author Rodrigo Mason
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingDescriptor:
    """Immutable descriptor for a single platform setting key."""

    key: str
    section: str
    project_section: str | None
    type: str
    default: Any
    enum: tuple[str, ...] | None = None
    label_key: str = ""
    minimum: float | None = None
    maximum: float | None = None


REGISTRY: list[SettingDescriptor] = [
    SettingDescriptor(
        key="autonomy.level",
        section="autonomy",
        project_section="security",
        type="enum",
        default="guided",
        enum=("guided", "recommended", "autonomous"),
        label_key="app.settings.autonomy.level",
    ),
    SettingDescriptor(
        key="security.sandboxProfileId",
        section="security",
        project_section="security",
        type="string",
        default="",
        label_key="app.settings.security.sandboxProfile",
    ),
    SettingDescriptor(
        key="budget.maxCostUsd",
        section="costs",
        project_section="budget",
        type="number",
        default=0,
        label_key="app.settings.budget.maxCostUsd",
    ),
    SettingDescriptor(
        key="agents.responseStyle",
        section="costs",
        project_section="budget",
        type="enum",
        default="compact",
        enum=("compact", "normal"),
        label_key="app.settings.agents.responseStyle",
    ),
    SettingDescriptor(
        key="runtime.cli.enabled",
        section="runtime",
        project_section=None,
        type="boolean",
        default=True,
        label_key="app.settings.runtime.cli.enabled",
    ),
    SettingDescriptor(
        key="runtime.remote.enabled",
        section="runtime",
        project_section=None,
        type="boolean",
        default=True,
        label_key="app.settings.runtime.remote.enabled",
    ),
    SettingDescriptor(
        key="runtime.ollama.enabled",
        section="runtime",
        project_section=None,
        type="boolean",
        default=True,
        label_key="app.settings.runtime.ollama.enabled",
    ),
    SettingDescriptor(
        key="runtime.nvidia.enabled",
        section="runtime",
        project_section=None,
        type="boolean",
        default=True,
        label_key="app.settings.runtime.nvidia.enabled",
    ),
    SettingDescriptor(
        key="project.runtime.cli.enabled",
        section="runtime",
        project_section="runtime",
        type="boolean",
        default=True,
        label_key="app.settings.project.runtime.cli.enabled",
    ),
    SettingDescriptor(
        key="project.runtime.remote.enabled",
        section="runtime",
        project_section="runtime",
        type="boolean",
        default=True,
        label_key="app.settings.project.runtime.remote.enabled",
    ),
    SettingDescriptor(
        key="project.runtime.allowedProviders",
        section="runtime",
        project_section="runtime",
        type="string_list",
        default=[],
        label_key="app.settings.project.runtime.allowedProviders",
    ),
    SettingDescriptor(
        key="project.runtime.defaultMode",
        section="runtime",
        project_section="runtime",
        type="enum",
        default="hybrid",
        enum=("api", "cli", "ollama", "hybrid", "manual"),
        label_key="app.settings.project.runtime.defaultMode",
    ),
    SettingDescriptor(
        key="project.routing.forceLocal",
        section="runtime",
        project_section="runtime",
        type="boolean",
        default=False,
        label_key="app.settings.project.routing.forceLocal",
    ),
    SettingDescriptor(
        key="worker.autostart",
        section="worker",
        project_section=None,
        type="boolean",
        default=False,
        label_key="app.settings.worker.autostart",
    ),
    SettingDescriptor(
        key="worker.pollIntervalSeconds",
        section="worker",
        project_section=None,
        type="number",
        default=5,
        label_key="app.settings.worker.pollIntervalSeconds",
        minimum=0.25,
        maximum=3600,
    ),
    SettingDescriptor(
        key="worker.maxConcurrentJobs",
        section="worker",
        project_section=None,
        type="number",
        default=1,
        label_key="app.settings.worker.maxConcurrentJobs",
        minimum=1,
        maximum=1,
    ),
    SettingDescriptor(
        key="resources.profile",
        section="resources",
        project_section=None,
        type="enum",
        default="auto",
        enum=("auto", "interactive_unreal", "development", "idle_validation"),
        label_key="app.settings.resources.profile",
    ),
    SettingDescriptor(
        key="resources.maxHeavyWorkloads",
        section="resources",
        project_section=None,
        type="number",
        default=1,
        label_key="app.settings.resources.maxHeavyWorkloads",
        minimum=1,
        maximum=1,
    ),
    SettingDescriptor(
        key="resources.maxLightWorkloads",
        section="resources",
        project_section=None,
        type="number",
        default=2,
        label_key="app.settings.resources.maxLightWorkloads",
        minimum=1,
        maximum=2,
    ),
    SettingDescriptor(
        key="resources.minFreeMemoryGiB",
        section="resources",
        project_section=None,
        type="number",
        default=16,
        label_key="app.settings.resources.minFreeMemoryGiB",
        minimum=1,
        maximum=1024,
    ),
    SettingDescriptor(
        key="resources.hardFreeMemoryGiB",
        section="resources",
        project_section=None,
        type="number",
        default=8,
        label_key="app.settings.resources.hardFreeMemoryGiB",
        minimum=1,
        maximum=1024,
    ),
    SettingDescriptor(
        key="resources.minFreeDiskGiB",
        section="resources",
        project_section=None,
        type="number",
        default=50,
        label_key="app.settings.resources.minFreeDiskGiB",
        minimum=1,
        maximum=10_240,
    ),
    SettingDescriptor(
        key="resources.minFreeDiskPercent",
        section="resources",
        project_section=None,
        type="number",
        default=5,
        label_key="app.settings.resources.minFreeDiskPercent",
        minimum=0,
        maximum=90,
    ),
    SettingDescriptor(
        key="resources.maxCpuPercent",
        section="resources",
        project_section=None,
        type="number",
        default=65,
        label_key="app.settings.resources.maxCpuPercent",
        minimum=1,
        maximum=95,
    ),
    SettingDescriptor(
        key="resources.unrealReserveMemoryGiB",
        section="resources",
        project_section=None,
        type="number",
        default=20,
        label_key="app.settings.resources.unrealReserveMemoryGiB",
        minimum=0,
        maximum=1024,
    ),
    SettingDescriptor(
        key="resources.blockLocalGpuWhenUnreal",
        section="resources",
        project_section=None,
        type="boolean",
        default=True,
        label_key="app.settings.resources.blockLocalGpuWhenUnreal",
    ),
    SettingDescriptor(
        key="resources.sampleIntervalSeconds",
        section="resources",
        project_section=None,
        type="number",
        default=2,
        label_key="app.settings.resources.sampleIntervalSeconds",
        minimum=0.25,
        maximum=60,
    ),
    SettingDescriptor(
        key="research.internetPolicy",
        section="research",
        project_section="internet",
        type="enum",
        default="official_allowlist",
        enum=("blocked", "official_allowlist", "policy_gated"),
        label_key="app.settings.research.internetPolicy",
    ),
    SettingDescriptor(
        key="research.preferOfficialDocs",
        section="research",
        project_section="internet",
        type="boolean",
        default=True,
        label_key="app.settings.research.preferOfficialDocs",
    ),
    SettingDescriptor(
        key="research.maxSources",
        section="research",
        project_section=None,
        type="number",
        default=5,
        label_key="app.settings.research.maxSources",
    ),
    SettingDescriptor(
        key="research.trustedDomains",
        section="research",
        project_section="internet",
        type="string_list",
        default=[],
        label_key="app.settings.research.trustedDomains",
    ),
    SettingDescriptor(
        key="research.webSearch.provider",
        section="research",
        project_section="internet",
        type="enum",
        default="searxng",
        enum=("searxng", "duckduckgo"),
        label_key="app.settings.research.webSearchProvider",
    ),
    SettingDescriptor(
        key="research.webSearch.baseUrl",
        section="research",
        project_section="internet",
        type="string",
        default="http://127.0.0.1:8888",
        label_key="app.settings.research.webSearchBaseUrl",
    ),
    SettingDescriptor(
        key="security.gitleaks.enforced",
        section="security",
        project_section="security",
        type="boolean",
        default=True,
        label_key="app.settings.security.gitleaksEnforced",
    ),
    SettingDescriptor(
        key="security.shell.profile",
        section="security",
        project_section="security",
        type="enum",
        default="dev_safe",
        enum=("plan", "dev_safe", "qa", "release"),
        label_key="app.settings.security.shellProfile",
    ),
    SettingDescriptor(
        key="security.pentest.beforeRelease",
        section="pentest",
        project_section="security",
        type="boolean",
        default=False,
        label_key="app.settings.security.pentestBeforeRelease",
    ),
    SettingDescriptor(
        key="project.goal.statement",
        section="goal",
        project_section="goal",
        type="string",
        default="",
        label_key="app.settings.goal.statement",
    ),
    SettingDescriptor(
        key="project.loop.teamMode",
        section="goal",
        project_section="goal",
        type="enum",
        default="balanced",
        enum=("economy", "balanced", "critical", "maximum"),
        label_key="app.settings.goal.teamMode",
    ),
    SettingDescriptor(
        key="project.loop.risk",
        section="goal",
        project_section="goal",
        type="enum",
        default="medium",
        enum=("low", "medium", "high", "critical"),
        label_key="app.settings.goal.risk",
    ),
    SettingDescriptor(
        key="project.quality.gateCommands",
        section="quality",
        project_section="quality",
        type="string_list",
        default=[],
        label_key="app.settings.quality.gateCommands",
    ),
    SettingDescriptor(
        key="project.runtime.containerized",
        section="quality",
        project_section="quality",
        type="boolean",
        default=False,
        label_key="app.settings.quality.containerizedRuntime",
    ),
    SettingDescriptor(
        key="project.quality.devopsChecksEnabled",
        section="quality",
        project_section="quality",
        type="boolean",
        default=False,
        label_key="app.settings.quality.devopsChecksEnabled",
    ),
    SettingDescriptor(
        key="project.git.baseBranch",
        section="git",
        project_section="git",
        type="string",
        default="dev",
        label_key="app.settings.git.baseBranch",
    ),
    SettingDescriptor(
        key="project.git.integrationMode",
        section="git",
        project_section="git",
        type="enum",
        default="manual_pr",
        enum=("direct_push", "auto_pr", "manual_pr"),
        label_key="app.settings.git.integrationMode",
    ),
    SettingDescriptor(
        key="project.git.remoteName",
        section="git",
        project_section="git",
        type="string",
        default="origin",
        label_key="app.settings.git.remoteName",
    ),
    SettingDescriptor(
        key="project.git.autoDeleteBranch",
        section="git",
        project_section="git",
        type="boolean",
        default=True,
        label_key="app.settings.git.autoDeleteBranch",
    ),
    SettingDescriptor(
        key="project.git.requireCiGreen",
        section="git",
        project_section="git",
        type="boolean",
        default=False,
        label_key="app.settings.git.requireCiGreen",
    ),
    SettingDescriptor(
        key="project.git.workBranchPrefix",
        section="git",
        project_section="git",
        type="string",
        default="codex",
        label_key="app.settings.git.workBranchPrefix",
    ),
]

REGISTRY.extend(
    SettingDescriptor(
        key=f"decision_engine.{key}",
        section="runtime",
        project_section="runtime",
        type=kind,
        default=default,
        enum=choices,
        minimum=minimum,
        maximum=maximum,
        label_key=f"app.settings.decisionEngine.{key}",
    )
    for key, kind, default, choices, minimum, maximum in (
        ("enabled", "boolean", True, None, None, None),
        ("provider", "enum", "jev", ("jev", "deterministic"), None, None),
        ("mode", "enum", "shadow", ("disabled", "shadow", "runtime_selection"), None, None),
        ("shadow.enabled", "boolean", True, None, None, None),
        ("jev.enabled", "boolean", True, None, None, None),
        ("endpoint", "string", "https://api.typesafe.ai/v1/systemone", None, None, None),
        ("api_key_reference", "string", "env:TYPESAFE_API_KEY", None, None, None),
        ("model", "string", "jev-1.13.0", None, None, None),
        ("version", "string", "1.13.0", None, None, None),
        ("timeout_seconds", "number", 5.0, None, 0.01, 5),
        ("confidence_threshold", "number", 0.85, None, 0, 1),
        ("margin_threshold", "number", 0.20, None, 0, 1),
        ("max_risk", "enum", "high", ("low", "medium", "high", "critical"), None, None),
        ("probability_tolerance", "number", 0.000001, None, 0, 0.001),
        ("circuit_failure_threshold", "number", 3, None, 1, 20),
        ("circuit_cooldown_seconds", "number", 30, None, 1, 300),
    )
)

_REGISTRY_BY_KEY: dict[str, SettingDescriptor] = {d.key: d for d in REGISTRY}


def descriptor_for(key: str) -> SettingDescriptor | None:
    """Return the descriptor for ``key``, or ``None`` if the key is not registered."""
    return _REGISTRY_BY_KEY.get(key)


def validate_value(descriptor: SettingDescriptor, value: Any) -> Any:
    """Validate and coerce ``value`` against ``descriptor``; raise ``ValueError`` on failure.

    - enum: value must be one of the declared members (string match).
    - number: value is coerced via ``float()``; raises ``ValueError`` if not numeric.
    - string: returned as-is (any string is valid).
    """
    if descriptor.key.startswith("decision_engine."):
        from local_control_center.decision_engine.config import SETTING_NAMES, DecisionConfig

        field = next(
            name for name, key in SETTING_NAMES.items() if descriptor.key == f"decision_engine.{key}"
        )
        if field in {"model", "version"}:
            import re

            pattern = r"jev-\d+\.\d+\.\d+" if field == "model" else r"\d+\.\d+\.\d+"
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError("invalid_model_version")
        else:
            try:
                DecisionConfig(**{field: value})
            except ValueError:
                raise ValueError("invalid_decision_engine_setting") from None
    if descriptor.key == "research.webSearch.baseUrl":
        from local_control_center.research.web_search import validate_search_base_url

        return validate_search_base_url(value if isinstance(value, str) else "")
    if descriptor.type == "enum":
        if value not in (descriptor.enum or ()):
            raise ValueError(
                f"Invalid value {value!r} for {descriptor.key!r}; must be one of {descriptor.enum!r}."
            )
        return value
    if descriptor.type == "number":
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value {value!r} for {descriptor.key!r}.") from exc
        if descriptor.minimum is not None and parsed < descriptor.minimum:
            raise ValueError(f"Value for {descriptor.key!r} must be at least {descriptor.minimum}.")
        if descriptor.maximum is not None and parsed > descriptor.maximum:
            raise ValueError(f"Value for {descriptor.key!r} must be at most {descriptor.maximum}.")
        return parsed
    if descriptor.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(f"Invalid boolean value {value!r} for {descriptor.key!r}.")
    if descriptor.type == "string_list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Invalid value {value!r} for {descriptor.key!r}; expected a string list.")
        return list(value)
    if not isinstance(value, str):
        raise ValueError(f"Invalid value {value!r} for {descriptor.key!r}; expected a string.")
    return value
