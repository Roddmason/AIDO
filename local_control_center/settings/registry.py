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
        default=True,
        label_key="app.settings.worker.autostart",
    ),
    SettingDescriptor(
        key="worker.pollIntervalSeconds",
        section="worker",
        project_section=None,
        type="number",
        default=5,
        label_key="app.settings.worker.pollIntervalSeconds",
    ),
    SettingDescriptor(
        key="worker.maxConcurrentJobs",
        section="worker",
        project_section=None,
        type="number",
        default=2,
        label_key="app.settings.worker.maxConcurrentJobs",
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
]

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
    if descriptor.type == "enum":
        if value not in (descriptor.enum or ()):
            raise ValueError(
                f"Invalid value {value!r} for {descriptor.key!r}; must be one of {descriptor.enum!r}."
            )
        return value
    if descriptor.type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value {value!r} for {descriptor.key!r}.") from exc
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
