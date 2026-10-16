"""Lectura tolerante de las métricas de uso (costo, latencia, calidad) que reporta un runtime.

Funciones puras sobre el resultado del runtime y sus entradas de uso; no tocan la base ni el estado del
coordinador.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any


def runtime_provider_usage(
    runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Extrae el bloque de uso del proveedor del resultado del runtime o de su entrada de uso."""
    for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
        if not isinstance(source, dict):
            continue
        for key in ("usage", "providerUsage", "rawUsage"):
            value = source.get(key)
            if isinstance(value, dict):
                return value
    return None


def runtime_actual_cost_usd(
    runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
) -> float | None:
    """Costo real en USD reportado por el runtime, o ``None`` si no lo informó."""
    for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
        if not isinstance(source, dict):
            continue
        for key in ("actualCostUsd", "actual_cost_usd", "costUsd", "cost_usd"):
            value = source.get(key)
            if value is not None and value != "":
                return float(value)
    return None


def runtime_latency_ms(
    runtime_result: dict[str, Any], usage_entry: dict[str, Any] | None = None
) -> int | None:
    """Latencia en milisegundos reportada por el runtime, o ``None`` si no la informó."""
    for source in (usage_entry, runtime_result, runtime_result.get("runtimeResult")):
        if not isinstance(source, dict):
            continue
        for key in ("latencyMs", "latency_ms", "durationMs", "duration_ms"):
            value = source.get(key)
            if value is not None and value != "":
                return int(value)
    return None


def runtime_bool_metric(
    usage_entry: dict[str, Any] | None,
    *,
    keys: tuple[str, ...],
    default: bool,
) -> bool:
    """Lee una métrica booleana tolerante (bool, número o texto) de la entrada de uso."""
    if not isinstance(usage_entry, dict):
        return default
    truthy = {"1", "true", "yes", "passed", "pass", "success", "succeeded", "completed"}
    falsey = {"0", "false", "no", "failed", "fail", "blocked", "not_started"}
    for key in keys:
        value = usage_entry.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in truthy:
            return True
        if normalized in falsey:
            return False
    return default


def runtime_quality_score_metric(
    usage_entry: dict[str, Any] | None,
    *,
    default: float,
) -> float:
    """Lee el puntaje de calidad de la entrada de uso, acotado a [0, 1]."""
    if not isinstance(usage_entry, dict):
        return default
    for key in ("qualityScore", "quality_score", "quality"):
        value = usage_entry.get(key)
        if value is None or value == "":
            continue
        return max(0.0, min(float(value), 1.0))
    return default


def runtime_resource_usage_entries(runtime_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista las observaciones de uso de recursos que el runtime reportó en su resultado."""
    for key in ("resourceUsage", "resource_usage", "aiUsage", "ai_usage", "usageObservations"):
        value = runtime_result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []
