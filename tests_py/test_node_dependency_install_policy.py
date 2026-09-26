"""Politica de seguridad para la instalacion determinista de dependencias Node en el QA.

Solo el argv exacto de una instalacion congelada y preferentemente offline (`--frozen-lockfile`,
`npm ci`, `--immutable`) es de bajo riesgo; cualquier otra invocacion de instalacion (agregar un
paquete, instalar sin lockfile, actualizar) sigue en riesgo medio como hoy.

@author Rodrigo Mason
"""

from __future__ import annotations

import pytest

from local_control_center.projects.toolchain import PNPM_VERSION
from local_control_center.security_policy.permissions import low_risk_shell_category, parse_command
from local_control_center.security_policy.policy_engine import allowlisted_shell_categories


def _category(command: str) -> str | None:
    parsed = parse_command(command)
    assert parsed is not None, command
    return low_risk_shell_category(parsed)


@pytest.mark.parametrize(
    "command",
    [
        f"corepack pnpm@{PNPM_VERSION} install --frozen-lockfile --prefer-offline --ignore-scripts",
        "npm ci --prefer-offline --no-audit --no-fund --ignore-scripts",
        "yarn install --frozen-lockfile --ignore-scripts",
        "yarn install --immutable --mode=skip-build",
    ],
)
def test_frozen_offline_install_commands_are_low_risk(command: str) -> None:
    assert _category(command) == "dependency_install_frozen"


@pytest.mark.parametrize(
    "command",
    [
        f"corepack pnpm@{PNPM_VERSION} install",
        "pnpm install",
        "npm install",
        "npm ci",
        "yarn install",
        f"corepack pnpm@{PNPM_VERSION} add left-pad",
        "npm ci --prefer-offline",
        # Sin --ignore-scripts correrian los postinstall de dependencias que el developer pudo agregar.
        f"corepack pnpm@{PNPM_VERSION} install --frozen-lockfile --prefer-offline",
        "npm ci --prefer-offline --no-audit --no-fund",
        "yarn install --frozen-lockfile",
        "yarn install --immutable",
        # Solo la version de pnpm que fija la toolchain.
        "corepack pnpm@9.0.0 install --frozen-lockfile --prefer-offline --ignore-scripts",
    ],
)
def test_install_commands_without_the_exact_frozen_argv_stay_non_low_risk(command: str) -> None:
    assert _category(command) != "dependency_install_frozen"


def test_qa_profile_allows_the_frozen_install_category_but_dev_safe_does_not() -> None:
    assert "allowlisted_dependency_install" in allowlisted_shell_categories(
        "qa", ["dependency_install_frozen"]
    )
    assert "allowlisted_dependency_install" not in allowlisted_shell_categories(
        "dev_safe", ["dependency_install_frozen"]
    )
