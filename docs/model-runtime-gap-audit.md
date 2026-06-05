# Model Runtime Gap Audit

Fecha: 2026-05-20

> Historical snapshot: this audit records the pre-gateway gap analysis from
> 2026-05-20. The current product contract is stricter: mocks/fakes are allowed
> only inside tests, and runtime/provider execution must be real or fail closed
> with `configuration_required`, `blocked` or `unavailable`.

## Estado actual de `agents/model_gateway.py`

- `local_control_center/agents/model_gateway.py` existe, pero es una capa mínima.
- Expone `ModelGateway.prepare_model_call()` para seleccionar el primer candidato permitido por una `model_policy` existente.
- Soporta redacción básica de secretos mediante `redact_secrets()`.
- Distingue parcialmente proveedores locales/remotos con listas estáticas.
- Registra en `model_calls` y `cost_usage`, pero no registra tokens detallados, coste real, cuotas, decisiones de routing, health checks ni runtimes CLI como entidades separadas.
- `runtime_provider_status()` detecta `codex`, `claude` y Ollama de forma superficial. No hay adapters CLI ejecutables ni sesiones persistidas.

## Estado actual de `model_providers`, `model_policies`, `model_calls`, `cost_usage`

- `model_providers` existe desde la fase 3 como catálogo simple: `id`, `provider`, `label`, `status`, `allow_remote`, `metadata`.
- `model_policies` existe desde la fase 2 con `preferred`, `fallback`, `max_cost_usd`, `max_tokens`, `temperature`, `allow_remote`, `allow_local`, `status`.
- `model_calls` existe con `provider`, `model`, `status`, `prompt_tokens`, `completion_tokens`, `cost_usd` y `metadata`.
- `cost_usage` existe como ledger simple por `project_id`, `scope`, `amount_usd`, `metadata`.
- Falta granularidad: cached/reasoning/tool tokens, costes estimados vs reales, runtime type, role, workflow/job/task/session ids y raw provider usage.

## Endpoints existentes relacionados con modelos/agentes

- `GET /api/v1/model-providers`
- `GET /api/v1/model-policies`
- `POST /api/v1/model-policies`
- `GET /api/v1/runtime/providers`
- `GET /api/v1/agent-profiles`
- `POST /api/v1/agent-profiles`
- `GET /api/v1/agent-runs`
- `POST /api/v1/agent-runs`
- No existen endpoints bajo `/api/v1/model-gateway/*`.

## Estado actual del frontend Model Gateway

- La página existe dentro de `local-control-center/web/src/features/pages.tsx`.
- Muestra proveedores simples, catálogo Ollama, formulario estricto de `model_policy`, coste agregado y `model_calls`.
- No tiene secciones para provider accounts, model catalog editable, routing profiles, role assignments, usage ledger detallado, budgets, limits, routing decisions, CLI sessions ni benchmarks.
- El frontend usa `Overview` generado por OpenAPI y helpers manuales en `api/client.ts`.

## Estado actual de Agents

- `agent_profiles` existe con rol, runtime, model policy, herramientas, permission profile, límites de coste y runtime.
- Roles actuales permitidos: `product_owner`, `technical_lead`, `implementer`, `qa_reviewer`, `security_reviewer`.
- Falta `analyst`, `developer`, `qa`, `release_manager` como roles de modelo/routing.
- Los perfiles no guardan routing profile, allowed providers/runtimes ni flags separados `allow_cli`/`allow_api`.

## Estado actual de Workflows

- `workflow_steps` ya guarda `input`, `output` y `metadata`.
- Los steps se crean con nombres fijos y metadata `{order}`.
- No hay campos dedicados para role, task_type, risk_level, model_mode ni manual_model_override.
- La integración correcta debe usar metadata/input sin romper datos existentes y puede migrar columnas opcionales después.

## Estado actual de Settings

- `.env.example` existe con configuración mínima.
- `shared/settings.py` solo resuelve `LOCAL_CONTROL_CENTER_DB` y `LOCAL_CONTROL_CENTER_CWD`.
- No existe configuración formal para real provider calls, CLI runtimes, routing mode, provider API keys ni límites default.

## Módulos que ya existen y deben reutilizarse

- `shared.migrations` para crear tablas y seeds idempotentes.
- `shared.serialization` para JSON estable.
- `shared.time.utc_now` para timestamps.
- `shared.event_bus.EventBus` para audit/event logging.
- `agents.repository.AgentsRepository` para compatibilidad con agentes existentes.
- `security_policy.policy_engine.evaluate_action` y `command_classifier` para policy checks antes de runtime execution.
- `evidence.artifacts` y `EvidenceRepository` para artifacts de ejecución.
- `control_plane.overview` y `control_plane.models` para snapshot dashboard.

## Tablas faltantes

- `provider_accounts`
- `model_catalog`
- `routing_profiles`
- `role_model_policies`
- `usage_ledger`
- `provider_limits`
- `routing_decisions`
- `cli_sessions`
- `runtime_capabilities`
- `provider_health_checks`
- `budget_rules`
- `model_benchmarks`

## Tests existentes

- Tests Python bajo `tests_py` cubren schema, API v1, policies, workflows, evidence, runtime risk controls y dashboard state.
- Tests Playwright bajo `tests_web/control-center.spec.js` cubren navegación, formularios estrictos y Model Gateway básico.
- No hay tests específicos para Unified Model & Runtime Gateway, routing decisions, quota manager, providers API, CLI adapters ni redacción de secretos en nuevos endpoints.

## Riesgos técnicos

