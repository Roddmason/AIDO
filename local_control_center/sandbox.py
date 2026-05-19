from __future__ import annotations

import shutil
import subprocess
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

    def run_low_risk(self, command: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        decision = self.assess(command)
        if not decision.allowed or decision.mode != "restricted-subprocess":
            raise PermissionError(decision.reason)
        return subprocess.run(
            command,
            cwd=self.workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
