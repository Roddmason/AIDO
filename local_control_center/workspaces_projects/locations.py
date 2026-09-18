"""Ubicación canónica de los workspaces aislados de cada proyecto.

Un workspace materializa el código de UN proyecto, así que pertenece a ese proyecto y no al
repositorio del sistema. Antes vivían bajo ``<aido>/.tmp/workspaces``: eso dejaba worktrees de
repositorios ajenos anidados dentro del árbol de trabajo de AIDO, mezclando el contenido de cada
proyecto con el del sistema y haciendo que la limpieza de uno pudiera afectar al otro.

Vivir bajo el propio proyecto además mantiene el worktree en el mismo volumen que su repositorio,
que es lo que hace barato crearlo, y deja que el proyecto se mueva o se borre con lo suyo dentro.

``LEGACY_WORKSPACES_ROOT`` se conserva sólo para que la política de limpieza siga reconociendo y
pudiendo retirar lo que quedó de la ubicación anterior; nada nuevo se escribe ahí.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

PROJECT_WORKSPACES_DIRNAME = ".aido"
WORKSPACES_SUBDIRNAME = "workspaces"
LEGACY_WORKSPACES_PARTS = (".tmp", "workspaces")
NEWLINE = "\n"
EXCLUDE_HEADER = "# AIDO aisla aqui los workspaces del proyecto; no es contenido del repositorio."


def project_workspaces_root(project_path: Path | str) -> Path:
    """Raíz de workspaces del proyecto: ``<proyecto>/.aido/workspaces``."""
    return (Path(project_path) / PROJECT_WORKSPACES_DIRNAME / WORKSPACES_SUBDIRNAME).resolve(strict=False)


def legacy_workspaces_root(root: Path | str) -> Path:
    """Raíz anterior, dentro del repositorio del sistema. Sólo para reconocer lo ya creado."""
    return Path(root).joinpath(*LEGACY_WORKSPACES_PARTS).resolve(strict=False)


def ensure_workspaces_excluded(project_path: Path | str) -> bool:
    """Excluye ``.aido/`` del git del proyecto sin tocar su ``.gitignore`` versionado.

    Los workspaces viven dentro del proyecto, así que sin esto aparecerían como archivos sin
    trackear en el ``git status`` del propio proyecto. Se escribe en ``.git/info/exclude``, que es
    local al clon y no forma parte del historial: AIDO no le modifica archivos versionados a nadie.

    Devuelve ``True`` si dejó la exclusión escrita. Nunca lanza: si el proyecto no es un repo o el
    archivo no se puede escribir, el workspace se crea igual.
    """
    marker = f"/{PROJECT_WORKSPACES_DIRNAME}/"
    info = Path(project_path) / ".git" / "info"
    try:
        if not info.parent.is_dir():
            return False
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if any(line.strip() == marker for line in current.splitlines()):
            return True
        separator = "" if not current or current.endswith(NEWLINE) else NEWLINE
        exclude.write_text(
            current + separator + EXCLUDE_HEADER + NEWLINE + marker + NEWLINE,
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
