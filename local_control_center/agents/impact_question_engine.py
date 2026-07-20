"""Motor de preguntas por impacto: valida, prioriza y agrupa preguntas de descubrimiento.

Cada pregunta declara category, question, whyItMatters, blocking, options, recommendation,
defaultDecision y confidence. El motor valida ese contrato estricto, descarta lo ya detectado en el
repositorio (para no repreguntar), ordena por impacto (bloqueo > menor confianza > peso de categoría)
y agrupa como máximo cinco preguntas por turno; el resto queda diferido con su decisión por defecto
para que el flujo pueda avanzar sin bloquearse.

@author Rodrigo Mason
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

QUESTION_CATEGORIES = {
    "scope",
    "users",
    "data",
    "integration",
    "compliance",
    "nonfunctional",
    "ux",
    "risk",
    "delivery",
}

logger = logging.getLogger(__name__)

DEFAULT_QUESTION_CATEGORY = "scope"
CATEGORY_ALIASES = {
    # nonfunctional
    "performance": "nonfunctional",
    "scalability": "nonfunctional",
    "reliability": "nonfunctional",
    "availability": "nonfunctional",
    "latency": "nonfunctional",
    "throughput": "nonfunctional",
    "maintainability": "nonfunctional",
    "observability": "nonfunctional",
    "quality": "nonfunctional",
    "technical": "nonfunctional",
    "architecture": "nonfunctional",
    "infrastructure": "nonfunctional",
    "operational": "nonfunctional",
    "ops": "nonfunctional",
    "non-functional": "nonfunctional",
    "non functional": "nonfunctional",
    "nfr": "nonfunctional",
    "resilience": "nonfunctional",
    "capacity": "nonfunctional",
    # ux
    "usability": "ux",
    "design": "ux",
    "ui": "ux",
    "user experience": "ux",
    "user-experience": "ux",
    "accessibility": "ux",
    "a11y": "ux",
    "interaction": "ux",
    # scope
    "functional": "scope",
    "feature": "scope",
    "features": "scope",
    "functionality": "scope",
    "mvp": "scope",
    "requirement": "scope",
    "requirements": "scope",
    "boundaries": "scope",
    # delivery
    "timeline": "delivery",
    "schedule": "delivery",
    "cost": "delivery",
    "budget": "delivery",
    "resourcing": "delivery",
    "rollout": "delivery",
    "release": "delivery",
    "deployment": "delivery",
    "milestone": "delivery",
    "milestones": "delivery",
    "planning": "delivery",
    "roadmap": "delivery",
    "effort": "delivery",
    "estimate": "delivery",
    "estimation": "delivery",
    # compliance
    "legal": "compliance",
    "regulatory": "compliance",
    "regulation": "compliance",
    "regulations": "compliance",
    "privacy": "compliance",
    "gdpr": "compliance",
    "hipaa": "compliance",
    "data privacy": "compliance",
    "data protection": "compliance",
    "governance": "compliance",
    "policy": "compliance",
    "licensing": "compliance",
    # risk
    "security": "risk",
    "threat": "risk",
    "vulnerability": "risk",
    "safety": "risk",
    "business risk": "risk",
    "dependency": "risk",
    "dependencies": "risk",
    "uncertainty": "risk",
    # data
    "database": "data",
    "storage": "data",
    "schema": "data",
    "data model": "data",
    "data modeling": "data",
    "analytics": "data",
    "data retention": "data",
    "persistence": "data",
    "migration": "data",
    # integration
    "api": "integration",
    "apis": "integration",
    "third party": "integration",
    "third-party": "integration",
    "interoperability": "integration",
    "integrations": "integration",
    "external": "integration",
    "webhook": "integration",
    "webhooks": "integration",
    "connectivity": "integration",
    "interface": "integration",
    # users
    "user": "users",
    "persona": "users",
    "personas": "users",
    "audience": "users",
    "stakeholder": "users",
    "stakeholders": "users",
    "target users": "users",
    "customer": "users",
    "customers": "users",
    "roles": "users",
}


def normalize_category(raw: Any) -> tuple[str, str | None]:
    """Coerce una categoría de pregunta al valor canónico del catálogo.

    Returns:
        ``(canonical, coerced_from)`` donde ``coerced_from`` es ``None`` si el valor ya era
        canónico, o el valor original (en minúsculas) cuando se mapeó vía alias o cayó al
        default ``DEFAULT_QUESTION_CATEGORY`` por ser desconocido.
    """
    value = str(raw or "").strip().lower()
    if value in QUESTION_CATEGORIES:
        return value, None
    mapped = CATEGORY_ALIASES.get(value)
    if mapped is not None:
        return mapped, value
    return DEFAULT_QUESTION_CATEGORY, value


CONFIDENCES = {"low", "medium", "high"}
MAX_QUESTIONS_PER_TURN = 5
MIN_OPTIONS = 2
BLOCKING_IMPACT = 1000
COVERED_CATEGORY_PENALTY = 500
CONFIDENCE_IMPACT = {"low": 100, "medium": 50, "high": 20}
CATEGORY_IMPACT = {
    "compliance": 50,
    "data": 45,
    "risk": 45,
    "integration": 35,
    "scope": 30,
    "users": 25,
    "nonfunctional": 20,
    "delivery": 15,
    "ux": 10,
}
BRIEF_FIELD_CATEGORY = {"targetUsers": "users", "scope": "scope", "successMetrics": "delivery"}


class ImpactQuestionValidationError(ValueError):
    """Se lanza cuando una pregunta no cumple el contrato del motor de preguntas por impacto."""


def _required_text(item: dict[str, Any], key: str, *, index: int) -> str:
    raw = item.get(key)
    value = "" if raw is None else str(raw).strip()
    if not value:
        raise ImpactQuestionValidationError(f"questions[{index}].{key} is required.")
    return value


# Guillemets and curly quotes, escaped so the source stays free of ambiguous glyphs.
_QUOTE_CHARS = "\u00ab\u00bb\u201c\u201d\u2018\u2019"


def _option_key(text: str) -> str:
    """Normaliza una opción para comparar intención, no bytes: enumeración, comillas, punto final y caja."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    normalized = re.sub(r"^\s*(?:[-*•]|\(?\d+[.)])\s*", "", normalized)
    normalized = normalized.strip("\"'" + _QUOTE_CHARS).strip()
    normalized = normalized.rstrip(".").strip()
    return re.sub(r"\s+", " ", normalized).casefold()


