"""Discovers SKILL.md skill definitions on disk and syncs them into the catalog.

Parses each skill's required front-matter keys, upserts the skill record, and pins an
immutable content-hashed version row so skill instructions are auditable over time.
The registry is the source of truth for which skills an agent profile may invoke.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

REQUIRED_SKILL_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "inputs",
    "outputs",
    "tools",
    "risk_level",
    "instructions",
}


def parse_skill_markdown(path: Path) -> dict[str, Any]:
    """Parse a SKILL.md file into a normalized skill dict, raising if required keys are missing."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in REQUIRED_SKILL_KEYS:
            values[key] = value.strip()
    missing = REQUIRED_SKILL_KEYS - values.keys()
    if missing:
        raise ValueError(f"Skill {path} is missing required keys: {', '.join(sorted(missing))}")
    return {
        "name": values["name"],
        "description": values["description"],
        "license": values["license"],
        "compatibility": values["compatibility"],
        "riskLevel": values["risk_level"],
        "metadata": {
            "inputs": values["inputs"],
            "outputs": values["outputs"],
            "tools": values["tools"],
            "instructions": values["instructions"],
        },
    }


def row_to_skill(row: sqlite3.Row) -> dict[str, Any]:
    """Map a `skills` row to the camelCase skill dict returned by the API."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "license": row["license"],
        "compatibility": row["compatibility"],
        "riskLevel": row["risk_level"],
        "path": row["path"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _skill_version_row_to_record(skill: dict[str, Any], version: sqlite3.Row | None) -> dict[str, Any]:
    metadata = skill.get("metadata") or {}
    return {
        "id": skill["id"],
        "name": skill["name"],
        "description": skill["description"],
        "riskLevel": skill["riskLevel"],
        "path": skill["path"],
        "tools": metadata.get("tools"),
        "version": version["version"] if version else None,
        "instructionsHash": version["instructions_hash"] if version else None,
    }


class SkillRegistry:
    """Catalog of installed skills backed by the `skills` and `skill_versions` tables."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def sync(self, skills_path: str | Path) -> int:
        """Scan `<root>/*/SKILL.md`, upsert each skill, version it by hash, and return the count.

        Per skill, writes one `skills` upsert plus an idempotent `skill_versions` insert
        keyed by content hash. The caller owns the surrounding transaction/commit.
        """
        root = Path(skills_path)
        count = 0
        for skill_file in root.glob("*/SKILL.md"):
            skill = parse_skill_markdown(skill_file)
            skill_id = f"skill-{hashlib.sha256(skill['name'].encode('utf-8')).hexdigest()[:12]}"
            content = skill_file.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            timestamp = utc_now()
            self.connection.execute(
                """
                INSERT INTO skills
                    (id, name, description, license, compatibility, risk_level, path,
                     metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    license = excluded.license,
                    compatibility = excluded.compatibility,
                    risk_level = excluded.risk_level,
                    path = excluded.path,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    skill_id,
                    skill["name"],
                    skill["description"],
                    skill["license"],
                    skill["compatibility"],
                    skill["riskLevel"],
                    str(skill_file),
                    json_dumps(skill["metadata"]),
                    timestamp,
                    timestamp,
                ),
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO skill_versions
                    (id, skill_id, version, instructions_hash, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"skill-version-{content_hash[:12]}", skill_id, "1", content_hash, content, timestamp),
            )
            count += 1
        return count

    def list_skills(self) -> list[dict[str, Any]]:
        """Return all catalogued skills ordered by name."""
        rows = self.connection.execute("SELECT * FROM skills ORDER BY name ASC").fetchall()
        return [row_to_skill(row) for row in rows]

    def resolve_for_agent_run(
        self, *, requested_refs: list[str], allowed_refs: list[str]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Resolve requested skills to immutable versions and enforce the agent profile allowlist."""
        if not requested_refs:
            return [], None
        allow_all = "*" in allowed_refs
        allowed = {str(item) for item in allowed_refs if isinstance(item, str) and item}
        refs = [ref for ref in (str(item).strip() for item in requested_refs) if ref]
        if not refs:
            return [], None

        # Resolución por lote: un SELECT para el catálogo y otro para las versiones, en vez
        # de dos queries por ref. La semántica por-ref (orden, errores, allowlist, dedupe)
        # se conserva recorriendo ``refs`` en orden sobre los mapas resueltos.
        unique_refs = sorted(set(refs))
        placeholders = ", ".join("?" for _ in unique_refs)
        skill_rows = self.connection.execute(
            f"SELECT * FROM skills WHERE id IN ({placeholders}) OR name IN ({placeholders})",
            (*unique_refs, *unique_refs),
        ).fetchall()
        skills_by_ref: dict[str, dict[str, Any]] = {}
        for row in skill_rows:
            skill = row_to_skill(row)
            skills_by_ref.setdefault(skill["id"], skill)
            skills_by_ref.setdefault(skill["name"], skill)

        skill_ids = sorted({skill["id"] for skill in skills_by_ref.values()})
        latest_version_by_skill: dict[str, Any] = {}
        if skill_ids:
            version_placeholders = ", ".join("?" for _ in skill_ids)
            version_rows = self.connection.execute(
                f"""
                SELECT * FROM skill_versions
                WHERE skill_id IN ({version_placeholders})
                ORDER BY created_at DESC, id DESC
                """,
                skill_ids,
            ).fetchall()
            for version in version_rows:
                latest_version_by_skill.setdefault(version["skill_id"], version)

        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in refs:
            skill = skills_by_ref.get(ref)
            if skill is None:
                return [], f"Skill is not cataloged: {ref}."
            if not allow_all and skill["id"] not in allowed and skill["name"] not in allowed:
                return [], f"Skill '{skill['name']}' is not allowed by the agent profile."
            if skill["id"] in seen:
                continue
            version = latest_version_by_skill.get(skill["id"])
            if not version:
                return [], f"Skill '{skill['name']}' has no immutable version record."
            resolved.append(_skill_version_row_to_record(skill, version))
            seen.add(skill["id"])
        return resolved, None

    def bind_skills_for_run(
        self,
        *,
        skills: list[dict[str, Any]],
        agent_profile_id: str,
        workflow_id: str | None,
        agent_run_id: str,
        task_id: str,
    ) -> None:
        """Persist a per-run skill binding so skill use is queryable outside evidence packages."""
        if not skills:
            return
        timestamp = utc_now()
        for skill in skills:
            self.connection.execute(
                """
                INSERT INTO skill_bindings
                    (id, skill_id, agent_profile_id, workflow_id, status, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"skill-binding-{uuid.uuid4()}",
                    skill["id"],
                    agent_profile_id,
                    workflow_id,
                    "resolved",
                    json_dumps(
                        {
                            "agentRunId": agent_run_id,
                            "taskId": task_id,
                            "instructionsHash": skill.get("instructionsHash"),
                            "version": skill.get("version"),
                        }
                    ),
                    timestamp,
                ),
            )
