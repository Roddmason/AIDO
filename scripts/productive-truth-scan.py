import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TEXT_EXTENSIONS = {
    ".cjs",
    ".js",
    ".json",
    ".jsx",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
IGNORED_PARTS = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "test-results",
}
IGNORED_FILENAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "uv.lock",
}
ENFORCEMENT_CONFIG_ALLOWLIST = {
    ".semgrep.yml",
}
ALLOWED_PARTS = {
    "docs",
    "tests",
    "tests_py",
    "tests_web",
}
# Tokens ofuscados con concatenacion EXPLICITA (operador +) para que el propio scanner
# no se auto-marque y para sobrevivir a `ruff format`, que une literales adyacentes
# (concatenacion implicita) pero NO los unidos por el operador +. Misma tecnica que el
# mensaje de la regla shell+=True de mas abajo. (No deletrear los tokens en texto plano.)
PROHIBITED_TOKENS = (
    "internal_" + "mo" + "ck",
    "mo" + "ck",
    "fa" + "ke",
    "dum" + "my",
)
TOKEN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + "|".join(re.escape(token) for token in PROHIBITED_TOKENS) + r")(?![A-Za-z0-9])"
)
ALLOWED_REFERENCE_PATTERN = re.compile(r"(?i)\b(docs|tests|tests_py|tests_web)[\\/][^\s\"']+")
AVAILABLE_TRUE_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_])[\"']?available[\"']?\s*(?::|=)\s*(true|True)\b")
SHELL_TRUE_PATTERN = re.compile(r"\bshell\s*=\s*True\b")


@dataclass(frozen=True)
class Violation:
    rule_id: str
    relative_path: str
    line_number: int
    message: str
    line: str

    def format(self) -> str:
        return f"{self.relative_path}:{self.line_number}: {self.rule_id}: {self.message}: {self.line.strip()}"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_allowed_path(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts}
    if parts & ALLOWED_PARTS:
        return True
    return path.suffix.lower() == ".md" or path.name.lower().startswith("readme")


def _is_ignored_path(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    if relative.as_posix() in ENFORCEMENT_CONFIG_ALLOWLIST:
        return True
    if path.name in IGNORED_FILENAMES:
        return True
    return any(part in IGNORED_PARTS for part in relative.parts)


def _is_scannable(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS


def _is_runtime_provider_path(relative_path: str) -> bool:
    normalized = relative_path.lower()
    return (
        "local_control_center/agents/runtime" in normalized
        or "local_control_center/agents/providers/" in normalized
        or normalized == "local_control_center/integrations/mcp_gateway.py"
        or "local-control-center/web/src/features/model-gateway/" in normalized
    )


def _should_prune_directory(root: Path, path: Path) -> bool:
    return _is_ignored_path(root, path) or _is_allowed_path(root, path)


def _iter_scannable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = sorted(
            dirname for dirname in dirnames if not _should_prune_directory(root, current / dirname)
        )
        for filename in sorted(filenames):
            path = current / filename
            if _is_scannable(path) and not _is_ignored_path(root, path) and not _is_allowed_path(root, path):
                files.append(path)
    return files


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _scan_line(relative_path: str, line_number: int, line: str) -> list[Violation]:
    violations: list[Violation] = []
    product_line = ALLOWED_REFERENCE_PATTERN.sub("", line)
    if TOKEN_PATTERN.search(product_line):
        violations.append(
            Violation(
                rule_id="prohibited-product-token",
                relative_path=relative_path,
                line_number=line_number,
                message="Prohibited simulation token appears outside tests or docs.",
                line=line,
            )
        )
    if _is_runtime_provider_path(relative_path) and AVAILABLE_TRUE_PATTERN.search(line):
        violations.append(
            Violation(
                rule_id="hardcoded-runtime-available",
                relative_path=relative_path,
                line_number=line_number,
                message="Runtime provider availability must come from a real health check, not available=True.",
                line=line,
            )
        )
    if SHELL_TRUE_PATTERN.search(line):
        violations.append(
            Violation(
                rule_id="shell-true-outside-tests",
                relative_path=relative_path,
                line_number=line_number,
                message="shell" + "=True is allowed only in controlled tests.",
                line=line,
            )
        )
    return violations


def scan_root(root: Path) -> list[Violation]:
    resolved_root = root.resolve()
    violations: list[Violation] = []
    for path in _iter_scannable_files(resolved_root):
        text = _read_text(path)
        if text is None:
            continue
        relative_path = _relative(resolved_root, path)
        for line_number, line in enumerate(text.splitlines(), start=1):
            violations.extend(_scan_line(relative_path, line_number, line))
    return violations


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when productive code exposes simulated execution semantics."
    )
    parser.add_argument("--root", type=Path, default=_default_root(), help="Repository root to scan.")
    parser.add_argument("--max-violations", type=int, default=100, help="Maximum violations to print.")
    args = parser.parse_args(argv)

    violations = scan_root(args.root)
    if not violations:
        print("Productive truth scan passed.")
        return 0

    print(f"Productive truth scan found {len(violations)} violation(s).", file=sys.stderr)
    for violation in violations[: args.max_violations]:
        print(violation.format(), file=sys.stderr)
    if len(violations) > args.max_violations:
        print(f"... {len(violations) - args.max_violations} more violation(s).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
