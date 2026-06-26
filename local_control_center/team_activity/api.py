"""HTTP route for the Team Activity board: a read-only per-project aggregation.

Exposes ``GET /api/v1/projects/{project_id}/team-activity`` which composes the activity
entries from ``build_team_activity`` over ``platform.connection``. The route is a read, so it
is open (no ``require_write`` gate), consistent with the other overview/aggregate reads.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request

from .models import TeamActivityResponse
from .service import build_team_activity


def create_router(*, platform: Any, require_write: Callable[[Request], None]) -> APIRouter:
    """Build the Team Activity router bound to ``platform``'s SQLite connection.

    ``require_write`` is accepted to match the slice router factory signature but is unused:
    the only route is a read and stays open, like the product-loop and overview reads.
    """
    _ = require_write
    router = APIRouter()

    @router.get(
        "/api/v1/projects/{project_id}/team-activity",
        response_model=TeamActivityResponse,
    )
    async def team_activity(project_id: str) -> dict[str, Any]:
        """Return the project's agent activity entries, in-flight work first."""
        return build_team_activity(connection=platform.connection, project_id=project_id)

    return router
