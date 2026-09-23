# Runtimes locales de primera clase: llama.cpp, LM Studio, vLLM y servidor local OpenAI-compatible

**Fecha:** 2026-09-23
**Estado:** Diseño aprobado por el operador (2026-09-23). Pendiente de revisión de la spec y plan.
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
2. Con ~20 GiB de RAM libres (situación normal del host con un modelo residente) una llamada a llama.cpp no queda
   en `resource_wait`.
3. LM Studio, vLLM y el servidor genérico pasan el mismo ciclo contra dobles de prueba fieles a su documentación
   oficial (no están instalados en el host; ver §9).
4. Ningún flujo existente de Ollama, del equipo por hilo ni del product loop regresa.

## 2. Estado actual (verificado 2026-09-23)

Exploración de 7 áreas con verificación adversarial (evidencia `archivo:línea`) y sondeo en vivo.

- `llama_cpp` existe solo como preset de catálogo (`agents/provider_catalog.py:411-426`, commit `0de3956e`): no
  aparece en `GET /api/v1/runtime/providers` ni en `GET /api/v1/runtime/team-candidates`.
- **Bloqueo 1 — salud:** `ModelGateway._provider_configuration` rechaza todo `providerType=local` que no sea Ollama
  (`agents/model_gateway.py:595-601`, "Unsupported local model provider"). Sin evidencia `healthy`,
  `_api_provider_status` queda `executable=false` y ni el gestor de recursos ni el router lo eligen.
- **Bloqueo 2 — costo:** el preflight solo exime de "costo desconocido" a `apiFormat=ollama`
  (`agents/runtime_preflight.py:167-183,351-370`); el sync genérico deja precios `NULL` y `free_tier=0`
  (`provider_catalog_api.py:438-456`) y un resync pisa cualquier marca manual.
- **Bloqueo 3 — habilitación:** readiness gatea lo no-CLI con `remoteEnabled` y solo Ollama usa `ollamaEnabled`
  (`agents/runtime_readiness.py:160-166`); el modo de proyecto `ollama` exige id Ollama
  (`runtime_integrations/repository.py:289-300`). No existe un modo "solo local" que admita llama.cpp.
- **Bloqueo 4 — recursos:** una URL loopback se clasifica `local_gpu_model` (`runtime_readiness.py:144-148`):
  carga pesada de 16 GiB y el único slot pesado (`host_resources/profiles.py:121-130`,
  `settings/registry.py:185-193`). Con `minFreeMemoryGiB=12` exige 28 GiB libres y compite con el propio job
  `agent_cli`; el modelo ya está residente en el servidor externo, así que la reserva cuenta dos veces la misma
  memoria.
- **Localidad inconsistente:** 3 clasificadores divergentes (`ai_resource_manager.py:152-164`,
  `runtime_readiness.py:144-148`, `ollama/api.py` `_infer_kind`) y uno con rama muerta (`deploymentMode` "local"
  no existe en `model_gateway_models.py:30-36`); los contratos de agente tratan la familia `openai_compatible`
  como remota (allowRemote/networkRequired), aunque el endpoint sea loopback.
- **Ruta de modelo (afecta también a Ollama y a todo OpenAI-compatible):** timeout fijo de 60 s
  (`providers/openai_compatible.py:211`, `providers/ollama.py:183`) que ignora el `timeoutSeconds` pedido;
  `max_tokens` serializado como `maxTokens` junto a `metadata` (`openai_compatible.py:204`); sin
  `response_format`; la redacción de secretos se aplica antes de parsear y corrompe JSON de parches
  (`runtime_adapters/provider_factory.py:246`, `shared/redaction.py:20-36`); el uso de tokens y la latencia se
  descartan en la ruta del broker (`provider_factory.py:246-266`); el Architect del product loop no recibe
  modelo (`product_loop/coordinator.py:4557-4571`); el modelo por defecto solo existe para Ollama; los bloques de
  razonamiento (`<think>`, `reasoning_content`) no se descartan antes de parsear.
