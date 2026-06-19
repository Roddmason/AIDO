"""Adaptador del runtime Claude Code CLI: traduce una request al argv de `claude`.

Resuelve el binario por env vars (AIDO_CLAUDE_COMMAND / CLAUDE_CODE_CLI_PATH), mapea perfiles
de agente a modelo y arma el comando en modo no interactivo (--print) con edición aceptada y el
workspace acotado vía --add-dir. La ejecución segura y el registro los hereda de CliRuntime.
"""

from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest

CLAUDE_PROFILES = {
    "claude_sonnet_developer": {"model": "sonnet", "effort": "medium"},
    "claude_sonnet_qa": {"model": "sonnet", "effort": "medium"},
    "claude_opus_planner": {"model": "opus", "effort": "high"},
    "claude_opus_xhigh_architect": {"model": "opus", "effort": "xhigh"},
    "claude_opus_max_requires_approval": {"model": "opus", "effort": "max"},
    "claude_opusplan_if_supported": {"model": "opus", "effort": "high"},
}


class ClaudeCodeCliRuntime(CliRuntime):
    """Runtime CLI para Claude Code, con perfiles que fijan modelo y esfuerzo por rol."""

    runtime_id = "claude_code_cli"
    display_name = "Claude Code CLI"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable
            or os.environ.get("AIDO_CLAUDE_COMMAND")
            or os.environ.get("CLAUDE_CODE_CLI_PATH", "claude"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Arma el argv de `claude --print` para el workspace validado; el prompt va al final.

        El modelo explícito de la request gana sobre el del perfil; los extra_args se interponen
        antes del prompt para no romper su posición posicional.
        """
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        profile = CLAUDE_PROFILES.get(request.profile or "", {})
        model = request.model or profile.get("model")
        command = [
            self.executable,
            "--print",
            "--permission-mode",
            "acceptEdits",
            "--add-dir",
            str(workspace),
        ]
        if model:
            command.extend(["--model", str(model)])
        command.extend(request.extra_args)
        command.append(request.prompt)
        return command
