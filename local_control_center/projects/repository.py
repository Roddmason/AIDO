"""Acceso SQLite al catálogo de proyectos, proveedores, equipos y agentes.

Mapea filas ``sqlite3.Row`` a dicts ``camelCase`` para la API y emite los ``INSERT``
del catálogo. La conexión recibida opera en autocommit (``isolation_level=None``, ver
``shared/db.py``): cada ``execute`` confirma su propia transacción de forma independiente,
no se agrupan escrituras atómicamente. ``create_project`` se confirma con su único
``INSERT`` y es idempotente por ruta: si el proyecto ya existe no inserta y lo devuelve
con ``_created=False``.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

PROJECT_TEMPLATES = [
    {"id": "react-vite", "name": "React + Vite", "kind": "frontend"},
    {"id": "node-cli", "name": "Node CLI", "kind": "tooling"},
    {"id": "python-fastapi", "name": "Python FastAPI", "kind": "backend"},
    {"id": "other", "name": "Other", "kind": "generic"},
]


def row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``projects`` al dict ``camelCase`` que expone la API."""
    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["path"],
        "templateId": row["template_id"],
        "source": row["source"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_provider(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``providers`` al dict ``camelCase`` (deserializa JSON con default)."""
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "capabilities": json_loads(row["capabilities"], []),
        "models": json_loads(row["models"], []),
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "updatedAt": row["updated_at"],
    }


def row_to_team(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``teams`` al dict ``camelCase`` que expone la API."""
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "version": row["version"],
        "capabilities": json_loads(row["capabilities"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_agent(row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de ``agents`` al dict ``camelCase`` que expone la API."""
    return {
        "id": row["id"],
        "teamId": row["team_id"],
        "name": row["name"],
        "role": row["role"],
        "kind": row["kind"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "capabilities": json_loads(row["capabilities"], []),
        "permissions": json_loads(row["permissions"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class ProjectsRepository:
    """Repositorio del catálogo de proyectos sobre una conexión SQLite en autocommit."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def seed_providers(self) -> None:
        """Inserta los proveedores por defecto; ``INSERT OR IGNORE`` lo hace idempotente."""
        providers = [
            ("codex", "agent", "Codex", ["code", "review"], ["gpt-5", "gpt-5.4"], "available"),
            ("claudecode", "agent", "Claude Code", ["code", "analysis"], ["sonnet"], "available"),
            ("gemini", "research", "Gemini", ["research"], ["gemini-pro"], "available"),
            ("openai-agents", "agent", "OpenAI Agents SDK", ["planning", "tools"], ["gpt-5"], "disabled"),
        ]
        timestamp = utc_now()
        for provider in providers:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO providers
                    (id, kind, label, capabilities, models, status, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider[0],
                    provider[1],
                    provider[2],
                    json_dumps(provider[3]),
                    json_dumps(provider[4]),
                    provider[5],
                    json_dumps({}),
                    timestamp,
                ),
            )

    def get_project_by_path(self, path: str | Path) -> dict[str, Any] | None:
        """Busca un proyecto por su ruta normalizada; ``None`` si no existe."""
        row = self.connection.execute("SELECT * FROM projects WHERE path = ?", (str(Path(path)),)).fetchone()
        return row_to_project(row) if row else None

    def find_project(self, ref: str | Path) -> dict[str, Any] | None:
        """Resuelve un proyecto por id y, si no hay coincidencia, por ruta; ``None`` si falla ambas."""
        ref_text = str(ref)
        row = self.connection.execute("SELECT * FROM projects WHERE id = ?", (ref_text,)).fetchone()
        if row:
            return row_to_project(row)
        return self.get_project_by_path(ref_text)

    def create_project(
        self,
        *,
        name: str,
        path: str | Path,
        template_id: str = "other",
        create_directory: bool = True,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea el proyecto (opcionalmente su carpeta) o devuelve el existente para esa ruta.

        Idempotente por ruta: si ya hay proyecto en ``path`` no inserta y lo retorna con
        ``_created=False``; al crear, el ``INSERT`` se autocommitea y retorna ``_created=True``.
        """
        project_path = Path(path)
        if create_directory:
            project_path.mkdir(parents=True, exist_ok=True)
        existing = self.get_project_by_path(project_path)
        if existing:
            return {**existing, "_created": False}
        timestamp = utc_now()
        project_id = f"project-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO projects
                (id, name, path, template_id, source, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                str(project_path),
                template_id or "other",
                source,
                "active",
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        return {**self.get_project(project_id), "_created": True}

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Devuelve el proyecto por id.

        Raises:
            KeyError: si no existe un proyecto con ese id.
        """
        row = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise KeyError(f"Project not found: {project_id}")
        return row_to_project(row)

    def list_projects(self) -> list[dict[str, Any]]:
        """Lista todos los proyectos ordenados por fecha de creación ascendente."""
        rows = self.connection.execute("SELECT * FROM projects ORDER BY created_at ASC").fetchall()
        return [row_to_project(row) for row in rows]

    def list_project_templates(self) -> list[dict[str, Any]]:
        """Devuelve copias de las plantillas estáticas para no exponer la lista mutable interna."""
        return [template.copy() for template in PROJECT_TEMPLATES]

    def list_providers(self) -> list[dict[str, Any]]:
        """Lista los proveedores registrados ordenados por id."""
        rows = self.connection.execute("SELECT * FROM providers ORDER BY id ASC").fetchall()
        return [row_to_provider(row) for row in rows]

    def list_teams(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los equipos, filtrados por proyecto si se indica ``project_id``."""
        if project_id:
            rows = self.connection.execute(
                "SELECT * FROM teams WHERE project_id = ? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM teams ORDER BY created_at ASC").fetchall()
        return [row_to_team(row) for row in rows]

    def list_agents(self, team_id: str | None = None) -> list[dict[str, Any]]:
        """Lista los agentes del catálogo, acotados a un equipo si se indica ``team_id``."""
        if team_id:
            rows = self.connection.execute(
                "SELECT * FROM agents WHERE team_id = ? ORDER BY created_at ASC",
                (team_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
        return [row_to_agent(row) for row in rows]
