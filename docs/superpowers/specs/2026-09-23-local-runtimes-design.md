# Runtimes locales de primera clase: llama.cpp, LM Studio, vLLM y servidor local OpenAI-compatible

**Fecha:** 2026-09-23
**Estado:** Diseño aprobado por el operador (2026-09-23); enmendado tras crítica adversarial multi-lente
(arquitectura, seguridad/privacidad, hechos de API, Codex) con 22 hallazgos verificados. Pendiente de revisión de la
spec y plan.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Objetivo

Que AIDO no dependa solo de Ollama como runtime local. llama.cpp (`llama-server`), LM Studio, vLLM y cualquier
servidor local OpenAI-compatible deben poder configurarse, validarse, seleccionarse para roles del equipo y
ejecutar agentes de punta a punta, con la misma seriedad (fail-closed, evidencia, privacidad) que el resto de
runtimes.

**Criterio de éxito verificable:**

1. Con el `llama-server` real del operador (`http://127.0.0.1:8082`, router mode, 4 modelos) AIDO lo agrega
   desde el asistente, lo marca `healthy`, lista sus modelos con estado de carga, valida el modelo por defecto y
   lo ofrece como candidato del equipo del hilo; un hilo con el PO en llama.cpp llega a brief/backlog ejecutado
   por llama.cpp.
2. Con ~20 GiB de RAM libres (situación normal del host con un modelo residente) una llamada a llama.cpp hecha
   dentro de un job `agent_cli` no queda en `resource_wait` ni se difiere en el preflight.
3. LM Studio, vLLM y el servidor genérico pasan el mismo ciclo contra dobles de prueba construidos desde su
   documentación oficial (no están instalados en el host; ver §9).
4. Ningún flujo existente de Ollama, del equipo por hilo ni del product loop regresa (incluidos los casos de
   `test_runtime_preflight.py:515-553`).

## 2. Estado actual (verificado 2026-09-23)

Exploración de 7 áreas con verificación adversarial (evidencia `archivo:línea`) y sondeo en vivo.

- `llama_cpp` existe solo como preset de catálogo (`agents/provider_catalog.py:411-426`, commit `0de3956e`): no
  aparece en `GET /api/v1/runtime/providers` ni en `GET /api/v1/runtime/team-candidates`.
- **Bloqueo 1 — salud:** `ModelGateway._provider_configuration` rechaza todo `providerType=local` que no sea Ollama
  (`agents/model_gateway.py:595-601`, "Unsupported local model provider"). Sin evidencia `healthy`,
  `_api_provider_status` queda `executable=false` y ni el gestor de recursos ni el router lo eligen.
- **Bloqueo 2 — costo:** el preflight solo exime de "costo desconocido" a `providerType=local ∧ apiFormat=ollama ∧
  loopback` (`agents/runtime_preflight.py:167-183,351-370`); el sync genérico deja precios `NULL` y `free_tier=0`
  (`provider_catalog_api.py:438-456`) y un resync pisa cualquier marca manual.
- **Bloqueo 3 — habilitación:** readiness gatea lo no-CLI con `remoteEnabled` y solo Ollama usa `ollamaEnabled`
  (`agents/runtime_readiness.py:160-166`); el modo de proyecto `ollama` exige id Ollama
  (`runtime_integrations/repository.py:289-300`). No existe un modo "solo local" que admita llama.cpp.
- **Bloqueo 4 — recursos:** una URL loopback se clasifica `local_gpu_model` (`runtime_readiness.py:144-148`):
  carga pesada de 16 GiB con `gpu_required` y el único slot pesado (`host_resources/profiles.py:121-130`,
  `settings/registry.py:185-193`). `_parent_covers` solo deja que un hijo use la lease del padre si ambos son el
  literal `local_gpu_model` (`host_resources/branch_admission.py:125-128`), así que dentro de un job `agent_cli`
  (8 GiB, `profiles.py:70-79`) la llamada pide admisión propia y cae en `aggregate_memory_budget`
  (`host_resources/governor.py:190-199`). Con el default `minFreeMemoryGiB=16` (`settings/registry.py:205-213`;
  este host lo tiene en 12) nunca se admite con ~20 GiB libres. El modelo ya está residente en el servidor externo:
  la reserva cuenta dos veces la misma memoria.
- **Localidad inconsistente:** 3 clasificadores divergentes (`ai_resource_manager.py:152-164`,
  `runtime_readiness.py:144-148`, `ollama/api.py` `_infer_kind`); `deploymentMode` "local" no existe en
  `model_gateway_models.py:30-36` (rama muerta); los contratos de agente tratan `openai_compatible` como familia
  remota (allowRemote/networkRequired) aunque el endpoint sea loopback.
