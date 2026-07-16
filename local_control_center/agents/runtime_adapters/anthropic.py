"""Anthropic Messages API adapter executed under SQLite runtime policy.

Resolves base URL/API key/model from constructor or runtime configuration, gap-checks the
execution policy, converts OpenAI-style messages into the Anthropic `/messages` payload
(system prompts included), and stores the redacted text reply as evidence.

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

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from ..providers.http_transport import urlopen_fail_closed
from ..runtime_provider_config import runtime_provider_configuration
from .common import _ArtifactRecorder, _bounded_timeout, _redact_text, _result
from .models import RuntimeExecutionRequest, RuntimeExecutionResult

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096


class AnthropicAdapter:
    """Calls Anthropic Messages API under SQLite runtime policy."""

    adapter_id = "anthropic_api"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        connection: sqlite3.Connection | None = None,
        artifact_root: str | Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.environ = environ
        self.recorder = _ArtifactRecorder(
            connection=connection, artifact_root=artifact_root, adapter_id=self.adapter_id
        )

    def _configuration(self) -> dict[str, str | None]:
        source = self.environ or os.environ
        runtime_configuration = runtime_provider_configuration(self.adapter_id, environ=source)
        return {
            "baseUrl": (
                self.base_url or source.get("AIDO_ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL or ""
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
            return "Anthropic provider is missing required configuration: " + ", ".join(missing) + "."
        return None

    def _runtime_policy_gap(self, project_id: str | None) -> str | None:
        if self.recorder.connection is None:
            return "SQLite runtime policy is required for Anthropic execution."
        decision = RuntimeConfigRepository(self.recorder.connection).runtime_policy_decision(
            provider_id=self.adapter_id,
            kind="api",
            project_id=project_id,
        )
        if decision.get("allowed"):
            return None
        return str(decision.get("reason") or "Anthropic execution is disabled by runtime policy.")

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") in {None, "text"}
            ]
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @classmethod
    def _messages_payload(
        cls,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: Any,
        max_tokens: Any,
    ) -> dict[str, Any]:
        system_messages: list[str] = []
        conversation_messages: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = message.get("content", "")
            if role == "system":
                system_messages.append(cls._text_content(content))
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            conversation_messages.append({"role": role, "content": content})
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS),
            "messages": conversation_messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if system_messages:
            payload["system"] = "\n\n".join(item for item in system_messages if item)
        return payload

    @classmethod
    def _content_text(cls, raw_response: Any) -> str:
        content = raw_response.get("content") if isinstance(raw_response, dict) else None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        return ""

    def _headers(self, configuration: dict[str, str | None]) -> dict[str, str]:
        return {
            "x-api-key": str(configuration["apiKey"] or ""),
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def health_check(self) -> dict[str, Any]:
        """Probe Anthropic `/models` after checking required config and the execution flag."""
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
            headers=self._headers(configuration),
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
                "reason": str(redact_secrets(f"Anthropic health check failed: {error.__class__.__name__}")),
            }
        status = "available"
        return {
            "status": status,
            "available": status == "available",
            "reason": "Anthropic /models responded.",
        }

    def execute(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """POST `input.messages` to Anthropic `/messages` and store the redacted text reply."""
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
                reason="Anthropic execution requires input.messages.",
            )
        model = str(request.input.get("model") or configuration["model"])
        payload = json.dumps(
            self._messages_payload(
                model=model,
                messages=messages,
                temperature=request.input.get("temperature", 0.2),
                max_tokens=request.input.get("maxTokens") or request.input.get("max_tokens"),
            )
        ).encode("utf-8")
        http_request = Request(
            f"{configuration['baseUrl']}/messages",
            data=payload,
            headers=self._headers(configuration),
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
                reason=str(redact_secrets(f"Anthropic execution failed: {error.__class__.__name__}")),
                redacted=True,
            )
        clean_content, redacted = _redact_text(self._content_text(raw))
        output_id = self.recorder.record_output(
            request=request,
            name="anthropic-api-output.txt",
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
