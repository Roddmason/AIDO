"""Adaptadores de backend de credenciales: dónde vive realmente el secreto, fuera de SQLite.

Define el protocolo ``CredentialBackend`` (write/read/remove) y sus implementaciones: keyring (por
defecto, Windows Credential Manager), environment_override (solo bootstrap/CI, de solo lectura), vault
y openbao (KV v2 por HTTP) y dpapi_sqlite (opcional, cifra con DPAPI de Windows y guarda el blob
cifrado en su propio SQLite). Las dependencias externas (keyring, win32crypt) se importan de forma
perezosa y, si faltan o la operación no aplica, fallan cerrado con un error claro. Ningún adaptador
imprime ni registra el valor.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlparse

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
HTTP_TIMEOUT_SECONDS = 15


class CredentialBackendError(RuntimeError):
    """Se lanza cuando un backend de credenciales no puede ejecutar la operación pedida."""


@runtime_checkable
class CredentialBackend(Protocol):
    """Contrato de un almacén de secretos: escribe, lee y borra por un ``locator`` opaco."""

    backend_kind: str

    def write(self, locator: str, value: str) -> None:
        """Guarda ``value`` bajo ``locator`` en el almacén subyacente."""
        ...

    def read(self, locator: str) -> str | None:
        """Devuelve el secreto bajo ``locator``, o ``None`` si no existe."""
        ...

    def remove(self, locator: str) -> None:
        """Elimina el secreto bajo ``locator`` del almacén subyacente."""
        ...


class KeyringBackend:
    """Backend por defecto: el keyring del sistema (Windows Credential Manager). Locator ``service/account``."""

    backend_kind = "keyring"

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as error:
            raise CredentialBackendError(
                "Python keyring is not installed; install it or use another backend."
            ) from error
        return keyring

    @staticmethod
    def _parse(locator: str) -> tuple[str, str]:
        service, separator, account = locator.partition("/")
        if not separator or not service.strip() or not account.strip():
            raise CredentialBackendError("keyring locator must be 'service/account'.")
        return service, account

    def write(self, locator: str, value: str) -> None:
        """Guarda el secreto en el keyring del sistema bajo service/account."""
        service, account = self._parse(locator)
        keyring = self._keyring()
        try:
            keyring.set_password(service, account, value)
        except Exception as error:
            raise CredentialBackendError(f"keyring write failed: {error}") from error

    def read(self, locator: str) -> str | None:
        """Lee el secreto del keyring del sistema, o ``None`` si no está."""
        service, account = self._parse(locator)
        keyring = self._keyring()
        try:
            return keyring.get_password(service, account)
        except Exception as error:
            raise CredentialBackendError(f"keyring read failed: {error}") from error

    def remove(self, locator: str) -> None:
        """Borra el secreto del keyring; ignora que ya no exista, falla cerrado ante otros errores."""
        service, account = self._parse(locator)
        keyring = self._keyring()
        try:
            keyring.delete_password(service, account)
        except Exception as error:
            if type(error).__name__ == "PasswordDeleteError":
                return
            raise CredentialBackendError(f"keyring delete failed: {error}") from error


class EnvironmentOverrideBackend:
    """Backend de solo lectura para bootstrap/CI: el secreto viene de una variable de entorno."""

    backend_kind = "environment_override"
    read_only = True

    def __init__(self, env: dict[str, str] | None = None):
        self._env = env

    def write(self, locator: str, value: str) -> None:
        """Siempre falla: este backend es solo bootstrap y de solo lectura."""
        raise CredentialBackendError(
            "The environment_override backend is bootstrap-only and read-only; cannot write."
        )

    def read(self, locator: str) -> str | None:
        """Lee el secreto de la variable de entorno nombrada por ``locator``."""
        source = os.environ if self._env is None else self._env
        return source.get(locator) or None

    def remove(self, locator: str) -> None:
        """Siempre falla: este backend es solo bootstrap; no borra variables de entorno."""
        raise CredentialBackendError("The environment_override backend is bootstrap-only; cannot delete.")


EnvBackend = EnvironmentOverrideBackend


class VaultBackend:
    """Backend HashiCorp Vault / OpenBao (KV v2 por HTTP). Locator ``mount/path#field``."""

    def __init__(self, *, backend_kind: str, address: str, token: str, default_mount: str = "secret"):
        self.backend_kind = backend_kind
        self._address = address.rstrip("/")
        self._token = token
        self._default_mount = default_mount
        self._verify_address()

    def _verify_address(self) -> None:
        parsed = urlparse(self._address)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CredentialBackendError("Vault address must be an absolute http(s) URL.")
        if parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS:
            raise CredentialBackendError("Vault over http is only allowed on loopback; use https.")

    @staticmethod
    def _parse(locator: str) -> tuple[str, str, str]:
        target, separator, field = locator.partition("#")
        if not separator or not field.strip():
            raise CredentialBackendError("vault locator must be 'mount/path#field'.")
        mount, slash, path = target.partition("/")
        if not slash or not mount.strip() or not path.strip():
            raise CredentialBackendError("vault locator must be 'mount/path#field'.")
        return mount, path, field

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("X-Vault-Token", self._token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {}
            raise CredentialBackendError(f"Vault request failed with status {error.code}.") from error
        except urllib.error.URLError as error:
            raise CredentialBackendError("Vault is unreachable.") from error
        return json.loads(body) if body else {}

    def write(self, locator: str, value: str) -> None:
        """Escribe el secreto en el motor KV v2 de Vault/OpenBao."""
        mount, path, field = self._parse(locator)
        url = f"{self._address}/v1/{quote(mount)}/data/{quote(path)}"
        self._request("POST", url, {"data": {field: value}})

    def read(self, locator: str) -> str | None:
        """Lee el campo del secreto del motor KV v2, o ``None`` si no existe."""
        mount, path, field = self._parse(locator)
        url = f"{self._address}/v1/{quote(mount)}/data/{quote(path)}"
        payload = self._request("GET", url)
        data = (((payload.get("data") or {}).get("data")) or {}) if isinstance(payload, dict) else {}
        value = data.get(field)
        return str(value) if value is not None else None

    def remove(self, locator: str) -> None:
        """Borra todas las versiones del secreto (metadata delete de KV v2)."""
        mount, path, _field = self._parse(locator)
        url = f"{self._address}/v1/{quote(mount)}/metadata/{quote(path)}"
        self._request("DELETE", url)


class DpapiSqliteBackend:
    """Backend opcional (Windows): cifra con DPAPI y guarda el blob cifrado en su propio SQLite."""

    backend_kind = "dpapi_sqlite"

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS credential_blobs (locator TEXT PRIMARY KEY, ciphertext BLOB NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=30)

    @staticmethod
    def _crypt() -> Any:
        try:
            import win32crypt  # type: ignore[import-not-found]
        except ImportError as error:
            raise CredentialBackendError("DPAPI backend requires pywin32 (win32crypt) on Windows.") from error
        return win32crypt

    def write(self, locator: str, value: str) -> None:
        """Cifra el secreto con DPAPI y guarda el blob cifrado (nunca el texto plano)."""
        crypt = self._crypt()
        try:
            ciphertext = crypt.CryptProtectData(value.encode("utf-8"), None, None, None, None, 0)
        except Exception as error:
            raise CredentialBackendError("DPAPI encryption failed.") from error
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO credential_blobs (locator, ciphertext) VALUES (?, ?)",
                (locator, ciphertext),
            )

    def read(self, locator: str) -> str | None:
        """Descifra el blob DPAPI y devuelve el secreto, o ``None`` si no está."""
        crypt = self._crypt()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT ciphertext FROM credential_blobs WHERE locator = ?", (locator,)
            ).fetchone()
        if not row:
            return None
        try:
            _description, plaintext = crypt.CryptUnprotectData(row[0], None, None, None, 0)
        except Exception as error:
            raise CredentialBackendError("DPAPI decryption failed.") from error
        return bytes(plaintext).decode("utf-8")

    def remove(self, locator: str) -> None:
        """Borra el blob cifrado del SQLite del backend."""
        with self._connect() as connection:
            connection.execute("DELETE FROM credential_blobs WHERE locator = ?", (locator,))


