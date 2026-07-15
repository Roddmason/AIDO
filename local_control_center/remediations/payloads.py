"""Blocker-type Strategy registry for remediation action-spec payload builders.

Each blocker type resolves to one builder in ``PAYLOAD_BUILDERS``; a builder receives an
immutable ``BlockerPayloadContext`` (blocker reason, redacted details, and the shared payload
fragments derived from them) and returns the ordered action specs that
``BlockerRemediationService`` persists as user-executable remediation actions. Builders are
pure: they never touch the database — DB-backed inputs such as the persisted Git remote
re-add specs are injected by the service through the context factory
``build_blocker_payload_context``.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from local_control_center.agents.runtime_provider_config import (
    DEFAULT_OLLAMA_BASE_URL,
    RUNTIME_PROVIDER_CONFIG_SPECS,
    known_provider_default_base_url,
)
from local_control_center.runtime_integrations.repository import is_ollama_runtime_id
from local_control_center.shared.redaction import redact_secrets

OLLAMA_REMOTE_PROVIDER_IDS = frozenset({"ollama_remote"})


@dataclass(frozen=True)
class BlockerPayloadContext:
    """Read-only inputs shared by every blocker-type action-spec builder."""

    reason: str
    details: dict[str, Any]
    runtime_payload: dict[str, Any]
    provider_settings_payload: dict[str, Any]
    resource_manager_settings_payload: dict[str, Any]
    team_scheduler_settings_payload: dict[str, Any]
    product_owner_settings_payload: dict[str, Any]
    resource_learning_settings_payload: dict[str, Any]
    workspace_recovery_payload: dict[str, Any]
    research_recovery_payload: dict[str, Any]
    review_diff_payload: dict[str, Any]
    gitleaks_recovery_payload: dict[str, Any]
    qa_recovery_payload: dict[str, Any]
    runtime_recovery_payload: dict[str, Any]
    runtime_settings_payload: dict[str, Any]
    git_dirty_tree_payload: dict[str, Any]
    git_init_payload: dict[str, Any]
    delivery_approval_payload: dict[str, Any]
    git_remote_add_specs: list[dict[str, Any]]


def build_blocker_payload_context(
    *,
    reason: str,
    details: dict[str, Any],
    git_remote_add_specs: list[dict[str, Any]],
) -> BlockerPayloadContext:
    """Derive every shared payload fragment from the blocker evidence once per dispatch."""
    runtime_id = (
        details.get("selectedRuntimeId")
        or details.get("runtimeId")
        or details.get("providerId")
        or _provider_id_from_resource_blockers(details)
    )
    runtime_id_text = str(runtime_id or "").strip()
    runtime_payload = {"runtimeId": runtime_id_text} if runtime_id_text else {}
    provider_setup_payload = _provider_credentials_setup_payload(runtime_id_text) if runtime_id_text else {}
    provider_settings_payload = {"section": "providers-cli"}
    if provider_setup_payload:
        provider_settings_payload["providerId"] = provider_setup_payload["providerId"]
        provider_settings_payload["providerSetup"] = provider_setup_payload
    runtime_recovery_payload = _runtime_recovery_payload(details)
    runtime_settings_payload = {**provider_settings_payload, **runtime_recovery_payload}
    if runtime_id_text:
        runtime_settings_payload["runtimeId"] = runtime_id_text
    git_dirty_tree_payload = _git_dirty_tree_payload(details)
    return BlockerPayloadContext(
        reason=reason,
        details=details,
        runtime_payload=runtime_payload,
        provider_settings_payload=provider_settings_payload,
        resource_manager_settings_payload=_resource_manager_settings_payload(details),
        team_scheduler_settings_payload=_team_scheduler_settings_payload(details),
        product_owner_settings_payload=_product_owner_settings_payload(details),
        resource_learning_settings_payload=_resource_learning_settings_payload(details),
        workspace_recovery_payload=_workspace_recovery_payload(details, reason=reason),
        research_recovery_payload=_research_recovery_payload(details),
        review_diff_payload=_review_diff_payload(details),
        gitleaks_recovery_payload=_gitleaks_recovery_payload(details),
        qa_recovery_payload=_qa_recovery_payload(details),
        runtime_recovery_payload=runtime_recovery_payload,
        runtime_settings_payload=runtime_settings_payload,
        git_dirty_tree_payload=git_dirty_tree_payload,
        git_init_payload={**git_dirty_tree_payload, "defaultBranch": "dev"},
        delivery_approval_payload=_delivery_approval_payload(details),
        git_remote_add_specs=git_remote_add_specs,
    )


def _provider_id_from_resource_blockers(details: dict[str, Any]) -> str:
    resource_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
    if not isinstance(resource_blockers, list):
        return ""
    for blocker in resource_blockers:
        if not isinstance(blocker, dict):
            continue
        for source in (blocker, blocker.get("decision")):
            if not isinstance(source, dict):
                continue
            provider_id = str(source.get("providerId") or source.get("provider_id") or "").strip()
            if provider_id:
                return provider_id
            selected = source.get("selected")
            if isinstance(selected, dict):
                provider_id = str(selected.get("providerId") or selected.get("provider_id") or "").strip()
                if provider_id:
                    return provider_id
            for key in ("rejected", "candidates"):
                values = source.get(key)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if not isinstance(value, dict):
                        continue
                    provider_id = str(value.get("providerId") or value.get("provider_id") or "").strip()
                    if provider_id:
                        return provider_id
    return ""


def _provider_credentials_setup_payload(provider_id: str) -> dict[str, Any]:
    normalized_provider_id = str(provider_id or "").strip()
    if not normalized_provider_id:
        return {}
    if is_ollama_runtime_id(normalized_provider_id) or normalized_provider_id in OLLAMA_REMOTE_PROVIDER_IDS:
        remote = normalized_provider_id in OLLAMA_REMOTE_PROVIDER_IDS or normalized_provider_id.startswith(
            "ollama-remote"
        )
        payload = {
            "providerId": normalized_provider_id,
            "displayName": "Ollama remote" if remote else "Ollama Local/Remote",
            "kind": "local",
            "knownProvider": True,
            "requiresManualBaseUrl": remote,
            "authFields": ["apiKey"] if remote else [],
            "requiredFields": ["baseUrl"],
        }
        if not remote:
            payload["baseUrl"] = DEFAULT_OLLAMA_BASE_URL
            payload["baseUrlSource"] = "known_provider_default"
        return payload
    spec = next(
        (item for item in RUNTIME_PROVIDER_CONFIG_SPECS if item.provider_id == normalized_provider_id),
        None,
    )
    default_base_url = known_provider_default_base_url(normalized_provider_id)
    variables = spec.variables if spec is not None else ()
    auth_fields = [variable.key for variable in variables if variable.secret]
    required_fields = [variable.key for variable in variables if variable.required]
    if spec is None:
        auth_fields = ["apiKey"]
        required_fields = ["apiKey"]
    payload = {
        "providerId": normalized_provider_id,
        "displayName": spec.display_name if spec is not None else normalized_provider_id,
        "kind": spec.kind if spec is not None else "api",
        "knownProvider": bool(spec is not None or default_base_url),
        "requiresManualBaseUrl": "baseUrl" in required_fields and not bool(default_base_url),
        "authFields": auth_fields,
        "requiredFields": required_fields,
    }
    if default_base_url:
        payload["baseUrl"] = default_base_url
        payload["baseUrlSource"] = "known_provider_default"
    return payload


def _resource_manager_settings_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"section": "routing"}
    resource_blockers = _resource_blocker_summaries(details)
    if resource_blockers:
        payload["resourceBlockers"] = resource_blockers
        blocked_roles: list[str] = []
        for blocker in resource_blockers:
            role = str(blocker.get("role") or "").strip()
            if role and role not in blocked_roles:
                blocked_roles.append(role)
        if blocked_roles:
            payload["blockedRoles"] = blocked_roles

    agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
    if isinstance(agent_task_ids, list):
        payload["agentTaskIds"] = [str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()]

    team_schedule_summary = _team_schedule_repair_summary(details)
    if team_schedule_summary:
        payload["teamScheduleSummary"] = team_schedule_summary
    return redact_secrets(payload)


def _resource_blocker_summaries(details: dict[str, Any]) -> list[dict[str, Any]]:
    raw_blockers = details.get("resourceBlockers") if isinstance(details, dict) else None
    if not isinstance(raw_blockers, list):
        return []
    summaries: list[dict[str, Any]] = []
    for blocker in raw_blockers[:8]:
        if not isinstance(blocker, dict):
            continue
        decision = blocker.get("decision") if isinstance(blocker.get("decision"), dict) else {}
        summary: dict[str, Any] = {
            "role": str(blocker.get("role") or "").strip(),
            "taskId": str(blocker.get("taskId") or "").strip(),
            "reason": str(
                blocker.get("reason") or decision.get("decisionReason") or "AI resource selection blocked."
            ).strip(),
            "selected": _resource_candidate_summary(decision.get("selected")),
        }
        decision_reason = str(decision.get("decisionReason") or "").strip()
        if decision_reason:
            summary["decisionReason"] = decision_reason
        rejected = _resource_candidate_summaries(decision.get("rejected"))
        if rejected:
            summary["rejected"] = rejected
        policy_result = resource_policy_summary(decision.get("policyResult"))
        if policy_result:
            summary["policyResult"] = policy_result
        summaries.append(summary)
    return summaries


def _resource_candidate_summaries(candidates: Any) -> list[dict[str, Any]]:
    if not isinstance(candidates, list):
        return []
    return [
        summary
        for candidate in candidates[:8]
        for summary in [_resource_candidate_summary(candidate)]
        if summary is not None
    ]


def _resource_candidate_summary(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    summary = {
        key: candidate.get(key)
        for key in ("providerId", "model", "runtime", "reason")
        if candidate.get(key) is not None
    }
    return summary or None


def resource_policy_summary(policy_result: Any) -> dict[str, Any]:
    """Keep only the policy-result fields that remediation payloads and approvals expose."""
    if not isinstance(policy_result, dict):
        return {}
    return {
        key: policy_result[key]
        for key in ("scoring", "opaqueMlUsed", "unknownCostPolicy")
        if key in policy_result
    }


def _team_schedule_repair_summary(details: dict[str, Any]) -> dict[str, Any]:
    team_schedule = details.get("teamSchedule") if isinstance(details, dict) else None
    if not isinstance(team_schedule, dict):
        return {}
    roles = team_schedule.get("roles") if isinstance(team_schedule.get("roles"), list) else []
    summary = team_schedule.get("summary") if isinstance(team_schedule.get("summary"), dict) else {}
    repair_summary: dict[str, Any] = {}
    if team_schedule.get("schedulerVersion") is not None:
        repair_summary["schedulerVersion"] = team_schedule.get("schedulerVersion")
    repair_summary["roleCount"] = len(roles)
    if summary.get("resourceDecisionBlockedCount") is not None:
        repair_summary["resourceDecisionBlockedCount"] = summary.get("resourceDecisionBlockedCount")
    for key in ("phase", "mode", "risk", "status"):
        value = str(team_schedule.get(key) or "").strip()
        if value:
            repair_summary[key] = value
    return repair_summary


def _team_scheduler_settings_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"section": "team"}
    for key in ("status", "productOwnerOutputId", "backlogArtifactId"):
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
    if isinstance(agent_task_ids, list):
        payload["agentTaskIds"] = [str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()]

    scheduled_roles = _team_schedule_role_names(details)
    if scheduled_roles:
        payload["scheduledRoles"] = scheduled_roles

    for key in ("unscheduledRoles", "scheduledRoles"):
        values = details.get(key) if isinstance(details, dict) else None
        if isinstance(values, list):
            payload[key] = [str(value).strip() for value in values if str(value).strip()]

    team_schedule_summary = _team_schedule_repair_summary(details)
    if team_schedule_summary:
        payload["teamScheduleSummary"] = team_schedule_summary
    return redact_secrets(payload)


def _team_schedule_role_names(details: dict[str, Any]) -> list[str]:
    team_schedule = details.get("teamSchedule") if isinstance(details, dict) else None
    if not isinstance(team_schedule, dict):
        return []
    roles = team_schedule.get("roles") if isinstance(team_schedule.get("roles"), list) else []
    names: list[str] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        role_name = str(role.get("role") or "").strip()
        if role_name and role_name not in names:
            names.append(role_name)
    return names


def _product_owner_settings_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"section": "team"}
    scalar_fields = (
        "status",
        "outputStatus",
        "workspaceId",
        "productOwnerOutputId",
        "briefId",
        "reason",
    )
    for key in scalar_fields:
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    list_fields = (
        "artifactIds",
        "clarificationQuestionIds",
        "productDecisionIds",
    )
    for key in list_fields:
        values = details.get(key) if isinstance(details, dict) else None
        if isinstance(values, list):
            payload[key] = [str(value).strip() for value in values if str(value).strip()]

    pending_decisions = details.get("pendingThreadDecisions") if isinstance(details, dict) else None
    if not isinstance(pending_decisions, list) and isinstance(details, dict):
        pending_decisions = details.get("pendingDecisions")
    if isinstance(pending_decisions, list):
        payload["pendingThreadDecisionCount"] = len(pending_decisions)
    return redact_secrets(payload)


def _workspace_recovery_payload(details: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"section": "workspaces"}
    scalar_fields = (
        "status",
        "taskId",
        "workspaceId",
        "workspacePath",
        "projectPath",
        "workspaceRoot",
        "branchName",
    )
    for key in scalar_fields:
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    reason_text = str(reason or "").strip()
    if isinstance(details, dict) and str(details.get("reason") or "").strip():
        reason_text = str(details.get("reason") or "").strip()
    if reason_text:
        payload["reason"] = reason_text
    return redact_secrets(payload)


def _resource_learning_settings_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"section": "routing"}
    for key in ("status", "reason", "workspaceId", "evidenceRef"):
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    agent_task_ids = details.get("agentTaskIds") if isinstance(details, dict) else None
    if isinstance(agent_task_ids, list):
        payload["agentTaskIds"] = [str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()]

    review = details.get("review") if isinstance(details, dict) else None
    if isinstance(review, dict):
        changed_files = [str(path).strip() for path in review.get("changedFiles") or [] if str(path).strip()]
        if changed_files:
            payload["changedFiles"] = changed_files

    gitleaks = details.get("gitleaks") if isinstance(details, dict) else None
    if isinstance(gitleaks, dict):
        gitleaks_status = str(gitleaks.get("status") or "").strip()
        if gitleaks_status:
            payload["gitleaksStatus"] = gitleaks_status

    scheduled_roles = _team_schedule_role_names(details)
    if scheduled_roles:
        payload["scheduledRoles"] = scheduled_roles

    team_schedule_summary = _team_schedule_repair_summary(details)
    if team_schedule_summary:
        payload["teamScheduleSummary"] = team_schedule_summary
    return redact_secrets(payload)


def _review_diff_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if not isinstance(details, dict):
        return payload

    for key in ("status", "reason", "workspaceId", "workspacePath", "runtimeStatus"):
        value = str(details.get(key) or "").strip()
        if value:
            payload[key] = value

    agent_task_ids = details.get("agentTaskIds")
    if isinstance(agent_task_ids, list):
        payload["agentTaskIds"] = [str(task_id).strip() for task_id in agent_task_ids if str(task_id).strip()]

    runtime_result = details.get("runtimeResult") if isinstance(details.get("runtimeResult"), dict) else {}
    if not payload.get("runtimeStatus"):
        runtime_status = str(runtime_result.get("status") or "").strip()
        if runtime_status:
            payload["runtimeStatus"] = runtime_status

    review = details.get("review") if isinstance(details.get("review"), dict) else None
    if review is None and isinstance(runtime_result.get("review"), dict):
        review = runtime_result["review"]
    if isinstance(review, dict):
        changed_files = review.get("changedFiles")
        if isinstance(changed_files, list):
            payload["changedFiles"] = [str(path).strip() for path in changed_files if str(path).strip()]

        diff_refs = review.get("diffRefs")
        if isinstance(diff_refs, list):
            payload["diffRefs"] = [item for item in diff_refs if item]

    qa_results = details.get("qaResults")
    if not isinstance(qa_results, list):
        qa_results = runtime_result.get("qaResults")
    if isinstance(qa_results, list):
        payload["qaResultCount"] = len(qa_results)

    scheduled_roles = _team_schedule_role_names(details)
    if scheduled_roles:
        payload["scheduledRoles"] = scheduled_roles

    team_schedule_summary = _team_schedule_repair_summary(details)
    if team_schedule_summary:
        payload["teamScheduleSummary"] = team_schedule_summary
    return redact_secrets(payload)


def _gitleaks_recovery_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload = _review_diff_payload(details)
    if not isinstance(details, dict):
        return payload

    gitleaks = details.get("gitleaks") if isinstance(details.get("gitleaks"), dict) else details
    gitleaks_status = str(gitleaks.get("status") or "").strip()
    if gitleaks_status:
        payload["gitleaksStatus"] = gitleaks_status

    finding_count = gitleaks.get("findingCount")
    if finding_count is not None:
        payload["gitleaksFindingCount"] = finding_count

    if gitleaks.get("deliveryBlocked") is not None:
        payload["deliveryBlocked"] = bool(gitleaks.get("deliveryBlocked"))
    return redact_secrets(payload)


def _qa_recovery_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload = _review_diff_payload(details)
    if not isinstance(details, dict):
        return payload

    qa_verdict = str(details.get("qaVerdict") or "").strip()
    if not qa_verdict:
        runtime_result = (
            details.get("runtimeResult") if isinstance(details.get("runtimeResult"), dict) else {}
        )
        evidence_package = (
            runtime_result.get("evidencePackage")
            if isinstance(runtime_result.get("evidencePackage"), dict)
            else {}
        )
        qa_verdict = str(evidence_package.get("qaVerdict") or "").strip()
    if qa_verdict:
        payload["qaVerdict"] = qa_verdict

    qa_results = details.get("qaResults") if isinstance(details.get("qaResults"), list) else []
    non_passing = (
        details.get("nonPassingQaResults")
        if isinstance(details.get("nonPassingQaResults"), list)
        else [
            result
            for result in qa_results
            if not isinstance(result, dict) or str(result.get("status") or "").strip().lower() != "passed"
        ]
    )
    payload["nonPassingQaResultCount"] = len(non_passing)
    summaries = _qa_result_summaries(non_passing)
    if summaries:
        payload["nonPassingQaResults"] = summaries
    return redact_secrets(payload)


def _qa_result_summaries(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    summaries: list[dict[str, Any]] = []
    for result in results[:8]:
        if not isinstance(result, dict):
            summaries.append({"status": "invalid"})
            continue
        summary = {
            key: result[key]
            for key in ("command", "status", "reason", "artifactId")
            if result.get(key) is not None
        }
        if summary:
            summaries.append(summary)
    return summaries


def _runtime_recovery_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload = _review_diff_payload(details)
    if not isinstance(details, dict):
        return payload

    for key in ("selectedRuntimeId", "providerId", "model"):
        value = str(details.get(key) or "").strip()
        if value:
            payload[key] = value
    if details.get("executable") is not None:
        payload["executable"] = bool(details.get("executable"))

    runtime = details.get("runtime") if isinstance(details.get("runtime"), dict) else {}
    runtime_id = str(
        details.get("runtimeId") or runtime.get("id") or details.get("selectedRuntimeId") or ""
    ).strip()
    if runtime_id:
        payload["runtimeId"] = runtime_id
    return redact_secrets(payload)


def _git_dirty_tree_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if not isinstance(details, dict):
        return payload

    for key in ("status", "reason", "projectId", "workspaceId", "projectPath", "branch", "currentBranch"):
        value = str(details.get(key) or "").strip()
        if value:
            payload[key] = value
    if details.get("dirty") is not None:
        payload["dirty"] = bool(details.get("dirty"))
    if details.get("remoteMissing") is not None:
        payload["remoteMissing"] = bool(details.get("remoteMissing"))
    configured_remotes = details.get("configuredRemotes")
    if isinstance(configured_remotes, int):
        payload["configuredRemotes"] = configured_remotes

    dirty_file_count = 0
    for key in ("changedFiles", "stagedFiles", "untrackedFiles"):
        values = details.get(key)
        if isinstance(values, list):
            clean_values = [str(value).strip() for value in values if str(value).strip()]
            payload[key] = clean_values
            dirty_file_count += len(clean_values)
    payload["dirtyFileCount"] = dirty_file_count

    remotes = details.get("remotes")
    if isinstance(remotes, list):
        remote_names = [
            str(remote.get("name") or "").strip()
            for remote in remotes
            if isinstance(remote, dict) and str(remote.get("name") or "").strip()
        ]
        if remote_names:
            payload["remoteNames"] = remote_names
    return redact_secrets(payload)


def _delivery_approval_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("status", "reason", "workspaceId"):
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    for key in ("evidenceRefs", "changedFiles"):
        values = details.get(key) if isinstance(details, dict) else None
        if isinstance(values, list):
            payload[key] = [str(value).strip() for value in values if str(value).strip()]

    diff_refs = details.get("diffRefs") if isinstance(details, dict) else None
    if isinstance(diff_refs, list):
        payload["diffRefs"] = [item for item in diff_refs if item]

    gitleaks = details.get("gitleaks") if isinstance(details, dict) else None
    if isinstance(gitleaks, dict):
        gitleaks_status = str(gitleaks.get("status") or "").strip()
        if gitleaks_status:
            payload["gitleaksStatus"] = gitleaks_status

    resource_learning = details.get("resourceLearning") if isinstance(details, dict) else None
    if isinstance(resource_learning, dict):
        payload["resourceLearning"] = {
            key: resource_learning[key]
            for key in ("status", "evidenceRef", "observationCount")
            if key in resource_learning
        }
    return redact_secrets(payload)


def _research_recovery_payload(details: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("status", "jobId", "researchStatus"):
        value = str(details.get(key) or "").strip() if isinstance(details, dict) else ""
        if value:
            payload[key] = value

    decisions = details.get("decisions") if isinstance(details, dict) else None
    if isinstance(decisions, list):
        payload["decisionCount"] = len([item for item in decisions if isinstance(item, dict)])
        payload["decisions"] = [
            {field: item[field] for field in ("title", "category", "impact", "status") if field in item}
            for item in decisions[:8]
            if isinstance(item, dict)
        ]

    policy = details.get("researchPolicy") if isinstance(details, dict) else None
    if isinstance(policy, dict):
        payload["researchPolicy"] = policy
    return redact_secrets(payload)


def _research_requires_network_check(reason: str, details: dict[str, Any]) -> bool:
    remediation = details.get("remediation") if isinstance(details, dict) else None
    action = remediation.get("action") if isinstance(remediation, dict) else None
    if action == "check_network_access":
        return True
    text = f"{reason} {details}".lower()
    return any(
        token in text for token in ("network", "urlopen", "timed out", "timeout", "offline", "internet")
    )


def _runtime_not_executable_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    runtime_settings_payload = context.runtime_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open runtime settings",
            "description": "Configure an executable CLI, API, Ollama, or local runtime before retrying.",
            "payload": runtime_settings_payload,
        },
        {
            "actionType": "validate_runtime",
            "title": "Validate runtime",
            "description": "Re-check local runtime availability after fixing installation or PATH.",
            "payload": {**runtime_settings_payload, "settingsSection": "providers-cli"},
        },
        {
            "actionType": "switch_runtime",
            "title": "Switch runtime",
            "description": "Select a different executable runtime for this blocked run.",
            "payload": {**runtime_settings_payload, "settingsSection": "providers-cli"},
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after configuring or selecting an executable runtime.",
            "payload": {**runtime_settings_payload, "retryTarget": "runtime"},
        },
    ]


def _runtime_auth_missing_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    runtime_settings_payload = context.runtime_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open runtime credentials",
            "description": "Open provider/runtime credentials settings.",
            "payload": {**runtime_settings_payload, "section": "credentials"},
        },
        {
            "actionType": "validate_runtime",
            "title": "Validate runtime",
            "description": "Validate runtime authentication after updating credentials.",
            "payload": runtime_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after fixing runtime authentication.",
            "payload": {**runtime_settings_payload, "retryTarget": "runtime_auth"},
        },
    ]


def _runtime_output_invalid_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    runtime_recovery_payload = context.runtime_recovery_payload
    return [
        {
            "actionType": "continue_plan_only",
            "title": "Continue plan-only",
            "description": "Continue without executing code while runtime output is invalid.",
            "payload": runtime_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry the blocked Product Loop after correcting the runtime output problem.",
            "payload": {**runtime_recovery_payload, "retryTarget": "runtime"},
        },
    ]


def _git_not_initialized_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    return [
        {
            "actionType": "git_init",
            "title": "Initialize Git repository",
            "description": "Create Git metadata in the project folder before Product Loop execution.",
            "payload": context.git_init_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry the blocked Product Loop after Git has been initialized.",
            "payload": {**context.git_dirty_tree_payload, "retryTarget": "git_not_initialized"},
        },
    ]


def _git_dirty_tree_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    git_dirty_tree_payload = context.git_dirty_tree_payload
    return [
        {
            "actionType": "view_diff",
            "title": "View current diff",
            "description": "Inspect the dirty tree before Product Loop execution continues.",
            "payload": git_dirty_tree_payload,
        },
        {
            "actionType": "create_branch",
            "title": "Create branch",
            "description": "Create a branch to isolate the current dirty work.",
            "payload": {
                **git_dirty_tree_payload,
                "branchName": "codex/remediate-dirty-tree",
            },
        },
        {
            "actionType": "save_patch",
            "title": "Save patch",
            "description": "Capture the current patch so the user can preserve dirty changes.",
            "payload": git_dirty_tree_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the dirty tree is preserved, committed, stashed, or otherwise cleaned.",
            "payload": {**git_dirty_tree_payload, "retryTarget": "git_dirty_tree"},
        },
    ]


def _git_status_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    git_dirty_tree_payload = context.git_dirty_tree_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open workspace settings",
            "description": "Review the project path and Git workspace settings before retrying status.",
            "payload": {"section": "workspaces", **git_dirty_tree_payload},
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after Git status can be collected from the project workspace.",
            "payload": {**git_dirty_tree_payload, "retryTarget": "git_status_failed"},
        },
    ]


def _git_branch_missing_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    git_dirty_tree_payload = context.git_dirty_tree_payload
    return [
        {
            "actionType": "create_branch",
            "title": "Create branch",
            "description": "Create the missing branch required for execution.",
            "payload": git_dirty_tree_payload,
        },
        {
            "actionType": "checkout_branch",
            "title": "Checkout branch",
            "description": "Switch to an existing branch before continuing.",
            "payload": git_dirty_tree_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after creating or checking out the branch required for execution.",
            "payload": {**git_dirty_tree_payload, "retryTarget": "git_branch_missing"},
        },
    ]


def _git_remote_missing_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    git_dirty_tree_payload = context.git_dirty_tree_payload
    return [
        *context.git_remote_add_specs,
        {
            "actionType": "open_settings_section",
            "title": "Open Git remote settings",
            "description": "Reconnect or re-add the project Git remote before delivery continues.",
            "payload": {"section": "workspaces", **git_dirty_tree_payload},
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the project Git remote is reachable again.",
            "payload": {
                "section": "workspaces",
                **git_dirty_tree_payload,
                "retryTarget": "git_remote",
            },
        },
    ]


def _gitleaks_missing_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    gitleaks_recovery_payload = context.gitleaks_recovery_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open security tools settings",
            "description": "Configure the gitleaks executable required by the security gate.",
            "payload": {"section": "security-tools", **gitleaks_recovery_payload},
        },
        {
            "actionType": "run_gitleaks",
            "title": "Run gitleaks",
            "description": "Retry the gitleaks gate after installing the executable.",
            "payload": gitleaks_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the gitleaks executable is installed and the gate passes.",
            "payload": {**gitleaks_recovery_payload, "retryTarget": "gitleaks"},
        },
    ]


def _gitleaks_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    gitleaks_recovery_payload = context.gitleaks_recovery_payload
    return [
        {
            "actionType": "run_gitleaks",
            "title": "Run gitleaks",
            "description": "Retry the gitleaks gate after removing detected secrets.",
            "payload": gitleaks_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after detected secrets have been removed and gitleaks passes.",
            "payload": {**gitleaks_recovery_payload, "retryTarget": "gitleaks"},
        },
    ]


def _qa_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    qa_recovery_payload = context.qa_recovery_payload
    return [
        {
            "actionType": "continue_plan_only",
            "title": "Continue plan-only",
            "description": "Continue planning while QA failures are reviewed.",
            "payload": qa_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry the loop after addressing QA failures.",
            "payload": {**qa_recovery_payload, "retryTarget": "qa"},
        },
    ]


def _po_needs_input_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    details = context.details
    pending_decisions = details.get("pendingDecisions") if isinstance(details, dict) else None
    specs = [
        {
            "actionType": "answer_question",
            "title": str(item.get("title") or "Answer ProductOwnerAgent question"),
            "description": "Provide the missing product decision or clarification.",
            "payload": {
                "decisionId": item.get("decisionId"),
                "prompt": item.get("prompt") or item.get("title"),
                "clarificationQuestionId": item.get("clarificationQuestionId"),
                "productDecisionId": item.get("productDecisionId"),
                "initiativeId": item.get("initiativeId"),
                "options": item.get("options") or [],
            },
        }
        for item in (pending_decisions or [])
        if isinstance(item, dict) and str(item.get("decisionId") or "").strip()
    ]
    if specs:
        return specs
    return [
        {
            "actionType": "answer_question",
            "title": "Answer ProductOwnerAgent question",
            "description": "Provide the missing product decision or clarification.",
            "payload": {
                "decisionId": details.get("decisionId"),
                "prompt": details.get("prompt") or context.reason,
                "clarificationQuestionId": details.get("clarificationQuestionId"),
                "productDecisionId": details.get("productDecisionId"),
                "initiativeId": details.get("initiativeId"),
                "options": details.get("options") or [],
            },
        }
    ]


def _worker_not_running_specs(_context: BlockerPayloadContext) -> list[dict[str, Any]]:
    return [
        {
            "actionType": "run_worker_once",
            "title": "Run worker once",
            "description": "Run one bounded worker batch to process queued thread work.",
            "payload": {},
        }
    ]


def _provider_missing_credentials_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    provider_settings_payload = context.provider_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open credentials settings",
            "description": "Configure the provider API key using the known provider defaults.",
            "payload": provider_settings_payload,
        },
        {
            "actionType": "validate_runtime",
            "title": "Validate provider",
            "description": "Validate provider health after updating credentials.",
            "payload": context.runtime_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after provider credentials are configured and validated.",
            "payload": {**provider_settings_payload, "retryTarget": "provider_credentials"},
        },
    ]


def _provider_health_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    runtime_payload = context.runtime_payload
    return [
        {
            "actionType": "validate_runtime",
            "title": "Validate provider health",
            "description": "Re-check provider health.",
            "payload": runtime_payload,
        },
        {
            "actionType": "switch_runtime",
            "title": "Switch runtime",
            "description": "Switch away from the unhealthy provider.",
            "payload": runtime_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after provider health recovers or a healthy runtime is selected.",
            "payload": {**runtime_payload, "retryTarget": "provider_health"},
        },
    ]


def _resource_manager_unconfigured_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    resource_manager_settings_payload = context.resource_manager_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open provider and model settings",
            "description": "Enable a catalogued model with the capabilities required by the blocked role.",
            "payload": {**resource_manager_settings_payload, "section": "providers-cli"},
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after enabling an eligible model and executable runtime.",
            "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
        },
    ]


def _resource_manager_privacy_blocked_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    resource_manager_settings_payload = context.resource_manager_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open local provider settings",
            "description": (
                "Configure and validate an executable local model while preserving "
                "the project's local-only privacy policy."
            ),
            "payload": {**resource_manager_settings_payload, "section": "providers-cli"},
            "primary": True,
        },
        {
            "actionType": "open_settings_section",
            "title": "Review AI routing privacy",
            "description": ("Review the local-only routing requirement and the rejected remote resources."),
            "payload": {**resource_manager_settings_payload, "section": "routing"},
            "primary": False,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": (
                "Retry after an eligible local model is executable and satisfies the routing policy."
            ),
            "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
            "primary": False,
        },
    ]


def _resource_manager_approval_required_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    resource_manager_settings_payload = context.resource_manager_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open AI resources settings",
            "description": "Review the selected AI resource, cost policy, and approval requirement.",
            "payload": resource_manager_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after approving or changing the AI resource policy.",
            "payload": {**resource_manager_settings_payload, "retryTarget": "resource_manager"},
        },
    ]


def _technical_lead_planning_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    team_scheduler_settings_payload = context.team_scheduler_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open team planning settings",
            "description": "Review TeamScheduler and TechnicalLead planning configuration before execution.",
            "payload": team_scheduler_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after TechnicalLead can generate required role tasks.",
            "payload": {**team_scheduler_settings_payload, "retryTarget": "technical_lead"},
        },
    ]


def _team_scheduler_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    team_scheduler_settings_payload = context.team_scheduler_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open team planning settings",
            "description": "Review TeamScheduler scope, risk, and mode configuration before execution.",
            "payload": team_scheduler_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after TeamScheduler can produce a valid role schedule.",
            "payload": {**team_scheduler_settings_payload, "retryTarget": "team_scheduler"},
        },
    ]


# A brief/backlog that fails validation almost always means the runtime behind
# ProductOwnerAgent answered badly, so the runtime repairs lead the settings navigation.
def _product_owner_output_invalid_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    runtime_payload = context.runtime_payload
    product_owner_settings_payload = context.product_owner_settings_payload
    return [
        {
            "actionType": "validate_runtime",
            "title": "Validate runtime",
            "description": "Re-check the runtime that ProductOwnerAgent used before retrying the loop.",
            "payload": {**runtime_payload, "settingsSection": "providers-cli"},
            "primary": True,
        },
        {
            "actionType": "switch_runtime",
            "title": "Switch runtime",
            "description": "Select a different executable runtime for ProductOwnerAgent.",
            "payload": {**runtime_payload, "settingsSection": "providers-cli"},
        },
        {
            "actionType": "open_settings_section",
            "title": "Open ProductOwner settings",
            "description": "Review ProductOwnerAgent runtime and output configuration before backlog generation.",
            "payload": product_owner_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after ProductOwnerAgent can produce a validated brief or backlog.",
            "payload": {**product_owner_settings_payload, "retryTarget": "product_owner"},
        },
    ]


def _research_required_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    research_recovery_payload = context.research_recovery_payload
    if _research_requires_network_check(context.reason, context.details):
        return [
            {
                "actionType": "check_network_access",
                "title": "Check network access",
                "description": "Verify ResearchAgent can reach the web-search endpoint before retrying.",
                "payload": {"section": "internet", **research_recovery_payload},
                "primary": True,
            },
            {
                "actionType": "run_worker_once",
                "title": "Run research worker once",
                "description": "Process the queued ResearchAgent job after connectivity is restored.",
                "payload": research_recovery_payload,
            },
            {
                "actionType": "retry_loop",
                "title": "Retry loop",
                "description": "Retry after the required research evidence is available.",
                "payload": {**research_recovery_payload, "retryTarget": "research"},
            },
        ]
    return [
        {
            "actionType": "run_worker_once",
            "title": "Run research worker once",
            "description": "Process the queued ResearchAgent job required before this loop can continue.",
            "payload": research_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the required research evidence is available.",
            "payload": {**research_recovery_payload, "retryTarget": "research"},
        },
    ]


def _workspace_root_missing_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    workspace_recovery_payload = context.workspace_recovery_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open project workspace settings",
            "description": "Configure the project workspace root required for isolated execution.",
            "payload": workspace_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after configuring a valid workspace root.",
            "payload": {**workspace_recovery_payload, "retryTarget": "workspace_root"},
        },
    ]


def _workspace_allocation_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    workspace_recovery_payload = context.workspace_recovery_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open workspace isolation settings",
            "description": "Review worktree, branch, and workspace isolation settings before execution.",
            "payload": workspace_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after resolving the workspace allocation blocker.",
            "payload": {**workspace_recovery_payload, "retryTarget": "workspace_allocation"},
        },
    ]


def _review_diff_unavailable_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    review_diff_payload = context.review_diff_payload
    return [
        {
            "actionType": "view_diff",
            "title": "View current diff",
            "description": "Inspect the available project diff before retrying review capture.",
            "payload": review_diff_payload,
        },
        {
            "actionType": "save_patch",
            "title": "Save patch",
            "description": "Persist the available patch as evidence before retrying or requesting changes.",
            "payload": review_diff_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the runtime produces real changed files in the assigned workspace.",
            "payload": {**review_diff_payload, "retryTarget": "review_diff"},
        },
    ]


def _approval_unavailable_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    delivery_approval_payload = context.delivery_approval_payload
    return [
        {
            "actionType": "view_diff",
            "title": "View delivery diff",
            "description": "Inspect the diff and evidence that could not be attached to an approval request.",
            "payload": delivery_approval_payload,
        },
        {
            "actionType": "save_patch",
            "title": "Save patch",
            "description": "Persist the available patch before retrying approval creation.",
            "payload": delivery_approval_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after delivery approval persistence is available.",
            "payload": {**delivery_approval_payload, "retryTarget": "delivery_approval"},
        },
    ]


def _resource_learning_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    resource_learning_settings_payload = context.resource_learning_settings_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open AI resource routing",
            "description": "Review AI resource routing and metrics persistence before approving delivery.",
            "payload": resource_learning_settings_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after AI resource learning can persist cost and quality observations.",
            "payload": {**resource_learning_settings_payload, "retryTarget": "resource_learning"},
        },
    ]


def _project_assessment_failed_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    workspace_recovery_payload = context.workspace_recovery_payload
    return [
        {
            "actionType": "open_settings_section",
            "title": "Open project assessment settings",
            "description": "Review project path, assessment inputs, and workspace access before ProductOwnerAgent runs.",
            "payload": workspace_recovery_payload,
        },
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after the project assessment can read the existing workspace.",
            "payload": {**workspace_recovery_payload, "retryTarget": "project_assessment"},
        },
    ]


def _functionality_memory_decision_required_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    details = context.details
    return [
        {
            "actionType": "answer_question",
            "title": "Choose existing functionality action",
            "description": "Choose whether to continue, improve, run a performance pass, or create a new thread anyway.",
            "payload": {
                "decisionId": details.get("decisionId"),
                "prompt": details.get("prompt") or context.reason,
                "options": details.get("options") or [],
                "functionalityId": details.get("functionalityId"),
                "sourceThreadId": details.get("sourceThreadId"),
            },
        }
    ]


def _thread_similarity_decision_required_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    details = context.details
    return [
        {
            "actionType": "answer_question",
            "title": "Choose similar thread action",
            "description": "Choose whether to continue the existing thread, improve it, run a performance pass, or create a new thread anyway.",
            "payload": {
                "decisionId": details.get("decisionId"),
                "prompt": details.get("prompt") or context.reason,
                "options": details.get("options") or [],
                "candidateThreadId": details.get("candidateThreadId"),
                "candidateTitle": details.get("candidateTitle"),
                "score": details.get("score"),
            },
        }
    ]


def _thread_intake_decision_required_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    details = context.details
    return [
        {
            "actionType": "answer_question",
            "title": "Answer intake decision",
            "description": "Choose one of the offered intake options so AIDO can continue safely.",
            "payload": {
                "decisionId": details.get("decisionId"),
                "prompt": details.get("prompt") or context.reason,
                "options": details.get("options") or [],
                "planMode": details.get("planMode"),
                "sourceMessageId": details.get("sourceMessageId"),
            },
        }
    ]


Builder = Callable[[BlockerPayloadContext], list[dict[str, Any]]]

PAYLOAD_BUILDERS: dict[str, Builder] = {
    "runtime_not_executable": _runtime_not_executable_specs,
    "runtime_auth_missing": _runtime_auth_missing_specs,
    "runtime_output_invalid": _runtime_output_invalid_specs,
    "git_not_initialized": _git_not_initialized_specs,
    "git_dirty_tree": _git_dirty_tree_specs,
    "git_status_failed": _git_status_failed_specs,
    "git_branch_missing": _git_branch_missing_specs,
    "git_remote_missing": _git_remote_missing_specs,
    "gitleaks_missing": _gitleaks_missing_specs,
    "gitleaks_failed": _gitleaks_failed_specs,
    "qa_failed": _qa_failed_specs,
    "po_needs_input": _po_needs_input_specs,
    "worker_not_running": _worker_not_running_specs,
    "provider_missing_credentials": _provider_missing_credentials_specs,
    "provider_health_failed": _provider_health_failed_specs,
    "resource_manager_unconfigured": _resource_manager_unconfigured_specs,
    "resource_manager_privacy_blocked": _resource_manager_privacy_blocked_specs,
    "resource_manager_approval_required": _resource_manager_approval_required_specs,
    "technical_lead_planning_failed": _technical_lead_planning_failed_specs,
    "team_scheduler_failed": _team_scheduler_failed_specs,
    "product_owner_output_invalid": _product_owner_output_invalid_specs,
    "research_required": _research_required_specs,
    "workspace_root_missing": _workspace_root_missing_specs,
    "workspace_allocation_failed": _workspace_allocation_failed_specs,
    "review_diff_unavailable": _review_diff_unavailable_specs,
    "approval_unavailable": _approval_unavailable_specs,
    "resource_learning_failed": _resource_learning_failed_specs,
    "project_assessment_failed": _project_assessment_failed_specs,
    "functionality_memory_decision_required": _functionality_memory_decision_required_specs,
    "thread_similarity_decision_required": _thread_similarity_decision_required_specs,
    "thread_intake_decision_required": _thread_intake_decision_required_specs,
}
