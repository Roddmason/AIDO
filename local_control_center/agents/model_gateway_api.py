"""Router HTTP del Model Gateway: CRUD de proveedores/modelos/políticas y ejecución de ruteo.

Expone los endpoints REST que administran el catálogo de proveedores, modelos, precios, perfiles/políticas
de ruteo, límites, presupuestos, ledger de uso, benchmarks y runtimes CLI, además de previsualizar y
ejecutar rutas. La ejecución falla cerrada: exige aprobación, policy SQLite y credenciales válidas.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.runtime_integrations.config import resolve_executable
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

from .credentials import CredentialResolver
from .model_benchmarks import ModelBenchmarkStore
from .model_gateway import ModelGateway, _provider_usage_reported, provider_instance
from .model_gateway_models import (
    BudgetRulePatchRequest,
    BudgetRuleResponse,
    BudgetRulesListResponse,
    BudgetRuleUpsertRequest,
    CliRuntimesListResponse,
    CliSessionResponse,
    CliSessionsListResponse,
    DiscoverModelsResponse,
    ModelBenchmarkOutcomeCreateRequest,
    ModelBenchmarkOutcomeResponse,
    ModelBenchmarkOutcomesListResponse,
    ModelBenchmarksListResponse,
    ModelCatalogListResponse,
    ModelCatalogPatchRequest,
    ModelCatalogResponse,
    ModelCatalogUpsertRequest,
    ModelGatewayOverviewResponse,
    PricingSnapshotCreateRequest,
    PricingSnapshotResponse,
    PricingSnapshotsListResponse,
    ProviderAccountPatchRequest,
    ProviderAccountResponse,
    ProviderAccountsListResponse,
    ProviderAccountUpsertRequest,
    ProviderHealthResponse,
    ProviderLimitPatchRequest,
    ProviderLimitResponse,
    ProviderLimitsListResponse,
    RolePoliciesListResponse,
    RolePolicyPatchRequest,
    RolePolicyResponse,
    RolePolicyUpsertRequest,
    RouteExecuteResponse,
    RoutingDecisionsListResponse,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
    RoutingProfilePatchRequest,
    RoutingProfileResponse,
    RoutingProfilesListResponse,
    RoutingProfileUpsertRequest,
    RuntimeDetectionResponse,
    RuntimeHealthResponse,
    TestPromptRequest,
    TestPromptResponse,
    UsageLedgerListResponse,
    UsageSummaryResponse,
)
from .model_router import ModelRouter, RoutingRequest
from .provider_accounts import ProviderAccountStore
from .quota_manager import QuotaManager
from .routing_profiles import RoutingProfileStore
from .runtime_registry import RuntimeRegistry
from .usage_ledger import UsageLedger

CATALOG_ID_RE = re.compile(r"^[a-z0-9_.:-]{2,96}$")
SERVER_OWNED_PROVIDER_HEALTH_FIELDS = {"healthStatus", "lastHealthCheckAt", "lastError"}


def _payload(body: Any, *, exclude_none: bool = True) -> dict[str, Any]:
    if hasattr(body, "model_dump"):
        return body.model_dump(by_alias=True, exclude_none=exclude_none)
    return dict(body)


def _provider_client_payload(body: Any) -> dict[str, Any]:
    payload = _payload(body)
    for field in SERVER_OWNED_PROVIDER_HEALTH_FIELDS:
        payload.pop(field, None)
    return payload


def _provider_instance(provider_id: str, *, connection: Any):
    return provider_instance(provider_id, connection=connection)


def _requires_credential_for_real_discovery(account: dict[str, Any]) -> bool:
    return str(account.get("providerType") or "") in {"api", "gateway"}


def _requires_remote_provider_call(account: dict[str, Any]) -> bool:
    return str(account.get("providerType") or "") in {"api", "gateway"}


def _validate_real_discovery_credentials(account: dict[str, Any]) -> None:
    if not _requires_credential_for_real_discovery(account):
        return
    credential_ref = str(account.get("credentialRef") or "")
    if not credential_ref:
        raise HTTPException(
            status_code=400, detail=f"Credential ref is required for provider {account['providerId']}."
        )
    credential = CredentialResolver().resolve(credential_ref, fetch=False)
    if credential.status not in {"configured", "unverified"}:
        raise HTTPException(
            status_code=400,
            detail=redact_secrets(f"Credential ref {credential_ref} is {credential.status}."),
        )


TEST_PROMPT_MESSAGE = "Reply with the single word: ok."
TEST_PROMPT_SAMPLE_LIMIT = 280


def _resolve_test_prompt_model(
    store: ProviderAccountStore, provider_id: str, *, requested: str | None
) -> str:
    """Devuelve el modelo a probar: el solicitado o el primer modelo habilitado del catálogo del proveedor."""
    if requested:
        return requested
    for model in store.list_models():
        if model.get("providerId") == provider_id and model.get("enabled"):
            return str(model.get("model"))
    raise HTTPException(
        status_code=400,
        detail=f"Provider {provider_id} has no enabled model; sync or select a model before testing.",
    )


def _run_provider_test_prompt(provider_id: str, model: str, *, connection: Any) -> dict[str, Any]:
    """Ejecuta una completion corta contra el proveedor y devuelve un resultado redactado con latencia.

    Cualquier fallo del proveedor se captura y se reporta como ``ok=False`` con el error saneado, para
    que la prueba nunca filtre el secreto ni propague la excepción cruda al cliente.
    """
    from .providers.base import ModelRequest

    started = time.monotonic()
    try:
        provider = provider_instance(provider_id, connection=connection)
        response = provider.chat_completion(
            ModelRequest.model_validate(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": TEST_PROMPT_MESSAGE}],
                    "temperature": 0,
                }
            )
        )
        usage = response.usage
        raw_usage = getattr(usage, "raw_usage", None) or {}
        usage_reported = _provider_usage_reported(raw_usage)
        usage_source = str(raw_usage.get("usage_source") or "provider") if usage_reported else "unknown"
        return {
            "providerId": provider_id,
            "model": model,
            "ok": True,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "sample": str(redact_secrets(response.content or ""))[:TEST_PROMPT_SAMPLE_LIMIT],
            "totalTokens": int(getattr(usage, "total_tokens", 0) or 0) if usage_reported else None,
            "usageSource": usage_source,
            "error": None,
        }
    except Exception as error:
        # Any provider/network failure is surfaced as a redacted test result, never raised, so the
        # test action degrades gracefully and never leaks the secret in a stack trace.
        return {
            "providerId": provider_id,
            "model": model,
            "ok": False,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "sample": "",
            "totalTokens": None,
            "usageSource": "unknown",
            "error": str(redact_secrets(f"{error.__class__.__name__}: {error}")),
        }


def _approval_request_payload(body_payload: dict[str, Any], routing_result: dict[str, Any]) -> dict[str, Any]:
    request_payload = {key: value for key, value in body_payload.items() if key not in {"prompt", "messages"}}
    return redact_secrets(
        {
            "approvalRequired": True,
            "request": request_payload,
            "routing": routing_result,
        }
    )


def _validate_role_policy_payload(body: dict[str, Any], *, provider_ids: set[str]) -> dict[str, Any]:
    for field in ("id", "role", "routingProfileId"):
        value = body.get(field)
        if value is not None and not CATALOG_ID_RE.match(str(value)):
            raise HTTPException(status_code=422, detail=f"{field} must be a compact catalog id.")

    for field in ("preferred", "fallback", "escalation", "blocked"):
        candidates = body.get(field) or []
        if not isinstance(candidates, list):
            raise HTTPException(status_code=422, detail=f"{field} must be a provider catalog list.")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise HTTPException(status_code=422, detail=f"{field} entries must be objects.")
            provider = str(candidate.get("provider") or "")
            model = str(candidate.get("model") or "")
            if provider not in provider_ids:
                raise HTTPException(
                    status_code=422, detail="Role policy provider is not in the configured provider catalog."
                )
            if not model or len(model) > 160 or any(char.isspace() for char in model):
                raise HTTPException(
                    status_code=422, detail="Role policy model id must be a compact catalog value."
                )

    try:
        max_cost = float(body.get("maxCostPerTaskUsd", 0))
        max_tokens = int(body.get("maxTokensPerRun", 0))
        approval_threshold = body.get("requiresApprovalOverUsd")
        approval_value = None if approval_threshold in {None, ""} else float(approval_threshold)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Role policy numeric fields are invalid.") from error
    if max_cost < 0:
        raise HTTPException(status_code=422, detail="maxCostPerTaskUsd must be zero or positive.")
    if max_tokens < 0 or max_tokens > 200000:
        raise HTTPException(status_code=422, detail="maxTokensPerRun must be between 0 and 200000.")
    if approval_value is not None and approval_value < 0:
        raise HTTPException(status_code=422, detail="requiresApprovalOverUsd must be zero or positive.")
    return body


def create_router(*, platform: Any, require_write: Any) -> APIRouter:
    """Construye el APIRouter del Model Gateway, cableado a la conexión y al guard de escritura."""
    router = APIRouter(prefix="/api/v1/model-gateway", tags=["model-gateway"])

    def providers() -> ProviderAccountStore:
        return ProviderAccountStore(platform.connection)

    def routing() -> RoutingProfileStore:
        return RoutingProfileStore(platform.connection)

    def usage() -> UsageLedger:
        return UsageLedger(platform.connection)

    def jobs() -> JobsRepository:
        return JobsRepository(platform.connection)

    def benchmarks() -> ModelBenchmarkStore:
        return ModelBenchmarkStore(platform.connection)

    def runtimes() -> RuntimeConfigRepository:
        return RuntimeConfigRepository(platform.connection)

    def audit(action: str, target: str, payload: dict[str, Any] | None = None) -> None:
        EventBus(platform.connection).record_audit(action=action, target=target, payload=payload or {})

    def cli_runtime_command(runtime_id: str) -> str | None:
        try:
            installation = runtimes().get_installation(runtime_id)
        except KeyError:
            installation = {"runtimeId": runtime_id, "executablePath": None}
        return resolve_executable(installation).get("path")

    def persist_cli_probe(
        runtime_id: str, *, check_type: str, payload: dict[str, Any], executable_hint: str | None = None
    ) -> None:
        status = str(payload.get("status") or "unknown")
        version = payload.get("version")
        detected_executable = payload.get("executable") or executable_hint
        installed = status in {"installed", "healthy"}
        version_checked = bool(version)
        if installed and (version_checked or check_type == "health"):
            health_status = "healthy"
            last_error = ""
        elif installed:
            # El binario existe pero el probe de versión no respondió (p. ej. timeout bajo carga):
            # degradar, no declarar offline, y nunca guardar el mensaje de éxito como error.
            health_status = "degraded"
            last_error = "CLI was detected but the version probe did not return a usable version."
        else:
            health_status = "offline"
            last_error = str(payload.get("message") or status)
        timestamp = utc_now()
        try:
            current = runtimes().get_installation(runtime_id)
        except KeyError:
            current = {}
        runtimes().upsert_installation(
            {
                "runtimeId": runtime_id,
                "kind": "cli",
                "executablePath": detected_executable or current.get("executablePath"),
                "detectedVersion": version or current.get("detectedVersion"),
                "enabled": installed,
                "healthStatus": health_status,
                "lastValidationAt": timestamp
                if installed and version_checked
                else current.get("lastValidationAt"),
                "lastHealthCheckAt": timestamp,
                "lastError": last_error,
                "capabilities": current.get("capabilities") or [],
                "preferredRoles": current.get("preferredRoles") or [],
                "configurationSource": "detected"
                if installed
                else current.get("configurationSource", "manual"),
                "metadata": redact_secrets(
                    {
                        **(current.get("metadata") or {}),
                        "lastProbeType": check_type,
                        "lastProbeStatus": status,
                    }
                ),
            }
        )
        runtimes().record_health_check(
            runtime_id=runtime_id,
            check_type=check_type,
            status=health_status,
            payload=payload,
        )

    @router.get("/overview", response_model=ModelGatewayOverviewResponse)
    async def overview() -> dict[str, Any]:
        """Resume el estado del gateway: proveedores, salud, costo del día y pendientes."""
        provider_rows = providers().list_provider_accounts()
        usage_summary = usage().summary()
        limit_rows = routing().list_provider_limits()
        cli_sessions = routing().list_cli_sessions()
        action_requests = jobs().list_action_requests()
        return {
            "overview": {
                "providersEnabled": sum(1 for item in provider_rows if item["enabled"]),
                "apiProviders": sum(
                    1 for item in provider_rows if item["providerType"] in {"api", "gateway"}
                ),
                "cliRuntimes": sum(1 for item in provider_rows if item["providerType"] == "cli"),
                "localProviders": sum(1 for item in provider_rows if item["providerType"] == "local"),
                "healthy": sum(1 for item in provider_rows if item["healthStatus"] == "healthy"),
                "degraded": sum(1 for item in provider_rows if item["healthStatus"] == "degraded"),
                "offline": sum(
                    1 for item in provider_rows if item["healthStatus"] in {"offline", "misconfigured"}
                ),
                "totalTokensToday": usage_summary["totalTokens"],
                "estimatedCostToday": usage_summary["estimatedCostUsd"],
                "actualCostToday": usage_summary["actualCostUsd"],
                "pendingModelApprovals": sum(
                    1
                    for item in action_requests
                    if item["status"] == "pending" and str(item["actionType"]).startswith("model.")
                ),
                "providersInCooldown": sum(1 for item in limit_rows if item.get("cooldownUntil")),
                "activeCliSessions": sum(1 for item in cli_sessions if item["status"] == "running"),
            }
        }

    @router.get("/providers", response_model=ProviderAccountsListResponse)
    async def list_providers() -> dict[str, Any]:
        """Lista todas las cuentas de proveedor configuradas."""
        return {"providers": providers().list_provider_accounts()}

    @router.post("/providers", status_code=201, response_model=ProviderAccountResponse)
    async def create_provider(body: ProviderAccountUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza una cuenta de proveedor (los campos de salud los gestiona el servidor)."""
        require_write(request)
        try:
            provider = providers().upsert_provider_account(_provider_client_payload(body))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid credential_ref: {error}") from error
        audit(
            "model_gateway.provider.upserted", provider["providerId"], {"providerId": provider["providerId"]}
        )
        return {"provider": provider}

    @router.get("/providers/{provider_id}", response_model=ProviderAccountResponse)
    async def get_provider(provider_id: str) -> dict[str, Any]:
        """Devuelve una cuenta de proveedor por id (404 si no existe)."""
        try:
            return {"provider": providers().get_provider_account(provider_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.patch("/providers/{provider_id}", response_model=ProviderAccountResponse)
    async def patch_provider(
        provider_id: str, body: ProviderAccountPatchRequest, request: Request
    ) -> dict[str, Any]:
        """Actualiza parcialmente una cuenta de proveedor."""
        require_write(request)
        try:
            provider = providers().patch_provider_account(provider_id, _provider_client_payload(body))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid credential_ref: {error}") from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        audit("model_gateway.provider.updated", provider_id, {"providerId": provider_id})
        return {"provider": provider}

    @router.post("/providers/{provider_id}/health-check", response_model=ProviderHealthResponse)
    async def provider_health_check(provider_id: str, request: Request) -> dict[str, Any]:
        """Ejecuta un health-check del proveedor, registra el resultado y activa cooldown si hubo 429."""
        require_write(request)
        try:
            providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        health = ModelGateway(platform.connection).provider_health(provider_id)
        if health.get("status") == "rate_limited" or "429" in str(
            health.get("message") or health.get("lastError") or ""
        ):
            model = "auto_best_available" if provider_id == "nvidia_nim" else "*"
            QuotaManager(platform.connection).record_rate_limit(
                provider_id=provider_id, model=model, retry_after_seconds=300
            )
        providers().record_health_check(
            provider_id=provider_id, status=health["healthStatus"], payload=health
        )
        audit("model_gateway.provider.health_checked", provider_id, health)
        return {"health": health}

    @router.post("/providers/{provider_id}/discover-models", response_model=DiscoverModelsResponse)
    async def discover_models(provider_id: str, request: Request) -> dict[str, Any]:
        """Descubre y cataloga los modelos del proveedor; exige habilitación, flag de llamadas y credencial."""
        require_write(request)
        try:
            account = providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if not account.get("enabled"):
            raise HTTPException(
                status_code=403,
                detail=f"Provider {provider_id} is disabled; discovery requires explicit enablement.",
            )
        if _requires_remote_provider_call(account):
            policy_decision = runtimes().runtime_policy_decision(
                provider_id=provider_id, kind=str(account.get("providerType") or "")
            )
            if not policy_decision.get("allowed"):
                raise HTTPException(
                    status_code=403,
                    detail=str(policy_decision.get("reason") or "Remote provider discovery is disabled."),
                )
        _validate_real_discovery_credentials(account)
        discovered = [
            item.model_dump(by_alias=True)
            for item in _provider_instance(provider_id, connection=platform.connection).list_models()
        ]
        stored = [
            providers().upsert_model({**item, "providerId": provider_id, "enabled": True})
            for item in discovered
        ]
        audit(
            "model_gateway.provider.models_discovered",
            provider_id,
            {"count": len(stored), "source": "provider"},
        )
        return {"models": stored}

    @router.post("/providers/{provider_id}/test-prompt", response_model=TestPromptResponse)
    async def test_prompt(provider_id: str, body: TestPromptRequest, request: Request) -> dict[str, Any]:
        """Prueba el proveedor con una completion corta y controlada; falla cerrado como discover-models.

        Exige habilitación, política de runtime permitida y credencial válida. Envía un prompt fijo y
        benigno (nunca uno provisto por el cliente) y devuelve latencia, uso y una muestra redactada.
        No aplica a runtimes CLI/manual, que se ejecutan por sesiones de runtime aprobadas por política.
        """
        require_write(request)
        try:
            account = providers().get_provider_account(provider_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if str(account.get("providerType") or "") in {"cli", "manual"}:
            raise HTTPException(
                status_code=400,
                detail=f"Provider {provider_id} runs as a CLI/manual runtime; test-prompt targets API providers.",
            )
        if not account.get("enabled"):
            raise HTTPException(
                status_code=403,
                detail=f"Provider {provider_id} is disabled; test-prompt requires explicit enablement.",
            )
        if _requires_remote_provider_call(account):
            policy_decision = runtimes().runtime_policy_decision(
                provider_id=provider_id, kind=str(account.get("providerType") or "")
            )
            if not policy_decision.get("allowed"):
                raise HTTPException(
                    status_code=403,
                    detail=str(policy_decision.get("reason") or "Remote provider calls are disabled."),
                )
        _validate_real_discovery_credentials(account)
        model = _resolve_test_prompt_model(providers(), provider_id, requested=body.model)
        result = _run_provider_test_prompt(provider_id, model, connection=platform.connection)
        audit("model_gateway.provider.test_prompt", provider_id, {"model": model, "ok": result["ok"]})
        return {"test": result}

    @router.get("/models", response_model=ModelCatalogListResponse)
    async def list_models() -> dict[str, Any]:
        """Lista el catálogo de modelos de todos los proveedores."""
        return {"models": providers().list_models()}

    @router.post("/models", status_code=201, response_model=ModelCatalogResponse)
    async def create_model(body: ModelCatalogUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza una entrada del catálogo de modelos."""
        require_write(request)
        model = providers().upsert_model(_payload(body))
        audit(
            "model_gateway.model.upserted",
            model["id"],
            {"providerId": model["providerId"], "model": model["model"]},
        )
        return {"model": model}

    @router.patch("/models/{model_id:path}", response_model=ModelCatalogResponse)
    async def patch_model(model_id: str, body: ModelCatalogPatchRequest, request: Request) -> dict[str, Any]:
        """Actualiza parcialmente una entrada del catálogo de modelos."""
        require_write(request)
        try:
            model = providers().patch_model(model_id, _payload(body))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        audit("model_gateway.model.updated", model_id, {"modelId": model_id})
        return {"model": model}

    @router.get("/pricing-snapshots", response_model=PricingSnapshotsListResponse)
    async def list_pricing_snapshots() -> dict[str, Any]:
        """Lista los snapshots de precios registrados."""
        return {"pricingSnapshots": providers().list_pricing_snapshots()}

    @router.post("/pricing-snapshots", status_code=201, response_model=PricingSnapshotResponse)
    async def create_pricing_snapshot(body: PricingSnapshotCreateRequest, request: Request) -> dict[str, Any]:
        """Registra un snapshot de precios, opcionalmente aplicándolo al catálogo."""
        require_write(request)
        snapshot = providers().create_pricing_snapshot(_payload(body))
        audit(
            "model_gateway.pricing_snapshot.created",
            snapshot["id"],
            {
                "providerId": snapshot["providerId"],
                "model": snapshot["model"],
                "applyToCatalog": snapshot["applyToCatalog"],
                "sourceRef": snapshot["sourceRef"],
            },
        )
        return {"pricingSnapshot": snapshot}

    @router.get("/routing-profiles", response_model=RoutingProfilesListResponse)
    async def list_routing_profiles() -> dict[str, Any]:
        """Lista los perfiles de ruteo configurados."""
        return {"routingProfiles": routing().list_routing_profiles()}

    @router.post("/routing-profiles", status_code=201, response_model=RoutingProfileResponse)
    async def create_routing_profile(body: RoutingProfileUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza un perfil de ruteo."""
        require_write(request)
        profile = routing().upsert_routing_profile(_payload(body))
        return {"routingProfile": profile}

    @router.patch("/routing-profiles/{profile_id}", response_model=RoutingProfileResponse)
    async def patch_routing_profile(
        profile_id: str, body: RoutingProfilePatchRequest, request: Request
    ) -> dict[str, Any]:
        """Actualiza parcialmente un perfil de ruteo."""
        require_write(request)
        try:
            profile = routing().patch_routing_profile(profile_id, _payload(body))
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"routingProfile": profile}

    @router.get("/role-policies", response_model=RolePoliciesListResponse)
    async def list_role_policies() -> dict[str, Any]:
        """Lista las políticas de modelo por rol."""
        return {"rolePolicies": routing().list_role_policies()}

    @router.post("/role-policies", status_code=201, response_model=RolePolicyResponse)
    async def create_role_policy(body: RolePolicyUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza la política de un rol, validando ids, candidatos y límites numéricos."""
        require_write(request)
        payload = _payload(body)
        provider_ids = {item["providerId"] for item in providers().list_provider_accounts()}
        _validate_role_policy_payload(payload, provider_ids=provider_ids)
        policy = routing().upsert_role_policy(payload)
        return {"rolePolicy": policy}

    @router.patch("/role-policies/{policy_id}", response_model=RolePolicyResponse)
    async def patch_role_policy(
        policy_id: str, body: RolePolicyPatchRequest, request: Request
    ) -> dict[str, Any]:
        """Actualiza parcialmente la política de un rol, revalidando el resultado fusionado."""
        require_write(request)
        try:
            payload = body.model_dump(by_alias=True, exclude_none=True, exclude_unset=True)
            existing = routing().get_role_policy(policy_id)
            provider_ids = {item["providerId"] for item in providers().list_provider_accounts()}
            _validate_role_policy_payload({**existing, **payload}, provider_ids=provider_ids)
            policy = routing().patch_role_policy(policy_id, payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"rolePolicy": policy}

    @router.post("/route/preview", response_model=RoutingPreviewResponse)
    async def route_preview(body: RoutingPreviewRequest, request: Request) -> dict[str, Any]:
        """Previsualiza la decisión de ruteo para la solicitud y registra la decisión."""
        require_write(request)
        body_payload = _payload(body)
        result = ModelRouter(platform.connection).preview(RoutingRequest(**body_payload), record=True)
        audit(
            "model_gateway.route.previewed",
            body_payload.get("role", "unknown"),
            {"selected": result.get("selected")},
        )
        return result

    @router.post("/route/execute", response_model=RouteExecuteResponse)
    async def route_execute(body: RoutingPreviewRequest, request: Request) -> dict[str, Any]:
        """Rutea y ejecuta la llamada de modelo; abre solicitud de aprobación o bloquea cuando corresponde.

        Falla cerrado: 409 si no hay ruta o requiere aprobación, 403 para CLI deshabilitado, 501 para
        ejecución CLI real, y propaga el estado no-completado del gateway como el HTTP equivalente.
        """
        require_write(request)
        body_payload = _payload(body)
        result = ModelRouter(platform.connection).preview(RoutingRequest(**body_payload), record=True)
        selected = result.get("selected")
        if not selected:
            raise HTTPException(status_code=409, detail=result.get("decisionReason") or "No route selected.")
        if result.get("policyResult", {}).get("requiresApproval"):
            project_id = str(body_payload.get("projectId") or "model-gateway")
            approval_payload = _approval_request_payload(body_payload, result)
            job_result = jobs().create_job(
                project_id=project_id,
                kind="model_route_execute",
                status="approval_required",
                payload=approval_payload,
                workflow_run_id=body_payload.get("workflowRunId"),
                workflow_step_id=body_payload.get("workflowStepId"),
            )
            action = jobs().create_action_request(
                job_id=job_result["job"]["id"],
                project_id=project_id,
                action_type="model.route.execute",
                risk_level=str(body_payload.get("riskLevel") or "high"),
                command=f"{selected.get('provider')}/{selected.get('model')}:{selected.get('runtime')}",
                payload=approval_payload,
                reason="Model route execution requires approval before calling a provider or runtime.",
            )
            audit(
                "model_gateway.route.execution_approval_requested",
                selected.get("provider", "unknown"),
                {"jobId": job_result["job"]["id"], "actionRequestId": action["id"]},
            )
            raise HTTPException(
                status_code=409,
                detail="Route execution requires approval before calling a provider or runtime.",
            )
        runtime_type = str(selected.get("runtime") or "")
        if runtime_type == "cli":
            project_id = str(body_payload.get("projectId") or "model-gateway")
            policy_decision = runtimes().runtime_policy_decision(
                provider_id=str(selected.get("provider") or ""), kind="cli", project_id=project_id
            )
            if not policy_decision.get("allowed"):
                raise HTTPException(
                    status_code=403,
                    detail=str(policy_decision.get("reason") or "CLI route execution is blocked by policy."),
                )
            raise HTTPException(
                status_code=501,
                detail="Real CLI execution must be launched through policy-approved agent runtime sessions.",
            )
        message = str(body_payload.get("prompt") or body_payload.get("taskType") or "Execute routed task.")
        gateway = ModelGateway(platform.connection)
        planned_call = gateway.plan_model_call(
            project_id=str(body_payload.get("projectId") or "model-gateway"),
            provider=selected["provider"],
            model=selected["model"],
            runtime_type=runtime_type,
            messages=[{"role": "user", "content": message}],
            estimated_cost_usd=result.get("estimatedCostUsd"),
            budget_remaining_usd=body_payload.get("budgetRemainingUsd"),
            metadata={"routing": result},
        )
        execution = gateway.execute_model_call(
            {
                **planned_call,
                "role": body_payload.get("role"),
                "workflowRunId": body_payload.get("workflowRunId"),
                "workflowStepId": body_payload.get("workflowStepId"),
                "agentId": body_payload.get("agentId"),
                "jobId": body_payload.get("jobId"),
                "taskId": body_payload.get("taskId"),
            }
        )
        if execution["status"] != "completed":
            status_code = {
                "blocked": 403,
                "configuration_required": 400,
                "blocked_budget": 409,
                "unavailable": 503,
            }.get(str(execution["status"]), 409)
            detail = f"{execution['status']}: {execution.get('reason') or 'model call did not complete'}"
            raise HTTPException(status_code=status_code, detail=redact_secrets(detail))
        audit(
            "model_gateway.route.execute", selected["provider"], {"usageLedgerId": execution["usage"]["id"]}
        )
        return {"routing": result, "usage": execution["usage"], "content": execution["content"]}

    @router.get("/usage-ledger", response_model=UsageLedgerListResponse)
    async def list_usage_ledger() -> dict[str, Any]:
        """Lista los asientos del ledger de uso."""
        return {"usageLedger": usage().list_usage()}

    @router.get("/usage-ledger/summary", response_model=UsageSummaryResponse)
    async def usage_summary() -> dict[str, Any]:
        """Devuelve el resumen de uso agregado (tokens y costo, con desglose por proveedor)."""
        return {"summary": usage().summary()}

    @router.get("/routing-decisions", response_model=RoutingDecisionsListResponse)
    async def list_routing_decisions() -> dict[str, Any]:
        """Lista el historial de decisiones de ruteo registradas."""
        return {"routingDecisions": routing().list_routing_decisions()}

    @router.get("/benchmarks", response_model=ModelBenchmarksListResponse)
    async def list_benchmarks() -> dict[str, Any]:
        """Lista los benchmarks agregados de modelos."""
        return {"benchmarks": benchmarks().list_benchmarks()}

    @router.get("/benchmark-outcomes", response_model=ModelBenchmarkOutcomesListResponse)
    async def list_benchmark_outcomes() -> dict[str, Any]:
        """Lista los outcomes de benchmark crudos."""
        return {"outcomes": benchmarks().list_outcomes()}

    @router.post("/benchmark-outcomes", status_code=201, response_model=ModelBenchmarkOutcomeResponse)
    async def create_benchmark_outcome(
        body: ModelBenchmarkOutcomeCreateRequest, request: Request
    ) -> dict[str, Any]:
        """Registra un outcome de benchmark reportado."""
        require_write(request)
        outcome = benchmarks().record_outcome(_payload(body))
        audit("model_gateway.benchmark_outcome.recorded", outcome["providerId"], {"outcomeId": outcome["id"]})
        return {"outcome": outcome}

    @router.get("/provider-limits", response_model=ProviderLimitsListResponse)
    async def list_provider_limits() -> dict[str, Any]:
        """Lista los límites configurados por proveedor."""
        return {"providerLimits": routing().list_provider_limits()}

    @router.patch("/provider-limits/{limit_id}", response_model=ProviderLimitResponse)
    async def patch_provider_limit(
        limit_id: str, body: ProviderLimitPatchRequest, request: Request
    ) -> dict[str, Any]:
        """Actualiza parcialmente los límites de un proveedor."""
        require_write(request)
        try:
            return {"providerLimit": routing().patch_provider_limit(limit_id, _payload(body))}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/budget-rules", response_model=BudgetRulesListResponse)
    async def list_budget_rules() -> dict[str, Any]:
        """Lista las reglas de presupuesto configuradas."""
        return {"budgetRules": routing().list_budget_rules()}

    @router.post("/budget-rules", status_code=201, response_model=BudgetRuleResponse)
    async def create_budget_rule(body: BudgetRuleUpsertRequest, request: Request) -> dict[str, Any]:
        """Crea o reemplaza una regla de presupuesto."""
        require_write(request)
        return {"budgetRule": routing().upsert_budget_rule(_payload(body))}

    @router.patch("/budget-rules/{rule_id}", response_model=BudgetRuleResponse)
    async def patch_budget_rule(
        rule_id: str, body: BudgetRulePatchRequest, request: Request
    ) -> dict[str, Any]:
        """Actualiza parcialmente una regla de presupuesto."""
        require_write(request)
        try:
            return {"budgetRule": routing().patch_budget_rule(rule_id, _payload(body))}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/cli-runtimes", response_model=CliRuntimesListResponse)
    async def list_cli_runtimes() -> dict[str, Any]:
        """Lista los runtimes CLI conocidos por el registry."""
        return {"cliRuntimes": RuntimeRegistry().list_runtimes()}

    @router.post("/cli-runtimes/{runtime_id}/detect", response_model=RuntimeDetectionResponse)
    async def detect_cli_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        """Detecta el ejecutable y versión de un runtime CLI."""
        require_write(request)
        try:
            command = cli_runtime_command(runtime_id)
            detection = RuntimeRegistry().detect(runtime_id, executable=command)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        persist_cli_probe(runtime_id, check_type="detect", payload=detection, executable_hint=command)
        audit("model_gateway.cli_runtime.detected", runtime_id, detection)
        return {"detection": detection}

    @router.post("/cli-runtimes/{runtime_id}/health-check", response_model=RuntimeHealthResponse)
    async def health_cli_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        """Ejecuta un health-check de un runtime CLI."""
        require_write(request)
        try:
            command = cli_runtime_command(runtime_id)
            health = RuntimeRegistry().health_check(runtime_id, executable=command)
            detection = RuntimeRegistry().detect(runtime_id, executable=command)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        persist_cli_probe(
            runtime_id,
            check_type="health",
            payload={**detection, **health},
            executable_hint=command,
        )
        audit("model_gateway.cli_runtime.health_checked", runtime_id, health)
        return {"health": health}

    @router.get("/cli-sessions", response_model=CliSessionsListResponse)
    async def list_cli_sessions() -> dict[str, Any]:
        """Lista las sesiones CLI registradas."""
        return {"cliSessions": routing().list_cli_sessions()}

    @router.get("/cli-sessions/{session_id}", response_model=CliSessionResponse)
    async def get_cli_session(session_id: str) -> dict[str, Any]:
        """Devuelve una sesión CLI por id (404 si no existe)."""
        try:
            return {"cliSession": routing().get_cli_session(session_id)}
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
