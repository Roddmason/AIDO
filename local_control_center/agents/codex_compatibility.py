"""Compatibilidad Codex basada en capacidades, identidad binaria y smoke seguro verificable.

El help demuestra sintaxis, no aislamiento. Un cambio de binario, contrato o versión invalida
el smoke anterior. GET sólo lee evidencia durable; el probe explícito se ejecuta en el worker.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from local_control_center.process_supervision.service import run_probe_command
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

REQUIRED_FLAGS = frozenset(
    {
        "--ask-for-approval",
        "--sandbox",
        "--cd",
        "--config",
        "--disable",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
    }
)


def parse_codex_version(version: str) -> tuple[int, int, int] | None:
    """Acepta semver estable del CLI; prereleases y texto ambiguo requieren revisión del contrato."""
    match = re.fullmatch(r"codex-cli (0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version.strip())
    return tuple(map(int, match.groups())) if match else None


def contract_fingerprint() -> str:
    """Vincula la aprobación al argv de aislamiento vigente, incluido el prefijo read-only."""
    from .runtime_registry import PRODUCT_OWNER_CODEX_ALLOWED_ENVIRONMENT_KEYS, PRODUCT_OWNER_CODEX_EXTRA_ARGS

    return hashlib.sha256(
        json_dumps(
            [
                "--ask-for-approval",
                "never",
                "exec",
                "--sandbox",
                "read-only",
                *PRODUCT_OWNER_CODEX_EXTRA_ARGS,
                "--json",
                "minimal-isolated-environment-v1",
                "exact-command-evidence-v1",
                *sorted(PRODUCT_OWNER_CODEX_ALLOWED_ENVIRONMENT_KEYS),
            ]
        ).encode()
    ).hexdigest()


def binary_fingerprint(path: Path) -> str:
    """Calcula SHA-256 por bloques y rechaza un binario modificado durante la lectura."""
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("Codex executable changed during fingerprinting.")
    return digest


def assess_capabilities(
    version: str, help_text: str, fingerprint: str, receipt: dict | None
) -> dict[str, Any]:
    """Calcula un veredicto puro; una versión nueva nunca hereda aprobación por rango semver."""
    flags = set(re.findall(r"--[a-z][a-z0-9-]*", help_text))
    missing = sorted(REQUIRED_FLAGS - flags)
    reasons = []
    if parse_codex_version(version) is None:
        reasons.append("unsupported_version_format")
    if missing:
        reasons.append("required_flags_missing")
    if not fingerprint:
        reasons.append("binary_fingerprint_missing")
    status = "incompatible" if reasons else "validation_required"
    if not reasons:
        expected = {
            "version": version,
            "binaryFingerprint": fingerprint,
            "contractFingerprint": contract_fingerprint(),
            "status": "validated",
        }
        if receipt and all(receipt.get(key) == value for key, value in expected.items()):
            status = "compatible"
        else:
            reasons.append("validated_smoke_missing")
    return {
        "status": status,
        "version": version,
        "binaryFingerprint": fingerprint,
        "contractFingerprint": contract_fingerprint(),
        "requiredFlags": sorted(REQUIRED_FLAGS),
        "missingFlags": missing,
        "blockingReasons": reasons,
    }


class CodexCompatibilityService:
    """Mantiene probes y receipts separados; nunca acepta una aprobación enviada por el cliente."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def probe(self, executable: str) -> dict[str, Any]:
        """Sondea versión y help con timeout corto, sin autenticación ni inferencia de modelo."""
        resolved = shutil.which(executable)
        if not resolved:
            return {"status": "incompatible", "blockingReasons": ["not_installed"]}
        path = Path(resolved).resolve()
        fingerprint = binary_fingerprint(path)
        version = run_probe_command([str(path), "--version"], capture_output=True, text=True, timeout=5)
        help_result = run_probe_command(
            [str(path), "exec", "--help"], capture_output=True, text=True, timeout=5
        )
        global_help = run_probe_command([str(path), "--help"], capture_output=True, text=True, timeout=5)
        if version.returncode != 0 or help_result.returncode != 0 or global_help.returncode != 0:
            raise ValueError("Codex capability probe failed.")
        if binary_fingerprint(path) != fingerprint:
            raise ValueError("Codex executable changed during capability probe.")
        stat = path.stat()
        version_text = version.stdout.strip()
        flags_text = " ".join(
            sorted(set(re.findall(r"--[a-z][a-z0-9-]*", help_result.stdout + "\n" + global_help.stdout)))
        )
        self.connection.execute(
            """INSERT INTO codex_capability_probes (executable, binary_fingerprint, file_size, mtime_ns, version, flags, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(executable) DO UPDATE SET binary_fingerprint=excluded.binary_fingerprint,
            file_size=excluded.file_size, mtime_ns=excluded.mtime_ns, version=excluded.version, flags=excluded.flags, checked_at=excluded.checked_at""",
            (str(path), fingerprint, stat.st_size, stat.st_mtime_ns, version_text, flags_text, utc_now()),
        )
        return self.status(str(path))

    def status(self, executable: str, *, verify_hash: bool = False) -> dict[str, Any]:
        """Lee evidencia local; el builder verifica además el hash antes de una ejecución real."""
        resolved = shutil.which(executable)
        row = None
        if resolved:
            path = Path(resolved).resolve()
            row = self.connection.execute(
                "SELECT * FROM codex_capability_probes WHERE executable=?", (str(path),)
            ).fetchone()
        if not row:
            return {"status": "validation_required", "blockingReasons": ["capability_probe_required"]}
        try:
            stat = path.stat()
            changed = stat.st_size != row["file_size"] or stat.st_mtime_ns != row["mtime_ns"]
            if verify_hash:
                changed |= binary_fingerprint(path) != row["binary_fingerprint"]
        except OSError:
            changed = True
        if changed:
            return {"status": "validation_required", "blockingReasons": ["executable_changed"]}
        receipt = self.connection.execute(
            "SELECT receipt_json FROM codex_smoke_receipts WHERE binary_fingerprint=? AND contract_fingerprint=? ORDER BY checked_at DESC LIMIT 1",
            (row["binary_fingerprint"], contract_fingerprint()),
        ).fetchone()
        result = assess_capabilities(
            row["version"],
            row["flags"],
            row["binary_fingerprint"],
            json_loads(receipt[0]) if receipt else None,
        )
        result["lastCheckedAt"] = row["checked_at"]
        return result
