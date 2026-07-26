"""Concurrencia del snapshot git: los GET status/branches no retienen el lock global de /api/.

La fase subprocess (7 comandos git brokered) corre fuera del ``store_request_lock`` sobre una
conexión sqlite dedicada; la fase con carrera lógica (workspace/agent run) sigue bajo el lock y
los requests concurrentes del mismo proyecto comparten una sola ejecución brokered (single-flight).

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from local_control_center.git_workspace.api import is_git_snapshot_request
from local_control_center.security_policy.git_command_runner import git_available
from tests_py.test_git_workspace_api import create_client, create_git_project

pytestmark = pytest.mark.skipif(not git_available(), reason="git CLI is required for git workspace tests")

FAKE_GIT_SLEEP_ENV = "AIDO_FAKE_GIT_SLEEP_SECONDS"

FAKE_GIT_SCRIPT = """
import os
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    subcommand = args[0] if args else ""
    if subcommand == "status":
        delay = float(os.environ.get("AIDO_FAKE_GIT_SLEEP_SECONDS", "0") or 0)
        if delay:
            time.sleep(delay)
        return 0
    if subcommand == "branch":
        if "--show-current" in args:
            print("main")
        elif "--remotes" not in args:
            print("main")
        return 0
    if subcommand == "log":
        separator = "\\x1f"
        print(separator.join(["a" * 40, "a" * 7, "AIDO Tests", "2026-07-26T00:00:00Z", "Initial commit"]))
        return 0
    if subcommand == "worktree":
        print(f"worktree {os.getcwd()}")
        print("HEAD " + "a" * 40)
        print("branch refs/heads/main")
        return 0
    return 0


