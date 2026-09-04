"""Publica contratos terminales sin confundir el HTTP 202 con el resultado del worker.

La extensión OpenAPI enlaza cada aceptación con el esquema de ``ExecutionResponse.result``.
Los DTO originales permanecen disponibles para clientes tipados y validación en el runner.

@author Rodrigo Mason
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import TypeAdapter


def install_execution_openapi(app: FastAPI) -> None:
    """Extiende el documento generado, manteniendo respuestas HTTP y resultados separados."""
    original = app.openapi

    def execution_openapi():
        schema = original()
        routes = [
            route
            for route in app.routes
            if isinstance(route, APIRoute) and hasattr(route.endpoint, "_aido_operation")
        ]
        specs = {route.endpoint._aido_operation.name: route.endpoint._aido_operation for route in routes}
        results, definitions = TypeAdapter.json_schemas(
            [
                (name, "serialization", TypeAdapter(spec.result_model))
                for name, spec in specs.items()
                if spec.result_model is not None
            ],
            ref_template="#/components/schemas/{model}",
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for name, definition in definitions.get("$defs", {}).items():
            components.setdefault(name, definition)
        for route in routes:
            spec = route.endpoint._aido_operation
            for method in route.methods:
                operation = schema.get("paths", {}).get(route.path_format, {}).get(method.lower())
                if operation is not None:
                    operation["x-aido-execution"] = {
                        "operation": spec.name,
                        "resultSchema": results.get((spec.name, "serialization"), {}),
                        "statusPath": "/api/v1/executions/{execution_id}",
                    }
        return schema

    app.openapi = execution_openapi
