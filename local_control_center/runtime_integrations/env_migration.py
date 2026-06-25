"""Migración deprecada de configuración por variables de entorno a las tablas persistidas.

Lee las env vars heredadas (rutas de CLI, GitHub token, API keys de proveedores y refs de auth de
Vault) y las registra en ``runtime_installations`` (ruta del ejecutable) y ``credential_refs``
    (referencia + fingerprint; el valor del secreto NUNCA se copia, queda en el entorno), enlazando además
``provider_accounts``. El valor sigue resolviéndose desde el adapter ``environment_override`` durante
el período de deprecación: cada variable usada emite un warning y queda marcada con
``source=environment_override``.
La migración es idempotente (upsert por runtime/credencial) y no almacena ni registra ningún secreto.
"""

from __future__ import annotations

import logging
from typing import Any

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.credentials.manager import new_salt, secret_fingerprint
from local_control_center.credentials.repository import CredentialRepository

from .repository import RuntimeConfigRepository

logger = logging.getLogger(__name__)

ENVIRONMENT_OVERRIDE_SOURCE = "environment_override"

# Ejecutables de runtime: (runtime_id, kind, env vars en orden de precedencia) -> runtime_installations.
RUNTIME_EXECUTABLE_MIGRATIONS = (
    ("codex_cli", "cli", ("AIDO_CODEX_COMMAND", "CODEX_CLI_PATH")),
    ("claude_code_cli", "cli", ("AIDO_CLAUDE_COMMAND",)),
)
# Secretos: (nombre de credencial, env var, auth_mode, provider_id a re-enlazar o None) -> credential_refs.
CREDENTIAL_MIGRATIONS = (
    ("github/token", "AIDO_GITHUB_TOKEN", "token", None),
    ("provider/openai_compatible", "AIDO_OPENAI_COMPATIBLE_API_KEY", "api_key", "openai_compatible"),
    ("provider/openrouter", "AIDO_OPENROUTER_API_KEY", "api_key", "openrouter"),
    ("provider/nvidia_nim", "AIDO_NVIDIA_API_KEY", "api_key", "nvidia_nim"),
    ("provider/anthropic", "AIDO_ANTHROPIC_API_KEY", "api_key", "anthropic_api"),
    ("vault/auth", "AIDO_VAULT_TOKEN", "token", None),
    ("openbao/auth", "AIDO_OPENBAO_TOKEN", "token", None),
)


def _first_set(env: dict[str, str], names: tuple[str, ...]) -> tuple[str, str] | None:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value, name
    return None


def _existing_installation(repository: RuntimeConfigRepository, runtime_id: str) -> dict[str, Any]:
    try:
        return repository.get_installation(runtime_id)
    except KeyError:
        return {}


def migrate_environment_config(
    connection: Any, *, env: dict[str, str], actor: str = "env_migration"
) -> dict[str, Any]:
    """Migra la configuración de entorno a las tablas persistidas y devuelve un reporte de lo migrado.

    Para cada env var presente: registra la ruta del ejecutable o la referencia de credencial (con su
    fingerprint, nunca el valor), re-enlaza el provider_account si aplica, emite un warning de
    deprecación y marca la procedencia como ``environment_override``. Las env vars ausentes se omiten.
    Idempotente. NO almacena ni registra ningún secreto.
    """
    runtime_repository = RuntimeConfigRepository(connection)
    credential_repository = CredentialRepository(connection)
    provider_store = ProviderAccountStore(connection)
    report: dict[str, Any] = {
        "source": ENVIRONMENT_OVERRIDE_SOURCE,
        "runtimes": [],
        "credentials": [],
        "providers": [],
        "warnings": [],
        "skipped": [],
    }

    for runtime_id, kind, env_vars in RUNTIME_EXECUTABLE_MIGRATIONS:
        found = _first_set(env, env_vars)
        if found is None:
            report["skipped"].append(
                {"target": f"runtime_installations:{runtime_id}", "envVars": list(env_vars)}
            )
            continue
        value, source_var = found
        existing = _existing_installation(runtime_repository, runtime_id)
        metadata = {
            **(existing.get("metadata") or {}),
            "source": ENVIRONMENT_OVERRIDE_SOURCE,
            "migratedFrom": source_var,
            "deprecated": True,
        }
        runtime_repository.upsert_installation(
            {
                "runtimeId": runtime_id,
                "kind": kind,
                "executablePath": value,
                "detectedVersion": existing.get("detectedVersion"),
                "enabled": existing.get("enabled", False),
                "healthStatus": existing.get("healthStatus", "unknown"),
                "lastHealthCheckAt": existing.get("lastHealthCheckAt"),
                "lastError": existing.get("lastError"),
                "metadata": metadata,
            }
        )
        warning = (
            f"{source_var} is deprecated as runtime configuration; the executable path now lives in "
            f"runtime_installations[{runtime_id}]. The environment value is kept as a fallback "
            f"(source={ENVIRONMENT_OVERRIDE_SOURCE})."
        )
        logger.warning(warning)
        report["warnings"].append(warning)
        report["runtimes"].append(
            {"runtimeId": runtime_id, "migratedFrom": source_var, "source": ENVIRONMENT_OVERRIDE_SOURCE}
        )

    for name, env_var, auth_mode, provider_id in CREDENTIAL_MIGRATIONS:
        value = str(env.get(env_var) or "").strip()
        if not value:
            report["skipped"].append({"target": f"credential_refs:{name}", "envVars": [env_var]})
            continue
        salt = new_salt()
        fingerprint = secret_fingerprint(value, salt)
        metadata = {"source": ENVIRONMENT_OVERRIDE_SOURCE, "migratedFrom": env_var, "deprecated": True}
        existing_credential = credential_repository.find_credential_by_name(name)
        patch = {
            "backendKind": ENVIRONMENT_OVERRIDE_SOURCE,
            "locator": env_var,
            "fingerprint": fingerprint,
            "salt": salt,
            "authMode": auth_mode,
            "status": "active",
            "metadata": metadata,
        }
        if existing_credential:
            credential = credential_repository.update_credential(existing_credential["id"], patch)
        else:
            credential = credential_repository.create_credential_ref({"name": name, **patch})
        credential_repository.append_audit(
            {
                "credentialId": credential["id"],
                "name": name,
                "action": "migrate",
                "outcome": "success",
                "actor": actor,
                "backendKind": ENVIRONMENT_OVERRIDE_SOURCE,
                "detail": (
                    f"Registered reference from environment variable {env_var}; env fallback is "
                    f"deprecated ({ENVIRONMENT_OVERRIDE_SOURCE}). The secret value was not copied."
                ),
            }
        )
        if provider_id:
            try:
                provider_store.patch_provider_account(provider_id, {"credentialRef": f"env:{env_var}"})
                report["providers"].append({"providerId": provider_id, "credentialRef": f"env:{env_var}"})
            except KeyError:
                pass
        warning = (
            f"{env_var} is deprecated as credential configuration; manage credential '{name}' via the "
            f"CredentialManager. The environment value is kept as a fallback "
            f"(source={ENVIRONMENT_OVERRIDE_SOURCE})."
        )
        logger.warning(warning)
        report["warnings"].append(warning)
        report["credentials"].append(
            {
                "name": name,
                "migratedFrom": env_var,
                "authMode": auth_mode,
                "source": ENVIRONMENT_OVERRIDE_SOURCE,
            }
        )

    return report
