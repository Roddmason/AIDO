"""Frescura de validación por runtime para el equipo del hilo (ventana corta, solo lectura).

Lee la misma evidencia de ejecución real que ``model_execution_health`` usa para su TTL de 24 h,
pero con una ventana propia: el selector del hilo exige una prueba de ida y vuelta reciente con la
configuración actual. Los consumidores de 24 h no cambian.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from local_control_center.agents.model_execution_health import provider_configuration_fingerprint

RUNTIME_TEAM_FRESHNESS_SECONDS = 30 * 60


@dataclass(frozen=True)
class RuntimeValidationState:
    """Última evidencia de un runtime traducida al estado que muestra y exige el selector."""

    status: str
    checked_at: str | None = None
    latency_ms: int | None = None
    model: str | None = None
    reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Serializa el estado con el contrato camelCase de la API."""
        return {
            "status": self.status,
            "checkedAt": self.checked_at,
            "latencyMs": self.latency_ms,
            "model": self.model,
            "reason": self.reason,
        }


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def runtime_validation_state(
    connection: sqlite3.Connection,
    provider_id: str,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> RuntimeValidationState:
    """Clasifica la última ejecución registrada del runtime: validated, stale, failed o never.

    Una falla posterior invalida cualquier éxito anterior y un cambio de configuración vuelve
    ``stale`` la evidencia, igual que ``model_validation_rejection`` pero a nivel de runtime.
    """
    row = connection.execute(
        """SELECT model, configuration_fingerprint, success, started_at, observed_at
           FROM model_execution_health WHERE provider_id = ?
           ORDER BY started_at DESC, id DESC LIMIT 1""",
        (provider_id,),
    ).fetchone()
    if row is None:
        return RuntimeValidationState("never", reason="runtime_validation_required")
    started = _parse_timestamp(row["started_at"])
    observed = _parse_timestamp(row["observed_at"])
    latency = (
        int((observed - started).total_seconds() * 1000)
        if started and observed and observed >= started
        else None
    )
    evidence = {"checked_at": row["observed_at"], "latency_ms": latency, "model": row["model"]}
    if not row["success"]:
        return RuntimeValidationState("failed", reason="runtime_validation_failed", **evidence)
    if row["configuration_fingerprint"] != provider_configuration_fingerprint(connection, provider_id):
        return RuntimeValidationState("stale", reason="runtime_validation_configuration_changed", **evidence)
    age = ((now or datetime.now(UTC)) - started).total_seconds() if started else -1.0
    if not 0 <= age < max_age_seconds:
        return RuntimeValidationState("stale", reason="runtime_validation_expired", **evidence)
    return RuntimeValidationState("validated", **evidence)


def runtime_validated_within(
    connection: sqlite3.Connection, provider_id: str, seconds: int = RUNTIME_TEAM_FRESHNESS_SECONDS
) -> bool:
    """Indica si el runtime respondió una prueba real dentro de ``seconds`` con su configuración actual."""
    return runtime_validation_state(connection, provider_id, max_age_seconds=seconds).status == "validated"
