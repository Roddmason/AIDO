"""Evalua el riesgo de ejecutar comandos del workspace y bloquea la ejecucion directa.

Invariante de seguridad: ninguna ejecucion ocurre desde aqui. `assess` clasifica el
comando (Docker disponible vs. tokens peligrosos vs. bajo riesgo) y `run_low_risk`
siempre lanza `PermissionError`, forzando la cadena ToolBroker -> PolicyEngine ->
Approval/Grant -> RuntimeAdapter -> Evidence como unico camino de ejecucion.

@author Rodrigo Mason
"""

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
    """Veredicto de aislamiento para un comando: si se permite, en que modo y por que."""

    allowed: bool
    mode: str
    reason: str


class WindowsSandbox:
    """Clasifica comandos por riesgo de aislamiento; nunca los ejecuta directamente."""

    def __init__(self, *, workspace: str | Path):
        self.workspace = Path(workspace)

    def docker_available(self) -> bool:
        """Indica si Docker esta en el PATH y puede usarse como sandbox aislado."""
        return shutil.which("docker") is not None

    def assess(self, command: list[str]) -> SandboxDecision:
        """Decide el modo de aislamiento: Docker si esta disponible, si no rechaza tokens peligrosos.

        Devuelve `allowed=False` cuando el comando contiene un token destructivo y no hay Docker;
        de lo contrario lo marca como subproceso de bajo riesgo. No autoriza ejecucion por si misma.
        """
        lowered = {part.lower() for part in command}
        if self.docker_available():
            return SandboxDecision(True, "docker", "Docker Desktop is available for isolated execution.")
        if lowered & DANGEROUS_TOKENS:
            return SandboxDecision(
                False, "restricted-subprocess", "Command requires Docker or explicit approval."
            )
        return SandboxDecision(
            True, "restricted-subprocess", "Only low-risk subprocess execution is available."
        )

    def run_low_risk(self, command: list[str], *, timeout: int = 30) -> None:
        """Rechaza siempre la ejecucion local; reenvia a la cadena de aprobacion/grant.

        Raises:
            PermissionError: incondicionalmente, citando el motivo de la decision y la
                cadena ToolBroker -> PolicyEngine -> Approval/Grant -> RuntimeAdapter -> Evidence.
        """
        decision = self.assess(command)
        raise PermissionError(
            f"{decision.reason} Execution must go through "
            "ToolBroker -> PolicyEngine -> Approval/Grant -> RuntimeAdapter -> Evidence."
        )
