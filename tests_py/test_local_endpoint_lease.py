"""La lease durable limita las llamadas simultáneas a un endpoint local entre conexiones y procesos."""

from __future__ import annotations

import time
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents import local_endpoint_lease
from local_control_center.agents.local_endpoint_lease import local_endpoint_slot, max_local_call_seconds
from local_control_center.agents.local_runtime_causes import LocalRuntimeError
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.factory import ProviderAdapterFactory
from local_control_center.agents.runtime_adapters.models import RuntimeExecutionRequest
from local_control_center.agents.runtime_adapters.provider_factory import ProviderFactoryAdapter
from local_control_center.process_supervision.context import (
    ExecutionDeadlineExceeded,
    ProcessExecutionContext,
    execution_scope,
)
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import running_llama_router


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "lease.sqlite"
    with closing(open_sqlite_connection(path)) as connection:
        initialize_platform_schema(connection)
    return path


def _factory(path: Path):
    return lambda: open_sqlite_connection(path)


def _slot(path: Path, **overrides):
    options = {"limit": 1, "ttl_seconds": 60, "wait_seconds": 0, **overrides}
    return local_endpoint_slot(_factory(path), "llama_cpp", **options)


def _local_llama(connection, base_url: str) -> None:
    store = ProviderAccountStore(connection)
    store.upsert_provider_account(
        {
            "providerId": "llama_cpp",
            "providerType": "local",
            "providerFamily": "openai_compatible",
            "apiFormat": "openai_compatible",
            "baseUrl": base_url,
            "enabled": True,
        }
    )
    store.set_provider_catalog_id("llama_cpp", "llama_cpp")


def test_a_second_connection_is_busy_while_the_single_slot_is_held(database):
    with _slot(database) as first:
        with pytest.raises(LocalRuntimeError) as busy, _slot(database):
            pytest.fail("A second call used the busy local endpoint")
        assert busy.value.cause == "local_endpoint_busy"
    with _slot(database) as second:
        assert second.slot == first.slot == 0
        assert second.fence == first.fence + 1


def test_limit_two_admits_two_concurrent_calls_and_busies_the_third(database):
    with _slot(database, limit=2) as first, _slot(database, limit=2) as second:
        assert {first.slot, second.slot} == {0, 1}
        with pytest.raises(LocalRuntimeError, match="local_endpoint_busy"), _slot(database, limit=2):
            pytest.fail("A third call exceeded the endpoint limit")


def test_an_expired_holder_cannot_release_the_slot_it_lost(database):
    clock = FakeClock()
    stale_slot = _slot(database, ttl_seconds=30, clock=clock, holder="stale-runner")
    stale = stale_slot.__enter__()
    clock.now += 31
    fresh_slot = _slot(database, ttl_seconds=30, clock=clock, holder="fresh-runner")
    fresh = fresh_slot.__enter__()
    stale_slot.__exit__(None, None, None)
    with closing(open_sqlite_connection(database)) as connection:
        row = connection.execute(
            "SELECT holder, fence FROM local_endpoint_leases WHERE provider_id = 'llama_cpp' AND slot = 0"
        ).fetchone()
    fresh_slot.__exit__(None, None, None)
    assert (row["holder"], row["fence"]) == ("fresh-runner", fresh.fence)
    assert fresh.fence == stale.fence + 1


def test_waiting_is_bounded(database):
    clock = FakeClock()
    sleeps = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    with (
        _slot(database, ttl_seconds=600, clock=clock),
        pytest.raises(LocalRuntimeError, match="local_endpoint_busy"),
        _slot(database, ttl_seconds=600, wait_seconds=0.5, clock=clock, sleep=sleep),
    ):
        pytest.fail("The busy wait never ended")
    assert 5 <= len(sleeps) <= 6


def test_call_budget_setting_drives_the_lease_ttl(database):
    with closing(open_sqlite_connection(database)) as connection:
        assert max_local_call_seconds(connection) == 900
        RuntimeConfigRepository(connection).set_runtime_setting("runtime.local.maxCallSeconds", 120)
        assert max_local_call_seconds(connection) == 120


def test_the_slot_wait_never_outlasts_the_running_execution(database):
    default_wait = local_endpoint_lease.LOCAL_ENDPOINT_WAIT_SECONDS
    assert local_endpoint_lease.local_endpoint_wait_seconds() == default_wait
    nearly_done = ProcessExecutionContext(
        db_path=database, execution_deadline_monotonic=time.monotonic() + 25
    )
    with execution_scope(nearly_done):
        assert 8 <= local_endpoint_lease.local_endpoint_wait_seconds() <= 10


def test_the_slot_wait_never_outlasts_the_call_deadline():
    assert 4 <= local_endpoint_lease.local_endpoint_wait_seconds(time.monotonic() + 5) <= 5
    assert local_endpoint_lease.local_endpoint_wait_seconds(time.monotonic() - 1) == 0.0


