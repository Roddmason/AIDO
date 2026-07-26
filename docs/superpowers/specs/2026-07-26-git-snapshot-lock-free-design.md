# Git snapshot fuera del lock global de /api/ — diseño

- Fecha: 2026-07-26
- Estado: aprobado para implementación (sesión autónoma; supuestos declarados abajo)
- Alcance: `local_control_center/api.py` (middleware), `local_control_center/git_workspace/{api,service}.py`, tests.

## Problema

El middleware `serialize_runtime_access` serializa TODO `/api/` bajo un `threading.Lock` global
durante la request completa. `GET git/status` y `GET git/branches` ejecutan 7 subcomandos git
secuenciales vía `GitWorkspaceService._run_git` → `ToolBroker` (cada uno con escrituras sqlite de
trace), sumando 5-7 s medidos bajo el lock. Durante esa ventana el poller de 5 s del dashboard y
cualquier escritura quedan bloqueados. Agravante: los handlers son `async def` que llaman al
servicio síncrono en el event loop, así que además del lock bloquean el loop completo.

## Objetivo verificable

1. Mientras un snapshot git corre, cualquier otro request `/api/` responde sin esperar los
   subprocess (medible: un `GET /api/v1/security/handshake` concurrente termina en una fracción
   del tiempo del snapshot).
2. Las escrituras sqlite siguen serializadas: la única escritura con carrera lógica
   (SELECT-then-INSERT de `_ensure_project_workspace`) permanece bajo el lock global; el resto de
   las escrituras del snapshot (decisiones de policy, tool calls, telemetría, evidencia, update de
   `agent_runs`) usan ids propios y quedan serializadas a nivel sqlite (WAL + `busy_timeout=30000`,
   autocommit) desde una conexión dedicada.
3. Contratos de ToolBroker/policy intactos: mismos comandos brokered por snapshot, mismas trazas y
   decisiones en la respuesta, cero cambios de código en `tool_broker.py` y `policy_engine.py`.

## Hechos verificados que habilitan el diseño

- `open_sqlite_connection` fija WAL, `busy_timeout=30000`, `isolation_level=None` (autocommit) y
  `check_same_thread=False` (`local_control_center/shared/db.py:18-32`).
- Ya existe precedente de segunda conexión concurrente: `snapshot_overview()` en `api.py` abre
  conexión propia por llamada mientras otros requests escriben en `platform.connection`.
- Los upserts que toca el snapshot (`upsert_agent_profile`) son `INSERT ... ON CONFLICT DO UPDATE`
  atómicos; `create_agent_run`/`record_agent_tool_call`/`record_decision` son INSERTs con uuid.
- `RestrictedSubprocessSandbox.execute` hereda el entorno del proceso cuando `environment=None`
  (los git snapshot no pasan environment), así que el comportamiento del subprocess no cambia por
  moverlo de thread.
- `branches()` ya reusa el snapshot de `status()` (fix dc77643d): un solo camino que optimizar.

## Diseño

### 1. Split de fases en `GitWorkspaceService.status()`

- `prepare_status(project_id)` — fase sqlite corta: gates (`git` en PATH, carpeta existe, `.git`
  propio), `_project_context` (incluye `_ensure_project_workspace`, la carrera SELECT-then-INSERT),
  `_git_profile()` y `_begin_operation`. Devuelve `(respuesta_terminal | None, PreparedStatusOperation | None)`.
- `collect_status(prepared)` — fase larga: los 7 `_run_git` bajo ToolBroker, parseo y
  `_finish_operation`. Solo escribe filas con ids propios del run.
- `status()` compone ambas fases: contrato y callers internos (checkout, apply_branch_policy,
  diff, product_loop coordinator, branches) quedan idénticos.
- El armado de la vista de branches se extrae a `branches_view(status, project_id)` módulo-level
  para que el router la reuse; `branches()` la usa internamente.

### 2. Exención del middleware, telemetría aún serializada

