# Equipo de runtimes por hilo — selector validado + asignación de roles

**Fecha:** 2026-09-22
**Estado:** Diseño aprobado (sub-proyecto 1 de 3). Pendiente de plan de implementación.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Objetivo

Que el operador elija, **por hilo**, qué runtimes de IA participan (Claude Code CLI, Codex CLI, NVIDIA NIM,
Ollama, llama.cpp, OmniRoute, Gemini…) y qué runtime cumple cada rol (PO, Developer, Arquitecto,
Seguridad), y que **solo sean seleccionables los runtimes que respondieron a una prueba real reciente**.

Criterios de éxito verificables:

1. El panel lista todos los runtimes configurados; el checkbox solo se habilita si el runtime pasó una
   prueba de ida y vuelta en los últimos 30 minutos con la configuración actual.
2. Con 1 runtime seleccionado, ese runtime queda asignado a todos los roles para los que es elegible;
   con N, el reparto automático distribuye y el operador puede editar cada rol.
3. El product loop del hilo **solo** usa runtimes del conjunto seleccionado y, por rol, el asignado.
   Un runtime fuera del conjunto no puede ser elegido ni por failover.
4. Si al momento de ejecutar un runtime asignado dejó de estar validado, el loop se bloquea con una
   remediación accionable ("Re-probar runtime"), nunca cae en silencio a otro runtime.

## 2. Estado actual (verificado)

| Hecho | Evidencia |
|---|---|
| No existe allowlist ni override de runtime por hilo ni por rol | `product_loop/metadata.py:34-64` — `runConfiguration` solo guarda `teamMode` |
| El filtro duro ya existe en el gestor de recursos | `AIResourceRequest.allowed_provider_ids` `agents/ai_resource_manager.py:75`, aplicado en `:1336` (`provider_not_allowed_for_agent`) y `runtime_preflight_cli.py:158` |
| La selección por equipo no pasa allowlist | `_team_resource_request` `product_loop/coordinator.py:2920-2934` |
| `preferred_resources` se descarta cuando la decisión elige runtime | `ai_resource_manager.py:541-544` → una asignación dura debe expresarse como allowlist de un proveedor |
| Prueba real de ida y vuelta para API/local | `_run_provider_test_prompt` `agents/model_gateway_api.py:204-317` ("Reply with the single word: ok.") |
| El endpoint `test-prompt` rechaza CLI | `model_gateway_api.py:869` |
| Prueba real para CLI existe pero solo dentro del loop | `validate_cli_candidate` `agents/runtime_preflight_cli.py:121` (exige contexto de ejecución, `allow_unknown_cost`, aprobación de smoke Codex) |
| Evidencia de ejecución con TTL 24 h y fingerprint de configuración | `model_execution_health` (`shared/migrations.py:151-172`), `agents/model_execution_health.py:18,42-76,117-150` |
| llama.cpp no existe en el catálogo | `agents/provider_catalog.py:179-428` (sin entrada); precedente local OpenAI-compatible: OmniRoute `:241` |
| Roles que consumen un runtime de IA | PO `coordinator.py:3239`, Developer `:3378`→`phases/execution.py:90-115`, Seguridad `:3411`→`phases/security.py:128`, Arquitecto `:4413` (ignora `preferredRuntime` hoy). QA, DevOps y Tech Lead son deterministas |
| Gemini no puede ser PO hoy | Familia `gemini` fuera de `MODEL_RUNTIME_TOOLS` (`security_policy/policy_engine.py:67`); filtro PO `coordinator.py:3253-3266` |

## 3. Diseño

### 3.1 Prueba de runtime (`models.validate_runtime`)

- **Operación nueva por runtime:** `POST /api/v1/model-gateway/providers/{provider_id}/validate-runtime`,
  decorada `@queued_operation("models.validate_runtime", …)` y agregada a `INFERENCE_OPERATIONS`
  (`executions/workloads.py:11-21`) para que cada runtime reciba su clase de carga (CLI → `agent_cli`,
  API → `remote_llm_light`, loopback → `local_gpu_model`). Una operación **por runtime** (no batch), para
  no imponer la clase más restrictiva a todos.
