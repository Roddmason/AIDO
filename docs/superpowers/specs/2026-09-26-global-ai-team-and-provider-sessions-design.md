# Equipo de IA global, switches por proveedor y sesiones por proveedor

**Fecha:** 2026-09-26
**Estado:** Diseño aprobado por el operador (enfoque A, tres rebanadas). Pendiente de plan de implementación.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Objetivo

Que AIDO se parezca a cómo el operador trabaja en Claude: un conjunto de proveedores de IA activos
para todo AIDO, un equipo fijo de roles (PO, Developer, Arquitecto, Seguridad…) asignado a esos
proveedores, y **una sesión por rol dentro de cada hilo** en el proveedor que le toca, de modo que un
turno recuerde lo que hizo el anterior. Los runtimes dejan de ser "del hilo": son de todos los hilos.

Criterios de éxito verificables:

1. En *Settings → Providers & CLI* cada proveedor configurado (Claude Code CLI, Codex CLI, NVIDIA
   NIM, Ollama, llama.cpp, Gemini, OmniRoute…) tiene un switch. Apagado ⇒ ningún hilo lo usa ni por
   failover; la barra de estado muestra "N proveedores activos".
2. Existe un **equipo de IA global**: por rol, una lista ordenada de proveedores (el primero es el
   asignado, los siguientes son fallback), configurable en scope general con override por proyecto.
   Sin configurar, AIDO deriva un equipo automático desde los proveedores activos.
3. Un hilo nuevo usa el equipo global sin tocar nada. El chip "AI team" del intake dice "Equipo
   global"; personalizar el hilo (override) sigue disponible y tiene prioridad.
4. Si el proveedor asignado a un rol está inactivo o falla, el rol usa el siguiente activo de su
   orden y el hilo deja el evento `runtime_failover`; bloquea solo si no queda ninguno.
5. Cada (hilo, rol) tiene su sesión en su proveedor:
   - Claude Code CLI y Codex CLI: sesión **nativa**, reanudada turno a turno (mismo `session_id` /
     `thread_id`).
   - Modelos API y locales (llama.cpp, Ollama, NIM, OmniRoute, Gemini): sesión **emulada**, memoria
     acotada de turnos anteriores que AIDO reinyecta, etiquetada como emulada en la UI.
6. Prueba de punta a punta en el sandbox: en un rework, el Developer en Claude Code CLI reanuda la
   misma sesión (mismo `session_id` en `provider_sessions` y en la salida JSON del CLI); con gemma en
   llama.cpp, el segundo turno recibe el resumen del primero en su prompt.
7. QA sigue siendo determinista (ejecuta los comandos de QA del proyecto); no tiene rol de modelo.

## 2. Estado actual (verificado el 2026-09-26)

