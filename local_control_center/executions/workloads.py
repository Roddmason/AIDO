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
        "models.route_execute",
    }
)


def operation_workload(connection, spec, payload):
    """Reserva el perfil más restrictivo posible sin confiar en flags de recursos del cliente."""
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
    if "local_gpu_model" in classes:
        return "local_gpu_model"
    if "agent_cli" in classes:
        return "agent_cli"
    return "remote_llm_light"
