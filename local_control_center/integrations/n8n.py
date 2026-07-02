"""Adaptador n8n: automatización externa acotada, nunca cerebro ni executor de AIDO.

Outbound: envía eventos allowlisted a targets n8n project-scoped, resolviendo el token desde
``credentialRef`` y persistiendo entregas con payloads redactados. Inbound: acepta solo creación de
thread o loop con token scoped; bloquea comandos, secretos y aprobaciones críticas.

@author Rodrigo Mason
"""

from __future__ import annotations

import hmac
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from local_control_center.agents.credentials import CredentialResolver
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now
from local_control_center.threads.contracts import THREAD_OWNER_TYPES
from local_control_center.threads.repository import ThreadsRepository

from .models import N8N_EVENT_TYPES
from .repository import IntegrationsRepository

HttpPost = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_TIMEOUT_SECONDS = 10.0
FORBIDDEN_INBOUND_ACTIONS = {
    "execute_command",
    "run_command",
    "shell",
    "write_secret",
    "create_secret",
    "rotate_secret",
    "approve_action",
    "approve_critical_action",
    "resolve_approval",
}
FORBIDDEN_COMMAND_KEYS = {"command", "commands", "argv", "shell", "cwd", "workingDirectory"}
FORBIDDEN_APPROVAL_KEYS = {"approve", "approval", "approved", "humanToken", "humanApprovalToken"}


class N8nIntegrationError(ValueError):
    """Error de contrato del adaptador n8n que se mapea a 4xx."""


class N8nAuthenticationError(PermissionError):
    """Se lanza cuando el token scoped inbound no calza con un target habilitado."""


class N8nDeliveryError(RuntimeError):
    """Se lanza cuando el target n8n no puede recibir el evento."""


