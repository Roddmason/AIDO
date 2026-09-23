"""Proveedor SearXNG del ResearchAgent contra un servidor HTTP local de prueba, y su diagnóstico.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest

import local_control_center.research.web_search as web_search_module
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.research.web_search import (
    PROVIDER_BLOCKED_CODE,
    PublicRedirectHandler,
    ResearchProviderBlockedError,
    ResearchSourceUrlError,
    ResearchWebSearchError,
    assert_public_source_url,
    configured_web_search_provider,
    open_public_source,
    searxng_web_search_provider,
    validate_search_base_url,
)
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository


@contextmanager
def _search_server(
    status: int, body: bytes, content_type: str = "application/json"
) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_searxng_provider_returns_deduplicated_http_sources() -> None:
    body = json.dumps(
        {
            "results": [
                {"url": "https://docs.python.org/3/", "title": "Python docs", "content": "Official."},
                {"url": "javascript:alert(1)", "title": "Not a source"},
                {"url": "https://docs.python.org/3/", "title": "Duplicate"},
            ]
        }
    ).encode("utf-8")
    with _search_server(200, body) as (base_url, requests):
        sources = searxng_web_search_provider(base_url)("python asyncio", 5)

    assert sources == [
        {
            "url": "https://docs.python.org/3/",
            "title": "Python docs",
            "publisher": "docs.python.org",
            "sourceType": "web_search",
        }
    ]
    assert requests and requests[0].startswith("/search?")
    assert "format=json" in requests[0]
    assert "q=python+asyncio" in requests[0]


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [
        (403, b"Forbidden", "text/plain"),
        (429, b"Too many requests", "text/plain"),
        (200, b"<html>json format disabled</html>", "text/html"),
    ],
)
def test_searxng_refusal_is_reported_as_provider_blocked(status: int, body: bytes, content_type: str) -> None:
    with (
        _search_server(status, body, content_type) as (base_url, _requests),
        pytest.raises(ResearchProviderBlockedError) as caught,
    ):
        searxng_web_search_provider(base_url)("python asyncio", 5)

    assert caught.value.code == PROVIDER_BLOCKED_CODE
    assert "blocked the query" in str(caught.value)
    assert "no sources" not in str(caught.value)


def _closed_loopback_url() -> str:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_searxng_down_is_a_typed_network_failure_not_a_block() -> None:
    """Contenedor detenido: conexión rechazada ⇒ error tipado de red, nunca ``ValueError`` suelto."""
    with pytest.raises(ResearchWebSearchError) as caught:
        searxng_web_search_provider(_closed_loopback_url())("python asyncio", 5)

    assert not isinstance(caught.value, ResearchProviderBlockedError)
    assert str(caught.value).startswith("ResearchAgent web search failed:")


@contextmanager
def _dropping_server(*, reset: bool) -> Iterator[str]:
    """Acepta la conexión y la corta sin responder, como el proxy de Docker con SearXNG reiniciando."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)

    def serve() -> None:
        with suppress(OSError):
            connection, _address = listener.accept()
            with connection:
                connection.recv(65536)
                if reset:
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        worker.join(timeout=5)
        listener.close()


@pytest.mark.parametrize("reset", [False, True], ids=["accept-then-close", "accept-then-reset"])
def test_searxng_dropping_the_connection_is_a_typed_network_failure(reset: bool) -> None:
    """``RemoteDisconnected`` / ``ConnectionResetError`` tras conectar no pueden escapar sin tipar."""
    with _dropping_server(reset=reset) as base_url, pytest.raises(ResearchWebSearchError) as caught:
        searxng_web_search_provider(base_url)("python asyncio", 5)

    assert not isinstance(caught.value, ResearchProviderBlockedError)
    assert str(caught.value).startswith("ResearchAgent web search failed:")


def _resolve_to(address: str):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    return fake_getaddrinfo


