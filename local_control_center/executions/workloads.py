"""Clasificación de operaciones desde cuentas persistidas antes de reclamar recursos.

@author Rodrigo Mason
"""

from __future__ import annotations

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.runtime_readiness import provider_workload_class

INFERENCE_OPERATIONS = frozenset(
    {
        "models.execute_provider_embedding",
        "models.execute_provider_rerank",
        "models.execute_provider_image_generation",
        "models.execute_provider_image_editing",
        "models.execute_ai_execution",
        "models.test_prompt",
        "models.validate_runtime",
        "local_endpoints.validate_model",
        "models.route_execute",
    }
)


def operation_workload(connection, spec, payload):
    """Reserva el perfil más restrictivo posible sin confiar en flags de recursos del cliente.

    Una operación de inferencia contra un runtime local residente reserva `local_model_call` (liviano, sujeto al
    conflicto con Unreal); una operación de agente conserva su sobre declarado, que cubre la llamada local.
    """
    if spec.name == "remediations.execute":
        action = connection.execute(
            "SELECT action_type FROM remediation_actions WHERE id=?", (payload.get("remediation_id"),)
        ).fetchone()
        # Revalidation reads persisted readiness; it must precede the conversation it repairs.
        # All other actions retain the conservative profile, regardless of client payload claims.
        if action and action["action_type"] in {"validate_runtime", "revalidate_runtime"}:
            return "qa_light"
    if spec.name not in INFERENCE_OPERATIONS:
        return spec.workload_class
    ids = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"providerId", "provider_id", "provider"} and isinstance(item, str):
                    ids.add(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    accounts = ProviderAccountStore(connection).list_provider_accounts()
    candidates = [account for account in accounts if account["providerId"] in ids]
    if not ids:
        candidates = [account for account in accounts if account["enabled"]]
    classes = {provider_workload_class(account) for account in candidates}
    if "agent_cli" in classes:
        return "agent_cli"
    if "local_model_call" in classes:
        return "local_model_call"
    return "remote_llm_light"
