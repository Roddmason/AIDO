"""Adapta handlers productivos existentes a aceptación durable sin reescribir su dominio.

El decorador declara operaciones explícitas. La autenticación ocurre antes de encolar y el
handler original sólo se registra para el worker; nunca se invoca desde el wrapper HTTP.

@author Rodrigo Mason
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, get_type_hints

from fastapi import APIRouter, HTTPException, Request
from fastapi.datastructures import DefaultPlaceholder
from fastapi.encoders import jsonable_encoder

from local_control_center.credentials.backends import CredentialBackendError
from local_control_center.host_resources.models import WorkloadClass
from local_control_center.shared.redaction import redact_secrets

from .inputs import OperationInputStore
from .models import ExecutionAccepted
from .repository import ExecutionRepository
from .workloads import operation_workload


@dataclass(frozen=True)
class OperationSpec:
    """Identifica una operación registrada por código, nunca una ruta importada desde input."""

    name: str
    workload_class: WorkloadClass
    result_model: Any = None


def queued_operation(name: str, *, workload_class: WorkloadClass):
    """Declara un handler existente como trabajo durable ejecutable sólo por el worker."""

    def decorate(handler):
        handler._aido_operation = OperationSpec(name, workload_class)
        return handler

    return decorate


class ExecutionRouter(APIRouter):
    """Conserva validación FastAPI y publica 202 para handlers explícitamente marcados."""

    def __init__(self, *, platform: Any, require_write: Callable[[Request], None], **kwargs):
        super().__init__(**kwargs)
        self.platform = platform
        self.require_write = require_write
        if not hasattr(platform, "execution_handlers"):
            platform.execution_handlers = {}

    def add_api_route(self, path: str, endpoint: Callable, **kwargs) -> None:
        """Registra el original para despacho y un wrapper rápido como endpoint HTTP."""
        spec = getattr(endpoint, "_aido_operation", None)
        if spec is None:
            super().add_api_route(path, endpoint, **kwargs)
            return
        hints = get_type_hints(endpoint)
        result_model = kwargs.get("response_model")
        if isinstance(result_model, DefaultPlaceholder):
            result_model = hints.get("return")
        spec = replace(spec, result_model=result_model)
        self.platform.execution_handlers[spec.name] = (spec, endpoint)
        status_code = kwargs.get("status_code") or 200
        signature = inspect.signature(endpoint)

        @wraps(endpoint)
        async def enqueue(*args, **values):
            arguments = signature.bind(*args, **values).arguments
            request = next((value for value in arguments.values() if isinstance(value, Request)), None)
            if request is None:
                raise HTTPException(403, "Authenticated HTTP request required for enqueue.")
            self.require_write(request)
            return enqueue_registered_operation(
                self.platform,
                spec,
                {key: value for key, value in arguments.items() if value is not request},
                result_status_code=status_code,
            )

        # wraps conserva strings forward-ref del módulo original; resolverlos evita contexto incorrecto.
        enqueue.__signature__ = signature.replace(
            parameters=[
                value.replace(annotation=hints.get(name, value.annotation))
                for name, value in signature.parameters.items()
            ],
            return_annotation=ExecutionAccepted,
        )
        enqueue._aido_operation = spec
        kwargs.update(status_code=202, response_model=ExecutionAccepted)
        super().add_api_route(path, enqueue, **kwargs)


def enqueue_registered_operation(
    platform: Any,
    spec: OperationSpec,
    arguments: dict[str, Any],
    *,
    result_status_code: int = 200,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Encola una operación registrada tras la autenticación del router, sin ejecutar su handler."""
    if spec.name not in platform.execution_handlers:
        raise ValueError("Operation must be registered by application code.")
    payload = jsonable_encoder(arguments)
    _reject_credentials(payload)
    project_id = project_id or payload.get("project_id") or payload.get("projectId")
    if isinstance(payload.get("body"), dict):
        project_id = project_id or payload["body"].get("projectId")
    workspace_id = payload.get("workspace_id") or (
        payload.get("body", {}).get("workspaceId") if isinstance(payload.get("body"), dict) else None
    )
    if workspace_id:
        workspace = platform.connection.execute(
            "SELECT project_id FROM workspaces WHERE id=?", (workspace_id,)
        ).fetchone()
        if workspace is None:
            raise HTTPException(404, "Workspace not found.")
        if project_id and project_id != workspace["project_id"]:
            raise HTTPException(422, "Project does not own the requested workspace.")
        project_id = workspace["project_id"]
    inputs = OperationInputStore(platform.db_path)
    try:
        locator = inputs.put(payload)
    except CredentialBackendError as error:
        raise HTTPException(
            503, "configuration_required: secure operation input storage is unavailable."
        ) from error
    try:
        return ExecutionRepository(platform.connection).enqueue(
            operation=spec.name,
            workload_class=operation_workload(platform.connection, spec, payload),
            arguments={"sealedInput": locator},
            project_id=project_id,
            cwd=str(platform.cwd),
            result_status_code=result_status_code,
        )
    except Exception:
        inputs.remove(locator)
        raise


def _reject_credentials(value: Any, key: str = "") -> None:
    if isinstance(value, dict):
        for name, item in value.items():
            _reject_credentials(item, str(name))
    elif isinstance(value, list):
        for item in value:
            _reject_credentials(item, key)
    elif isinstance(value, str) and (
        redact_secrets(value) != value
        or (
            key.lower() in {"api_key", "apikey", "password", "authorization", "access_token", "refresh_token"}
            and value
        )
    ):
        raise HTTPException(422, "Do not enqueue credentials; use the local credential store.")