def _truncation_matches(target: str, options: list[str]) -> list[str]:
    """Opciones de las que ``target`` es una truncación en frontera de palabra.

    El modelo tiende a citar la opción por su encabezado ("Inspect codebase" por "Inspect codebase for
    frontend tech"). Solo se acepta esa dirección: que la opción *empiece* con el valor emitido. La
    inversa ("Web and Mobile" por "Web") no es una truncación sino una ampliación que cambiaría la
    decisión, así que no se contempla. La frontera de palabra evita que un corte a mitad de token
    ("Insp") identifique una opción por accidente.
    """
    if not target:
        return []
    return [
        option
        for option in options
        if (key := _option_key(option)) != target and key.startswith(target) and key[len(target)] == " "
    ]


def _resolve_option(value: str, options: list[str], *, index: int, field: str) -> str:
    """Ancla ``value`` a la opción canónica equivalente; falla si no hay exactamente una coincidencia.

    El modelo suele emitir la opción con otra caja, un prefijo de enumeración o un punto final, o bien
    la acorta a su encabezado. Ni la variación tipográfica ni la truncación son errores de producto,
    así que ambas se coercen a la opción canónica (mismo criterio tolerante que ``normalize_category``);
    la equivalencia exacta tiene precedencia sobre la truncación. Una opción inventada o ambigua sigue
    fallando: el valor persistido debe ser miembro exacto de ``options``.

    Raises:
        ImpactQuestionValidationError: si ninguna opción equivale a ``value`` o si más de una lo hace.
    """
    if value in options:
        return value
    target = _option_key(value)
    matches = [option for option in options if _option_key(option) == target]
    variation = "typographic variation"
    if not matches:
        matches = _truncation_matches(target, options)
        variation = "truncated to its leading words"
    if len(matches) == 1:
        logger.warning(
            "questions[%s].%s %r coerced to option %r (%s).",
            index,
            field,
            value,
            matches[0],
            variation,
        )
        return matches[0]
    raise ImpactQuestionValidationError(
        f"questions[{index}].{field} must be one of options; got {value!r}, options are {options!r}."
    )


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))[:80]


def question_dedup_key(question: dict[str, Any]) -> str:
    """Clave determinista (category + slug del texto) para detectar preguntas repetidas o ya resueltas."""
    return f"{question.get('category')}:{_slug(question.get('question', ''))}"


def impact_score(question: dict[str, Any], *, category_covered: bool = False) -> int:
    """Puntúa el impacto: el bloqueo domina, luego la menor confianza y el peso de la categoría.

    ``category_covered`` degrada una pregunta cuya categoría ya está cubierta por el brief. La
    penalización es menor que ``BLOCKING_IMPACT`` a propósito: una bloqueante en categoría cubierta
    debe seguir ganándole a cualquier no bloqueante.
    """
    blocking = BLOCKING_IMPACT if question.get("blocking") else 0
    confidence = CONFIDENCE_IMPACT.get(str(question.get("confidence")), CONFIDENCE_IMPACT["high"])
    category = CATEGORY_IMPACT.get(str(question.get("category")), 10)
    covered = COVERED_CATEGORY_PENALTY if category_covered else 0
    return blocking + confidence + category - covered