- **Selección multi-modelo:** los candidatos se forman por (runtime, modelo); con varios modelos habilitados del
  mismo runtime local, Jev debe elegir entre N candidatos y bloquea con `confidence_below_threshold` (mismo
  fenómeno corregido para equipos multi-runtime en `e8a31dcc`).
- **UI:** URL de llama.cpp fija (el asistente no la muestra y un re-guardado la resetea), sin multi-instancia en
  la UI (el backend sí la soporta con `instanceId`), sin panel de endpoints genérico, remediación que pide una API
  key inexistente (`remediations/payloads.py:234-278`).
- **En vivo:** Ollama apagado (`:11434` rechaza), `llama-server` sano en `:8082` en router mode
  (`/props`: `role=router`, `max_instances=1`, `models_autoload=true`); `/v1/models` expone `status.value`
  (`loaded`/`unloaded`) por modelo.

## 3. Decisiones del operador (2026-09-23)

| Tema | Decisión |
|---|---|
| Enfoque | Familia de protocolo `openai_compatible` + eje de localidad + perfil de sondeo por servidor (enfoque A). Se descartan un adapter/familia por servidor (≈10 puntos por runtime) y enrutar por gateway (rompe `local_private` y oculta el estado del modelo). |
| Runtimes | llama.cpp, servidor local OpenAI-compatible genérico, LM Studio, vLLM (vía WSL). |
| Modelos en router/JIT | Preferir el modelo cargado; cambiar si el rol lo exige (autoload del servidor), registrando el cambio. AIDO no carga/descarga por API ni gestiona procesos. |
| Localidad | Solo loopback, más opt-in explícito por cuenta (`endpointKind=local`) para WSL/Docker. El resto es remoto. |
| Costo | Servidor de inferencia propio (self-hosted) = costo marginal cero automático; un gateway nunca, aunque sea loopback. Override del operador se preserva. |
| Settings | `runtime.local.enabled` + modo de proyecto `local`; `ollama` queda como sub-modo retrocompatible. |
| Capacidades de código | `code_edit`/`code_review` opt-in por modelo, no sembradas por runtime. |
| Ruta de modelo | Las correcciones transversales entran en este programa. |
| Concurrencia CLI + local | Sin cambio del slot pesado; la llamada local deja de usarlo (§4.4). |

## 4. Diseño

### 4.1 Eje de localidad (fuente única)

Módulo nuevo `local_control_center/agents/endpoint_locality.py` con funciones puras sobre la cuenta
(`provider_accounts`):

- `endpoint_locality(account) -> Literal["loopback", "declared_local", "remote"]`:
  - `loopback`: host de `baseUrl` es `localhost`, `127.0.0.0/8` o `::1` (por `ipaddress.is_loopback`, no por lista
    fija).
  - `declared_local`: `metadata.endpointKind == "local"` fijado por el operador (WSL con IP propia, Docker con
    `host.docker.internal`). Se registra en auditoría quién lo declaró.
  - `remote`: todo lo demás, incluidos hosts LAN (regla R13: ante la duda, remoto).
- `is_local_model_runtime(account) -> bool`: `providerType == "local"` y localidad ≠ `remote`.
- `is_self_hosted_inference(account) -> bool`: la entrada de catálogo (`metadata.providerCatalogId`) declara
  `pricing_source` ∈ {`local_runtime_cost_only`, `operator_managed_remote_runtime`} y `providerType == "local"`.
  Nunca verdadero para `gateway`.

Consumidores que pasan a usar el helper (reemplazan sus criterios propios): `ai_resource_manager` (privacidad y
locality), `runtime_readiness.provider_workload_class` (elimina la rama muerta de `deploymentMode`),
`ollama/api._infer_kind` (sigue estampando `endpointKind`, ahora vía helper), `runtime_preflight` (costo),
`model_router` (costo local), flags `allowRemote`/`networkRequired` de PO, Developer, Architect y Security
(dejan de derivarse de la familia), y `runtime_status` (rollup `local`).

**Cambio de comportamiento declarado:** una cuenta `providerType=local` cuyo host no es loopback ni está declarada
local (p. ej. un Ollama o llama.cpp en la LAN) se trata como remota para privacidad y habilitación
(`local_private` la rechaza; requiere `runtime.remote.enabled`). Su costo sigue siendo cero si es self-hosted.

