"""Integracion real: el QAAgent provisiona `node_modules` en un workspace Node antes de correr QA.

Usa una copia del sandbox `aido-e2e-sandbox` (pnpm + vitest, store de pnpm ya caliente) como
checkout principal de un proyecto AIDO: el git worktree que `allocate_workspace` crea para el QA
nunca trae `node_modules` (esta en `.gitignore`), que es exactamente el bug reportado en vivo.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import shutil
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.agents.qa_agent import QAAgentRunner
from local_control_center.projects.toolchain import NODE_NO_LOCKFILE_REASON, node_lockfile_sha256
from local_control_center.security_policy.git_command_runner import git_available, run_git
from tests_py.control_plane_fixture import ControlPlaneFixture

_SANDBOX = Path(os.environ.get("AIDO_E2E_NODE_SANDBOX", r"H:\Proyectos\Personales\aido-e2e-sandbox"))
"""Proyecto pnpm real (vitest) con el store caliente; es integración local, no parte de la suite portátil."""

pytestmark = [
    pytest.mark.usefixtures("controlled_domain_host"),
    pytest.mark.skipif(not git_available(), reason="git CLI is not available"),
    pytest.mark.skipif(
        not (_SANDBOX / "pnpm-lock.yaml").is_file(),
        reason="Sandbox pnpm local no disponible (fija AIDO_E2E_NODE_SANDBOX)",
    ),
]
_SANDBOX_ENTRIES = ("package.json", "pnpm-lock.yaml", "src", "tests", "js")

pytestmark.append(
    pytest.mark.skipif(not _SANDBOX.is_dir(), reason="aido-e2e-sandbox fixture project is not available")
)


@pytest.fixture
def create_store():
    with ExitStack() as _owned_fixture_resources:

        def create_owned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ControlPlaneFixture:
            monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
            store = _owned_fixture_resources.enter_context(
                closing(ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite"))
            )
            store.init()
            return store

        yield create_owned


def _sandbox_copy_project(store: ControlPlaneFixture, tmp_path: Path, name: str) -> dict[str, Any]:
    """Copia (sin `.git` ni `node_modules`) el sandbox pnpm y lo commitea como proyecto AIDO.

    Nunca escribe dentro de `_SANDBOX`: solo lee de ahi. vitest corre con su pool `forks` por
    defecto (casi un proceso por CPU): cubre el `ActiveProcessLimit` real de las clases de QA
    (`host_resources.profiles.TEST_RUNNER_PROCESS_LIMIT`), que antes lo cortaba.
    """
    project_path = tmp_path / name.lower().replace(" ", "-")
    project_path.mkdir(parents=True, exist_ok=True)
    for entry in _SANDBOX_ENTRIES:
        source = _SANDBOX / entry
        if source.is_dir():
            shutil.copytree(source, project_path / entry)
        elif source.is_file():
            shutil.copy2(source, project_path / entry)
    assert run_git(["init"], cwd=project_path).returncode == 0
    assert run_git(["add", "-A"], cwd=project_path).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", "init"],
        cwd=project_path,
    )
    assert commit.returncode == 0, commit.stderr
    return store.create_project(name=name, path=project_path, template_id="other")


def test_qa_agent_provisions_node_modules_in_a_worktree_and_runs_pnpm_test(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = _sandbox_copy_project(store, tmp_path, "Sandbox Node Provisioning")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="node-provisioning",
        agent_id="qa_agent",
        reason="node provisioning e2e workspace",
        isolation_type="git_worktree",
    )
    workspace_path = Path(workspace["path"])
    assert not (workspace_path / "node_modules").exists(), "el worktree no debe traer node_modules"

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="node-provisioning",
        commands=[],
    )

    assert summary["verdict"] == "passed", summary["results"]
    install_result = next(result for result in summary["results"] if result["argv"][0] == "corepack")
    assert install_result["status"] == "passed"
    assert install_result["argv"] == [
        "corepack",
        "pnpm@10.24.0",
        "install",
        "--frozen-lockfile",
        "--prefer-offline",
    ]
    test_result = next(result for result in summary["results"] if result is not install_result)
    assert test_result["status"] == "passed"
    assert (workspace_path / "node_modules").is_dir()
    assert (workspace_path / "node_modules" / ".aido-deps.json").is_file()


def test_qa_agent_skips_install_on_the_next_run_when_the_marker_is_current(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = _sandbox_copy_project(store, tmp_path, "Sandbox Node Marker Reuse")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="node-provisioning-marker",
        agent_id="qa_agent",
        reason="node provisioning marker workspace",
        isolation_type="git_worktree",
    )
    runner = QAAgentRunner(store.connection, root=tmp_path)
    first = runner.run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="node-provisioning-marker",
        commands=[],
    )
    assert first["verdict"] == "passed"

    second = runner.run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="node-provisioning-marker",
        commands=[],
    )

    assert second["verdict"] == "passed"
    assert not any("install" in result["argv"] for result in second["results"]), second["results"]
    assert len(second["results"]) == 1, "sin reinstalar, solo debe correr el comando de test"


def test_qa_agent_install_failure_is_a_failed_qa_result(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project = _sandbox_copy_project(store, tmp_path, "Sandbox Node Install Failure")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="node-provisioning-fail",
        agent_id="qa_agent",
        reason="node provisioning failure workspace",
        isolation_type="git_worktree",
    )
    workspace_path = Path(workspace["path"])
    # package.json cambio (agrega una dependencia) sin actualizar el lockfile: --frozen-lockfile
    # debe rechazar la instalacion, que es exactamente la causa legitima de rework que pide el diseno.
    manifest = workspace_path / "package.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"devDependencies"', '"dependencies": {"left-pad": "999.999.999"}, "devDependencies"'
        ),
        encoding="utf-8",
    )

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="node-provisioning-fail",
        commands=[],
    )

    assert summary["verdict"] == "failed"
    install_result = next(result for result in summary["results"] if result["argv"][0] == "corepack")
    assert install_result["status"] == "failed"
    assert install_result["exitCode"] != 0
    assert not (workspace_path / "node_modules" / ".aido-deps.json").exists()


def test_qa_agent_without_lockfile_blocks_with_skipped_with_reason(
    create_store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = create_store(tmp_path, monkeypatch)
    project_path = tmp_path / "no-lockfile-project"
    project_path.mkdir()
    (project_path / "package.json").write_text(
        '{"name":"demo","dependencies":{"left-pad":"1.0.0"},"scripts":{"test":"echo ok"}}\n',
        encoding="utf-8",
    )
    assert run_git(["init"], cwd=project_path).returncode == 0
    assert run_git(["add", "-A"], cwd=project_path).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", "init"],
        cwd=project_path,
    )
    assert commit.returncode == 0, commit.stderr
    project = store.create_project(name="No Lockfile Project", path=project_path, template_id="other")
    workspace = store.workspaces.allocate_workspace(
        project_id=project["id"],
        task_id="node-provisioning-no-lockfile",
        agent_id="qa_agent",
        reason="node provisioning no lockfile workspace",
        isolation_type="git_worktree",
    )

    summary = QAAgentRunner(store.connection, root=tmp_path).run_for_context(
        project_id=project["id"],
        workspace_id=workspace["id"],
        task_id="node-provisioning-no-lockfile",
        commands=[],
    )

    assert summary["verdict"] == "skipped_with_reason"
    assert len(summary["results"]) == 1
    assert summary["results"][0]["status"] == "skipped_with_reason"
    assert summary["results"][0]["reason"] == NODE_NO_LOCKFILE_REASON


def test_node_lockfile_sha256_is_stable_for_the_same_content(tmp_path: Path) -> None:
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    assert node_lockfile_sha256(lockfile) == node_lockfile_sha256(lockfile)
