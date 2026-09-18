"""Confina el ejecutable de un argv a un origen confiable antes de que la politica lo permita.

El clasificador de comandos solo ve un string, y los llamadores reducen ``argv[0]`` a su basename
antes de clasificarlo (`qa_agent._display_command`). El directorio del binario nunca llegaba a la
decision, asi que el nombre del archivo quedaba como unica credencial: bastaba traer un ejecutable
propio llamado como uno allowlisted para heredar su categoria de bajo riesgo y correr sin
aprobacion humana. El allowlist del sandbox comparte el mismo punto ciego, asi que endurecer solo
el clasificador seria teatro.

Este modulo mira el argv real. Un ejecutable es confiable en cuatro casos, y en ninguno otro:

* nombre pelado sin directorio, que el sistema resuelve por PATH recien al ejecutar;
* ruta cuyo directorio padre es una entrada del PATH, que es lo que emite ``shutil.which``;
* ruta contenida en el workspace asignado, que es el wrapper versionado que el proyecto fijo
  (``mvnw``/``gradlew``) y que es deliberadamente la version que manda sobre la global;
* el interprete que corre este mismo proceso, que es lo que emiten los pasos de calidad y el
  dispatcher (``sys.executable``). Se compara el archivo exacto y no su directorio: dejar pasar
  todo el ``Scripts/`` del venv regalaria confianza a cualquier binario que alguien deje ahi.

Invariante: ante un argv mal formado, una ruta invalida o un workspace ausente devuelve "no
confiable". Sin workspace declarado no hay contencion que probar, y la duda se resuelve hacia el
lado seguro en vez de heredar la confianza del basename.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def is_path_inside(path: str | None, root: str | None) -> bool:
    """Confirma que ``path`` resuelve dentro de ``root`` tras normalizar ``..`` y symlinks.

    Considera dentro cuando falta path o root (no hay restriccion declarada). Resuelve ambas
    rutas para evitar escapes via traversal; ante rutas invalidas devuelve ``False`` (fuera),
    fallando hacia el lado seguro en vez de lanzar.
    """
    if not path or not root:
        return True
    try:
        candidate = Path(path).resolve(strict=False)
        workspace_root = Path(root).resolve(strict=False)
        candidate.relative_to(workspace_root)
        return True
    except (OSError, ValueError):
        return False


def _normalized_path(value: str) -> str | None:
    """Normaliza una ruta para comparar por identidad, colapsando ``..`` sin seguir symlinks.

    No usa ``resolve``: un binario legitimo del PATH suele ser un symlink o shim hacia otro arbol,
    y resolverlo lo sacaria de su directorio del PATH convirtiendo un origen valido en denegado.
    """
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(value)))
    except (OSError, ValueError):
        return None


def _path_directories() -> set[str]:
    """Directorios del PATH vigente; se lee en cada llamada porque el entorno cambia en runtime."""
    raw = os.environ.get("PATH") or ""
    directories = set()
    for entry in raw.split(os.pathsep):
        # Windows admite entradas entrecomilladas; sin quitarlas, un directorio valido del PATH
        # no coincidiria y un binario legitimo quedaria denegado.
        candidate = entry.strip().strip('"')
        if not candidate:
            continue
        normalized = _normalized_path(candidate)
        if normalized:
            directories.add(normalized)
    return directories


def executable_origin_is_trusted(argv: Any, *, workspace_path: str | None) -> bool:
    """Indica si ``argv[0]`` viene del PATH, del workspace asignado o del interprete en curso.

    Args:
        argv: argv estructurado de la llamada; cualquier forma que no sea una lista de strings no
            vacios se considera no confiable.
        workspace_path: workspace asignado a la ejecucion. Si falta, la contencion no se puede
            probar y solo pasan el nombre pelado, el PATH y el interprete en curso.
    """
    if not isinstance(argv, list) or not argv:
        return False
    executable = argv[0]
    if not isinstance(executable, str) or not executable.strip():
        return False
    if not os.path.dirname(executable):
        return True
    normalized = _normalized_path(executable)
    if normalized is not None and normalized == _normalized_path(sys.executable):
        return True
    parent = _normalized_path(os.path.dirname(executable))
    if parent is not None and parent in _path_directories():
        return True
    return bool(workspace_path) and is_path_inside(executable, workspace_path)