def test_unresolvable_source_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def unresolvable(*_args, **_kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", unresolvable)
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url("https://rebind.example/doc")


def test_source_connection_revalidates_the_address_it_connects_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: pública al validar la URL, loopback al conectar ⇒ se rechaza sin conectar."""
    answers = iter(["151.101.0.223", "127.0.0.1"])

    def rebinding(host, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("a rebound address must never be connected to")

    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", rebinding)
    monkeypatch.setattr(web_search_module.socket, "create_connection", forbidden_connect)
    with pytest.raises(ResearchSourceUrlError):
        open_public_source("http://rebind.example/doc", headers={}, timeout=5)


def test_source_connection_is_pinned_to_the_validated_address(monkeypatch: pytest.MonkeyPatch) -> None:
    connected: list[tuple[str, int]] = []

    def recording_connect(address, *_args, **_kwargs):
        connected.append(address)
        raise ConnectionRefusedError("recorded")

    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("151.101.0.223"))
    monkeypatch.setattr(web_search_module.socket, "create_connection", recording_connect)
    with pytest.raises(URLError):
        open_public_source("https://docs.python.org/3/", headers={}, timeout=5)

    assert connected == [("151.101.0.223", 443)]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://127.0.0.1:4310/api/v1/overview",
        "http://localhost:4310/",
        "http://[::1]:4310/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fd00::1]/",
        "http://user:secret@docs.python.org/",
        "file:///etc/passwd",
    ],
)
def test_source_urls_to_non_public_destinations_are_rejected(url: str) -> None:
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url(url)


def test_public_name_resolving_to_a_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("127.0.0.1"))
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url("https://rebind.example/doc")

    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("169.254.169.254"))
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url("https://metadata.example/latest")


def test_public_name_resolving_to_a_global_address_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("151.101.0.223"))

    assert assert_public_source_url("https://docs.python.org/3/") is None


def test_redirects_are_revalidated_before_being_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("151.101.0.223"))
    handler = PublicRedirectHandler()
    original = Request("https://docs.python.org/3/")

    with pytest.raises(ResearchSourceUrlError):
        handler.redirect_request(original, None, 302, "Found", {}, "http://127.0.0.1:4310/api/v1/overview")
    with pytest.raises(ResearchSourceUrlError):
        handler.redirect_request(original, None, 301, "Moved", {}, "http://169.254.169.254/latest/meta-data/")
    followed = handler.redirect_request(original, None, 302, "Found", {}, "https://docs.python.org/3.13/")
    assert followed is not None and followed.full_url == "https://docs.python.org/3.13/"


def test_search_base_url_accepts_only_loopback_without_credentials() -> None:
    assert validate_search_base_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    assert validate_search_base_url("http://localhost:8888") == "http://localhost:8888"
    for rejected in (
        "http://example.com:8888",
        "http://user:secret@127.0.0.1:8888",
        "http://127.0.0.1:8888/?token=x",
        "http://127.0.0.1:bad",
        "http://127.0.0.1:99999",
        "file:///etc/passwd",
        "",
    ):
        with pytest.raises(ValueError):
            validate_search_base_url(rejected)


def test_search_settings_are_registered_and_validated() -> None:
    provider = descriptor_for("research.webSearch.provider")
    base_url = descriptor_for("research.webSearch.baseUrl")

    assert provider is not None and provider.default == "searxng"
    assert provider.enum == ("searxng", "duckduckgo")
    assert base_url is not None and base_url.default == "http://127.0.0.1:8888"
    assert validate_value(base_url, "http://localhost:8888/") == "http://localhost:8888"
    for rejected in ("http://10.0.0.5:8888", "http://127.0.0.1:bad"):
        with pytest.raises(ValueError):
            validate_value(base_url, rejected)


def test_configured_provider_follows_the_setting(tmp_path: Path) -> None:
    def duckduckgo(_query: str, _max_sources: int) -> list[dict]:
        return []

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        default_provider = configured_web_search_provider(connection, project_id=None, duckduckgo=duckduckgo)
        SettingsRepository(connection).set_value("research.webSearch.provider", "general", None, "duckduckgo")
        chosen = configured_web_search_provider(connection, project_id=None, duckduckgo=duckduckgo)

    assert default_provider is not duckduckgo
    assert chosen is duckduckgo


def _research_project_and_thread(connection, tmp_path: Path) -> tuple[dict, dict]:
    project = ProjectsRepository(connection).create_project(
        name="Research blocked", path=tmp_path / "project", template_id="other"
    )
    thread = ThreadsRepository(connection).create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Research question"
    )
    return project, thread


def test_blocked_search_provider_offers_research_settings_first(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _research_project_and_thread(connection, tmp_path)
        actions = BlockerRemediationService(connection, root=tmp_path).create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=None,
            stage="research",
            reason="The web search provider blocked the query (searxng: HTTP 403).",
            details={
                "status": "research_blocked",
                "jobId": "job-research",
                "remediation": {"action": PROVIDER_BLOCKED_CODE},
            },
        )

    assert (actions[0]["blockerType"], actions[0]["actionType"]) == (
        "research_required",
        "open_settings_section",
    )
    assert actions[0]["payload"]["section"] == "research"
    assert actions[0]["payload"]["researchRemediation"] == PROVIDER_BLOCKED_CODE
    assert "check_network_access" not in {action["actionType"] for action in actions}


def _execute_network_check(tmp_path: Path, base_url: str) -> dict:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        SettingsRepository(connection).set_value("research.webSearch.baseUrl", "general", None, base_url)
        project, thread = _research_project_and_thread(connection, tmp_path)
        service = BlockerRemediationService(connection, root=tmp_path)
        created = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=None,
            stage="research",
            reason="ResearchAgent web search failed: <urlopen error connection refused>",
            details={"status": "research_blocked", "remediation": {"action": "check_network_access"}},
        )
        network_check = next(action for action in created if action["actionType"] == "check_network_access")
        return service.execute(network_check["id"], platform=None)["execution"]


def test_network_check_probes_the_configured_searxng_instance(tmp_path: Path) -> None:
    """``Check network access`` sondea el SearXNG configurado, no un tercero que sí responde."""
    with _search_server(200, b'{"results": []}') as (base_url, requests):
        execution = _execute_network_check(tmp_path, base_url)

    assert execution["status"] == "completed"
    assert execution["endpoint"].startswith(f"{base_url}/search?")
    assert requests and "format=json" in requests[0]


def test_network_check_reports_a_stopped_searxng_as_unreachable(tmp_path: Path) -> None:
    closed_url = _closed_loopback_url()

    execution = _execute_network_check(tmp_path, closed_url)

    assert execution["status"] == "blocked"
    assert execution["endpoint"].startswith(f"{closed_url}/search?")
    assert "unreachable" in execution["reason"]
