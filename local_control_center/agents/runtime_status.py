"""Computes the availability/executability of every runtime provider for the UI.

Joins provider accounts, model catalog, runtime detections, and env configuration into
one status per provider (API, CLI, Ollama, manual), deciding detected/configured/
available/executable and a human reason. Execution is gated by explicit env flags
(`AIDO_ENABLE_REAL_PROVIDER_CALLS`, `AIDO_ENABLE_CLI_RUNTIMES`); error text is redacted.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.runtime_integrations.config import resolve_executable
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .developer_agent_contract import developer_agent_readiness
from .model_gateway import ollama_status
from .provider_accounts import ProviderAccountStore
from .runtime_provider_config import RuntimeProviderConfiguration, runtime_provider_configuration
from .runtime_registry import RuntimeRegistry

RUNTIME_MODES = ["api", "cli", "ollama", "hybrid", "manual"]
CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}
API_RUNTIME_KINDS = {"api", "gateway"}
OPENAI_COMPATIBLE_FORMATS = {"openai_compatible", "responses"}
CLI_EXECUTABLE_TOKENS = {
    "codex_cli": ("codex",),
    "claude_code_cli": ("claude",),
}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() == "true"


def _capabilities(connection: sqlite3.Connection) -> dict[str, list[str]]:
    rows = connection.execute(
        """
        SELECT runtime, capability
        FROM runtime_capabilities
        WHERE enabled = 1
        ORDER BY runtime ASC, capability ASC
        """
    ).fetchall()
    capabilities: dict[str, list[str]] = {}
    for row in rows:
        capabilities.setdefault(str(row["runtime"]), []).append(str(row["capability"]))
    capabilities.setdefault("ollama", ["chat"])
    capabilities.setdefault("manual", ["approval"])
    return capabilities


def _has_enabled_model(connection: sqlite3.Connection, provider_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM model_catalog
        WHERE provider_id = ? AND enabled = 1
        LIMIT 1
        """,
        (provider_id,),
    ).fetchone()
    return row is not None


def _base_safety(kind: str) -> dict[str, Any]:
    if kind == "cli":
        return {
            "workspaceBound": True,
            "shell": False,
            "structuredArgv": True,
            "network": "runtime_policy_gated",
        }
    if kind in API_RUNTIME_KINDS:
        return {
            "workspaceBound": False,
            "shell": False,
            "structuredArgv": True,
            "network": "remote_calls_disabled_by_default",
        }
    return {"workspaceBound": True, "shell": False, "structuredArgv": True, "network": "local_only"}


def _status_payload(
    *,
    provider_id: str,
    kind: str,
    display_name: str,
    detected: bool,
    configured: bool,
    available: bool,
    executable: bool,
    reason: str,
    version: str | None,
    detected_command: str | None,
    health_checked_at: str | None,
    capabilities: list[str],
    required_configuration: list[str],
    requires_approval: bool,
    health_status: str = "unknown",
    last_error: str = "",
) -> dict[str, Any]:
    sanitized_last_error = str(redact_secrets(last_error or ""))
    return {
        "id": provider_id,
        "kind": kind,
        "displayName": display_name,
        "detected": detected,
        "configured": configured,
        "available": available,
        "executable": executable,
        "requiresApproval": requires_approval,
        "reason": reason,
        "version": version,
        "detectedCommand": detected_command,
        "healthStatus": health_status,
        "healthCheckedAt": health_checked_at,
        "lastError": sanitized_last_error,
        "capabilities": capabilities,
        "requiredConfiguration": required_configuration,
        "safety": _base_safety(kind),
    }


def _api_required_configuration(account: dict[str, Any]) -> list[str]:
    api_format = str(account.get("apiFormat") or "")
    if api_format in OPENAI_COMPATIBLE_FORMATS or account["providerId"] in {"openai_compatible", "litellm"}:
        return ["baseUrl", "apiKey", "model"]
    if account["providerId"] == "anthropic_api":
        return ["apiKey", "model"]
    return ["baseUrl", "apiKey"]


def _api_account_configuration(connection: sqlite3.Connection, account: dict[str, Any]) -> dict[str, Any]:
    required_configuration = _api_required_configuration(account)
    credential_status = str(account.get("credentialStatus") or "unknown")
    base_url = str(account.get("baseUrl") or "").strip()
    return {
        "requiredConfiguration": required_configuration,
        "hasModel": "model" not in required_configuration
        or _has_enabled_model(connection, str(account["providerId"])),
        "hasBaseUrl": "baseUrl" not in required_configuration or bool(base_url),
        "hasCredential": "apiKey" not in required_configuration or credential_status == "configured",
        "credentialStatus": credential_status,
    }