- **Ruta de modelo (afecta también a Ollama y a todo OpenAI-compatible):** timeout fijo de 60 s
  (`providers/openai_compatible.py:211`, `providers/ollama.py:183`); `max_tokens` serializado como `maxTokens`
  junto a `metadata` (`openai_compatible.py:204`), así que hoy el límite no llega al servidor; sin
  `response_format`; la redacción de secretos se aplica antes de persistir el artifact que luego los agentes
  parsean (`runtime_adapters/provider_factory.py:246-251`, `shared/redaction.py:20-36`; parsers en
  `developer_agent.py:346-350,459`, `product_owner_agent.py:1071-1078`, `architect_agent.py:723-727`); uso y
  latencia descartados en el broker (`provider_factory.py:246-266`); el Architect del product loop no recibe
  modelo (`product_loop/coordinator.py:4557-4571`); no se descartan bloques de razonamiento; lectura de respuesta
  sin límite de tamaño (`openai_compatible.py:180,212`).
- **Selección multi-modelo:** `_selection_models` devuelve una fila por (provider, modelo)
  (`ai_resource_manager.py:920-939`), la preflight sondea cada elegible (`:434-466`) y Jev reparte la
  probabilidad entre todos (`decision_engine/service.py:254-255`); `role_allowlist` filtra por provider, no por
  modelo (`ai_resource_manager.py:1336-1341`). Con varios modelos del mismo runtime local Jev bloquea con
  `confidence_below_threshold` (fenómeno corregido para equipos multi-runtime en `e8a31dcc`).
- **Validación por runtime:** el gate del equipo lee la última fila del provider sin mirar el modelo
  (`runtime_team/validation.py:64-69`) y la sonda usa el primer modelo alfabético (`runtime_team/probe.py:197`).
- **UI:** URL de llama.cpp fija (el asistente no la muestra y un re-guardado la resetea), sin multi-instancia en
  la UI (el backend la soporta con `instanceId`), sin panel de endpoints genérico ni `DELETE` de endpoints,
  remediación que pide una API key inexistente (`remediations/payloads.py:234-278`).
- **En vivo:** Ollama apagado (`:11434` rechaza), `llama-server` sano en `:8082` en router mode
  (`/props`: `role=router`, `max_instances=1`, `models_autoload=true`); `/v1/models` expone `status.value`
  (`loaded`/`unloaded`) por modelo. Estos campos no están en la documentación oficial (§9).

## 3. Decisiones del operador (2026-09-23)

| Tema | Decisión |
|---|---|
| Enfoque | Familia de protocolo `openai_compatible` + eje de localidad + perfil de sondeo por servidor (enfoque A). Se descartan un adapter/familia por servidor (≈10 puntos por runtime) y enrutar por gateway (rompe `local_private` y oculta el estado del modelo). |
| Runtimes | llama.cpp, servidor local OpenAI-compatible genérico, LM Studio, vLLM (vía WSL). |
| Modelos en router/JIT | Preferir el modelo cargado; cambiar si el rol lo exige (autoload del servidor), registrando el cambio. AIDO no carga/descarga por API ni gestiona procesos. |
| Localidad | Solo loopback, más declaración explícita auditada para WSL/Docker. El resto es remoto. |
| Costo | Servidor de inferencia propio (self-hosted) = costo marginal cero automático, también en la LAN (IP privada literal); una URL pública, un gateway o un proxy/túnel marcado remoto nunca. Override del operador se preserva. |
| Settings | `runtime.local.enabled` + modo de proyecto `local`; `ollama` queda como sub-modo retrocompatible. |
| Capacidades de código | `code_edit`/`code_review` opt-in por modelo, no sembradas por runtime. |
| Ruta de modelo | Las correcciones transversales entran en este programa. |
| Concurrencia CLI + local | Sin cambio del slot pesado; la llamada local deja de necesitarlo (§4.4). |

## 4. Diseño

### 4.1 Identidad, localidad y costo (fuente única)

Módulo nuevo `local_control_center/agents/endpoint_locality.py` con funciones puras sobre la cuenta:

- **Identidad de catálogo del servidor:** `catalog_entry_for_account(account)` resuelve la entrada como
  `provider_catalog_api._catalog_for_account`: `providerCatalogId` y, si falta, fallback legado por
  `apiFormat=ollama` (cuentas creadas por el router Ollama, que no escriben el campo). `providerCatalogId` pasa a ser
  un campo **del servidor**: lo escriben solo from-catalog y `/local-endpoints`; si llega en la metadata del cliente
  (`model_gateway_api.py:149-158`, `provider_catalog_api.py:234-236`) se ignora.
- `endpoint_locality(account) -> Literal["loopback", "declared_local", "remote"]`, en este orden:
  1. `providerType == "gateway"` o `metadata.endpointKind == "remote"` ⇒ `remote` siempre (proxies y túneles en
     loopback, OmniRoute `scripts/setup_omniroute.py:148`, caso `remote-ollama` de
     `test_runtime_preflight.py:517,553`).
  2. host de `baseUrl` loopback (`ipaddress.is_loopback`, incluido IPv4-mapped; `localhost`) ⇒ `loopback`.
  3. declaración local vigente (abajo) ⇒ `declared_local`.
  4. cualquier otro caso ⇒ `remote` (R13).
- **Declaración local (WSL/Docker):** campo del servidor `localDeclaration {declaredBy, declaredAt, host}` escrito
  solo por `PUT /api/v1/local-endpoints/{id}/declare-local` (`require_write`, auditado). Solo se acepta si el host
  es una IP literal privada o link-local (RFC 1918, RFC 4193, 169.254/16, fe80::/10) o `host.docker.internal`; los
  nombres se resuelven de nuevo en cada invocación y la declaración se invalida si ya no resuelven a una IP
  aceptada (anti DNS rebinding). `metadata.endpointKind == "local"` enviado por el cliente no tiene efecto.
