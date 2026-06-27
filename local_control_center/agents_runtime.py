"""Seam opcional para el OpenAI Agents SDK, encerrado tras la capa de aprobacion local.

Define `GatedAgentsPlanner`: el SDK solo puede *proponer* acciones que quedan
registradas como action requests pendientes de grant; nunca ejecuta comandos.
Es un punto de integracion intencionalmente no cableado en el primer corte Python.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .jobs_approvals.repository import JobsRepository


@dataclass
class PlannerResult:
    """Resultado de una propuesta del planner: si el SDK esta activo y la accion registrada."""

    enabled: bool
    summary: str
    action_request_id: str | None = None


class GatedAgentsPlanner:
    """Thin optional integration point for the OpenAI Agents SDK.

    The first Python cut deliberately keeps the SDK behind the local policy and
    approval layer. It may propose actions, but it does not execute commands.
    """

    def __init__(self, *, jobs: JobsRepository):
        self.jobs = jobs

    def available(self) -> bool:
        """Indica si el SDK puede usarse: requiere `OPENAI_API_KEY` y el paquete `agents` importable."""
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import agents  # noqa: F401
        except Exception:
            return False
        return True

    def propose_action(self, *, project_id: str, job_id: str, prompt: str) -> PlannerResult:
        """Registra la accion propuesta como action request pendiente de aprobacion, sin ejecutarla.

        Si el SDK no esta disponible devuelve un resultado deshabilitado; en caso contrario crea
        un action request de riesgo medio en `jobs` y devuelve su id para el flujo de grant.
        """
        if not self.available():
            return PlannerResult(
                enabled=False,
                summary="OpenAI Agents SDK is disabled until OPENAI_API_KEY and openai-agents are available.",
            )
        action = self.jobs.create_action_request(
            project_id=project_id,
            job_id=job_id,
            action_type="agent.proposed_action",
            risk_level="medium",
            command="agents-sdk-plan",
            payload={"prompt": prompt},
            reason="Agents SDK proposed an action that requires local policy approval.",
        )
        return PlannerResult(
            enabled=True,
            summary="Agents SDK proposal recorded behind the approval gate.",
            action_request_id=action["id"],
        )