def _runtime_configuration_present(configuration: RuntimeProviderConfiguration | None, key: str) -> bool:
    return bool(configuration and configuration.value(key))


def _configured_argv(
    configuration: RuntimeProviderConfiguration | None, key: str
) -> tuple[list[str] | None, str | None]:
    raw = configuration.value(key) if configuration else None
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{key} must be valid JSON."
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
    ):
        return None, f"{key} must be a non-empty JSON array of strings."
    return list(parsed), None


def _cli_command_matches_provider(provider_id: str, detection: dict[str, Any]) -> bool:
    required_tokens = CLI_EXECUTABLE_TOKENS.get(provider_id)
    if not required_tokens:
        return True
    executable = str(detection.get("executable") or "").strip()
    if not executable:
        return False
    executable_name = Path(executable).name.lower()
    return all(token in executable_name for token in required_tokens)


def _api_provider_status(
    connection: sqlite3.Connection,
    account: dict[str, Any],
    capabilities: list[str],
    configuration: RuntimeProviderConfiguration | None = None,
) -> dict[str, Any]:
    provider_id = str(account["providerId"])
    enabled = bool(account.get("enabled"))
    health_status = str(account.get("healthStatus") or "unknown")
    health_checked_at = account.get("lastHealthCheckAt")
    last_error = str(redact_secrets(str(account.get("lastError") or "").strip()))
    account_configuration = _api_account_configuration(connection, account)
    required_configuration = account_configuration["requiredConfiguration"]
    has_model = bool(
        account_configuration["hasModel"] or _runtime_configuration_present(configuration, "model")
    )
    has_base_url = bool(
        account_configuration["hasBaseUrl"] or _runtime_configuration_present(configuration, "baseUrl")
    )
    has_credential = bool(
        account_configuration["hasCredential"] or _runtime_configuration_present(configuration, "apiKey")
    )
    configured = bool(
        (configuration and configuration.configured) or (has_base_url and has_credential and has_model)
    )
    remote_calls_enabled = _env_flag("AIDO_ENABLE_REAL_PROVIDER_CALLS")
    healthy = health_status == "healthy" and bool(health_checked_at)
    available = configured and healthy
    executable = available and enabled and remote_calls_enabled
    if not configured and configuration is not None and not configuration.configured:
        reason = configuration.reason
    elif not has_credential:
        reason = f"Provider credential is {account_configuration['credentialStatus']}; configure the required API key."
    elif not has_base_url:
        reason = "Provider base URL is not configured."
    elif not has_model:
        reason = "Provider model is not configured or enabled."
    elif not healthy:
        health_reason = last_error or f"health status is {health_status}"
        reason = f"Provider has not passed an explicit health check ({health_reason})."
    elif not enabled:
        reason = "Provider is available but the provider account is disabled for execution."
    elif not remote_calls_enabled:
        reason = (
            "Provider is available but remote execution is disabled by AIDO_ENABLE_REAL_PROVIDER_CALLS=false."
        )
    else:
        reason = "Provider is configured, health checked, and executable."
    return _status_payload(
        provider_id=provider_id,
        kind=str(account["providerType"]),
        display_name=str(account["displayName"]),
        detected=available,
        configured=configured,
        available=available,
        executable=executable,
        reason=reason,
        version=None,
        detected_command=None,
        health_status=health_status,
        health_checked_at=health_checked_at,
        last_error=last_error,
        capabilities=capabilities or ["chat"],
        required_configuration=required_configuration,
        requires_approval=True,
    )


