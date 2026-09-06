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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
        from local_control_center.shared.diagnostics import diagnostic_event, ensure_diagnostics

        ensure_diagnostics()
        diagnostic_event(
            "runtime.startup",
            component="control_plane",
            effectiveConfig={
                "sqliteVersion": sqlite3.sqlite_version,
                "diagnostics": "local_jsonl",
                "remoteExportEnabledByDiagnostics": False,
            },
        )
        self.cwd = Path(cwd) if cwd is not None else default_cwd()
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self._connection: sqlite3.Connection | None = None
        self._operation_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"aido_sqlite_connection_{id(self)}",
            default=None,
        )
        self._token = secrets.token_urlsafe(32)

    @property
    def connection(self) -> sqlite3.Connection:
        """Devuelve la conexión de la operación actual o la conexión de compatibilidad directa.

        Los requests HTTP y las operaciones productivas usan ``operation_connection``. El fallback
        cacheado se conserva para pruebas y llamadas directas existentes que todavía componen
        repositorios a partir de ``runtime.connection`` fuera de un límite operacional.
        """
        operation_connection = self._operation_connection.get()
        if operation_connection is not None:
            return operation_connection
        if self._connection is None:
            self._connection = open_sqlite_connection(self.db_path)
        return self._connection

    @contextmanager
    def operation_connection(self) -> Iterator[sqlite3.Connection]:
        """Vincula una conexión SQLite corta al contexto y garantiza su cierre determinista."""
        existing = self._operation_connection.get()
        if existing is not None:
            yield existing
            return
        connection = open_sqlite_connection(self.db_path)
        token = self._operation_connection.set(connection)
        try:
            yield connection
        finally:
            self._operation_connection.reset(token)
            connection.close()

    def close(self) -> None:
        """Cierra la conexion cacheada si existe; idempotente tras el primer cierre."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def init(self) -> None:
        """Crea el directorio de la base y aplica el esquema de plataforma sobre la conexion."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.operation_connection() as connection:
            initialize_platform_schema(connection)
            from local_control_center.agents.team_bootstrap import bootstrap_base_team_if_needed

            bootstrap_base_team_if_needed(connection)

    def get_handshake(self) -> dict[str, Any]:
        """Devuelve el token de escritura por sesion y la marca de acceso solo-loopback."""
        return {"token": self._token, "loopbackOnly": True}

    def ensure_runtime_project(self) -> dict[str, Any]:
        """Devuelve el proyecto que representa el cwd, creandolo y auditandolo si no existia.

        Invariante: registra el evento de auditoria ``project.create`` solo en la creacion
        real (cuando el repositorio reporta ``_created``), no al reusar uno existente.
        """
        with self.operation_connection() as connection:
            projects = ProjectsRepository(connection)
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
                EventBus(connection).record_audit(
                    project_id=project["id"],
                    action="project.create",
                    target=project["id"],
                    payload={"path": project["path"], "source": "runtime"},
                )
            return project
