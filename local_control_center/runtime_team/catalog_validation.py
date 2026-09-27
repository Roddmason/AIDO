"""Validación modelo por modelo de un proveedor de API/gateway (operación ``models.validate_all_models``).

Un gateway como OmniRoute anuncia 1000+ modelos y solo algunos tienen cuenta upstream; NVIDIA NIM,
OpenRouter y los OpenAI-compatible también exponen muchos. Esta corrida prueba cada modelo habilitado
con la misma completion de test-prompt que ``models.validate_runtime`` (``RuntimeValidationService``),
deja la evidencia por modelo en ``model_execution_health`` y guarda el último resultado en
``model_validation_outcomes``:

- ``ok``: el modelo respondió; queda validado (alimenta el orden de candidatos y la frescura).
- ``failed``: falla definitiva del modelo (400/401/403/404, 200 sin completion...). Se **descarta**:
  ``enabled=0`` con ``disabled_reason='validation_failed'``, nunca se borra, un resync no lo revive y el
  operador lo re-habilita o lo vuelve a probar. Solo se descarta si el proveedor demostró funcionar (un
  modelo pasó en esta corrida o tiene otro validado en 24 h): si fallan todos, el problema es del
  proveedor (credencial, endpoint) y no se apaga ningún modelo.
- ``skipped``: falla transitoria (429, 502/503/504, timeout) o del endpoint: ni validado ni descartado.

El proveedor sigue validado mientras al menos un modelo habilitado pase (``enabled_models_validation_state``).
La red corre solo en hilos acotados (``concurrency``), cada uno con su propia conexión SQLite en autocommit
y sin transacción abierta; el hilo principal persiste cada resultado en transacciones cortas, revisa la
cancelación de la ejecución, el switch del proveedor y la suspensión por cuota, y respeta el presupuesto
total (tope por modelo y por corrida, dentro del sobre de 900 s de la ejecución).

@author Rodrigo Mason
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS
from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.provider_accounts import VALIDATION_FAILED_REASON, ProviderAccountStore
from local_control_center.agents.providers.factory import (
    ProviderAdapterResolutionError,
    UnsupportedProviderCapabilityError,
    provider_account_policy_kind,
)
from local_control_center.agents.quota_manager import QuotaManager
from local_control_center.agents.runtime_failure_classifier import EVIDENCE_LIMIT
from local_control_center.process_supervision.context import remaining_execution_timeout
from local_control_center.process_supervision.repository import ManagedProcessRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.db import immediate_transaction, open_sqlite_connection
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .probe import RuntimeValidationService, endpoint_unreachable, stored_secret_missing
from .validation import (
    REMOTE_MODEL_RUNTIME_KINDS,
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    account_validation_state,
    enabled_models_validation_state,
)

#: Estados HTTP transitorios: el modelo puede andar en otro momento, no se descarta.
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 502, 503, 504})
#: Por debajo de este margen no se lanza otra prueba (no alcanzaría a responder).
MIN_MODEL_SECONDS = 2.0
#: Cada cuánto el hilo principal revisa cancelación, switch del proveedor, cuota y presupuesto.
CONTROL_INTERVAL_SECONDS = 0.5
#: Margen reservado dentro del sobre de la ejecución para persistir el cierre.
CLEANUP_SECONDS = 30
#: Espera extra por hilos colgados tras el presupuesto; son daemon y no retienen el proceso.
WORKER_GRACE_SECONDS = 15.0
#: Una corrida ``running`` sin avance por más que esto (y sin ejecución viva) se informa interrumpida.
STALE_RUN_SECONDS = 15 * 60
_DETAIL_LIMIT = min(EVIDENCE_LIMIT, 500)
_WORKER_DONE = object()


class CatalogValidationBlocked(Exception):
    """La corrida no puede empezar (proveedor apagado, sin credencial, suspendido, otra corrida viva)."""

    def __init__(self, code: str, detail: str, *, status_code: int = 409):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class CatalogValidationLimits:
    """Límites de una corrida; los valores ya vienen acotados por el contrato HTTP."""

    concurrency: int = 4
    model_timeout_seconds: float = 20.0
    budget_seconds: float = 600.0


@dataclass
class _RunState:
    run_id: str
    provider_id: str
    kind: str
    total: int
    limits: CatalogValidationLimits
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: int = 0
    abort_reason: str = ""


def classify_outcome(result: dict[str, Any]) -> str:
    """``ok``, ``failed`` (definitiva, descartable) o ``skipped`` (transitoria o del endpoint)."""
    if result.get("status") == "validated":
        return "ok"
    http_status = result.get("httpStatus")
    if isinstance(http_status, int):
        return "skipped" if http_status in TRANSIENT_HTTP_STATUSES or http_status >= 500 else "failed"
    if result.get("status") == "deferred" or endpoint_unreachable(result):
        return "skipped"
    text = f"{result.get('reason') or ''} {result.get('evidence') or ''}".lower()
    if "timeout" in text or "timed out" in text or "provider_unreachable" in text:
        return "skipped"
    return "failed"


def _outcome_detail(result: dict[str, Any]) -> str:
    """Causa breve y redactada: estado HTTP + extracto del proveedor, o la causa estable."""
    parts = []
    if isinstance(result.get("httpStatus"), int):
        parts.append(f"HTTP {result['httpStatus']}")
    evidence = str(result.get("evidence") or "").strip()
    parts.append(evidence or str(result.get("reason") or "runtime_validation_failed"))
    return str(redact_secrets(": ".join(parts)))[:_DETAIL_LIMIT]


def _database_path(connection: sqlite3.Connection) -> Path | None:
    row = connection.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CatalogValidationService:
    """Corre y lee la validación modelo por modelo de un proveedor de API/gateway."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)

    # ------------------------------------------------------------------ lectura

    def status(self, provider_id: str) -> dict[str, Any]:
        """Última corrida (reconciliada con su ejecución) y último resultado por modelo, sin red.

        Raises:
            KeyError: el proveedor no existe.
        """
        account = self.accounts.get_provider_account(provider_id)
        provider_id = str(account["providerId"])
        row = self.connection.execute(
            """SELECT * FROM model_validation_runs WHERE provider_id = ?
               ORDER BY started_at DESC, rowid DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        outcomes = self._outcomes(provider_id)
        tested = {item["model"] for item in outcomes}
        untested = sum(
            1
            for item in self.accounts.list_models(provider_id)
            if item.get("enabled") and str(item["model"]) not in tested
        )
        return {
            "run": self._run_record(row) if row is not None else None,
            "outcomes": outcomes,
            "untested": untested,
            "providerValidation": self._provider_validation(provider_id),
        }

    def _provider_validation(self, provider_id: str) -> dict[str, Any]:
        return account_validation_state(
            self.connection,
            provider_id,
            max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
            kinds=REMOTE_MODEL_RUNTIME_KINDS,
        ).to_record()

    def _outcomes(self, provider_id: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT o.*, c.id AS catalog_id, c.enabled AS catalog_enabled, c.disabled_reason
               FROM model_validation_outcomes o
               JOIN model_catalog c ON c.provider_id = o.provider_id AND c.model = o.model
               WHERE o.provider_id = ? AND (? IS NULL OR o.run_id = ?)
               ORDER BY o.model""",
            (provider_id, run_id, run_id),
        ).fetchall()
        return [
            {
                "modelId": row["catalog_id"],
                "model": row["model"],
                "status": row["status"],
                "httpStatus": row["http_status"],
                "reason": row["reason"],
                "detail": row["detail"],
                "latencyMs": row["latency_ms"],
                "runId": row["run_id"],
                "testedAt": row["tested_at"],
                "enabled": bool(row["catalog_enabled"]),
                "discarded": row["disabled_reason"] == VALIDATION_FAILED_REASON,
            }
            for row in rows
        ]

    def _run_record(self, row: sqlite3.Row) -> dict[str, Any]:
        status = str(row["status"])
        reason = str(row["reason"] or "")
        if status == "running":
            execution = self.connection.execute(
                "SELECT status, reason FROM operational_executions WHERE id = ?", (row["id"],)
            ).fetchone()
            updated = _parse(row["updated_at"])
            if execution is not None and execution["status"] in {
                "cancelled",
                "failed",
                "blocked",
                "interrupted",
            }:
                status = "cancelled" if execution["status"] == "cancelled" else "interrupted"
                reason = reason or str(execution["reason"] or execution["status"])
            elif execution is None and (
                updated is None or (datetime.now(UTC) - updated).total_seconds() > STALE_RUN_SECONDS
            ):
                status, reason = "interrupted", reason or "model_validation_interrupted"
        return {
            "runId": row["id"],
            "providerId": row["provider_id"],
            "status": status,
            "reason": reason,
            "total": row["total"],
            "done": row["done"],
            "ok": row["ok"],
            "failed": row["failed"],
            "skipped": row["skipped"],
            "discarded": row["discarded"],
            "concurrency": row["concurrency"],
            "modelTimeoutSeconds": row["model_timeout_seconds"],
            "budgetSeconds": row["budget_seconds"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "finishedAt": row["finished_at"],
        }

    # ------------------------------------------------------------------ corrida

    def run(
        self,
        provider_id: str,
        *,
        run_id: str | None = None,
        model_ids: list[str] | None = None,
        only_untested: bool = False,
        limits: CatalogValidationLimits | None = None,
        project_id: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Prueba los modelos pedidos (o los habilitados) y devuelve la corrida con sus resultados.

        Raises:
            KeyError: el proveedor no existe.
            ValueError: el proveedor no es de API/gateway o algún id no es de su catálogo.
            CatalogValidationBlocked: el proveedor no puede probarse ahora (sin tocar ningún modelo).
        """
        limits = limits or CatalogValidationLimits()
        account = self.accounts.get_provider_account(provider_id)
        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if kind not in REMOTE_MODEL_RUNTIME_KINDS:
            raise ValueError(f"Provider {provider_id} is not an API or gateway provider.")
        self._preflight(account, project_id=project_id)
        models = self._target_models(provider_id, model_ids, only_untested=only_untested)
        run_id = run_id or f"model-validation-{uuid.uuid4().hex}"
        self._ensure_no_live_run(provider_id, run_id)
        budget = float(
            remaining_execution_timeout(int(limits.budget_seconds), cleanup_seconds=CLEANUP_SECONDS)
        )
        limits = CatalogValidationLimits(
            concurrency=max(1, min(limits.concurrency, len(models) or 1)),
            model_timeout_seconds=limits.model_timeout_seconds,
            budget_seconds=budget,
        )
        state = _RunState(run_id, provider_id, kind, len(models), limits)
        self._start_run(state)
        cancel_check = should_cancel or (lambda: self._cancel_requested(run_id))
        if models:
            self._execute(state, models, cancel_check)
        return self._finish(state)

    def _preflight(self, account: dict[str, Any], *, project_id: str | None) -> None:
        """Switch, política, credencial y cuota antes de cualquier red; un bloqueo no toca modelos."""
        from local_control_center.agents.model_gateway_api import _validate_real_discovery_credentials
        from local_control_center.runtime_team.probe import credential_failure

        provider_id = str(account["providerId"])
        if not account.get("enabled"):
            raise CatalogValidationBlocked(
                "provider_disabled",
                f"Provider {provider_id} is switched off; turn it on to validate its models.",
            )
        decision = RuntimeConfigRepository(self.connection).runtime_policy_decision(
            provider_id=provider_id,
            provider_family=str(account.get("providerFamily") or ""),
            kind=provider_account_policy_kind(account),
            project_id=project_id,
        )
        if not decision.get("allowed"):
            raise CatalogValidationBlocked(
                "policy_denied",
                str(decision.get("reason") or "Runtime policy denies this provider."),
                status_code=403,
            )
        try:
            _validate_real_discovery_credentials(account)
        except Exception as error:
            cause, evidence = credential_failure(account, str(getattr(error, "detail", error)))
            raise CatalogValidationBlocked(cause, evidence, status_code=400) from error
        if (unreadable := stored_secret_missing(account)) is not None:
            raise CatalogValidationBlocked(unreadable[0], unreadable[1], status_code=400)
        if provider_id in QuotaManager(self.connection).providers_in_cooldown():
            raise CatalogValidationBlocked(
                "provider_quota_suspended",
                f"Provider {provider_id} is suspended by usage quota; validating every model would spend more.",
            )
        try:
            provider_instance(provider_id, connection=self.connection)
        except (ProviderAdapterResolutionError, UnsupportedProviderCapabilityError) as error:
            code = getattr(error, "public_code", None) or getattr(
                error, "code", "provider_adapter_unavailable"
            )
            raise CatalogValidationBlocked(str(code), str(redact_secrets(str(error)))) from error

    def _target_models(
        self, provider_id: str, model_ids: list[str] | None, *, only_untested: bool
    ) -> list[str]:
        catalog = self.accounts.list_models(provider_id)
        if model_ids is not None:
            by_id = {str(item["id"]): str(item["model"]) for item in catalog}
            unknown = [item for item in dict.fromkeys(model_ids) if item not in by_id]
            if unknown:
                raise ValueError(f"{len(unknown)} model id(s) are not in the catalog of {provider_id}.")
            return [by_id[item] for item in dict.fromkeys(model_ids)]
        enabled = [str(item["model"]) for item in catalog if item.get("enabled")]
        if not only_untested:
            return enabled
        cutoff = (
            (datetime.now(UTC) - timedelta(seconds=VALIDATION_TTL_SECONDS))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        recent = {
            str(row["model"])
            for row in self.connection.execute(
                """SELECT model FROM model_validation_outcomes
                   WHERE provider_id = ? AND status IN ('ok', 'failed') AND tested_at >= ?""",
                (provider_id, cutoff),
            ).fetchall()
        }
        return [model for model in enabled if model not in recent]

    def _ensure_no_live_run(self, provider_id: str, run_id: str) -> None:
        row = self.connection.execute(
            """SELECT * FROM model_validation_runs WHERE provider_id = ? AND status = 'running' AND id <> ?
               ORDER BY started_at DESC LIMIT 1""",
            (provider_id, run_id),
        ).fetchone()
        if row is not None and self._run_record(row)["status"] == "running":
            raise CatalogValidationBlocked(
                "model_validation_already_running",
                f"A model validation of {provider_id} is already running ({row['done']}/{row['total']}).",
            )

    def _cancel_requested(self, run_id: str) -> bool:
        row = self.connection.execute(
            "SELECT status, cancel_requested_at FROM operational_executions WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return False
        if row["cancel_requested_at"] or row["status"] in {"cancel_requested", "cancelled"}:
            return True
        return ManagedProcessRepository(self.connection).cancellation_reason(run_id) is not None

    def _start_run(self, state: _RunState) -> None:
        now = utc_now()
        with immediate_transaction(self.connection):
            self.connection.execute(
                """INSERT INTO model_validation_runs
                   (id, provider_id, status, total, concurrency, model_timeout_seconds, budget_seconds,
                    started_at, updated_at)
                   VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status = 'running', total = excluded.total, done = 0,
                     ok = 0, failed = 0, skipped = 0, discarded = 0, reason = '', updated_at = excluded.updated_at,
                     finished_at = NULL""",
                (
                    state.run_id,
                    state.provider_id,
                    state.total,
                    state.limits.concurrency,
                    state.limits.model_timeout_seconds,
                    state.limits.budget_seconds,
                    now,
                    now,
                ),
            )

    def _execute(self, state: _RunState, models: list[str], should_cancel: Callable[[], bool]) -> None:
        """Reparte los modelos en hilos de red y persiste cada resultado desde este hilo."""
        deadline = time.monotonic() + state.limits.budget_seconds
        stop = threading.Event()
        results: queue.Queue[Any] = queue.Queue()
        pending = iter(models)
        lock = threading.Lock()
        db_path = _database_path(self.connection)

        def next_model() -> str | None:
            with lock:
                return next(pending, None)

        workers = state.limits.concurrency if db_path is not None else 0
        for index in range(workers):
            threading.Thread(
                target=_probe_worker,
                args=(
                    db_path,
                    state.provider_id,
                    state.kind,
                    next_model,
                    stop,
                    deadline,
                    state.limits,
                    results,
                ),
                name=f"aido-model-validation-{index}",
                daemon=True,
            ).start()
        if workers == 0:  # base en memoria (pruebas unitarias): mismo camino, sin hilos
            _probe_loop(
                self.connection,
                state.provider_id,
                state.kind,
                next_model,
                stop,
                deadline,
                state.limits,
                results,
            )
            results.put(_WORKER_DONE)
            workers = 1
        last_control = 0.0
        while workers:
            try:
                item = results.get(timeout=CONTROL_INTERVAL_SECONDS)
            except queue.Empty:
                item = None
            if item is _WORKER_DONE:
                workers -= 1
            elif item is not None and item[0] is None:
                # El hilo no pudo armar su adapter: es del proveedor, no de un modelo.
                state.abort_reason = state.abort_reason or "provider_adapter_unavailable"
                stop.set()
            elif item is not None:
                self._record_outcome(state, *item)
                if not state.abort_reason and endpoint_unreachable(item[1]):
                    state.abort_reason = "provider_unreachable"
                    stop.set()
            now = time.monotonic()
            if now - last_control >= CONTROL_INTERVAL_SECONDS and not stop.is_set():
                last_control = now
                reason = self._control_reason(state.provider_id, should_cancel)
                if reason:
                    state.abort_reason = reason
                    stop.set()
            if now > deadline + WORKER_GRACE_SECONDS:
                stop.set()
                break
        done = len(state.ok) + len(state.failed) + state.skipped
        if not state.abort_reason and done < state.total:
            state.abort_reason = "budget_exhausted"

    def _control_reason(self, provider_id: str, should_cancel: Callable[[], bool]) -> str:
        """Motivo para detener la corrida: cancelación, switch apagado o suspensión por cuota."""
        if should_cancel():
            return "cancelled"
        enabled = self.connection.execute(
            "SELECT enabled FROM provider_accounts WHERE provider_id = ?", (provider_id,)
        ).fetchone()
        if enabled is None or not enabled["enabled"]:
            return "provider_disabled"
        if provider_id in QuotaManager(self.connection).providers_in_cooldown():
            return "provider_quota_suspended"
        return ""

    def _record_outcome(self, state: _RunState, model: str, result: dict[str, Any]) -> None:
        status = classify_outcome(result)
        detail = None if status == "ok" else _outcome_detail(result)
        if status == "ok":
            state.ok.append(model)
        elif status == "failed":
            state.failed[model] = detail or ""
        else:
            state.skipped += 1
        http_status = result.get("httpStatus")
        with immediate_transaction(self.connection):
            self.connection.execute(
                """INSERT INTO model_validation_outcomes
                   (provider_id, model, status, http_status, reason, detail, latency_ms, run_id, tested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id, model) DO UPDATE SET status = excluded.status,
                     http_status = excluded.http_status, reason = excluded.reason, detail = excluded.detail,
                     latency_ms = excluded.latency_ms, run_id = excluded.run_id, tested_at = excluded.tested_at""",
                (
                    state.provider_id,
                    model,
                    status,
                    http_status if isinstance(http_status, int) else None,
                    None
                    if status == "ok"
                    else str(result.get("reason") or "runtime_validation_failed")[:120],
                    detail,
                    result.get("latencyMs") if isinstance(result.get("latencyMs"), int) else None,
                    state.run_id,
                    utc_now(),
                ),
            )
            self.connection.execute(
                """UPDATE model_validation_runs SET done = ?, ok = ?, failed = ?, skipped = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    len(state.ok) + len(state.failed) + state.skipped,
                    len(state.ok),
                    len(state.failed),
                    state.skipped,
                    utc_now(),
                    state.run_id,
                ),
            )

    def _provider_proven(self, state: _RunState) -> bool:
        """El proveedor funciona: un modelo pasó en esta corrida u otro habilitado está validado (24 h)."""
        if state.ok:
            return True
        failed = set(state.failed)
        others = [
            str(item["model"])
            for item in self.accounts.list_models(state.provider_id)
            if item.get("enabled") and str(item["model"]) not in failed
        ]
        return (
            enabled_models_validation_state(
                self.connection, state.provider_id, others, max_age_seconds=VALIDATION_TTL_SECONDS
            ).status
            == "validated"
        )

    def _finish(self, state: _RunState) -> dict[str, Any]:
        """Descarta fallas definitivas, restaura los que pasaron y cierra la corrida en una transacción.

        Solo descarta si el proveedor demostró funcionar (``_provider_proven``).
        """
        proven = self._provider_proven(state)
        reason = state.abort_reason
        if state.failed and not proven:
            reason = reason or "no_model_passed"
        status = {
            "": "completed",
            "no_model_passed": "completed",
            "cancelled": "cancelled",
            "budget_exhausted": "budget_exhausted",
        }.get(reason, "aborted")
        with immediate_transaction(self.connection):
            discarded = self.accounts.discard_failed_models(state.provider_id, state.failed) if proven else 0
            self.accounts.restore_validated_models(state.provider_id, state.ok)
            self.connection.execute(
                """UPDATE model_validation_runs SET status = ?, reason = ?, discarded = ?, updated_at = ?,
                     finished_at = ? WHERE id = ?""",
                (status, reason, discarded, utc_now(), utc_now(), state.run_id),
            )
        row = self.connection.execute(
            "SELECT * FROM model_validation_runs WHERE id = ?", (state.run_id,)
        ).fetchone()
        return {
            "run": self._run_record(row),
            "outcomes": self._outcomes(state.provider_id, run_id=state.run_id),
            "providerValidation": self._provider_validation(state.provider_id),
        }


