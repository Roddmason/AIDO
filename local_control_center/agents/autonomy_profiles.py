"""Perfiles de autonomía: deciden cuándo el sistema actúa solo, recomienda o pregunta.

Define tres niveles (guided/recommended/autonomous) con overrides por categoría de decisión
(product, architecture, technology, infrastructure, security, budget, release) y resuelve, para cada
decisión, una acción (auto/recommend/ask) según nivel efectivo, bloqueo, confianza y reversibilidad.
Toda decisión automática produce un registro auditable con sus alternativas, razón, confianza y
reversibilidad, de modo que nada irreversible o de baja confianza se ejecuta en silencio.
"""

from __future__ import annotations

from typing import Any

AUTONOMY_LEVELS = {"guided", "recommended", "autonomous"}
DECISION_CATEGORIES = {
    "product",
    "architecture",
    "technology",
    "infrastructure",
    "security",
    "budget",
    "release",
}
CONFIDENCES = {"low", "medium", "high"}
REVERSIBILITIES = {"reversible", "recoverable", "irreversible"}

ACTION_AUTO = "auto"
ACTION_RECOMMEND = "recommend"
ACTION_ASK = "ask"

MIN_OPTIONS = 2


class AutonomyValidationError(ValueError):
    """Se lanza cuando un perfil o una decisión no cumplen el contrato de los perfiles de autonomía."""


