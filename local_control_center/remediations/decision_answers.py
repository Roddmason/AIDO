"""Validate and normalise operator answers to thread decisions.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

PRODUCT_OWNER_DECISION_SOURCE = "product_owner_agent"
"""`thread_decisions.metadata.source` de las preguntas y decisiones que levanta el ProductOwnerAgent
(agents/product_owner_agent.py::PRODUCT_OWNER_AGENT_ID). Solo estas admiten selección múltiple y texto
libre en `_answer_question`; el resto (similitud, funcionalidad existente, intake) son excluyentes por
semántica y siguen exigiendo una sola opción exacta.
"""


def answer_options(*, decision: dict[str, Any], action_payload: dict[str, Any]) -> list[str]:
    """Return the de-duplicated options offered by a decision."""
    raw_options = decision.get("options") or action_payload.get("options") or []
    return list(dict.fromkeys(str(option).strip() for option in raw_options if str(option).strip()))


def option_key(value: str) -> str:
    """Normalise an option for case/underscore-insensitive comparison."""
    return "_".join(str(value).replace("_", " ").strip().lower().split())


def matched_option(answer: str, options: list[str]) -> str | None:
    """Return the canonical offered option matching ``answer``, if any."""
    if not options:
        return None
    answer_key = option_key(answer)
    return next((option for option in options if option_key(option) == answer_key), None)


def validated_answer(
    *,
    decision: dict[str, Any],
    action_payload: dict[str, Any],
    selected_options: list[str],
    free_text: str,
) -> list[str]:
    """Partes de una respuesta válida (opciones canónicas + texto libre) o ``ValueError`` con el motivo.

    Única definición de qué respuesta acepta una decisión: la usan la resolución real
    (``_answer_question``) y la validación previa del lote, para que un lote no descubra a mitad
    de camino una respuesta que la resolución iba a rechazar. Solo las preguntas del PO admiten
    varias opciones y texto libre; similitud, funcionalidad existente e intake son excluyentes.
    """
    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    allows_free_form = str(metadata.get("source") or "").strip() == PRODUCT_OWNER_DECISION_SOURCE
    options = answer_options(decision=decision, action_payload=action_payload)
    if not allows_free_form and len(selected_options) > 1:
        raise ValueError("This decision accepts a single option.")
    resolved: list[str] = []
    for raw_option in selected_options:
        matched = matched_option(raw_option, options)
        if matched is None and options:
            raise ValueError("Answer must match one of the offered decision options.")
        resolved.append(matched or raw_option)
    if free_text and not allows_free_form and options:
        raise ValueError("Free text is only accepted for Product Owner questions and decisions.")
    combined = list(dict.fromkeys(resolved))
    if free_text:
        combined.append(free_text)
    if not combined:
        raise ValueError("answer, selectedOptions or freeText is required.")
    return combined
