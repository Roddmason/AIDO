"""Recuperación de runtimes: re-validación vencida, reanudación tras validar o tras cooldown de cuota.

Familia extraída de ``remediations/service.py``: cada función recibe el ``BlockerRemediationService``
y encola la re-validación de los runtimes cuya evidencia venció, reanuda los retries que quedaron
esperando esa validación o el fin de un cooldown de cuota, y fija el runtime por defecto al cambiar de
runtime. Lo que el servicio expone o los tests parchean sigue disponible como delegador en la clase.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS
from local_control_center.remediations.retry_support import cooldown_rejected_provider_ids
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.validation import (
    REMOTE_MODEL_RUNTIME_KINDS,
    account_validation_state,
    runtime_validation_state,
)
from local_control_center.threads.repository import ThreadsRepository

if TYPE_CHECKING:
    from local_control_center.remediations.service import BlockerRemediationService

COOLDOWN_ELIGIBLE_BLOCKER_TYPES = frozenset({"resource_manager_unconfigured", "runtime_not_executable"})
"""Blocker types de `resource_manager` que la reanudación automática por cooldown puede tocar; ver
`resume_cooldown_expired_blocks`.
"""

__all__ = [
    "in_flight_execution",
    "resume_after_runtime_validation",
    "resume_cooldown_expired_blocks",
    "resume_runtime_team_retry",
    "revalidate_runtime",
    "set_default_runtime",
    "stale_validation_targets",
]


def revalidate_runtime(
    service: BlockerRemediationService, *, action: dict[str, Any], platform: Any
) -> dict[str, Any]:
    """Encola una ``models.validate_runtime`` por runtime vencido o reanuda el retry si ya responden.

    Una operación por runtime deja que ``operation_workload`` reserve la clase de cada proveedor
    (CLI, API o GPU local). La remediación queda ``pending`` (estado ``validating``) hasta que
    ``resume_after_runtime_validation`` la re-ejecuta con todos sus runtimes validados. Las
    ejecuciones encoladas se persisten en ``validationExecutions`` del payload: re-ejecutar la
    acción mientras una prueba sigue en curso la reutiliza en vez de gastar otra cuota del CLI.
    Los runtimes salen solo del payload persistido por el bloqueo; el cliente no puede cambiarlos.
    """
    stored = action.get("payload") or {}
    runtime_ids = [str(item) for item in stored.get("runtimeIds") or [] if str(item).strip()]
    if not runtime_ids:
        return {"status": "blocked", "action": "revalidate_runtime", "reason": "runtimeIds is required."}
    stale = service._stale_validation_targets(stored)
    if not stale:
        return resume_runtime_team_retry(service, action)
    registered = (getattr(platform, "execution_handlers", None) or {}).get("models.validate_runtime")
    if registered is None:
        return {
            "status": "blocked",
            "action": "revalidate_runtime",
            "reason": "The models.validate_runtime operation is not registered in this process.",
        }
    from local_control_center.executions.router import enqueue_registered_operation

    known = dict(stored.get("validationExecutions") or {})
    execution_ids: dict[str, str] = {}
    for provider_id, model in stale:
        key = f"{provider_id}:{model}" if model else provider_id
        body = {"projectId": action["projectId"], **({"model": model} if model else {})}
        in_flight = in_flight_execution(service, known.get(key))
        execution_ids[key] = in_flight or str(
            enqueue_registered_operation(
                platform,
                registered[0],
                {"provider_id": provider_id, "body": body},
                project_id=action["projectId"],
            ).get("executionId")
            or ""
        )
    service.repository.merge_payload(action["id"], {"validationExecutions": {**known, **execution_ids}})
    return {
        "status": "validating",
        "action": "revalidate_runtime",
        "runtimeIds": list(dict.fromkeys(provider_id for provider_id, _model in stale)),
        "executionIds": list(execution_ids.values()),
        "reason": "Runtime tests queued; the run resumes when every runtime answers.",
    }


def stale_validation_targets(
    service: BlockerRemediationService, stored: dict[str, Any]
) -> list[tuple[str, str | None]]:
    """Pares (runtime, modelo) del payload persistido que siguen sin validación vigente (24 h).

    Un runtime con ``runtimeModels`` se evalúa por cada modelo sellado que el gate de ejecución vio
    vencido (``staleRuntimes[].model``); uno sin modelos, como el gate: por cualquier modelo habilitado
    validado en API/gateway y a nivel de runtime en el resto. Así un éxito
    de otro modelo del mismo servidor no reanuda un loop que volvería a bloquear en el sellado.
    """
    runtime_models = stored.get("runtimeModels") if isinstance(stored.get("runtimeModels"), dict) else {}
    targets: list[tuple[str, str | None]] = []
    for provider_id in [str(item) for item in stored.get("runtimeIds") or [] if str(item).strip()]:
        models = [str(model) for model in runtime_models.get(provider_id) or [] if str(model).strip()]
        for model in models or [None]:
            state = (
                runtime_validation_state(
                    service.connection, provider_id, max_age_seconds=VALIDATION_TTL_SECONDS, model=model
                )
                if model
                else account_validation_state(
                    service.connection,
                    provider_id,
                    max_age_seconds=VALIDATION_TTL_SECONDS,
                    kinds=REMOTE_MODEL_RUNTIME_KINDS,
                )
            )
            if state.status != "validated":
                targets.append((provider_id, model))
    return targets


def in_flight_execution(service: BlockerRemediationService, execution_id: Any) -> str | None:
    """Devuelve ``execution_id`` si esa ejecución sigue sin estado terminal; ``None`` si no existe."""
    from local_control_center.executions.models import TERMINAL_STATUSES
    from local_control_center.executions.repository import ExecutionRepository

    if not str(execution_id or "").strip():
        return None
    try:
        status = ExecutionRepository(service.connection).get(str(execution_id))["status"]
    except KeyError:
        return None
    return None if status in TERMINAL_STATUSES else str(execution_id)


def resume_runtime_team_retry(service: BlockerRemediationService, action: dict[str, Any]) -> dict[str, Any]:
    """Reanuda el ``retry_loop`` pendiente del mismo loop cuando el equipo ya está validado."""
    retry_action = next(
        (
            item
            for item in service.repository.list_for_thread(action["threadId"])
            if item["actionType"] == "retry_loop"
            and item["loopId"] == action["loopId"]
            and item["status"] == "pending"
        ),
        None,
    )
    if retry_action is None:
        return {
            "status": "completed",
            "action": "revalidate_runtime",
            "reason": "Runtimes revalidated; there is no pending retry to resume.",
        }
    retry = service._retry_loop(action=retry_action, payload={})
    if retry.get("status") in {"completed", "queued"}:
        service.repository.mark_status(retry_action["id"], "resolved")
    return {**retry, "action": "revalidate_runtime", "retry": retry}


def resume_after_runtime_validation(
    service: BlockerRemediationService, provider_id: str
) -> list[dict[str, Any]]:
    """Re-ejecuta las re-pruebas que el operador lanzó para ``provider_id`` y ya tienen todo validado.

    Lo invoca el handler de ``models.validate_runtime`` tras un ``validated``. Solo cuenta una
    re-prueba cuyo "Re-probar" encoló la prueba de ese runtime (``validationExecutions``): una prueba
    hecha desde el panel del equipo no reanuda loops que el operador no pidió reintentar. Una
    re-prueba con otro runtime aún vencido sigue esperando su propia validación.
    """
    rows = service.connection.execute(
        """SELECT id FROM remediation_actions
           WHERE action_type = 'revalidate_runtime' AND status = 'pending'
           ORDER BY created_at ASC, rowid ASC"""
    ).fetchall()
    resumed: list[dict[str, Any]] = []
    for row in rows:
        action = service.repository.get(row["id"])
        stored = action["payload"] or {}
        runtime_ids = [str(item) for item in stored.get("runtimeIds") or []]
        stored_models = stored.get("runtimeModels")
        runtime_models = stored_models if isinstance(stored_models, dict) else {}
        model_keys = {f"{provider_id}:{model}" for model in runtime_models.get(provider_id) or []}
        keys = {provider_id, *model_keys}
        if provider_id not in runtime_ids or not keys & set(stored.get("validationExecutions") or {}):
            continue
        if not service._stale_validation_targets(stored):
            resumed.append(service.execute(action["id"], platform=None))
    return resumed


def resume_cooldown_expired_blocks(service: BlockerRemediationService) -> list[dict[str, Any]]:
    """Reanuda automáticamente los bloqueos de recursos cuyo cooldown de cuota ya cedió.

    Barato cuando no hay candidatos: el filtro inicial por `status`/`action_type`/`stage`/
    `blocker_type` corre antes de tocar la cuota. La primera vez que ve una acción pendiente
    comprueba si TODOS sus proveedores rechazados están hoy en cooldown de cuota (si alguno
    fue rechazado por otra causa —credenciales, salud, privacidad— no es de nuestra
    incumbencia y no la vuelve a tocar); de ser así, congela ese conjunto en el payload de la
    remediación (`cooldownSnapshot`) para poder comparar más tarde contra el cooldown vigente
    sin depender de un registro histórico aparte. Sólo reanuda cuando ese conjunto congelado
    deja de ser subconjunto del cooldown vigente, es decir, cuando al menos uno de esos
    proveedores ya salió.

    Reutiliza `execute()` sobre la acción `retry_loop` ya creada por `create_for_blocked_run`:
    el mismo camino del botón "Reintentar" del operador y de `resume_after_runtime_validation`.
    Un reintento exitoso deja la acción `resolved`, así que el siguiente tick ya no la
    encuentra: idempotente ante reintentos fallidos, resúmenes manuales del operador y
    reinicios del worker, porque todo el estado vive en SQLite, no en memoria del proceso.
    """
    placeholders = ",".join("?" for _ in COOLDOWN_ELIGIBLE_BLOCKER_TYPES)
    rows = service.connection.execute(
        f"""SELECT id FROM remediation_actions
            WHERE status = 'pending' AND action_type = 'retry_loop' AND stage = 'resource_manager'
              AND blocker_type IN ({placeholders})
            ORDER BY created_at ASC, rowid ASC""",
        tuple(COOLDOWN_ELIGIBLE_BLOCKER_TYPES),
    ).fetchall()
    if not rows:
        return []
    from local_control_center.agents.quota_manager import QuotaManager

    still_cooling = QuotaManager(service.connection).providers_in_cooldown_detail()
    resumed: list[dict[str, Any]] = []
    for row in rows:
        action = service.repository.get(str(row["id"]))
        if action["status"] != "pending":
            continue
        payload = action["payload"] if isinstance(action["payload"], dict) else {}
        snapshot = payload.get("cooldownSnapshot")
        if not isinstance(snapshot, dict) or not snapshot.get("providerIds"):
            provider_ids = cooldown_rejected_provider_ids(payload.get("details") or {})
            if not provider_ids or not provider_ids <= still_cooling.keys():
                continue
            service.repository.merge_payload(
                action["id"],
                {
                    "cooldownSnapshot": {
                        "providerIds": sorted(provider_ids),
                        "cooldownUntil": min(still_cooling[pid] for pid in provider_ids),
                    }
                },
            )
            continue
        provider_ids = {str(item) for item in snapshot["providerIds"]}
        if not provider_ids or provider_ids <= still_cooling.keys():
            continue
        relieved = sorted(provider_ids - still_cooling.keys())
        result = service.execute(action["id"], platform=None)
        if (result.get("execution") or {}).get("status") == "queued":
            ThreadsRepository(service.connection).record_event(
                thread_id=action["threadId"],
                type="auto_resumed",
                agent_role="worker",
                payload={
                    "reason": f"Reanudado automáticamente: {relieved[0]} salió de cooldown.",
                    "providerIds": relieved,
                    "remediationActionId": action["id"],
                },
            )
            resumed.append(result)
    return resumed


def set_default_runtime(service: BlockerRemediationService, runtime_id: str) -> dict[str, Any]:
    """Fija ``runtime_id`` como runtime por defecto global (primero en el orden) y guarda las preferencias."""
    repository = RuntimeConfigRepository(service.connection)
    try:
        current = repository.get_preferences()
    except KeyError:
        current = {
            "scope": "global",
            "scopeId": "",
            "runtimeOrder": [],
            "defaultProfiles": {},
            "enabled": True,
            "metadata": {},
        }
    runtime_order = [str(item) for item in current.get("runtimeOrder") or [] if str(item).strip()]
    if runtime_id not in runtime_order:
        runtime_order.insert(0, runtime_id)
    return repository.upsert_preferences(
        {
            "scope": "global",
            "scopeId": "",
            "defaultRuntime": runtime_id,
            "runtimeOrder": runtime_order,
            "defaultProfiles": current.get("defaultProfiles") or {},
            "enabled": current.get("enabled", True),
            "metadata": {
                **dict(current.get("metadata") or {}),
                "updatedBy": "blocker_remediation",
            },
        }
    )
