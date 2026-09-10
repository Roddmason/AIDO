from __future__ import annotations

import asyncio
import gc
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from starlette.testclient import TestClient as StarletteTestClient

from local_control_center.agents.model_gateway import reset_ollama_status_cache
from local_control_center.agents.runtime_registry import reset_detection_cache

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Retain a failed phase immediately, even if a later test kills the pytest owner."""
    outcome = yield
    report = outcome.get_result()
    if not report.failed or call.excinfo is None or not os.environ.get("AIDO_ACCEPTANCE_EVIDENCE"):
        return
    from local_control_center.shared.redaction import redact_secrets
    from tests_py.operational_acceptance_support import evidence

    try:
        evidence(
            "pytest-failure",
            redact_secrets(
                {
                    "nodeId": item.nodeid,
                    "phase": report.when,
                    "outcome": "failed",
                    "durationSeconds": report.duration,
                    "traceback": str(call.excinfo.getrepr(showlocals=False, style="long")),
                    "toolRefs": {
                        key: os.environ[key]
                        for key in ("AIDO_TEST_PROCDUMP", "AIDO_TEST_CDB")
                        if os.environ.get(key)
                    },
                    "fixtureToolRefs": dict(item.user_properties).get("nativeToolRefs", {}),
                }
            ),
        )
    except Exception as error:
        # Do not replace the test's causal exception or recurse through diagnostic logging.
        print(f"AIDO failure report unavailable: {type(error).__name__}", file=sys.stderr, flush=True)


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    """Keep fixture repositories outside pytest's disposable internals and retained evidence."""
    destination = os.environ.get("AIDO_QUALITY_FIXTURES")
    if not destination:
        return tmp_path_factory.mktemp(request.node.name[:40])
    root = Path(destination)
    from local_control_center.quality.paths import validate_scratch_parent

    validate_scratch_parent(
        root, [Path(__file__).resolve().parents[1], Path(os.environ["AIDO_ACCEPTANCE_EVIDENCE"])]
    )
    return Path(tempfile.mkdtemp(prefix="fixture-", dir=root))


@pytest.fixture
def controlled_domain_host(monkeypatch):
    """Capacidad determinista opt-in; conserva procesos nativos y el gate externo real."""
    from local_control_center.host_resources.models import ResourceSnapshot
    from local_control_center.host_resources.probes import HostResourceProbe

    monkeypatch.setattr(HostResourceProbe, "sample", lambda self, **kwargs: ResourceSnapshot.test_snapshot())


@pytest.fixture(autouse=True)
def isolated_default_process_database(tmp_path, monkeypatch):
    """Evita que ejecuciones reales de sandbox en tests escriban la base del operador."""
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "default-runtime.sqlite"))


@pytest.fixture
def low_impact_host_policy(tmp_path):
    from tests_py.operational_acceptance_support import low_impact_fixture_policy

    with ExitStack() as stack:
        yield lambda db: stack.enter_context(low_impact_fixture_policy(db, fixture_root=tmp_path))


@pytest.fixture
def controlled_codex_compatibility(monkeypatch):
    """Aísla compatibilidad en contratos de dominio con CLI ya simulado, sin ejecutar inferencia."""
    monkeypatch.setattr(
        "local_control_center.agents.codex_compatibility.CodexCompatibilityService.status",
        lambda *args, **kwargs: {"status": "compatible"},
    )


@pytest.fixture(autouse=True)
def reset_runtime_status_caches() -> Iterator[None]:
    """Aísla los cachés TTL de estado de runtimes (detección CLI y probe Ollama) entre tests."""
    reset_detection_cache()
    reset_ollama_status_cache()
    yield
    reset_detection_cache()
    reset_ollama_status_cache()


@pytest.fixture(autouse=True)
def manage_testclient_event_loops(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    created_clients: list[StarletteTestClient] = []
    original_init = StarletteTestClient.__init__
    original_enter = StarletteTestClient.__enter__
    original_exit = StarletteTestClient.__exit__

    def enter_once(self: StarletteTestClient):
        if getattr(self, "_aido_auto_entered", False):
            return self
        result = original_enter(self)
        self._aido_auto_entered = True
        return result

    def exit_once(self: StarletteTestClient, *args):
        try:
            return original_exit(self, *args)
        finally:
            self._aido_auto_entered = False

    def auto_entering_init(self: StarletteTestClient, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self.__enter__()
        self._aido_auto_entered = True
        created_clients.append(self)

    monkeypatch.setattr(StarletteTestClient, "__init__", auto_entering_init)
    monkeypatch.setattr(StarletteTestClient, "__enter__", enter_once)
    monkeypatch.setattr(StarletteTestClient, "__exit__", exit_once)
    yield
    for client in reversed(created_clients):
        if getattr(client, "_aido_auto_entered", False):
            client.__exit__(None, None, None)
    gc.collect()