def _cli_provider_status(
    account: dict[str, Any],
    detection: dict[str, Any],
    capabilities: list[str],
    configuration: RuntimeProviderConfiguration | None = None,
) -> dict[str, Any]:
    detected = detection.get("status") == "installed"
    enabled = bool(account.get("enabled"))
    cli_enabled = _env_flag("AIDO_ENABLE_CLI_RUNTIMES")
    can_code_edit = "code_edit" in set(capabilities)
    issue_to_patch_argv, issue_to_patch_argv_error = _configured_argv(configuration, "issueToPatchArgv")
    version = detection.get("version") if detected else None
    command_configured = bool(detection.get("executable"))
    configured = bool(command_configured or (configuration and configuration.configured) or detected)
    available = configured and detected and bool(version)
    command_matches_provider = (
        _cli_command_matches_provider(str(account["providerId"]), detection) if can_code_edit else True
    )
    executable = (
        available
        and enabled
        and cli_enabled
        and can_code_edit
        and command_matches_provider
        and issue_to_patch_argv_error is None
    )
    if not configured and configuration is not None:
        reason = (
            f"{configuration.reason}; CLI runtime was not detected because command configuration is missing."
        )
    elif not detected:
        reason = str(detection.get("message") or "CLI runtime was not detected.")
    elif not version:
        reason = "CLI runtime was detected but the safe version health check did not return a usable version."
    elif not enabled:
        reason = "CLI runtime is available but the provider account is disabled for execution."
    elif not cli_enabled:
        reason = "CLI runtime is available but execution is disabled by AIDO_ENABLE_CLI_RUNTIMES=false."
    elif issue_to_patch_argv_error:
        reason = issue_to_patch_argv_error
    elif not can_code_edit:
        reason = "Runtime does not advertise the code_edit capability required by DeveloperAgent."
    elif not command_matches_provider:
        reason = "Runtime detected executable does not match the declared runtime command."
    else:
        reason = "CLI runtime is detected, enabled, and executable."
    payload = _status_payload(
        provider_id=str(account["providerId"]),
        kind="cli",
        display_name=str(account["displayName"]),
        detected=detected,
        configured=configured,
        available=available,
        executable=executable,
        reason=reason,
        version=version,
        detected_command=detection.get("executable") if detected else None,
        health_status="healthy" if available else "offline",
        health_checked_at=utc_now(),
        last_error="" if available else str(detection.get("message") or ""),
        capabilities=capabilities,
        required_configuration=["command"],
        requires_approval=True,
    )
    if issue_to_patch_argv is not None:
        payload["issueToPatchArgv"] = issue_to_patch_argv
    if issue_to_patch_argv_error:
        payload["lastError"] = issue_to_patch_argv_error
    return payload


def _ollama_provider_status(
    account: dict[str, Any],
    capabilities: list[str],
    configuration: RuntimeProviderConfiguration | None = None,
) -> dict[str, Any]:
    base_url = (
        (configuration.value("baseUrl") if configuration else None)
        or str(account.get("baseUrl") or "").strip()
        or os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or ""
    )
    status = (
        ollama_status(base_url=base_url)
        if base_url
        else {
            "provider": "ollama",
            "available": False,
            "models": [],
            "reason": "Ollama base URL is not configured.",
        }
    )
    daemon_available = bool(status.get("available"))
    enabled = bool(account.get("enabled"))
    configured = bool((configuration and configuration.configured) or base_url)
    executable = daemon_available and enabled
    if not configured and configuration is not None:
        reason = configuration.reason
    elif not configured:
        reason = "Ollama base URL is not configured."
    elif not daemon_available:
        reason = str(status.get("reason") or "Ollama daemon did not respond to /api/tags.")
    elif not enabled:
        reason = "Ollama daemon is reachable but the provider account is disabled for execution."
    else:
        reason = "Ollama daemon is reachable and executable."
    payload = _status_payload(
        provider_id="ollama",
        kind="local",
        display_name=str(account.get("displayName") or "Ollama"),
        detected=daemon_available,
        configured=configured,
        available=daemon_available,
        executable=executable,
        reason=reason,
        version=None,
        detected_command=None,
        health_status="healthy" if daemon_available else "offline",
        health_checked_at=utc_now(),
        last_error="" if daemon_available else reason,
        capabilities=capabilities or ["chat"],
        required_configuration=["baseUrl"],
        requires_approval=False,
    )
    payload["models"] = status.get("models") or []
    return payload


def _manual_provider_status(account: dict[str, Any], capabilities: list[str]) -> dict[str, Any]:
    enabled = bool(account.get("enabled"))
    return _status_payload(
        provider_id="manual",
        kind="manual",
        display_name=str(account.get("displayName") or "Manual Operator"),
        detected=enabled,
        configured=enabled,
        available=False,
        executable=False,
        reason="Manual operator path is configured but is not an automated available or executable runtime.",
        version=None,
        detected_command=None,
        health_status=str(account.get("healthStatus") or "unknown"),
        health_checked_at=account.get("lastHealthCheckAt"),
        last_error=str(account.get("lastError") or ""),
        capabilities=capabilities or ["approval"],
        required_configuration=["operator"],
        requires_approval=True,
    )