### 4.2 Perfiles de runtime local en el catálogo

`ProviderCatalogEntry` gana un campo opcional `local_profile: LocalRuntimeProfile | None` (dataclass congelada):

| Campo | llama_cpp | lm_studio | vllm | local_openai_compatible |
|---|---|---|---|---|
| `default_base_url` | `http://127.0.0.1:8082/v1` | `http://127.0.0.1:1234/v1` | `http://127.0.0.1:8000/v1` | — (obligatoria) |
| `liveness_path` | `/health` (raíz del servidor) | `/v1/models` | `/health` | `/v1/models` |
| `model_state_source` | `openai_models_status` (`/v1/models` → `status.value` en router mode) | `lm_studio_rest` (`/api/v1/models`, fallback `/api/v0/models` → `state`) | `single_model` (el único id de `/v1/models`) | `none` |
| `multi_model` | router (autoload, `max_instances` en `/props`) | JIT + Auto-Evict | no (un proceso por modelo) | desconocido |
| `cold_start_timeout_s` | 180 | 180 | 0 (modelo fijo al arrancar) | 180 |
| `supports_json_schema` | por evidencia | por evidencia | por evidencia | por evidencia |

Las rutas del perfil son relativas a la raíz del servidor (`baseUrl` sin el sufijo `/v1`).

Notas verificadas en documentación oficial (fuentes en §11): llama-server responde `503` en `/health` mientras
carga; con `--api-key` solo `/health` es público (por eso la liveness no usa `/v1/models` en llama.cpp y el
listado usa la credencial si existe); vLLM expone `/health`, `/v1/models`, `--api-key` y no corre nativo en
Windows (WSL2 con reenvío a `localhost`); LM Studio no documenta `/health` y expone estado de carga en su REST
nativa (`/api/v0/models` campo `state`; `/api/v1/models` desde 0.4.0). La capacidad `json_schema` nunca se asume
por servidor: se prueba por modelo (LM Studio documenta que modelos < 7B pueden fallarla).

El catálogo agrega las entradas `lm_studio`, `vllm` y `local_openai_compatible` (esta última `providerType=local`,
`baseUrl` obligatoria, bearer opcional), todas en la familia `openai_compatible` y ubicadas antes de la entrada
genérica `openai_compatible` (R11). `llama_cpp` conserva su id y alias. Los modelos de todas ellas pertenecen al
adapter `OpenAICompatibleProvider`; solo el estado de modelos de LM Studio usa una estrategia propia
(`LocalModelStateReader` por `model_state_source`), seleccionada por `metadata.providerCatalogId` (R7, R8).

### 4.3 Salud, validación y selección

- **Guard:** `ModelGateway._provider_configuration` acepta toda cuenta `providerType=local` que la factory
  resuelva a un adapter y tenga `baseUrl`; la credencial se valida solo si hay `credentialRef` (fail-closed, R4) y
  se aplica `runtime_policy_decision` con el kind derivado de la localidad.
- **Health check:** `GET liveness_path` (timeout corto) + listado de modelos. `503` o estado `loading` →
  resultado `model_loading` (no es falla, no abre el cooldown de 300 s del backoff) y reintento en el ciclo normal.
  Un servidor caído deja de producir "sync OK con 0 modelos": el sync falla con causa `local_server_unreachable`.
- **Modelos:** el sync guarda `status` de carga (cuando el perfil lo expone), `contextWindow` si viene, y
  `freeTier=true` + precio 0 si `is_self_hosted_inference` (preservando override del operador en resync). El
  operador elige en el asistente los modelos habilitados, **un modelo por defecto** (obligatorio) y, por modelo,
  las capacidades opt-in `code_edit` y `code_review`. Las capacidades del runtime son `chat` ∪ capacidades de sus
  modelos habilitados (reemplaza la siembra por runtime de `BUILD_REVIEW_SEEDED_CATALOG_IDS` para entradas locales).
