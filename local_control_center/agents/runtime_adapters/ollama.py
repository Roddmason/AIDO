"""Ollama chat adapter that executes broker-approved requests against local or remote daemons.

Resolves base URL and credential as one trust unit (a persisted bearer never crosses to an
override host), health-checks `/api/tags`, sends `input.messages` to `/api/chat`, and stores
the redacted reply as evidence.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from ..credentials import CredentialResolver
from ..provider_accounts import ProviderAccountStore
from ..providers.http_transport import urlopen_fail_closed
from ..runtime_provider_config import runtime_provider_configuration
from .common import _ArtifactRecorder, _bounded_timeout, _redact_text, _result
from .models import RuntimeExecutionRequest, RuntimeExecutionResult


class OllamaAdapter:
    """Calls a local Ollama daemon's chat API and records the reply as evidence."""

    adapter_id = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.connection = connection
        self.environ = environ
        self.recorder = _ArtifactRecorder(
            connection=connection, artifact_root=artifact_root, adapter_id=self.adapter_id
        )

    def _persisted_account(self, provider_id: str = "ollama") -> dict[str, Any]:
        if self.connection is None:
            return {}
        try:
            account = ProviderAccountStore(self.connection).get_provider_account(provider_id)
        except KeyError:
            return {}
        return account if account.get("enabled") else {}

    def _resolved_endpoint(self, provider_id: str = "ollama") -> tuple[str | None, str]:
        """Resolve URL and credential as one trust unit so a token never crosses hosts."""
        account = self._persisted_account(provider_id)
        if provider_id != "ollama":
            base_url = str(account.get("baseUrl") or "").strip()
            return (
                base_url.rstrip("/") or None,
                str(account.get("credentialRef") or "").strip(),
            )
        source = self.environ if self.environ is not None else os.environ
        configuration = runtime_provider_configuration("ollama", environ=source)
        explicit_base_url = str(self.base_url or "").strip()
        if explicit_base_url:
            return explicit_base_url.rstrip("/"), ""
        configured_base_url = str((configuration.value("baseUrl") if configuration else None) or "").strip()
        if configured_base_url:
            return configured_base_url.rstrip("/"), ""
        persisted_base_url = str(account.get("baseUrl") or "").strip()
        if persisted_base_url:
            return (
                persisted_base_url.rstrip("/"),
                str(account.get("credentialRef") or "").strip(),
            )
        legacy_base_url = str(source.get("OLLAMA_BASE_URL") or source.get("OLLAMA_HOST") or "").strip()
        return legacy_base_url.rstrip("/") or None, ""

    def _configured_base_url(self) -> str | None:
        return self._resolved_endpoint()[0]

    def _auth_headers_or_block(self, credential_ref: str) -> tuple[dict[str, str], str | None]:
        if not credential_ref:
            return {}, None
        resolution = CredentialResolver().resolve(credential_ref)
        if resolution.configured:
            return {"Authorization": f"Bearer {resolution.value}"}, None
        return {}, (f"Ollama credentialRef is {resolution.status}; configure the remote access token.")

    def health_check(self) -> dict[str, Any]:
        """Probe `/api/tags` to confirm the Ollama daemon is reachable and list its models."""
        return self._health_check_for("ollama")

    def _health_check_for(self, provider_id: str) -> dict[str, Any]:
        base_url, credential_ref = self._resolved_endpoint(provider_id)
        if not base_url:
            return {
                "status": "configuration_required",
                "available": False,
                "reason": "Ollama base URL is not configured.",
            }
        headers, blocking_reason = self._auth_headers_or_block(credential_ref)
        if blocking_reason:
            return {
                "status": "configuration_required",
                "available": False,
                "reason": blocking_reason,
            }
        try:
            request = Request(f"{base_url}/api/tags", headers=headers, method="GET")
            with urlopen_fail_closed(
                request,
                timeout=5,
                urlopen_override=urlopen,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {
                "status": "unavailable",
                "available": False,
                "reason": str(redact_secrets(f"Ollama health check failed: {error.__class__.__name__}")),
            }
        models = payload.get("models", []) if isinstance(payload, dict) else []
        status = "available"
        return {
            "status": status,
            "available": status == "available",
            "reason": "Ollama daemon responded.",
            "models": models,
        }

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Send `input.messages` to Ollama's chat API and store the redacted reply as evidence.

        Returns configuration_required/unavailable when the daemon is unreachable, and
        blocked when model or messages are missing.
        """
        started_at = utc_now()
        provider_id = str(request.input.get("providerId") or "ollama").strip() or "ollama"
        base_url, credential_ref = self._resolved_endpoint(provider_id)
        if not base_url:
            return _result(
                status="configuration_required",
                started_at=started_at,
                reason="Ollama base URL is not configured.",
            )
        health = self._health_check_for(provider_id)
        if not health.get("available"):
            return _result(status=str(health["status"]), started_at=started_at, reason=str(health["reason"]))
        model = str(request.input.get("model") or "").strip()
        messages = request.input.get("messages")
        if not model:
            return _result(
                status="configuration_required", started_at=started_at, reason="Ollama model is required."
            )
        if not isinstance(messages, list) or not messages:
            return _result(
                status="blocked", started_at=started_at, reason="Ollama execution requires input.messages."
            )
        headers, blocking_reason = self._auth_headers_or_block(credential_ref)
        if blocking_reason:
            return _result(status="configuration_required", started_at=started_at, reason=blocking_reason)
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": request.input.get("options") or {},
            }
        ).encode("utf-8")
        try:
            http_request = Request(
                f"{base_url}/api/chat",
                data=payload,
                headers={
                    **headers,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urlopen_fail_closed(
                http_request,
                timeout=_bounded_timeout(request),
                urlopen_override=urlopen,
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return _result(
                status="unavailable",
                started_at=started_at,
                reason=str(redact_secrets(f"Ollama execution failed: {error.__class__.__name__}")),
                redacted=True,
            )
        message = raw.get("message") if isinstance(raw, dict) else {}
        content = str((message or {}).get("content") or "")
        clean_content, redacted = _redact_text(content)
        output_id = self.recorder.record_output(
            request=request, name="ollama-output.txt", content=clean_content
        )
        evidence_id = self.recorder.create_evidence(
            request=request,
            status="completed",
            exit_code=0,
            reason=None,
            artifact_ids=[output_id] if output_id else [],
        )
        return _result(
            status="completed",
            started_at=started_at,
            exit_code=0,
            output_artifact_id=output_id,
            evidence_package_id=evidence_id,
            redacted=redacted,
        )
