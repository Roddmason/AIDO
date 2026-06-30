# Real Threads — Design Spec

> Reemplazar el "chat decorativo" del shell por **threads reales** persistidos, con un
> coordinator que ejecuta una decisión real (responde o bloquea) sobre cada mensaje de usuario.
> Fecha: 2026-06-28 · Rama: `dev` · Autor: Rodrigo Mason

## 1. Contexto verificado (estado actual)

- El route `threads` ya existe y renderiza `ShellPage` → hoy **reusa `overview.sessions` + `overview.chats`** como stand-in de threads (`ShellSidebar.tsx`, `ThreadTree.tsx`). El centro es `WorkbenchPage`, cuyo composer crea `session → chat → pipeline` (cadena legacy). Eso es el "chat decorativo".
- El backend ya tiene: migraciones por fases en `shared/migrations.py` (última = **fase 31 `product_owner_outputs`**, WIP concurrente), repos slice con autocommit-por-statement + `immediate_transaction` para atomicidad, overview agregado en `control_plane/overview.py`, routers FastAPI `create_router(*, platform, require_write)` montados en `api.py`.
- **`product_loop/intent_classifier.py` (WIP, ya con tests)** clasifica un prompt de forma **determinista** y devuelve `plan_mode ∈ {execute, plan, ask, blocked}` + `questions`, `intents`, `risk`, `required_roles`, `required_gates`. Esto es exactamente el "responde o bloquea".
- Escritor concurrente activo en el mismo checkout (intake ProductOwner). → minimizar edición de archivos compartidos; el grueso del trabajo va en **archivos nuevos** aislados.

## 2. Objetivo verificable

Un usuario abre un proyecto, crea un **thread real** (fila en `project_threads`), envía un mensaje (fila `user` en `thread_messages`), y el **ThreadCoordinator** ejecuta sobre ese mensaje produciendo, de forma determinista y trazable:
- **Responde**: mensaje `aido_lead` + evento de coordinación + artifact de intake → `thread.status = open`.
- **Bloquea**: mensaje `decision_request` + `thread_decisions(pending)` + evento → `thread.status = waiting_decision`.

Criterio de éxito (tests): crear thread · enviar mensaje · coordinator responde o bloquea · artifacts aparecen en el thread · gates verdes (`test:py`, OpenAPI sync, i18n, arquitectura, `check:web`, `build:web`, `test:web`).

## 3. Modelo de datos — migración fase 32

`project_threads` — header del hilo, **ownership polimórfico**.
- `id` (`thread-<uuid>`), `project_id`, `owner_type`, `owner_id`, `title`, `status`, `summary`, `metadata` (JSON), `created_at`, `updated_at`.
- `owner_type ∈ {workspace, loop, story, agent_task, review}`; `owner_id` referencia la PK de la entidad dueña (sin FK física: desacople, como el resto del esquema).
- `status ∈ {open, waiting_decision, blocked, resolved, archived}`.
- Índice `(project_id, owner_type, owner_id)` y `(project_id, updated_at)`.

`thread_messages` — timeline append-only.
- `id`, `thread_id`, `project_id`, `sequence` (monótona por thread), `kind`, `author`, `content`, `metadata`, `created_at`. `UNIQUE(thread_id, sequence)`.
- `kind ∈ {user, aido_lead, agent_summary, decision_request, artifact, error, system_event}`.

`thread_artifacts` — artifacts surgidos en el hilo (link, no copia).
- `id`, `thread_id`, `project_id`, `message_id` (nullable), `artifact_id` (ref a artifact externo o id propio), `kind`, `title`, `metadata`, `created_at`.

`thread_agent_events` — bitácora de ejecución del coordinator/agentes.
- `id`, `thread_id`, `project_id`, `sequence`, `type`, `agent_role` (nullable), `payload` (JSON), `metadata`, `created_at`. `UNIQUE(thread_id, sequence)`.

