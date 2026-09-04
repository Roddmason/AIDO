"""Proyección de evidencia nativa y hashes de artefactos, sin guardar argv ni prompts.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .models import ManagedProcessRecord


def process_evidence(connection: sqlite3.Connection, record: ManagedProcessRecord) -> dict:
    """Une el registro exacto del proceso con sus capturas completas verificables."""
    duration = None
    if record.finished_at:
        duration = max(
            0,
            int(
                (
                    datetime.fromisoformat(record.finished_at.replace("Z", "+00:00"))
                    - datetime.fromisoformat(record.started_at.replace("Z", "+00:00"))
                ).total_seconds()
                * 1000
            ),
        )
    evidence = {
        "managedProcessId": record.managed_process_id,
        "executionId": record.execution_id,
        "resourceLeaseId": record.resource_lease_id,
        "workloadClass": record.workload_class,
        "commandFingerprint": record.command_fingerprint,
        "startedAt": record.started_at,
        "finishedAt": record.finished_at,
        "durationMs": duration,
        "returnCode": record.exit_code,
        "timedOut": record.timed_out,
        "cancelled": record.cancelled,
        "peakMemoryBytes": record.peak_memory_bytes,
        "cpuTimeSeconds": record.cpu_time_seconds,
        "terminationReason": record.termination_reason,
    }
    for stream, artifact_id in (("stdout", record.stdout_artifact_id), ("stderr", record.stderr_artifact_id)):
        artifact = connection.execute("SELECT hash FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        evidence[f"{stream}ArtifactId"] = artifact_id
        evidence[f"{stream}Sha256"] = artifact["hash"] if artifact else None
    return evidence
