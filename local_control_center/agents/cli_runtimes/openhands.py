"""Adaptador del runtime OpenHands: traduce una request al argv de `openhands` headless.

Resuelve el binario por env vars (AIDO_OPENHANDS_COMMAND / OPENHANDS_CLI_PATH) y arma el comando
en modo --headless --json para captura estructurada de salida. Exige que el workspace sea un
worktree Git porque el agente opera sobre el repositorio. Ejecución y registro se heredan de CliRuntime.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest


class OpenHandsRuntime(CliRuntime):
    """Runtime CLI para OpenHands en modo headless sobre un worktree Git."""

    runtime_id = "openhands"
    display_name = "OpenHands"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable
            or os.environ.get("AIDO_OPENHANDS_COMMAND")
            or os.environ.get("OPENHANDS_CLI_PATH", "openhands"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Arma el argv headless de OpenHands con el prompt pasado vía -t.

        Raises:
            ValueError: si el workspace no está registrado, no existe o no es un worktree Git.
        """
        workspace = self._validate_workspace(request)
        self._validate_git_workspace(workspace)
        self._validate_safe_args(request)
        return [self.executable, "--headless", "--json", *request.extra_args, "-t", request.prompt]
