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
        from local_control_center.shared.diagnostics import diagnostic_event

        diagnostic_event("dispatcher.started", component="dispatcher")
        from local_control_center.process_supervision.session_client import request_session

        result = (
            request_session(context, "dispatch")
            if context.aggregate_managed_process_id
            else run_supervised_capture(
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
        if job["kind"] == "thread.product_loop.run" and result.get("timedOut"):
            from .timeout_reconciliation import (
                reconcile_product_loop_timeout,
                record_timeout_observation,
                timeout_loop_for_job,
            )

            record_timeout_observation(
                connection,
                execution_id=execution_id,
                attempt_id=context.attempt_id,
                supervision_result=result,
            )
            try:
                loop = timeout_loop_for_job(connection, job)
                reconcile_product_loop_timeout(
                    connection,
                    execution_id=execution_id,
                    expected_loop_id=loop["id"],
                    expected_loop_version=loop["version"],
                    expected_attempt_id=context.attempt_id,
                    supervision_result=result,
                    owner_id=context.worker_id,
                    fencing_token=context.fencing_token,
                )
            except (ValueError, KeyError) as error:
                diagnostic_event(
                    "dispatcher.timeout_reconciliation_deferred", component="dispatcher", reason=str(error)
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
