"""Readiness con sondeo explícito y host controlado para pruebas del contrato de proveedores.

No sustituye autenticación, policy ni compatibilidad; los tests inyectan sus probes donde corresponde.
Las lecturas HTTP sin probes se verifican con el servicio normal en test_durable_executions.

@author Rodrigo Mason
"""

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.host_resources.models import ResourceSnapshot
from local_control_center.host_resources.repository import ResourceRepository


class ProbedRuntimeStatusService(RuntimeStatusService):
    """Activa sólo el modo de sondeo y una muestra de recursos explícitamente de test."""

    def __init__(self, connection):
        ResourceRepository(connection).record_sample(ResourceSnapshot.test_snapshot())
        super().__init__(connection, allow_probes=True)