| Hecho | Evidencia |
|---|---|
| El switch por proveedor existe en la BD y en la API, pero la sección Providers & CLI solo muestra estado | `provider_accounts.enabled`; `PATCH /api/v1/model-gateway/providers/{id}` (`agents/model_gateway_api.py:597-610`); único botón Enable/Disable en `web/src/features/model-gateway/ProviderAccountsPanel.tsx:116-125`; tarjetas sin toggle en `runtime-setup/RuntimeSetupPanel.tsx:488-610` |
| Interruptor global de CLIs | `runtime.cli.enabled` (general) AND `project.runtime.cli.enabled` (`settings/registry.py:74-81,124-131`), leído en `runtime_integrations/repository.py:138-167,237-249` |
| El equipo es por hilo y se guarda en el hilo | `project_threads.metadata.runConfiguration{allowedRuntimes, roleRuntimes, teamMode}` (`runtime_team/configuration.py:39-48,130-200`); `PATCH /api/v1/threads/{id}/run-configuration` (`threads/api.py:203-243`) |
| En modo automático todos los roles quedan confinados al proveedor del PO | `role_allowlist` (`runtime_team/configuration.py:455-479`): sin equipo devuelve `[product_owner_provider_id]` |
| Roles del equipo: PO y Developer obligatorios; Arquitecto y Seguridad opcionales; el resto hereda del PO | `runtime_team/roles.py:28-33,79-93` (`team_role_for` devuelve `None` para aido_lead, qa_engineer, technical_lead…) |
| Reparto automático determinista ya existe | `auto_assign_roles` (`runtime_team/roles.py:96-117`): CLI primero, luego `runtimeOrder`, luego id |
| Sellado del equipo en el run y validación con dos ventanas | `seal_thread_runtime_team` (`product_loop/metadata.py:67-110`); 30 min + fingerprint al elegir (`runtime_team/validation.py:19,51-89`); 24 h + fingerprint al ejecutar (`agents/model_execution_health.py:18,42-75`), gate en `product_loop/phases/environment.py:163-177` |
| Failover existente por rol | `_run_with_failover` (`product_loop/coordinator.py:5516+`); allowlist dura en `AIResourceRequest.allowed_provider_ids` (`agents/ai_resource_manager.py:81`) |
| Ninguna llamada conserva sesión | Claude: `claude --print --permission-mode … --add-dir <ws>` (`agents/cli_runtimes/claude_code_cli.py:115-145`); PO y Arquitecto agregan `--no-session-persistence` (`agents/runtime_registry.py:109-113`); Codex: `codex exec --cd <ws> --ephemeral` (`agents/cli_runtimes/codex_cli.py:52-89`, `runtime_registry.py:44-149`); proceso nuevo por llamada vía ToolBroker (`agents/developer_agent.py:433-482`, `agents/tool_broker.py:1100-1112`) |
| Modelos API/locales reciben un `messages` fresco en cada llamada | `agents/product_owner_agent.py:576-606`, `agents/developer_agent.py:282-329`, `runtime_adapters/openai_compatible.py:165-180` |
| `cli_sessions` es auditoría por ejecución, no reanudable | `shared/migrations.py:1655`; `agents/cli_sessions.py:27-58` |
| Un solo run vivo por hilo | `ThreadBusyError` (`threads/coordinator.py:60-62,142-146`) |
| Worktree estable por hilo, cwd de cada llamada | `product_loop/phases/execution.py:236-249`; `workspaces_projects/repository.py:135-194` (`stable_task_suffix(thread_id)`, `reuse_existing`) |
| Scopes de settings: solo `general` y `project` (project > general > default) | `settings/resolver.py:26-82,111-133`; tipos disponibles: boolean, enum, number, string, string_list (`settings/registry.py`) |
| Claude Code CLI 2.1.274 instalado: `-p --output-format json` devuelve `session_id`; `claude -p --resume <id>` funciona desde cualquier directorio (≥ 2.1.223); retención 30 días (`cleanupPeriodDays`); `--session-id <uuid>`, `-n <nombre>`, `--fork-session` disponibles | `claude --help`; https://code.claude.com/docs/en/sessions.md; https://code.claude.com/docs/en/headless.md |
| Codex CLI 0.155.1 instalado: `codex exec --json` emite eventos JSONL; `codex exec resume <SESSION_ID> [PROMPT]` con `--all` (sin filtrar por cwd); `--ephemeral` desactiva la persistencia; sesiones en `~/.codex/sessions/AAAA/MM/DD/rollout-*.jsonl` con `cwd` en su metadata | `codex exec --help`, `codex exec resume --help`, inspección local |
| Claude agrupa sesiones por directorio de trabajo; `CLAUDE_CODE_PROJECT_DIR_NAME` solo aplica con `CLAUDE_CONFIG_DIR` propio (aislaría credenciales) | https://code.claude.com/docs/en/sessions.md#name-the-project-directory-yourself |

## 3. Decisiones tomadas con el operador