- `is_local_model_runtime(account)`: `providerType == "local"` ∧ localidad ≠ `remote`.
- `endpoint_network_scope(account) -> Literal["loopback", "private_network", "declared_local", "public"]`: igual
  que la localidad, pero distingue una IP **literal** privada o link-local (RFC 1918, RFC 4193, 169.254/16,
  fe80::/10) no declarada (`private_network`) de cualquier otro host (`public`, incluidos nombres DNS no
  declarados). Solo se usa para costo; la privacidad usa `endpoint_locality`.
- `is_self_hosted_inference(account)`: la entrada resuelta declara `pricing_source == "local_runtime_cost_only"`
  ∧ `providerType == "local"` ∧ `metadata.endpointKind != "remote"` ∧ `endpoint_network_scope ≠ "public"`. Nunca
  verdadero para `gateway`/`api`. Así una cuenta de catálogo local en la LAN (IP privada literal) conserva costo
  cero, pero una URL pública (p. ej. mal tipeada hacia un servicio pagado) cae al camino estándar de costo
  desconocido con aprobación (R5). Los endpoints Ollama remotos creados por el router Ollama (hoy
  `providerType=gateway`, `ollama/api.py:155-156`) mantienen su comportamiento actual (no exentos).
- **Política:** el kind de política sigue saliendo de `providerType` (`providers/factory.py:118-122`, no
  debilitable por metadata del cliente). La localidad solo decide qué interruptor aplica (`localEnabled` vs
  `remoteEnabled`) y la privacidad: `local_private`/`local_only` exigen `is_local_model_runtime`.
- **Transporte de credenciales:** prohibido enviar un bearer por `http://` a un host `remote`; falla cerrado con
  causa `insecure_credential_transport` (loopback y `declared_local` permitidos).

Consumidores que pasan a usar el módulo (y dejan de tener criterio propio): `ai_resource_manager`
(`_catalog_provider_locality`, privacidad `:994,1015,1355-1356`), `runtime_readiness.provider_workload_class`
(elimina la rama muerta de `deploymentMode`), `ollama/api._infer_kind`, `runtime_preflight` (exención de costo),
`model_router` (costo local, `:656`), `pricing_catalog`, los flags `allowRemote`/`networkRequired` de PO,
Developer, Architect y Security (`architect_agent.py:~582` y equivalentes), `product_owner_agent_contract.
is_product_owner_runtime` (una cuenta local exige modelos o modelo por defecto validado, como Ollama) y
`runtime_status` (rollup `local`).

**Cambio de comportamiento declarado:** una cuenta `providerType=local` cuyo host no es loopback ni está declarada
(p. ej. llama.cpp en la LAN) se trata como remota para privacidad y habilitación (`local_private` la rechaza;
requiere `runtime.remote.enabled`); su costo sigue siendo cero si es self-hosted en una IP privada literal.

### 4.2 Perfiles de runtime local en el catálogo

`ProviderCatalogEntry` gana un campo opcional `local_profile: LocalRuntimeProfile | None` (dataclass congelada).
Las rutas son relativas a la raíz del servidor (`baseUrl` sin el sufijo `/v1`).

| Campo | llama_cpp | lm_studio | vllm | local_openai_compatible |
|---|---|---|---|---|
| `default_base_url` | `http://127.0.0.1:8082/v1` | `http://127.0.0.1:1234/v1` | `http://127.0.0.1:8000/v1` | — (obligatoria) |
| `liveness_path` | `/health` | `/v1/models` | `/health` (solo liveness) | `/v1/models` |
| `health_requires_models` | no | sí | sí (id esperado en `/v1/models`) | sí |
| `model_state_source` | `openai_models_status` | `lm_studio_rest` | `single_model` | `none` |
| `multi_model` | router (autoload) | JIT + Auto-Evict | no (un proceso por modelo) | desconocido |
| `cold_start_timeout_s` | 180 | 180 | 60 (buffer de primer uso) | 180 |
| `disable_reasoning_hint` | `chat_template_kwargs.enable_thinking=false` | — | — | — |

Reglas de estado de carga (parseo defensivo; campo ausente ⇒ desconocido):

- `openai_models_status`: `GET /v1/models` → `data[].status.value` (`loaded`/`loading`/`unloaded`), observado en
  vivo en el router del operador; complemento `GET /props` (`role`, `max_instances`, `models_autoload`).
- `lm_studio_rest`: `GET /api/v1/models` → cargado si `len(loaded_instances) > 0`; si no existe (versiones
  anteriores a 0.4.0), `GET /api/v0/models` → `state == "loaded"`.
- `single_model`: el único id de `/v1/models` está cargado.

