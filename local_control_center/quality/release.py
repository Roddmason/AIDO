"""Instalación aislada, upgrade real desde el baseline y backup/restore sin promover datos.

Usa directorios temporales propios, locks de dependencias y árboles supervisados secuenciales.
No cambia la instalación del operador, no activa proveedores y no ejecuta inferencia facturable.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from contextlib import closing
from pathlib import Path

import httpx

from local_control_center.process_supervision.context import execution_scope
from local_control_center.process_supervision.evidence import process_evidence
from local_control_center.process_supervision.service import (
    ProcessSupervisorService,
    terminate_managed_process,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import CURRENT_SCHEMA_VERSION, initialize_platform_schema
from local_control_center.shared.settings import default_db_path
from local_control_center.shared.time import utc_now

from .__main__ import _emit, _inherited_context, _prepare_paths, _run, _write_progress, _write_report
from .maintenance import backup_bundle, restore_bundle
from .plans import QualityStep

BASELINE_REVISION = "94eaf8c1756f17493c7f80af359d86cb9561816c"


def upgrade_from_baseline(
    root: Path, temporary: Path, db_path: Path, report: dict, environment: dict[str, str] | None = None
) -> None:
    """Crea una base con código baseline real, la respalda y migra la restauración dos veces."""
    archive = temporary / "baseline.zip"
    result = _run(
        QualityStep(
            "baseline-archive", ("git", "archive", "--format=zip", f"--output={archive}", BASELINE_REVISION)
        ),
        root=root,
        db_path=db_path,
        environment=environment,
    )
    report["steps"].append({"name": "baseline-archive", **result})
    if result["returnCode"] != 0:
        raise RuntimeError("Baseline revision is unavailable; upgrade verification was not run.")
    checkout = temporary / "baseline-source"
    checkout.mkdir()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            if not (checkout / member.filename).resolve().is_relative_to(checkout.resolve()):
                raise ValueError("Archive path escapes the isolated baseline directory.")
        source.extractall(checkout)
    original = temporary / "original" / "platform.sqlite"
    bootstrap = (
        "import sys; from pathlib import Path; "
        "from local_control_center.control_plane.runtime import ControlCenterRuntime; "
        "runtime=ControlCenterRuntime(cwd=Path.cwd(),db_path=Path(sys.argv[1])); "
        "runtime.init(); runtime.ensure_runtime_project(); runtime.close()"
    )
    result = _run(
        QualityStep("baseline-bootstrap", (sys.executable, "-c", bootstrap, str(original))),
        root=checkout,
        db_path=db_path,
        environment=environment,
    )
    report["steps"].append({"name": "baseline-bootstrap", **result})
    if result["returnCode"] != 0:
        raise RuntimeError("The baseline application could not create its database.")
    with closing(open_sqlite_connection(original)) as connection:
        before = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        projects = [
            tuple(row) for row in connection.execute("SELECT id, name, path FROM projects ORDER BY id")
        ]
    manifest = backup_bundle(original, temporary / "baseline-backup")
    restored = restore_bundle(temporary / "baseline-backup", temporary / "upgraded")
    with closing(open_sqlite_connection(restored)) as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        after = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if (
            after != CURRENT_SCHEMA_VERSION
            or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or connection.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise RuntimeError("Upgrade integrity/schema verification failed.")
        if projects != [
            tuple(row) for row in connection.execute("SELECT id, name, path FROM projects ORDER BY id")
        ]:
            raise RuntimeError("Upgrade changed existing project records.")
    backup_bundle(restored, temporary / "upgraded-backup")
    roundtrip = restore_bundle(temporary / "upgraded-backup", temporary / "roundtrip")
    with closing(open_sqlite_connection(roundtrip)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == after
    report["migration"] = {
        "status": "passed",
        "baselineRevision": BASELINE_REVISION,
        "schemaBefore": before,
        "schemaAfter": after,
        "applications": 2,
        "projectRowsPreserved": len(projects),
        "backupFormat": manifest["formatVersion"],
        "restoreRoundtrip": "passed",
        "upgradeDatabaseScope": "isolated baseline copy; operator domain data was not migrated",
        "supervisionDatabaseWrites": True,
    }


def native_api_smoke(root: Path, db_path: Path) -> dict:
    """Comprueba el launcher instalado sobre HTTP local y termina sólo su árbol nativo propio."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    service = ProcessSupervisorService(db_path=db_path)
    managed = service.start(
        argv=[
            sys.executable,
            "local-control-center/scripts/start_control_center.py",
            "--mode",
            "api",
            "--no-build",
            "--dashboard-host",
            "127.0.0.1",
            "--dashboard-port",
            str(port),
            "--db-path",
            str(root / "native-smoke.sqlite"),
            "--workspace",
            str(root),
        ],
        cwd=root,
        workload_class="qa_light",
        env={**os.environ, "AIDO_ENABLE_REAL_PROVIDER_CALLS": "false", "AIDO_ENABLE_CLI_RUNTIMES": "false"},
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    try:
        deadline = time.monotonic() + 60
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
            while time.monotonic() < deadline:
                if managed.process.poll() is not None:
                    raise RuntimeError(f"Native API exited before readiness: {managed.process.returncode}")
                try:
                    health = client.get("/healthz")
                    dashboard = client.get("/")
                    if health.status_code == 200 and dashboard.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Native API/dashboard did not become ready within 60 seconds.")
    finally:
        try:
            stats = terminate_managed_process(managed.process, reason="native_smoke_cleanup", grace_seconds=2)
        finally:
            record = service.complete(
                managed, exit_code=managed.process.poll(), termination_reason="native_smoke_cleanup"
            )
    if stats.remaining_descendant_count:
        raise RuntimeError("Native API smoke left descendants in its managed container.")
    with closing(open_sqlite_connection(db_path)) as connection:
        return {
            "status": "passed",
            "healthHttpStatus": health.status_code,
            "dashboardHttpStatus": dashboard.status_code,
            "remainingDescendantCount": 0,
            "process": process_evidence(connection, record),
        }


def clean_install(
    root: Path,
    temporary: Path,
    db_path: Path,
    report: dict,
    report_path: Path,
    environment: dict[str, str] | None = None,
) -> None:
    """Instala Python y frontend desde locks en un árbol nuevo y comprueba API y build reales."""
    install = temporary / "clean-install"
    install.mkdir()
    for relative in (
        "local_control_center",
        "local-control-center/web",
        "local-control-center/scripts",
        "scripts",
    ):
        shutil.copytree(
            root / relative,
            install / relative,
            ignore=shutil.ignore_patterns("__pycache__", "node_modules", ".tmp", "dist"),
        )
    for name in (
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        ".npmrc",
    ):
        if (root / name).is_file():
            shutil.copy2(root / name, install / name)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("configuration_required: Node is not available.")
    corepack = Path(node).parent / "node_modules/corepack/dist/corepack.js"
    manager = (node, str(corepack), "pnpm@10.24.0") if corepack.is_file() else ("corepack", "pnpm@10.24.0")
    python = install / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    smoke = (
        "from pathlib import Path; from fastapi.testclient import TestClient; "
        "from local_control_center.app import create_app; "
        "from local_control_center.control_plane.runtime import ControlCenterRuntime; "
        "r=ControlCenterRuntime(cwd=Path.cwd(),db_path=Path('smoke.sqlite')); "
        "app=create_app(runtime=r,static_dir=None); "
        "client=TestClient(app); response=client.get('/healthz'); "
        "assert response.status_code==200, response.text; client.close(); r.close()"
    )
    steps = [
        QualityStep(
            "clean-python-install",
            ("uv", "sync", "--locked", "--extra", "test", "--python", sys._base_executable),
            "build_heavy",
            1800,
        ),
        QualityStep("clean-api-smoke", (str(python), "-c", smoke)),
        QualityStep("clean-web-install", (*manager, "install", "--frozen-lockfile"), "build_heavy", 1800),
        QualityStep(
            "clean-web-build",
            (
                node,
                "node_modules/vite/bin/vite.js",
                "build",
                "--config",
                "local-control-center/web/vite.config.ts",
            ),
            "build_heavy",
            900,
        ),
        QualityStep(
            "clean-native-api",
            (
                str(python),
                "-c",
                "import json; from pathlib import Path; "
                "from local_control_center.quality.release import native_api_smoke; "
                "from local_control_center.quality.__main__ import _inherited_context; "
                "from local_control_center.process_supervision.context import execution_scope; "
                "import os; db=Path(os.environ['AIDO_QUALITY_DB_PATH']); "
                "\nwith execution_scope(_inherited_context(db)):\n"
                " print(json.dumps(native_api_smoke(Path.cwd(),db)))",
            ),
            "qa_light",
            120,
        ),
    ]
    for step in steps:
        report["activeStep"] = step.name
        _write_progress(report_path, report)
        result = _run(step, root=install, db_path=db_path, environment=environment)
        report["steps"].append({"name": step.name, **result})
        _write_progress(report_path, report)
        if result["returnCode"] != 0 or result["timedOut"] or result["cancelled"]:
            raise RuntimeError(f"Clean installation failed at {step.name}.")
    report["cleanInstall"] = "passed"


def main() -> int:
    """Ejecuta sólo validación aislada y conserva un reporte sin datos sensibles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade-only", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    db_path = Path(os.environ.get("AIDO_QUALITY_DB_PATH") or default_db_path())
    path = root / ".tmp/operational-hardening-p0" / f"release-{uuid.uuid4().hex}.json"
    report = {"startedAt": utc_now(), "status": "running", "steps": []}
    _write_progress(path, report)
    code = 1
    try:
        with execution_scope(_inherited_context(db_path)):
            report["paths"], environment = _prepare_paths(
                root,
                db_path,
                path.with_suffix(""),
                Path(
                    os.environ.get(
                        "AIDO_QUALITY_TEMP_ROOT", str(Path(tempfile.gettempdir()) / "aido-quality")
                    )
                ),
            )
            with tempfile.TemporaryDirectory(
                prefix="aido-release-", dir=report["paths"]["scratch"]
            ) as directory:
                temporary = Path(directory)
                upgrade_from_baseline(root, temporary, db_path, report, environment)
                _write_progress(path, report)
                if not args.upgrade_only:
                    clean_install(root, temporary, db_path, report, path, environment)
        report["status"] = "passed"
        code = 0
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=str(error))
        _emit(str(error), error=True)
    finally:
        report.update(finishedAt=utc_now(), exitCode=code)
        _write_report(path, report)
        _emit(f"Release report: {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
