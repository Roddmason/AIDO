"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest


class SweAgentRuntime(CliRuntime):
    runtime_id = "swe_agent"
    display_name = "SWE-agent"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable or os.environ.get("AIDO_SWE_AGENT_COMMAND") or os.environ.get("SWE_AGENT_CLI_PATH", "sweagent"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
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