Notas verificadas en documentación oficial (fuentes en §11): llama-server responde `503` en `/health` mientras
carga y con `--api-key` solo `/health` es público (por eso la liveness no usa `/v1/models` y el listado usa la
credencial si existe); vLLM expone `/health`, `/v1/models` y `--api-key`, no corre nativo en Windows (WSL2 con
reenvío a `localhost` por defecto) y su `/health` no tiene semántica documentada de 503 mientras carga; LM Studio no
documenta `/health`. La capacidad `json_schema` nunca se asume por servidor: se prueba por modelo.

El catálogo agrega `lm_studio`, `vllm` y `local_openai_compatible` (`providerType=local`, `baseUrl` obligatoria,
bearer opcional), todas en la familia `openai_compatible`, ubicadas antes de la entrada genérica
`openai_compatible` (R11). `llama_cpp` conserva id y alias. Todas usan `OpenAICompatibleProvider`; el estado de
modelos usa `LocalModelStateReader` elegido por `model_state_source` de la entrada resuelta (R7, R8).

### 4.3 Salud, modelos, validación y selección

- **Guard:** `ModelGateway._provider_configuration` acepta toda cuenta `providerType=local` que la factory resuelva
  a un adapter y tenga `baseUrl`; la credencial se valida solo si hay `credentialRef` (fail-closed, R4) y aplica
  `runtime_policy_decision` con el kind de `providerType`.
- **Health check:** `GET liveness_path` (timeout corto) y, si `health_requires_models`, `GET /v1/models` con el id
  esperado. `503` o estado `loading` ⇒ resultado `model_loading` (no es falla, no abre el cooldown de 300 s). Un
  servidor caído deja de producir "sync OK con 0 modelos": el sync falla con `local_server_unreachable`.
- **Configuración por (cuenta, modelo):** tabla nueva `local_model_settings` (separada de `model_catalog`, que el
  resync reescribe; `migrations.py:1339-1367`, `provider_accounts.py:413-455`): `provider_id`, `model`,
  `is_default` (único por cuenta), `code_edit`, `code_review`, `operator_order`, procedencia por campo. El estado
  de carga **no** se persiste: es una caché volátil compartida de 10 s, no bloqueante para lecturas de UI (lecturas
  en paralelo con espera total acotada; servidor lento ⇒ desconocido).
- **Capacidades:** las del runtime son `chat` ∪ capacidades de sus modelos habilitados;
  `ai_resource_manager._catalog_capabilities` y `_catalog_model_profile` (`:1031-1060`) leen las capacidades por
  modelo para cuentas locales. Se elimina para entradas locales la siembra por runtime de
  `BUILD_REVIEW_SEEDED_CATALOG_IDS` (`provider_catalog_api.py:62-63,388-389`) y una migración retira las filas ya
  sembradas de `llama_cpp`.
- **Precio:** el sync marca `freeTier=true` y precio 0 cuando `is_self_hosted_inference`, preservando el override
  del operador en el resync.
- **Validación (`models.validate_runtime`) por (cuenta, modelo):** prueba el modelo resuelto para el rol (abajo),
  no el primero alfabético: chat real + salida JSON contra un schema mínimo, con `max_tokens` suficiente para
  modelos de razonamiento (1024) y el `disable_reasoning_hint` del perfil cuando existe. `model_loading` y un cold
  start no cuentan como fallo. El gate de sellado (30 min) y el de ejecución (24 h + fingerprint) de
  `runtime_team/validation.py` y `runtime_team/configuration.py` pasan a evaluarse por (providerId, modelo).
- **Selección determinista de modelo (toda cuenta local, incluido Ollama):** para un rol, `E` = modelos
  habilitados con las capacidades del rol y validación vigente.
  1. Modelos de `E` cargados: si hay alguno, el primero por desempate (por defecto, luego `operator_order`, luego
     id estable) — sin cambio de modelo.
  2. Si no, el por defecto si ∈ `E`; si no, el primero de `E` por `operator_order`. Si no está cargado, la llamada
     usa el autoload del servidor (presupuesto en §4.6) y se registra `local_model_switch`
     `{runtimeId, fromModel, toModel, role, reason}` en el hilo (si hay) y siempre en la auditoría de la ejecución.
  3. Afinidad entre roles: si otro rol del mismo run ya resolvió un modelo de `E`, se prefiere ese, para no
     serializar cold starts en un router `max_instances=1`.
  4. `E` vacío ⇒ bloqueo `local_model_not_validated` con remediación "Validar modelo"; sin fallback silencioso.
  El colapso ocurre en `_selection_models` (antes de construir `eligible` para `prevalidate_candidates`,
  `ai_resource_manager.py:434-466`), deja **un solo candidato por cuenta local** y registra cada modelo no elegido
  con `rejected.append(self._rejected(model, "local_model_not_selected"))`. En hilos con equipo, el modelo por rol
  se resuelve al sellar y se guarda en el campo sellado nuevo `roleModels: {role: model}` del equipo
  (`threads/coordinator.py:36-37` hoy solo sella `roleRuntimes` rol→providerId, y nunca desde el cliente); la
  ejecución usa el modelo sellado (con autoload si ya no está cargado) y `assess_runtime_team`
  (`runtime_team/validation.py:190-222`) recibe el modelo por rol para evaluar la validación por (provider, modelo). Estado
  de carga desconocido (genérico; Ollama en este programa) ⇒ se usa el por defecto.

