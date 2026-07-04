"""Validation for ``aido.plugin.json`` manifests.

The validator is intentionally strict: a plugin manifest is a security boundary, not just a
catalog record. It requires explicit permissions, checksummed file contracts, ToolBroker-backed
tools, and agent contracts with role, capabilities, and schema before persistence can proceed.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_control_center.shared.serialization import json_dumps

AIDO_VERSION = "0.1.0"
MANIFEST_FILE = "aido.plugin.json"
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
ALLOWED_TRUST_LEVELS = {"core", "first_party", "verified", "third_party"}
DANGEROUS_PERMISSIONS = {
    "*",
    "shell.unrestricted",
    "process.spawn",
    "secrets.read",
    "secrets.write",
    "network.unrestricted",
    "filesystem.write:*",
    "filesystem.write:/",
    "filesystem.write:\\",
}


class PluginValidationError(ValueError):
    """Raised when a plugin manifest violates the install-time security contract."""


@dataclass(frozen=True)
class ValidatedPluginManifest:
    """Validated manifest plus derived hashes and normalized entrypoint records."""

    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_hash: str
    package_hash: str
    skills: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    tools: list[dict[str, Any]]


def sha256_bytes(payload: bytes) -> str:
    """Return a prefixed SHA-256 digest for bytes."""
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def sha256_file(path: Path) -> str:
    """Return a prefixed SHA-256 digest for a file."""
    return sha256_bytes(path.read_bytes())


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise PluginValidationError("minAidoVersion and version must use MAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _safe_relative_path(root: Path, raw_path: str) -> Path:
    if not raw_path or Path(raw_path).is_absolute():
        raise PluginValidationError("Manifest file paths must be non-empty relative paths.")
    candidate = (root / raw_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PluginValidationError("Manifest file paths must stay inside the plugin directory.") from exc
    return candidate


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginValidationError(f"{MANIFEST_FILE} is not valid JSON.") from exc
    if not isinstance(loaded, dict):
        raise PluginValidationError(f"{MANIFEST_FILE} must be a JSON object.")
    return loaded


def _require_string(manifest: dict[str, Any], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PluginValidationError(f"{field} is required.")
    return value.strip()


def _require_string_list(manifest: dict[str, Any], field: str) -> list[str]:
    value = manifest.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PluginValidationError(f"{field} must be an explicit list of strings.")
    return [item.strip() for item in value]


def _is_dangerous_permission(permission: str) -> bool:
    normalized = permission.strip().lower().replace("\\", "/")
    if normalized in DANGEROUS_PERMISSIONS:
        return True
    if normalized.startswith("filesystem.write:"):
        target = normalized.removeprefix("filesystem.write:").strip()
        return target in {"", "/", "*"} or re.fullmatch(r"[a-z]:/?", target) is not None
    return normalized.endswith(".unrestricted") or normalized.endswith(":*")


def _validate_permissions(permissions: list[str]) -> None:
    dangerous = [permission for permission in permissions if _is_dangerous_permission(permission)]
    if dangerous:
        joined = ", ".join(sorted(dangerous))
        raise PluginValidationError(f"dangerous plugin permissions are blocked: {joined}")


_ELEVATED_PERMISSION_PREFIXES = (
    "filesystem.write",
    "network",
    "process",
    "shell",
    "system",
    "credentials",
    "secrets",
    "env.",
    "http",
    "https",
    "fetch",
    "exec",
    "command",
    "spawn",
    "egress",
    "outbound",
)
_ELEVATED_PERMISSION_VERBS = frozenset(
    {
        "write",
        "exec",
        "execute",
        "run",
        "spawn",
        "delete",
        "remove",
        "destroy",
        "post",
        "put",
        "patch",
        "send",
        "fetch",
        "request",
        "upload",
        "download",
        "egress",
        "mutate",
    }
)


def permission_risk_level(permission: str) -> str:
    """Classify the residual risk of an already-permitted plugin permission.

    Hard-blocked permissions never reach persistence (see ``_validate_permissions``);
    permitted ones still range from read-only (``low``) to mutation- or egress-capable
    (``medium``). The operator console raises a review badge on anything above ``low``.
    Blocklisted permissions map to ``high`` for completeness even though install rejects
    them first.

    Elevation is detected two ways so no verb form slips through: a broad namespace prefix
    match, and a token match against known mutation/egress verbs anywhere in the scope
    (the part before any ``:`` target). This catches ``exec.command``, ``command.run``,
    ``filesystem.remove:reports`` and ``http.post:...`` that a prefix list alone misses,
    while exact-token matching keeps plural nouns like ``runs.read`` at ``low``.
    """
    normalized = permission.strip().lower().replace("\\", "/")
    if _is_dangerous_permission(permission):
        return "high"
    if normalized.startswith(_ELEVATED_PERMISSION_PREFIXES):
        return "medium"
    scope = normalized.split(":", 1)[0]
    if _ELEVATED_PERMISSION_VERBS.intersection(re.split(r"[._]", scope)):
        return "medium"
    return "low"


def _validate_checksums(root: Path, checksums: Any) -> dict[str, str]:
    if not isinstance(checksums, dict) or not checksums:
        raise PluginValidationError("checksums must be a non-empty object.")
    normalized: dict[str, str] = {}
    for raw_path, expected in checksums.items():
        if not isinstance(raw_path, str) or not isinstance(expected, str):
            raise PluginValidationError("checksums must map relative file paths to sha256 digests.")
        if not expected.startswith("sha256:"):
            raise PluginValidationError("checksums must use sha256:<hex> values.")
        candidate = _safe_relative_path(root, raw_path)
        if not candidate.exists() or not candidate.is_file():
            raise PluginValidationError(f"checksummed file does not exist: {raw_path}")
        actual = sha256_file(candidate)
        if actual != expected:
            raise PluginValidationError(f"checksum mismatch for {raw_path}")
        normalized[raw_path] = expected
    return normalized


def _validate_skills(
    root: Path, entrypoints: dict[str, Any], checksums: dict[str, str]
) -> list[dict[str, Any]]:
    raw_skills = entrypoints.get("skills", [])
    if not isinstance(raw_skills, list):
        raise PluginValidationError("entrypoints.skills must be a list.")
    skills: list[dict[str, Any]] = []
    for skill in raw_skills:
        if not isinstance(skill, dict):
            raise PluginValidationError("Every skill entrypoint must be an object.")
        skill_id = str(skill.get("id") or "").strip()
        raw_path = str(skill.get("path") or "").strip()
        if not skill_id or not raw_path:
            raise PluginValidationError("Every skill entrypoint requires id and path.")
        if Path(raw_path).name != "SKILL.md":
            raise PluginValidationError("Plugin skills must point to a SKILL.md contract.")
        if raw_path not in checksums:
            raise PluginValidationError(f"Skill contract must be checksummed: {raw_path}")
        skill_path = _safe_relative_path(root, raw_path)
        content = skill_path.read_text(encoding="utf-8")
        if not content.startswith("---") or "\nname:" not in content:
            raise PluginValidationError("SKILL.md contracts must include frontmatter with name.")
        skills.append(
            {
                "id": skill_id,
                "path": raw_path,
                "contractHash": checksums[raw_path],
                "status": "declared",
            }
        )
    return skills


def _validate_agents(entrypoints: dict[str, Any]) -> list[dict[str, Any]]:
    raw_agents = entrypoints.get("agents", [])
    if not isinstance(raw_agents, list):
        raise PluginValidationError("entrypoints.agents must be a list.")
    agents: list[dict[str, Any]] = []
    for agent in raw_agents:
        if not isinstance(agent, dict):
            raise PluginValidationError("Every agent entrypoint must be an object.")
        agent_id = str(agent.get("id") or "").strip()
        role = str(agent.get("role") or "").strip()
        capabilities = agent.get("capabilities")
        schema = agent.get("schema")
        if not agent_id or not role:
            raise PluginValidationError("Plugin agents require id and role.")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            raise PluginValidationError("Plugin agents require explicit capabilities.")
        if not isinstance(schema, dict) or not schema:
            raise PluginValidationError("Plugin agents require a non-empty schema object.")
        agents.append(
            {
                "id": agent_id,
                "role": role,
                "capabilities": [str(item).strip() for item in capabilities],
                "schema": schema,
                "status": "declared",
            }
        )
    return agents


def _validate_tools(entrypoints: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tools = entrypoints.get("tools", [])
    if not isinstance(raw_tools, list):
        raise PluginValidationError("entrypoints.tools must be a list.")
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict):
            raise PluginValidationError("Every tool entrypoint must be an object.")
        tool_id = str(tool.get("id") or "").strip()
        name = str(tool.get("name") or "").strip()
        broker_tool = str(tool.get("brokerTool") or "").strip()
        schema = tool.get("schema")
        policy = tool.get("policy") or tool.get("policyRef")
        if not tool_id or not name:
            raise PluginValidationError("Plugin tools require id and name.")
        if not broker_tool:
            raise PluginValidationError("Plugin tools must declare brokerTool for ToolBroker routing.")
        if not isinstance(schema, dict) or not schema:
            raise PluginValidationError("Plugin tools require a non-empty schema object.")
        if policy in (None, "", {}):
            raise PluginValidationError("Plugin tools must declare policy before ToolBroker exposure.")
        tools.append(
            {
                "id": tool_id,
                "name": name,
                "brokerTool": broker_tool,
                "execute": bool(tool.get("execute")),
                "policyRequired": True,
                "policy": policy,
                "schema": schema,
                "status": "declared",
            }
        )
    return tools


def _package_hash(manifest_hash: str, checksums: dict[str, str]) -> str:
    payload = json_dumps({"manifest": manifest_hash, "files": checksums}).encode("utf-8")
    return sha256_bytes(payload)


def _raw_manifest_identity(manifest_path: Path) -> dict[str, Any]:
    """Best-effort identity fields from an unvalidated manifest, for scan reporting only."""
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    raw_entrypoints = loaded.get("entrypoints")
    entrypoints = raw_entrypoints if isinstance(raw_entrypoints, dict) else {}
    raw_permissions = loaded.get("permissions")
    permissions = raw_permissions if isinstance(raw_permissions, list) else []

    def _optional_string(field: str) -> str | None:
        value = loaded.get(field)
        return str(value) if isinstance(value, str) and value.strip() else None

    def _entry_count(field: str) -> int:
        entries = entrypoints.get(field)
        return len(entries) if isinstance(entries, list) else 0

    return {
        "id": _optional_string("id"),
        "name": _optional_string("name"),
        "version": _optional_string("version"),
        "publisher": _optional_string("publisher"),
        "trustLevel": _optional_string("trustLevel"),
        "permissions": [str(item) for item in permissions if isinstance(item, str)],
        "skillsCount": _entry_count("skills"),
        "agentsCount": _entry_count("agents"),
        "toolsCount": _entry_count("tools"),
    }


def scan_local_candidates(scan_path: str | Path) -> list[dict[str, Any]]:
    """Scan a local directory for installable plugin candidates without persisting anything.

    Considers ``scan_path`` itself plus its direct children; any directory containing
    ``aido.plugin.json`` is a candidate. Each candidate runs the same strict validation
    as install, but failures are reported per candidate as ``issues`` instead of aborting
    the scan, so an operator can review a folder of mixed plugins safely. Nothing is
    executed, copied, or written during a scan.
    """
    root = Path(scan_path).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise PluginValidationError("Scan path must be an existing local directory.")
    if (root / MANIFEST_FILE).is_file():
        candidate_roots = [root]
    else:
        candidate_roots = sorted(
            (child for child in root.iterdir() if child.is_dir() and (child / MANIFEST_FILE).is_file()),
            key=lambda child: child.name.lower(),
        )
    candidates: list[dict[str, Any]] = []
    for candidate_root in candidate_roots:
        identity = _raw_manifest_identity(candidate_root / MANIFEST_FILE)
        try:
            validated = load_and_validate_manifest(candidate_root)
        except PluginValidationError as exc:
            candidates.append({"path": str(candidate_root), "valid": False, "issues": [str(exc)], **identity})
            continue
        manifest = validated.manifest
        candidates.append(
            {
                "path": str(candidate_root),
                "valid": True,
                "issues": [],
                "id": manifest["id"],
                "name": manifest["name"],
                "version": manifest["version"],
                "publisher": manifest["publisher"],
                "trustLevel": manifest["trustLevel"],
                "permissions": list(manifest["permissions"]),
                "skillsCount": len(validated.skills),
                "agentsCount": len(validated.agents),
                "toolsCount": len(validated.tools),
            }
        )
    return candidates


def load_and_validate_manifest(plugin_path: str | Path) -> ValidatedPluginManifest:
    """Load, normalize, and validate a local plugin manifest directory."""
    root = Path(plugin_path).expanduser().resolve(strict=False)
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.exists() or not manifest_path.is_file():
        raise PluginValidationError(f"{MANIFEST_FILE} not found in local plugin path.")
    manifest = _load_manifest(manifest_path)
    plugin_id = _require_string(manifest, "id")
    if not PLUGIN_ID_RE.match(plugin_id):
        raise PluginValidationError("id must use lowercase letters, numbers, dots, dashes, or underscores.")
    _require_string(manifest, "name")
    _version_tuple(_require_string(manifest, "version"))
    _require_string(manifest, "publisher")
    trust_level = _require_string(manifest, "trustLevel")
    if trust_level not in ALLOWED_TRUST_LEVELS:
        raise PluginValidationError("trustLevel must be core, first_party, verified, or third_party.")
    _require_string_list(manifest, "capabilities")
    permissions = _require_string_list(manifest, "permissions")
    _validate_permissions(permissions)
    min_aido_version = _require_string(manifest, "minAidoVersion")
    if _version_tuple(min_aido_version) > _version_tuple(AIDO_VERSION):
        raise PluginValidationError("minAidoVersion is newer than this AIDO runtime.")
    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise PluginValidationError("entrypoints must be an object.")
    checksums = _validate_checksums(root, manifest.get("checksums"))
    skills = _validate_skills(root, entrypoints, checksums)
    agents = _validate_agents(entrypoints)
    tools = _validate_tools(entrypoints)
    manifest_hash = sha256_file(manifest_path)
    return ValidatedPluginManifest(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_hash=manifest_hash,
        package_hash=_package_hash(manifest_hash, checksums),
        skills=skills,
        agents=agents,
        tools=tools,
    )
