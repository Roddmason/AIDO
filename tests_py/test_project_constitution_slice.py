"""Slice de constitución del proyecto: versionado, bootstrap, sello en el run y contrato HTTP.

Cubre el ciclo completo del slice 3 del diseño spec-driven (docs/superpowers/specs/2026-07-28 §6):
el documento es fuente de verdad en BD con historial inmutable, el bootstrap deriva de settings sin
pisar jamás lo del operador, cada run del loop sella la versión + hash que lo gobernó, y la API
expone lectura abierta + upsert protegido por token de escritura.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun
from local_control_center.project_constitution.prompt import (
    CONSTITUTION_PROMPT_CHAR_LIMIT,
    render_constitution_prompt,
)
from local_control_center.project_constitution.repository import ProjectConstitutionRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import immediate_transaction, open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _project(connection, tmp_path: Path, name: str):
    return ProjectsRepository(connection).create_project(name=name, path=tmp_path / name, template_id="other")


def test_upsert_versions_document_and_appends_immutable_history(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "const")
        repo = ProjectConstitutionRepository(connection)

        with immediate_transaction(connection):
            first = repo.upsert(
                {
                    "projectId": project["id"],
                    "title": "Reglas",
                    "principles": ["Cambios quirúrgicos"],
                    "nonNegotiables": ["Sin secretos en código"],
                    "changeSummary": "inicial",
                    "authoredBy": "operator",
                }
            )
        assert first["version"] == 1
        assert first["source"] == "operator"

        with immediate_transaction(connection):
            second = repo.upsert(
                {
                    "projectId": project["id"],
                    "title": "Reglas",
                    "principles": ["Cambios quirúrgicos", "Evidencia antes de declarar éxito"],
                    "changeSummary": "agrega evidencia",
                }
            )
        assert second["version"] == 2
        assert second["id"] == first["id"]
        assert second["contentHash"] != first["contentHash"]

        versions = repo.list_versions(first["id"])
        assert [item["version"] for item in versions] == [2, 1]
        assert versions[1]["principles"] == ["Cambios quirúrgicos"]


def test_upsert_rejects_empty_principles_and_unknown_vocabulary(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "invalid")
        repo = ProjectConstitutionRepository(connection)
        with pytest.raises(ValueError, match="at least one principle"):
            repo.upsert({"projectId": project["id"], "principles": ["  "]})
        with pytest.raises(ValueError, match="enforcement"):
            repo.upsert({"projectId": project["id"], "principles": ["x"], "enforcement": "hard"})


def test_bootstrap_derives_goal_and_never_overwrites_operator_document(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "boot")
        repo = ProjectConstitutionRepository(connection)

        with immediate_transaction(connection):
            boot = repo.bootstrap_if_missing(project["id"], goal_statement="Lanzar el MVP")
        assert boot["source"] == "bootstrapped"
        assert boot["enforcement"] == "advisory"
        assert boot["principles"][0] == "Project goal: Lanzar el MVP"

        with immediate_transaction(connection):
            operator_doc = repo.upsert({"projectId": project["id"], "principles": ["Regla del operador"]})
        with immediate_transaction(connection):
            again = repo.bootstrap_if_missing(project["id"], goal_statement="otro goal")
        assert again["version"] == operator_doc["version"]
        assert again["principles"] == ["Regla del operador"]


def test_run_seals_constitution_version_and_hash_into_durable_context(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path, "seal")
        SettingsRepository(connection).set_value(
            key="project.goal.statement", value="Gobernar el loop", scope="project", scope_id=project["id"]
        )
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="Sealed run")
        run = _UserMessageRun(project_id=project["id"], message="hola", actor="operator")
        run.loop = loop

        coordinator._seal_constitution(run)

        assert run.constitution["source"] == "bootstrapped"
        sealed = coordinator.get(loop["id"])["context"]["durableRun"]["constitution"]
        assert sealed["version"] == run.constitution["version"]
        assert sealed["contentHash"] == run.constitution["contentHash"]
        assert sealed["enforcement"] == "advisory"

        # Idempotente: un segundo run reutiliza el documento sin crear versiones nuevas.
        run2 = _UserMessageRun(project_id=project["id"], message="hola", actor="operator")
        run2.loop = coordinator.get(loop["id"])
        coordinator._seal_constitution(run2)
        assert run2.constitution["version"] == run.constitution["version"]


def test_constitution_api_reads_open_and_writes_with_token(tmp_path: Path) -> None:
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    client = TestClient(create_app(runtime=runtime, static_dir=None))
    try:
        project = ProjectsRepository(runtime.connection).create_project(
            name="API const", path=tmp_path / "api-const", template_id="other"
        )
        url = f"/api/v1/projects/{project['id']}/constitution"

        empty = client.get(url)
        assert empty.status_code == 200
        assert empty.json() == {"constitution": None, "versions": []}

        body = {"principles": ["Regla uno"], "nonNegotiables": ["Nada de secretos"], "changeSummary": "v1"}
        assert client.put(url, json=body).status_code == 403

        token = client.get("/api/v1/security/handshake").json()["token"]
        written = client.put(url, json=body, headers={"X-Local-Control-Token": token})
        assert written.status_code == 200
        payload = written.json()
        assert payload["constitution"]["version"] == 1
        assert payload["constitution"]["source"] == "operator"
        assert payload["versions"][0]["changeSummary"] == "v1"

        invalid = client.put(url, json={"principles": []}, headers={"X-Local-Control-Token": token})
        assert invalid.status_code == 422
    finally:
        runtime.close()


def test_prompt_render_is_bounded_and_empty_without_document() -> None:
    assert render_constitution_prompt(None) == ""
    doc = {
        "version": 3,
        "principles": ["p" * 800 for _ in range(6)],
        "nonNegotiables": ["n" * 400],
    }
    rendered = render_constitution_prompt(doc)
    assert rendered.startswith("Project constitution (v3):")
    assert len(rendered) <= CONSTITUTION_PROMPT_CHAR_LIMIT + 40
    assert rendered.endswith("[constitution truncated]")


def test_developer_prompt_is_byte_identical_without_constitution_and_prepends_with_it() -> None:
    """El bloque de constitución es estrictamente opcional: sin él, prompt legado byte a byte."""
    from local_control_center.agents.runtime_registry import developer_agent_prompt

    legacy = developer_agent_prompt(instruction="do x", qa_commands=[["pytest"]])
    explicit_none = developer_agent_prompt(instruction="do x", qa_commands=[["pytest"]], constitution=None)
    assert legacy == explicit_none

    block = "Project constitution (v2):\n- Cambios quirúrgicos"
    with_constitution = developer_agent_prompt(
        instruction="do x", qa_commands=[["pytest"]], constitution=block
    )
    assert with_constitution.index(block) < with_constitution.index("Instruction:")
    assert with_constitution.replace(block + "\n\n", "") == legacy


def test_product_owner_context_carries_constitution_only_when_provided() -> None:
    from local_control_center.agents.product_owner_agent import ProductOwnerAgent

    agent = ProductOwnerAgent()
    without = agent._assessment_context(idea="idea", assessment={})
    assert "projectConstitution" not in without

    with_doc = agent._assessment_context(
        idea="idea", assessment={}, constitution="Project constitution (v1):\n- Regla"
    )
    assert with_doc["projectConstitution"].startswith("Project constitution")


def test_security_analysis_prompt_prepends_constitution() -> None:
    from local_control_center.agents.security_agent import SecurityAgentRunner

    findings = {"findings": [{"id": "f-1"}]}
    plain = SecurityAgentRunner._model_analysis_prompt({}, findings)
    with_doc = SecurityAgentRunner._model_analysis_prompt(
        {"constitution": "Project constitution (v1):\n- Regla"}, findings
    )
    assert plain in with_doc
    assert with_doc.startswith("Project constitution")


def test_architect_review_context_includes_constitution_only_when_provided(tmp_path: Path) -> None:
    from local_control_center.agents.architect_agent import ArchitectAgentRunner

    runner = ArchitectAgentRunner.__new__(ArchitectAgentRunner)
    without = runner._messages(payload={"diffArtifactId": "artifact-1"}, diff_text="diff")
    assert '"constitution"' not in without[1]["content"]

    with_doc = runner._messages(
        payload={"diffArtifactId": "artifact-1", "constitution": "Project constitution (v1):\n- Regla"},
        diff_text="diff",
    )
    assert '"constitution"' in with_doc[1]["content"]