- **API / gateway / local:** reusa `_run_provider_test_prompt` con el mensaje fijo existente.
- **CLI:** reusa `validate_cli_candidate` construyendo un `AIResourceRequest` del operador con
  `allow_unknown_cost=True`, `require_approval_for_unknown_cost=False` y `project_id` del payload. El clic
  del operador **es la aprobación**: se registra auditoría `runtime.validation.operator_approved`
  (runtime, proyecto, hora). La prueba gasta cuota de suscripción; la UI lo dice.
- **Evidencia:** `record_model_execution(..., source="test_prompt", fingerprint)` en la tabla existente.
  Sin migración. Un fallo invalida también la validación de 24 h (fail-closed, deliberado).
- **Frescura para el selector:** predicado nuevo de solo lectura
  `runtime_validated_within(connection, provider_id, seconds=1800)` = último registro exitoso, mismo
  fingerprint actual, edad < 30 min. Los consumidores de 24 h (`ai_resource_manager.py:509`,
  `tool_broker.py:632`, `runtime_preflight.py:313/378/431`, `model_gateway_api.py:1132`,
  `runtime_risk_review.py:568`) no cambian.
- **Nombre:** la remediación existente `validate_runtime` (`remediations/service.py:1107`) solo lee
  readiness; la operación nueva usa el nombre calificado `models.validate_runtime` y la remediación del
  §3.5 la invoca.

### 3.2 llama.cpp como proveedor

Entrada de catálogo `llama_cpp` en `agents/provider_catalog.py`, calcada de OmniRoute:

| Campo | Valor |
|---|---|
| `provider_type` | `local` (sin credencial obligatoria, localidad local) |
| `api_format` / `provider_family` | `openai_compatible` (entra en `MODEL_RUNTIME_TOOLS` → elegible como PO y Developer por patch) |
| `default_base_url` | `http://127.0.0.1:8082/v1` |
| `credential_kind` | `optional_bearer_token` (soporta `--api-key` de llama-server) |
| `model_sync` | `/v1/models` |
| capacidades | `chat, local, private, streaming`, más `code_edit`/`code_review` sembradas como `_seed_omniroute_runtime_capabilities` (`provider_catalog_api.py:63-88`) |

Se agrega a la lista paralela de la UI (`features/runtime-setup/runtimeSetup.ts`) con copy bilingüe.

### 3.3 Candidatos y elegibilidad por rol

`GET /api/v1/runtime/team-candidates?projectId=` devuelve por runtime configurado y habilitado:
`providerId, label, kind (cli|api|gateway|local), validation {status: validated|stale|failed|running|never,
checkedAt, latencyMs, reason}, eligibleRoles[]`.

Elegibilidad (regla dura, calculada en backend, no en la UI):

| Rol | Elegible si |
|---|---|
| `product_owner` | CLI de PO o familia en `PRODUCT_OWNER_AGENT_MODEL_RUNTIMES` (`product_owner_agent_contract.py:27-47`) |
| `developer` | capacidad `code_edit` (CLI o API vía `workspace_patch`) |
| `architect` | capacidad `chat` |
| `security` | capacidad `chat` o `code_review` |

### 3.4 Configuración del hilo

- `project_threads.metadata.runConfiguration` gana `allowedRuntimes: string[]` y
  `roleRuntimes: {product_owner?, developer?, architect?, security?}` (patrón `_remember_team_mode`,
  `metadata.py:37-110`).
- **Escritura:** `PATCH /api/v1/threads/{thread_id}/run-configuration` (nuevo; `ThreadUpdateRequest` solo
  renombra) y opcionalmente en `POST /api/v1/threads`. Editable mientras el hilo no esté `queued`/`running`.
- **Sellado al enviar mensaje** (donde hoy se sella `teamMode`, `threads/coordinator.py:928`): el backend
  re-verifica cada runtime con `runtime_validated_within` y lo intersecta con
  `project.runtime.allowedProviders` (`settings/registry.py:123`). **Solo restringe, nunca amplía.** Un rol
  sin runtime válido → el mensaje se rechaza con 422 y causa legible (no se encola nada).
- Sin selección explícita → comportamiento actual (sin allowlist), para no romper hilos existentes.

### 3.5 Aplicación en el product loop

