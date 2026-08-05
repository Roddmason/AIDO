"""Audita claves del catálogo i18n que ya no aparecen en ningún fuente del repositorio.

El catálogo crece con cada pantalla, pero nada lo poda cuando una superficie se retira: las
claves quedan como peso muerto y alguien puede terminar traduciendo copy de una feature que ya
no existe. Este script hace el inventario reproducible en vez de reconstruirlo a mano cada vez.

Método: tokeniza todo el código (frontend TS/TSX, backend Python, tests, scripts y docs) y marca
como huérfana toda clave del catálogo que no aparezca como token exacto. Las claves que el
frontend arma con template literals (``t(`app.threads.event.${type}`)``) nunca aparecen completas
en el código, así que se preservan por prefijo declarado en ``DYNAMIC_PREFIXES``: sin esa lista el
reporte pediría borrar copy en uso.

Es SOLO lectura: reporta y devuelve 0 siempre. La decisión de podar es humana, porque una clave
huérfana hoy puede ser copy que volverá con la pantalla que se está reescribiendo, y borrarla del
default no la quita de las instalaciones ya sembradas (``ensure_seeded`` solo inserta lo ausente).

Uso:
    python scripts/audit-i18n-orphans.py [--json ruta.json]

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "local_control_center" / "i18n" / "default_catalog.json"

SOURCE_TREES: tuple[tuple[Path, tuple[str, ...]], ...] = (
    (ROOT / "local-control-center" / "web" / "src", (".ts", ".tsx")),
    (ROOT / "local_control_center", (".py",)),
    (ROOT / "tests_py", (".py",)),
    (ROOT / "tests_web", (".js", ".ts")),
    (ROOT / "scripts", (".py", ".mjs")),
    (ROOT / "docs", (".md",)),
)

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.\-]+")

# Familias que el frontend construye por interpolación; la clave completa nunca es un literal.
DYNAMIC_PREFIXES: tuple[str, ...] = (
    "app.runtime.failure.",
    "app.settings.enum.",
    "app.threads.event.",
    "app.threads.remediation.action.",
    "app.threads.remediation.blocker.",
    "app.threads.stepState.",
)


def collect_tokens() -> set[str]:
    """Junta todos los identificadores presentes en los árboles de código auditados."""
    tokens: set[str] = set()
    for base, suffixes in SOURCE_TREES:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in suffixes or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            tokens.update(TOKEN_PATTERN.findall(text))
    return tokens


def find_orphans() -> tuple[list[str], int, int]:
    """Devuelve las claves huérfanas, el total del catálogo y cuántas salvó un prefijo dinámico."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    translations: dict[str, dict[str, str]] = catalog["translations"]
    tokens = collect_tokens()

    orphans: list[str] = []
    dynamic_saved = 0
    for key in translations:
        if key in tokens:
            continue
        if any(key.startswith(prefix) for prefix in DYNAMIC_PREFIXES):
            dynamic_saved += 1
            continue
        orphans.append(key)
    return sorted(orphans), len(translations), dynamic_saved


def main() -> int:
    """Imprime el resumen por prefijo y, si se pide, vuelca el listado completo a JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Ruta donde volcar el listado completo.")
    args = parser.parse_args()

    orphans, total, dynamic_saved = find_orphans()
    by_prefix = Counter(".".join(key.split(".")[:2]) for key in orphans)

    print(f"Claves en el catálogo: {total}")
    print(f"Preservadas por prefijo dinámico: {dynamic_saved}")
    print(f"Huérfanas (sin token en ningún fuente): {len(orphans)}")
    if orphans:
        print("\nPor prefijo:")
        for prefix, count in by_prefix.most_common():
            print(f"  {count:4d}  {prefix}")

    if args.json:
        args.json.write_text(json.dumps(orphans, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nListado completo: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
