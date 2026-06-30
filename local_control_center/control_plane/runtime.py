"""Aprovisiona el runtime del proceso: conexion SQLite, esquema, token y proyecto runtime.

Concentra el bootstrap del proceso FastAPI: resuelve cwd y ruta de la base, abre la
conexion SQLite perezosamente, inicializa el esquema de plataforma y emite el token de
handshake loopback. Tambien garantiza que exista el proyecto que representa el cwd actual.
Las lecturas/escrituras de dominio viven en los repositorios de cada slice, no aqui.

@author Rodrigo Mason
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.settings import default_cwd, default_db_path


class ControlCenterRuntime:
    """Runtime boundary for the FastAPI app.

    This object owns process bootstrap concerns only: cwd, SQLite connection,
    schema initialization, loopback token, and runtime project creation.
    Domain reads/writes stay in slice repositories mounted by routers.
    """

    def __init__(self, cwd: str | Path | None = None, db_path: str | Path | None = None):
        self.cwd = Path(cwd) if cwd is not None else default_cwd()
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self._connection: sqlite3.Connection | None = None
        self._token = secrets.token_urlsafe(32)

    @property
    def connection(self) -> sqlite3.Connection:
        """Conexion SQLite abierta de forma perezosa y cacheada para el resto del proceso."""
        if self._connection is None:
            self._connection = open_sqlite_connection(self.db_path)
        return self._connection

    def close(self) -> None:
        """Cierra la conexion cacheada si existe; idempotente tras el primer cierre."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def init(self) -> None:
        """Crea el directorio de la base y aplica el esquema de plataforma sobre la conexion."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        initialize_platform_schema(self.connection)
        from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed

        bootstrap_base_team_if_needed(self.connection)

    def get_handshake(self) -> dict[str, Any]:
        """Devuelve el token de escritura por sesion y la marca de acceso solo-loopback."""
        return {"token": self._token, "loopbackOnly": True}

    def ensure_runtime_project(self) -> dict[str, Any]:
        """Devuelve el proyecto que representa el cwd, creandolo y auditandolo si no existia.

        Invariante: registra el evento de auditoria ``project.create`` solo en la creacion
        real (cuando el repositorio reporta ``_created``), no al reusar uno existente.
        """
        projects = ProjectsRepository(self.connection)
        existing = projects.get_project_by_path(self.cwd)
        if existing:
            return existing
        project = projects.create_project(
            name=self.cwd.name or "Local Control Center",
            path=self.cwd,
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        created = bool(project.pop("_created", False))
        if created:
            EventBus(self.connection).record_audit(
                project_id=project["id"],
                action="project.create",
                target=project["id"],
                payload={"path": project["path"], "source": "runtime"},
            )
        return project
