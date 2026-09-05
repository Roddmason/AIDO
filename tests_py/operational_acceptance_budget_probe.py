"""Contraprueba acotada: una reserva prestada no prueba un presupuesto nativo compartido.

Sólo crea procesos dormidos y guarda el resultado observado; exit 1 significa rechazo
de aceptación, nunca éxito por tener tests verdes en otro escenario.
@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from local_control_center.host_resources.governor import HostResourceGovernor
from local_control_center.host_resources.models import ResourceAdmissionRequest, ResourceSnapshot
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ProcessSupervisorService, ResourceWaitError
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.operational_acceptance_support import evidence, native_readback


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="aido-acceptance-budget-"))
    db = directory / "platform.sqlite"
    with closing(open_sqlite_connection(db)) as connection:
        initialize_platform_schema(connection)
        lease = (
            HostResourceGovernor(connection)
            .admit(
                ResourceAdmissionRequest(
                    execution_id="same-lease", owner_id="test", workload_class="qa_light"
                ),
                snapshot=ResourceSnapshot.test_snapshot(),
            )
            .lease
        )
    report = {
        "scope": "three sleeping sibling roots, one borrowed lease; no CPU saturation",
        "leaseCpuPercent": lease.cpu_limit_percent,
        "native": [],
        "status": "FAIL",
    }
    children = []
    with execution_scope(
        ProcessExecutionContext(db_path=db, execution_id="same-lease", resource_lease_id=lease.id)
    ):
        service = ProcessSupervisorService(db_path=db)
        try:
            for _ in range(3):
                try:
                    child = service.start(
                        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                        cwd=directory,
                        workload_class="qa_light",
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except ResourceWaitError:
                    break
                children.append(child)
                report["native"].append(native_readback(child))
            report["independentRootCount"] = len(children)
            report["sumCpuCapsPercent"] = sum(r["cpuRate"] / 100 for r in report["native"])
            report["status"] = "PASS" if len(children) <= 1 else "FAIL"
        finally:
            for child in children:
                if not child.released:
                    service.cancel(child.managed_process_id, reason="acceptance probe cleanup")
            report["remainingRoots"] = sum(c.process.poll() is None for c in children)
            evidence("borrowed-lease-budget", report)
    print(report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
