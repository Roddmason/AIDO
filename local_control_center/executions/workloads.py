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
    if spec.name == "agents.run_developer_agent":
        body = payload.get("body")
        preferred = body.get("preferredRuntime") if isinstance(body, dict) else None
        if isinstance(preferred, str) and preferred:
            account = next(
                (
                    account
                    for account in ProviderAccountStore(connection).list_provider_accounts()
                    if account["providerId"] == preferred
                ),
                None,
            )
            if account is not None and provider_workload_class(account) == "local_gpu_model":
                return "local_gpu_model"
        # Preserve the patch/QA envelope without selecting a runtime during enqueue.
        return spec.workload_class
    if spec.name == "remediations.execute":
        action = connection.execute(
            "SELECT action_type FROM remediation_actions WHERE id=?", (payload.get("remediation_id"),)
        ).fetchone()
        # Revalidation reads persisted readiness; it must precede the conversation it repairs.
        # All other actions retain the conservative profile, regardless of client payload claims.
        if action and action["action_type"] == "validate_runtime":
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
    if "local_gpu_model" in classes:
        return "local_gpu_model"
    if "agent_cli" in classes:
        return "agent_cli"
    return "remote_llm_light"
