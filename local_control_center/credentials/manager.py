"""CredentialManager: API segura para crear, validar, rotar, borrar y listar metadatos de secretos.

El valor del secreto entra solo como input (create/rotate), viaja al backend y se descarta; ningún
método lo devuelve. La base guarda solo la referencia al backend, un fingerprint (HMAC-SHA256 con sal
por credencial) y la bitácora de auditoría. ``validate`` lee el secreto del backend internamente para
comparar su fingerprint y lo descarta sin exponerlo. Toda operación queda registrada en la auditoría.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from local_control_center.shared.time import utc_now

from .backends import CredentialBackend, CredentialBackendError
from .repository import CredentialRepository

FINGERPRINT_ALGO = "hmac-sha256"
DEFAULT_BACKEND = "keyring"


def new_salt() -> str:
    """Genera una sal aleatoria (hex) para el fingerprint de una credencial; no es secreta."""
    return secrets.token_hex(16)


def secret_fingerprint(value: str, salt: str) -> str:
    """Calcula el fingerprint HMAC-SHA256 (hex) de un secreto con su sal; es unidireccional, no reversible."""
    return hmac.new(bytes.fromhex(salt), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


class CredentialError(ValueError):
    """Se lanza ante una operación inválida del CredentialManager (input faltante, backend ausente, etc.)."""


class CredentialManager:
    """Orquesta el ciclo de vida de secretos sin exponer su valor; persiste refs, fingerprints y auditoría."""

    def __init__(
        self,
        repository: CredentialRepository,
        *,
        backends: dict[str, CredentialBackend],
        default_backend: str = DEFAULT_BACKEND,
    ):
        self.repository = repository
        self.backends = dict(backends)
        self.default_backend = default_backend

    def _backend(self, kind: str) -> CredentialBackend:
        store = self.backends.get(kind)
        if store is None:
            raise CredentialError(f"No credential backend is registered for: {kind}")
        return store

    @staticmethod
    def _fingerprint(value: str, salt: str) -> str:
        return secret_fingerprint(value, salt)

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        # Proyecta solo metadatos no sensibles: nunca fingerprint ni sal (ni, por supuesto, el valor).
        return {
            "id": record["id"],
            "name": record["name"],
            "backendKind": record["backendKind"],
            "locator": record["locator"],
            "authMode": record["authMode"],
            "status": record["status"],
            "enabled": record["enabled"],
            "fingerprintAlgo": record["fingerprintAlgo"],
            "hasFingerprint": bool(record.get("fingerprint")),
            "rotatedAt": record["rotatedAt"],
            "lastValidatedAt": record["lastValidatedAt"],
            "metadata": record["metadata"],
            "createdAt": record["createdAt"],
            "updatedAt": record["updatedAt"],
        }

    @staticmethod
    def _remove_quietly(store: CredentialBackend, locator: str) -> None:
        try:
            store.remove(locator)
        except CredentialBackendError:
            return

    def _audit(self, record: dict[str, Any], *, action: str, outcome: str, actor: str, detail: str) -> None:
        self.repository.append_audit(
            {
                "credentialId": record.get("id", "unpersisted"),
                "name": record.get("name", ""),
                "action": action,
                "outcome": outcome,
                "actor": actor,
                "backendKind": record.get("backendKind", ""),
                "detail": detail,
            }
        )

    def create_credential(
        self,
        *,
        name: str,
        value: str,
        locator: str,
        backend: str | None = None,
        auth_mode: str = "token",
        actor: str = "operator",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea una credencial: escribe el secreto en el backend y persiste solo su referencia y fingerprint.

        El valor del secreto nunca se guarda en SQLite ni se devuelve. Devuelve únicamente metadatos.

        Raises:
            CredentialError: si falta name/value/locator, ya existe el nombre, el backend no está
                registrado o el backend rechaza la escritura.
        """
        if not str(name or "").strip():
            raise CredentialError("Credential name is required.")
        if not str(value or ""):
            raise CredentialError("Credential value is required.")
        if not str(locator or "").strip():
            raise CredentialError("Credential locator is required.")
        if self.repository.find_credential_by_name(name) is not None:
            raise CredentialError(f"Credential already exists: {name}")
        kind = backend or self.default_backend
        store = self._backend(kind)
        salt = new_salt()
        fingerprint = self._fingerprint(value, salt)
        try:
            store.write(locator, value)
        except CredentialBackendError as error:
            self._audit(
                {"name": name, "backendKind": kind},
                action="create",
                outcome="failure",
                actor=actor,
                detail=str(error),
            )
            raise CredentialError(str(error)) from error
        try:
            record = self.repository.create_credential_ref(
                {
                    "name": name,
                    "backendKind": kind,
                    "locator": locator,
                    "fingerprint": fingerprint,
                    "fingerprintAlgo": FINGERPRINT_ALGO,
                    "salt": salt,
                    "authMode": auth_mode,
                    "status": "active",
                    "metadata": metadata or {},
                }
            )
        except Exception as error:
            # El secreto ya está en el backend pero no se pudo registrar: revierte el huérfano y audita.
            self._remove_quietly(store, locator)
            self._audit(
                {"name": name, "backendKind": kind},
                action="create",
                outcome="failure",
                actor=actor,
                detail="Failed to persist credential reference; backend secret rolled back.",
            )
            raise CredentialError("Failed to persist credential reference.") from error
        self._audit(
            record, action="create", outcome="success", actor=actor, detail=f"Stored in {kind} backend."
        )
        return self._public(record)

    def validate(self, name: str, *, actor: str = "operator") -> dict[str, Any]:
        """Valida una credencial comparando el fingerprint del secreto en el backend; nunca devuelve el valor.

        Devuelve ``{name, present, valid, fingerprintMatches}``. Lee el secreto de forma interna solo
        para hashearlo y lo descarta.

        Raises:
            KeyError: si no existe una credencial con ese nombre.
            CredentialError: si su backend no está registrado.
        """
        record = self.repository.get_credential_by_name(name)
        store = self._backend(record["backendKind"])
        try:
            value = store.read(record["locator"])
        except CredentialBackendError as error:
            self._audit(record, action="validate", outcome="failure", actor=actor, detail=str(error))
            return {"name": name, "present": False, "valid": False, "fingerprintMatches": False}
        if value is None:
            self.repository.update_credential(
                record["id"], {"status": "missing", "lastValidatedAt": utc_now()}
            )
            self._audit(
                record, action="validate", outcome="missing", actor=actor, detail="Secret absent in backend."
            )
            return {"name": name, "present": False, "valid": False, "fingerprintMatches": False}
        fingerprint = record.get("fingerprint")
        salt = record.get("salt")
        if not fingerprint or not salt:
            matches = False
        else:
            matches = hmac.compare_digest(self._fingerprint(value, salt), fingerprint)
        self.repository.update_credential(record["id"], {"status": "active", "lastValidatedAt": utc_now()})
        self._audit(
            record,
            action="validate",
            outcome="success" if matches else "mismatch",
            actor=actor,
            detail="Fingerprint match." if matches else "Fingerprint mismatch.",
        )
        return {"name": name, "present": True, "valid": matches, "fingerprintMatches": matches}

    def rotate(self, name: str, new_value: str, *, actor: str = "operator") -> dict[str, Any]:
        """Rota una credencial: reescribe el secreto en el backend y actualiza fingerprint/sal y rotatedAt.

        No devuelve el valor.

        Raises:
            KeyError: si la credencial no existe.
            CredentialError: si falta el nuevo valor o el backend rechaza la escritura.
        """
        if not str(new_value or ""):
            raise CredentialError("New credential value is required.")
        record = self.repository.get_credential_by_name(name)
        store = self._backend(record["backendKind"])
        salt = new_salt()
        fingerprint = self._fingerprint(new_value, salt)
        try:
            store.write(record["locator"], new_value)
        except CredentialBackendError as error:
            self._audit(record, action="rotate", outcome="failure", actor=actor, detail=str(error))
            raise CredentialError(str(error)) from error
        updated = self.repository.update_credential(
            record["id"],
            {"fingerprint": fingerprint, "salt": salt, "status": "active", "rotatedAt": utc_now()},
        )
        self._audit(updated, action="rotate", outcome="success", actor=actor, detail="Secret rotated.")
        return self._public(updated)

    def delete(self, name: str, *, actor: str = "operator") -> dict[str, Any]:
        """Borra una credencial: la elimina del backend y quita su referencia, conservando la auditoría.

        Raises:
            KeyError: si la credencial no existe.
            CredentialError: si su backend no está registrado.
        """
        record = self.repository.get_credential_by_name(name)
        store = self._backend(record["backendKind"])
        detail = "Secret removed from backend."
        try:
            store.remove(record["locator"])
        except CredentialBackendError as error:
            if not getattr(store, "read_only", False):
                # Un backend de escritura que no pudo borrar deja un secreto huérfano: audita el fallo,
                # conserva la referencia para no perderle el rastro y propaga el error.
                self._audit(
                    record,
                    action="delete",
                    outcome="failure",
                    actor=actor,
                    detail=f"Backend removal failed: {error}",
                )
                raise CredentialError(f"Backend removal failed: {error}") from error
            detail = "Backend is read-only; the referenced secret is not managed here."
        self._audit(record, action="delete", outcome="success", actor=actor, detail=detail)
        self.repository.delete_credential_ref(record["id"])
        return {"name": name, "deleted": True}

    def list_metadata(self) -> list[dict[str, Any]]:
        """Lista los metadatos de todas las credenciales (sin valor, fingerprint ni sal)."""
        return [self._public(record) for record in self.repository.list_credential_refs()]