- El scope solicitado es amplio para una sola iteración; la decisión técnica correcta es implementar un MVP completo y tipado con adapters reales mínimos y tests aislados por mocks/fakes solo dentro de test.
- OpenAPI generado puede quedar desalineado si se usan nuevos endpoints desde frontend. Para reducir riesgo, el frontend puede usar `apiRequest` tipado manualmente y luego regenerar OpenAPI si el build lo exige.
- SQLite no tiene ALTER COLUMN; cualquier migración debe ser aditiva.
- El router debe evitar mezclar providers API con CLI runtimes. Esto requiere tablas separadas por `provider_type` y `runtime_type`, aunque se compartan catálogos para UI.
- Discovery real de proveedores no debe ejecutarse en tests ni al arrancar.

## Riesgos de seguridad

- API keys no deben persistirse; solo `credential_ref`.
- `Authorization`, tokens y secret env names sensibles deben redacted en logs, artifacts y raw usage.
- CLI runtimes son de alto riesgo: deben bloquear flags de bypass, ejecución fuera de workspace, acceso a secretos y network sin policy.
- `local_private` debe bloquear proveedores remotos por defecto.
- NVIDIA NIM debe tratarse como remote API trial/free-limited, no como runtime de edición de código.

## Dependencias existentes

- Backend: FastAPI, Uvicorn, NumPy, Pydantic transitivo por FastAPI, pytest/httpx para tests.
- Frontend: React, TypeScript, Vite, Playwright, Lucide, Radix.
- No se requiere nueva dependencia obligatoria. LiteLLM debe permanecer opcional.

## Plan de implementación por fases

1. Agregar tests backend rojos para esquema, seeds, route preview, quotas, ledger, providers reales con HTTP/daemon falso solo en tests, CLI detection y redaction.
2. Crear migración fase 12 idempotente con tablas nuevas y seeds iniciales no dogmáticos.
3. Implementar DTOs base, pricing catalog, quota manager, usage ledger, provider accounts, routing profiles y model router.
4. Implementar providers API reales/fail-closed: NVIDIA NIM, OpenAI-compatible, OpenAI, Anthropic, OpenRouter, Ollama y LiteLLM opcional.
5. Implementar CLI runtime adapters con detection real y ejecución delegada a sesiones aprobadas: Codex CLI, Claude Code CLI, OpenHands, SWE-agent y manual.
6. Añadir router `/api/v1/model-gateway/*`, registrar audit events y conservar endpoints legacy.
7. Ampliar overview y frontend Model Gateway como consola operacional con route preview sin ejecución simulada.
8. Documentar configuración, riesgos y pruebas; actualizar `.env.example` y `config/model-routing.example.yaml`.
9. Ejecutar pytest, typecheck/build frontend y Playwright cuando sea viable.

## Resultado de esta iteración

- Se agregó migración fase 12 con tablas del Unified Model & Runtime Gateway y seeds iniciales.
- Se implementaron adapters iniciales para proveedores API y runtimes CLI; el contrato actual exige ejecución real o fallo cerrado.
- Se agregaron endpoints `/api/v1/model-gateway/*`.
- Se amplió el frontend Model Gateway como consola operacional.
- Se mantuvo compatibilidad con `model_policies`, `model_calls`, `cost_usage` y formularios estrictos existentes.
- Las llamadas reales de proveedores y runtimes CLI quedan desactivadas por defecto.

## Cierre de riesgos posteriores

- OpenAPI ya no queda desalineado: los endpoints del Model Gateway tienen `response_model` Pydantic, el cliente generado incluye los tipos nuevos y el frontend usa operaciones generadas para leer/mutar el gateway.
- `routing_decisions` ahora puede enlazar `workflowRunId`, `workflowStepId`, `agentId`, `jobId` y `taskId` mediante migración aditiva.
- `Workflows` invoca `ModelRouter` al iniciar un workflow y registra una decisión por step sin ejecutar proveedores reales durante preview.
- `Agent Profiles` expone y persiste `routingProfileId`, `roleModelPolicyId`, providers/runtimes permitidos, límites de tokens, flags remote/CLI/API y umbral de approval.
- `CLI runtimes` parsean usage desde JSON/JSONL cuando el runtime lo emite; si no hay usage exacto, se conserva `None` y no se inventa precisión.
- `Benchmarks` exponen datos derivados de `usage_ledger` para intentos, coste medio, latencia y último uso; success/QA/rework se calculan cuando existen outcomes explícitos.
- Descubrimiento remoto real sigue deshabilitado por defecto y protegido por `AIDO_ENABLE_REAL_PROVIDER_CALLS=false`.

## Cierre de siguientes pasos

- Se agregó fase 13 con `model_benchmark_outcomes` para recolectar outcomes explícitos sin guardar prompts.
- `GET/POST /api/v1/model-gateway/benchmark-outcomes` permite registrar success, QA pass, rework, coste y latencia.
- `GET /api/v1/model-gateway/benchmarks` ahora fusiona usage ledger y outcomes para métricas reales cuando existen.
- `POST /api/v1/model-gateway/route/execute` existe y falla cerrado por defecto; requiere enablement explícito, credenciales configuradas, routing válido y ausencia de approval pendiente. Cuando el routing requiere aprobación, crea un `action_request` `model.route.execute` antes de devolver `409`.
- `POST /api/v1/evidence` ingiere automáticamente outcomes de benchmark cuando recibe `usageLedgerId` o identidad explícita de provider/model/runtime.
- Los parsers CLI reconocen aliases estructurados comunes (`usage`, `token_usage`, `tokens`, `message.usage`, `metrics.token_usage`, `llm_metrics`) y devuelven `None` para texto libre.
- La migración fase 3 ahora actualiza tablas `workspaces`, `workspace_allocations` y `test_results` legacy con columnas requeridas antes de crear índices, manteniendo arranque sobre DBs locales antiguas sin destruir datos.