### 4.4 Recursos

- Nueva clase de carga `local_model_call` (`host_resources/profiles.py`, `Literal WorkloadClass` en
  `host_resources/models.py:15-25`, OpenAPI): **liviana**, reserva de cliente 2 GiB, `gpu_required=False` (la VRAM
  la gestiona el servidor externo), sin slot pesado. Así `_parent_covers` la cubre con la lease del job padre
  (`agent_cli`, 8 GiB) sin reserva extra.
- **Conflicto con Unreal (R12) explícito:** se define `LOCAL_INFERENCE_CLASSES = {local_gpu_model,
  local_model_call}`.
  - `governor.py:150-157` compara la clase pedida contra el conjunto (no contra el literal) para el motivo
    `unreal_local_gpu_conflict`.
  - En el camino con lease prestada, `BranchAdmission.reserve()` llama a `governor.preview` con la clase del
    **padre** (`branch_admission.py:172-179`), así que el conflicto no se dispararía. Por eso `reserve()` evalúa
    primero Unreal con la clase **solicitada por el hijo**: si pertenece al conjunto, UnrealEditor está activo y la
    política bloquea GPU local con Unreal, devuelve `unreal_local_gpu_conflict` aunque `_parent_covers` acepte el
    préstamo. `runtime_readiness._readiness_resource_request`, que también reescribe la clase del hijo, aplica el
    mismo chequeo previo.
  - `executions/workloads.py:39-40,72-76` y `jobs_approvals/worker.py:349-350` clasifican para reserva (no emiten
    motivos de admisión): ganan una rama `local_model_call` para no degradar la clasificación.
- `provider_workload_class` devuelve `local_model_call` para cuentas `is_local_model_runtime` servidas por un
  proceso externo observado (todas las de este programa y Ollama). `local_gpu_model` queda para procesos de modelo
  que AIDO lance (ninguno hoy).
- **Concurrencia por endpoint:** lease durable por cuenta en SQLite (`BEGIN IMMEDIATE`, TTL y fencing, patrón de
  `quota_manager`), por defecto 1 llamada simultánea (configurable por cuenta), adquirida en el punto común de
  invocación del provider; cada ejecución corre en su propio proceso runner (`executions/dispatcher.py:40-54`), por
  eso no sirve un semáforo en memoria. Espera acotada; al vencer ⇒ causa `local_endpoint_busy`.
- AIDO no reserva ni mide VRAM del servidor externo; un fallo de carga del servidor se clasifica
  `local_model_load_failed` con causa legible.

### 4.5 Settings y política

- `runtime.local.enabled` (bool; al migrar toma el valor actual de `runtime.ollama.enabled`).
- `project.runtime.defaultMode` gana `local` (cualquier runtime local habilitado). `ollama` se mantiene: equivale a
  `local` restringido a Ollama. `runtime.ollama.enabled` pasa a sub-interruptor que solo restringe (Ollama
  habilitado ⇔ `local.enabled ∧ ollama.enabled`).
- Readiness y `runtime_policy_decision` eligen el interruptor por localidad (`is_local_model_runtime` ⇒
  `localEnabled`; resto no-CLI ⇒ `remoteEnabled`), eliminando la contradicción readiness/política.

### 4.6 Ruta de ejecución de modelo

Aplica a `OpenAICompatibleProvider` (y a `OllamaProvider` donde corresponda):

1. Body explícito `{model, messages, temperature, max_tokens, stream:false}` (+ `response_format` si aplica); sin
   `metadata` ni alias camelCase.
2. Timeout efectivo = `min(timeoutSeconds + cold_start (si hay cambio de modelo), runtime.local.maxCallSeconds,
   remaining_execution_timeout())` (`process_supervision/context.py:134-144`). Si el presupuesto restante no alcanza
   para el cold start, falla antes de invocar con `insufficient_time_for_model_load`; nunca 60 s fijos.
3. `response_format: {type: json_schema, ...}` cuando el modelo tiene esa capacidad validada; si no, `json_object`
   o instrucción de JSON en el prompt (comportamiento actual).
4. Antes de parsear: se descarta `reasoning_content` y bloques `<think>…</think>` con un strip lineal y acotado; se
   retiran fences. La sonda de preflight de cuentas locales usa `max_tokens` 64 y el `disable_reasoning_hint`, y
   una respuesta con `finish_reason=length` y razonamiento no vacío cuenta como viva (no como recibo fallido;
   `runtime_preflight.py:47,224`, `model_gateway.py:393-404`).
5. **Redacción:** los agentes no reciben el `RuntimeExecutionResult` del adapter: consumen el dict `toolCall` que
   devuelve `ToolBroker.evaluate_tool_call` (`agents/tool_broker.py:743`) y releen el artifact ya redactado
   (`developer_agent.py:346-350` `_model_output_text`, `product_owner_agent.py:1071-1078` `_runtime_output_text`,
   `architect_agent.py:723-730` `_artifact_text`, `workflows/issue_to_patch_runner.py:180`). Canal nuevo: un
   almacén transitorio en proceso `transient_model_output` (clave = id de la tool call del broker; lectura única
   con `pop`; TTL corto; tope de tamaño) donde el adapter deja el texto crudo. No forma parte de ningún dict
   devuelto, así que ningún llamador puede persistirlo por accidente. Esos cuatro parsers lo prefieren y, si no
   existe (runtimes CLI, otro proceso), vuelven a leer el artifact como hoy. El artifact, los logs,
   `agent_tool_calls` y la evidencia siguen redactados. Las razones de las causas nuevas (§4.6.8) se redactan como
   hoy (`model_gateway.py:392`). Supuesto a verificar en el plan: para runtimes de modelo, la llamada
   adapter→broker→agente ocurre en el mismo proceso.
