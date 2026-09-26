"""Tests: _prune_telemetry_if_due encadena la retencion de disco completa y nunca deja que un
fallo de poda interrumpa el batch del worker; el fallo queda como diagnostico WARNING.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.workers.runtime import LocalWorkerRuntime


def _new_worker(db_path: Path) -> LocalWorkerRuntime:
    worker = object.__new__(LocalWorkerRuntime)
    worker.db_path = db_path
    worker._last_telemetry_prune_monotonic = None
    return worker


def _seed_db(db_path: Path) -> None:
    with closing(open_sqlite_connection(db_path)) as handle, handle:
        initialize_platform_schema(handle)


def test_prune_telemetry_if_due_runs_the_new_disk_retention_functions(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "platform.sqlite"
    _seed_db(db_path)

    calls: list[str] = []
    import local_control_center.workers.runtime as runtime_module

    def _track(name):
        def _fn(connection, *args, **kwargs):
            calls.append(name)
            return 0

        return _fn

    monkeypatch.setattr(runtime_module, "drain_remediation_retention", _track("remediations"))
    monkeypatch.setattr(runtime_module, "prune_provider_health_checks", _track("provider_health_checks"))
    monkeypatch.setattr(runtime_module, "prune_operational_executions", _track("operational_executions"))

    _new_worker(db_path)._prune_telemetry_if_due()

    assert calls == ["remediations", "provider_health_checks", "operational_executions"]


def test_a_retention_failure_is_recorded_as_a_diagnostic_and_does_not_raise(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "platform.sqlite"
    _seed_db(db_path)

    import local_control_center.workers.runtime as runtime_module

    def _boom(connection, *args, **kwargs):
        raise RuntimeError("disk retention exploded")

    monkeypatch.setattr(runtime_module, "drain_remediation_retention", _boom)

    events: list[dict] = []
    monkeypatch.setattr(
        runtime_module,
        "diagnostic_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )

    _new_worker(db_path)._prune_telemetry_if_due()  # No debe propagar la excepcion.

    assert events, "el fallo de poda debe quedar registrado como diagnostico"
    assert events[0]["level"] == "WARNING"
    assert events[0]["error"] is not None


def test_incremental_vacuum_runs_after_retention_when_the_database_supports_it(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "platform.sqlite"
    _seed_db(db_path)

    calls: list[bool] = []
    import local_control_center.workers.runtime as runtime_module

    real_incremental_vacuum = runtime_module.incremental_vacuum_if_enabled

    def _spy(connection, **kwargs):
        result = real_incremental_vacuum(connection, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(runtime_module, "incremental_vacuum_if_enabled", _spy)

    _new_worker(db_path)._prune_telemetry_if_due()

    assert calls == [True]
