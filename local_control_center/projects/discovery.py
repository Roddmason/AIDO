"""Descubre el perfil de un proyecto leyendo sus manifiestos (package.json, pyproject, pom, etc.).

Cada parser por ecosistema extrae nombre, fuentes y runtimes detectados sin ejecutar nada;
``discover_project_path`` los combina, deduplica runtimes y sugiere nombre y plantilla.
Es solo lectura y acota el tamaño de cada manifiesto a ``MAX_MANIFEST_BYTES`` para no leer
archivos arbitrariamente grandes.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

MAX_MANIFEST_BYTES = 512 * 1024


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return data[:MAX_MANIFEST_BYTES].decode("utf-8", errors="replace")


def _clean_package_name(name: str) -> str:
    value = name.strip()
    if "/" in value and value.startswith("@"):
        value = value.split("/", 1)[1]
    return value or "Project"


def _source(manifest: str, *, name: str | None = None, kind: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"manifest": manifest}
    if name:
        payload["name"] = name
    if kind:
        payload["kind"] = kind
    return payload


def _runtime(
    runtime_id: str, *, kind: str, label: str, manifest: str, path: str = ".", confidence: float = 0.85
) -> dict[str, Any]:
    return {
        "id": runtime_id,
        "kind": kind,
        "label": label,
        "manifest": manifest,
        "path": path,
        "confidence": confidence,
    }


def _parse_package_json(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]], str]:
    manifest = root / "package.json"
    if not manifest.exists():
        return None, [], [], "other"
    try:
        data = json.loads(_read_text(manifest))
    except json.JSONDecodeError:
        data = {}
    name = _clean_package_name(str(data.get("name") or "")) if data.get("name") else None
    dependencies = {
        **(data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}),
        **(data.get("devDependencies") if isinstance(data.get("devDependencies"), dict) else {}),
    }
    is_frontend = any(
        package in dependencies for package in ("react", "vite", "@vitejs/plugin-react", "next")
    )
    runtime_kind = "frontend" if is_frontend else "tooling"
    template_id = "react-vite" if is_frontend else "node-cli"
    return (
        name,
        [_source("package.json", name=name, kind=runtime_kind)],
        [_runtime("node", kind=runtime_kind, label="Node.js", manifest="package.json")],
        template_id,
    )


def _parse_pyproject(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = root / "pyproject.toml"
    if not manifest.exists():
        return None, [], []
    try:
        data = tomllib.loads(_read_text(manifest))
    except tomllib.TOMLDecodeError:
        data = {}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    name = str(project.get("name") or "").strip() or None
    return (
        name,
        [_source("pyproject.toml", name=name, kind="backend")],
        [_runtime("python", kind="backend", label="Python", manifest="pyproject.toml")],
    )


def _parse_build_json(root: Path) -> tuple[str | None, list[dict[str, Any]]]:
    manifest = root / "build.json"
    if not manifest.exists():
        return None, []
    try:
        data = json.loads(_read_text(manifest))
    except json.JSONDecodeError:
        data = {}
    name = str(data.get("name") or data.get("project") or "").strip() or None
    return name, [_source("build.json", name=name, kind="build")]


def _parse_pom(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = root / "pom.xml"
    if not manifest.exists():
        return None, [], []
    name: str | None = None
    artifact_id: str | None = None
    try:
        xml_root = ET.fromstring(_read_text(manifest))
        for element in xml_root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            text = (element.text or "").strip()
            if tag == "name" and text and not name:
                name = text
            if tag == "artifactId" and text and not artifact_id:
                artifact_id = text
    except ET.ParseError:
        pass
    resolved_name = name or artifact_id
    return (
        resolved_name,
        [_source("pom.xml", name=resolved_name, kind="backend")],
        [_runtime("java-maven", kind="backend", label="Java / Maven", manifest="pom.xml")],
    )


def _parse_gradle(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    for filename in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"):
        manifest = root / filename
        if not manifest.exists():
            continue
        text = _read_text(manifest)
        match = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", text)
        name = match.group(1).strip() if match else None
        return (
            name,
            [_source(filename, name=name, kind="backend")],
            [_runtime("java-gradle", kind="backend", label="Java / Gradle", manifest=filename)],
        )
    return None, [], []


def _parse_cargo(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = root / "Cargo.toml"
    if not manifest.exists():
        return None, [], []
    try:
        data = tomllib.loads(_read_text(manifest))
    except tomllib.TOMLDecodeError:
        data = {}
    package = data.get("package") if isinstance(data.get("package"), dict) else {}
    name = str(package.get("name") or "").strip() or None
    return (
        name,
        [_source("Cargo.toml", name=name, kind="backend")],
        [_runtime("rust", kind="backend", label="Rust", manifest="Cargo.toml")],
    )


def _parse_requirements(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = root / "requirements.txt"
    if not manifest.exists():
        return None, [], []
    return (
        None,
        [_source("requirements.txt", kind="backend")],
        [_runtime("python", kind="backend", label="Python", manifest="requirements.txt")],
    )


def _parse_go_mod(root: Path) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = root / "go.mod"
    if not manifest.exists():
        return None, [], []
    match = re.search(r"^module\s+(\S+)", _read_text(manifest), re.MULTILINE)
    module_path = match.group(1).strip() if match else ""
    name = module_path.rsplit("/", 1)[-1] or None
    return (
        name,
        [_source("go.mod", name=name, kind="backend")],
        [_runtime("go", kind="backend", label="Go", manifest="go.mod")],
    )


def _detect_git(root: Path) -> list[dict[str, Any]]:
    # `.git` is a directory in normal clones but a file in linked worktrees and submodules.
    if (root / ".git").exists():
        return [_source(".git", kind="vcs")]
    return []


def _dedupe_runtimes_by_id(runtimes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for runtime in runtimes:
        runtime_id = str(runtime.get("id", ""))
        if runtime_id in seen:
            continue
        seen.add(runtime_id)
        unique.append(runtime)
    return unique


def _detect_terraform(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    runtimes: list[dict[str, Any]] = []
    for manifest in sorted(root.rglob("*.tf"))[:8]:
        relative = manifest.relative_to(root).as_posix()
        sources.append(_source(relative, kind="infra"))
        if not any(runtime["id"] == "terraform" for runtime in runtimes):
            runtime_path = "." if manifest.parent == root else manifest.parent.relative_to(root).as_posix()
            runtimes.append(
                _runtime("terraform", kind="infra", label="Terraform", manifest=relative, path=runtime_path)
            )
    return sources, runtimes


def discover_project_path(path: str | Path) -> dict[str, Any]:
    """Inspecciona una ruta y resume runtimes, manifiestos, nombre y plantilla sugeridos.

    Corre cada parser de ecosistema sobre el directorio (o el padre, si ``path`` es un
    archivo), añade Terraform y Git, y deduplica runtimes por id. No falla si la ruta no
    existe: refleja ``exists``/``isDirectory`` y degrada el nombre a ``root.name``.
    """
    root = Path(path).expanduser()
    exists = root.exists()
    manifest_root = root if root.is_dir() else root.parent
    manifest_sources: list[dict[str, Any]] = []
    detected_runtimes: list[dict[str, Any]] = []
    name_candidates: list[str] = []
    template_id = "other"

    if exists and manifest_root.is_dir():
        for parser in (
            _parse_package_json,
            _parse_pyproject,
            _parse_build_json,
            _parse_pom,
            _parse_gradle,
            _parse_cargo,
            _parse_requirements,
            _parse_go_mod,
        ):
            parsed = parser(manifest_root)
            if len(parsed) == 4:
                name, sources, runtimes, parsed_template = parsed
                if parsed_template != "other" and template_id == "other":
                    template_id = parsed_template
            elif len(parsed) == 3:
                name, sources, runtimes = parsed
            else:
                name, sources = parsed
                runtimes = []
            if name:
                name_candidates.append(str(name))
            manifest_sources.extend(sources)
            detected_runtimes.extend(runtimes)

        terraform_sources, terraform_runtimes = _detect_terraform(manifest_root)
        manifest_sources.extend(terraform_sources)
        detected_runtimes.extend(terraform_runtimes)

        manifest_sources.extend(_detect_git(manifest_root))

    detected_runtimes = _dedupe_runtimes_by_id(detected_runtimes)
    suggested_name = name_candidates[0] if name_candidates else (root.name or "Project")
    return {
        "path": str(root),
        "exists": exists,
        "isDirectory": root.is_dir(),
        "suggestedName": suggested_name,
        "templateId": template_id,
        "manifestSources": manifest_sources,
        "detectedRuntimes": detected_runtimes,
    }
