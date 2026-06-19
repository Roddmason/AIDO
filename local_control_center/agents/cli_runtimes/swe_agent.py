"""Adaptador del runtime SWE-agent: traduce una request al argv de `sweagent run`.

Resuelve el binario por env vars (AIDO_SWE_AGENT_COMMAND / SWE_AGENT_CLI_PATH) y arma el comando
pasando el repo y el enunciado del problema como parámetros nombrados, con aplicación local del
parche. Exige que el workspace sea un worktree Git. Ejecución y registro se heredan de CliRuntime.
"""

from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest


class SweAgentRuntime(CliRuntime):
    """Runtime CLI para SWE-agent, que resuelve el problema sobre un worktree Git y aplica el parche."""

    runtime_id = "swe_agent"
    display_name = "SWE-agent"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable
            or os.environ.get("AIDO_SWE_AGENT_COMMAND")
            or os.environ.get("SWE_AGENT_CLI_PATH", "sweagent"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Arma el argv de `sweagent run` con el repo y el enunciado como parámetros nombrados.

        Raises:
            ValueError: si el workspace no está registrado, no existe o no es un worktree Git.
        """
        workspace = self._validate_workspace(request)
        self._validate_git_workspace(workspace)
        self._validate_safe_args(request)
        return [
            self.executable,
            "run",
            f"--env.repo.path={workspace}",
            f"--problem_statement.text={request.prompt}",
            "--actions.apply_patch_locally",
            *request.extra_args,
        ]
