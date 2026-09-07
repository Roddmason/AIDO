"""Per-invocation scratch paths; retained receipts never become pytest basetemp.

@author Rodrigo Mason
"""

from __future__ import annotations

import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from local_control_center.shared.serialization import publish_json_exclusive


def require_non_repository(code: int, stderr: str) -> None:
    """Distinguish Git's non-repository response from permission/configuration failures."""
    if (
        code != 128
        or stderr.strip() != "fatal: not a git repository (or any of the parent directories): .git"
    ):
        raise ValueError(f"Scratch Git context is not proven non-repository (exit {code}): {stderr[:512]}")


def validate_scratch_parent(path: Path, protected: list[Path]) -> Path:
    """Reject aliases and overlap before allocating any disposable directory."""
    path = path.absolute()
    for part in [path, *path.parents]:
        if (part.exists() or part.is_symlink()) and (
            part.is_symlink()
            or (getattr(part, "is_junction", lambda: False)())
            or (getattr(part.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        ):
            raise ValueError(f"Reparse point rejected for quality scratch: {part}")
    resolved = path.resolve()
    for boundary in protected:
        boundary = boundary.resolve()
        if resolved.is_relative_to(boundary) or boundary.is_relative_to(resolved):
            raise ValueError(f"Quality scratch overlaps protected path: {boundary}")
    for parent in [resolved, *resolved.parents]:
        if (parent / ".git").exists():
            raise ValueError(f"Quality scratch is inside a repository: {parent}")
    return resolved


@dataclass(frozen=True)
class QualityPaths:
    """One invocation owns fresh scratch and a physically separate evidence directory."""

    invocation_id: str
    scratch: Path
    evidence: Path

    @classmethod
    def create(cls, parent: Path, evidence: Path, protected: list[Path]) -> QualityPaths:
        """Allocate new paths exclusively; never reuse an existing pytest base."""
        parent = validate_scratch_parent(parent, protected)
        parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="invocation-", dir=parent))
        invocation = uuid.uuid4().hex
        retained = evidence / invocation
        retained.mkdir(parents=True, exist_ok=False)
        result = cls(invocation, scratch, retained)
        publish_json_exclusive(
            retained / "paths.json",
            {
                "invocationId": invocation,
                "scratch": str(scratch),
                "evidence": str(retained),
                "protected": [str(p.resolve()) for p in protected],
            },
        )
        return result

    def environment(self, source: dict[str, str]) -> dict[str, str]:
        """Return child-only settings without mutating the user's environment."""
        return {
            **source,
            "TEMP": str(self.scratch),
            "TMP": str(self.scratch),
            "AIDO_QUALITY_INVOCATION_ID": self.invocation_id,
            "AIDO_QUALITY_SCRATCH": str(self.scratch),
            "AIDO_QUALITY_RETAINED": str(self.evidence),
            "PLAYWRIGHT_ARTIFACT_ROOT": str(self.evidence / "playwright"),
            "AIDO_DIAGNOSTICS_DIR": str(self.evidence / "diagnostics"),
        }

    def prepare_pytest(
        self, argv: tuple[str, ...], source: dict[str, str]
    ) -> tuple[tuple[str, ...], dict[str, str]]:
        """Allocate one attempt with structured pytest paths and independent fixtures."""
        # Never parse Windows paths through PYTEST_ADDOPTS/shlex. Reject destructive inherited destinations.
        if any(
            option in source.get("PYTEST_ADDOPTS", "") for option in ("--basetemp", "--junit", "cache_dir")
        ):
            raise ValueError("Runner owns pytest temporary and evidence destinations; remove path overrides")
        if any(arg.startswith("--basetemp") for arg in argv):
            raise ValueError("Caller-provided pytest basetemp is forbidden")
        attempt = uuid.uuid4().hex
        step = self.scratch / attempt
        step.mkdir(exist_ok=False)
        fixtures = step / "fixtures"
        fixtures.mkdir()
        retained = self.evidence / attempt
        retained.mkdir(exist_ok=False)
        arguments = []
        skip_next = False
        for arg in argv:
            if skip_next:
                skip_next = False
                continue
            if arg in ("--junitxml", "--junit-xml"):
                skip_next = True
            elif not arg.startswith(("--junitxml=", "--junit-xml=")):
                arguments.append(arg)
        arguments.extend(
            (
                "--basetemp",
                str(step / "pytest"),
                "-o",
                f"cache_dir={step / 'cache'}",
                "--junitxml",
                str(retained / "results.xml"),
            )
        )
        env = {
            **self.environment(source),
            "AIDO_QUALITY_FIXTURES": str(fixtures),
            "AIDO_ACCEPTANCE_EVIDENCE": str(retained),
            "AIDO_QUALITY_ATTEMPT_ID": attempt,
        }
        return tuple(arguments), env


def inherited_paths(environment: dict[str, str]) -> QualityPaths | None:
    """Child gates share the invocation, but allocate new attempt paths before each pytest spawn."""
    if not environment.get("AIDO_QUALITY_SCRATCH"):
        return None
    scratch = Path(environment["AIDO_QUALITY_SCRATCH"])
    retained = Path(environment["AIDO_QUALITY_RETAINED"])
    validate_scratch_parent(scratch, [Path.cwd(), retained])
    return QualityPaths(environment["AIDO_QUALITY_INVOCATION_ID"], scratch, retained)
