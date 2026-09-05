"""Verificación P0 secuencial con recibos incrementales y alcance explícito de huérfanos.

El gate no llama proveedores reales ni transforma configuración ausente en éxito. Cada
comando hijo usa el supervisor nativo y deja stdout/stderr redactados como artefactos.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import uuid
from contextlib import closing
from pathlib import Path

import psutil

from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.process_supervision.context import execution_scope
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.process_supervision.service import ResourceWaitError
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.settings import default_db_path
from local_control_center.shared.time import utc_now

from .__main__ import _emit, _inherited_context, _monitor, _run, _write_report
from .plans import QualityStep


def verification_plan(run_dir: Path) -> list[QualityStep]:
    """Enumera los gates obligatorios; el último siempre ejecuta quality:pr completo."""

    def pytest_step(name: str, *tests: str) -> QualityStep:
        return QualityStep(
            name,
            (sys.executable, "-m", "pytest", "-q", *tests, f"--junitxml={run_dir / (name + '.xml')}"),
            timeout_seconds=1800,
        )

    return [
        QualityStep(
            "migration-upgrade-backup",
            (sys.executable, "-m", "local_control_center.quality.release", "--upgrade-only"),
            "build_heavy",
            900,
        ),
        pytest_step(
            "p0-focused",
            "tests_py/test_operational_hardening_p0.py",
            "tests_py/test_operational_recovery.py",
            "tests_py/test_operational_launchers.py",
            "tests_py/test_durable_executions.py",
            "tests_py/test_codex_capability_contract.py",
            "tests_py/test_quality_tiers.py",
        ),
        pytest_step("api-responsiveness", "tests_py/test_operational_http_boundaries.py"),
        pytest_step("leader-lease", "tests_py/test_worker_leadership.py"),
        pytest_step(
            "resource-admission",
            "tests_py/test_host_resource_governor.py",
            "tests_py/test_branch_resource_admission.py",
        ),
        pytest_step("process-cancellation", "tests_py/test_process_supervision.py"),
        QualityStep(
            "openapi-drift",
            (sys.executable, "local-control-center/scripts/generate_openapi_client.py", "--check"),
        ),
        QualityStep(
            "quality-pr",
            (sys.executable, "-m", "local_control_center.quality", "--tier", "pr"),
            "build_heavy",
            25200,
        ),
    ]


def audit_managed_roots(db_path: Path) -> dict:
    """Inspecciona identidades registradas sin terminar nada; AccessDenied queda unresolved."""
    with closing(open_sqlite_connection(db_path)) as connection:
        records = ManagedProcessRepository(connection).active()
    results = []
    for record in records:

        def alive(pid: int, created: float) -> bool | None:
            if pid <= 0 or created <= 0:
                return None
            try:
                process = psutil.Process(pid)
                return (
                    process.status() != psutil.STATUS_ZOMBIE and abs(process.create_time() - created) < 0.01
                )
            except psutil.NoSuchProcess:
                return False
            except psutil.AccessDenied:
                return None

        owner = alive(record.owner_pid, record.owner_create_time)
        root = alive(record.root_pid, record.root_create_time)
        results.append(
            {
                "managedProcessId": record.managed_process_id,
                "ownerAlive": owner,
                "rootAlive": root,
                "orphan": owner is False and root is True,
            }
        )
    return {
        "checkedAt": utc_now(),
        "scope": "active managed roots in the selected database; descendants additionally asserted by native containment tests",
        "records": results,
        "orphanRootCount": sum(row["orphan"] for row in results),
        "unresolvedCount": sum(row["ownerAlive"] is None or row["rootAlive"] is None for row in results),
        "abandonedRecordCount": sum(
            row["ownerAlive"] is False and row["rootAlive"] is False for row in results
        ),
        "foreignProcessesTerminated": 0,
    }


def main() -> int:
    """Publica reportes honestos aun ante fallo, cancelación, ausencia de recursos o configuración."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path", type=Path, default=Path(os.environ.get("AIDO_QUALITY_DB_PATH") or default_db_path())
    )
    args = parser.parse_args()
    root = Path.cwd().resolve()
    output = root / ".tmp/operational-hardening-p0"
    run_dir = output / f"verify-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True)
    report = {
        "startedAt": utc_now(),
        "status": "running",
        "runDirectory": str(run_dir),
        "steps": [],
        "resourceObservation": {},
        "runtime": {"python": sys.version, "sqlite": sqlite3.sqlite_version},
        "externalSmoke": {
            "status": "configuration_required",
            "commandExecuted": False,
            "missingRequirements": [
                "explicit approval for billable inference",
            ],
            "unverifiedRequirements": [
                "enabled authenticated provider and project policy",
                "fresh runtime health and validated capability contract",
            ],
            "repeat": "See docs/operational-hardening/p0-runbook.md; providers are never enabled by verification.",
        },
    }
    stop = threading.Event()
    monitor = threading.Thread(target=_monitor, args=(stop, report["resourceObservation"]), daemon=True)
    monitor.start()
    code = 1

    def save() -> None:
        _write_report(run_dir / "report.json", report)
        _write_report(output / "report.json", report)

    save()
    try:
        report["preflight"] = (
            HostResourceProbe(relevant_paths=[root, args.db_path.parent]).sample().model_dump(by_alias=True)
        )
        with execution_scope(_inherited_context(args.db_path)):
            for step in verification_plan(run_dir):
                report["activeStep"] = step.name
                save()
                result = _run(step, root=root, db_path=args.db_path)
                report["steps"].append({"name": step.name, **result})
                save()
                if result["returnCode"] != 0 or result["timedOut"] or result["cancelled"]:
                    raise RuntimeError(f"Verification gate failed: {step.name}; exit={result['returnCode']}")
                if step.name in {"process-cancellation", "quality-pr"}:
                    report["processTree"] = audit_managed_roots(args.db_path)
                    tree = report["processTree"]
                    if tree["orphanRootCount"] or tree["unresolvedCount"] or tree["abandonedRecordCount"]:
                        raise RuntimeError(
                            "Managed-process audit requires recovery; no processes were killed."
                        )
        report["status"] = "passed"
        code = 0
    except ResourceWaitError as error:
        report.update(status="resource_wait", error=str(error))
        code = 75
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=redact_secrets(str(error)))
        _emit(report["error"], error=True)
    finally:
        stop.set()
        monitor.join(timeout=3)
        report.update(finishedAt=utc_now(), exitCode=code)
        save()
        for name, value in {
            "process-tree-report.json": report.get("processTree", {"status": "not_run"}),
            "resource-report.json": {"preflight": report.get("preflight"), **report["resourceObservation"]},
            "concurrency-report.json": {
                "steps": [
                    step for step in report["steps"] if step["name"] in {"api-responsiveness", "leader-lease"}
                ]
            },
            "migration-report.json": {
                "steps": [step for step in report["steps"] if step["name"] == "migration-upgrade-backup"]
            },
        }.items():
            _write_report(output / name, value)
        lines = [
            "# AIDO P0 verification",
            "",
            f"Status: {report['status']}; exit: {code}.",
            f"Evidence: {run_dir}",
            "",
            "| Gate | Exit | Managed process |",
            "| --- | --- | --- |",
        ]
        lines.extend(
            f"| {step['name']} | {step['returnCode']} | {step['managedProcessId']} |"
            for step in report["steps"]
        )
        lines.extend(
            [
                "",
                "External smoke: configuration_required; command not executed. See report.json for requirements.",
            ]
        )
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
