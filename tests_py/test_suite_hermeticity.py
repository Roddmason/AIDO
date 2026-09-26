"""Guardas de hermeticidad de la suite: un test no puede depender de la configuracion del equipo.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from pathlib import Path

from local_control_center.security_policy.git_command_runner import run_git


def test_temporary_repositories_do_not_inherit_the_global_git_template(
    tmp_path: Path, controlled_domain_host: None
) -> None:
    """Un `git init` de test no copia los hooks de `init.templateDir` del desarrollador.

    Medido: con la plantilla global y gitleaks instalados, un fixture con un token de forma real
    dejaba el commit del test en rc=1. En un equipo sin plantilla este test pasa siempre; en uno
    con plantilla, es el que detecta si el aislamiento se pierde.
    """
    assert Path(os.environ["GIT_TEMPLATE_DIR"]).is_dir()
    assert not any(Path(os.environ["GIT_TEMPLATE_DIR"]).iterdir())

    assert run_git(["init"], cwd=tmp_path).returncode == 0

    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()


def test_diagnostics_do_not_write_to_the_user_folder() -> None:
    """El sink de diagnósticos de la suite no comparte carpeta ni lock con el AIDO del usuario."""
    from local_control_center.shared import diagnostics

    user_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "AIDO/diagnostics"

    assert diagnostics.diagnostic_root() != user_root
    assert diagnostics.ensure_diagnostics().root != user_root
