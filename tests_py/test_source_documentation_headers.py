from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SOURCE_ROOT = ROOT / "local-control-center" / "web" / "src"
BACKEND_SOURCE_ROOT = ROOT / "local_control_center"

EXPECTED_JSDOC_HEADER = """/**
 * @file AIDO frontend source module.
 * @copyright Copyright (c) AIDO.
 * @author Roddmason
 */
"""

EXPECTED_PYDOC_HEADER = '''"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
'''


def source_files(root: Path, suffixes: set[str]) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in suffixes and "generated" not in path.relative_to(root).parts
    )


def test_frontend_source_files_start_with_aido_jsdoc_header() -> None:
    missing = [
        str(path.relative_to(ROOT))
        for path in source_files(FRONTEND_SOURCE_ROOT, {".ts", ".tsx"})
        if not path.read_text(encoding="utf-8").startswith(EXPECTED_JSDOC_HEADER)
    ]

    assert missing == []


def test_backend_source_files_start_with_aido_pydoc_header() -> None:
    missing = [
        str(path.relative_to(ROOT))
        for path in source_files(BACKEND_SOURCE_ROOT, {".py"})
        if not path.read_text(encoding="utf-8").startswith(EXPECTED_PYDOC_HEADER)
    ]

    assert missing == []
