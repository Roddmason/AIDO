"""Preserve only the disposable lifecycle fixture, never an operational database.

@author Rodrigo Mason
"""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import psutil

from local_control_center.quality.maintenance import _sqlite_copy
from local_control_center.quality.paths import validate_scratch_parent
from local_control_center.shared.diagnostics import clean


def main():
    """Copy this runner's fixture and report only allowlisted operational state."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("pids", type=int, nargs="+")
    args = parser.parse_args()
    scratch = Path(os.environ["AIDO_QUALITY_SCRATCH"]).resolve()
    retained = Path(os.environ["AIDO_QUALITY_RETAINED"]).resolve()
    source = validate_scratch_parent(args.source, [Path.cwd(), retained])
    target = args.destination.resolve()
    if not source.is_relative_to(scratch) or not source.parent.name.startswith("aido-lifecycle-"):
        raise ValueError("Not this runner's lifecycle fixture")
    if not target.is_relative_to(retained) or target.suffix != ".sqlite":
        raise ValueError("Snapshot must be new retained evidence")
    _sqlite_copy(source, target)
    identities = []
    for pid in args.pids:
        try:
            root = psutil.Process(pid)
            identities.extend(
                {
                    "pid": process.pid,
                    "creationTime": process.create_time(),
                    "username": process.username(),
                    "exe": process.exe(),
                    "parentPid": process.ppid(),
                    "cwd": process.cwd(),
                }
                for process in [root, *root.children(recursive=True)]
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
            identities.append({"pid": pid, "identityStatus": "UNKNOWN", "error": type(error).__name__})
    selected = {
        "jobs": ["id", "kind", "status", "created_at", "updated_at", "lease_owner", "lease_expires_at"],
        "job_runs": ["id", "job_id", "status", "started_at", "completed_at", "summary"],
        "worker_control_state": None,
        "worker_leader_leases": None,
        "worker_heartbeats": None,
        "managed_processes": [
            "managed_process_id",
            "execution_id",
            "root_pid",
            "root_create_time",
            "owner_pid",
            "owner_create_time",
            "started_at",
            "finished_at",
            "exit_code",
            "resource_lease_id",
        ],
        "resource_leases": None,
        "resource_admission_decisions": [
            "execution_id",
            "status",
            "reason_code",
            "reason",
            "created_at",
            "snapshot_json",
        ],
    }
    result = {"fixtureDbPath": str(source), "snapshotPath": str(target), "identities": identities}
    with closing(sqlite3.connect(target)) as connection:
        connection.row_factory = sqlite3.Row
        for table, allowed in selected.items():
            columns = [r[1] for r in connection.execute(f"PRAGMA table_info({table})")]
            columns = [c for c in columns if allowed is None or c in allowed]
            if columns:
                result[table] = [
                    {("fencingToken" if k == "fencing_token" else k): r[k] for k in r.keys()}
                    for r in connection.execute(f"SELECT {','.join(columns)} FROM {table}")
                ]
    print(json.dumps(clean(result)))


if __name__ == "__main__":
    main()
