"""Provision de `node_modules` en workspaces Node antes de que el QA corra comandos del proyecto.

Los workspaces de las historias son git worktrees: `node_modules/` esta en `.gitignore` y ningun
worktree nuevo lo trae, asi que todo comando Node del QA moria con `Cannot find module`. Este
modulo decide, a partir del lockfile y de un marcador propio, si hace falta instalar, con que
gestor, y si es posible hacerlo de forma determinista.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.projects.toolchain import (
    NODE_NO_LOCKFILE_REASON,
    node_dependency_marker_matches,
    node_lockfile_sha256,
    plan_workspace_commands,
    resolve_node_install_plan,
    write_node_dependency_marker,
)

PACKAGE_JSON_WITH_DEPS = json.dumps({"name": "demo", "dependencies": {"left-pad": "1.0.0"}})
PACKAGE_JSON_NO_DEPS = json.dumps({"name": "demo", "scripts": {"test": "x"}})


def _pnpm_project(workspace: Path, *, lockfile_content: str = "lockfileVersion: '9.0'\n") -> None:
    (workspace / "package.json").write_text(PACKAGE_JSON_WITH_DEPS, encoding="utf-8")
    (workspace / "pnpm-lock.yaml").write_text(lockfile_content, encoding="utf-8")


def test_plan_requires_install_when_node_modules_is_missing(tmp_path: Path) -> None:
    _pnpm_project(tmp_path)

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is True
    assert plan.blocking_reason is None
    assert plan.command is not None
    assert plan.command.argv == [
        "corepack",
        "pnpm@10.24.0",
        "install",
        "--frozen-lockfile",
        "--prefer-offline",
        "--ignore-scripts",
    ]
    assert plan.command.critical is True


def test_plan_skips_install_when_marker_matches_current_lockfile(tmp_path: Path) -> None:
    _pnpm_project(tmp_path)
    (tmp_path / "node_modules").mkdir()
    lockfile_sha256 = node_lockfile_sha256(tmp_path / "pnpm-lock.yaml")
    write_node_dependency_marker(tmp_path, manager="pnpm", lockfile_sha256=lockfile_sha256)

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is False
    assert plan.command is None


def test_plan_requires_install_when_lockfile_changed_since_the_marker(tmp_path: Path) -> None:
    _pnpm_project(tmp_path)
    (tmp_path / "node_modules").mkdir()
    write_node_dependency_marker(tmp_path, manager="pnpm", lockfile_sha256="stale-hash")

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is True
    assert plan.command is not None


def test_plan_requires_install_when_marker_manager_does_not_match(tmp_path: Path) -> None:
    _pnpm_project(tmp_path)
    (tmp_path / "node_modules").mkdir()
    lockfile_sha256 = node_lockfile_sha256(tmp_path / "pnpm-lock.yaml")
    write_node_dependency_marker(tmp_path, manager="npm", lockfile_sha256=lockfile_sha256)

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is True


def test_plan_without_lockfile_blocks_instead_of_installing_nondeterministically(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(PACKAGE_JSON_WITH_DEPS, encoding="utf-8")

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is True
    assert plan.command is None
    assert plan.blocking_reason == NODE_NO_LOCKFILE_REASON


def test_plan_without_lockfile_but_with_existing_node_modules_does_not_block(tmp_path: Path) -> None:
    """Un `node_modules` ya presente (aunque no haya lockfile) no se toca ni se bloquea."""
    (tmp_path / "package.json").write_text(PACKAGE_JSON_WITH_DEPS, encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is False
    assert plan.blocking_reason is None


def test_plan_skips_entirely_without_declared_dependencies(tmp_path: Path) -> None:
    """`package.json` sin `dependencies`/`devDependencies` no dispara ninguna instalacion."""
    (tmp_path / "package.json").write_text(PACKAGE_JSON_NO_DEPS, encoding="utf-8")

    plan = resolve_node_install_plan(tmp_path, manager="pnpm")

    assert plan.needs_install is False
    assert plan.command is None
    assert plan.blocking_reason is None


def test_npm_and_yarn_install_commands_are_frozen_and_offline(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(PACKAGE_JSON_WITH_DEPS, encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    npm_plan = resolve_node_install_plan(tmp_path, manager="npm")
    assert npm_plan.command.argv == [
        "npm",
        "ci",
        "--prefer-offline",
        "--no-audit",
        "--no-fund",
        "--ignore-scripts",
    ]

    (tmp_path / "package-lock.json").unlink()
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    yarn_plan = resolve_node_install_plan(tmp_path, manager="yarn")
    assert yarn_plan.command.argv == ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"]

    (tmp_path / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")
    yarn_immutable_plan = resolve_node_install_plan(tmp_path, manager="yarn")
    assert yarn_immutable_plan.command.argv == ["yarn", "install", "--immutable", "--mode=skip-build"]


def test_plan_workspace_commands_runs_install_before_test_and_build(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "dependencies": {"left-pad": "1.0.0"},
                "scripts": {"test": "vitest run", "build": "vite build"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

    commands = plan_workspace_commands(tmp_path)

    purposes = [command.purpose for command in commands]
    assert purposes == ["install", "test", "build"]
    assert commands[0].argv == [
        "corepack",
        "pnpm@10.24.0",
        "install",
        "--frozen-lockfile",
        "--prefer-offline",
        "--ignore-scripts",
    ]
    assert commands[0].node_install_marker == {
        "manager": "pnpm",
        "lockfileSha256": node_lockfile_sha256(tmp_path / "pnpm-lock.yaml"),
    }


def test_plan_workspace_commands_without_lockfile_reduces_to_the_blocking_entry(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {"name": "demo", "dependencies": {"left-pad": "1.0.0"}, "scripts": {"test": "vitest run"}}
        ),
        encoding="utf-8",
    )

    commands = plan_workspace_commands(tmp_path)

    assert len(commands) == 1
    assert commands[0].unresolved_reason == NODE_NO_LOCKFILE_REASON
    assert commands[0].as_command_spec()["unresolvedReason"] == NODE_NO_LOCKFILE_REASON


def test_node_dependency_marker_matches_only_same_manager_and_hash(tmp_path: Path) -> None:
    _pnpm_project(tmp_path)
    (tmp_path / "node_modules").mkdir()
    lockfile_sha256 = node_lockfile_sha256(tmp_path / "pnpm-lock.yaml")
    write_node_dependency_marker(tmp_path, manager="pnpm", lockfile_sha256=lockfile_sha256)

    assert node_dependency_marker_matches(tmp_path, manager="pnpm", lockfile_sha256=lockfile_sha256) is True
    assert node_dependency_marker_matches(tmp_path, manager="npm", lockfile_sha256=lockfile_sha256) is False
    assert node_dependency_marker_matches(tmp_path, manager="pnpm", lockfile_sha256="other") is False
