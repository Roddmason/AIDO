"""Backup/restore consistente y conservador para mantenimiento local de AIDO.

Invariante: nunca sobrescribe un destino ni copia archivos WAL vivos; los backups usan
SQLite backup bajo un bloqueo de escritura breve. Se exige detener el worker previamente.
Los secretos externos y los workspaces no se exportan; DPAPI sigue ligado al usuario nativo.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from contextlib import ExitStack, closing
from pathlib import Path

from local_control_center.shared.db import require_safe_sqlite_runtime
from local_control_center.shared.time import utc_now

BUNDLE_FILES = {"platform.sqlite", ".tmp/operation-inputs.sqlite"}


def _connect(path: Path, mode: str = "ro") -> sqlite3.Connection:
    require_safe_sqlite_runtime()
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode={mode}", uri=True, timeout=3, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    return connection


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_quiescent(connection: sqlite3.Connection) -> None:
    """Rechaza un worker vivo o procesos pendientes; no recupera leases ni termina procesos."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if (
        "worker_leader_leases" in tables
        and connection.execute(
            "SELECT 1 FROM worker_leader_leases WHERE expires_at>?", (utc_now(),)
        ).fetchone()
    ):
        raise RuntimeError("Stop the worker and wait for its leader lease to expire before maintenance.")
    if (
        "managed_processes" in tables
        and connection.execute(
            "SELECT 1 FROM managed_processes WHERE finished_at IS NULL OR released_at IS NULL"
        ).fetchone()
    ):
        raise RuntimeError("Resolve active or abandoned managed processes before maintenance.")


def _sqlite_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    deadline = time.monotonic() + 120

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite backup exceeded its 120 second deadline.")

    with closing(_connect(source)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer, pages=256, progress=progress, sleep=0.05)
        if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite backup integrity check failed.")


def backup_bundle(source: Path, destination: Path) -> dict:
    """Genera un nuevo bundle íntegro; un fallo deja evidencia incompleta y nunca borra el origen."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    with ExitStack() as stack:
        lock = stack.enter_context(closing(_connect(source, "rw")))
        lock.execute("BEGIN IMMEDIATE")
        require_quiescent(lock)
        inputs = source.parent / ".tmp" / "operation-inputs.sqlite"
        sources = {"platform.sqlite": source}
        if inputs.is_file():
            input_lock = stack.enter_context(closing(_connect(inputs, "rw")))
            input_lock.execute("BEGIN IMMEDIATE")
            sources[".tmp/operation-inputs.sqlite"] = inputs
        destination.mkdir(parents=True, exist_ok=False)
        manifest = {
            "formatVersion": 1,
            "status": "incomplete",
            "createdAt": utc_now(),
            "files": {},
            "externalCredentials": "not_exported; preserve the native credential account/keyring separately",
            "workspacesAndArtifacts": "not_copied; preserve referenced directories separately",
        }
        path = destination / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for relative, original in sources.items():
            target = destination / relative
            _sqlite_copy(original, target)
            manifest["files"][relative] = {"sha256": _digest(target), "sizeBytes": target.stat().st_size}
        manifest["status"] = "completed"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest


def restore_bundle(source: Path, destination: Path) -> Path:
    """Verifica hashes y restaura sólo a un directorio nuevo, sin tocar la instancia existente."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files", {})
    if (
        manifest.get("formatVersion") != 1
        or manifest.get("status") != "completed"
        or "platform.sqlite" not in files
    ):
        raise ValueError("Incomplete or unsupported backup manifest.")
    if set(files) - BUNDLE_FILES:
        raise ValueError("Unrecognized backup file; path traversal is not permitted.")
    for relative, metadata in files.items():
        original = (source / relative).resolve(strict=True)
        if not original.is_relative_to(source) or _digest(original) != metadata.get("sha256"):
            raise ValueError("Backup file hash or containment check failed.")
    destination.mkdir(parents=True, exist_ok=False)
    for relative in files:
        _sqlite_copy(source / relative, destination / relative)
    return destination / "platform.sqlite"


def main() -> None:
    """Expone mantenimiento explícito sin activar providers, workers ni restaurar in-place."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = (
        backup_bundle(args.source, args.destination)
        if args.action == "backup"
        else str(restore_bundle(args.source, args.destination))
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