`git_workspace/api.py` exporta `is_git_snapshot_request(method, path)` (regex
`^/api/v1/projects/[^/]+/git/(status|branches)$`, solo `GET` — el POST de branches sigue bajo el
lock). El middleware, para esos requests: ejecuta `call_next` SIN retener el lock global y toma el
lock solo alrededor de `record_http_request` (que escribe en `platform.connection`).

### 3. Camino desacoplado en el router git + single-flight por proyecto

`create_router` recibe `snapshot_lock` (el mismo `store_request_lock`). Los dos GET corren en
`anyio.to_thread.run_sync` (dejan de bloquear el event loop) con este flujo:

1. Single-flight: registro en closure `project_id -> vuelo` protegido por un lock de módulo de
   router. Si ya hay un snapshot en vuelo para el proyecto, el request espera su resultado y lo
   comparte (misma ejecución auditada: mismas trazas, como ya hace branches con status). Timeout de
   espera generoso (superior al peor caso de 7×`GIT_TIMEOUT_SECONDS`) con fallback a ejecutar un
   snapshot propio.
2. Fase 1 bajo `snapshot_lock` sobre `platform.connection` (misma garantía de hoy para la carrera
   del workspace).
3. Fase 2 sin lock sobre una conexión dedicada `open_sqlite_connection(platform.db_path)`, cerrada
   en `finally`.
4. El dueño del vuelo publica resultado o excepción; los esperadores re-lanzan la excepción tal
   cual (p.ej. `KeyError` → 404).

## Alternativas descartadas

- **TTL cache puro del snapshot**: en cada miss sigue bloqueando 5-7 s bajo el lock (el poller de
  5 s garantiza misses frecuentes), y rompe el contrato auditado que ancla
  `test_git_branches_reuse_status_snapshot_without_extra_git_commands` (cada snapshot debe
  re-brokerear sus comandos; un cache TTL devolvería trazas de otra request pasada).
- **Split decide→release→execute→re-acquire dentro del ToolBroker**: tocaría el chokepoint de
  seguridad y obligaría a manipular el lock del middleware desde capas internas (inversión de
  propiedad del lock). Innecesario: el broker ya es seguro sobre una conexión dedicada.

## Riesgos y mitigaciones

- Carrera `_ensure_project_workspace` (snapshot vs mutación POST del mismo proyecto): cerrada
  dejando la fase 1 bajo el lock global.
- Pile-up del poller (snapshot nuevo cada 5 s mientras el anterior sigue corriendo) y carreras de
  `git status` sobre el index: cerradas por el single-flight por proyecto.
- Escritor concurrente en otra conexión con transacción `BEGIN IMMEDIATE` larga: `busy_timeout`
  de 30 s cubre; las escrituras de fase 2 son statements sueltos en autocommit.
- Telemetría HTTP de los paths eximidos: sigue registrándose (lock breve en el middleware); test
  la ancla.

## Tests (no-regresión + nuevos)

- Suite existente `test_git_workspace_api.py` completa: cubre broker/policy end-to-end por el
  camino nuevo (evidencia, redacción de secretos, reuse de branches, conteo de comandos).
- Nuevos (`tests_py/test_git_snapshot_lock.py`), con `git` falso lento vía PATH (mismo patrón que
  `write_fake_gitleaks`):
  1. Matcher `is_git_snapshot_request` (GET sí; POST branches no; otros paths no).
  2. No-bloqueo: con un snapshot lento en vuelo, un request `/api/` concurrente responde en una
     fracción del sleep del fake (aserción direccional, robusta bajo carga).
  3. Single-flight: dos GET concurrentes del mismo proyecto → una sola ejecución brokered
     (conteo de tool calls) y ambas respuestas `completed`.
  4. Telemetría: el path eximido sigue registrando `telemetry.http.request`.

## Supuestos declarados (sesión autónoma)

- Se prioriza el fix estructural sobre el TTL cache por las razones de contrato auditado ya
  descritas; si se quisiera además reducir la frecuencia de snapshots, un TTL corto puede añadirse
  después sin tocar este diseño (capa por encima del single-flight).
- No se tocan los demás endpoints `/api/` que bloquean el event loop con sqlite síncrono: fuera de
  alcance del pedido (cambio quirúrgico).
