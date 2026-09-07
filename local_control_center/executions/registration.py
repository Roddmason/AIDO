"""Register the existing operation handler without constructing an HTTP application.

Only code-owned module names are imported; durable input cannot select a Python module.
"""

import importlib
import secrets

from fastapi import HTTPException, Request

_DOMAINS = {
    "agents": "agents.api",
    "catalog": "agents.provider_catalog_api",
    "cli": "agents.cli_session_stream_api",
    "git": "git_workspace.api",
    "integrations": "integrations.api",
    "models": "agents.model_gateway_api",
    "ollama": "ollama.api",
    "product_loop": "product_loop.api",
    "projects": "projects.api",
    "remediations": "remediations.api",
    "retrieval": "memory_retrieval.api",
    "workflows": "workflows.api",
    "workspaces": "workspaces_projects.api",
}


def register_operation(platform, operation: str) -> None:
    """Retain original handler, validation and authentication; omit unrelated HTTP wiring."""
    if operation.startswith("legacy_job:"):
        return  # The existing legacy dispatcher validates the exact job kind.
    module = _DOMAINS.get(operation.partition(".")[0])
    if module is None:
        raise ValueError("Operation is not registered by application code.")

    def require_write(request: Request) -> None:
        expected = platform.get_handshake()["token"]
        provided = request.headers.get("X-Local-Control-Token")
        if not provided or not secrets.compare_digest(str(provided), str(expected)):
            raise HTTPException(403, "A valid loopback write token is required.")

    previous = getattr(platform, "_register_operations_only", False)
    platform.execution_handlers = getattr(platform, "execution_handlers", {})
    platform._register_operations_only = True
    try:
        importlib.import_module(f"local_control_center.{module}").create_router(
            platform=platform, require_write=require_write
        )
    finally:
        platform._register_operations_only = previous
    if operation not in platform.execution_handlers:
        raise ValueError("Operation is not registered by application code.")
