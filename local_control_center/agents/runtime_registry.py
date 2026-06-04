from __future__ import annotations

import sqlite3
from typing import Any

from .cli_runtimes.claude_code_cli import ClaudeCodeCliRuntime
from .cli_runtimes.codex_cli import CodexCliRuntime
from .cli_runtimes.manual import ManualRuntime
from .cli_runtimes.openhands import OpenHandsRuntime
from .cli_runtimes.swe_agent import SweAgentRuntime


def runtime_for(runtime_id: str, *, connection: sqlite3.Connection | None = None):
    runtimes = {
        "codex_cli": CodexCliRuntime(connection=connection),
        "claude_code_cli": ClaudeCodeCliRuntime(connection=connection),
        "openhands": OpenHandsRuntime(connection=connection),
        "swe_agent": SweAgentRuntime(connection=connection),
        "manual": ManualRuntime(connection=connection),
    }
    if runtime_id not in runtimes:
        raise KeyError(f"Runtime not found: {runtime_id}")
    return runtimes[runtime_id]


class RuntimeRegistry:
    def list_runtimes(self) -> list[dict[str, Any]]:
        result = []
        for runtime_id in ["codex_cli", "claude_code_cli", "openhands", "swe_agent", "manual"]:
            runtime = runtime_for(runtime_id)
            detection = runtime.detect().model_dump(by_alias=True)
            result.append({"id": runtime_id, "runtime": runtime_id, **detection})
        return result

    def detect(self, runtime_id: str) -> dict[str, Any]:
        return runtime_for(runtime_id).detect().model_dump(by_alias=True)

    def health_check(self, runtime_id: str) -> dict[str, Any]:
        return runtime_for(runtime_id).health_check().model_dump(by_alias=True)
