"""Scanner de documentación: rechaza headers genéricos y exige descripción semántica.

Reemplaza el antiguo gate de banners idénticos por una verificación de que cada módulo
productivo (backend Python + frontend TS/TSX, excl. ``generated/``) explica qué hace,
sin exigir ``@author``. Las docstrings de API pública Python se enforcan vía Ruff
(``D101/D102/D103``). Contrato completo en ``docs/development/documentation-standard.md``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "local_control_center"
FRONTEND = ROOT / "local-control-center" / "web" / "src"

# Placeholders del esquema antiguo: si reaparecen, el header no es semántico.
GENERIC_MARKERS = (
    "AIDO backend source module",
    "AIDO frontend source module",
)
MIN_SUMMARY_CHARS = 30

# Módulos con requisitos de dominio (ver §4 del estándar).
SECURITY_KEYWORDS = ("invariant", "raises", "raise", "lanza")
SECURITY_EXTRA_FILES = {
    BACKEND / "sandbox.py",
    BACKEND / "agents" / "tool_broker.py",
    BACKEND / "agents" / "credentials.py",
}
REPOSITORY_KEYWORDS = ("transacc", "transaction", "commit", "atómic", "atomic")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _backend_files() -> list[Path]:
    return sorted(BACKEND.rglob("*.py"))


def _frontend_files() -> list[Path]:
    return sorted(
        path
        for path in FRONTEND.rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and not path.name.endswith(".d.ts")
        and "generated" not in path.relative_to(FRONTEND).parts
    )


def _module_docstring(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    return ast.get_docstring(tree)


def _leading_block_comment(text: str) -> str | None:
    match = re.match(r"\s*/\*\*(.*?)\*/", text, re.DOTALL)
    return match.group(1) if match else None


def _block_description(block: str) -> str:
    lines = (re.sub(r"^\s*\*\s?", "", line).strip() for line in block.splitlines())
    without_tags = re.sub(r"@\w+[^\n]*", "", " ".join(lines))
    return without_tags.strip()


def test_backend_modules_have_semantic_docstring() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _backend_files():
        docstring = _module_docstring(path)
        if not docstring or not docstring.strip():
            offenders.append((_rel(path), "missing module docstring"))
            continue
        if any(marker in docstring for marker in GENERIC_MARKERS):
            offenders.append((_rel(path), "generic placeholder header"))
            continue
        summary = docstring.strip().splitlines()[0].strip()
        if len(summary) < MIN_SUMMARY_CHARS:
            offenders.append((_rel(path), f"summary too short ({len(summary)} chars)"))

    assert offenders == [], offenders


def test_frontend_modules_have_semantic_block_comment() -> None:
    offenders: list[tuple[str, str]] = []
    for path in _frontend_files():
        block = _leading_block_comment(path.read_text(encoding="utf-8"))
        if block is None:
            offenders.append((_rel(path), "missing leading /** */ module comment"))
            continue
        if any(marker in block for marker in GENERIC_MARKERS):
            offenders.append((_rel(path), "generic placeholder header"))
            continue
        if len(_block_description(block)) < MIN_SUMMARY_CHARS:
            offenders.append((_rel(path), "description too short"))

    assert offenders == [], offenders


def test_no_generic_banner_remains_anywhere() -> None:
    # El esquema nuevo no exige @author; basta con que no quede ningún placeholder genérico.
    leftovers = [
        _rel(path)
        for path in [*_backend_files(), *_frontend_files()]
        if any(marker in path.read_text(encoding="utf-8") for marker in GENERIC_MARKERS)
    ]
    assert leftovers == [], leftovers


def test_security_modules_document_invariants_or_raises() -> None:
    security_files = sorted({*(BACKEND / "security_policy").rglob("*.py"), *SECURITY_EXTRA_FILES})
    offenders: list[str] = []
    for path in security_files:
        if not path.exists():
            continue
        docstring = (_module_docstring(path) or "").lower()
        if not any(keyword in docstring for keyword in SECURITY_KEYWORDS):
            offenders.append(_rel(path))

    assert offenders == [], offenders


def test_repository_modules_document_transactions() -> None:
    offenders: list[str] = []
    for path in _backend_files():
        if path.name != "repository.py":
            continue
        docstring = (_module_docstring(path) or "").lower()
        if not any(keyword in docstring for keyword in REPOSITORY_KEYWORDS):
            offenders.append(_rel(path))

    assert offenders == [], offenders
