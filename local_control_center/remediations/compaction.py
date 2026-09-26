"""Compacta el payload de una remediacion antes de persistirlo.

Medido en la instalacion real: `remediation_actions` (498 filas / 284 MiB, hasta 14,3 MB por fila).
El crecimiento tiene dos formas, y ambas aparecen en cualquier profundidad de `payload.details`, no
solo en su nivel superior (un blocker de ejecucion guarda el snapshot completo del input enviado al
agente, que a su vez contiene su propio `teamSchedule`/`agentAssignments`):

- **Perfiles de rol completos** bajo las claves `teamSchedule.roles` o `agentAssignments` (~4-7 MB
  medidos): cada rol trae su perfil de ejecucion entero (tools, quality gates, JSON schema de
  salida). Ningun lector conocido navega ese detalle completo, asi que se reemplaza por un resumen
  fijo de 4 campos (rol, runtime/modelo elegido, estado, motivo acotado a 500 caracteres).
- **Listas de candidatos sin tope** bajo las claves `rejected`/`candidates`/`deferred`/`validated`
  (hasta 3.454 entradas, ~547 KiB, medido dentro de un solo `resourceBlockers[i].decision`): el
  re-diagnostico estructural (`payloads.decision_engine_failure` /
  `resource_selection_constraint_failure`) solo pregunta la *presencia* de un `reason` dentro de
  esas listas, nunca las recorre completas, asi que se muestrean con un representante por `reason`
  distinto (mas relleno hasta el tope) en vez de truncarse a ciegas, agregando `<key>Total` y
  `<key>ByReason` para quien necesite el conteo real.

Ni siquiera comprimiendo ambos patrones un blocker rico (12+ roles bloqueados, cada uno con su
`policyResult` completo) garantiza bajar de 64 KiB solo por conteo fijo: por eso `resourceBlockers`
usa ademas un presupuesto de bytes (`RESOURCE_BLOCKERS_BUDGET_BYTES`), conservando blockers completos
en orden hasta agotarlo en vez de un numero de entradas fijo. Ademas, un blocker de ejecucion puede
guardar snapshots crudos de la corrida (`runtimeResult`/`agentRun`/manifiestos de archivos con
cientos de entradas) que combinan varios de estos patrones a la vez; para esos casos, tras aplicar
las reglas especificas, `_hard_cap_if_needed` poda la rama mas pesada del arbol hasta que `details`
completo quepa en `DETAILS_HARD_CAP_BUDGET_BYTES`. Medido sobre las 498 filas reales: 0 filas sobre
64 KiB tras la compactacion (maximo 61,4 KB), ~8 ms/fila.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.shared.serialization import json_dumps

MAX_STRING_LENGTH = 500
MAX_CANDIDATE_ENTRIES = 5
CANDIDATE_LIST_KEYS = ("rejected", "candidates", "deferred", "validated")
ROLE_LIST_KEYS = ("roles", "agentAssignments")
GENERIC_LIST_LIMIT = 3
"""Red de seguridad para listas largas no anticipadas (manifiestos de archivos, tareas, etc.).

Medido en la instalacion real: incluso tras resumir roles y candidatos, algunas filas seguian sobre
64 KiB por listas ajenas a ese patron (`agentTasks`, `fileManifest.files` con cientos de entradas
`{path, sha256}` de la evidencia de ejecucion). Ningun re-diagnostico estructural conocido navega
esas listas completas; son solo evidencia informativa, asi que cualquier lista larga no cubierta
por una regla especifica se trunca igual, con `<key>Total` para no perder el conteo real.
"""
RESOURCE_BLOCKERS_BUDGET_BYTES = 36 * 1024
"""Tope de bytes para una lista `resourceBlockers` ya compactada.

Medido en la instalacion real: un blocker real (con `policyResult` completo: `decisionEngine`,
`roleExecutionPolicy`, `runtimePreflight` con varias listas de candidatos) pesa varios KB incluso
truncado a 1 representante por `reason`; con 12+ blockers en una sola fila, acotar solo las listas
internas no alcanza a bajar de 64 KiB. Un presupuesto de bytes (en vez de un conteo fijo) es la
unica forma de garantizar el limite duro sin importar cuan rico sea cada blocker.
"""
DETAILS_HARD_CAP_BUDGET_BYTES = 49 * 1024
"""Presupuesto final para `details` completo tras las reglas especificas de arriba.

