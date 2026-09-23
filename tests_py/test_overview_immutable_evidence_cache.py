"""El overview reutiliza la compactación de filas inmutables entre polls sin cambiar su salida.

Medido sobre una copia de la BD viva: decodificar ~118 MB de JSON de evidencia por poll costaba
~2 s de los 2,8 s del overview. Esas columnas no se actualizan tras el INSERT.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.control_plane import overview as overview_module
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

HEAVY = "x" * (overview_module.OVERVIEW_EMBEDDED_VALUE_BYTE_LIMIT * 3)


def _seed(connection, tmp_path: Path) -> tuple[EvidenceRepository, dict]:
    project = ProjectsRepository(connection).create_project(
        name="Overview cache", path=tmp_path / "project", template_id="other"
    )
    evidence = EvidenceRepository(connection)
    package = evidence.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        test_results=[{"command": "pytest", "status": "passed", "metadata": {"blob": HEAVY}}],
        logs=[HEAVY],
        diff_refs=[{"path": "a.py", "patch": HEAVY}],
    )
    return evidence, package


def test_overview_evidence_equals_the_full_compaction(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        evidence, _package = _seed(connection, tmp_path)

        snapshot = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)
        expected_packages = [
            overview_module._compact_overview_record(record)
            for record in evidence.list_evidence_packages(limit=overview_module.OVERVIEW_EVIDENCE_LIMIT)
        ]
        expected_results = [
            overview_module._compact_overview_record(record)
            for record in evidence.list_all_test_results(limit=overview_module.OVERVIEW_TEST_RESULT_LIMIT)
        ]

    assert snapshot["evidencePackages"] == expected_packages
    assert snapshot["testResultRecords"] == expected_results
    assert snapshot["evidencePackages"][0]["testResults"][0]["status"] == "passed"


def test_second_overview_does_not_reread_immutable_evidence(tmp_path: Path, monkeypatch) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        evidence, package = _seed(connection, tmp_path)
        first = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)

        def refuse(_self, ids):
            if ids:
                raise AssertionError(f"immutable evidence re-read: {ids}")
            return {}

        monkeypatch.setattr(EvidenceRepository, "immutable_evidence_fields", refuse)
        monkeypatch.setattr(EvidenceRepository, "load_test_results", refuse)
        second = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)
        evidence.update_evidence_links(package["id"], qa_verdict="passed")
        third = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)

    assert second["evidencePackages"] == first["evidencePackages"]
    assert second["testResultRecords"] == first["testResultRecords"]
    assert third["evidencePackages"][0]["qaVerdict"] == "passed"