class RuntimeStatusService:
    """Derives per-provider runtime status from accounts, detections, and configuration."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)
        self.registry = RuntimeRegistry()

    def list_provider_statuses(self) -> list[dict[str, Any]]:
        """Compute a status payload for every catalogued provider, dispatching by kind.

        CLI providers are detected by their configured command, API/Ollama providers by
        configuration plus health, manual by enablement; unknown kinds report non-executable.
        """
        capabilities = _capabilities(self.connection)
        configurations = {
            provider_id: runtime_provider_configuration(provider_id)
            for provider_id in CLI_RUNTIME_IDS
            | {"openai_compatible", "openrouter", "nvidia_nim", "anthropic_api", "ollama"}
        }
        runtime_installations = {
            installation["runtimeId"]: installation
            for installation in RuntimeConfigRepository(self.connection).list_installations()
        }
        detections: dict[str, dict[str, Any]] = {}
        for runtime_id in sorted(CLI_RUNTIME_IDS):
            configuration = configurations.get(runtime_id)
            installation = runtime_installations.get(
                runtime_id, {"runtimeId": runtime_id, "executablePath": None}
            )
            command = resolve_executable(installation).get("path")
            detections[runtime_id] = (
                self.registry.detect(runtime_id, executable=command)
                if command
                else {
                    "runtime": runtime_id,
                    "status": "not_configured",
                    "executable": None,
                    "version": None,
                    "message": configuration.reason if configuration else "CLI command is not configured.",
                }
            )
        statuses: list[dict[str, Any]] = []
        for account in self.accounts.list_provider_accounts():
            provider_id = str(account["providerId"])
            provider_capabilities = capabilities.get(provider_id, [])
            provider_type = str(account.get("providerType") or "")
            if provider_id == "ollama":
                statuses.append(
                    _ollama_provider_status(account, provider_capabilities, configurations.get(provider_id))
                )
            elif provider_id == "manual":
                statuses.append(_manual_provider_status(account, provider_capabilities))
            elif provider_id in CLI_RUNTIME_IDS:
                statuses.append(
                    _cli_provider_status(
                        account,
                        detections.get(provider_id, {}),
                        provider_capabilities,
                        configurations.get(provider_id),
                    )
                )
            elif provider_type in API_RUNTIME_KINDS:
                statuses.append(
                    _api_provider_status(
                        self.connection,
                        account,
                        provider_capabilities,
                        configurations.get(provider_id),
                    )
                )
            else:
                statuses.append(
                    _status_payload(
                        provider_id=provider_id,
                        kind=provider_type or "unknown",
                        display_name=str(account.get("displayName") or provider_id),
                        detected=False,
                        configured=False,
                        available=False,
                        executable=False,
                        reason="Provider type is not executable by the local control plane.",
                        version=None,
                        detected_command=None,
                        health_status=str(account.get("healthStatus") or "unknown"),
                        health_checked_at=account.get("lastHealthCheckAt"),
                        last_error=str(account.get("lastError") or ""),
                        capabilities=provider_capabilities,
                        required_configuration=[],
                        requires_approval=True,
                    )
                )
        return statuses

    def runtime_provider_status(self) -> dict[str, Any]:
        """Summarize provider statuses into Ollama/CLI/API rollups and DeveloperAgent readiness."""
        providers = self.list_provider_statuses()
        ollama = next((provider for provider in providers if provider["id"] == "ollama"), None)
        cli_providers = [provider for provider in providers if provider["id"] in CLI_RUNTIME_IDS]
        api_providers = [provider for provider in providers if provider["kind"] in API_RUNTIME_KINDS]
        return {
            "runtimeModes": RUNTIME_MODES,
            "ollama": {
                "provider": "ollama",
                "available": bool(ollama and ollama["available"]),
                "models": list((ollama or {}).get("models") or []),
                "reason": str(ollama.get("reason") if ollama else "Ollama provider is not catalogued."),
            },
            "cli": {
                "provider": "cli",
                "available": any(provider["available"] for provider in cli_providers),
                "adapters": {
                    "cli_codex": bool(
                        next((item for item in cli_providers if item["id"] == "codex_cli"), {}).get(
                            "available"
                        )
                    ),
                    "cli_claude": bool(
                        next((item for item in cli_providers if item["id"] == "claude_code_cli"), {}).get(
                            "available"
                        )
                    ),
                },
            },
            "api": {
                "provider": "api",
                "available": any(provider["available"] for provider in api_providers),
                "adapters": [provider["id"] for provider in api_providers],
            },
            "developerAgent": developer_agent_readiness(providers),
            "providers": providers,
        }