Deja margen dentro del limite duro de 64 KiB por fila para el resto del payload (resumen de
nivel superior, ids, `teamScheduleSummary`). Es la ultima red de seguridad: solo se activa cuando
las reglas especificas (roles, candidatos, listas largas genericas) no alcanzaron a bajar del
presupuesto, medido en filas reales con varios patrones grandes combinados en la misma fila.
"""


def compact_remediation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recorta perfiles de rol y listas de candidatos en cualquier profundidad de `payload.details`.

    No muta ``payload``: los llamadores (``create_action``, el backfill de filas existentes) siguen
    controlando si el resultado reemplaza o no lo persistido. Idempotente: aplicarla dos veces sobre
    su propio resultado no cambia nada mas (un rol ya resumido no tiene mas de 20 candidatos que
    muestrear, y una lista ya muestreada no vuelve a crecer).
    """
    details = payload.get("details")
    if not isinstance(details, dict):
        return payload
    compacted = _compact_tree(details)
    compacted = _hard_cap_if_needed(compacted, DETAILS_HARD_CAP_BUDGET_BYTES)
    return {**payload, "details": compacted}


def _hard_cap_if_needed(value: Any, budget_bytes: int, *, max_steps: int = 40) -> Any:
    """Ultima red de seguridad: poda la rama mas pesada del arbol hasta encajar en el presupuesto.

    Solo actua sobre lo que las reglas especificas de `_compact_tree` no cubrieron (por ejemplo,
    varios patrones grandes distintos combinados en la misma fila). Reduce a la mitad la rama mas
    pesada en cada paso; itera en vez de recursar sobre si misma, asi que nunca falla por limite de
    recursion sin importar cuan profundo este el problema.
    """
    for _ in range(max_steps):
        if len(json_dumps(value).encode()) <= budget_bytes:
            return value
        value = _shrink_heaviest_branch(value)
    return value