`thread_decisions` — solicitudes de decisión (cuando el coordinator bloquea).
- `id`, `thread_id`, `project_id`, `message_id`, `title`, `status`, `prompt`, `options` (JSON), `resolution` (nullable), `decided_by` (nullable), `decided_at` (nullable), `metadata`, `created_at`, `updated_at`.
- `status ∈ {pending, resolved, dismissed}`.

Ordenamiento append-only por `sequence` (no por `id` aleatorio) — evita el flake de utc_now ms-resolution.

## 4. Contratos (`threads/contracts.py`, nuevo, aislado)

Literals para response_model estricto (evita el "Literal drift → 500"):
`ThreadOwnerType`, `ThreadStatus`, `ThreadMessageKind`, `ThreadEventType`, `ThreadDecisionStatus`. Pydantic records: `ThreadRecord`, `ThreadMessageRecord`, `ThreadArtifactRecord`, `ThreadAgentEventRecord`, `ThreadDecisionRecord`, y respuestas (`ThreadListResponse`, `ThreadDetailResponse`, `ThreadMessageResultResponse`).

## 5. Repository (`threads/repository.py`, nuevo)

`ThreadsRepository(connection)` espejo de `ProductLoopRepository`: mappers `row_to_*`, `redact_secrets` en metadata/content-libre, ids `thread-*`/`thread-msg-*`/etc., `utc_now`.
Métodos: `create_thread`, `get_thread`, `list_threads(project_id?, owner_type?, owner_id?)`, `append_message`, `list_messages`, `attach_artifact`, `list_artifacts`, `record_event`, `list_events`, `create_decision`, `resolve_decision`, `list_decisions`, `set_status`. La secuencia se calcula con `COALESCE(MAX(sequence),0)+1` dentro de `immediate_transaction` (evita colisión en `UNIQUE`).

## 6. Coordinator (`threads/coordinator.py`, nuevo) — REAL, determinista, fail-closed

`ThreadCoordinator(connection, classifier=IntentClassifier())`:

`post_message(thread_id, content, author="user", *, project_assessment=None, git_state=None, changed_files=None) -> dict`:
1. `append_message(kind="user", author, content)` (en `immediate_transaction`).
2. Construye `IntentClassificationInput(prompt=content, project_assessment, changed_files, git_state, user_mode)`.
3. `decision = classifier.classify(input)` — **clasificador determinista real**, sin mock.
4. `record_event(type="coordinator_run", payload=decision.to_dict())`.
5. `attach_artifact(kind="intake_classification", title=…, artifact_id="thread-intake-<uuid>", message_id=None)` con la decisión serializada → **"artifacts aparecen en el thread"**.
6. Ramifica por `decision.plan_mode`:
   - `ask` | `blocked` → **bloquea**: `append_message(kind="decision_request", author="aido_lead", content=questions)`, `create_decision(status="pending", prompt, options=questions)`, `set_status("waiting_decision")`.
   - `execute` | `plan` → **responde**: `append_message(kind="aido_lead", author="aido_lead", content=resumen intents/roles/gates/planMode)`, `set_status("open")`.
7. Devuelve `{thread, blocked: bool, messages:[…], decision?, artifacts:[…], events:[…]}`.

`resolve_decision(thread_id, decision_id, resolution, decided_by) -> dict`: marca la decisión `resolved`, registra `system_event`, reabre `status="open"`.

Sin proveedores externos por defecto (igual que el classifier); fail-closed: si el runtime no es ejecutable, `plan_mode="blocked"` ⇒ bloquea. No fabrica salidas de LLM.

## 7. API (`threads/api.py`, nuevo) + montaje

