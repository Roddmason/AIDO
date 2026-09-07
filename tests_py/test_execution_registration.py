"""Dispatcher registration must not construct an HTTP application or unrelated routes."""

import pytest

from local_control_center.control_plane.runtime import ControlCenterRuntime


def test_registers_original_smoke_without_constructing_fastapi_routes(tmp_path, monkeypatch):
    from fastapi import routing

    from local_control_center.executions.registration import register_operation

    def forbidden(*args, **kwargs):
        raise AssertionError("Dispatcher constructed an HTTP route")

    monkeypatch.setattr(routing.APIRoute, "__init__", forbidden)
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "isolated.sqlite")
    try:
        runtime.init()
        register_operation(runtime, "models.codex_smoke")
        spec, handler = runtime.execution_handlers["models.codex_smoke"]
        assert spec.workload_class == "agent_cli"
        assert spec.result_model is not None
        assert handler.__name__ == "codex_smoke"
        assert not any(name.startswith("workflows.") for name in runtime.execution_handlers)
    finally:
        runtime.close()


@pytest.mark.parametrize("operation", ["os.system", "models.nonexistent", "__import__:evil"])
def test_unknown_operation_is_not_imported(tmp_path, operation):
    from local_control_center.executions.registration import register_operation

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "isolated.sqlite")
    try:
        runtime.init()
        with pytest.raises(ValueError, match="registered"):
            register_operation(runtime, operation)
    finally:
        runtime.close()
