"""Adaptador del runtime Codex CLI: traduce una request al argv de `codex exec`.

Resuelve el binario por env vars (AIDO_CODEX_COMMAND / CODEX_CLI_PATH), mapea perfiles a modelo
y esfuerzo de razonamiento, y arma el comando con sandbox acorde al perfil y aprobación no interactiva.
La ejecución bajo el sandbox de subprocesos y el registro del resultado los hereda de CliRuntime.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3

from local_control_center.agents.model_aliases import (
    LEGACY_PROFILE_ALIASES,
    MODEL_ALIASES,
    resolve_model_alias,
)

from .base import CliRuntime, RuntimeRequest

CODEX_PROFILES = {alias: {"alias": alias} for alias in MODEL_ALIASES}
CODEX_PROFILES.update(
    {key: {"alias": value} for key, value in LEGACY_PROFILE_ALIASES.items() if key.startswith("codex_")}
)


class CodexCliRuntime(CliRuntime):
    """Runtime CLI para Codex con intención semántica resuelta desde el catálogo real."""

    runtime_id = "codex_cli"
    display_name = "Codex CLI"
    auth_status_argv = ("login", "status")
    login_hint = "Codex CLI is not logged in; run `codex login` and retry."

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable
            or os.environ.get("AIDO_CODEX_COMMAND")
            or os.environ.get("CODEX_CLI_PATH", "codex"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Arma el argv de `codex exec` para el workspace validado; el prompt va al final.

        Modelo y esfuerzo de la request prevalecen sobre los del perfil y se omiten si no hay valor;
        los extra_args se interponen antes del prompt posicional.
        """
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        profile_name = request.profile or "fast_coding"
        if profile_name not in CODEX_PROFILES:
            raise ValueError("Unknown Codex profile.")
        model = request.model or resolve_model_alias(
            self.connection, provider_id=self.runtime_id, alias=CODEX_PROFILES[profile_name]["alias"]
        )
        if not model or model.startswith("-") or any(character.isspace() for character in model):
            raise ValueError("Invalid catalog model identifier.")
        effort = request.effort
        plan_only = request.role == "product_owner" or request.env_policy.get("permissionProfile") == "plan"
        sandbox_mode = "read-only" if plan_only else "workspace-write"
        command = [
            self.executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            sandbox_mode,
            "--cd",
            str(workspace),
        ]
        if model:
            command.extend(["--model", str(model)])
        if effort:
            command.extend(["--config", f'model_reasoning_effort="{effort}"'])
        command.extend(request.extra_args)
        command.extend(["--", request.prompt])
        return command
