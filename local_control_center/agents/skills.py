from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.store import json_dumps, json_loads, utc_now


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


class SkillRegistry:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def sync(self, skills_path: str | Path) -> int:
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
        rows = self.connection.execute("SELECT * FROM skills ORDER BY name ASC").fetchall()
        return [row_to_skill(row) for row in rows]