class AutonomyProfile:
    """Perfil de autonomía: un nivel base más overrides por categoría de decisión.

    El nivel efectivo de una categoría es su override si existe, o el nivel base en su defecto.
    """

    def __init__(self, *, level: str = "guided", overrides: dict[str, str] | None = None):
        normalized_level = str(level or "guided").strip().lower()
        if normalized_level not in AUTONOMY_LEVELS:
            raise AutonomyValidationError(f"Autonomy level must be one of {sorted(AUTONOMY_LEVELS)}.")
        normalized_overrides: dict[str, str] = {}
        for category, override_level in (overrides or {}).items():
            category_key = str(category).strip().lower()
            if category_key not in DECISION_CATEGORIES:
                raise AutonomyValidationError(
                    f"Autonomy override category must be one of {sorted(DECISION_CATEGORIES)}."
                )
            override_value = str(override_level or "").strip().lower()
            if override_value not in AUTONOMY_LEVELS:
                raise AutonomyValidationError(
                    f"Autonomy override level must be one of {sorted(AUTONOMY_LEVELS)}."
                )
            normalized_overrides[category_key] = override_value
        self.level = normalized_level
        self.overrides = normalized_overrides

    @classmethod
    def from_dict(cls, data: Any) -> AutonomyProfile:
        """Crea un perfil desde un dict ``{level, overrides}``; tolera ``None`` como perfil guided.

        Raises:
            AutonomyValidationError: si el dato no es objeto, o el nivel/override no están catalogados.
        """
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise AutonomyValidationError("Autonomy profile must be an object.")
        return cls(level=str(data.get("level") or "guided"), overrides=data.get("overrides") or {})

    def effective_level(self, category: str) -> str:
        """Devuelve el nivel efectivo de una categoría (su override, o el nivel base).

        Raises:
            AutonomyValidationError: si la categoría no está catalogada.
        """
        category_key = str(category).strip().lower()
        if category_key not in DECISION_CATEGORIES:
            raise AutonomyValidationError(f"Decision category must be one of {sorted(DECISION_CATEGORIES)}.")
        return self.overrides.get(category_key, self.level)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el perfil a ``{level, overrides}`` para respuestas y auditoría."""
        return {"level": self.level, "overrides": dict(self.overrides)}


def validate_decision(decision: Any, *, index: int = 0) -> dict[str, Any]:
    """Valida y normaliza una decisión candidata para la resolución de autonomía.

    Raises:
        AutonomyValidationError: si falta un campo, un enum es inválido, hay menos de dos opciones, o
            la opción elegida (``chosen``) no figura entre las ``options``.
    """
    if not isinstance(decision, dict):
        raise AutonomyValidationError(f"decisions[{index}] must be an object.")
    category = str(decision.get("category") or "").strip().lower()
    if category not in DECISION_CATEGORIES:
        raise AutonomyValidationError(
            f"decisions[{index}].category must be one of {sorted(DECISION_CATEGORIES)}."
        )
    confidence = str(decision.get("confidence") or "").strip().lower()
    if confidence not in CONFIDENCES:
        raise AutonomyValidationError(f"decisions[{index}].confidence must be one of {sorted(CONFIDENCES)}.")
    reversibility = str(decision.get("reversibility") or "").strip().lower()
    if reversibility not in REVERSIBILITIES:
        raise AutonomyValidationError(
            f"decisions[{index}].reversibility must be one of {sorted(REVERSIBILITIES)}."
        )
    blocking = decision.get("blocking", False)
    if not isinstance(blocking, bool):
        raise AutonomyValidationError(f"decisions[{index}].blocking must be a boolean.")
    options = decision.get("options")
    if not isinstance(options, list):
        raise AutonomyValidationError(f"decisions[{index}].options must be a list.")
    normalized_options = [
        str(option).strip() for option in options if isinstance(option, str) and option.strip()
    ]
    if len(normalized_options) < MIN_OPTIONS or len(normalized_options) != len(options):
        raise AutonomyValidationError(
            f"decisions[{index}].options must list at least {MIN_OPTIONS} non-empty strings."
        )
    title = str(decision.get("decision") or decision.get("title") or "").strip()
    if not title:
        raise AutonomyValidationError(f"decisions[{index}].decision is required.")
    chosen = str(decision.get("chosen") or "").strip()
    if not chosen:
        raise AutonomyValidationError(f"decisions[{index}].chosen is required.")
    if chosen not in normalized_options:
        raise AutonomyValidationError(f"decisions[{index}].chosen must be one of options.")
    reason = str(decision.get("reason") or "").strip()
    if not reason:
        raise AutonomyValidationError(f"decisions[{index}].reason is required.")
    return {
        "category": category,
        "decision": title,
        "options": normalized_options,
        "chosen": chosen,
        "reason": reason,
        "confidence": confidence,
        "reversibility": reversibility,
        "blocking": blocking,
    }


def resolve_action(level: str, *, blocking: bool, confidence: str, reversibility: str) -> str:
    """Resuelve la acción (auto/recommend/ask) a partir del nivel, bloqueo, confianza y reversibilidad.

    Nunca devuelve ``auto`` para algo irreversible o de baja confianza; lo blinda escalando a ``ask``
    (o ``recommend`` cuando no bloquea), de modo que la autonomía no ejecute decisiones de alto riesgo.
    """
    if level == "guided":
        return ACTION_ASK
    if level == "recommended":
        if reversibility == "reversible" and confidence == "high" and not blocking:
            return ACTION_AUTO
        return ACTION_ASK if blocking else ACTION_RECOMMEND
    # autonomous
    if reversibility == "irreversible":
        return ACTION_ASK
    if confidence == "low":
        return ACTION_ASK if blocking else ACTION_RECOMMEND
    return ACTION_AUTO


def decision_record(decision: dict[str, Any], *, level: str, action: str) -> dict[str, Any]:
    """Construye el registro auditable de una decisión: alternativas, razón, confianza y reversibilidad.

    Las ``alternatives`` son las opciones descartadas (los caminos no elegidos). El registro marca
    ``automatic`` solo cuando la acción resuelta es ``auto``.
    """
    return {
        "category": decision["category"],
        "decision": decision["decision"],
        "chosen": decision["chosen"],
        "alternatives": [option for option in decision["options"] if option != decision["chosen"]],
        "reason": decision["reason"],
        "confidence": decision["confidence"],
        "reversibility": decision["reversibility"],
        "blocking": decision["blocking"],
        "autonomyLevel": level,
        "action": action,
        "automatic": action == ACTION_AUTO,
    }


class AutonomyEngine:
    """Resuelve decisiones según un perfil de autonomía y emite el registro auditable de cada una."""

    def __init__(self, profile: AutonomyProfile):
        self.profile = profile

    def resolve(self, decision: Any, *, index: int = 0) -> dict[str, Any]:
        """Valida una decisión, resuelve su acción según el nivel efectivo y devuelve su registro.

        Raises:
            AutonomyValidationError: si la decisión no cumple el contrato de validación.
        """
        normalized = validate_decision(decision, index=index)
        level = self.profile.effective_level(normalized["category"])
        action = resolve_action(
            level,
            blocking=normalized["blocking"],
            confidence=normalized["confidence"],
            reversibility=normalized["reversibility"],
        )
        return {
            "action": action,
            "effectiveLevel": level,
            "decision": decision_record(normalized, level=level, action=action),
        }

    def resolve_all(self, decisions: list[Any]) -> dict[str, Any]:
        """Resuelve un lote y lo separa en automáticas, recomendadas y escaladas (a preguntar).

        Raises:
            AutonomyValidationError: si alguna decisión del lote no cumple el contrato.
        """
        automatic: list[dict[str, Any]] = []
        recommended: list[dict[str, Any]] = []
        escalated: list[dict[str, Any]] = []
        for index, decision in enumerate(decisions):
            resolution = self.resolve(decision, index=index)
            if resolution["action"] == ACTION_AUTO:
                automatic.append(resolution["decision"])
            elif resolution["action"] == ACTION_RECOMMEND:
                recommended.append(resolution["decision"])
            else:
                escalated.append(resolution["decision"])
        return {
            "profile": self.profile.to_dict(),
            "automatic": automatic,
            "recommended": recommended,
            "escalated": escalated,
            "counts": {
                "automatic": len(automatic),
                "recommended": len(recommended),
                "escalated": len(escalated),
            },
        }
