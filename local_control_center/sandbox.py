from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


DANGEROUS_TOKENS = {
    "rm",
    "del",
    "format",
    "shutdown",
    "reg",
    "powershell",
    "curl",
    "wget",
    "ssh",
    "git",
    "npm",
    "pip",
}


@dataclass(frozen=True)
class SandboxDecision:
    allowed: bool
    mode: str
    reason: str


class WindowsSandbox:
    def __init__(self, *, workspace: str | Path):
        self.workspace = Path(workspace)

    def docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def assess(self, command: list[str]) -> SandboxDecision:
        lowered = {part.lower() for part in command}
        if self.docker_available():
            return SandboxDecision(True, "docker", "Docker Desktop is available for isolated execution.")
        if lowered & DANGEROUS_TOKENS:
            return SandboxDecision(False, "restricted-subprocess", "Command requires Docker or explicit approval.")
        return SandboxDecision(True, "restricted-subprocess", "Only low-risk subprocess execution is available.")

    def run_low_risk(self, command: list[str], *, timeout: int = 30) -> None:
        decision = self.assess(command)
        raise PermissionError(
            f"{decision.reason} Execution must go through "
            "ToolBroker -> PolicyEngine -> Approval/Grant -> RuntimeAdapter -> Evidence."
        )