- **Validación (`models.validate_runtime`):** prueba el modelo que la selección usaría (cargado elegible o por
  defecto; §abajo), no el primero alfabético (`runtime_team/probe.py:197-207`): chat real + salida JSON contra un
  schema mínimo. La evidencia se guarda por (cuenta, modelo) con las mismas vigencias que hoy (30 min para sellar,
  24 h para ejecutar, R3). El timeout de validación suma `cold_start_timeout_s`.
- **Selección determinista de modelo (aplica a toda cuenta local, incluido Ollama):** para un rol, `E` = modelos
  habilitados con las capacidades del rol y evidencia de validación vigente.
  1. Si el modelo cargado ∈ `E` → ese (sin cambio de modelo). El estado de carga se lee del perfil con una caché
     corta (10 s); si la lectura falla, el estado es desconocido.
  2. Si no, el modelo por defecto ∈ `E` → ese; si no está cargado, la llamada usa el autoload del servidor con
     presupuesto `cold_start_timeout_s` y se registra un evento `local_model_switch`
     `{runtimeId, fromModel, toModel, reason}` en el hilo (si la llamada pertenece a uno) y siempre en la auditoría
     de la ejecución.
  3. Si no, el primero de `E` en el orden del operador (mismo evento si implica cambio).
  4. Si `E` está vacío → bloqueo `local_model_not_validated` con remediación "Validar modelo".
  El resultado es **un solo candidato por runtime local**, así Jev nunca elige entre modelos del mismo runtime (evita
  `confidence_below_threshold`). Estado de carga desconocido (genérico, Ollama en este programa) → se usa el por
  defecto.

### 4.4 Recursos

- Nueva clase de carga `local_model_call` en `host_resources/profiles.py`: **liviana**, reserva de cliente 2 GiB
  (igual que `remote_llm_light`), `gpu_required=True` (conserva el conflicto con Unreal de R12), sin el slot pesado.
- `provider_workload_class` devuelve `local_model_call` para cuentas `is_local_model_runtime` servidas por un
  proceso externo observado (todas las de este programa y Ollama). `local_gpu_model` queda para procesos de modelo
  que AIDO lance (ninguno hoy).
- Límite de concurrencia por endpoint local: por defecto 1 llamada simultánea por cuenta (configurable en la
  cuenta), aplicado en el adapter del proceso que ejecuta la llamada (semáforo por cuenta; las llamadas de agentes
  vía ToolBroker no pasan por admisión propia, solo la del job). Evita thrash del router al alternar modelos entre
  llamadas paralelas del mismo proceso.
- El cambio de clase afecta el chequeo `resourceAdmissible` del preflight (hoy un candidato local exige el slot
  pesado que ya ocupa el propio job `agent_cli`) y la admisión de ejecuciones del Model Gateway.
- AIDO no reserva ni mide VRAM del servidor externo (lo gestiona el servidor); un fallo de carga del servidor se
  clasifica (`local_model_load_failed`) y se reporta con causa legible.

### 4.5 Settings y política

- `runtime.local.enabled` (bool, default = valor actual de `runtime.ollama.enabled` al migrar).
- `project.runtime.defaultMode` gana `local` (cualquier runtime local habilitado). `ollama` se mantiene: equivale
  a `local` restringido a `apiFormat=ollama`. `runtime.ollama.enabled` pasa a sub-interruptor que solo restringe
  (Ollama habilitado ⇔ `local.enabled ∧ ollama.enabled`).
- Readiness y `runtime_policy_decision` eligen el flag por localidad: local → `localEnabled`; remoto →
  `remoteEnabled`. Se elimina la contradicción readiness/política.
- Enum `RuntimeMode` y contratos OpenAPI actualizados y regenerados (R15).

### 4.6 Ruta de ejecución de modelo

Aplica a `OpenAICompatibleProvider` (y a `OllamaProvider` donde corresponda):

1. Body explícito `{model, messages, temperature, max_tokens, stream:false}` (+ `response_format` si aplica); sin
   `metadata` ni alias camelCase.
2. Timeout = `timeoutSeconds` de la ejecución (+ `cold_start_timeout_s` si hay cambio de modelo) con tope
   configurable `runtime.local.maxCallSeconds`; nunca 60 s fijos.
