"""OpenAI-compatible chat-completions adapter executed under SQLite runtime policy.

Covers the openai_compatible, openrouter, and nvidia_nim provider families: resolves
base URL/API key/model from constructor or runtime configuration, gap-checks config and
runtime policy, health-checks `/models`, and stores the redacted chat reply as evidence.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from ..providers.http_transport import urlopen_fail_closed
from ..runtime_provider_config import runtime_provider_configuration
from .common import _ArtifactRecorder, _bounded_timeout, _redact_text, _result
from .models import RuntimeExecutionRequest, RuntimeExecutionResult


class OpenAICompatibleAdapter:
    """Calls an OpenAI-compatible chat-completions endpoint under SQLite runtime policy."""

    adapter_id = "openai_compatible"

    def __init__(
        self,
        *,
        provider_id: str = "openai_compatible",
        display_name: str = "OpenAI-compatible",
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.adapter_id = provider_id
        self.display_name = display_name
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.environ = environ
        self.recorder = _ArtifactRecorder(
            connection=connection, artifact_root=artifact_root, adapter_id=self.adapter_id
        )

    def _default_base_url(self, source: Mapping[str, str]) -> str | None:
        if self.adapter_id == "openrouter":
            return (
                source.get("AIDO_OPENROUTER_BASE_URL")
                or source.get("OPENROUTER_BASE_URL")
                or "https://openrouter.ai/api/v1"
            )
        if self.adapter_id == "nvidia_nim":
            return source.get("AIDO_NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1"
        return source.get("OPENAI_COMPATIBLE_BASE_URL")

    def _configuration(self) -> dict[str, str | None]:
        source = self.environ or os.environ
        runtime_configuration = runtime_provider_configuration(self.adapter_id, environ=source)
        return {
            "baseUrl": (
                self.base_url
                or (runtime_configuration.value("baseUrl") if runtime_configuration else None)
                or self._default_base_url(source)
                or ""
            ).rstrip("/")
            or None,
            "apiKey": self.api_key
            or (runtime_configuration.value("apiKey") if runtime_configuration else None),
            "model": self.model or (runtime_configuration.value("model") if runtime_configuration else None),
        }

    def _configuration_gap(self, configuration: dict[str, str | None]) -> str | None:
        missing = [
            name
            for key, name in (
                ("baseUrl", "base URL"),
                ("apiKey", "API key"),
                ("model", "model"),
            )
            if not configuration.get(key)
        ]
        if missing:
            return (
                f"{self.display_name} provider is missing required configuration: " + ", ".join(missing) + "."
            )
        return None

    def _runtime_policy_gap(self, project_id: str | None) -> str | None:
        if self.recorder.connection is None:
            return f"SQLite runtime policy is required for {self.display_name} execution."
        decision = RuntimeConfigRepository(self.recorder.connection).runtime_policy_decision(
            provider_id=self.adapter_id,
            kind="gateway" if self.adapter_id == "openrouter" else "api",
            project_id=project_id,
        )
        if decision.get("allowed"):
            return None
        return str(decision.get("reason") or f"{self.display_name} execution is disabled by runtime policy.")

    def health_check(self) -> dict[str, Any]:
        """Probe `/models` to confirm the endpoint and credentials respond, gap-checking config first."""
        configuration = self._configuration()
        gap = self._configuration_gap(configuration)
        if gap:
            status = "configuration_required" if "missing required configuration" in gap else "unavailable"
            return {"status": status, "available": False, "reason": gap}
        policy_gap = self._runtime_policy_gap(project_id=None)
        if policy_gap:
            return {"status": "unavailable", "available": False, "reason": policy_gap}
        request = Request(
            f"{configuration['baseUrl']}/models",
            headers={"Authorization": f"Bearer {configuration['apiKey']}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen_fail_closed(
                request,
                timeout=10,
                urlopen_override=urlopen,
            ) as response:
                json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return {
                "status": "unavailable",
                "available": False,
                "reason": str(
                    redact_secrets(f"{self.display_name} health check failed: {error.__class__.__name__}")
                ),
            }
        status = "available"
        return {
            "status": status,
            "available": status == "available",
            "reason": f"{self.display_name} /models responded.",
        }

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """POST `input.messages` to chat-completions and store the redacted reply as evidence.

        Returns configuration_required/unavailable on a config gap or transport error, and
        blocked when messages are missing.
        """
        started_at = utc_now()
        configuration = self._configuration()
        gap = self._configuration_gap(configuration)
        if gap:
            status = "configuration_required" if "missing required configuration" in gap else "unavailable"
            return _result(status=status, started_at=started_at, reason=gap)
        policy_gap = self._runtime_policy_gap(project_id=request.project_id)
        if policy_gap:
            return _result(status="blocked", started_at=started_at, reason=policy_gap)
        messages = request.input.get("messages")
        if not isinstance(messages, list) or not messages:
            return _result(
                status="blocked",
                started_at=started_at,
                reason=f"{self.display_name} execution requires input.messages.",
            )
        model = str(request.input.get("model") or configuration["model"])
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": request.input.get("temperature", 0.2),
                "stream": False,
            }
        ).encode("utf-8")
        http_request = Request(
            f"{configuration['baseUrl']}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {configuration['apiKey']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
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
                reason=str(
                    redact_secrets(f"{self.display_name} execution failed: {error.__class__.__name__}")
                ),
                redacted=True,
            )
        choices = raw.get("choices") if isinstance(raw, dict) else []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
        content = str((message or {}).get("content") or "")
        clean_content, redacted = _redact_text(content)
        output_id = self.recorder.record_output(
            request=request,
            name=f"{self.adapter_id.replace('_', '-')}-output.txt",
            content=clean_content,
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