def validate_impact_question(item: Any, *, index: int = 0) -> dict[str, Any]:
    """Valida y normaliza una pregunta contra el contrato de ocho campos del motor.

    ``category`` es tolerante: sinónimos conocidos se coercen al catálogo canónico y valores
    desconocidos caen a ``DEFAULT_QUESTION_CATEGORY`` con auditoría en log (la categoría solo
    afecta priorización/dedup, no la corrección del brief).

    ``recommendation`` y ``defaultDecision`` toleran variación tipográfica (caja, prefijo de
    enumeración, comillas, punto final) y se anclan a la opción canónica equivalente; el valor
    devuelto siempre es miembro exacto de ``options``. Una opción inventada o ambigua sí falla.

    Raises:
        ImpactQuestionValidationError: si falta un campo, un tipo no coincide o un enum es inválido
            (confidence fuera de catálogo, options con menos de dos opciones, o
            recommendation/defaultDecision sin equivalencia unívoca entre las options).
    """
    if not isinstance(item, dict):
        raise ImpactQuestionValidationError(f"questions[{index}] must be an object.")
    category, coerced_from = normalize_category(item.get("category"))
    if coerced_from is not None:
        logger.warning(
            "questions[%s].category %r coerced to %r (category only affects ranking/dedup).",
            index,
            coerced_from,
            category,
        )
    confidence = str(item.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCES:
        raise ImpactQuestionValidationError(
            f"questions[{index}].confidence must be one of {sorted(CONFIDENCES)}."
        )
    blocking = item.get("blocking")
    if not isinstance(blocking, bool):
        raise ImpactQuestionValidationError(f"questions[{index}].blocking must be a boolean.")
    options = item.get("options")
    if not isinstance(options, list):
        raise ImpactQuestionValidationError(f"questions[{index}].options must be a list.")
    normalized_options = [
        str(option).strip() for option in options if isinstance(option, str) and option.strip()
    ]
    if len(normalized_options) < MIN_OPTIONS or len(normalized_options) != len(options):
        raise ImpactQuestionValidationError(
            f"questions[{index}].options must list at least {MIN_OPTIONS} non-empty strings."
        )
    recommendation = _resolve_option(
        _required_text(item, "recommendation", index=index),
        normalized_options,
        index=index,
        field="recommendation",
    )
    default_decision = _resolve_option(
        _required_text(item, "defaultDecision", index=index),
        normalized_options,
        index=index,
        field="defaultDecision",
    )
    return {
        "category": category,
        "question": _required_text(item, "question", index=index),
        "whyItMatters": _required_text(item, "whyItMatters", index=index),
        "blocking": blocking,
        "options": normalized_options,
        "recommendation": recommendation,
        "defaultDecision": default_decision,
        "confidence": confidence,
    }


def detected_facts_from_assessment(
    *, brief: dict[str, Any] | None = None, existing_questions: list[dict[str, Any]] | None = None
) -> set[str]:
    """Reúne lo ya detectado en el repositorio: claves de preguntas previas y categorías cubiertas por el brief.

    Un campo poblado del brief (targetUsers/scope/successMetrics) marca su categoría como detectada;
    cada pregunta existente aporta su clave puntual para no repetir exactamente la misma pregunta.
    """
    facts: set[str] = set()
    for field, category in BRIEF_FIELD_CATEGORY.items():
        if (brief or {}).get(field):
            facts.add(category)
    for question in existing_questions or []:
        category = str((question.get("metadata") or {}).get("category") or question.get("category") or "")
        facts.add(f"{category}:{_slug(question.get('question', ''))}")
    return facts


class ImpactQuestionEngine:
    """Agrupa preguntas por impacto: valida, descarta lo ya detectado y limita a cinco por turno."""

    def __init__(self, *, max_per_turn: int = MAX_QUESTIONS_PER_TURN):
        self.max_per_turn = max_per_turn

    def select(self, candidates: list[Any], *, detected_facts: set[str] | None = None) -> dict[str, Any]:
        """Valida, descarta lo ya detectado, ordena por impacto y separa el turno (≤max) de lo diferido.

        Una candidata se descarta si su clave puntual ya está en ``detected_facts``. Que su *categoría*
        esté cubierta por el brief no es autoridad suficiente para descartar una bloqueante —hacerlo
        vaciaba el turno y dejaba el loop en "waiting decision" sin nada que responder—, así que en ese
        caso solo se degrada su prioridad. Las diferidas conservan su ``defaultDecision`` para poder
        avanzar sin haberlas preguntado.

        Raises:
            ImpactQuestionValidationError: si alguna candidata no cumple el contrato de ocho campos.
        """
        detected = detected_facts or set()
        validated = [validate_impact_question(item, index=index) for index, item in enumerate(candidates)]
        kept: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        for question in validated:
            covered_category = question["category"] in detected
            if question_dedup_key(question) in detected or (covered_category and not question["blocking"]):
                suppressed.append(question)
            else:
                kept.append(question)
        ranked = sorted(
            kept,
            key=lambda question: -impact_score(question, category_covered=question["category"] in detected),
        )
        turn = ranked[: self.max_per_turn]
        deferred = ranked[self.max_per_turn :]
        return {
            "turn": turn,
            "deferred": deferred,
            "suppressed": suppressed,
            "counts": {
                "candidates": len(validated),
                "asked": len(turn),
                "deferred": len(deferred),
                "suppressed": len(suppressed),
            },
        }
