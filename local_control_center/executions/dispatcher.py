"""Despacha una ejecución a un proceso Python contenido y recupera su resultado durable.

La API no invoca este módulo. El worker mantiene su heartbeat y el supervisor puede terminar
también una llamada remota bloqueada, sin depender de cancelar un hilo Python.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

from local_control_center.process_supervision.context import CURRENT_EXECUTION
from local_control_center.process_supervision.service import run_supervised_capture

from .models import TERMINAL_STATUSES
from .repository import ExecutionRepository


def dispatch_execution(job: dict, *, connection, db_path):
    """Ejecuta sólo un job reclamado con fence y reserva propagados por el worker."""
    context = CURRENT_EXECUTION.get()
    if context is None or not context.worker_id or context.fencing_token is None:
        raise RuntimeError("Las ejecuciones durables requieren liderazgo verificable.")
    repository = ExecutionRepository(connection)
    execution_id = job["id"]
    current = repository.get(execution_id)
    if current["status"] not in TERMINAL_STATUSES:
        result = run_supervised_capture(
            [
                sys.executable,
                "-m",
                "local_control_center.executions.runner",
                "--db",
                str(db_path),
                "--execution-id",
                execution_id,
                "--owner-id",
                context.worker_id,
                "--fencing-token",
                str(context.fencing_token),
            ],
            cwd=Path(__file__).resolve().parents[2],
            timeout_seconds=900,
            workload_class=current["workloadClass"],
        )
        current = repository.get(execution_id)
        if current["status"] not in TERMINAL_STATUSES:
            current = repository.finish(
                execution_id,
                owner_id=context.worker_id,
                fencing_token=context.fencing_token,
                status="cancelled" if result["cancelled"] else "failed",
                reason=result["terminationReason"] or "El runner terminó sin resultado durable.",
            )
    legacy_result = (
        current["result"]
        if current["operation"].startswith("legacy_job:") and isinstance(current["result"], dict)
        else {}
    )
    return {
        "summary": current["reason"] or f"Operación {current['operation']}: {current['status']}.",
        "metadata": {
            **legacy_result.get("metadata", {}),
            "executionId": execution_id,
            "status": current["status"],
        },
    }