1. **Alcance de la asignación y los switches:** global, con override por proyecto (scopes existentes).
2. **Sesiones:** una por rol dentro de cada hilo (PO, Developer, Arquitecto, Seguridad…), nunca una
   compartida.
3. **Fallback:** siguiente proveedor activo del orden del rol, con evento visible; bloqueo solo sin
   candidatos.
4. **Equipo por hilo actual:** se conserva como override opcional; el sellado, la validación y Jev
   siguen operando sobre el equipo efectivo.
5. **Bugs encontrados en el camino**, propios o ajenos, se corrigen y documentan (instrucción del
   operador).

## 4. Diseño

### 4.1 Switch por proveedor

- Fuente de verdad única: `provider_accounts.enabled`. No se crea otra bandera.
- *Settings → Providers & CLI*: cada tarjeta de proveedor recibe un switch que llama a
  `PATCH /api/v1/model-gateway/providers/{id}` con `enabled`; los endpoints locales usan su `PATCH`
  propio (`/api/v1/local-endpoints/{id}`). El switch se deshabilita mientras el proveedor esté en uso
  por un run activo (la política ya lo excluye en la siguiente selección; no se corta un run en curso).
- Barra de estado: "N proveedores activos" (activos = `enabled` y política de runtime permitida),
  junto al conteo de ejecutables que ya existe.
- Los interruptores globales de transporte (`runtime.cli.enabled`, `runtime.remote.enabled`, …) se
  mantienen como están: son de categoría, no de proveedor.

### 4.2 Equipo de IA global

**Roles.** Los cuatro roles del equipo actual siguen siendo la base: `product_owner`, `developer`
(obligatorios), `architect`, `security` (opcionales). Se agregan dos roles derivados que hoy heredan
del PO y pasan a ser asignables: `technical_lead` y `researcher`. Un rol derivado sin asignación
explícita usa la del PO (comportamiento actual). QA, DevOps y aido_lead no tienen rol de modelo.

**Configuración.** Seis settings `string_list`, sección nueva `team`, con scope general y override
por proyecto (`project_section="team"`):

```
team.role.product_owner   ["claude_code_cli", "llama_cpp"]
team.role.developer       ["codex_cli", "claude_code_cli", "llama_cpp"]
team.role.architect       []
team.role.security        []
team.role.technical_lead  []          # vacío = hereda del PO
team.role.researcher      []          # vacío = hereda del PO
team.sessionMaxTurns      25          # number, 5..200 (§4.4)
```

Una lista vacía en un rol obligatorio significa "automático": AIDO deriva la asignación con
`auto_assign_roles` sobre los proveedores activos y elegibles (misma función y mismo orden que hoy usa
el chip), y la UI la muestra como "automático (claude_code_cli)". Los ids que no correspondan a un
proveedor activo y elegible para el rol se ignoran al resolver (no rompen la resolución) y la UI los
marca como inválidos.

**Equipo efectivo de un hilo** (`resolve_effective_team(project_id, thread_id)`):

1. Si el hilo tiene override (`runConfiguration.allowedRuntimes`), es el equipo del hilo (sin cambio).
2. Si no, equipo global resuelto para el proyecto (project > general > automático), intersectado
   con `project.runtime.allowedProviders` como hoy hace `narrow_runtime_team`.
3. El resultado se sella en el run con la misma forma que hoy (`runtimeTeam`, `roleModels`,
   `runtimeTeamDiscarded`) más `runtimeTeamSource: "thread" | "project" | "general" | "automatic"`, para
   que la validación de 30 min / 24 h, `roleModels` y la remediación de runtime rancio sigan iguales.

**Ruteo.** `role_allowlist` deja de devolver `[proveedor del PO]` cuando no hay override: devuelve el
orden del rol del equipo efectivo (asignado + fallbacks) filtrado por activos y elegibles. Los roles
derivados sin asignación devuelven el orden del PO. El primer elemento es la elección determinista;
`_run_with_failover` recorre los siguientes ante `runtime_unavailable`/fallo del proveedor y deja el
evento `runtime_failover` (existente). Si la lista queda vacía, bloqueo `runtime_not_executable` con
la remediación existente "Open team" (sección `team`).

