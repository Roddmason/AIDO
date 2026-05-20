from __future__ import annotations

import os
import re
from dataclasses import dataclass


ENV_REF_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
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

    def resolve(self, credential_ref: str | None) -> CredentialResolution:
        ref = (credential_ref or "").strip()
        if not ref:
            return CredentialResolution(ref="", status="unknown", source="none", message="No credential ref configured")
        if self._looks_like_raw_secret(ref):
            return CredentialResolution(
                ref="[redacted]",
                status="invalid",
                source="invalid",
                message="credential_ref must reference env:NAME or keyring:service/account, never a raw secret",
            )
        if ref.startswith("env:"):
            return self._resolve_env(ref.removeprefix("env:"), public_ref=ref)
        if ref.startswith("keyring:"):
            return self._resolve_keyring(ref.removeprefix("keyring:"), public_ref=ref)
        if ENV_REF_PATTERN.match(ref):
            return self._resolve_env(ref, public_ref=ref)
        return CredentialResolution(
            ref=ref,
            status="invalid",
            source="invalid",
            message="Unsupported credential ref format",
        )

    def status(self, credential_ref: str | None) -> str:
        return self.resolve(credential_ref).status

    def validate_ref(self, credential_ref: str | None) -> None:
        result = self.resolve(credential_ref)
        if result.status == "invalid":
            raise ValueError(result.message)

    @staticmethod
    def _looks_like_raw_secret(value: str) -> bool:
        return bool(SECRET_LIKE_PATTERN.search(value.strip()))

    @staticmethod
    def _resolve_env(name: str, *, public_ref: str) -> CredentialResolution:
        if not ENV_REF_PATTERN.match(name):
            return CredentialResolution(
                ref=public_ref,
                status="invalid",
                source="env",
                message="Environment credential refs must use uppercase variable names",
            )
        value = os.environ.get(name)
        if value:
            return CredentialResolution(ref=public_ref, status="configured", source="env", value=value)
        return CredentialResolution(ref=public_ref, status="missing", source="env", message=f"Environment variable {name} is not set")

    @staticmethod
    def _resolve_keyring(target: str, *, public_ref: str) -> CredentialResolution:
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
        return CredentialResolution(ref=public_ref, status="missing", source="keyring", message="Keyring credential is missing")


def resolve_credential(credential_ref: str | None) -> CredentialResolution:
    return CredentialResolver().resolve(credential_ref)


def validate_credential_ref(credential_ref: str | None) -> None:
    CredentialResolver().validate_ref(credential_ref)
