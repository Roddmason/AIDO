"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import os
import re
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urlparse


ENV_REF_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LEGACY_ENV_REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+_(API_KEY|TOKEN|SECRET|CREDENTIAL|PASSWORD)$")
LOOPBACK_VAULT_HOSTS = {"127.0.0.1", "::1", "localhost"}
SECRET_LIKE_PATTERN = re.compile(
    r"(?i)(^bearer\s+|^sk-[A-Za-z0-9_-]{8,}|api[_-]?key\s*=|secret\s*=|token\s*=)"
)


@dataclass(frozen=True, repr=False)
class CredentialResolution:
    ref: str
    status: str
    source: str
    value: str | None = None
    message: str = ""

    @property
    def configured(self) -> bool:
        return self.status == "configured" and bool(self.value)

    def __repr__(self) -> str:
        return (
            "CredentialResolution("
            f"ref={self.ref!r}, status={self.status!r}, source={self.source!r}, "
            "value='[redacted]', "
            f"message={self.message!r})"
        )

    def to_public_dict(self) -> dict[str, str]:
        return {
            "credentialRef": self.ref,
            "credentialStatus": self.status,
            "credentialSource": self.source,
            "message": self.message,
        }


class CredentialResolver:
    """Resolve credential references without persisting raw credential values."""

    def __init__(
        self,
        *,
        http_json_get: Callable[[str, dict[str, str], float], dict[str, object]] | None = None,
        http_json_post: Callable[[str, dict[str, str], dict[str, str], float], dict[str, object]]
        | None = None,
    ):
        self.http_json_get = http_json_get or self._default_http_json_get
        self.http_json_post = http_json_post or self._default_http_json_post

    def resolve(self, credential_ref: str | None, *, fetch: bool = True) -> CredentialResolution:
        ref = (credential_ref or "").strip()
        if not ref:
            return CredentialResolution(
                ref="", status="unknown", source="none", message="No credential ref configured"
            )
        if self._looks_like_raw_secret(ref):
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="invalid",
                message=(
                    "credential_ref must reference env:NAME, keyring:service/account, "
                    "openbao:mount/path#field or vault:mount/path#field, never a raw secret"
                ),
            )
        if ref.startswith("env:"):
            return self._resolve_env(ref.removeprefix("env:"), public_ref=ref, fetch=fetch)
        if ref.startswith("keyring:"):
            return self._resolve_keyring(ref.removeprefix("keyring:"), public_ref=ref, fetch=fetch)
        if ref.startswith("openbao:"):
            return self._resolve_vault_compatible(
                ref.removeprefix("openbao:"), public_ref=ref, source="openbao", fetch=fetch
            )
        if ref.startswith("vault:"):
            return self._resolve_vault_compatible(
                ref.removeprefix("vault:"), public_ref=ref, source="vault", fetch=fetch
            )
        if LEGACY_ENV_REF_PATTERN.match(ref):
            return self._resolve_env(ref, public_ref=ref, fetch=fetch)
        if ENV_REF_PATTERN.match(ref):
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="invalid",
                message="Bare credential refs are not accepted; use env:NAME or a supported vault/keyring ref",
            )
        return CredentialResolution(
            ref=ref,
            status="invalid",
            source="invalid",
            message="Unsupported credential ref format",
        )

    def status(self, credential_ref: str | None) -> str:
        return self.resolve(credential_ref, fetch=False).status

    def normalize_ref_for_storage(self, credential_ref: str | None) -> str:
        ref = (credential_ref or "").strip()
        if LEGACY_ENV_REF_PATTERN.match(ref):
            return f"env:{ref}"
        return ref

    def validate_ref(self, credential_ref: str | None) -> None:
        result = self.resolve(credential_ref, fetch=False)
        if result.status == "invalid":
            raise ValueError(result.message)

    @staticmethod
    def _looks_like_raw_secret(value: str) -> bool:
        return bool(SECRET_LIKE_PATTERN.search(value.strip()))

    @staticmethod
    def _resolve_env(name: str, *, public_ref: str, fetch: bool) -> CredentialResolution:
        if not ENV_REF_PATTERN.match(name):
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source="env",
                message="Environment credential refs must use uppercase variable names",
            )
        value = os.environ.get(name)
        resolved_name = name
        if not value and not name.startswith("AIDO_"):
            alias = f"AIDO_{name}"
            value = os.environ.get(alias)
            resolved_name = alias if value else name
        if value:
            return CredentialResolution(
                ref=public_ref, status="configured", source="env", value=value if fetch else None
            )
        return CredentialResolution(
            ref=public_ref,
            status="missing",
            source="env",
            message=f"Environment variable {resolved_name} is not set",
        )

    @staticmethod
    def _resolve_keyring(target: str, *, public_ref: str, fetch: bool) -> CredentialResolution:
        if "/" not in target:
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source="keyring",
                message="Keyring refs must use keyring:service/account",
            )
        service, account = (part.strip() for part in target.split("/", 1))
        if not service or not account:
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source="keyring",
                message="Keyring refs require service and account",
            )
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError:
            return CredentialResolution(
                ref=public_ref,
                status="unsupported",
                source="keyring",
                message="Python keyring is not installed; use env:NAME or install an optional keyring adapter",
            )
        if not fetch:
            return CredentialResolution(
                ref=public_ref,
                status="unverified",
                source="keyring",
                message="Keyring ref is valid; value was not fetched during status check",
            )
        try:
            value = keyring.get_password(service, account)
        except Exception as error:  # pragma: no cover - backend-specific keyring failure
            return CredentialResolution(
                ref=public_ref,
                status="unsupported",
                source="keyring",
                message=f"Keyring backend is unavailable: {error.__class__.__name__}",
            )
        if value:
            return CredentialResolution(ref=public_ref, status="configured", source="keyring", value=value)
        return CredentialResolution(
            ref=public_ref, status="missing", source="keyring", message="Keyring credential is missing"
        )

    def _resolve_vault_compatible(
        self,
        target: str,
        *,
        public_ref: str,
        source: str,
        fetch: bool,
    ) -> CredentialResolution:
        parsed = self._parse_vault_ref(target, public_ref=public_ref, source=source)
        if parsed.status == "invalid":
            return parsed
        address = self._vault_address()
        token = self._vault_token(fetch=fetch)
        token_ready = token.configured if fetch else token.status in {"configured", "unverified"}
        if address.status == "invalid":
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source=source,
                message=address.message,
            )
        if token.status in {"invalid", "unsupported", "unavailable"}:
            return CredentialResolution(
                ref=public_ref,
                status=token.status,
                source=source,
                message=token.message,
            )
        if not address.value or not token_ready:
            return CredentialResolution(
                ref=public_ref,
                status="missing",
                source=source,
                message=f"{source} address or token is not configured",
            )
        if not fetch:
            return CredentialResolution(
                ref=public_ref,
                status="unverified",
                source=source,
                message=f"{source} ref is valid; secret value was not fetched during status check",
            )
        mount, secret_path, field = parsed.message.split("|", 2)
        url = self._vault_kv2_url(address.value, mount, secret_path)
        try:
            payload = self.http_json_get(
                url,
                {"X-Vault-Token": token.value or "", "Accept": "application/json"},
                10,
            )
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            return CredentialResolution(
                ref=public_ref,
                status="unavailable",
                source=source,
                message=f"{source} secret lookup failed: {error.__class__.__name__}",
            )
        secret_data = self._extract_vault_kv2_data(payload)
        if secret_data is None:
            return CredentialResolution(
                ref=public_ref,
                status="missing",
                source=source,
                message=f"{source} response is missing KV v2 data.data",
            )
        if field not in secret_data or secret_data[field] in {None, ""}:
            return CredentialResolution(
                ref=public_ref,
                status="missing",
                source=source,
                message=f"{source} secret field is missing",
            )
        return CredentialResolution(
            ref=public_ref, status="configured", source=source, value=str(secret_data[field])
        )

    @staticmethod
    def _parse_vault_ref(target: str, *, public_ref: str, source: str) -> CredentialResolution:
        if "#" not in target:
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source=source,
                message="Vault-compatible credential refs must include a #field suffix",
            )
        secret_ref, field = target.rsplit("#", 1)
        secret_ref = secret_ref.strip("/")
        field = field.strip()
        if "/" not in secret_ref or not field:
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source=source,
                message="Vault-compatible credential refs must use mount/path#field",
            )
        mount, secret_path = secret_ref.split("/", 1)
        if not mount or not secret_path:
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source=source,
                message="Vault-compatible credential refs require mount and path",
            )
        return CredentialResolution(
            ref=public_ref, status="configured", source=source, message=f"{mount}|{secret_path}|{field}"
        )

    def _vault_token(self, *, fetch: bool) -> CredentialResolution:
        auth_method = os.environ.get("AIDO_SECRET_VAULT_AUTH_METHOD", "token").strip().lower() or "token"
        if auth_method == "approle":
            return self._vault_approle_token(fetch=fetch)
        if auth_method != "token":
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="vault_auth",
                message="Unsupported vault auth method; use token or approle",
            )
        token_ref = os.environ.get("AIDO_SECRET_VAULT_TOKEN_REF", "").strip()
        if token_ref:
            if token_ref.startswith(("openbao:", "vault:")):
                return CredentialResolution(
                    ref="[redacted]",
                    status="invalid",
                    source="vault_auth",
                    message="Vault auth token refs cannot recursively use a vault-compatible credential ref",
                )
            return self.resolve(token_ref, fetch=fetch)
        for name in ("AIDO_SECRET_VAULT_TOKEN", "OPENBAO_TOKEN", "VAULT_TOKEN"):
            value = os.environ.get(name)
            if value:
                return CredentialResolution(
                    ref=f"env:{name}", status="configured", source="env", value=value if fetch else None
                )
        return CredentialResolution(
            ref="", status="missing", source="vault_auth", message="Vault token is not configured"
        )

    def _vault_approle_token(self, *, fetch: bool) -> CredentialResolution:
        role_id_ref = os.environ.get("AIDO_SECRET_VAULT_ROLE_ID_REF", "").strip()
        secret_id_ref = os.environ.get("AIDO_SECRET_VAULT_SECRET_ID_REF", "").strip()
        if not role_id_ref or not secret_id_ref:
            return CredentialResolution(
                ref="",
                status="missing",
                source="vault_auth",
                message="AppRole requires AIDO_SECRET_VAULT_ROLE_ID_REF and AIDO_SECRET_VAULT_SECRET_ID_REF",
            )
        if role_id_ref.startswith(("openbao:", "vault:")) or secret_id_ref.startswith(("openbao:", "vault:")):
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="vault_auth",
                message="AppRole bootstrap refs cannot recursively use vault-compatible credential refs",
            )
        role_id = self.resolve(role_id_ref, fetch=fetch)
        secret_id = self.resolve(secret_id_ref, fetch=fetch)
        invalid = next((item for item in (role_id, secret_id) if item.status == "invalid"), None)
        if invalid is not None:
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="vault_auth",
                message=invalid.message,
            )
        if not fetch:
            ready = {role_id.status, secret_id.status}.issubset({"configured", "unverified"})
            if ready:
                return CredentialResolution(
                    ref="[redacted]",
                    status="unverified",
                    source="vault_auth",
                    message="AppRole refs are valid; token was not requested during status check",
                )
            return CredentialResolution(
                ref="[redacted]",
                status="missing",
                source="vault_auth",
                message="AppRole bootstrap refs are not configured",
            )
        if not role_id.configured or not secret_id.configured:
            return CredentialResolution(
                ref="[redacted]",
                status="missing",
                source="vault_auth",
                message="AppRole bootstrap credential refs are not configured",
            )
        address = self._vault_address()
        if address.status == "invalid":
            return CredentialResolution(
                ref="[redacted]", status="invalid", source="vault_auth", message=address.message
            )
        if not address.value:
            return CredentialResolution(
                ref="[redacted]",
                status="missing",
                source="vault_auth",
                message="Vault address is not configured",
            )
        approle_path = (
            os.environ.get("AIDO_SECRET_VAULT_APPROLE_PATH", "auth/approle").strip("/") or "auth/approle"
        )
        url = self._vault_api_url(address.value, f"{approle_path}/login")
        try:
            payload = self.http_json_post(
                url,
                {"Content-Type": "application/json", "Accept": "application/json"},
                {"role_id": role_id.value or "", "secret_id": secret_id.value or ""},
                10,
            )
        except (
            OSError,
            TimeoutError,
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            return CredentialResolution(
                ref="[redacted]",
                status="unavailable",
                source="vault_auth",
                message=f"AppRole login failed: {error.__class__.__name__}",
            )
        auth = payload.get("auth")
        token = auth.get("client_token") if isinstance(auth, dict) else None
        if not token:
            return CredentialResolution(
                ref="[redacted]",
                status="missing",
                source="vault_auth",
                message="AppRole login response is missing auth.client_token",
            )
        return CredentialResolution(
            ref="[redacted]", status="configured", source="vault_auth", value=str(token)
        )

    @staticmethod
    def _vault_address() -> CredentialResolution:
        raw = (
            os.environ.get("AIDO_SECRET_VAULT_ADDR")
            or os.environ.get("OPENBAO_ADDR")
            or os.environ.get("VAULT_ADDR")
            or ""
        ).rstrip("/")
        if not raw:
            return CredentialResolution(
                ref="", status="missing", source="vault_address", message="Vault address is not configured"
            )
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="vault_address",
                message="Vault address must be an absolute URL",
            )
        hostname = (parsed.hostname or "").lower()
        has_path = parsed.path not in {"", "/"}
        if has_path or parsed.params or parsed.query or parsed.fragment:
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="vault_address",
                message="Vault address must not include path, query, or fragment",
            )
        if parsed.scheme == "https":
            return CredentialResolution(
                ref="[redacted]", status="configured", source="vault_address", value=raw
            )
        allow_insecure_local = os.environ.get("AIDO_ALLOW_INSECURE_LOCAL_VAULT", "false").lower() == "true"
        if parsed.scheme == "http" and hostname in LOOPBACK_VAULT_HOSTS and allow_insecure_local:
            return CredentialResolution(
                ref="[redacted]", status="configured", source="vault_address", value=raw
            )
        return CredentialResolution(
            ref="[redacted]",
            status="invalid",
            source="vault_address",
            message="Vault address must use https; http is allowed only for loopback with AIDO_ALLOW_INSECURE_LOCAL_VAULT=true",
        )

    @staticmethod
    def _vault_kv2_url(address: str, mount: str, secret_path: str) -> str:
        safe_mount = quote(mount.strip("/"), safe="")
        safe_path = "/".join(quote(part, safe="") for part in secret_path.strip("/").split("/"))
        return f"{address}/v1/{safe_mount}/data/{safe_path}"

    @staticmethod
    def _vault_api_url(address: str, path: str) -> str:
        safe_path = "/".join(quote(part, safe="") for part in path.strip("/").split("/") if part)
        return f"{address}/v1/{safe_path}"

    @staticmethod
    def _extract_vault_kv2_data(payload: dict[str, object]) -> dict[str, object] | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        nested = data.get("data")
        if isinstance(nested, dict):
            return nested
        return None

    @staticmethod
    def _default_http_json_get(url: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        request = urllib.request.Request(url, headers=headers, method="GET")
        opener = urllib.request.build_opener(NoRedirectHandler)
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _default_http_json_post(
        url: str,
        headers: dict[str, str],
        payload: dict[str, str],
        timeout: float,
    ) -> dict[str, object]:
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
                return None

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirectHandler)
        with opener.open(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        return response_payload if isinstance(response_payload, dict) else {}


def resolve_credential(credential_ref: str | None) -> CredentialResolution:
    return CredentialResolver().resolve(credential_ref)


def validate_credential_ref(credential_ref: str | None) -> None:
    CredentialResolver().validate_ref(credential_ref)