3. `response_format: {type: json_schema, ...}` cuando el perfil y la evidencia del modelo lo soportan; si no,
   `json_object` o instrucción de JSON en prompt (comportamiento actual).
4. Antes de parsear: se descarta `reasoning_content` y bloques `<think>…</think>`; se retiran fences de código.
5. La redacción de secretos se aplica al contenido persistido (artifact, logs, evidencia), nunca al texto que se
   parsea en memoria.
6. `usage` (prompt/completion/total) y latencia se propagan al ledger con `usage_source` real; si el servidor no
   reporta uso, `unknown` con valores `NULL` (R5).
7. El Architect del product loop recibe el modelo de `resourceDecision`; todo runtime local tiene modelo por
   defecto (§4.3).
8. Errores clasificados: `local_server_unreachable`, `model_loading`, `local_model_load_failed`,
   `local_auth_required` (401/403), `context_length_exceeded`.

### 4.7 Endpoints, detección y API

- **Detección:** `POST /api/v1/local-runtimes/discover` sondea con `GET` y timeout de 2 s solo puertos loopback
  conocidos (8080, 8082 llama.cpp/llamafile/LocalAI; 1234 LM Studio; 8000 vLLM; 1337 Jan; 5001 KoboldCpp),
  identifica el servidor por firma (`/props.role`/`owned_by=llamacpp`, `/api/v0/models`, `owned_by=vllm`,
  `/v1/models` genérico) y devuelve sugerencias `{catalogId, baseUrl, server, models[], alreadyConfigured}`.
  Nunca crea cuentas ni envía credenciales.
- **Endpoints locales:** router genérico `/api/v1/local-endpoints` (listar, crear, editar, borrar, sondear,
  sincronizar) extraído de `ollama/api.py`; `/api/v1/ollama/endpoints` queda como envoltorio compatible. Al
  generalizar se corrigen: `provider_family` guardada como id del endpoint (`provider_accounts.py:279`,
  `ollama/api.py:376-393`), preservación del `enabled` del operador, reconciliación de modelos ausentes y `DELETE`.
