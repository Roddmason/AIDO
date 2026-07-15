"""Cubre el modo epic-first del ProductOwnerAgent: derivar HUs desde una épica existente.

Verifica que un run con ``epicId`` expanda la épica con historias nuevas enlazadas al
registro existente (sin duplicar la épica), que la validación rechace historias que
referencian otra épica sin persistir nada, y que el endpoint devuelva 404 para épicas
de otro proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.repository import BacklogRepository
from tests_py.test_product_owner_agent_real_runtime import (
    create_client,
    create_project_and_workspace,
    run_with_controlled_provider,
)


def _seed_epic_with_story(store: Any, project_id: str) -> dict[str, Any]:
    backlog = BacklogRepository(store.connection)
    epic = backlog.create_epic(
        {
            "projectId": project_id,
            "title": "Checkout revamp",
            "description": "Modernize the purchase funnel end to end.",
        }
    )
    backlog.create_user_story(
        {
            "projectId": project_id,
            "epicId": epic["id"],
            "title": "Guest checkout",
            "asA": "shopper",
            "iWant": "to check out without an account",
            "soThat": "I can buy faster",
            "businessValue": "high",
            "acceptanceCriteria": ["Order completes without login."],
        }
    )
    return epic


def _expansion_output(*, epic_title: str) -> dict[str, Any]:
    brief = {
        "title": "Checkout revamp",
        "summary": "Expand the checkout epic with saved payment methods.",
        "problemStatement": "Returning shoppers re-enter payment data on every purchase.",
        "goals": ["Reduce repeat checkout time"],
        "targetUsers": ["Returning shoppers"],
        "successMetrics": ["repeat_checkout_time"],
        "scope": "Saved payment methods inside the existing checkout.",
        "outOfScope": "New payment providers.",
    }
    stories = [
        {
            "epicTitle": epic_title,
            "title": "Saved payment methods",
            "asA": "returning shopper",
            "iWant": "to pay with a saved payment method",
            "soThat": "I finish checkout in one step",
            "businessValue": "high",
            "acceptanceCriteria": [
                "A saved method can be selected during checkout.",
                "Saved methods are stored tokenized, never as raw numbers.",
            ],
        },
        {
            "epicTitle": epic_title,
            "title": "Remove a saved payment method",
            "asA": "returning shopper",
            "iWant": "to delete a saved payment method",
            "soThat": "I keep control over my stored data",
            "businessValue": "medium",
            "acceptanceCriteria": ["Deleting a method removes it from future checkouts."],
        },
    ]
    return {
        "status": "backlog_ready",
        "summary": "Two stories expand the checkout epic.",
        "confidence": "high",
        "completeness": {"score": 90, "missing": [], "rationale": "Epic scope is clear."},
        "questions": [],
        "assumptions": [],
        "decisions": [],
        "blockingDecisions": [],
        "productBriefPatch": brief,
        "brief": brief,
        "epics": [],
        "userStories": stories,
        "risks": [],
        "recommendedNextAction": "Approve the expanded backlog.",
    }


def test_epic_expansion_links_new_stories_to_existing_epic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="epic-expansion")
    epic = _seed_epic_with_story(store, project["id"])
    backlog = BacklogRepository(store.connection)
    epics_before = len(backlog.list_epics(project["id"]))

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(_expansion_output(epic_title=epic["title"])),
        body={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "epic-expansion",
            "epicId": epic["id"],
            "workflowContext": {"title": "Epic expansion"},
            "preferredRuntime": "openai_compatible",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert epic["title"] in body["reason"]
    assert len(backlog.list_epics(project["id"])) == epics_before
    stories = backlog.list_user_stories(epic_id=epic["id"])
    titles = {story["title"] for story in stories}
    assert {"Guest checkout", "Saved payment methods", "Remove a saved payment method"} <= titles
    new_story = next(story for story in stories if story["title"] == "Saved payment methods")
    criteria = backlog.list_acceptance_criteria(story_id=new_story["id"])
    assert [item["criterion"] for item in criteria] == [
        "A saved method can be selected during checkout.",
        "Saved methods are stored tokenized, never as raw numbers.",
    ]


def test_epic_expansion_rejects_stories_for_other_epics_without_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="epic-expansion-invalid")
    epic = _seed_epic_with_story(store, project["id"])
    backlog = BacklogRepository(store.connection)

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=json.dumps(_expansion_output(epic_title="Another epic")),
        body={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "epic-expansion-invalid",
            "epicId": epic["id"],
            "workflowContext": {"title": "Epic expansion"},
            "preferredRuntime": "openai_compatible",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed_validation"
    assert "epicTitle" in body["reason"]
    assert [story["title"] for story in backlog.list_user_stories(epic_id=epic["id"])] == ["Guest checkout"]
    assert len(backlog.list_epics(project["id"])) == 1


def test_epic_expansion_unknown_epic_returns_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="epic-expansion-404")

    response = client.post(
        "/api/v1/agents/product-owner/runs",
        headers=headers,
        json={
            "projectId": project["id"],
            "workspaceId": workspace["id"],
            "taskId": "epic-expansion-404",
            "epicId": "epic-missing",
        },
    )

    assert response.status_code == 404
