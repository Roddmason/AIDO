"""Resolución de configuración de runtime: la base manda y las env vars son solo override.

Define qué variable de entorno puede sobrescribir cada dato persistido y aplica esa precedencia: si la
env var existe gana como override puntual, si no se usa el valor guardado en ``runtime_installations`` /
``runtime_preferences``. Así la configuración normal vive en la base y las env vars no son la fuente de
configuración, solo una excepción. No lee ni expone secretos: el override de CLI es la ruta del ejecutable.
"""

from __future__ import annotations

import os
from typing import Any

# Override de ejecutable por runtime (misma variable que el contrato heredado de runtime_provider_config).
RUNTIME_COMMAND_ENV = {
    "codex_cli": "AIDO_CODEX_COMMAND",
    "claude_code_cli": "AIDO_CLAUDE_COMMAND",
    "openhands": "AIDO_OPENHANDS_COMMAND",
    "swe_agent": "AIDO_SWE_AGENT_COMMAND",
}
DEFAULT_RUNTIME_ENV = "AIDO_DEFAULT_RUNTIME"
CLI_RUNTIME_IDS = {"codex_cli", "claude_code_cli", "openhands", "swe_agent"}


def _env_value(name: str, env: dict[str, str] | None) -> str:
    source = os.environ if env is None else env
    return str(source.get(name) or "").strip()


def executable_override(runtime_id: str, env: dict[str, str] | None = None) -> str | None:
    """Devuelve el override de ejecutable de una env var para el runtime, o ``None`` si no está definido."""
    name = RUNTIME_COMMAND_ENV.get(runtime_id)
    value = _env_value(name, env) if name else ""
    return value or None


def resolve_executable(installation: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Resuelve la ruta del ejecutable aplicando la env var solo como override sobre lo persistido.

    Devuelve ``{path, source}`` donde ``source`` es ``environment_override`` (ganó la env var),
    ``persisted`` (se usó ``executable_path`` de la base) o ``unset`` (ni override ni valor guardado).
    """
    override = executable_override(str(installation.get("runtimeId") or ""), env)
    if override:
        return {"path": override, "source": "environment_override"}
    persisted = installation.get("executablePath")
    return {"path": persisted, "source": "persisted" if persisted else "unset"}


def resolve_default_runtime(preferences: dict[str, Any], env: dict[str, str] | None = None) -> str | None:
    """Resuelve el runtime predeterminado: env var ``AIDO_DEFAULT_RUNTIME`` como override, si no la preferencia.

    La configuración normal es ``preferences['defaultRuntime']`` (sembrada como CLI); la env var solo
    sobrescribe puntualmente.
    """
    override = _env_value(DEFAULT_RUNTIME_ENV, env)
    if override:
        return override
    return preferences.get("defaultRuntime")


def is_cli_runtime(runtime_id: str) -> bool:
    """Indica si el runtime es un CLI ejecutable (codex_cli/claude_code_cli/openhands/swe_agent)."""
    return runtime_id in CLI_RUNTIME_IDS
