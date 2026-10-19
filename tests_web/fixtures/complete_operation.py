"""One-shot domain fixture: consumes one real 202 operation outside the HTTP server.

Uses the deterministic domain resource snapshot, not production worker scheduling. The outer
quality runner still admits and contains the complete browser tree using live host resources.
Never claims conversation jobs or enables providers. OS dispatch/fencing has separate native tests.
"""

import argparse
import os
from pathlib import Path


def fixture_database(value: str) -> Path:
    """Accept only this runner's exact chunk DB, outside checkout and retained evidence."""
    from local_control_center.quality.paths import validate_scratch_parent

    scratch = os.environ.get("AIDO_QUALITY_SCRATCH")
    retained = os.environ.get("AIDO_QUALITY_RETAINED")
    expected = os.environ.get("PLAYWRIGHT_DB_PATH")
    if not all((scratch, retained, expected)):
        raise ValueError("Domain fixture requires the supervised runner's scratch and chunk identity")
    protected = [Path.cwd(), Path(retained)]
    root = validate_scratch_parent(Path(scratch), protected)
    db = validate_scratch_parent(Path(value), protected)
    if (
        not db.is_relative_to(root)
        or db != Path(expected).resolve()
        or not db.name.startswith("playwright-")
        or not db.is_file()
    ):
        raise ValueError("Domain fixture DB does not match the isolated runner chunk")
    return db


def cap_hard_memory_floor():
    """Apply the pytest suite's bounded watchdog floor to this one-shot process.

    The chunk DB keeps the 8 GiB product default while the watchdog compares it against the real
    host memory: on a shared CI VM (~3 GiB free) it cancelled workspace/workflow operations with
    `hard_memory_floor` (ci-hub build #27). Same bound and env override as `tests_py/conftest.py`.
    """
    from local_control_center.process_supervision import service
    from tests_py.conftest import TEST_HARD_MEMORY_FLOOR_BYTES

    original = service.hard_memory_floor_bytes
    service.hard_memory_floor_bytes = lambda connection: min(
        original(connection), TEST_HARD_MEMORY_FLOOR_BYTES
    )


def main():
    """Complete a selected operation only in the runner's isolated domain-test database."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    db = fixture_database(args.db)
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.executions.registration import register_operation
    from local_control_center.executions.repository import ExecutionRepository
    from tests_py.execution_client import complete_operation

    cap_hard_memory_floor()
    platform = ControlCenterRuntime(cwd=Path.cwd(), db_path=db)
    try:
        execution = ExecutionRepository(platform.connection).get(args.execution_id)
        register_operation(platform, execution["operation"])
        complete_operation(platform, args.execution_id)
    finally:
        platform.close()


if __name__ == "__main__":
    main()
