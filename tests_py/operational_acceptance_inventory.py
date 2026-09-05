"""Inventario reproducible y adopción de copias; nunca inicializa la base operativa.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from local_control_center.quality.maintenance import backup_bundle, restore_bundle
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.operational_acceptance_support import save

ORIGINALS = [
    "verify-db869fa39a224ccebb7f9e29a65fc92c/report.json",
    "quality-pr-25153469e0d14808986260431e26c1ec.json",
    "release-9a28f64605f24fc78249d912828d7654.json",
    "final-clean-install.json",
    "post-commit-process-audit.json",
]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, check=True, timeout=30)
    return result.stdout.decode("utf-8")


def source_manifest():
    files = sorted(
        set(
            git("ls-files", "-z").split("\0")
            + git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
        )
    )
    hashes = {name: digest(name) for name in files if name and Path(name).is_file()}
    return {
        "head": git("rev-parse", "HEAD").strip(),
        "branch": git("branch", "--show-current").strip(),
        "status": git("status", "--short"),
        "files": hashes,
        "productiveHash": hashlib.sha256(
            json.dumps(
                {
                    p: h
                    for p, h in hashes.items()
                    if p.startswith(("local_control_center/", "local-control-center/", "scripts/"))
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ["fastapi", "uvicorn", "psutil", "pywin32", "pytest", "pydantic"]
        },
    }


def inspect_originals(source: Path):
    report = {
        "receipts": [],
        "artifacts": [],
        "historicalTraceability": "FAIL: no source-content manifest in original receipts",
    }
    root = Path(".tmp/operational-hardening-p0")
    artifact_root = source.parent / ".tmp/evidence-artifacts"
    for relative in ORIGINALS:
        path = root / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        report["receipts"].append(
            {
                "path": str(path),
                "sha256": digest(path),
                "status": data.get("status"),
                "exitCode": data.get("exitCode", data.get("returnCode")),
                "startedAt": data.get("startedAt"),
                "finishedAt": data.get("finishedAt"),
                "resourceObservation": data.get("resourceObservation"),
                "stepOutcomes": [
                    {
                        k: step.get(k)
                        for k in [
                            "name",
                            "returnCode",
                            "durationMs",
                            "cpuTimeSeconds",
                            "workloadClass",
                            "resourceLeaseId",
                            "peakMemoryBytes",
                            "remainingDescendantCount",
                        ]
                    }
                    for step in data.get("steps", [])
                ],
            }
        )
        for step in data.get("steps", [data]):
            for stream in ["stdout", "stderr"]:
                identity = step.get(f"{stream}ArtifactId")
                if not identity:
                    continue
                artifact = artifact_root / f"{identity}.{stream}.log"
                actual = digest(artifact)
                expected = step[f"{stream}Sha256"]
                report["artifacts"].append(
                    {
                        "id": identity,
                        "path": str(artifact),
                        "sha256": actual,
                        "matchesReceipt": actual == expected,
                        "sizeBytes": artifact.stat().st_size,
                    }
                )
                assert actual == expected, identity
    return report


def database_summary(connection):
    names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {
        "schema": connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
        "rowCounts": {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in sorted(names)
            if not table.startswith("sqlite_")
        },
        "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreignKeyViolationCount": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
    }


def adopt_copy(source: Path, destination: Path):
    # Backup uses a brief write lock to coordinate both SQLite files; it performs no migration
    # or operational row mutation. All initialization below targets newly restored copies only.
    before = digest(source)
    manifest = backup_bundle(source, destination / "consistent-backup")
    adopted = restore_bundle(destination / "consistent-backup", destination / "adoption")
    with closing(open_sqlite_connection(adopted)) as connection:
        old = database_summary(connection)
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        new = database_summary(connection)
    restored = restore_bundle(destination / "consistent-backup", destination / "rollback")
    with closing(open_sqlite_connection(restored)) as connection:
        rollback = database_summary(connection)
    assert old == new == rollback
    assert digest(source) == before
    return {
        "status": "PASS" if new["foreignKeyViolationCount"] == 0 else "FAIL",
        "copyAndRollbackStatus": "PASS",
        "source": str(source),
        "sourceUnchangedSha256": before,
        "bundle": manifest,
        "adopted": str(adopted),
        "rollback": str(restored),
        "before": old,
        "after": new,
        "rollbackSummary": rollback,
        "limitations": "Schema 67 operational copy; not a historical schema 58 dataset; external files/credentials not copied or decrypted",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adoption-root", type=Path)
    args = parser.parse_args()
    result = {"sourceManifest": source_manifest(), "originalEvidence": inspect_originals(args.source)}
    if args.adoption_root:
        result["adoption"] = adopt_copy(args.source, args.adoption_root)
    save(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "artifactsVerified": len(result["originalEvidence"]["artifacts"]),
                "productiveHash": result["sourceManifest"]["productiveHash"],
                "adoption": result.get("adoption", {}).get("status", "NOT_RUN"),
            }
        )
    )
