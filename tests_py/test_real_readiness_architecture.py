from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOTS = [
    ROOT / "local_control_center",
    ROOT / "local-control-center" / "web" / "src",
]
PRODUCT_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"}
EXCLUDED_PARTS = {
    ("local-control-center", "web", "src", "api", "generated"),
    ("local_control_center", "i18n"),
}

PROHIBITED_PRODUCT_PATTERNS = [
    ("internal mock runtime exposed in product code", re.compile(r"\binternal_mock\b")),
    ("model gateway mock response contract exposed in product code", re.compile(r"\bRouteExecuteMockResponse\b")),
    ("product provider or runtime exposes mock constructor flag", re.compile(r"\bmock\s*:\s*bool\b")),
    ("product provider or runtime accepts mock execution request flag", re.compile(r"\bmock\s*:\s*bool\b|\brequest\.mock\b")),
    ("product provider or runtime emits mock source metadata", re.compile(r'\bsource\s*=\s*"mock"|["\']source["\']\s*:\s*["\']mock["\']')),
    ("product provider or runtime returns mock completion", re.compile(r"\bmock response\b|\bmock runtime completed\b|\bmock local response\b")),
]


def _is_excluded(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return any(parts[: len(excluded)] == excluded for excluded in EXCLUDED_PARTS)


def _product_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCT_ROOTS:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in PRODUCT_EXTENSIONS and not _is_excluded(path)
        )
    return sorted(files)


def test_product_code_does_not_expose_internal_mock_runtime_paths() -> None:
    violations: list[str] = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PROHIBITED_PRODUCT_PATTERNS:
                if pattern.search(line):
                    relative = path.relative_to(ROOT).as_posix()
                    violations.append(f"{relative}:{line_number}: {label}: {line.strip()}")

    assert not violations, (
        "Product code exposes internal_mock outside tests.\n"
        + "\n".join(violations[:80])
        + (f"\n... {len(violations) - 80} more" if len(violations) > 80 else "")
    )
