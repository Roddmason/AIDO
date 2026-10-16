"""Pure rejection rules that explain why an AI resource candidate is not executable or allowed for a request.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.agents.model_wildcards import is_nvidia_nim_auto_selection_sentinel

if TYPE_CHECKING:
    from local_control_center.agents.ai_resource_manager import AIResourceRequest


def runtime_executable_reject_reason(
    *,
    model: dict[str, Any],
    runtime_statuses: dict[str, dict[str, Any]],
    request: AIResourceRequest,
) -> str | None:
    """Return why a model has no executable runtime for the request, or None."""
    provider_id = str(model.get("providerId") or "").strip()
    status = runtime_statuses.get(provider_id)
    if status is None:
        return f"runtime_not_executable: Provider {provider_id} is not configured in runtime status."
    if status.get("enabled") is False:
        return f"provider_disabled: Provider {provider_id} is switched off by the operator."
    if is_nvidia_nim_auto_selection_sentinel(
        str(status.get("providerFamily") or "").strip(),
        str(model.get("model") or "").strip(),
    ):
        return "nvidia_model_selection_required"
    if status.get("executable") is not True:
        reason = str(status.get("reason") or "").strip()
        detail = reason or f"Provider {provider_id} is not executable."
        return f"runtime_not_executable: {detail}"
    if request.agent_id == "product_owner_agent" and status.get("productOwnerExecutable") is False:
        return "runtime_not_executable: ProductOwnerAgent-specific execution contract is not satisfied."
    advertised_models = status.get("models")
    if isinstance(advertised_models, list) and advertised_models:
        model_id = str(model.get("model") or "").strip()
        available_models = {str(item).strip() for item in advertised_models if str(item).strip()}
        if model_id and model_id not in available_models:
            return (
                "runtime_model_not_available: "
                f"Provider {provider_id} does not currently report model {model_id}."
            )
    return None


def role_execution_policy_reject_reason(
    *,
    model: dict[str, Any],
    request: AIResourceRequest,
    runtime_status: dict[str, Any] | None,
) -> str | None:
    """Return why the role execution policy rejects a model, or None."""
    runtime_kind = str((runtime_status or {}).get("kind") or model.get("runtime") or "").lower()
    if runtime_kind == "cli":
        return None if request.allow_cli else "role_blocks_cli"
    if runtime_kind in {"api", "gateway"} and not request.allow_api:
        return "role_blocks_api"
    locality = str(model.get("locality") or "remote").lower()
    if locality == "local":
        return None if request.allow_local else "role_blocks_local"
    return None if request.allow_remote else "role_blocks_remote"


def blocked_by_role_policy(
    model: dict[str, Any],
    blocked_resources: list[dict[str, Any]],
) -> bool:
    """Return whether the role policy blocks the provider and model pair."""
    for item in blocked_resources:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("provider")
        model_id = item.get("model")
        if provider_id not in {None, "*", model["providerId"]}:
            continue
        if model_id not in {None, "*", "auto", model["model"]}:
            continue
        return True
    return False


def jev_confidence_blocked_reason(
    *,
    candidates: list[dict[str, Any]],
    measure: str,
    value: float | None,
    threshold: float,
    task_type: str,
) -> str:
    """La verdad del bloqueo por confianza o margen de Jev bajo el umbral: ninguna validación falló.

    Sin equipo o sin runtime propio para el rol, ninguna heurística determinista reemplaza a
    Jev cuando no está seguro (por diseño); asignarle un runtime al rol es lo único que decide.
    """
    value_text = f"{value:.2f}" if value is not None else "unavailable"
    return (
        f"Jev could not confidently rank {len(candidates)} AIDO-validated candidates for "
        f"{task_type} ({measure} {value_text} below the {threshold:.2f} required threshold). "
        "This is not a validation or runtime-health problem: assign a runtime to this role in the "
        "thread's AI team so the operator's choice, not Jev's uncertainty, decides."
    )


def rejection_summary(rejected: list[dict[str, Any]]) -> str:
    """Resume los descartes por categoría para que el motivo de bloqueo sea diagnosticable solo.

    Agrupa por el token previo a ``:`` porque varias razones llevan detalle por candidato
    (``runtime_not_executable: ...``, ``missing_capabilities:code``) y el operador necesita la
    categoría agregada, no ocho variantes del mismo descarte. Orden: frecuencia descendente y
    alfabético como desempate, para que el texto sea determinista y auditable.
    """
    counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("reason") or "").strip()
        if not reason:
            continue
        category = reason.split(":", 1)[0].strip()
        counts[category] = counts.get(category, 0) + 1
    if not counts:
        return ""
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))
    breakdown = ", ".join(f"{count} {category}" for category, count in ordered)
    noun = "candidate" if total == 1 else "candidates"
    return f"{total} {noun} rejected: {breakdown}."