sys.exit(main())
"""


def install_fake_git(bin_dir: Path, monkeypatch: pytest.MonkeyPatch, *, sleep_seconds: float) -> None:
    """Instala un ``git`` falso en PATH que duerme en ``git status`` y responde el resto al instante.

    Mismo patrón que ``write_fake_gitleaks``: el snapshot conserva su flujo brokered completo pero
    con una duración de subprocess controlada por el test, sin depender del repo real. El PATH
    queda reducido SOLO al directorio del fake: el resolver del sandbox prueba ``git.exe`` antes
    que ``git.cmd``, así que con el git real todavía visible el fake jamás se ejecutaría.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    implementation = bin_dir / "git_impl.py"
    implementation.write_text(FAKE_GIT_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        command = bin_dir / "git.cmd"
        command.write_text(f'@echo off\r\n"{sys.executable}" "{implementation}" %*\r\n', encoding="utf-8")
    else:
        command = bin_dir / "git"
        command.write_text(f"#!{sys.executable}\n{FAKE_GIT_SCRIPT}", encoding="utf-8")
        command.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv(FAKE_GIT_SLEEP_ENV, str(sleep_seconds))


def test_is_git_snapshot_request_matches_only_get_snapshot_paths() -> None:
    assert is_git_snapshot_request("GET", "/api/v1/projects/project-1/git/status")
    assert is_git_snapshot_request("GET", "/api/v1/projects/project-1/git/branches")
    # El POST de branches (crear rama) es mutación y debe seguir serializado bajo el lock global.
    assert not is_git_snapshot_request("POST", "/api/v1/projects/project-1/git/branches")
    assert not is_git_snapshot_request("POST", "/api/v1/projects/project-1/git/status")
    assert not is_git_snapshot_request("GET", "/api/v1/projects/project-1/git/diff")
    assert not is_git_snapshot_request("GET", "/api/v1/projects/project-1/git/status/extra")
    assert not is_git_snapshot_request("GET", "/api/v1/projects/git/status")
    assert not is_git_snapshot_request("GET", "/api/v1/overview")


def test_git_status_subprocess_phase_does_not_block_other_api_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mientras el snapshot corre sus subprocess, otro request /api/ debe responder sin esperarlo.

    Regresión del freeze del dashboard: con el lock global retenido durante los 5-7s de git,
    el poller de 5s y cualquier escritura quedaban bloqueados hasta terminar el snapshot.
    """
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    install_fake_git(tmp_path / "fake-bin", monkeypatch, sleep_seconds=6.0)

    status_result: dict[str, Any] = {}
    request_sent = threading.Event()

    def fetch_status() -> None:
        request_sent.set()
        response = client.get(f"/api/v1/projects/{project['id']}/git/status")
        status_result["status_code"] = response.status_code
        status_result["body"] = response.json()

    worker = threading.Thread(target=fetch_status)
    worker.start()
    assert request_sent.wait(timeout=5)
    time.sleep(0.5)
    started = time.perf_counter()
    handshake = client.get("/api/v1/security/handshake")
    elapsed_seconds = time.perf_counter() - started
    worker.join(timeout=60)

    assert not worker.is_alive()
    assert handshake.status_code == 200
    # Con el lock retenido durante el snapshot este request esperaría ~6s (el sleep del fake git).
    assert elapsed_seconds < 4.0, f"handshake blocked for {elapsed_seconds:.2f}s behind the git snapshot"
    assert status_result["status_code"] == 200
    assert status_result["body"]["status"] == "completed"
    assert status_result["body"]["toolCalls"]
    assert status_result["body"]["policyDecisionIds"]


def test_concurrent_snapshot_requests_share_one_brokered_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos GET solapados del mismo proyecto coalescen en un solo snapshot brokered (single-flight).

    Sin coalescing, el poller de 5s apila ejecuciones de 7 comandos git cada vez que el snapshot
    tarda más que el intervalo, duplicando subprocess y trazas por la misma información.
    """
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)
    install_fake_git(tmp_path / "fake-bin", monkeypatch, sleep_seconds=4.0)
    base_tool_calls = len(store.agents.list_agent_tool_calls())

    results: dict[str, tuple[int, dict[str, Any]]] = {}

    def fetch(name: str, suffix: str) -> None:
        response = client.get(f"/api/v1/projects/{project['id']}/git/{suffix}")
        results[name] = (response.status_code, response.json())

    status_thread = threading.Thread(target=fetch, args=("status", "status"))
    branches_thread = threading.Thread(target=fetch, args=("branches", "branches"))
    status_thread.start()
    time.sleep(0.5)
    branches_thread.start()
    status_thread.join(timeout=60)
    branches_thread.join(timeout=60)

    assert not status_thread.is_alive()
    assert not branches_thread.is_alive()
    executed_tool_calls = len(store.agents.list_agent_tool_calls()) - base_tool_calls
    assert executed_tool_calls == 7, f"expected one shared snapshot (7 commands), got {executed_tool_calls}"
    status_code, status_body = results["status"]
    branches_code, branches_body = results["branches"]
    assert status_code == 200
    assert branches_code == 200
    assert status_body["status"] == "completed"
    assert branches_body["status"] == "completed"
    # El fake git reporta la rama main: la vista de branches sale del mismo snapshot compartido.
    assert branches_body["localBranches"] == ["main"]
    assert branches_body["currentBranch"] == status_body["currentBranch"]
    # Misma ejecución auditada: las trazas compartidas viajan en ambas respuestas.
    assert branches_body["policyDecisionIds"] == status_body["policyDecisionIds"]


def test_git_snapshot_requests_still_record_http_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La exención del lock no puede saltarse la telemetría HTTP ni el header de correlación."""
    store, client, _headers = create_client(tmp_path, monkeypatch)
    project = create_git_project(store, tmp_path)

    response = client.get(f"/api/v1/projects/{project['id']}/git/status")

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"]
    http_events = [event for event in store.events.list_events() if event["type"] == "telemetry.http.request"]
    snapshot_events = [
        event
        for event in http_events
        if event["payload"]["path"] == f"/api/v1/projects/{project['id']}/git/status"
    ]
    assert snapshot_events
    assert snapshot_events[0]["payload"]["method"] == "GET"
    assert snapshot_events[0]["payload"]["statusCode"] == 200
