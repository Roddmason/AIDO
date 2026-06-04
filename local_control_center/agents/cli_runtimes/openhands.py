from __future__ import annotations

import os
import sqlite3

from .base import CliRuntime, RuntimeRequest


class OpenHandsRuntime(CliRuntime):
    runtime_id = "openhands"
    display_name = "OpenHands"

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable or os.environ.get("OPENHANDS_CLI_PATH", "openhands"),
            connection=connection,
        )

    def build_command(self, request: RuntimeRequest) -> list[str]:
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        return [self.executable, "run", "--workspace", str(workspace), *request.extra_args, request.prompt]
