"""Ejecutor secuencial de quality tiers con árboles supervisados y reportes recuperables.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import psutil

from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ResourceWaitError, run_supervised_capture
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import publish_json_exclusive
from local_control_center.shared.settings import default_db_path
from local_control_center.shared.time import utc_now

from .paths import QualityPaths, inherited_paths, require_non_repository, validate_scratch_parent
from .plans import QualityStep, build_plan

ADMISSION_WAIT_SECONDS = 120
ADMISSION_RETRY_SECONDS = 5


def _inherited_context(db_path: Path) -> ProcessExecutionContext:
    """Hereda sólo una lease ligada a un ancestro nativo vivo, no a IDs suministrados por entorno."""
    from local_control_center.shared.migrations import initialize_platform_schema

    with closing(open_sqlite_connection(db_path)) as connection:
        initialize_platform_schema(connection)
        for ancestor in psutil.Process().parents():
            row = connection.execute(
                """SELECT p.*, l.owner_id AS lease_owner, l.workload_class AS lease_class
                FROM managed_processes p JOIN resource_leases l ON l.id=p.resource_lease_id
                WHERE p.root_pid=? AND p.finished_at IS NULL AND p.released_at IS NULL
                AND l.released_at IS NULL AND l.expires_at>?""",
                (ancestor.pid, utc_now()),
            ).fetchone()
            if row and abs(ancestor.create_time() - row["root_create_time"]) < 0.01:
                job = connection.execute(
                    "SELECT lease_owner FROM jobs WHERE id=?", (row["execution_id"],)
                ).fetchone()
                leader = connection.execute(
                    "SELECT owner_id, fencing_token FROM worker_leader_leases WHERE expires_at>?",
                    (utc_now(),),
                ).fetchone()
                if job and (not leader or leader["owner_id"] != job["lease_owner"]):
                    raise RuntimeError("The parent execution no longer owns worker leadership.")
                return ProcessExecutionContext(
                    db_path=db_path,
                    execution_id=row["execution_id"],
                    resource_lease_id=row["resource_lease_id"],
                    worker_id=leader["owner_id"] if job else None,
                    fencing_token=leader["fencing_token"] if job else None,
                )
    return ProcessExecutionContext(db_path=db_path)


def _write_report(path: Path, report: dict) -> None:
    publish_json_exclusive(path, redact_secrets(report))


def _write_progress(path: Path, report: dict) -> None:
    """Retain immutable checkpoints; only the final receipt occupies the announced path."""
    checkpoint = path.parent / (path.stem + "-progress") / f"{uuid.uuid4().hex}.json"
    _write_report(checkpoint, {**report, "finalReportPath": str(path)})


def _emit(value: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    encoding = stream.encoding or "utf-8"
    print(value.encode(encoding, errors="backslashreplace").decode(encoding), file=stream, flush=True)


def _run(
    step: QualityStep,
    *,
    root: Path,
    db_path: Path,
    display_output: bool = True,
    environment: dict[str, str] | None = None,
) -> dict:
    environment = dict(os.environ if environment is None else environment)
    arguments = step.argv
    if "pytest" in arguments:
        paths = inherited_paths(environment)
        if paths is None:
            raise ValueError("Pytest requires an isolated quality invocation")
        arguments, environment = paths.prepare_pytest(arguments, environment)
    executable = shutil.which(step.argv[0])
    if not executable:
        raise RuntimeError(f"configuration_required: missing executable {step.argv[0]}")
    _emit(f"[{utc_now()}] {step.name} ({step.workload_class})")
    deadline = time.monotonic() + ADMISSION_WAIT_SECONDS
    while True:
        try:
            result = run_supervised_capture(
                [executable, *arguments[1:]],
                cwd=root,
                db_path=db_path,
                workload_class=step.workload_class,
                timeout_seconds=step.timeout_seconds,
                environment={**environment, "AIDO_QUALITY_DB_PATH": str(db_path.resolve())},
            )
            break
        except ResourceWaitError as error:
            # Admission failed before spawn. Never retry a command that actually executed.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            _emit(f"[{utc_now()}] {step.name}: {redact_secrets(str(error))}")
            time.sleep(min(ADMISSION_RETRY_SECONDS, remaining))
    for stream in ("stdout", "stderr"):
        if display_output and result.get(stream):
            _emit(redact_secrets(result[stream]))
    return {
        **result,
        "qualityCommand": [executable, *arguments[1:]],
        "qualityInvocationId": environment.get("AIDO_QUALITY_INVOCATION_ID"),
        "qualityAttemptId": environment.get("AIDO_QUALITY_ATTEMPT_ID"),
    }


def _changed_files(root: Path, db_path: Path, base: str) -> tuple[list[str], list[str], str]:
    revision = _run(
        QualityStep(
            "base-revision", ("git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
        ),
        root=root,
        db_path=db_path,
        display_output=False,
    )
    if revision["returnCode"] != 0:
        raise ValueError("The quality base must resolve to an existing commit.")
    base = revision["stdout"].strip()
    changed = _run(
        QualityStep("changed-files", ("git", "diff", "--name-only", "-z", "--diff-filter=ACMR", base, "--")),
        root=root,
        db_path=db_path,
        display_output=False,
    )
    untracked = _run(
        QualityStep("untracked-files", ("git", "ls-files", "--others", "--exclude-standard", "-z")),
        root=root,
        db_path=db_path,
        display_output=False,
    )
    if any(result["returnCode"] != 0 or result["stdoutCaptureTruncated"] for result in (changed, untracked)):
        raise RuntimeError("Unable to obtain complete changed-file inventory.")
    new = [value for value in untracked["stdout"].split("\0") if value and (root / value).is_file()]
    return sorted(set([value for value in changed["stdout"].split("\0") if value] + new)), new, base


def _prepare_paths(root: Path, db_path: Path, evidence: Path, parent: Path) -> tuple[dict, dict[str, str]]:
    """Validate the checkout and all worktrees before preparing a public runner invocation."""
    from local_control_center.shared.diagnostics import configure_diagnostics

    parent = validate_scratch_parent(parent, [root, db_path.parent])
    configure_diagnostics(evidence / "runner-diagnostics")
    worktrees = _run(
        QualityStep("worktree-context", ("git", "worktree", "list", "--porcelain")),
        root=root,
        db_path=db_path,
        display_output=False,
    )
    if worktrees["returnCode"] != 0 or worktrees["stdoutCaptureTruncated"]:
        raise ValueError("Cannot validate worktree boundaries")
    protected = [
        root,
        db_path.parent,
        *[Path(line[9:]) for line in worktrees["stdout"].splitlines() if line.startswith("worktree ")],
    ]
    paths = QualityPaths.create(parent, evidence, protected)
    env = {
        **paths.environment(dict(os.environ)),
        "AIDO_QUALITY_TEMP_ROOT": str(parent),
        "AIDO_TEST_QUALITY_DB": str(db_path.resolve()),
    }
    check = _run(
        QualityStep("scratch-git-context", ("git", "rev-parse", "--show-toplevel")),
        root=paths.scratch,
        db_path=db_path,
        display_output=False,
        environment={**env, "LC_ALL": "C"},
    )
    require_non_repository(check["returnCode"], check["stderr"])
    return {
        "invocationId": paths.invocation_id,
        "scratch": str(paths.scratch),
        "evidence": str(paths.evidence),
        "runnerDiagnostics": str(evidence / "runner-diagnostics"),
    }, env


def _monitor(stop: threading.Event, report: dict) -> None:
    """Mide el host durante el gate; no confunde estos samples con el peak del Job Object."""
    psutil.cpu_percent()
    while not stop.wait(2):
        cpu = psutil.cpu_percent()
        available = psutil.virtual_memory().available
        report["hostCpuPeakPercent"] = max(report.get("hostCpuPeakPercent") or 0, cpu)
        report["hostAvailableMemoryMinimumBytes"] = min(
            report.get("hostAvailableMemoryMinimumBytes") or available, available
        )
        report["sampleCount"] = report.get("sampleCount", 0) + 1


def main(argv: list[str] | None = None) -> int:
    """Ejecuta el tier seleccionado y conserva su evidencia aun cuando un gate falle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("fast", "story", "pr", "release"), default="pr")
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--python-test", action="append", default=[])
    parser.add_argument("--web-test", action="append", default=[])
    parser.add_argument(
        "--temporary-root",
        type=Path,
        default=Path(os.environ["AIDO_QUALITY_TEMP_ROOT"])
        if os.environ.get("AIDO_QUALITY_TEMP_ROOT")
        else Path(tempfile.gettempdir()) / "aido-quality",
    )
    parser.add_argument(
        "--db-path", type=Path, default=Path(os.environ.get("AIDO_QUALITY_DB_PATH") or default_db_path())
    )
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    report_path = root / ".tmp/operational-hardening-p0" / f"quality-{args.tier}-{uuid.uuid4().hex}.json"
    report = {
        "tier": args.tier,
        "startedAt": utc_now(),
        "status": "running",
        "steps": [],
        "resourceObservation": {
            "hostCpuPeakPercent": None,
            "hostAvailableMemoryMinimumBytes": None,
            "sampleCount": 0,
        },
    }
    stop = threading.Event()
    monitor = threading.Thread(target=_monitor, args=(stop, report["resourceObservation"]), daemon=True)
    _write_progress(report_path, report)
    _emit(f"Quality report: {report_path}")
    monitor.start()
    exit_code = 1
    try:
        report["preflight"] = (
            HostResourceProbe(relevant_paths=[root, args.db_path.parent]).sample().model_dump(by_alias=True)
        )
        with execution_scope(_inherited_context(args.db_path)):
            report["paths"], env = _prepare_paths(
                root, args.db_path, report_path.with_suffix(""), args.temporary_root
            )
            changed, untracked, base_commit = (
                _changed_files(root, args.db_path, args.base)
                if args.tier in {"fast", "story"}
                else ([], [], "HEAD")
            )
            plan = build_plan(
                root, args.tier, changed_files=changed, python_tests=args.python_test, web_tests=args.web_test
            )
            if args.tier in {"fast", "story"}:
                plan.extend(
                    QualityStep(
                        f"secrets-untracked-{index}",
                        ("gitleaks", "dir", "--config", ".gitleaks.toml", "--redact", path),
                    )
                    for index, path in enumerate(untracked)
                )
                if args.base != "HEAD":
                    # Git receives the revision as one argument; never as a shell command.
                    plan.append(
                        QualityStep(
                            "secrets-branch",
                            (
                                "gitleaks",
                                "git",
                                "--config",
                                ".gitleaks.toml",
                                "--redact",
                                f"--log-opts={base_commit}..HEAD",
                                ".",
                            ),
                        )
                    )
            for step in plan:
                report["activeStep"] = step.name
                _write_progress(report_path, report)
                result = _run(step, root=root, db_path=args.db_path, environment=env)
                report["steps"].append({"name": step.name, **result})
                _write_progress(report_path, report)
                if result["returnCode"] != 0 or result["timedOut"] or result["cancelled"]:
                    raise RuntimeError(f"Gate failed: {step.name}; exit={result['returnCode']}")
        report["status"] = "passed"
        exit_code = 0
    except ResourceWaitError as error:
        report.update(status="resource_wait", error=str(error))
        exit_code = 75
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=redact_secrets(str(error)))
        _emit(report["error"], error=True)
    finally:
        stop.set()
        monitor.join(timeout=3)
        report.update(finishedAt=utc_now(), exitCode=exit_code)
        _write_report(report_path, report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