def test_local_provider_chat_calls_share_the_durable_slot(tmp_path, monkeypatch):
    path = tmp_path / "wired.sqlite"
    monkeypatch.setattr(local_endpoint_lease, "LOCAL_ENDPOINT_WAIT_SECONDS", 0)
    request = ModelRequest(model="gemma-4-26b-a4b", messages=[{"role": "user", "content": "Reply OK."}])
    with running_llama_router() as (root, router), closing(open_sqlite_connection(path)) as connection:
        initialize_platform_schema(connection)
        _local_llama(connection, f"{root}/v1")
        provider = ProviderAdapterFactory(connection).resolve("llama_cpp")
        with _slot(path), pytest.raises(LocalRuntimeError, match="local_endpoint_busy"):
            provider.chat_completion(request)
        response = provider.chat_completion(request)
    assert response.content == "OK"
    assert len(router.chat_bodies) == 1


def test_remote_accounts_keep_an_unbounded_invocation(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "remote.sqlite")) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": "fixture-api",
                "providerType": "api",
                "providerFamily": "openai_compatible",
                "apiFormat": "openai_compatible",
                "baseUrl": "https://fixture.example.invalid/v1",
                "enabled": True,
            }
        )
        provider = ProviderAdapterFactory(connection).resolve("fixture-api")
    assert provider.invocation_slot is nullcontext


def test_broker_adapter_reports_a_busy_local_endpoint_by_cause(tmp_path, monkeypatch):
    def busy(_request):
        raise LocalRuntimeError("local_endpoint_busy", "slot 0 is held")

    monkeypatch.setattr(
        ProviderAdapterFactory,
        "resolve_for_execution",
        lambda *_args: SimpleNamespace(
            base_url="http://127.0.0.1:1/v1", credential_ref="", chat_completion=busy
        ),
    )
    with closing(open_sqlite_connection(tmp_path / "adapter.sqlite")) as connection:
        initialize_platform_schema(connection)
        _local_llama(connection, "http://127.0.0.1:1/v1")
        result = ProviderFactoryAdapter(
            provider_family="openai_compatible", display_name="llama.cpp", connection=connection
        ).execute(
            RuntimeExecutionRequest(
                projectId="project-local",
                workspaceId="workspace-local",
                workspacePath=str(tmp_path),
                capability="chat",
                input={
                    "providerId": "llama_cpp",
                    "model": "gemma-4-26b-a4b",
                    "messages": [{"role": "user", "content": "Reply OK."}],
                },
            )
        )
    assert result.status == "unavailable"
    assert result.reason == "llama.cpp execution failed: local_endpoint_busy"


def test_lowering_the_limit_never_admits_more_calls_than_the_new_limit(database):
    low_slot = _slot(database, limit=2, holder="low-runner")
    high_slot = _slot(database, limit=2, holder="high-runner")
    low, high = low_slot.__enter__(), high_slot.__enter__()
    assert (low.slot, high.slot) == (0, 1)
    low_slot.__exit__(None, None, None)
    try:
        with pytest.raises(LocalRuntimeError, match="local_endpoint_busy"), _slot(database, limit=1):
            pytest.fail("A call ran beside the slot still held above the lowered limit")
    finally:
        high_slot.__exit__(None, None, None)
    with _slot(database, limit=1) as admitted:
        assert admitted.slot == 0


def test_a_locked_database_is_a_busy_endpoint_within_the_wait(database):
    with closing(open_sqlite_connection(database)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            with (
                pytest.raises(LocalRuntimeError, match="local_endpoint_busy"),
                _slot(database, wait_seconds=0.3),
            ):
                pytest.fail("The slot was taken while another writer held the database")
        finally:
            writer.execute("ROLLBACK")
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    ("provider_id", "provider_family"), [("ollama", "ollama"), ("loopback-ollama", "openai_compatible")]
)
def test_ollama_accounts_bind_the_durable_slot(tmp_path, provider_id, provider_family):
    path = tmp_path / "ollama.sqlite"
    with closing(open_sqlite_connection(path)) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).upsert_provider_account(
            {
                "providerId": provider_id,
                "providerType": "local",
                "providerFamily": provider_family,
                "apiFormat": "ollama",
                "baseUrl": "http://127.0.0.1:1",
                "enabled": True,
            }
        )
        provider = ProviderAdapterFactory(connection).resolve(provider_id)
        assert provider.invocation_slot is not nullcontext
        with provider.invocation_slot():
            holder = connection.execute(
                "SELECT holder FROM local_endpoint_leases WHERE provider_id = ? AND slot = 0", (provider_id,)
            ).fetchone()["holder"]
    assert holder


def test_an_exhausted_deadline_propagates_without_calling_the_local_model(tmp_path):
    path = tmp_path / "deadline.sqlite"
    with running_llama_router() as (root, router), closing(open_sqlite_connection(path)) as connection:
        initialize_platform_schema(connection)
        _local_llama(connection, f"{root}/v1")
        exhausted = ProcessExecutionContext(db_path=path, execution_deadline_monotonic=time.monotonic() - 1)
        with execution_scope(exhausted), pytest.raises(ExecutionDeadlineExceeded):
            ProviderFactoryAdapter(
                provider_family="openai_compatible", display_name="llama.cpp", connection=connection
            ).execute(
                RuntimeExecutionRequest(
                    projectId="project-local",
                    workspaceId="workspace-local",
                    workspacePath=str(tmp_path),
                    capability="chat",
                    input={
                        "providerId": "llama_cpp",
                        "model": "gemma-4-26b-a4b",
                        "messages": [{"role": "user", "content": "Reply OK."}],
                    },
                )
            )
    assert router.chat_bodies == []