def default_backends(env: dict[str, str] | None = None) -> dict[str, CredentialBackend]:
    """Construye el registro de backends productivos; keyring es el predeterminado.

    Vault/OpenBao se registran solo si su dirección y token están en el entorno de bootstrap
    (``AIDO_VAULT_ADDR``/``AIDO_VAULT_TOKEN``, ``AIDO_OPENBAO_ADDR``/``AIDO_OPENBAO_TOKEN``); el secreto
    de auth a Vault es bootstrap, no configuración normal. ``dpapi_sqlite`` se registra solo cuando
    ``AIDO_DPAPI_SQLITE_PATH`` apunta a su SQLite cifrada. ``env`` queda como alias legacy interno de
    ``environment_override`` para filas antiguas.
    """
    source = os.environ if env is None else env
    environment_override = EnvironmentOverrideBackend(env)
    backends: dict[str, CredentialBackend] = {
        "keyring": KeyringBackend(),
        "environment_override": environment_override,
        "env": environment_override,
    }
    dpapi_path = str(source.get("AIDO_DPAPI_SQLITE_PATH") or "").strip()
    if dpapi_path:
        backends["dpapi_sqlite"] = DpapiSqliteBackend(Path(dpapi_path))
    for kind, addr_var, token_var in (
        ("vault", "AIDO_VAULT_ADDR", "AIDO_VAULT_TOKEN"),
        ("openbao", "AIDO_OPENBAO_ADDR", "AIDO_OPENBAO_TOKEN"),
    ):
        address = str(source.get(addr_var) or "").strip()
        token = str(source.get(token_var) or "").strip()
        if address and token:
            backends[kind] = VaultBackend(backend_kind=kind, address=address, token=token)
    return backends