| Sitio | Cambio |
|---|---|
| `_team_resource_request` (`coordinator.py:2934`) | `allowed_provider_ids = allowlist del hilo`; para roles asignados, allowlist de **un** proveedor |
| `_product_owner_resource_selection` (`:3267/:3311`) | intersección con la allowlist; si hay `roleRuntimes.product_owner`, solo ese |
| `_failover_replacement` (`:4840`) | el reemplazo solo dentro de la allowlist del hilo; un rol asignado no hace failover a otro proveedor → bloquea |
| `_security_execution_resource` (`:3411`) | allowlist de un proveedor si está asignado |
| Arquitecto (`:4413`) | pasar `preferredRuntime` (el runner ya lo lee, `architect_agent.py:~657`) y validar pertenencia |

Runtime asignado que dejó de estar validado al ejecutar → bloqueo con remediación **"Re-probar runtime"**
que encola `models.validate_runtime` y, si pasa, reanuda el retry existente.

### 3.6 Reparto automático

Función pura (backend, con espejo de lectura en la UI para previsualizar) sobre los runtimes
seleccionados y validados, en orden de importancia de rol:

1. **Developer** ← el runtime elegible de mayor rango para `code_edit`: CLI antes que API; en empate, el
   orden de `runtime_preferences.runtime_order` global y luego `providerId` alfabético (determinista).
2. **PO** ← el siguiente runtime elegible distinto; si no hay, reusa.
3. **Arquitecto**, **Seguridad** ← los restantes en orden; si se acaban, reciclan desde el primero.

Con 1 runtime: ese runtime en todos los roles donde sea elegible. Un rol sin ningún elegible entre los
seleccionados queda marcado y bloquea el envío. El operador puede cambiar cualquier asignación.

### 3.7 UI

- Chip en el composer (hilo nuevo y existente): **"Equipo IA · 2 de 5"** → abre panel (`Drawer`/popover).
- Lista de runtimes: `Checkbox` (deshabilitado si no está `validated`), `StatusChip` de validación,
  latencia, "Probar" por fila. **Al abrir el panel se prueban automáticamente los vencidos** (el operador
  eligió esta política; las filas CLI muestran que la prueba consume cuota).
- Grilla rol → runtime (`SelectField` por rol, solo opciones elegibles y seleccionadas), con el reparto
  automático precargado y botón "Repartir automáticamente".
- QA, DevOps y Tech Lead se muestran como filas fijas "AIDO (determinista)".
- Copy vía `t()` y claves bilingües (`i18n/default_catalog.json`); sin literales gateados.

## 4. Errores y observabilidad

- Fallo de prueba: estado `failed` con causa clasificada (reusa `classify_runtime_failure`), nunca
  secretos (redacción existente).
- Auditoría por prueba de CLI aprobada por el operador.
- Evento de hilo `run_configuration_updated` al guardar; el sellado deja la configuración efectiva en la
  metadata del run.

## 5. Pruebas

- Unit: predicado de frescura (vigente/vencida/fingerprint distinto/fallo posterior), elegibilidad por
  rol, reparto automático (1, 2, 4 runtimes; rol sin elegibles).
- Integración: `validate-runtime` API y CLI (CLI con el runner mockeado en el borde del proceso), PATCH
  run-configuration, sellado que rechaza runtimes no validados, `allowed_provider_ids` aplicado en PO,
  equipo, failover y seguridad; arquitecto recibe `preferredRuntime`.
- Contrato: OpenAPI regenerado (endpoint nuevo + `EXECUTION_OPERATIONS`), catálogo con `llama_cpp`.
- Web: spec Playwright del panel (checkbox deshabilitado sin validación, reparto, envío).

## 6. Fuera de alcance

- Crear un runtime nuevo desde el panel (se usa el wizard existente).
- Hacer elegible a Gemini como PO (requiere adaptador de familia).
- Costos por rol en el panel (existe `ThreadCostPanel`).

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Probar CLIs al abrir el panel gasta cuota | Solo los vencidos (> 30 min); aviso visible; auditoría |
| La prueba CLI puede diferirse por admisión de recursos (`agent_cli` 8 GiB) | Estado `running`/`deferred` visible con la causa del governor |
| Cambio de configuración tras validar | Fingerprint re-verificado al sellar y al ejecutar |
| llama.cpp y Ollama compiten por GPU (`local_gpu_model`) | Admisión existente; causa visible |
