from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest


CODEX_PROFILES = {
    "codex_gpt55_developer": {"model": "gpt-5.5", "effort": "high"},
    "codex_gpt55_reviewer": {"model": "gpt-5.5", "effort": "high"},
    "codex_gpt55_xhigh_architect": {"model": "gpt-5.5", "effort": "xhigh"},
    "codex_mini_analyst": {"model": "mini", "effort": "medium"},
}


class CodexCliRuntime(CliRuntime):
    runtime_id = "codex_cli"
    display_name = "Codex CLI"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable or os.environ.get("AIDO_CODEX_COMMAND") or os.environ.get("CODEX_CLI_PATH", "codex"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        profile = CODEX_PROFILES.get(request.profile or "", {})
        model = request.model or profile.get("model")
        effort = request.effort or profile.get("effort")
        command = [self.executable, "exec", "--sandbox", "workspace-write", "--ask-for-approval", "on-request", "--cd", str(workspace)]
        if model:
            command.extend(["--model", str(model)])
        if effort:
            command.extend(["--model-reasoning-effort", str(effort)])
        command.extend(request.extra_args)
        command.append(request.prompt)
        return command