- **From-catalog:** acepta `baseUrl`, `instanceId` y `endpointKind`; un re-guardado ya no resetea la URL.
- **Remediaciones:** payload derivado del perfil (sin campo de API key si no hay auth); acciones "Probar de nuevo",
  "Abrir asistente", "Validar modelo"; textos por causa ("Inicia el servidor", "Cargando modelo", "Requiere
  token").
- **Estado:** `GET /api/v1/runtime/providers` incluye las cuentas locales con `models` (id, estado de carga,
  habilitado, por defecto, capacidades) y un bucket `local` en el rollup (la clave `ollama` se mantiene).

### 4.8 UI (web)

- Tarjetas de catálogo: llama.cpp, LM Studio, vLLM, "Servidor local OpenAI-compatible" (espejo TS sin drift).
- Botón "Detectar runtimes locales" en la configuración de runtimes: lista las sugerencias y abre el asistente
  prellenado.
- Asistente para locales: Endpoint (URL editable prellenada, nombre de instancia, token opcional, casilla "corre en
  este equipo (WSL/Docker)" solo si el host no es loopback, con advertencia) → Modelos (estado de carga,
  habilitados, por defecto, capacidades por modelo) → Validar (validación real del modelo por defecto) → Listo.
- Panel "Endpoints locales" (generaliza `OllamaEndpointsPanel`): nombre, tipo, URL, salud, modelo cargado, n.º de
  modelos; acciones sondear, sincronizar, editar, borrar.
- Panel del equipo del hilo: los runtimes locales validados aparecen como candidatos con chip del modelo cargado.
- Línea de tiempo del hilo: evento `local_model_switch` legible ("AIDO cambió el modelo de llama.cpp de X a Y
  para el rol Z").
- Modal de salud/remediación con las causas locales de §4.6.
- Copy por `t()` con claves bilingües (en ≠ es), sin estilos prohibidos por los guardrails visuales.

## 5. Datos y migración

- Migración nueva (fase siguiente a la última registrada): setting `runtime.local.enabled` sembrado desde
  `runtime.ollama.enabled`; `runtime_installations` por instancia local cuando se crea la cuenta (no fila
  compartida de familia, `runtime_status.py:991`); metadata de modelos locales (`loadState`, `isDefault`,
  `capabilities`) en la fila del modelo; evidencia de validación por (cuenta, modelo).
- Sin migración destructiva de settings existentes; `ollama` sigue siendo valor válido.
- Nuevos literales (tipo de evento `local_model_switch`, causas, modo `local`) registrados en los `Literal` de
  contratos y OpenAPI regenerado.

## 6. Pruebas

- **Dobles de prueba fieles a la documentación oficial** en `tests_py/fakes/local_llm_servers.py` (servidores
  HTTP en `127.0.0.1:0`): router llama.cpp (`/health` con 503 mientras carga, `/props`, `/v1/models` con
  `status.value`, autoload con latencia simulada, `usage`, 401 con `--api-key` salvo `/health`), LM Studio
  (`/v1/models`, `/api/v0/models` y `/api/v1/models` con `state`), vLLM (`/health`, un solo modelo), genérico
  (`/v1/models` + chat). Cada rebanada agrega tests que fallan antes y pasan después.
- Contratos PO/Developer/Architect/Security con runtime local ejecutable; preflight con costo self-hosted;
  privacidad `local_private` (loopback acepta, LAN rechaza, declarada acepta); workload `local_model_call` con 20
  GiB libres; selección determinista (cargado, por defecto, cambio con evento, vacío bloquea); Jev con un candidato.
- Regresión: suites de Ollama, runtime-team, product loop, preflight y gates `test:py`.
- Playwright: detección + asistente contra un doble local, panel de endpoints, candidato en el equipo del hilo y
  evento de cambio de modelo.
- **Validación en vivo final:** `llama-server` real en `:8082` — agregar, validar, hilo con PO en llama.cpp.

## 7. Entrega por rebanadas

1. Localidad, guard, costo, readiness/settings y `local_model_call` → llama.cpp `healthy` y ejecutable (backend).
2. Ruta de modelo (§4.6).
3. Estado de modelos, modelo por defecto, capacidades por modelo, validación por modelo y selección determinista
   con evento de cambio.
4. Catálogo (`lm_studio`, `vllm`, `local_openai_compatible`), perfiles, lector de estado LM Studio, detección,
   router de endpoints locales, remediaciones.
5. UI y Playwright.
6. Validación en vivo con el `llama-server` real.

## 8. Fuera de alcance

Arrancar/detener procesos de servidores; carga/descarga explícita por API (`/models/load`, `/api/v1/models/load`);
streaming SSE; tool calling y edición por diff del Developer (iniciativa aparte de paridad con CLI); embeddings;
estado de carga de Ollama (`/api/ps`); entradas para Docker Model Runner (sin auth de aplicación), SGLang, TGI y
Lemonade; medición de VRAM.

## 9. Riesgos y supuestos

- **LM Studio y vLLM no están instalados en el host:** se validan contra dobles construidos desde la documentación
  oficial; la forma exacta de `/api/v1/models` de LM Studio se verifica antes de codificar y se parsea de forma
  defensiva con fallback a `/api/v0/models`. Riesgo residual declarado hasta una prueba real.
- **llama.cpp es rolling release:** el campo `status` de `/v1/models` en router mode se parsea defensivamente
  (ausente → estado desconocido).
- **Cambio de modelo compartido:** preferir el cargado reduce, pero no elimina, el desalojo del modelo de otra
  sesión; el evento `local_model_switch` lo hace visible.
- **Ollama en LAN** pasa a remoto para privacidad (§4.1): puede requerir habilitar remotos en instalaciones que lo
  usan.
- Supuesto: WSL2 reenvía `localhost` al host Windows (default), así que vLLM en WSL se ve como loopback.

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
  https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict ;
  https://lmstudio.ai/docs/developer/openai-compat/structured-output
- vLLM: https://docs.vllm.ai/en/latest/serving/online_serving/ ;
  https://docs.vllm.ai/en/latest/features/structured_outputs/ ;
  https://docs.vllm.ai/en/stable/getting_started/installation/gpu/
