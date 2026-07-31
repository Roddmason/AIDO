"""Cubre el ensamblador de spec por historia y su propagacion al DeveloperAgent.

Verifica que la HU opere como spec ejecutable: epica + historia + criterios de
aceptacion + responsabilidades por rol llegan ensamblados (build/render), el prompt
del DeveloperAgent los incluye solo cuando se proveen y el endpoint read-only los
expone por proyecto/historia.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.agents.runtime_registry import developer_agent_prompt
from local_control_center.app import create_app
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.backlog.story_spec import (
    STORY_SPEC_PROMPT_CHAR_LIMIT,
    build_story_spec,
    render_story_spec_prompt,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.control_plane_fixture import ControlPlaneFixture


def _seed_story_with_tasks(connection: sqlite3.Connection, tmp_path: Path) -> dict[str, Any]:
    projects = ProjectsRepository(connection)
    backlog = BacklogRepository(connection)
    project = projects.create_project(name="Spec", path=tmp_path / "spec-project", template_id="other")
    epic = backlog.create_epic(
        {
            "projectId": project["id"],
            "title": "Checkout revamp",
            "description": "Modernize the purchase funnel.",
        }
    )
    story = backlog.create_user_story(
        {
            "projectId": project["id"],
            "epicId": epic["id"],
            "title": "Guest checkout",
            "asA": "shopper",
            "iWant": "to check out without an account",
            "soThat": "I can buy faster",
            "businessValue": "high",
            "acceptanceCriteria": [
                "Order completes without login.",
                "Email receipt is sent.",
            ],
        }
    )
    backend_task = backlog.create_agent_task(
        {
            "projectId": project["id"],
            "storyId": story["id"],
            "title": "Backend Engineer: Guest checkout",
            "role": "backend_engineer",
            "metadata": {
                "goal": "Implement backend changes honoring the acceptance criteria.",
                "reviewerRole": "technical_lead",
                "qualityGates": [{"id": "unit_tests", "blocking": True}],
            },
        }
    )
    qa_task = backlog.create_agent_task(
        {
            "projectId": project["id"],
            "storyId": story["id"],
            "title": "Qa Engineer: Guest checkout",
            "role": "qa_engineer",
            "metadata": {
                "goal": "Validate acceptance criteria with executable checks.",
                "reviewerRole": "technical_lead",
            },
        }
    )
    backlog.create_task_dependency(
        {
            "projectId": project["id"],
            "taskId": qa_task["id"],
            "dependsOnTaskId": backend_task["id"],
        }
    )
    return {
        "project": project,
        "epic": epic,
        "story": story,
        "backendTask": backend_task,
        "qaTask": qa_task,
    }


def test_build_story_spec_assembles_epic_story_criteria_and_roles(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        seeded = _seed_story_with_tasks(connection, tmp_path)
        backlog = BacklogRepository(connection)

        spec = build_story_spec(backlog, seeded["story"]["id"])

        assert spec["storyId"] == seeded["story"]["id"]
        assert spec["epic"]["title"] == "Checkout revamp"
        assert spec["story"]["asA"] == "shopper"
        assert spec["story"]["iWant"] == "to check out without an account"
        criteria = spec["acceptanceCriteria"]
        assert [item["criterion"] for item in criteria] == [
            "Order completes without login.",
            "Email receipt is sent.",
        ]
        assert [item["sequence"] for item in criteria] == [1, 2]
        roles = {item["role"]: item for item in spec["roleResponsibilities"]}
        assert set(roles) == {"backend_engineer", "qa_engineer"}
        assert roles["backend_engineer"]["goal"].startswith("Implement backend changes")
        assert roles["backend_engineer"]["reviewerRole"] == "technical_lead"
        assert roles["qa_engineer"]["dependsOn"] == [
            {"taskId": seeded["backendTask"]["id"], "role": "backend_engineer"}
        ]


def test_build_story_spec_raises_for_unknown_story(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        backlog = BacklogRepository(connection)
        with pytest.raises(KeyError, match="User story not found"):
            build_story_spec(backlog, "user-story-missing")


def test_render_story_spec_prompt_is_bounded_and_deterministic(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        seeded = _seed_story_with_tasks(connection, tmp_path)
        backlog = BacklogRepository(connection)
        spec = build_story_spec(backlog, seeded["story"]["id"])

        rendered_once = render_story_spec_prompt([spec])
        rendered_twice = render_story_spec_prompt([spec])

        assert rendered_once == rendered_twice
        assert "Guest checkout" in rendered_once
        assert "Order completes without login." in rendered_once
        assert "backend_engineer" in rendered_once
        assert "qa_engineer" in rendered_once

        truncated = render_story_spec_prompt([spec], char_limit=120)
        assert len(truncated) <= 120
        assert truncated.endswith("[truncated]")
        assert STORY_SPEC_PROMPT_CHAR_LIMIT > 1_000


def test_developer_agent_prompt_includes_story_specs_only_when_provided() -> None:
    base = developer_agent_prompt(instruction="Do the work.", qa_commands=[])
    with_spec = developer_agent_prompt(
        instruction="Do the work.",
        qa_commands=[],
        story_specs="## Story: Guest checkout\n- AC1: Order completes without login.",
    )

    assert "Guest checkout" not in base
    assert "User story spec" not in base
    assert "Guest checkout" in with_spec
    assert "User story spec" in with_spec
    assert with_spec.startswith(base.split("Instruction:")[0][:40])


def test_product_loop_story_spec_endpoint_returns_spec_and_404(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    seeded = _seed_story_with_tasks(store.connection, tmp_path)
    client = TestClient(create_app(runtime=store, static_dir=None))
    project_id = seeded["project"]["id"]
    story_id = seeded["story"]["id"]

    response = client.get(f"/api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec")

    assert response.status_code == 200
    payload = response.json()
    assert payload["storyId"] == story_id
    assert payload["epic"]["title"] == "Checkout revamp"
    assert [item["criterion"] for item in payload["acceptanceCriteria"]] == [
        "Order completes without login.",
        "Email receipt is sent.",
    ]
    assert {item["role"] for item in payload["roleResponsibilities"]} == {
        "backend_engineer",
        "qa_engineer",
    }
    assert "promptText" in payload
    assert "Guest checkout" in payload["promptText"]

    missing = client.get(f"/api/v1/projects/{project_id}/product-loop/stories/user-story-missing/spec")
    assert missing.status_code == 404

    other_project = store.create_project(name="Other", path=tmp_path / "other-project", template_id="other")
    cross_project = client.get(f"/api/v1/projects/{other_project['id']}/product-loop/stories/{story_id}/spec")
    assert cross_project.status_code == 404


def test_render_story_spec_prompt_filters_responsibilities_for_role() -> None:
    """`for_role` filtra responsabilidades al destinatario; sin él, spec completo para todos."""
    spec = {
        "storyId": "s1",
        "epic": {"id": "e1", "title": "Epic", "description": ""},
        "story": {
            "title": "Historia",
            "asA": "usuario",
            "iWant": "algo",
            "soThat": "valor",
            "businessValue": "",
            "status": "ready",
            "priority": "high",
        },
        "acceptanceCriteria": [{"id": "c1", "sequence": 1, "criterion": "pasa", "status": "open"}],
        "roleResponsibilities": [
            {
                "taskId": "t1",
                "role": "backend_engineer",
                "title": "impl",
                "goal": "implementar",
                "reviewerRole": "technical_lead",
                "qualityGates": [],
                "dependsOn": [],
            },
            {
                "taskId": "t2",
                "role": "security_engineer",
                "title": "sec",
                "goal": "revisar seguridad",
                "reviewerRole": "technical_lead",
                "qualityGates": [],
                "dependsOn": [],
            },
        ],
    }
    full = render_story_spec_prompt([spec])
    assert "backend_engineer" in full and "security_engineer" in full

    filtered = render_story_spec_prompt([spec], for_role="security_engineer")
    assert "security_engineer" in filtered
    assert "backend_engineer" not in filtered
    # El núcleo de la historia y los criterios sobreviven al filtro.
    assert "Historia" in filtered and "AC1" in filtered
