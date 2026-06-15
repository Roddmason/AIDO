"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
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
    runtime_id = "claude_code_cli"
    display_name = "Claude Code CLI"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable or os.environ.get("AIDO_CLAUDE_COMMAND") or os.environ.get("CLAUDE_CODE_CLI_PATH", "claude"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        profile = CLAUDE_PROFILES.get(request.profile or "", {})
        model = request.model or profile.get("model")
        command = [self.executable, "--print", "--permission-mode", "acceptEdits", "--add-dir", str(workspace)]
        if model:
            command.extend(["--model", str(model)])
        command.extend(request.extra_args)
        command.append(request.prompt)
        return command
