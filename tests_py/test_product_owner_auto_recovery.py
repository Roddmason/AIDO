"""A failed read-only PO runtime must not stop a healthy alternative."""

from types import SimpleNamespace

import pytest

from local_control_center.product_loop.coordinator import ProductLoopCoordinator


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429, 503])
def test_product_owner_structured_failure_switches_before_blocking(status):
    coordinator = object.__new__(ProductLoopCoordinator)
    events, calls = [], []
    replacement = {"preferredRuntime": "healthy", "model": "working", "metadata": {}}
    coordinator._failover_replacement = lambda **kwargs: replacement
    coordinator._record_thread_event = lambda **kwargs: events.append(kwargs)

    def execute(payload):
        calls.append(payload)
        if payload["preferredRuntime"] == "broken":
            return {
                "status": "failed",
                "output": None,
                "reason": "provider_request_failed",
                "runtimeResult": {"httpStatus": status, "status": "failed"},
            }
        return {"status": "completed", "output": {"ok": True}}

    result = coordinator._run_with_failover(
        runtime=SimpleNamespace(run=execute),
        payload={"preferredRuntime": "broken", "model": "retired"},
        run=SimpleNamespace(loop={"id": "loop-test"}),
        attempts=[],
        thread_id="thread-test",
        role="product_owner",
    )
    assert result["status"] == "completed"
    assert len(calls) == 2
    assert len(events) == 1


def test_partial_developer_result_is_never_replayed():
    coordinator = object.__new__(ProductLoopCoordinator)
    calls = []
    result = {"status": "failed", "runtimeResult": {"httpStatus": 404}, "output": {"files": ["a.py"]}}

    def execute(payload):
        calls.append(payload)
        return result

    assert (
        coordinator._run_with_failover(
            runtime=SimpleNamespace(run=execute),
            payload={"preferredRuntime": "one"},
            run=SimpleNamespace(loop={"id": "loop"}),
            attempts=[],
            thread_id=None,
        )
        == result
    )
    assert len(calls) == 1


def test_exhausted_product_owner_keeps_last_failure_and_selection():
    coordinator = object.__new__(ProductLoopCoordinator)
    events, calls = [], []
    selected = {"selected": {"providerId": "second", "model": "unavailable"}}
    coordinator._failover_replacement = lambda **kwargs: (
        {"preferredRuntime": "second", "metadata": {"resourceSelection": selected}}
        if len(calls) == 1
        else None
    )
    coordinator._record_thread_event = lambda **kwargs: events.append(kwargs)

    def execute(payload):
        calls.append(payload)
        return {
            "status": "failed",
            "output": None,
            "reason": payload["preferredRuntime"],
            "runtimeResult": {"httpStatus": 404},
        }

    run = SimpleNamespace(loop={"id": "loop"})
    result = coordinator._run_with_failover(
        runtime=SimpleNamespace(run=execute),
        payload={"preferredRuntime": "first"},
        run=run,
        attempts=[],
        thread_id="thread",
        role="product_owner",
    )
    assert result["reason"] == "second"
    assert run.product_owner_resource_decision == selected
    assert len(calls) == 2


def test_product_owner_failover_reuses_executor_contract_and_seal(tmp_path, monkeypatch):
    from contextlib import closing

    from local_control_center.agents.ai_resource_manager import AIResourceManager
    from local_control_center.shared.db import open_sqlite_connection
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(tmp_path / "failover.sqlite")) as connection:
        initialize_platform_schema(connection)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        captured = []

        def select(_manager, request, **kwargs):
            captured.append(request)
            raise RuntimeError("Stopped at the real executor contract, before inference")

        monkeypatch.setattr(AIResourceManager, "select_resource", select)
        result = coordinator._failover_replacement(
            run=SimpleNamespace(project_id="project", loop={"id": "loop"}, request_meta={}),
            payload={"taskId": "task"},
            attempts=[{"failureClass": "authentication", "providerId": "nvidia_nim", "model": "old"}],
            provider_id="nvidia_nim",
            failed_model="old",
            role="product_owner",
        )
        assert result is None
        assert captured[0].agent_id == "product_owner_agent"
        assert captured[0].excluded_resources == [{"provider": "nvidia_nim", "model": "*"}]
        assert "gemini_cli" not in captured[0].allowed_provider_ids
        assert "claude_code_cli" in captured[0].allowed_provider_ids
