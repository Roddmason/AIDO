"""Contexto del repositorio para el DeveloperAgent que corre sobre un modelo sin herramientas.

Un runtime CLI (Claude Code, Codex) lee el workspace por su cuenta; un modelo API/local no ve nada y
debe devolver el contenido COMPLETO de cada archivo que escribe. Sin contexto inventaba la estructura
(visto en vivo: creó ``textkit/utils.py`` en la raíz de un proyecto con layout ``src/textkit/``, y
QA falló con ``ModuleNotFoundError``) y reescribía archivos existentes sin conocerlos (el README
quedó reducido a lo que el modelo imaginó). Este módulo arma, con presupuesto fijo, el mapa de
archivos del workspace y el contenido actual de los relevantes.

Solo entra lo que git considera parte del proyecto (versionado o nuevo sin ignorar), nunca archivos
con forma de secreto, y todo texto pasa por ``redact_secrets``: el prompt puede ir a un proveedor
remoto.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath

from local_control_center.security_policy.git_command_runner import run_git
from local_control_center.shared.redaction import redact_secrets

logger = logging.getLogger(__name__)

MAX_LISTED_PATHS = 400
"""Tope de rutas del mapa; un repo grande se lista truncado y lo dice."""

MAX_CONTENT_CHARS = 60_000
"""Presupuesto total de contenido (~15k tokens): cabe en cualquier modelo de 32k con margen."""

MAX_FILE_CHARS = 12_000
"""Un archivo más grande se lista pero no se incluye: el modelo no podría reescribirlo completo."""

LOCKFILE_NAMES = frozenset(
    {
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }
)
MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "package.json",
        "tsconfig.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    }
)
SECRET_NAME_RE = re.compile(
    r"(^\.env($|\.)|\.(pem|key|p12|pfx|jks|keystore|kdbx)$|^id_(rsa|dsa|ecdsa|ed25519)|"
    r"^\.(netrc|npmrc|pypirc)$|"
    r"^[^.]*(credential|secret)s?[^.]*(\.(json|ya?ml|toml|ini|cfg|conf|txt|env))?$)",
    re.IGNORECASE,
)
"""Archivos con forma de contenedor de secretos. El código (``credentials.py``) sí entra, redactado."""


def workspace_paths(workspace_path: Path) -> list[str]:
    """Rutas del proyecto según git (versionadas y nuevas no ignoradas), en orden estable.

    Devuelve lista vacía si el workspace no es un repo git o git falla: sin mapa el modelo trabaja
    como antes, en vez de bloquear la historia por un contexto opcional.
    """
    if not workspace_path.is_dir():
        return []
    try:
        completed = run_git(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=workspace_path
        )
    except OSError as error:
        # Incluye ``ResourceWaitError``: el contexto es opcional y nunca bloquea la historia.
        logger.warning("DeveloperAgent repository context unavailable: %s", type(error).__name__)
        return []
    if completed.returncode != 0:
        return []
    return sorted({path for path in completed.stdout.split("\0") if path.strip()})


def _is_secret_like(path: str) -> bool:
    return bool(SECRET_NAME_RE.search(PurePosixPath(path).name))


def _content_priority(path: str, focus_text: str) -> tuple[int, int, str]:
    """Orden de inclusión: lo que la historia menciona, luego manifiestos y docs, luego el resto."""
    pure = PurePosixPath(path)
    lowered = focus_text.lower()
    if path.lower() in lowered or (len(pure.stem) > 2 and pure.stem.lower() in lowered):
        rank = 0
    elif pure.name in MANIFEST_NAMES or pure.name.lower().startswith("readme"):
        rank = 1
    else:
        rank = 2
    return (rank, len(pure.parts), path)


def _read_text(path: Path) -> str | None:
    """Texto UTF-8 del archivo, o ``None`` si es binario, ilegible o symlink."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def render_repository_context(workspace_path: Path, paths: list[str], *, focus_text: str) -> str:
    """Arma el bloque de contexto: mapa de rutas y contenido actual dentro del presupuesto.

    Un archivo entra entero o no entra (nunca a medias: el modelo lo reescribiría truncado). Los
    omitidos quedan nombrados para que el modelo sepa que existen aunque no vea su contenido.
    """
    visible = [path for path in paths if not _is_secret_like(path)]
    if not visible:
        return ""
    listed = visible[:MAX_LISTED_PATHS]
    lines = ["Repository files (paths relative to the workspace root; this is the real layout):"]
    lines.extend(f"- {path}" for path in listed)
    if len(visible) > len(listed):
        lines.append(f"- ... {len(visible) - len(listed)} more files not listed")
    sections: list[str] = []
    omitted: list[str] = []
    remaining = MAX_CONTENT_CHARS
    for path in sorted(visible, key=lambda item: _content_priority(item, focus_text)):
        if PurePosixPath(path).name in LOCKFILE_NAMES:
            continue
        text = _read_text(workspace_path / path)
        if text is None:
            continue
        if len(text) > MAX_FILE_CHARS or len(text) > remaining:
            omitted.append(path)
            continue
        remaining -= len(text)
        sections.append(f"=== {path} ===\n{redact_secrets(text)}")
    if sections:
        lines.append("")
        lines.append("Current content of existing files:")
        lines.extend(sections)
    if omitted:
        lines.append("")
        lines.append(
            "Existing files whose content is not shown (too large for this prompt): " + ", ".join(omitted)
        )
    lines.append("")
    lines.append(
        "Rules for the files you return: put code where this layout expects it (reuse the existing "
        "packages and test folders; never create a parallel top-level package). A file you change "
        "must come back with its COMPLETE new content, keeping everything unrelated to the task."
    )
    return "\n".join(lines)


def repository_context(workspace_path: str | Path, *, focus_text: str) -> str:
    """Contexto del repositorio listo para el prompt, o cadena vacía si no hay nada que mostrar."""
    root = Path(workspace_path)
    return render_repository_context(root, workspace_paths(root), focus_text=focus_text)
