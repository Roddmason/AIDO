from __future__ import annotations

import hashlib
import ipaddress
import socket
from contextlib import ExitStack, closing
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from local_control_center.agents import research_agent as research_agent_module
from local_control_center.app import create_app
from local_control_center.research.source_log import list_research_sources
from local_control_center.research.web_search import ResearchProviderBlockedError, searxng_web_search_provider
from local_control_center.settings.repository import SettingsRepository
from local_control_center.threads.repository import ThreadsRepository
from tests_py.control_plane_fixture import ControlPlaneFixture
from tests_py.execution_client import CompletedExecutionClient as TestClient


@pytest.fixture(autouse=True)
def _public_source_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resuelve los nombres de las fuentes de prueba a una IP pública fija, sin DNS real.

    ``assert_public_source_url`` falla cerrado ante un nombre que no resuelve; sin este doble los
    tests que descargan ``docs.python.org`` (con ``urlopen`` parcheado) dependerían de la red.
    """
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            ipaddress.ip_address(str(host))
        except ValueError:
            if str(host).lower() != "localhost":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.0.223", port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def auth_headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/v1/security/handshake").json()["token"]
    return {"X-Local-Control-Token": token, "Origin": "http://127.0.0.1"}


@pytest.fixture
def create_client():
    with ExitStack() as _owned_fixture_resources:

        def create_owned(
            tmp_path: Path, monkeypatch: pytest.MonkeyPatch
        ) -> tuple[ControlPlaneFixture, TestClient, dict[str, str]]:
            monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
            store = _owned_fixture_resources.enter_context(
                closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
            )
            store.init()
            client = _owned_fixture_resources.enter_context(
                TestClient(create_app(runtime=store, static_dir=None))
            )
            return store, client, auth_headers(client)

        yield create_owned


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


@pytest.mark.parametrize("cited", [True, False])
def test_research_report_emits_thread_event_for_live_refresh(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cited: bool
) -> None:
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-event")
    threads = ThreadsRepository(store.connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Research"
    )
    url = "https://docs.python.org/3/library/asyncio-task.html"
    result = research_agent_module.ResearchAgentRunner(store.connection, root=tmp_path).run(
        research_request(
            project,
            workspace,
            metadata={"threadId": thread["id"]},
            sources=[{"url": url, "publisher": "Python", "content": "TaskGroup documentation."}],
            conclusions=[{"statement": "TaskGroup is documented.", "citations": [url] if cited else []}],
        )
    )
    events = [event for event in threads.list_events(thread["id"]) if event["type"] == "research_report"]
    assert len(events) == 1
    assert events[0]["payload"] == {
        "artifactId": result["reportArtifact"]["id"],
        "status": "research_ready" if cited else "research_blocked",
    }
    assert events[0]["agentRole"] == "research_agent"
    assert threads.list_artifacts(thread["id"])[0]["artifactId"] == events[0]["payload"]["artifactId"]


class _Headers:
    def get_content_charset(self) -> str:
        return "utf-8"


class _FetchedResponse:
    headers = _Headers()

    def __init__(self, content: str):
        self.content = content.encode("utf-8")

    def __enter__(self) -> _FetchedResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.content


def test_research_agent_status_exposes_source_policy_contract(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


def test_research_agent_blocks_when_internet_source_cannot_be_fetched(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline_urlopen(*_args: object, **_kwargs: object) -> _FetchedResponse:
        raise URLError("network unreachable")

    monkeypatch.setattr(research_agent_module, "urlopen", offline_urlopen)
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-offline")

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[
                {
                    "url": "https://docs.python.org/3/library/asyncio-task.html",
                    "publisher": "Python Software Foundation",
                }
            ],
            conclusions=[
                {
                    "statement": "Python asyncio TaskGroup is available.",
                    "citations": ["https://docs.python.org/3/library/asyncio-task.html"],
                    "webBased": True,
                }
            ],
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "research_blocked"
    assert body["reason"] == "ResearchAgent source fetch failed: <urlopen error network unreachable>"
    assert body["sources"] == []
    assert body["agentRun"]["status"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"


class _DroppedConnectionResponse(_FetchedResponse):
    """Respuesta cuya lectura del cuerpo se corta: ``urllib`` no envuelve ese error en ``URLError``."""

    def __init__(self, error: Exception):
        super().__init__("")
        self.error = error

    def read(self, _limit: int) -> bytes:
        raise self.error


@pytest.mark.parametrize(
    "dropped",
    [
        ConnectionResetError(10054, "connection reset by peer"),
        IncompleteRead(b"partial", 1024),
        RemoteDisconnected("remote end closed connection"),
    ],
    ids=["connection-reset", "incomplete-read", "remote-disconnected"],
)
def test_research_agent_blocks_when_source_connection_drops_mid_read(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dropped: Exception
) -> None:
    """Un corte al leer la fuente termina en ``research_blocked``, nunca en un job reventado."""
    monkeypatch.setattr(
        research_agent_module, "urlopen", lambda *_args, **_kwargs: _DroppedConnectionResponse(dropped)
    )
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-dropped")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[{"url": docs_url, "publisher": "Python Software Foundation"}],
            conclusions=[
                {
                    "statement": "Python asyncio TaskGroup is available.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "research_blocked"
    assert body["reason"] == f"ResearchAgent source fetch failed: {dropped}"
    assert body["agentRun"]["status"] == "blocked"


def test_research_agent_fetches_official_source_and_persists_research_sources(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_content = "Official Python asyncio documentation says TaskGroup is available."

    def mocked_urlopen(*_args: object, **_kwargs: object) -> _FetchedResponse:
        return _FetchedResponse(docs_content)

    monkeypatch.setattr(research_agent_module, "urlopen", mocked_urlopen)
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-fetch")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[{"url": docs_url, "publisher": "Python Software Foundation"}],
            conclusions=[
                {
                    "statement": "Python asyncio TaskGroup is available.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "research_ready"
    persisted = store.connection.execute(
        "SELECT * FROM research_sources WHERE project_id = ?", (project["id"],)
    ).fetchall()
    assert len(persisted) == 1
    assert persisted[0]["source_url"] == docs_url
    assert persisted[0]["url"] == docs_url
    assert persisted[0]["title"] == "Python Software Foundation"
    assert persisted[0]["source_type"] == "web"
    assert persisted[0]["trust_level"] == "official_documentation"
    assert persisted[0]["content_hash"] == hashlib.sha256(docs_content.encode("utf-8")).hexdigest()
    assert body["sources"][0]["id"] == persisted[0]["id"]
    assert body["sources"][0]["artifactId"] == persisted[0]["artifact_id"]
    run = store.connection.execute(
        "SELECT * FROM research_runs WHERE project_id = ?", (project["id"],)
    ).fetchone()
    assert run is not None
    assert run["status"] == "research_ready"
    assert run["recommendation_json"]

    findings = store.connection.execute(
        "SELECT * FROM research_findings WHERE research_run_id = ? ORDER BY created_at",
        (run["id"],),
    ).fetchall()
    assert {finding["finding_type"] for finding in findings} >= {"citation_check", "recommendation"}
    recommendation = next(finding for finding in findings if finding["finding_type"] == "recommendation")
    assert docs_url in recommendation["citations_json"]


def test_research_agent_recommendation_cites_trusted_source_without_manual_decision(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-recommendation")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            sources=[
                {
                    "url": docs_url,
                    "title": "asyncio Task documentation",
                    "publisher": "Python Software Foundation",
                    "content": "TaskGroup is the official structured concurrency API.",
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                }
            ],
            conclusions=[
                {
                    "statement": "Use TaskGroup for structured concurrency.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "research_ready"
    citations = body["recommendation"]["sourceCitations"]
    assert citations
    assert citations[0]["url"] == docs_url
    assert citations[0]["title"] == "asyncio Task documentation"
    assert citations[0]["sourceId"] == body["sources"][0]["id"]


def test_research_agent_uses_policy_allowed_web_search_and_prefers_official_sources(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-search")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"
    calls: list[tuple[str, int]] = []

    def search_provider(query: str, max_sources: int) -> list[dict[str, Any]]:
        calls.append((query, max_sources))
        return [
            {
                "url": "https://unknown.example/asyncio",
                "title": "Untrusted asyncio note",
                "publisher": "Unknown",
                "content": "Untrusted note.",
            },
            {
                "url": docs_url,
                "title": "asyncio Task documentation",
                "publisher": "Python Software Foundation",
                "content": "TaskGroup is the official structured concurrency API.",
            },
        ]

    result = research_agent_module.ResearchAgentRunner(
        store.connection,
        root=tmp_path,
        web_search_provider=search_provider,
    ).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            conclusions=[
                {
                    "statement": "Use TaskGroup for structured concurrency.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
            metadata={"allowWebSearch": True},
        )
    )

    assert calls == [("Python asyncio TaskGroup official docs", 1)]
    assert result["status"] == "research_ready"
    assert [source["url"] for source in result["sources"]] == [docs_url]
    assert result["sources"][0]["trustLevel"] == "official_documentation"


def test_research_agent_default_web_search_provider_fetches_and_prioritizes_official_sources(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    SettingsRepository(store.connection).set_value(
        "research.webSearch.provider", "general", None, "duckduckgo"
    )
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-default-search")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"
    unknown_url = "https://unknown.example/asyncio"
    calls: list[str] = []

    search_html = """
    <html><body>
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Funknown.example%2Fasyncio">Untrusted asyncio note</a>
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio-task.html">
        asyncio Task documentation
      </a>
    </body></html>
    """

    def mocked_urlopen(request: object, **_kwargs: object) -> _FetchedResponse:
        url = str(getattr(request, "full_url", request))
        calls.append(url)
        if "duckduckgo.com/html" in url:
            return _FetchedResponse(search_html)
        if url == docs_url:
            return _FetchedResponse("TaskGroup is the official structured concurrency API.")
        if url == unknown_url:
            return _FetchedResponse("Untrusted note.")
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(research_agent_module, "urlopen", mocked_urlopen)

    result = research_agent_module.ResearchAgentRunner(store.connection, root=tmp_path).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            conclusions=[
                {
                    "statement": "Use TaskGroup for structured concurrency.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_ready"
    assert [source["url"] for source in result["sources"]] == [docs_url]
    assert result["sources"][0]["title"] == "asyncio Task documentation"
    assert result["sources"][0]["sourceType"] == "web_search"
    assert result["sources"][0]["trustLevel"] == "official_documentation"
    assert len(calls) == 2


def test_research_agent_web_search_blocks_with_reason_when_internet_is_unavailable(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline_urlopen(*_args: object, **_kwargs: object) -> _FetchedResponse:
        raise URLError("network unreachable")

    monkeypatch.setattr(research_agent_module, "urlopen", offline_urlopen)
    store, client, headers = create_client(tmp_path, monkeypatch)
    SettingsRepository(store.connection).set_value(
        "research.webSearch.provider", "general", None, "duckduckgo"
    )
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-search-offline")

    response = client.post(
        "/api/v1/agents/research/runs",
        headers=headers,
        json=research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "research_blocked"
    assert body["reason"] == "ResearchAgent web search failed: <urlopen error network unreachable>"
    assert body["sources"] == []
    assert body["agentRun"]["status"] == "blocked"
    run = store.connection.execute(
        "SELECT * FROM research_runs WHERE project_id = ?", (project["id"],)
    ).fetchone()
    assert run is not None
    assert run["status"] == "research_blocked"
    assert "Check network access" in run["remediation_json"]
    blocker = store.connection.execute(
        """
        SELECT * FROM research_findings
        WHERE research_run_id = ? AND finding_type = 'blocker'
        """,
        (run["id"],),
    ).fetchone()
    assert blocker is not None
    assert "network unreachable" in blocker["summary"]


def test_research_agent_technical_decision_cites_persisted_source(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-decision")
    docs_url = "https://docs.python.org/3/library/asyncio-task.html"

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
                    "content": "TaskGroup is the official structured concurrency API.",
                    "fetchedAt": "2026-06-25T10:00:00.000Z",
                }
            ],
            conclusions=[
                {
                    "statement": "Use TaskGroup for structured concurrency.",
                    "citations": [docs_url],
                    "webBased": True,
                }
            ],
            technicalDecisions=[
                {
                    "title": "Structured concurrency API",
                    "decision": "Use asyncio.TaskGroup for concurrent subtasks.",
                    "sourceUrls": [docs_url],
                }
            ],
        ),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    decision = body["technicalDecisions"][0]
    assert decision["decision"] == "Use asyncio.TaskGroup for concurrent subtasks."
    citation = decision["sourceCitations"][0]
    assert citation["sourceId"] == body["sources"][0]["id"]
    assert citation["url"] == docs_url
    assert citation["hash"] == body["sources"][0]["hash"]
    assert body["recommendation"]["sourceCitations"] == decision["sourceCitations"]


def test_research_agent_persists_sources_and_recommends_highest_trust_conflict(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert body["status"] == "research_ready"
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
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert body["status"] == "research_blocked"
    assert body["citationCheck"]["uncited"] == ["No citation is present."]
    assert body["citationCheck"]["untrustedOnly"] == ["Only an untrusted source is cited."]
    assert body["agentRun"]["status"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"


def test_research_agent_highest_trust_conflict_requires_human_review(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert body["status"] == "research_blocked"
    assert body["agentRun"]["status"] == "blocked"
    assert body["evidencePackage"]["qaVerdict"] == "blocked"
    assert body["conflictFindings"][0]["recommendation"] == {
        "needsManualReview": True,
        "reason": "The highest-trust sources disagree; a human must resolve the conflict.",
    }


def test_duckduckgo_non_200_is_reported_as_provider_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Challenge(_FetchedResponse):
        status = 202

    monkeypatch.setattr(
        research_agent_module, "urlopen", lambda *_args, **_kwargs: _Challenge("<html>anomaly</html>")
    )

    with pytest.raises(ResearchProviderBlockedError) as caught:
        research_agent_module._duckduckgo_web_search_provider("python asyncio docs", 5)

    assert caught.value.code == "research_provider_blocked"


def test_research_run_reports_a_blocked_provider_instead_of_missing_sources(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-provider-blocked")

    def blocked_provider(_query: str, _max_sources: int) -> list[dict[str, Any]]:
        raise ResearchProviderBlockedError("searxng", "HTTP 403")

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=blocked_provider
    ).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert result["reason"] == "The web search provider blocked the query (searxng: HTTP 403)."
    assert result["remediation"]["action"] == "research_provider_blocked"


def test_research_run_with_searxng_down_blocks_with_network_remediation(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Réplica de ``docker stop aido-searxng``: el run termina ``research_blocked``, no con excepción."""
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-searxng-down")
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        closed_url = f"http://127.0.0.1:{probe.getsockname()[1]}"

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=searxng_web_search_provider(closed_url)
    ).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert result["reason"].startswith("ResearchAgent web search failed:")
    assert result["remediation"]["action"] == "check_network_access"


def test_research_run_never_fetches_a_non_public_source(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_urlopen(*_args: object, **_kwargs: object) -> _FetchedResponse:
        raise AssertionError("a non-public source must never be fetched")

    monkeypatch.setattr(research_agent_module, "urlopen", forbidden_urlopen)
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-ssrf")

    def metadata_provider(_query: str, _max_sources: int) -> list[dict[str, Any]]:
        return [
            {
                "url": "http://169.254.169.254/latest/meta-data/",
                "title": "Instance metadata",
                "publisher": "169.254.169.254",
                "sourceType": "web_search",
            }
        ]

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=metadata_provider
    ).run(
        research_request(
            project,
            workspace,
            query="cloud instance metadata",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert "must target a public host" in result["reason"]