`create_router(*, platform, require_write)`:
- `GET /api/v1/threads?projectId&ownerType&ownerId` → `ThreadListResponse`.
- `POST /api/v1/threads` (write) → crea thread (`{projectId, ownerType, ownerId, title}`) → `ThreadDetailResponse`.
- `GET /api/v1/threads/{threadId}` → thread + messages + artifacts + decisions + events.
- `POST /api/v1/threads/{threadId}/messages` (write) → `{content, author?}` → corre coordinator → `ThreadMessageResultResponse`.
- `POST /api/v1/threads/{threadId}/decisions/{decisionId}/resolve` (write) → `{resolution, decidedBy?}`.
Errores: `KeyError→404`, `ValueError→422`. Montar en `api.py` con `require_write`.
Overview: añadir `"threads": ThreadsRepository(connection).list_threads()` en `overview.py` y `threads: list[ThreadRecord]` en `OverviewResponse` (para que el sidebar liste threads por proyecto sin N llamadas).

## 8. Frontend (route `threads`, aislado del WorkbenchPage WIP)

- **Swap del centro**: `ShellPage` deja de renderizar `WorkbenchPage` y renderiza un nuevo `ThreadConversation` (features/shell). Así el route `threads` ya **no** usa el composer legacy (no crea session/chat/pipeline) → "no crear chats antiguos" + "ocultar legacy" sin tocar `WorkbenchPage` (que edita el escritor concurrente). El route `workbench` legacy queda intacto.
- **Sidebar**: `ThreadTree`/`ShellSidebar` leen `overview.threads` agrupados por `projectId` (en vez de sessions/chats). "New thread" llama `createThread` real (POST) y selecciona el thread creado.
- **ThreadConversation** (nuevo): carga `getThread(threadId)`; renderiza `thread_messages` por `kind` con tratamiento visual distinto (user, aido_lead, agent_summary, decision_request con UI de acción que llama resolve, artifact con chip al artifact, error, system_event); composer envía `postThreadMessage` → muestra la respuesta/bloqueo del coordinator. Estados loading/empty/error honestos. UI guíada por `ui-ux-pro-max`/`impeccable` (sin AI-slop, sin Inter/cyan/purple/gradients/side-stripes; tokens existentes; primitives `components/ui`).
- **API client**: wrappers `createThread`, `getThread`, `postThreadMessage`, `resolveThreadDecision` (patrón `requestGeneratedOperation`). Regenerar `openapi.ts` (`pnpm run openapi:generate`).
- **i18n**: registrar todas las copys nuevas en `default_catalog.json` (en+es, LF) antes de usarlas.

## 9. Migración / ocultar legacy

- El route `threads` ya no consume sessions/chats (las lee solo como fallback si fuera necesario, pero el modelo pasa a `overview.threads`). `sessions_chats` API permanece para compatibilidad del route `workbench`; no se borra (deuda preexistente, fuera de alcance) — se **oculta** del flujo de threads. No se crean chats nuevos desde threads.

## 10. Tests

Backend (`tests_py/`): `test_threads_repository.py` (crear/append/seq/artifacts/decisions), `test_threads_coordinator.py` (responde con runtime ejecutable + intent claro; bloquea con `ask`/`blocked`; artifact intake creado), `test_threads_api.py` (crear thread · enviar mensaje · responde/bloquea · GET muestra artifacts). Actualizar `test_ci_and_openapi_client.py` (nuevas operations) y `OverviewResponse`/arquitectura si aplican.
Web (`tests_web/`): spec Playwright que crea thread, envía mensaje y verifica render de la respuesta del coordinator y del artifact.

## 11. Gates de cierre

`pnpm run openapi:generate` → `uv run pytest tests_py -q` → `pnpm run check:web` → `pnpm run typecheck:web` → `pnpm run build:web` → `pnpm run test:web`. Leer exit code real. Commit quirúrgico (solo archivos de threads; staging explícito por la WIP concurrente).

## 12. Fuera de alcance (YAGNI)

Ejecución asíncrona/streaming del coordinator, invocación de agentes reales por rol desde el thread (se deja el `intent_classifier` determinista como motor de coordinación v1), borrado físico de `sessions_chats`.
