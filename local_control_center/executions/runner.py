"""Runner de una operación en proceso OS contenido, sin servidor HTTP ni worker interno.

Reutiliza los handlers registrados, valida la lease antes de cada resultado y conserva la
autenticación interna del adaptador. Nunca recibe prompts ni credenciales por la línea de comandos.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
from pathlib import Path
from typing import get_type_hints

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import TypeAdapter, ValidationError

from local_control_center.control_plane.runtime import ControlCenterRuntime
from local_control_center.host_resources.repository import ResourceRepository
from local_control_center.jobs_approvals.repository import StaleWorkerFenceError
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.shared.serialization import json_loads

from .inputs import OperationInputStore
from .repository import ExecutionRepository
from .workloads import operation_workload


def run_registered_operation(
    platform: ControlCenterRuntime, execution_id: str, *, owner_id: str, fencing_token: int
):
    """Invoca el handler original en una conexión propia, fuera de cualquier transacción."""
    with platform.operation_connection() as connection:
        repository = ExecutionRepository(connection)
        if not repository.start(execution_id, owner_id=owner_id, fencing_token=fencing_token):
            return repository.get(execution_id)
        row = connection.execute(
            "SELECT * FROM operational_executions WHERE id=?", (execution_id,)
        ).fetchone()
        try:
            legacy = row["operation"].startswith("legacy_job:")
            if not legacy:
                spec, handler = platform.execution_handlers[row["operation"]]
                reference = json_loads(row["arguments_json"], {})
                payload = OperationInputStore(platform.db_path).get(reference.get("sealedInput", ""))
                if operation_workload(connection, spec, payload) != row["workload_class"]:
                    raise ValueError("El perfil de recursos no coincide con la operación registrada.")
                arguments = _arguments(handler, payload, platform)
            lease = ResourceRepository(connection).active_lease_for_execution(execution_id)
            if lease is None:
                raise ValueError("La ejecución no posee una reserva vigente.")
            with execution_scope(
                ProcessExecutionContext(
                    db_path=platform.db_path,
                    execution_id=execution_id,
                    project_id=row["project_id"],
                    resource_lease_id=lease.id,
                    connection=connection,
                    worker_id=owner_id,
                    fencing_token=fencing_token,
                    in_job_runner=True,
                )
            ):
                result = (
                    _run_legacy_job(platform, connection, row, owner_id) if legacy else handler(**arguments)
                )
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                if not legacy and spec.result_model is not None:
                    adapter = TypeAdapter(spec.result_model)
                    result = adapter.dump_python(adapter.validate_python(result), mode="json", by_alias=True)
            return repository.finish(
                execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                status="completed",
                result=jsonable_encoder(result),
            )
        except ValidationError as error:
            return repository.finish(
                execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                status="failed",
                reason="Invalid durable execution contract.",
                result_status_code=500,
                result={
                    "detail": [
                        {"loc": list(item["loc"]), "type": item["type"]}
                        for item in error.errors(include_input=False, include_context=False)
                    ]
                },
            )
        except HTTPException as error:
            return repository.finish(
                execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                status="blocked",
                result={"detail": error.detail},
                reason=str(error.detail),
                result_status_code=error.status_code,
            )
        except StaleWorkerFenceError:
            raise
        except Exception as error:
            from local_control_center.jobs_approvals.worker import JobExecutionUnavailable

            failure_result = None
            if isinstance(error, JobExecutionUnavailable):
                failure_result = {
                    "summary": error.summary,
                    "metadata": {"status": error.status, **error.metadata},
                }
            return repository.finish(
                execution_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                status="failed",
                result=failure_result,
                reason=str(error),
                result_status_code=500,
            )


def _run_legacy_job(platform, connection, row, owner_id):
    from local_control_center.jobs_approvals.repository import JobsRepository
    from local_control_center.jobs_approvals.worker import (
        THREAD_PRODUCT_LOOP_JOB_KIND,
        THREAD_RESEARCH_JOB_KIND,
        execute_job,
    )

    job = JobsRepository(connection).get_job(row["job_id"])
    if (
        job["kind"] not in {THREAD_PRODUCT_LOOP_JOB_KIND, THREAD_RESEARCH_JOB_KIND}
        or row["operation"] != "legacy_job:" + job["kind"]
    ):
        raise ValueError("Unknown contained legacy operation.")
    return execute_job(job, connection=connection, db_path=platform.db_path, worker_id=owner_id)


def _arguments(handler, payload, platform):
    hints = get_type_hints(handler)
    arguments = {}
    for name, parameter in inspect.signature(handler).parameters.items():
        annotation = hints.get(name, parameter.annotation)
        if annotation is Request:
            # Identidad local efímera del runner autenticado por su lease; no viaja por red ni se persiste.
            arguments[name] = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/internal/execution",
                    "headers": [(b"x-local-control-token", platform.get_handshake()["token"].encode())],
                    "query_string": b"",
                    "client": ("127.0.0.1", 0),
                }
            )
        elif name in payload:
            arguments[name] = TypeAdapter(annotation).validate_python(payload[name])
        elif parameter.default is inspect.Parameter.empty:
            raise ValueError(f"Falta el argumento durable {name}.")
    return arguments


def main() -> int:
    """Ejecuta una sola identidad reclamada; el supervisor del padre gobierna su duración."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--fencing-token", required=True, type=int)
    args = parser.parse_args()
    from contextlib import closing

    from local_control_center.api import create_app
    from local_control_center.shared.db import open_sqlite_connection

    with closing(open_sqlite_connection(args.db)) as connection:
        row = connection.execute(
            "SELECT cwd FROM operational_executions WHERE id=?", (args.execution_id,)
        ).fetchone()
        ExecutionRepository(connection).require_fence(
            args.execution_id, owner_id=args.owner_id, fencing_token=args.fencing_token
        )
    platform = ControlCenterRuntime(cwd=Path(row[0]), db_path=Path(args.db))
    try:
        create_app(runtime=platform, static_dir=None)
        result = run_registered_operation(
            platform, args.execution_id, owner_id=args.owner_id, fencing_token=args.fencing_token
        )
        return 0 if result["status"] == "completed" else 1
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