6. `usage` (prompt/completion/total) y latencia se propagan al ledger con `usage_source` real; sin uso reportado ⇒
   `unknown` con `NULL` (R5).
7. El Architect del product loop recibe el modelo de `resourceDecision`; toda cuenta local tiene modelo por
   defecto (§4.3).
8. Causas clasificadas: `local_server_unreachable`, `model_loading`, `local_model_load_failed`,
   `local_auth_required` (401/403), `context_length_exceeded`, `insecure_credential_transport`,
   `local_endpoint_busy`, `insufficient_time_for_model_load`, `local_model_not_validated`.
9. Lectura de respuesta acotada (tope de bytes por `Content-Length` o lectura limitada) para servidores locales no
   confiables.

### 4.7 Endpoints, detección y API

- **Detección:** `POST /api/v1/local-runtimes/discover` (`require_write`) sondea solo `127.0.0.1` en puertos
  conocidos (8080, 8082; 1234; 8000; 1337; 5001) con `GET`, timeout de 2 s y cuerpo/arrays acotados; no acepta host
  del cliente ni envía credenciales. Identifica el servidor por firma best-effort (`/props.role`,
  `owned_by=llamacpp`, `/api/v0/models` o `/api/v1/models`, `owned_by=vllm`); una firma `/v1/models` desconocida se
  sugiere como `local_openai_compatible` solo con confirmación explícita del operador. Devuelve sugerencias
  `{catalogId, baseUrl, server, models[], alreadyConfigured}`; nunca crea cuentas.
- **Endpoints locales:** router genérico `/api/v1/local-endpoints` (listar, crear, editar, sondear, sincronizar,
  declarar local, borrar) extraído de `ollama/api.py`; `/api/v1/ollama/endpoints` queda como envoltorio
  compatible. Al generalizar se corrigen: `provider_family` guardada como id del endpoint
  (`provider_accounts.py:279`, `ollama/api.py:376-393`), la preservación del `enabled` del operador y la
  reconciliación de modelos ausentes.
- **Borrado:** `DELETE /api/v1/local-endpoints/{id}` es un tombstone transaccional nuevo (hoy no existe borrado de
  cuentas): limpia `runtime_installations`, `model_catalog` y `local_model_settings`; responde `409` con las
  referencias si hay equipos sellados o role policies que la usan (sin reasignación automática); preserva ledger,
  auditoría y evidencia.
- **From-catalog:** acepta `baseUrl` e `instanceId`; un re-guardado ya no resetea la URL; escribe
  `providerCatalogId` como campo del servidor.
- **Remediaciones:** payload derivado del perfil (sin campo de API key si no hay auth); acciones "Probar de nuevo",
  "Abrir asistente", "Validar modelo"; textos por causa ("Inicia el servidor", "Cargando modelo", "Requiere token",
  y para WSL: revisar `.wslconfig` y el firewall).
- **Estado:** `GET /api/v1/runtime/providers` incluye las cuentas locales con `models` (id, estado de carga,
  habilitado, por defecto, capacidades) y un bucket `local` en el rollup (la clave `ollama` se mantiene).

### 4.8 UI (web)

- Tarjetas de catálogo: llama.cpp, LM Studio, vLLM, "Servidor local OpenAI-compatible" (espejo TS sin drift).
- Botón "Detectar runtimes locales": lista las sugerencias y abre el asistente prellenado.
- Asistente para locales: Endpoint (URL editable prellenada, nombre de instancia, token opcional con aviso si el
  host no es loopback, acción "corre en este equipo (WSL/Docker)" que llama al endpoint auditado de declaración) →
  Modelos (estado de carga, habilitados, por defecto, capacidades por modelo) → Validar (validación real del modelo
  por defecto) → Listo.
- Panel "Endpoints locales" (generaliza `OllamaEndpointsPanel`): nombre, tipo, URL, salud, modelo cargado, n.º de
  modelos; acciones sondear, sincronizar, editar, borrar (con el detalle del 409).
- Panel del equipo del hilo: los runtimes locales validados aparecen como candidatos con chip del modelo cargado
  (desde la caché compartida) y el modelo resuelto por rol.
- Línea de tiempo del hilo: evento `local_model_switch` legible.
- Modal de salud/remediación con las causas locales de §4.6.
- Copy por `t()` con claves bilingües (en ≠ es), sin estilos prohibidos por los guardrails visuales.

## 5. Datos y migración