def _shrink_heaviest_branch(value: Any) -> Any:
    """Reduce a la mitad la rama mas pesada de un dict/list; strings largos tambien se acortan."""
    if isinstance(value, dict) and value:
        heaviest_key = max(value, key=lambda key: len(json_dumps(value[key]).encode()))
        shrunk = dict(value)
        shrunk[heaviest_key] = _shrink_heaviest_branch(value[heaviest_key])
        return shrunk
    if isinstance(value, list) and len(value) > 1:
        return value[: max(1, len(value) // 2)]
    if isinstance(value, str) and len(value) > 100:
        return value[: len(value) // 2]
    return value


def _compact_tree(value: Any) -> Any:
    """Camina la estructura completa aplicando los tres recortes, en cualquier profundidad."""
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in ROLE_LIST_KEYS and _is_role_like_list(item):
                compacted[key] = [_summarize_team_role(role) for role in item]
            elif key == "resourceBlockers" and isinstance(item, list) and item:
                compacted[key], total = _compact_resource_blockers(item)
                if total > len(compacted[key]):
                    compacted["resourceBlockersTotal"] = total
            elif key in CANDIDATE_LIST_KEYS and isinstance(item, list) and len(item) > MAX_CANDIDATE_ENTRIES:
                sample, total, by_reason = _diverse_candidate_sample(item)
                compacted[key] = _compact_tree(sample)
                compacted[f"{key}Total"] = total
                compacted[f"{key}ByReason"] = by_reason
            elif isinstance(item, list) and len(item) > GENERIC_LIST_LIMIT:
                compacted[key] = _compact_tree(item[:GENERIC_LIST_LIMIT])
                compacted[f"{key}Total"] = len(item)
            else:
                compacted[key] = _compact_tree(item)
        return compacted
    if isinstance(value, list):
        return [_compact_tree(item) for item in value]
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    return value


def _is_role_like_list(value: Any) -> bool:
    """Una lista de roles/asignaciones es una lista de dicts; nunca `scheduledRoles` (solo nombres)."""
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _compact_resource_blockers(blockers: list[Any]) -> tuple[list[Any], int]:
    """Compacta cada blocker y conserva, en su orden original, los que quepan en el presupuesto.

    Cada re-diagnostico de ``payloads.py`` devuelve la PRIMERA decision que reconoce, asi que se
    conserva siempre la primera coincidencia de cada uno (aunque excedan juntas el presupuesto) y
    despues se completa con los fallidos antes que los que tienen runtime elegido. Salir en el orden
    original hace que cada re-diagnostico devuelva exactamente la misma decision que sobre el
    original (medido en las 498 filas reales: priorizar solo "reconocidos por algun predicado" aun
    dejaba fuera los de revision de riesgo detras de seis de restricciones, en 8 filas).
    """
    compacted = [_compact_tree(blocker) for blocker in blockers]
    kept_indexes = set(_first_diagnostic_matches(compacted))
    used_bytes = 2 + sum(len(json_dumps(compacted[index]).encode()) + 1 for index in kept_indexes)
    failed_first = sorted(
        range(len(compacted)), key=lambda index: (_has_selected_runtime(compacted[index]), index)
    )
    for index in failed_first:
        if index in kept_indexes:
            continue
        blocker_bytes = len(json_dumps(compacted[index]).encode()) + 1  # + separador ","
        if kept_indexes and used_bytes + blocker_bytes > RESOURCE_BLOCKERS_BUDGET_BYTES:
            continue
        kept_indexes.add(index)
        used_bytes += blocker_bytes
    return [compacted[index] for index in sorted(kept_indexes)], len(blockers)


def _first_diagnostic_matches(blockers: list[Any]) -> list[int]:
    """Devuelve el indice de la primera coincidencia de cada re-diagnostico, sobre blockers compactados.

    Usa los mismos predicados que el re-diagnostico para no duplicar su logica. Import diferido:
    ``payloads`` no depende de este modulo, pero asi el repositorio no carga el grafo de diagnostico
    al importarse.
    """
    from local_control_center.remediations.payloads import (
        decision_engine_failure,
        resource_selection_constraint_failure,
        runtime_risk_review_failure,
    )

    matches: list[int] = []
    for check in (
        decision_engine_failure,
        runtime_risk_review_failure,
        resource_selection_constraint_failure,
    ):
        index = next(
            (
                index
                for index, blocker in enumerate(blockers)
                if check({"resourceBlockers": [blocker]}) is not None
            ),
            None,
        )
        if index is not None and index not in matches:
            matches.append(index)
    return matches


def _has_selected_runtime(blocker: Any) -> bool:
    decision = blocker.get("decision") if isinstance(blocker, dict) else None
    return not (isinstance(decision, dict) and decision.get("selected") is None)


def _summarize_team_role(role: Any) -> dict[str, str]:
    """Reduce un rol planificado a (rol, runtime/modelo elegido, estado, motivo acotado)."""
    if not isinstance(role, dict):
        return {"role": "", "runtime": "", "status": "", "reason": ""}
    runtime_or_model = (
        role.get("selectedRuntimeId")
        or role.get("runtimeId")
        or role.get("model")
        or role.get("runtime")
        or ""
    )
    status = role.get("status") or role.get("decisionStatus") or ""
    reason = role.get("reason") or role.get("decisionReason") or ""
    return {
        "role": str(role.get("role") or "")[:MAX_STRING_LENGTH],
        "runtime": str(runtime_or_model)[:MAX_STRING_LENGTH],
        "status": str(status)[:MAX_STRING_LENGTH],
        "reason": str(reason)[:MAX_STRING_LENGTH],
    }


def _diverse_candidate_sample(items: list[Any]) -> tuple[list[Any], int, dict[str, int]]:
    """Devuelve (muestra con >=1 entrada por `reason` distinto, total original, conteo por `reason`)."""
    total = len(items)
    by_reason: dict[str, int] = {}
    representative_by_reason: dict[str, Any] = {}
    for item in items:
        reason = str(item.get("reason") or "") if isinstance(item, dict) else ""
        by_reason[reason] = by_reason.get(reason, 0) + 1
        representative_by_reason.setdefault(reason, item)
    sample = list(representative_by_reason.values())[:MAX_CANDIDATE_ENTRIES]
    if len(sample) < MAX_CANDIDATE_ENTRIES:
        sample_ids = {id(entry) for entry in sample}
        for item in items:
            if len(sample) >= MAX_CANDIDATE_ENTRIES:
                break
            if id(item) not in sample_ids:
                sample.append(item)
                sample_ids.add(id(item))
    return sample, total, by_reason