Jev y las políticas de rol siguen aplicándose sobre esa allowlist; la ambigüedad
(`runtime_selection_ambiguous`) solo puede darse si el operador deja varios proveedores en el orden
y Jev está en modo `runtime_selection`; en modo `shadow` (default) gana el orden.

### 4.3 Sesiones por proveedor — modelo de datos

Tabla nueva `provider_sessions` (migración de esquema nueva; nunca se edita la BD viva a mano):

| Columna | Descripción |
|---|---|
| `id` | `provider-session-<uuid>` |
| `project_id`, `thread_id`, `role` | clave lógica junto con `provider_id`; una sesión **activa** por (hilo, rol, proveedor) |
| `provider_id` | cuenta de proveedor |
| `kind` | `native` (CLI con reanudación real) o `emulated` (memoria reinyectada) |
| `session_ref` | `session_id` de Claude / `thread_id` de Codex; vacío en emuladas |
| `workspace_id`, `cwd` | worktree donde nació la sesión (evidencia; la reanudación no depende del cwd) |
| `label` | `aido/<proyecto>/<hilo>/<rol>` (también se pasa a Claude con `-n`) |
| `status` | `active`, `rotated`, `expired`, `reset` |
| `turns` | turnos completados |
| `last_used_at`, `created_at`, `metadata` | `metadata` guarda motivo de cierre y el `session_ref` que la reemplazó |

Tabla `provider_session_turns` (solo sesiones emuladas): `session_id`, `sequence`, `instruction_excerpt`
(≤ 600 caracteres), `assistant_summary` (≤ 1 200 caracteres: `summary`, decisiones y riesgos que el
agente devolvió), `created_at`. Todo pasa por `redact_secrets` antes de escribirse.

`cli_sessions` (auditoría por ejecución) no cambia; cada fila nueva enlaza `provider_session_id`.

### 4.4 Sesiones nativas (Claude Code CLI, Codex CLI)

Ciclo por llamada del runner (PO, Developer, Arquitecto, Seguridad con modelo CLI):

1. `ProviderSessionStore.acquire(thread_id, role, provider_id, workspace)` devuelve la sesión activa o
   `None`.
2. El argv se construye según el caso:
   - **Claude, primera vez:** sin `--no-session-persistence`, con `--output-format json` y
     `-n aido/<proyecto>/<hilo>/<rol>`. Se lee `session_id` del JSON de salida.
   - **Claude, reanudación:** `claude -p --resume <session_ref> --output-format json …` con los mismos
     `--permission-mode`, `--add-dir` y `--model` de siempre (Claude no los restaura al reanudar).
   - **Codex, primera vez:** sin `--ephemeral`, con `--json`; se lee `thread_id` del evento
     `thread.started` (nombre exacto a confirmar en el plan contra la salida real de 0.155.1).
   - **Codex, reanudación:** `codex exec resume <session_ref> --all -- <prompt>` con `--cd <ws>` y el
     sandbox de siempre.
   El texto útil (respuesta JSON del PO/Arquitecto, salida del Developer) se extrae del envoltorio
   JSON del CLI; el parser de cada runner recibe el mismo contenido que hoy.
3. Tras una llamada completada, `record_turn(session, session_ref, cli_session_id)` incrementa
   `turns` y `last_used_at`. Una llamada fallida no consume turno ni cierra la sesión.
4. **Sesión inválida** (el CLI responde "No conversation found", el `session_ref` venció por retención
   de 30 días o el archivo fue borrado): la sesión pasa a `expired`, se abre una nueva en la misma
   llamada y el hilo recibe el evento `provider_session` con `action: reset`. Nunca bloquea.
