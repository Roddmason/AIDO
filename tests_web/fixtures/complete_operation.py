"""One-shot domain fixture: consumes one real 202 operation outside the HTTP server.

Uses the deterministic domain resource snapshot, not production worker scheduling. The outer
quality runner still admits and contains the complete browser tree using live host resources.
Never claims conversation jobs or enables providers. OS dispatch/fencing has separate native tests.
"""

import argparse
from pathlib import Path

from local_control_center.api import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime
from tests_py.execution_client import complete_operation


def main():
    """Complete a selected operation only in the runner's isolated domain-test database."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    db = Path(args.db).resolve()
    if not db.is_relative_to(Path(".tmp").resolve()) or not db.name.startswith("playwright-"):
        raise ValueError("Domain fixture requires an isolated .tmp/playwright- database")
    platform = ControlCenterRuntime(cwd=Path.cwd(), db_path=db)
    try:
        create_app(runtime=platform, static_dir=None)
        complete_operation(platform, args.execution_id)
    finally:
        platform.close()


if __name__ == "__main__":
    main()
