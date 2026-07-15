"""Telemetría operativa: registra eventos redactados y los exporta a OpenTelemetry.

Toda la observabilidad pasa por aquí: persiste eventos en el ``EventBus`` (siempre
redactados) y, opcionalmente, los reenvía a un exportador OTLP/HTTP configurado por
entorno. Provee helpers de correlación y de medición de latencia, y emisores tipados
por dominio (HTTP, decisiones de política, tool/model/agent calls). Garantiza que un
fallo del exportador externo nunca rompe el runtime local: se captura y se reporta en el estado.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .event_bus import EventBus
from .redaction import redact_secrets

HTTP_REQUEST_TELEMETRY_RETENTION_DAYS = 7


class ExternalTelemetryExporter(Protocol):
    """Contrato del exportador externo que recibe eventos ya redactados para reenviarlos."""

    def export_event(self, event: dict[str, Any]) -> None:
        """Export one already-redacted telemetry event."""


_external_exporter: ExternalTelemetryExporter | None = None
_external_status: dict[str, Any] = {
    "enabled": False,
    "mode": "none",
    "available": False,
    "tracesEnabled": False,
    "metricsEnabled": False,
    "reason": "External OpenTelemetry exporter is not configured.",
}


def _parse_otlp_headers(value: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if not value:
        return headers
    for item in value.split(","):
        if "=" not in item:
            continue
        key, header_value = item.split("=", 1)
        key = key.strip()
        if key:
            headers[key] = header_value.strip()
    return headers


def _flatten_attributes(prefix: str, value: Any, attributes: dict[str, str | int | float | bool]) -> None:
    if isinstance(value, (str, int, float, bool)) or value is None:
        attributes[prefix] = "" if value is None else value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_attributes(f"{prefix}.{key}", item, attributes)
        return
    if isinstance(value, list):
        attributes[prefix] = ",".join(str(item) for item in value[:12])
        return
    attributes[prefix] = str(value)


class OtlpHttpTelemetryExporter:
    """Exportador que envía eventos como spans y un contador vía OTLP/HTTP.

    Exige el extra opcional ``otel``; si los paquetes no están instalados, la construcción
    lanza ``RuntimeError`` para que el llamador degrade a "sin exportador externo".
    """

    def __init__(
        self,
        *,
        service_name: str,
        traces_endpoint: str,
        metrics_endpoint: str,
        headers: dict[str, str],
    ) -> None:
        try:
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry OTLP HTTP exporter packages are not installed. "
                "Install the optional 'otel' extra to enable external export."
            ) from error

        resource = Resource.create({"service.name": service_name})
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint, headers=headers))
        )
        trace.set_tracer_provider(tracer_provider)
        self.tracer = trace.get_tracer("local_control_center")

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=metrics_endpoint, headers=headers),
            export_interval_millis=5000,
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
        self.counter = metrics.get_meter("local_control_center").create_counter(
            "aido.telemetry.events",
            description="AIDO operational telemetry events exported to OpenTelemetry.",
        )

    def export_event(self, event: dict[str, Any]) -> None:
        """Exporta el evento como span OTLP con atributos aplanados e incrementa el contador por tipo."""
        attributes: dict[str, str | int | float | bool] = {
            "aido.event.id": str(event.get("id") or ""),
            "aido.event.type": str(event.get("type") or ""),
            "aido.project.id": str(event.get("projectId") or ""),
            "aido.job.id": str(event.get("jobId") or ""),
        }
        _flatten_attributes("aido.payload", event.get("payload") or {}, attributes)
        with self.tracer.start_as_current_span(str(event.get("type") or "aido.event")) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        self.counter.add(1, attributes={"aido.event.type": str(event.get("type") or "")})


def configure_external_telemetry_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """(Re)configura el exportador externo según ``AIDO_OTEL_*`` y devuelve el estado resultante.

    Deja el exportador deshabilitado (sin lanzar) cuando el modo es ``none``/no soportado o
    cuando faltan los paquetes OTLP, dejando la razón registrada en el estado.
    """
    global _external_exporter, _external_status
    source = environ or os.environ
    mode = str(source.get("AIDO_OTEL_EXPORTER") or "none").strip().lower()
    if mode in {"", "none", "off", "disabled"}:
        _external_exporter = None
        _external_status = {
            "enabled": False,
            "mode": "none",
            "available": False,
            "tracesEnabled": False,
            "metricsEnabled": False,
            "reason": "External OpenTelemetry exporter is not configured.",
        }
        return external_telemetry_status()

    if mode != "otlp_http":
        _external_exporter = None
        _external_status = {
            "enabled": False,
            "mode": mode,
            "available": False,
            "tracesEnabled": False,
            "metricsEnabled": False,
            "reason": "Unsupported AIDO_OTEL_EXPORTER value.",
        }
        return external_telemetry_status()

    base_endpoint = str(source.get("AIDO_OTEL_ENDPOINT") or "http://127.0.0.1:4318").rstrip("/")
    traces_endpoint = str(source.get("AIDO_OTEL_TRACES_ENDPOINT") or f"{base_endpoint}/v1/traces")
    metrics_endpoint = str(source.get("AIDO_OTEL_METRICS_ENDPOINT") or f"{base_endpoint}/v1/metrics")
    service_name = str(source.get("AIDO_SERVICE_NAME") or "aido-local-control-center")
    try:
        _external_exporter = OtlpHttpTelemetryExporter(
            service_name=service_name,
            traces_endpoint=traces_endpoint,
            metrics_endpoint=metrics_endpoint,
            headers=_parse_otlp_headers(source.get("AIDO_OTEL_HEADERS")),
        )
    except RuntimeError as error:
        _external_exporter = None
        _external_status = {
            "enabled": False,
            "mode": "otlp_http",
            "available": False,
            "tracesEnabled": False,
            "metricsEnabled": False,
            "reason": str(error),
            "tracesEndpoint": traces_endpoint,
            "metricsEndpoint": metrics_endpoint,
        }
        return external_telemetry_status()

    exporter_available = _external_exporter is not None
    _external_status = {
        "enabled": exporter_available,
        "mode": "otlp_http",
        "available": exporter_available,
        "tracesEnabled": exporter_available,
        "metricsEnabled": exporter_available,
        "reason": "" if exporter_available else "OpenTelemetry exporter was not initialized.",
        "serviceName": service_name,
        "tracesEndpoint": traces_endpoint,
        "metricsEndpoint": metrics_endpoint,
    }
    return external_telemetry_status()


def external_telemetry_status() -> dict[str, Any]:
    """Devuelve una copia del estado actual del exportador externo."""
    return dict(_external_status)


def set_external_exporter_for_tests(exporter: ExternalTelemetryExporter, *, mode: str = "test") -> None:
    """Inyecta un exportador (uso en tests) y actualiza el estado para reflejarlo como activo."""
    global _external_exporter, _external_status
    _external_exporter = exporter
    exporter_available = _external_exporter is not None
    _external_status = {
        "enabled": exporter_available,
        "mode": mode,
        "available": exporter_available,
        "tracesEnabled": exporter_available,
        "metricsEnabled": exporter_available,
        "reason": "" if exporter_available else "External telemetry exporter is not configured.",
    }


def clear_external_exporter_for_tests() -> None:
    """Restablece el exportador al estado deshabilitado por defecto (uso en tests)."""
    configure_external_telemetry_from_env({})


def new_correlation_id() -> str:
    """Genera un id de correlación nuevo con prefijo ``corr-``."""
    return f"corr-{uuid.uuid4()}"


def resolve_correlation_id(headers: Mapping[str, str] | None = None) -> str:
    """Toma el id de correlación de las cabeceras entrantes (truncado) o genera uno nuevo."""
    if headers:
        for key in ("x-correlation-id", "x-request-id"):
            value = headers.get(key) or headers.get(key.title())
            if value:
                return str(value)[:128]
    return new_correlation_id()


def monotonic_ms() -> float:
    """Devuelve un reloj monótono en milisegundos, apto para medir duraciones."""
    return time.perf_counter() * 1000


def elapsed_ms(start_ms: float) -> int:
    """Devuelve los milisegundos transcurridos desde ``start_ms`` (nunca negativo)."""
    return max(0, int(monotonic_ms() - start_ms))


def redact_telemetry(value: Any, *, key: str = "") -> Any:
    """Redacta secretos de un payload de telemetría antes de persistirlo o exportarlo."""
    return redact_secrets(value, key=key)


def record_telemetry_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    payload: dict[str, Any],
    project_id: str | None = None,
    job_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Redacta y persiste un evento, asegura un ``correlationId`` y lo reenvía al exportador externo.

    El fallo del exportador externo se captura y se anota en el estado; nunca interrumpe la escritura local.
    """
    clean_payload = redact_telemetry(
        {
            **payload,
            "correlationId": correlation_id or payload.get("correlationId") or new_correlation_id(),
        }
    )
    event = EventBus(connection).record_event(
        project_id=project_id,
        job_id=job_id,
        event_type=event_type,
        payload=clean_payload,
    )
    if _external_exporter is not None:
        try:
            _external_exporter.export_event(event)
        except Exception as error:  # pragma: no cover - exporter failures must never break local runtime
            _external_status["lastExportError"] = str(error)
    return event


