"""A diagnostic HTTP correlation context is not execution authority."""

from contextlib import closing

from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def test_http_correlation_does_not_enable_runtime_probes(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "readonly.sqlite")) as connection:
        initialize_platform_schema(connection)
        with execution_scope(
            ProcessExecutionContext(db_path=tmp_path / "readonly.sqlite", request_id="http-readonly")
        ):
            assert RuntimeStatusService(connection).allow_probes is False


def test_explicit_runtime_probe_remains_enabled(tmp_path):
    with closing(open_sqlite_connection(tmp_path / "explicit.sqlite")) as connection:
        initialize_platform_schema(connection)
        assert RuntimeStatusService(connection, allow_probes=True).allow_probes is True
        with execution_scope(
            ProcessExecutionContext(db_path=tmp_path / "explicit.sqlite", in_job_runner=True)
        ):
            assert RuntimeStatusService(connection).allow_probes is True
