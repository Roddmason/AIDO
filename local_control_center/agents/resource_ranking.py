"""Pure ranking helpers that order AI resource candidates by provider order, preferred resources and score.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

from local_control_center.agents.model_wildcards import MODEL_WILDCARDS


def provider_preference(provider_ids: list[str]) -> list[str]:
    """Return the ordered provider preference list without duplicates."""
    ordered: list[str] = []
    for provider_id in provider_ids:
        normalized = str(provider_id).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def normalized_preferred_resources(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Sanea las entradas de preferencia del rol conservando su orden (orden = prioridad)."""
    normalized: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider") or "").strip()
        if not provider_id:
            continue
        normalized.append({"provider": provider_id, "model": str(item.get("model") or "").strip()})
    return normalized


def preferred_resource_rank(
    candidate: dict[str, Any],
    preferred_resources: list[dict[str, str]],
) -> tuple[int, int]:
    """Rank ``(tier, index)`` del candidato contra las preferencias del rol; menor gana.

    ``tier`` es 0 para un match de modelo específico, 1 para un comodín provider-level
    (modelo vacío/"*"/"auto", mismo comodín que ``blocked`` y ``preferred_json`` legacy) y
    2 si ninguna preferencia matchea. Un match específico rankea antes que cualquier comodín
    aunque el comodín esté antes en la lista: sin esto, el comodín del ``runtime_order``
    persistido eclipsaba la preferencia específica del rol solo por su posición. ``index`` es
    el de la primera entrada del mismo tier, así que el orden de la lista sigue desempatando
    entre preferencias del mismo tipo.
    """
    candidate_provider = str(candidate.get("providerId") or "")
    candidate_model = str(candidate.get("model") or "")
    best: tuple[int, int] = (2, len(preferred_resources))
    for index, entry in enumerate(preferred_resources):
        if entry["provider"] != candidate_provider:
            continue
        if entry["model"] in MODEL_WILDCARDS:
            tier = 1
        elif entry["model"] == candidate_model:
            tier = 0
        else:
            continue
        best = min(best, (tier, index))
    return best


def selection_sort_key(
    candidate: dict[str, Any],
    provider_preference: list[str],
    preferred_resources: list[dict[str, str]],
    provider_order: list[str] | None = None,
) -> tuple[int, tuple[int, int], float, int, str, str, str]:
    """Return the deterministic sort key used to rank selectable candidates."""
    provider_id = str(candidate.get("providerId") or "")
    try:
        preference_rank = provider_preference.index(provider_id)
    except ValueError:
        preference_rank = len(provider_preference)
    order = provider_order or []
    order_rank = order.index(provider_id) if provider_id in order else len(order)
    return (
        order_rank,
        preferred_resource_rank(candidate, preferred_resources),
        -float(candidate["score"]),
        preference_rank,
        provider_id,
        str(candidate.get("model") or ""),
        str(candidate.get("runtime") or ""),
    )
