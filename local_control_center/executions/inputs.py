"""Almacén cifrado de inputs durables; los prompts no se guardan en logs ni filas operacionales.

Reutiliza DPAPI en Windows y el keyring del sistema en POSIX. Si el almacén seguro no está
disponible, falla cerrado: nunca degrada a JSON/plano. El locator es opaco y no contiene input.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from local_control_center.credentials.backends import (
    CredentialBackendError,
    DpapiSqliteBackend,
    KeyringBackend,
)
from local_control_center.shared.serialization import json_dumps, json_loads


class OperationInputStore:
    """Protege inputs con el almacén nativo del usuario que ejecuta API y worker."""

    def __init__(self, db_path: Path):
        self.backend = (
            DpapiSqliteBackend(db_path.parent / ".tmp" / "operation-inputs.sqlite")
            if os.name == "nt"
            else KeyringBackend()
        )

    def put(self, arguments: dict[str, Any]) -> str:
        """Escribe un input cifrado antes de iniciar la transacción de enqueue."""
        locator = f"aido-operation-inputs/{uuid.uuid4().hex}"
        self.backend.write(locator, json_dumps(arguments))
        return locator

    def get(self, locator: str) -> dict[str, Any]:
        """Recupera únicamente un locator propio para el runner autorizado por fencing."""
        if not locator.startswith("aido-operation-inputs/"):
            raise CredentialBackendError("Invalid operation input locator.")
        raw = self.backend.read(locator)
        if raw is None:
            raise CredentialBackendError("Operation input expired or secure storage is unavailable.")
        result = json_loads(raw, None)
        if not isinstance(result, dict):
            raise CredentialBackendError("Invalid sealed operation input.")
        return result

    def remove(self, locator: str) -> None:
        """Retira el input propio si el enqueue falla antes de crear una ejecución durable."""
        if not locator.startswith("aido-operation-inputs/"):
            raise CredentialBackendError("Invalid operation input locator.")
        self.backend.remove(locator)