- Migración nueva (fase siguiente a la última registrada):
  - setting `runtime.local.enabled` sembrado desde `runtime.ollama.enabled`;
  - tabla `local_model_settings` (§4.3);
  - campos del servidor `providerCatalogId` y `localDeclaration` en la cuenta (fuera de la metadata del cliente);
  - `runtime_installations` por instancia local al crear la cuenta (no la fila compartida de la familia,
    `runtime_status.py:991`);
  - retiro de las capacidades `code_edit`/`code_review` sembradas por runtime para `llama_cpp`.
- Metadata sellada del equipo del hilo: `roleModels: {role: model}` junto a `allowedRuntimes`/`roleRuntimes`
  (mismo sellado servidor, sin aceptarse del cliente); equipos sellados antes de la migración no lo tienen y usan la
  selección determinista en ejecución.
- Sin migración destructiva de settings existentes; `ollama` sigue siendo valor válido.
- Contratos y OpenAPI: `RuntimeMode += "local"` (`agents/contracts.py:45`); `WorkloadClass += "local_model_call"`
  (`host_resources/models.py:15-25`, usado en `executions/router.py:36`); las causas nuevas en su `Literal`
  concreto; el tipo de evento del hilo es `str` abierto (`threads/contracts.py:139`), sin cambio de contrato.
- `local_model_switch` se agrega a `_NON_INDEXED_EVENT_TYPES` (`threads/similarity.py:72`) con su test, para no
  repetir la regresión corregida en `4b467e15`.

## 6. Pruebas

- **Dobles de prueba** en `tests_py/fakes/local_llm_servers.py` (HTTP en `127.0.0.1:0`):
  - router llama.cpp: fixtures capturadas del servidor real en `:8082` (`/props`, `/v1/models`, `/models`) en la
    rebanada 1; `/health` con 503 mientras carga; autoload con latencia simulada; `usage`; 401 con api key salvo
    `/health`; modelo que emite `reasoning_content`;
  - LM Studio: `/v1/models`, `/api/v1/models` con `loaded_instances`, `/api/v0/models` con `state`;
  - vLLM: `/health`, un solo modelo en `/v1/models`;
  - genérico: `/v1/models` + chat.
  Cada rebanada agrega tests que fallan antes y pasan después.
- **Regresiones obligatorias:** `test_local_runtime_price_exemption_does_not_treat_loopback_proxy_as_free` (sus 3
  casos: local-ollama exento, remote-ollama y proxy loopback no exentos), `test_product_owner_model_eligibility`
  (+ caso local), suites de Ollama, runtime-team, product loop, preflight y gates `test:py`.
- **Nuevos, por tema:**
  - recursos: lease `agent_cli` activa + 20 GiB libres + `minFree` 16 ⇒ `local_model_call` admitida sin reserva
    extra (patrón `test_preflight_borrows_job_reservation_without_extra_memory_or_release`); Unreal activo ⇒
    `unreal_local_gpu_conflict` también cuando `_parent_covers` acepta el préstamo (en `BranchAdmission.reserve` y
    en el preview de readiness); lease de concurrencia con dos conexiones concurrentes;
  - costo: loopback e IP privada literal exentos; URL pública bajo `local_openai_compatible`/`lm_studio`/`vllm` no
    exenta; `endpointKind=remote` y `gateway` nunca exentos;
  - capacidades: tras la migración una cuenta `llama_cpp` existente pierde `code_edit`/`code_review` sembrados por
    runtime salvo opt-in por modelo, y `_catalog_capabilities` lee las capacidades por modelo;
  - localidad/privacidad: gateway loopback rechazado por `local_private`; llama.cpp no loopback no declarada
    remota; declaración solo con IP privada y resolución repetida; `providerCatalogId` del cliente ignorado; bearer
    por http a remoto ⇒ `insecure_credential_transport`;
  - selección: cargado, varios cargados con desempate, por defecto con evento, afinidad entre roles, vacío bloquea,
    rechazos con `local_model_not_selected`, Jev con un candidato;
  - validación: gate por (provider, modelo); `model_loading` no es fallo;
  - ejecución: `max_tokens` real, timeout con deadline casi agotado, cada causa con razón redactada, sonda con
    razonamiento; canal transitorio de extremo a extremo: un modelo que emite `<think>` o contenido con patrón
    secreto-símil sigue parseando como JSON válido en Developer, PO, Architect e issue-to-patch, y el texto crudo
    nunca llega a SQLite, `agent_tool_calls` ni logs;
  - equipo: `roleModels` se sella en el servidor, se ignora si viene del cliente y la validación se evalúa por
    (provider, modelo sellado);
  - borrado: tombstone, 409 con referencias, sin huérfanos.
- **Playwright:** detección + asistente contra un doble local, panel de endpoints (incluido borrado), candidato en
  el equipo del hilo y evento de cambio de modelo.
- **Validación en vivo final:** `llama-server` real en `:8082` — agregar, validar, hilo con PO en llama.cpp.

## 7. Entrega por rebanadas

1. Identidad/localidad/costo (§4.1), guard y health (§4.3 parcial), readiness/settings (§4.5) y recursos (§4.4:
   `local_model_call`, conflicto R12 por conjunto, lease de concurrencia) → llama.cpp `healthy` y ejecutable.
   Touchpoints de recursos: `profiles.py`, `models.py`, `branch_admission.py`, `governor.py`,
   `executions/workloads.py`, `jobs_approvals/worker.py`. Captura de fixtures del servidor real.