class N8nIntegrationService:
    """Casos de uso del adaptador n8n sobre repositorios existentes de AIDO."""

    def __init__(
        self,
        connection: Any,
        *,
        http_post: HttpPost | None = None,
        credential_resolver: CredentialResolver | None = None,
    ):
        self.connection = connection
        self.repository = IntegrationsRepository(connection)
        self.projects = ProjectsRepository(connection)
        self.events = EventBus(connection)
        self.credential_resolver = credential_resolver or CredentialResolver()
        self.http_post = http_post or default_http_post

    def configure_target(
        self,
        *,
        project_id: str,
        url: str,
        credential_ref: str,
        enabled: bool,
        allowed_event_types: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea/actualiza un target n8n validando proyecto, URL, credentialRef y allowlist."""
        project = self.projects.get_project(project_id)
        clean_url = validate_webhook_url(url)
        clean_ref = self.validate_credential_ref(credential_ref)
        clean_events = validate_event_allowlist(allowed_event_types)
        target = self.repository.upsert_n8n_webhook_target(
            project_id=project["id"],
            url=clean_url,
            credential_ref=clean_ref,
            enabled=enabled,
            allowed_event_types=clean_events,
            metadata=redact_secrets(metadata or {}),
        )
        self.events.record_audit(
            project_id=project["id"],
            action="n8n.webhook_target.upsert",
            target=target["id"],
            actor="operator",
            payload={
                "url": target["url"],
                "enabled": target["enabled"],
                "allowedEventTypes": target["allowedEventTypes"],
                "credentialRef": target["credentialRef"],
            },
        )
        return target

    def validate_credential_ref(self, credential_ref: str) -> str:
        """Valida que ``credentialRef`` sea una referencia soportada, nunca un secreto crudo."""
        clean_ref = str(credential_ref or "").strip()
        if not clean_ref:
            raise N8nIntegrationError("credentialRef is required.")
        result = self.credential_resolver.resolve(clean_ref, fetch=False)
        if result.status == "invalid":
            raise N8nIntegrationError(result.message)
        if result.status in {"unsupported", "unavailable"}:
            raise N8nIntegrationError(result.message or f"credentialRef is {result.status}.")
        if result.status == "missing":
            raise N8nIntegrationError(result.message or "credentialRef is missing.")
        return clean_ref

    def emit_event(
        self,
        *,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
        target_id: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        """Envía un evento soportado a un target n8n habilitado y registra la entrega."""
        if event_type not in N8N_EVENT_TYPES:
            raise N8nIntegrationError(f"Unsupported n8n event type: {event_type}")
        target = self._target_for_event(project_id=project_id, event_type=event_type, target_id=target_id)
        token = self._resolved_target_token(target)
        event_payload = {
            "source": "aido",
            "eventType": event_type,
            "projectId": project_id,
            "targetId": target["id"],
            "subjectId": subject_id,
            "emittedAt": utc_now(),
            "payload": redact_secrets(payload),
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            response = self.http_post(target["url"], headers, event_payload, DEFAULT_TIMEOUT_SECONDS)
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            delivery = self.repository.record_n8n_event_delivery(
                target_id=target["id"],
                project_id=project_id,
                event_type=event_type,
                subject_id=subject_id,
                status="failed",
                status_code=None,
                request_payload=event_payload,
                error=f"{error.__class__.__name__}: {error}",
            )
            raise N8nDeliveryError(f"n8n target is unreachable: {delivery['error']}") from error
        status_code = int(response.get("statusCode") or response.get("status_code") or 0)
        response_body = response.get("body") if isinstance(response.get("body"), dict) else {}
        status = "delivered" if 200 <= status_code < 300 else "failed"
        delivery = self.repository.record_n8n_event_delivery(
            target_id=target["id"],
            project_id=project_id,
            event_type=event_type,
            subject_id=subject_id,
            status=status,
            status_code=status_code,
            request_payload=event_payload,
            response_body=redact_secrets(response_body),
            error="" if status == "delivered" else f"n8n returned status {status_code}",
        )
        self.events.record_event(
            project_id=project_id,
            event_type="n8n.event.emitted",
            payload={
                "targetId": target["id"],
                "eventType": event_type,
                "subjectId": subject_id,
                "deliveryId": delivery["id"],
                "status": delivery["status"],
                "statusCode": delivery["statusCode"],
            },
        )
        if delivery["status"] != "delivered":
            raise N8nDeliveryError(delivery["error"])
        return delivery

    def test_target(
        self,
        *,
        project_id: str,
        event_type: str,
        payload: dict[str, Any],
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """Envía un evento mínimo de prueba por el mismo corredor outbound real."""
        test_payload = {"test": True, **redact_secrets(payload)}
        return self.emit_event(
            project_id=project_id,
            event_type=event_type,
            target_id=target_id,
            subject_id="n8n-test",
            payload=test_payload,
        )

    def handle_inbound(
        self,
        *,
        project_id: str,
        action: str,
        payload: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        """Procesa un webhook inbound permitido desde n8n tras validar token scoped."""
        project = self.projects.get_project(project_id)
        self._require_scoped_token(project_id=project["id"], token=token)
        action_key = normalize_action(action)
        self._validate_inbound_action(action_key, payload)
        if action_key == "create_thread":
            return {
                "accepted": True,
                "action": "create_thread",
                "thread": self._create_thread(project_id=project["id"], payload=payload),
                "loop": None,
            }
        if action_key == "create_loop":
            return {
                "accepted": True,
                "action": "create_loop",
                "thread": None,
                "loop": self._create_loop(project_id=project["id"], payload=payload),
            }
        raise N8nIntegrationError(f"Unsupported n8n inbound action: {action}")

    def _target_for_event(
        self,
        *,
        project_id: str,
        event_type: str,
        target_id: str | None,
    ) -> dict[str, Any]:
        self.projects.get_project(project_id)
        if target_id:
            target = self.repository.get_n8n_webhook_target(target_id)
            if target["projectId"] != project_id:
                raise N8nIntegrationError(f"n8n webhook target not found in project: {target_id}")
            targets = [target]
        else:
            targets = self.repository.list_n8n_webhook_targets(project_id=project_id, enabled_only=True)
            if not targets:
                raise N8nIntegrationError("No enabled n8n webhook target is configured for this project.")
        target = targets[0]
        if not target["enabled"]:
            raise N8nIntegrationError(f"n8n webhook target is disabled: {target['id']}")
        if event_type not in target["allowedEventTypes"]:
            raise N8nIntegrationError(f"n8n event type is not allowed for this project target: {event_type}")
        return target

    def _resolved_target_token(self, target: dict[str, Any]) -> str:
        resolution = self.credential_resolver.resolve(str(target["credentialRef"]), fetch=True)
        if not resolution.configured or not resolution.value:
            raise N8nIntegrationError(
                resolution.message or f"n8n credentialRef is {resolution.status}; configure the target token."
            )
        return resolution.value

    def _require_scoped_token(self, *, project_id: str, token: str) -> None:
        clean_token = str(token or "").strip()
        if not clean_token:
            raise N8nAuthenticationError("A scoped n8n token is required.")
        for target in self.repository.list_n8n_webhook_targets(project_id=project_id, enabled_only=True):
            resolution = self.credential_resolver.resolve(str(target["credentialRef"]), fetch=True)
            if (
                resolution.configured
                and resolution.value
                and hmac.compare_digest(resolution.value, clean_token)
            ):
                return
        raise N8nAuthenticationError("Invalid scoped n8n token.")

    @staticmethod
    def _validate_inbound_action(action: str, payload: dict[str, Any]) -> None:
        if action in FORBIDDEN_INBOUND_ACTIONS:
            raise N8nAuthenticationError(f"n8n is not allowed to perform action: {action}")
        if contains_forbidden_key(payload, FORBIDDEN_COMMAND_KEYS):
            raise N8nAuthenticationError("n8n webhooks cannot execute commands directly.")
        if contains_forbidden_key(payload, FORBIDDEN_APPROVAL_KEYS):
            raise N8nAuthenticationError(
                "n8n webhooks cannot approve critical actions without a human token."
            )
        if action not in {"create_thread", "create_loop"}:
            raise N8nIntegrationError(f"Unsupported n8n inbound action: {action}")

    def _create_thread(self, *, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        title = required_text(payload.get("title"), "Thread title is required.")
        owner_type = str(payload.get("ownerType") or payload.get("owner_type") or "workspace")
        if owner_type not in THREAD_OWNER_TYPES:
            raise N8nIntegrationError(f"Unknown thread owner type: {owner_type}")
        owner_id = str(payload.get("ownerId") or payload.get("owner_id") or "n8n").strip() or "n8n"
        metadata = redact_secrets({"source": "n8n", **dict(payload.get("metadata") or {})})
        repo = ThreadsRepository(self.connection)
        thread = repo.create_thread(
            project_id=project_id,
            owner_type=owner_type,
            owner_id=owner_id,
            title=str(redact_secrets(title)),
            metadata=metadata,
        )
        message = str(payload.get("message") or payload.get("firstMessage") or "").strip()
        if message:
            repo.append_message(
                thread_id=thread["id"],
                kind="user",
                author="n8n",
                content=message,
                metadata={"source": "n8n"},
            )
        self.events.record_audit(
            project_id=project_id,
            action="n8n.webhook.thread.create",
            actor="n8n",
            target=thread["id"],
            payload={"ownerType": owner_type, "ownerId": owner_id},
        )
        return repo.get_thread(thread["id"])

    def _create_loop(self, *, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        title = required_text(payload.get("title"), "Product loop title is required.")
        context = dict(payload.get("context") or {})
        context["source"] = context.get("source") or "n8n"
        loop = ProductLoopCoordinator(self.connection).start(
            project_id=project_id,
            title=str(redact_secrets(title)),
            initiative_id=optional_text(payload.get("initiativeId") or payload.get("initiative_id")),
            context=redact_secrets(context),
            correlation_id=optional_text(payload.get("correlationId") or payload.get("correlation_id")),
            actor="n8n",
            reason="n8n inbound webhook created product loop.",
        )
        self.events.record_audit(
            project_id=project_id,
            action="n8n.webhook.loop.create",
            actor="n8n",
            target=loop["id"],
            payload={"initiativeId": loop.get("initiativeId")},
        )
        return loop


def validate_event_allowlist(values: list[str]) -> list[str]:
    """Normaliza y valida la allowlist de eventos n8n."""
    clean = []
    for value in values:
        event_type = str(value or "").strip()
        if event_type not in N8N_EVENT_TYPES:
            raise N8nIntegrationError(f"Unsupported n8n event type: {event_type}")
        if event_type not in clean:
            clean.append(event_type)
    if not clean:
        raise N8nIntegrationError("allowedEventTypes must include at least one event type.")
    return clean


def validate_webhook_url(value: str) -> str:
    """Acepta HTTPS y HTTP solo en loopback para no enviar tokens por texto claro en red externa."""
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise N8nIntegrationError("n8n webhook URL must be an absolute http(s) URL.")
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and hostname not in LOOPBACK_HOSTS:
        raise N8nIntegrationError("n8n webhook URL over http is only allowed on loopback.")
    return url


def extract_inbound_token(headers: Any) -> str:
    """Lee el token scoped inbound desde ``X-AIDO-N8N-Token`` o ``Authorization: Bearer``."""
    direct = str(headers.get("X-AIDO-N8N-Token") or "").strip()
    if direct:
        return direct
    authorization = str(headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def normalize_action(value: str) -> str:
    """Normaliza acciones inbound para comparar de forma estable."""
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    """Busca claves operacionales prohibidas en payloads arbitrarios."""
    forbidden_normalized = {item.lower() for item in forbidden}
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in forbidden_normalized:
                return True
            if contains_forbidden_key(item, forbidden):
                return True
    if isinstance(value, list):
        return any(contains_forbidden_key(item, forbidden) for item in value)
    return False


def required_text(value: Any, message: str) -> str:
    """Devuelve texto no vacío o lanza error de contrato."""
    text = str(value or "").strip()
    if not text:
        raise N8nIntegrationError(message)
    return text


def optional_text(value: Any) -> str | None:
    """Normaliza texto opcional a ``None`` si viene vacío."""
    text = str(value or "").strip()
    return text or None


def default_http_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """POST JSON sin dependencias externas; desactiva comportamiento implícito fuera de este borde."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return {"statusCode": response.status, "body": parse_json_object(body)}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return {"statusCode": error.code, "body": parse_json_object(body)}


def parse_json_object(value: str) -> dict[str, Any]:
    """Parsea un JSON object; cuerpos no-JSON o arrays quedan como objeto seguro."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value[:500]}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