def record_http_request(
    connection: sqlite3.Connection,
    *,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    correlation_id: str,
) -> dict[str, Any]:
    """Registra una petición HTTP servida (método, ruta, código y latencia) como evento de telemetría."""
    return record_telemetry_event(
        connection,
        event_type="telemetry.http.request",
        correlation_id=correlation_id,
        payload={
            "method": method,
            "path": path,
            "statusCode": status_code,
            "durationMs": duration_ms,
        },
    )


def prune_http_request_telemetry(
    connection: sqlite3.Connection,
    *,
    retention_days: int = HTTP_REQUEST_TELEMETRY_RETENTION_DAYS,
) -> int:
    """Borra eventos ``telemetry.http.request`` más antiguos que la retención y devuelve cuántos.

    Solo poda telemetría de latencia HTTP (alto volumen, valor decreciente); nunca toca
    eventos de dominio ni ``audit_events``. El cutoff se calcula en el mismo formato
    ISO-8601 con sufijo ``Z`` de ``utc_now`` para que la comparación lexicográfica sea
    correcta también dentro del mismo día.
    """
    cutoff = (
        (datetime.now(UTC) - timedelta(days=retention_days))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    cursor = connection.execute(
        "DELETE FROM events WHERE type = 'telemetry.http.request' AND created_at < ?",
        (cutoff,),
    )
    return cursor.rowcount


def record_policy_decision(connection: sqlite3.Connection, decision: dict[str, Any]) -> dict[str, Any]:
    """Registra una decisión de política de permisos (tool, veredicto, riesgo) como evento."""
    return record_telemetry_event(
        connection,
        event_type="telemetry.policy.decision",
        project_id=decision.get("projectId"),
        payload={
            "permissionDecisionId": decision["id"],
            "workspaceId": decision.get("workspaceId"),
            "agentId": decision.get("agentId"),
            "tool": decision.get("tool"),
            "decision": decision.get("decision"),
            "riskLevel": decision.get("riskLevel"),
            "reason": decision.get("reason"),
        },
    )


def record_tool_call(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    agent_run_id: str,
    tool_call: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Registra la ejecución de una tool por un agente, con la decisión de política que la autorizó."""
    return record_telemetry_event(
        connection,
        event_type="telemetry.tool.call",
        project_id=project_id,
        payload={
            "toolCallId": tool_call["id"],
            "agentRunId": agent_run_id,
            "toolName": tool_call.get("toolName"),
            "status": tool_call.get("status"),
            "permissionDecisionId": decision.get("id"),
            "decision": decision.get("decision"),
            "riskLevel": decision.get("riskLevel"),
        },
    )


def record_agent_run(connection: sqlite3.Connection, agent_run: dict[str, Any]) -> dict[str, Any]:
    """Registra un cambio de estado de un agent run (tipo de evento derivado de su ``status``)."""
    metadata = agent_run.get("metadata") or {}
    return record_telemetry_event(
        connection,
        event_type=f"agent.run.{agent_run.get('status') or 'updated'}",
        project_id=agent_run.get("projectId"),
        job_id=agent_run.get("jobId"),
        payload={
            "agentRunId": agent_run["id"],
            "agentProfileId": metadata.get("agentProfileId"),
            "workflowRunId": agent_run.get("workflowRunId"),
            "workflowStepId": agent_run.get("workflowStepId"),
            "status": agent_run.get("status"),
            "runtimeType": metadata.get("runtimeType"),
            "taskId": metadata.get("taskId"),
        },
    )


def record_model_call(connection: sqlite3.Connection, model_call: dict[str, Any]) -> dict[str, Any]:
    """Registra una llamada a modelo (proveedor, modelo, tokens y costo) como evento de telemetría."""
    return record_telemetry_event(
        connection,
        event_type="telemetry.model.call",
        project_id=model_call.get("projectId"),
        payload={
            "modelCallId": model_call["id"],
            "agentRunId": model_call.get("agentRunId"),
            "modelPolicyId": model_call.get("modelPolicyId"),
            "provider": model_call.get("provider"),
            "model": model_call.get("model"),
            "status": model_call.get("status"),
            "promptTokens": model_call.get("promptTokens"),
            "completionTokens": model_call.get("completionTokens"),
            "costUsd": model_call.get("costUsd"),
            "metadata": model_call.get("metadata"),
        },
    )