5. **Rotación:** al alcanzar `team.sessionMaxTurns`, la sesión pasa a `rotated`, se abre una nueva y
   el primer prompt de la nueva lleva un recap de ≤ 1 200 caracteres (último resumen del agente).
   Evento `provider_session` con `action: rotated`.
6. **Reinicio manual:** `POST /api/v1/threads/{id}/provider-sessions/{sessionId}/reset` (409 si el
   hilo tiene un run activo) marca `reset`; la siguiente llamada abre una nueva.

Los reintentos (`retryOfLoopId`) y continuaciones (`continueOfLoopId`) conservan el hilo, por lo que
reutilizan las mismas sesiones: el rework "recuerda" el turno anterior. Un hilo tiene a lo más un run
vivo, así que ninguna sesión se reanuda por dos procesos a la vez.

### 4.5 Sesiones emuladas (API y locales)

Para proveedores sin sesión nativa, el runner inyecta antes de la instrucción un bloque
"Session so far" con los últimos turnos de `provider_session_turns` de esa (hilo, rol, proveedor),
acotado a `min(12 turnos, 6 000 caracteres)`, más recientes primero. El contexto de repositorio, la
spec de historia y el feedback de rework se construyen igual que hoy; la memoria de sesión es
adicional, no los reemplaza. Al completar la llamada, se guarda el turno (extracto de la instrucción
+ resumen que devolvió el agente). La rotación y el reinicio de §4.4 aplican igual (`turns`).

La UI etiqueta estas sesiones como "emulada": AIDO no afirma continuidad que el proveedor no da.

### 4.6 API

| Endpoint | Uso |
|---|---|
| `GET /api/v1/runtime/team?projectId=` | Equipo efectivo del proyecto: por rol, orden configurado, asignado resuelto, fuente (`general`/`project`/`automatic`), candidatos activos y elegibles (reusa `runtime/team-candidates`) |
| `GET/PUT /api/v1/settings` (existente) | Lectura y escritura de `team.role.*` y `team.sessionMaxTurns` por scope |
| `PATCH /api/v1/model-gateway/providers/{id}` (existente) | Switch por proveedor |
| `GET /api/v1/threads/{id}/provider-sessions` | Sesiones del hilo por rol: proveedor, tipo, estado, turnos, último uso |
| `POST /api/v1/threads/{id}/provider-sessions/{sessionId}/reset` | Reinicio manual (409 con run activo) |

Los contratos nuevos entran en `response_model` tipados y en `openapi.ts` regenerado (gate de drift).

### 4.7 UI

- **Providers & CLI:** switch por tarjeta; estado "activo / inactivo / en uso".
- **Settings → Team (sección existente):** panel "Equipo de IA" con scope general/proyecto: por rol,
  selector ordenado de proveedores activos y elegibles (agregar, quitar, subir/bajar), botón
  "automático", y `sessionMaxTurns`. Los roles derivados muestran "hereda del PO" cuando están vacíos.
- **Intake (chip):** "Equipo global" con el asignado por rol; acción "personalizar este hilo" abre el
  panel actual (override).
- **Inspector del hilo, pestaña Team:** por rol, proveedor efectivo y su fuente, y la sesión
  (nativa/emulada, turnos, último uso) con "reiniciar sesión".
- **Consola del hilo:** los eventos `provider_session` (`opened`, `resumed`, `rotated`, `reset`) y
  `runtime_failover` se muestran con título propio.

Todo el copy pasa por `t()` con claves bilingües (gate i18n).

### 4.8 Errores y fallback

| Situación | Comportamiento |
|---|---|
| Proveedor asignado inactivo o no ejecutable | Siguiente del orden del rol; evento `runtime_failover`; bloqueo `runtime_not_executable` solo sin candidatos |
| Proveedor falla durante la llamada | Failover existente; la sesión del proveedor fallido no consume turno |
| `session_ref` inválido | Nueva sesión + evento `reset`; la llamada continúa |
| Sesión llega a `sessionMaxTurns` | Rotación con recap; evento `rotated` |
| El operador apaga un proveedor con run activo | El run actual termina como está; la siguiente selección ya no lo considera |
| Salida del CLI sin `session_id`/`thread_id` | La llamada se trata como completada pero la sesión no se registra (evento `provider_session` con `action: unavailable`, sin bloquear) |