2. Ruta de modelo (§4.6). Touchpoints: `providers/base.py`, `providers/openai_compatible.py`,
   `providers/ollama.py`, `runtime_adapters/provider_factory.py`, `runtime_adapters/models.py`,
   `runtime_adapters/common.py`, `agents/tool_broker.py`, `process_supervision/context.py` (lectura del deadline),
   `runtime_preflight.py` (sonda), `developer_agent.py`, `product_owner_agent.py`, `architect_agent.py`,
   `workflows/issue_to_patch_runner.py`, `product_loop/coordinator.py` (modelo del Architect) y el almacén
   `transient_model_output` nuevo.
3. `local_model_settings`, capacidades por modelo (`ai_resource_manager._catalog_capabilities`/
   `_catalog_model_profile`), validación por (provider, modelo) en `runtime_team/validation.py` y
   `configuration.py`, sellado de `roleModels` en `threads/coordinator.py`, selección determinista con colapso en
   `_selection_models`, afinidad y evento.
4. Catálogo (`lm_studio`, `vllm`, `local_openai_compatible`), perfiles, lector de estado LM Studio, detección,
   router de endpoints locales con declaración y borrado, remediaciones.
5. UI y Playwright.
6. Validación en vivo con el `llama-server` real.

## 8. Fuera de alcance

Arrancar/detener procesos de servidores; carga/descarga explícita por API (`/models/load`, `/api/v1/models/load`);
streaming SSE; tool calling y edición por diff del Developer (iniciativa aparte de paridad con CLI); embeddings;
estado de carga de Ollama (`/api/ps`); exención de costo para endpoints Ollama remotos del router Ollama (se
mantiene el comportamiento actual); entradas para Docker Model Runner (sin auth de aplicación), SGLang, TGI y
Lemonade; medición de VRAM.

## 9. Riesgos y supuestos

- **LM Studio y vLLM no están instalados en el host:** se validan contra dobles construidos desde la documentación
  oficial; riesgo residual declarado hasta una prueba real.
- **llama.cpp es rolling release:** `status` de `/v1/models` y los campos de `/props` se observan en vivo pero no
  están en la documentación oficial; se parsean defensivamente (ausente ⇒ desconocido) y la detección por firma es
  best-effort.
- **vLLM:** `/health` puede responder 200 con el engine colgado (sin semántica documentada de carga; gap upstream);
  por eso la salud exige también `/v1/models` y el primer uso tiene buffer de arranque. `--api-key` no protege
  `/invocations`.
- **Cambio de modelo compartido:** preferir el cargado y la afinidad entre roles reducen, pero no eliminan, el
  desalojo del modelo de otra sesión; `local_model_switch` lo hace visible.
- **Cold starts serializados:** varios roles con modelos distintos sobre un router `max_instances=1` pagan un cold
  start por cambio; la afinidad lo mitiga cuando `E` lo permite.
- **LAN:** una cuenta local no loopback pasa a remota para privacidad (§4.1) y no puede usar bearer por http.
- **WSL2 (verificado, Microsoft Learn):** el modo NAT reenvía `localhost` al host por defecto
  (`localhostForwarding=true`); depende de `.wslconfig` y del firewall, que la remediación menciona.

## 10. Restricciones heredadas (aplican a todo el programa)

R1 fail-closed sin mocks en producción; R2 readiness = cuenta habilitada + health explícito (TTL 300 s); R3
validación por hilo 30 min/24 h; R4 solo `credentialRef`, bearer opcional para locales, ref que no resuelve falla
cerrado; R5 costo desconocido ≠ gratis, uso desconocido = `NULL`; R6 habilitación por `runtime_policy_decision`;
R7 binding modelo↔runtime por familia en `MODEL_RUNTIME_TOOLS`; R8 cuentas endpoint-scoped; R9 ranking por
tiers y comodines; R10 allowlist del hilo solo restringe; R11 la entrada genérica `openai_compatible` es la última
de su familia; R12 no matar procesos ajenos y conflicto GPU con Unreal; R13 ante la duda, remoto; R14 User-Agent
de gateways; R15 i18n bilingüe, `@author Rodrigo Mason`, `Literal` + OpenAPI regenerado, LF.

## 11. Fuentes

- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ;
  https://huggingface.co/blog/ggml-org/model-management-in-llamacpp
- LM Studio: https://lmstudio.ai/docs/developer/rest ; https://lmstudio.ai/docs/developer/rest/endpoints ;
  https://lmstudio.ai/docs/developer/rest/list ; https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict ;
  https://lmstudio.ai/docs/developer/openai-compat/structured-output
- vLLM: https://docs.vllm.ai/en/latest/serving/online_serving/ ;
  https://docs.vllm.ai/en/latest/features/structured_outputs/ ;
  https://docs.vllm.ai/en/stable/getting_started/installation/gpu/ ;
  https://docs.vllm.ai/en/stable/usage/security/
- WSL2: https://learn.microsoft.com/windows/wsl/wsl-config ; https://learn.microsoft.com/windows/wsl/networking
