from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.research.source_log import list_research_sources
from tests_py.control_plane_fixture import ControlPlaneFixture


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


def create_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    client = TestClient(create_app(runtime=store, static_dir=None))
    return store, client, auth_headers(client)


def create_project_and_workspace(
    store: ControlPlaneFixture,
    tmp_path: Path,
    *,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project_path = tmp_path / task_id
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "README.md").write_text("# Research agent test project\n", encoding="utf-8")
    project = store.create_project(name=f"Research {task_id}", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="research_agent",
        reason="research agent test workspace",
        isolation_type="directory",
    )
    return project, workspace


def research_request(project: dict[str, Any], workspace: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "projectId": project["id"],
        "workspaceId": workspace["id"],
        "taskId": "research-policy-check",
        **extra,
    }


def test_research_agent_status_exposes_source_policy_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, _headers = create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/agents/research/status")

    assert response.status_code == 200
    body = response.json()["researchAgent"]
    assert body["executable"] is True
    assert body["contract"]["sourcePolicy"]["trustLevels"] == [
        "official_documentation",
        "official_repository",
        "standard_rfc",
        "primary_research",
        "reputable_secondary",
    ]
    assert body["contract"]["requiredEvidence"] is True


def test_research_agent_persists_sources_and_recommends_highest_trust_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-persist")
    docs_content = "Official Python asyncio documentation says TaskGroup is available."
    secondary_content = "A secondary article says TaskGroup is unavailable."
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"
    secondary_url = "https://martinfowler.com/articles/example-research.html"

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[
                {
                    "url": docs_url,
                    "publisher": "Python Software Foundation",
                    "content": docs_content,
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                },
                {
                    "url": secondary_url,
                    "publisher": "Martin Fowler",
                    "content": secondary_content,
                    "fetchedAt": "2026-06-25T11:00:00.000Z",
                },
            ],
            conclusions=[
                {
                    "statement": "Python asyncio TaskGroup is available.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
            claims=[
                {"topic": "asyncio_taskgroup_availability", "value": "available", "sourceUrl": docs_url},
                {
                    "topic": "asyncio_taskgroup_availability",
                    "value": "unavailable",
                    "sourceUrl": secondary_url,
                },
            ],
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "completed"
    assert body["citationCheck"]["valid"] is True
    assert body["evidencePackage"]["qaVerdict"] == "passed"
    assert body["reportArtifact"]["id"].startswith("artifact-")
    assert Path(body["reportArtifact"]["path"]).exists()

    finding = body["conflictFindings"][0]
    assert finding["topic"] == "asyncio_taskgroup_availability"
    assert finding["recommendation"] == {
        "needsManualReview": False,
        "value": "available",
        "basis": "official_documentation",
        "sourceUrl": docs_url,
        "staleWarning": True,
    }

    stored_sources = list_research_sources(store.connection, project["id"])
    assert len(stored_sources) == 2
    stored_docs = next(source for source in stored_sources if source["url"] == docs_url)
    assert stored_docs["publisher"] == "Python Software Foundation"
    assert stored_docs["fetchedAt"] == "2026-06-25T10:00:00.000Z"
    assert stored_docs["hash"] == hashlib.sha256(docs_content.encode("utf-8")).hexdigest()
    assert stored_docs["trustLevel"] == "official_documentation"
    assert stored_docs["relatedArtifact"] == body["reportArtifact"]["id"]
    assert stored_docs["artifactId"] in body["evidencePackage"]["artifactIds"]


def test_research_agent_blocks_uncited_or_untrusted_web_conclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(_store, tmp_path, task_id="research-block")

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[
                {
                    "url": "https://unknown.example/post",
                    "publisher": "Unknown Blog",
                    "content": "A claim without policy trust.",
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                }
            ],
            conclusions=[
                {"statement": "No citation is present.", "citations": [], "webBased": True},
                {
                    "statement": "Only an untrusted source is cited.",
                    "citations": ["https://unknown.example/post"],
                    "webBased": True,
                },
            ],
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "blocked"
    assert body["citationCheck"]["uncited"] == ["No citation is present."]
    assert body["citationCheck"]["untrustedOnly"] == ["Only an untrusted source is cited."]
    assert body["agentRun"]["status"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"


def test_research_agent_highest_trust_conflict_requires_human_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(_store, tmp_path, task_id="research-review")
    url_a = "https://docs.python.org/3/library/pathlib.html"
    url_b = "https://docs.python.org/3/library/os.path.html"

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[
                {
                    "url": url_a,
                    "publisher": "Python Software Foundation",
                    "content": "Use pathlib for path operations.",
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                },
                {
                    "url": url_b,
                    "publisher": "Python Software Foundation",
                    "content": "Use os.path for path operations.",
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                },
            ],
            conclusions=[
                {
                    "statement": "Python path APIs have conflicting official guidance.",
                    "citations": [url_a, url_b],
                    "webBased": True,
                }
            ],
            claims=[
                {"topic": "preferred_path_api", "value": "pathlib", "sourceUrl": url_a},
                {"topic": "preferred_path_api", "value": "os.path", "sourceUrl": url_b},
            ],
        ),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "needs_human_review"
    assert body["agentRun"]["status"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "needs_human_review"
    assert body["conflictFindings"][0]["recommendation"] == {
        "needsManualReview": True,
        "reason": "The highest-trust sources disagree; a human must resolve the conflict.",
    }
