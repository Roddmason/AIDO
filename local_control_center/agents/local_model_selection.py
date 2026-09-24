"""Selección determinista del modelo de una cuenta local para un rol (función pura).

Un servidor local sirve varios modelos pero debe aportar un solo candidato por cuenta, para que Jev no
reparta la probabilidad entre modelos del mismo servidor. ``E`` son los modelos habilitados, validados
y con las capacidades del rol. Orden: un modelo de ``E`` ya cargado (desempate por defecto,
``operator_order`` e id) → la afinidad de otro rol del mismo run → el por defecto → el primero por
``operator_order``/id. ``E`` vacío bloquea con ``local_model_not_validated`` sin fallback silencioso; un
estado de carga desconocido nunca provoca un cambio de modelo. Los requisitos del rol (``code``,
``review``, ``issue_to_patch``) y las capacidades ofrecidas se comparan en un solo vocabulario canónico
(``normalize_model_capabilities``: ``code_edit``/``code_review``).

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from local_control_center.agents.local_model_settings import LocalModelSetting, setting_capabilities
from local_control_center.agents.local_model_state import LoadState
from local_control_center.agents.local_runtime_causes import LocalRuntimeCause

SelectionReason = Literal["loaded", "affinity", "default", "operator_order"]
SWITCHING_STATES = frozenset({"unloaded", "loading"})
CAPABILITY_ALIASES: Mapping[str, str] = {
    "code": "code_edit",
    "issue_to_patch": "code_edit",
    "review": "code_review",
}
"""Nombres de rol y del scheduler que equivalen a una capacidad opt-in del modelo (P22)."""


@dataclass(frozen=True)
class LocalModelResolution:
    """Modelo elegido para el rol y si la llamada exige que el servidor cambie el modelo cargado."""

    model: str | None
    requires_switch: bool
    from_model: str | None
    reason: SelectionReason | None
    blocked_cause: LocalRuntimeCause | None


def normalize_model_capabilities(capabilities: Iterable[str]) -> frozenset[str]:
    """Lleva capacidades del rol, del scheduler o del modelo al vocabulario canónico del modelo.

    ``code_edit``, ``code`` e ``issue_to_patch`` ⇒ ``code_edit``; ``code_review`` y ``review`` ⇒
    ``code_review``; ``chat`` y el resto se conservan en minúsculas y se descartan los vacíos.
    """
    names = (str(item).strip().lower() for item in capabilities)
    return frozenset(CAPABILITY_ALIASES.get(name, name) for name in names if name)


def resolve_local_model(
    *,
    required_capabilities: frozenset[str],
    enabled_models: Sequence[str],
    settings: Sequence[LocalModelSetting],
    validated_models: frozenset[str],
    load_states: Mapping[str, LoadState],
    affinity_model: str | None = None,
    model_capabilities: Mapping[str, frozenset[str]] | None = None,
    model_aliases: Mapping[str, str] | None = None,
) -> LocalModelResolution:
    """Resuelve el modelo del rol; ``model_capabilities`` reemplaza las capacidades de ``settings``.

    Los requisitos y las capacidades ofrecidas se comparan tras ``normalize_model_capabilities``. Con
    ``model_aliases`` (alias → id canónico) la configuración o la afinidad guardadas bajo un alias se aplican a
    su id canónico si este no tiene una propia; el elegido es siempre un id de ``enabled_models``.
    """
    aliases = dict(model_aliases or {})
    by_model = {setting.model: setting for setting in settings}
    for setting in settings:
        canonical = aliases.get(setting.model)
        if canonical and canonical not in by_model:
            by_model[canonical] = setting
    affinity = aliases.get(affinity_model, affinity_model) if affinity_model else None
    required = normalize_model_capabilities(required_capabilities)

    def capabilities(model: str) -> frozenset[str]:
        if model_capabilities is not None and model in model_capabilities:
            return normalize_model_capabilities(model_capabilities[model])
        return normalize_model_capabilities(setting_capabilities(by_model.get(model)))

    def order(model: str) -> int:
        setting = by_model.get(model)
        return setting.operator_order if setting is not None else 0

    def is_default(model: str) -> bool:
        setting = by_model.get(model)
        return bool(setting is not None and setting.is_default)

    eligible = [
        model
        for model in dict.fromkeys(enabled_models)
        if model in validated_models and required <= capabilities(model)
    ]
    if not eligible:
        return LocalModelResolution(None, False, None, None, "local_model_not_validated")
    loaded = sorted(
        (model for model in eligible if load_states.get(model) == "loaded"),
        key=lambda model: (not is_default(model), order(model), model),
    )
    if loaded:
        return LocalModelResolution(loaded[0], False, None, "loaded", None)
    if affinity in eligible:
        chosen, reason = str(affinity), "affinity"
    elif any(is_default(model) for model in eligible):
        chosen, reason = next(model for model in eligible if is_default(model)), "default"
    else:
        chosen, reason = min(eligible, key=lambda model: (order(model), model)), "operator_order"
    requires_switch = load_states.get(chosen) in SWITCHING_STATES
    displaced = sorted(
        model for model, state in load_states.items() if state == "loaded" and model not in aliases
    )
    from_model = displaced[0] if requires_switch and displaced else None
    return LocalModelResolution(chosen, requires_switch, from_model, reason, None)
