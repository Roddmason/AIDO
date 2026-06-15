"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import sqlite3

from .base import CliRuntime, RuntimeRequest, RuntimeResult


class ManualRuntime(CliRuntime):
    runtime_id = "manual"
    display_name = "Manual Runtime"

    def __init__(
        self,
        *,
        executable: str = "manual",
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(executable=executable, connection=connection)

    def build_command(self, request: RuntimeRequest) -> list[str]:
        self._validate_workspace(request)
        self._validate_safe_args(request)
        return ["manual", request.prompt]

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        result = RuntimeResult(
            runtime=self.runtime_id,
            status="created",
            command=self.build_command(request),
            stdout="Manual operator session created",
        )
        self._record_result(request, result)
        return result
