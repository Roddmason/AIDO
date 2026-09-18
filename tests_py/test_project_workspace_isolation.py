"""Tests del aislamiento de workspaces por proyecto.

El workspace de un proyecto no puede materializarse dentro del repositorio de AIDO: sus archivos
pertenecen al proyecto, no al sistema. Antes vivían en ``<aido>/.tmp/workspaces``, así que un
worktree de otro repositorio quedaba anidado en el árbol de trabajo de AIDO.

@author Rodrigo Mason
"""

from __future__ import annotations

import subprocess
from contextlib import closing
from pathlib import Path

from local_control_center.workspaces_projects.locations import project_workspaces_root
from local_control_center.workspaces_projects.repository import WorkspacesRepository
from tests_py.control_plane_fixture import ControlPlaneFixture


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _git_project(path: Path) -> Path:
    """Crea un repositorio de proyecto real, ajeno al repositorio del sistema."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "--initial-branch=dev"], path)
    _git(["config", "user.email", "aido@example.test"], path)
    _git(["config", "user.name", "AIDO Tests"], path)
    (path / "README.md").write_text("proyecto ajeno al sistema\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-m", "Chore (Test): commit inicial"], path)
    return path


def _allocate(tmp_path: Path, project_path: Path, task_id: str, stack):
    """Compone el plano de control de pruebas y asigna un workspace para ese proyecto."""
    aido_root = tmp_path / "aido"
    aido_root.mkdir(exist_ok=True)
    store = stack.enter_context(
        closing(ControlPlaneFixture(cwd=aido_root, db_path=aido_root / "platform.sqlite"))
    )
    store.init()
    project = store.projects.create_project(name="Proyecto Ajeno", path=project_path, template_id="other")
    workspace = WorkspacesRepository(store.connection, root=aido_root).allocate_workspace(
        project_id=project["id"],
        task_id=task_id,
        agent_id="developer",
        branch_name=f"codex/{task_id}",
    )
    store.connection.commit()
    return aido_root, workspace


def test_workspace_lands_inside_the_project_not_inside_aido(tmp_path: Path) -> None:
    """El workspace se materializa bajo el ``.aido/workspaces`` del proyecto, sea worktree o copia.

    La ubicación es la propiedad que importa y no depende del tipo de aislamiento: el contenido de
    un proyecto no puede quedar dentro del árbol de trabajo del sistema.
    """
    from contextlib import ExitStack

    project_path = _git_project(tmp_path / "proyecto-ajeno")
    with ExitStack() as stack:
        aido_root, workspace = _allocate(tmp_path, project_path, "story-aislada", stack)

        workspace_path = Path(workspace["path"]).resolve()
        assert workspace_path.is_relative_to(project_workspaces_root(project_path)), workspace_path
        assert not workspace_path.is_relative_to(aido_root.resolve()), (
            "el contenido del proyecto no puede quedar dentro del repositorio del sistema"
        )


def test_nothing_is_written_under_the_aido_working_tree(tmp_path: Path) -> None:
    """La ruta legacy ``<aido>/.tmp/workspaces`` deja de recibir workspaces de proyectos."""
    from contextlib import ExitStack

    project_path = _git_project(tmp_path / "proyecto-ajeno")
    with ExitStack() as stack:
        aido_root, _workspace = _allocate(tmp_path, project_path, "story-limpia", stack)

        legacy = aido_root / ".tmp" / "workspaces"
        assert not legacy.exists() or not any(legacy.iterdir()), sorted(p.name for p in legacy.iterdir())


def test_the_worktree_is_registered_in_the_project_repository(tmp_path: Path) -> None:
    """Cuando el aislamiento es git, el worktree y su rama quedan en el repositorio del proyecto."""
    from contextlib import ExitStack

    import pytest

    project_path = _git_project(tmp_path / "proyecto-ajeno")
    with ExitStack() as stack:
        _aido_root, workspace = _allocate(tmp_path, project_path, "story-namespace", stack)

        if workspace["isolationType"] != "git_worktree":
            pytest.skip(
                "el broker no concedio el git aislado en este entorno; la ubicacion ya se verifica aparte"
            )
        listed = _git(["worktree", "list", "--porcelain"], project_path).stdout.replace("\\", "/")
        assert str(Path(workspace["path"]).resolve()).replace("\\", "/") in listed, listed
        branches = _git(["branch", "--format=%(refname:short)"], project_path).stdout.split()
        assert "codex/story-namespace" in branches, branches


def test_directory_copy_never_recurses_into_the_workspaces_folder(tmp_path: Path) -> None:
    """Sin git, la copia del proyecto no puede arrastrar su propia carpeta de workspaces."""
    from contextlib import ExitStack

    project_path = tmp_path / "proyecto-sin-git"
    project_path.mkdir()
    (project_path / "archivo.txt").write_text("contenido\n", encoding="utf-8")
    previo = project_workspaces_root(project_path) / "workspace-previo"
    previo.mkdir(parents=True)
    (previo / "basura.txt").write_text("no debe copiarse\n", encoding="utf-8")

    with ExitStack() as stack:
        _aido_root, workspace = _allocate(tmp_path, project_path, "story-copia", stack)

        assert workspace["isolationType"] == "directory"
        copied = Path(workspace["path"])
        assert (copied / "archivo.txt").exists()
        assert not (copied / ".aido").exists(), "la copia arrastro la carpeta de workspaces"