def _probe_worker(
    db_path: Path,
    provider_id: str,
    kind: str,
    next_model: Callable[[], str | None],
    stop: threading.Event,
    deadline: float,
    limits: CatalogValidationLimits,
    results: queue.Queue[Any],
) -> None:
    """Hilo de red: conexión propia en autocommit (nunca dentro de una transacción) y un adapter propio."""
    try:
        connection = open_sqlite_connection(db_path)
    except Exception:
        results.put(_WORKER_DONE)
        return
    try:
        _probe_loop(connection, provider_id, kind, next_model, stop, deadline, limits, results)
    finally:
        connection.close()
        results.put(_WORKER_DONE)


def _probe_loop(
    connection: sqlite3.Connection,
    provider_id: str,
    kind: str,
    next_model: Callable[[], str | None],
    stop: threading.Event,
    deadline: float,
    limits: CatalogValidationLimits,
    results: queue.Queue[Any],
) -> None:
    try:
        provider = provider_instance(provider_id, connection=connection)
    except Exception:
        results.put((None, {}))
        return
    service = RuntimeValidationService(connection)
    for model in _models_until_stopped(next_model, stop, deadline):
        remaining = deadline - time.monotonic()
        try:
            result = service.probe_model(
                provider_id,
                kind,
                model,
                provider,
                timeout_seconds=max(min(limits.model_timeout_seconds, remaining), MIN_MODEL_SECONDS),
            )
        except Exception as error:  # una falla inesperada de un modelo no tumba la corrida
            result = {
                "status": "failed",
                "reason": "runtime_validation_failed",
                "evidence": str(redact_secrets(f"{error.__class__.__name__}: {error}"))[:_DETAIL_LIMIT],
                "httpStatus": None,
                "latencyMs": None,
            }
        results.put((model, result))


def _models_until_stopped(
    next_model: Callable[[], str | None], stop: threading.Event, deadline: float
) -> Iterator[str]:
    while not stop.is_set() and deadline - time.monotonic() >= MIN_MODEL_SECONDS:
        model = next_model()
        if model is None:
            return
        yield model