### 4.9 Seguridad y costo

- `session_ref` no es un secreto, pero las transcripciones del proveedor contienen el repo: viven
  donde el CLI ya las guarda para uso interactivo (`~/.claude`, `~/.codex`); AIDO no las copia.
- Todo lo que AIDO persiste (turnos emulados, etiquetas, metadata) pasa por `redact_secrets`.
- Costo: una sesión nativa crece con cada turno; `sessionMaxTurns` acota el contexto y el costo por
  turno. La memoria emulada está acotada por diseño (§4.5). El inspector muestra los turnos para que
  el operador vea el tamaño de cada sesión.

### 4.10 Tests y validación

- Unitarios: resolución del equipo efectivo (precedencia thread > project > general > automático;
  ids inválidos ignorados; roles derivados heredan del PO), `role_allowlist` por orden, failover al
  siguiente activo, rotación/expiración/reset de sesiones, construcción de argv (primera vez vs
  reanudación, Claude y Codex), extracción de `session_id`/`thread_id`, bloque "Session so far" acotado.
- API: `runtime/team`, `provider-sessions` (lista y reset con 409), switches.
- Playwright: switch en Providers & CLI, panel Equipo de IA (general y proyecto), chip "Equipo global",
  pestaña Team del inspector con sesiones.
- Gates existentes: `test:py` (i18n, arquitectura web, response_model), `check:web`, `typecheck:web`.
- En vivo (sandbox `aido-e2e-sandbox`): (a) rework con Developer en Claude Code CLI reanudando la
  misma sesión — requiere que el operador vuelva a autenticar `claude` (token vencido hoy); (b) rework
  con gemma en llama.cpp recibiendo el recap del turno anterior; (c) apagar un proveedor y ver el
  failover al siguiente activo.

### 4.11 Fuera de alcance

- Varios hilos en paralelo (worker de 1 job, llama.cpp con 1 slot): decisión de recursos aparte.
- Revisor de QA con modelo.
- Sesiones para hilos creados desde el plugin OpenClaw (usan el mismo mecanismo; no se validan aquí).
- Pantalla de arranque/onboarding: los switches viven en Settings y en la barra de estado.

## 5. Rebanadas

1. **Switches + equipo global + ruteo por rol**: §4.1, §4.2, endpoints `runtime/team`, UI de
   Providers & CLI, panel Equipo de IA, chip "Equipo global". Criterios 1-4.
2. **Sesiones nativas**: §4.3, §4.4, `provider-sessions` API, inspector, eventos. Criterios 5 (CLI) y 6a.
3. **Sesiones emuladas**: §4.5 y su validación con llama.cpp. Criterios 5 (API/locales) y 6b.

Cada rebanada se entrega con tests primero, gates en verde, commit y push a `dev`, y validación en vivo
antes de pasar a la siguiente.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Nombre exacto del evento JSONL de Codex que trae el `thread_id` (0.155.1) | Confirmar con una ejecución real en el plan antes de codificar el parser; fail-open (§4.8) |
| Claude retira una sesión antes de 30 días o cambia el formato JSON | Reset automático; parser tolerante con test de contrato sobre una salida real capturada |
| Contexto de sesión crece y encarece cada turno | `sessionMaxTurns` + recap; turnos visibles en el inspector |
| Un proveedor emulado repite decisiones viejas del recap | El recap va después del contexto de repo fresco y se marca como "sesión anterior"; el bloque es acotado |
| Cambiar `role_allowlist` altera casos con equipo por hilo | El override conserva su rama actual; tests existentes del equipo por hilo siguen sin cambios |
