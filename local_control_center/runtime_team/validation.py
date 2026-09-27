"""Frescura de validación por runtime para el equipo del hilo (ventana corta, solo lectura).

Lee la misma evidencia de ejecución real que ``model_execution_health`` usa para su TTL de 24 h,
pero con una ventana propia: el selector del hilo exige una prueba de ida y vuelta reciente con la
configuración actual. Los consumidores de 24 h no cambian. Los runtimes de modelo (API, gateway,
local) se evalúan por modelo habilitado: basta uno validado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from local_control_center.agents.model_execution_health import provider_configuration_fingerprint
from local_control_center.agents.provider_accounts import ProviderAccountStore

RUNTIME_TEAM_FRESHNESS_SECONDS = 30 * 60
#: Tipos de runtime cuya validación se lleva por (provider, modelo) habilitado.
MODEL_RUNTIME_KINDS = frozenset({"api", "gateway", "local"})
#: Runtimes remotos de modelo: el gate de un equipo sin ``roleModels`` los evalúa por cualquier modelo
#: habilitado. Un local sella ``roleModels`` y un equipo legado sin ellos conserva el gate por runtime.
REMOTE_MODEL_RUNTIME_KINDS = frozenset({"api", "gateway"})


@dataclass(frozen=True)
class RuntimeValidationState:
    """Última evidencia de un runtime traducida al estado que muestra y exige el selector."""

    status: str
    checked_at: str | None = None
    latency_ms: int | None = None
    model: str | None = None
    reason: str | None = None
    http_status: int | None = None

    def to_record(self) -> dict[str, Any]:
        """Serializa el estado con el contrato camelCase de la API."""
        return {
            "status": self.status,
            "checkedAt": self.checked_at,
            "latencyMs": self.latency_ms,
            "model": self.model,
            "reason": self.reason,
            "httpStatus": self.http_status,
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
    model: str | None = None,
    now: datetime | None = None,
) -> RuntimeValidationState:
    """Clasifica la última ejecución registrada del runtime (o de uno de sus modelos).

    Devuelve validated, stale, failed o never. Con ``model`` solo cuenta la evidencia de ese modelo, así la
    falla de otro modelo del mismo servidor no invalida la validación del sellado. Una falla posterior
    invalida cualquier éxito anterior y un cambio de configuración vuelve ``stale`` la evidencia (también la
    fallida, para que se vuelva a probar), en el mismo orden que ``model_validation_rejection``.
    """
    row = connection.execute(
        """SELECT model, configuration_fingerprint, success, started_at, observed_at, http_status
           FROM model_execution_health WHERE provider_id = ? AND (? IS NULL OR model = ?)
           ORDER BY started_at DESC, id DESC LIMIT 1""",
        (provider_id, model, model),
    ).fetchone()
    if row is None:
        return RuntimeValidationState("never", model=model, reason="runtime_validation_required")
    return _state_from_row(
        row,
        provider_configuration_fingerprint(connection, provider_id),
        max_age_seconds=max_age_seconds,
        now=now,
    )


def _state_from_row(
    row: sqlite3.Row, fingerprint: str, *, max_age_seconds: int, now: datetime | None
) -> RuntimeValidationState:
    """Traduce la última fila de evidencia de un modelo al estado del selector."""
    started = _parse_timestamp(row["started_at"])
    observed = _parse_timestamp(row["observed_at"])
    latency = (
        int((observed - started).total_seconds() * 1000)
        if started and observed and observed >= started
        else None
    )
    evidence = {"checked_at": row["observed_at"], "latency_ms": latency, "model": row["model"]}
    if row["configuration_fingerprint"] != fingerprint:
        return RuntimeValidationState("stale", reason="runtime_validation_configuration_changed", **evidence)
    if not row["success"]:
        return RuntimeValidationState(
            "failed", reason="runtime_validation_failed", http_status=row["http_status"], **evidence
        )
    age = ((now or datetime.now(UTC)) - started).total_seconds() if started else -1.0
    if not 0 <= age < max_age_seconds:
        return RuntimeValidationState("stale", reason="runtime_validation_expired", **evidence)
    return RuntimeValidationState("validated", **evidence)


def enabled_models_validation_state(
    connection: sqlite3.Connection,
    provider_id: str,
    enabled_models: Iterable[str],
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> RuntimeValidationState:
    """Estado de un runtime de modelo evaluado por (provider, modelo) sobre sus modelos habilitados.

    Vale como validado si ALGÚN modelo habilitado tiene una validación vigente: la falla de un modelo
    (p. ej. un upstream del gateway sin cuenta) no oculta el éxito de otro. Sin ninguno validado devuelve
    la evidencia más reciente entre los habilitados, así la evidencia de un modelo ya deshabilitado no
    cuenta; sin modelos habilitados o sin evidencia de ellos, ``never``. Lee la última fila de cada
    modelo en una sola consulta: un gateway con 1000+ modelos habilitados no hace 1000 consultas.
    """
    enabled = list(dict.fromkeys(enabled_models))
    rows = (
        connection.execute(
            """SELECT model, configuration_fingerprint, success, started_at, observed_at, http_status
               FROM (SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY model ORDER BY started_at DESC, id DESC) AS position
                     FROM model_execution_health WHERE provider_id = ?)
               WHERE position = 1""",
            (provider_id,),
        ).fetchall()
        if enabled
        else []
    )
    latest_rows = {str(row["model"]): row for row in rows}
    fingerprint = provider_configuration_fingerprint(connection, provider_id) if latest_rows else ""
    latest: RuntimeValidationState | None = None
    for model in enabled:
        row = latest_rows.get(model)
        if row is None:
            continue
        state = _state_from_row(row, fingerprint, max_age_seconds=max_age_seconds, now=now)
        if state.status == "validated":
            return state
        if state.status != "never" and (
            latest is None or str(state.checked_at or "") > str(latest.checked_at or "")
        ):
            latest = state
    return latest or RuntimeValidationState("never", reason="runtime_validation_required")


def account_validation_state(
    connection: sqlite3.Connection,
    provider_id: str,
    *,
    max_age_seconds: int,
    kinds: frozenset[str] = MODEL_RUNTIME_KINDS,
    now: datetime | None = None,
) -> RuntimeValidationState:
    """Validación de un runtime del equipo: por modelo habilitado en los ``kinds`` dados, global en el resto.

    Un CLI valida siempre su primer modelo y su evidencia se lee a nivel de runtime como antes; una cuenta
    desconocida también, para no inventar un estado.
    """
    store = ProviderAccountStore(connection)
    try:
        account = store.get_provider_account(provider_id)
    except KeyError:
        account = None
    if account is None or str(account.get("providerType") or "") not in kinds:
        return runtime_validation_state(connection, provider_id, max_age_seconds=max_age_seconds, now=now)
    enabled = [str(item["model"]) for item in store.list_models(provider_id) if item.get("enabled")]
    return enabled_models_validation_state(
        connection, provider_id, enabled, max_age_seconds=max_age_seconds, now=now
    )


def runtime_validated_within(
    connection: sqlite3.Connection, provider_id: str, seconds: int = RUNTIME_TEAM_FRESHNESS_SECONDS
) -> bool:
    """Indica si el runtime respondió una prueba real dentro de ``seconds`` con su configuración actual."""
    return runtime_validation_state(connection, provider_id, max_age_seconds=seconds).status == "validated"
