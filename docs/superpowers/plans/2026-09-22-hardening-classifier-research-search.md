# Endurecimiento del clasificador, búsqueda SearXNG y salud operativa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cerrar los diez defectos de la validación en vivo del 2026-09-22 (spec §2.1–§2.10) con un cambio acotado y una prueba que falla antes y pasa después por ítem, y dejar al ResearchAgent respondiendo con un SearXNG propio.

**Architecture:** Cambios quirúrgicos sobre los módulos existentes, sin migraciones de esquema ni cambios de `response_model`. Backend: el clasificador expone `scores`/`research_only`/`question_keys` y el intake rutea por ese flag; un módulo nuevo `research/web_search.py` concentra el proveedor SearXNG, la validación de URL y el error `ResearchProviderBlockedError`; el governor deduplica `resource_wait` y avisa al hilo; el repositorio de jobs estampa `parentJobId` y falla hijos zombi; el overview cachea la compactación de filas inmutables. Frontend: copy nueva sólo vía `t()` y un spec Playwright nuevo con rutas mockeadas.

**Tech Stack:** Python 3.13 (FastAPI, SQLite JSON1, pytest), React + TypeScript + Vite, Biome 2.5, Playwright, catálogo i18n `local_control_center/i18n/default_catalog.json`.

**Spec:** `docs/superpowers/specs/2026-09-22-hardening-classifier-research-search-design.md`

## Global Constraints

- Python tests: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['<test files>','-q','-p','no:randomly']))"` desde la raíz del repo (workaround del access violation de faiss).
- Ruff format: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format <py files>`; ruff check: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check <py files>` (nunca sobre `.json`).
- Web typecheck: `corepack pnpm@10.24.0 run typecheck:web`.
- Web lint/format: `corepack pnpm@10.24.0 exec biome check <files>` (usar `--write` primero cuando haya imports nuevos: `biome format` NO arregla organizeImports).
- Web build: `corepack pnpm@10.24.0 run build:web`.
- OpenAPI regen tras cualquier cambio de API/`response_model`: `corepack pnpm@10.24.0 run openapi:generate` (LF). Este plan no cambia contratos de respuesta; ver Task 14 paso de comprobación.
- Todo módulo productivo nuevo lleva header semántico (docstring/JSDoc) con `@author Rodrigo Mason`.
- Python productivo: sólo docstrings (sin comentarios `#` sueltos); docstrings de API pública obligatorias (ruff D101–D103, convención pep257: primera línea termina en punto).
- Copy de UI sólo vía `t('key','English fallback')` con claves bilingües (`en != es`) en `local_control_center/i18n/default_catalog.json`, editado quirúrgicamente preservando formato (4 espacios para la clave, 6 para `en`/`es`).
- Visual guardrails: nada de Inter/cyan/purple/radial-gradient ni `border-left|right >= 2px`; colores sólo por tokens oklch existentes.
- Nuevos `kind` de artifact o roles de agente exigen el `Literal` + regen (drift de `response_model`); este plan no agrega ninguno.
- Finales de línea LF en todo archivo tocado.
- Commit: `Tipo (Ámbito): mensaje en español` + trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage sólo rutas explícitas; nunca `--no-verify`.
- Playwright: specs directos con `PLAYWRIGHT_DASHBOARD_PORT` fijo y build fresco (`run-web-tests.mjs` ignora argumentos).
- Preflight web (antes de la primera tarea web de cada agente/runtime y en Task 14): `node --version` debe imprimir `v24.16.0` (`.nvmrc`; `package.json` `engines.node` `>=24.16.0 <25.0.0`) y `corepack pnpm@10.24.0 --version` debe imprimir `10.24.0`. Si falla: `powershell -NoProfile -ExecutionPolicy Bypass -File local-control-center/scripts/use-node.ps1` (README.md:290-300; exige nvm-windows en PATH) y repetir; si sigue fallando, detener las tareas web y reportar el bloqueo exacto, nunca marcar un paso web como verde sin haberlo corrido.

---

## Verified Facts (inspección 2026-09-22, base `dev` @ 9f965e00)

Hechos verificados contra el código y una copia read-only de la BD viva (`platform.sqlite`, 1,9 GB), que el plan usa como línea base:

- **§2.1** `threads/coordinator.py:265` rutea `elif "research" in decision.intents:`; `IntentClassification` (`product_loop/intent_classifier.py:141-167`) no expone `scores`; `_intent_scores` está en `:234`; las preguntas fijas en inglés en `_questions_for` `:339-347`. La rama local `fix/intake-spanish-research` (4d0a5b16+833c475d) sólo agrega `_fold_accents`, vocabulario español y tests; no se cherry-pickea: Task 8 reescribe esos cambios con el nuevo criterio. Mensaje original de la validación (`thread-d3c5dc0d…`, 2026-09-22T20:00:38Z): "Investiga en modo solo lectura: ¿qué versión de Python exige pyproject.toml de este repo? Responde en una línea y no modifiques archivos."
- **§2.2** `agents/research_agent.py:131-176` (DuckDuckGo HTML), default del runner en `:231`, "returned no sources" en `:323`; `run()` convierte `ResearchAgentValidationError`/`ResearchPolicyError` en `status="research_blocked"` (`:835-841`) y el dict de retorno (`:999-1018`) NO incluye `remediation`, aunque el worker lo lee (`jobs_approvals/worker.py:942`). `run()` sólo captura esos dos tipos: un `ValueError` suelto del proveedor escaparía del job. Las fuentes descubiertas se descargan solas (`_persist_sources` `:347` → `_source_text` `:205-209` → `_fetch_url_text` `:182-202`) y ese guard sólo bloquea los nombres literales `localhost|127.0.0.1|::1` y sigue redirects con el opener por defecto: con SearXNG como default ese camino queda activo, así que T9 lo endurece (RFC 1918, link-local/metadata, resolución DNS privada y redirects). La frase "high-impact decision" no existe en la UI; el copy engañoso para research de intake es `BLOCKER_COPY.research_required` (`remediationPresentation.ts:367-378`, "Choose a completed research report with a validated technical decision…"), pensado para el loop.
- **§2.3** `agents/runtime_health_refresh.py:145-190` reencola apenas no hay ejecución pendiente. `needs_refresh` (`:63-72`) mira sólo la edad de `healthCheckedAt`, pero `ProviderAccountsRepository.record_health_check` (`agents/provider_accounts.py:605-630`) escribe `lastHealthCheckAt` para cualquier desenlace y readiness exige además `healthStatus == "healthy"` (`agents/runtime_readiness.py:111-120`): el reinicio del cooldown debe exigir ambas cosas. BD viva: 233 checks completados de `claude_code_cli` contra 62 de `codex_cli` desde 2026-09-22T12:00Z, todos `completed/healthy`: el check termina pero la evidencia sigue rancia, así que "improductivo" se define por evidencia, no por estado de la ejecución.
- **§2.4** `host_resources/governor.py:202-229` llama `update_job_status` en cada reevaluación; `JobsRepository.update_job_status` (`jobs_approvals/repository.py:746-772`) emite `job.<status>` siempre. El banner está en `ThreadExecutionPanel.tsx:283-306`; el mapeo legible está en `RuntimeHealthModal.tsx:37-96` (`REASON_COPY`, `describeReason`).
- **§2.5** Opciones crudas en `ThreadConversation.tsx:562-572` (botones) y `ThreadBlockerCard.tsx:709-713` (`<option>`); los códigos salen de `threads/similarity.py:24-29` (`SIMILARITY_ACTIONS`).
- **§2.6 (punto de verificación explícito del spec)** `functionality_registry` en la BD viva: 35 filas, todas de hilos `archived`; 0 loops `delivered` y 0 briefs `approved` en toda la BD. Reproducción sobre la copia: `find_existing_functionality(query=<mensaje de thread-d3c5dc0d>)` devuelve `functionality-9fbcd16c…` (score 0.632) cuyo `sourceThreadId` es el propio hilo archivado sin ejecución. `ensure_project_functionality` (`threads/similarity.py:212-235`) materializa cualquier hilo `resolved|archived`; el único gate que bloquea es `find_existing_functionality` (`:375-387`) consumido en `product_loop/coordinator.py:1546-1552`.
- **§2.7** `derivePipeline` (`ThreadExecutionPanel.tsx:125-196`) sólo cierra pasos con `threadStatus === 'resolved'`.
- **§2.8** BD viva: `job-31a6b390-864a-4caf-96f0-980842fb6012` (`agent.product_owner`, `running`, `lease_expires_at NULL`, `updated_at 2026-07-18T09:09:40.882Z`) + `agent-run-905924ea…` `running`. `requeue_expired_jobs` exige `lease_expires_at IS NOT NULL` (`jobs_approvals/repository.py:895`). El worker ejecuta `recover()` → `requeue_expired_jobs()` en cada lote (`worker.py:72-81`, `:298`). El job padre del worker es `CURRENT_EXECUTION.execution_id` (`worker.py:152-156`).
- **§2.9** Los 6 módulos listados no tienen `@author` (gate `tests_py/test_source_documentation_headers.py:116`). Claves `en == es`: `app.threads.remediation.riskReview.field.runtime` (catálogo `:11699`) y `.field.loopId` (`:11727`). `KeyError 'details'` en `tests_py/test_local_worker_runtime.py:682`: diagnóstico estático en Task 6 (la identidad por `jobId` de 013b1b26 en `remediations/repository.py:109-113` impide que el fallo del job enriquezca la acción genérica sin `jobId` creada por `ensure_worker_remediation`, `remediations/service.py:268-285`).
- **§2.10** cProfile de `build_overview_from_connection` sobre la copia: **2,79 s** de pared; `list_evidence_packages` 1,07 s y `list_all_test_results` 0,90 s (decodificar ~118 MB de JSON por poll: `evidence_packages.test_results` hasta 14,3 MB por fila y 59 MB en las 100 filas; `test_results.metadata` 59 MB en 150 filas, con `resourceBlockers`/`teamSchedule` de ~7 MB). Las consultas livianas equivalentes (sin blobs) cuestan 0,05–0,06 s. Esas columnas no se actualizan nunca después del INSERT (el único `UPDATE evidence_packages` en `evidence/repository.py:297` no las toca; no existe `UPDATE test_results`). Costo dominante NO inherente → se ataca con caché de filas inmutables, sin recortar datos.

## File Map

| Archivo | Acción | Responsabilidad única |
|---|---|---|
| `local_control_center/agents/agent_resource_policy.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/agents/runtime_preflight.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/agents/runtime_preflight_cli.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/executions/timeout_reconciliation.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/product_loop/research_resolution.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/product_loop/runtime_risk_review.py` | Modify | Header `@author` (Task 1) |
| `local_control_center/i18n/default_catalog.json` | Modify | Copy bilingüe (Tasks 1, 8, 9, 10, 11, 12, 13; carril serializado) |
| `local_control_center/agents/runtime_health_refresh.py` | Modify | Cooldown exponencial por objetivo del refresco automático (Task 2) |
| `tests_py/test_runtime_health_refresh.py` | Modify | Pruebas del cooldown (Task 2) |
| `local_control_center/host_resources/governor.py` | Modify | `resource_wait` sólo al cambiar motivo + evento de hilo (Task 3) |
| `tests_py/test_host_resource_governor.py` | Modify | Pruebas de deduplicación y evento de hilo (Task 3) |
| `local_control_center/jobs_approvals/repository.py` | Modify | `parentJobId` al crear hijos, reaper de hijos zombi con vínculo comprobable y saneamiento histórico por id (Task 4) |
| `tests_py/test_orphan_child_jobs.py` | Create | Pruebas del reaper (Task 4) |
| `local_control_center/threads/similarity.py` | Modify | Alta, confirmación y match de funcionalidad sólo con evidencia de entrega (Task 5) |
| `tests_py/test_thread_memory_service.py` | Modify | Pruebas de alta/confirmación/match con evidencia y siembra en los tests existentes (Task 5) |
| `tests_py/test_product_loop_coordinator.py` | Modify | Siembra de loop entregado en los 5 tests del gate + regresión (Task 5) |
| `local_control_center/remediations/repository.py` | Modify | Identidad: una acción genérica sin `jobId` se enriquece con el primer job (Task 6) |
| `tests_py/test_remediation_job_lifecycle.py` | Modify | Prueba de la identidad (Task 6) |
| `local_control_center/evidence/repository.py` | Modify | Lecturas livianas y por id de filas inmutables (Task 7) |
| `local_control_center/control_plane/overview.py` | Modify | Caché LRU de compactación de filas inmutables (Task 7) |
| `tests_py/test_overview_immutable_evidence_cache.py` | Create | Equivalencia y reutilización de la caché (Task 7) |
| `local_control_center/product_loop/intent_classifier.py` | Modify | Scores expuestos, `research_only`, vocabulario español plegado, preguntas con clave (Task 8) |
| `local_control_center/threads/coordinator.py` | Modify | Ruteo de intake por `decision.research_only` (Task 8) |
| `tests_py/test_intent_classifier.py` | Modify | Corpus obligatorio parametrizado + claves (Task 8) |
| `tests_py/test_threads_coordinator.py` | Modify | Ruteo del corpus extremo a extremo (Task 8) |
| `local_control_center/research/web_search.py` | Create | Proveedor SearXNG, errores tipados de búsqueda (red/bloqueo), guard de salida HTTP de fuentes públicas con redirects revalidados, resolución por settings (Task 9) |
| `local_control_center/agents/research_agent.py` | Modify | Usa el proveedor configurado, captura errores tipados de búsqueda, descarga fuentes sólo públicas, expone `remediation` (Task 9) |
| `local_control_center/settings/registry.py` | Modify | Settings `research.webSearch.provider` y `.baseUrl` validados (Task 9) |
| `local_control_center/remediations/payloads.py` | Modify | Acciones de research para proveedor bloqueado + `researchRemediation` en el payload (Task 9) |
| `tests_py/test_research_web_search.py` | Create | Servidor HTTP local de prueba, caída del proveedor, SSRF (RFC 1918, metadata, DNS y redirect a privado), settings, remediación (Task 9) |
| `tests_py/test_research_agent.py` | Modify | DDG no-200 bloqueado, run con proveedor bloqueado y con SearXNG caído, fuente no pública nunca descargada, tests que fijan `duckduckgo` (Task 9) |
| `local-control-center/web/src/features/shell/decisionOptionCopy.ts` | Create | Etiquetas/descr. de opciones y texto de preguntas por clave (Task 10) |
| `local-control-center/web/src/features/shell/ThreadConversation.tsx` | Modify | Render de prompt y opciones legibles (Task 10) |
| `local-control-center/web/src/features/shell/ThreadBlockerCard.tsx` | Modify | `<option>` con etiqueta legible (Task 10) |
| `local-control-center/web/src/design-system/layout.css` | Modify | `.thread-decision-option-help` (Task 10) y `data-state="stopped"` (Task 13) |
| `tests_web/thread-hardening.spec.js` | Create | Spec Playwright mockeado de Tasks 10–13 |
| `local-control-center/web/src/features/shell/remediationPresentation.ts` | Modify | Copy de research de intake y de proveedor bloqueado (Task 11) |
| `local-control-center/web/src/features/runtime-setup/reasonCopy.ts` | Create | Mapeo único de códigos de capacidad/salud a texto legible (Task 12) |
| `local-control-center/web/src/features/runtime-setup/RuntimeHealthModal.tsx` | Modify | Importa el mapeo compartido (Task 12) |
| `local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx` | Modify | Banner de capacidad (Task 12) y paso `stopped` (Task 13) |

## Task Graph

```
Carril A1 (backend, sin catálogo)   T2 ──▶ T3 ──▶ T4
Carril A2 (backend, sin catálogo)   T5 ──▶ T6 ──▶ T7
Carril B  (catálogo serializado)    T1 ──▶ T8 ──▶ T9 ──▶ T10 ──▶ T11 ──▶ T12 ──▶ T13
Cierre                              (A1 + A2 + B) ──▶ T14 ──▶ T15 (operador)
```

- **Paralelismo real:** A1, A2 y B tocan conjuntos de archivos disjuntos (ver File Map); pueden correr a la vez en worktrees separados (máx. 3 agentes). Dentro de cada carril las tareas son secuenciales porque comparten archivo de test o el catálogo i18n.
- **Dependencias de contrato (no de archivo):** T12 consume el evento `resource_wait` que produce T3 (payload `{jobId, reasonCode, reason}`, fijado en este plan; el spec Playwright lo mockea, así que T12 no espera a T3 para pasar sus pruebas). T11 consume `payload.researchRemediation === 'research_provider_blocked'` que produce T9. T10 consume `metadata.questionKeys` que produce T8.
- **Complejidad / modelo:**
  - **HIGH (modelo más fuerte):** T4 (muta jobs vivos), T7 (corrección de caché), T8 (semántica del clasificador), T9 (borde de red + validación de URL).
  - **PATTERN (modelo eficiente):** T2, T3, T5, T6, T10, T11, T12, T13.
  - **MECHANICAL (delegable a modelo local, verificado por tests):** T1, T14.
  - **Operador:** T15 (tres pasos con gate de confirmación explícita al momento de ejecutarlos, que pide el orquestador: `docker pull searxng/searxng` en Step 1/2, reinicio de AIDO, y la escritura del zombi en la BD viva en Step 3b, precedida por el respaldo de `platform.sqlite`). Sin "sí" explícito cada paso queda pendiente; ninguna aprobación se generaliza al siguiente.

---

### Task 1: Rojos preexistentes — `@author` y claves i18n idénticas (§2.9 a/b)

**Files:**
- Modify: `local_control_center/agents/agent_resource_policy.py:1`
- Modify: `local_control_center/agents/runtime_preflight.py:1-5`
- Modify: `local_control_center/agents/runtime_preflight_cli.py:1`
- Modify: `local_control_center/executions/timeout_reconciliation.py:1`
- Modify: `local_control_center/product_loop/research_resolution.py:1-4`
- Modify: `local_control_center/product_loop/runtime_risk_review.py:1-4`
- Modify: `local_control_center/i18n/default_catalog.json:11699-11702,11727-11730`
- Test: `tests_py/test_source_documentation_headers.py`, `tests_py/test_i18n_platform.py` (existentes, hoy en rojo)

**Interfaces:**
- Consumes: gates existentes `test_productive_modules_declare_author`, `test_default_i18n_catalog_is_bilingual_and_complete`.
- Produces: catálogo 100 % bilingüe (precondición de Tasks 8–13).

- [ ] **Step 1: Confirmar el rojo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_source_documentation_headers.py','tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"
```
Expected: FAIL en `test_productive_modules_declare_author` (lista los 6 módulos) y en `test_default_i18n_catalog_is_bilingual_and_complete` (`identical` = las 2 claves `riskReview.field.runtime`/`loopId`). Si aparece otro rojo, detenerse y reportarlo (no es de esta tarea).

- [ ] **Step 2: Agregar `@author` a los 6 headers**

`agents/agent_resource_policy.py` línea 1, reemplazar:
```python
"""Effective project role restrictions and their execution-time seal."""
```
por:
```python
"""Effective project role restrictions and their execution-time seal.

@author Rodrigo Mason
"""
```
`agents/runtime_preflight_cli.py` línea 1, reemplazar:
```python
"""Fixed prompt-only CLI validation through the existing managed subprocess boundary."""
```
por:
```python
"""Fixed prompt-only CLI validation through the existing managed subprocess boundary.

@author Rodrigo Mason
"""
```
`executions/timeout_reconciliation.py` línea 1, reemplazar:
```python
"""Close a proven stopped Product Loop without replaying its interrupted workspace."""
```
por:
```python
"""Close a proven stopped Product Loop without replaying its interrupted workspace.

@author Rodrigo Mason
"""
```
`agents/runtime_preflight.py`, reemplazar:
```python
No endpoint, credential, permission or operator selection is repaired implicitly.
"""
```
por:
```python
No endpoint, credential, permission or operator selection is repaired implicitly.

@author Rodrigo Mason
"""
```
`product_loop/research_resolution.py`, reemplazar:
```python
This module never performs research or inference. Metadata supplied by callers is not authority.
"""
```
por:
```python
This module never performs research or inference. Metadata supplied by callers is not authority.

@author Rodrigo Mason
"""
```
`product_loop/runtime_risk_review.py`, reemplazar:
```python
No inference, credential changes, cost approvals or OS permission grants occur here.
"""
```
por:
```python
No inference, credential changes, cost approvals or OS permission grants occur here.

@author Rodrigo Mason
"""
```

- [ ] **Step 3: Traducir las 2 claves idénticas**

En `local_control_center/i18n/default_catalog.json`, reemplazar:
```json
    "app.threads.remediation.riskReview.field.runtime": {
      "en": "Runtime",
      "es": "Runtime"
    },
```
por:
```json
    "app.threads.remediation.riskReview.field.runtime": {
      "en": "Runtime",
      "es": "Entorno de ejecución"
    },
```
y:
```json
    "app.threads.remediation.riskReview.field.loopId": {
      "en": "Loop",
      "es": "Loop"
    },
```
por:
```json
    "app.threads.remediation.riskReview.field.loopId": {
      "en": "Loop",
      "es": "Ciclo"
    },
```
("Entorno de ejecución" es la traducción ya usada para "Runtime" en 5 claves del catálogo.)

- [ ] **Step 4: Verificar verde + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_source_documentation_headers.py','tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/agents/agent_resource_policy.py local_control_center/agents/runtime_preflight.py local_control_center/agents/runtime_preflight_cli.py local_control_center/executions/timeout_reconciliation.py local_control_center/product_loop/research_resolution.py local_control_center/product_loop/runtime_risk_review.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/agents/agent_resource_policy.py local_control_center/agents/runtime_preflight.py local_control_center/agents/runtime_preflight_cli.py local_control_center/executions/timeout_reconciliation.py local_control_center/product_loop/research_resolution.py local_control_center/product_loop/runtime_risk_review.py
```
Expected: PASS en ambos archivos de test; ruff sin cambios ni errores.

- [ ] **Step 5: Commit**

```
git add local_control_center/agents/agent_resource_policy.py local_control_center/agents/runtime_preflight.py local_control_center/agents/runtime_preflight_cli.py local_control_center/executions/timeout_reconciliation.py local_control_center/product_loop/research_resolution.py local_control_center/product_loop/runtime_risk_review.py local_control_center/i18n/default_catalog.json
git commit -m "Fix (Calidad): agrega @author a seis módulos y traduce dos claves i18n idénticas" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Cooldown exponencial del refresco automático de salud (§2.3)

**Files:**
- Modify: `local_control_center/agents/runtime_health_refresh.py:19-27` (imports), `:72-92` (`stale_health_targets`), `:145-190` (`enqueue_stale_health_checks`)
- Test: `tests_py/test_runtime_health_refresh.py` (modificar el test de `:356-370`, agregar uno nuevo)

**Interfaces:**
- Consumes: `needs_refresh(status, *, now)` (`:60-69`), `_pending_health_checks(platform)` (`:95-128`), `enqueue_registered_operation` (`executions/router.py:115`), `platform.db_path`.
- Produces:
  - `BACKOFF_SCHEDULE_SECONDS: tuple[int, int, int] = (60, 300, 900)`
  - `class HealthRefreshBackoff` con `reset(target: tuple[str, str]) -> None`, `allows(target: tuple[str, str], *, now: datetime) -> bool`, `record_enqueued(target: tuple[str, str], *, now: datetime) -> None`
  - `backoff_for(database: Any) -> HealthRefreshBackoff`
  - `enqueue_stale_health_checks(platform, *, statuses=None, now=None, backoff: HealthRefreshBackoff | None = None) -> dict` con clave nueva `"coolingDown": list[str]`.
  - Los health checks del operador (endpoints de API) no pasan por este módulo: ignoran el cooldown por construcción.

- [ ] **Step 1: Escribir el test que falla y ajustar el test que encodifica el comportamiento viejo**

En `tests_py/test_runtime_health_refresh.py` agregar al final:
```python
def _iso_at(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_unproductive_refresh_backs_off_exponentially_and_fresh_evidence_resets_it(tmp_path: Path) -> None:
    """Con el token vencido el check termina sin renovar la evidencia: no puede reencolarse cada ciclo.

    Medido en vivo: 233 checks de claude_code_cli contra 62 de codex_cli en la misma ventana.
    """
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        stale = [_status(id="claude_code_cli", kind="cli", healthCheckedAt=None)]

        def refresh_at(seconds: float, statuses: list[dict] | None = None) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=statuses or stale, now=start + timedelta(seconds=seconds)
            )
            runtime.connection.execute("UPDATE operational_executions SET status='completed'")
            return result

        assert refresh_at(0)["enqueued"] == 1
        cooling = refresh_at(30)
        assert cooling["enqueued"] == 0
        assert cooling["coolingDown"] == ["claude_code_cli"]
        assert refresh_at(61)["enqueued"] == 1
        assert refresh_at(360)["enqueued"] == 0
        assert refresh_at(362)["enqueued"] == 1
        assert refresh_at(1261)["enqueued"] == 0
        assert refresh_at(1263)["enqueued"] == 1
        assert refresh_at(2164)["enqueued"] == 1

        fresh = [
            _status(
                id="claude_code_cli",
                kind="cli",
                healthStatus="healthy",
                healthCheckedAt=_iso_at(start + timedelta(seconds=2170)),
            )
        ]
        assert refresh_at(2175, fresh)["enqueued"] == 0
        assert refresh_at(2180)["enqueued"] == 1
    finally:
        runtime.close()


def test_fresh_but_unhealthy_evidence_does_not_reset_the_backoff(tmp_path: Path) -> None:
    """Cada health check escribe su marca aunque el runtime quede unhealthy: esa marca no es un éxito.

    ``ProviderAccountsRepository.record_health_check`` escribe ``lastHealthCheckAt`` para cualquier
    desenlace y readiness exige además ``healthStatus == "healthy"``; reiniciar el cooldown con una
    marca fresca pero unhealthy devolvería el objetivo al primer escalón en cada ciclo.
    """
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        backoff = HealthRefreshBackoff()
        stale = [_status(id="claude_code_cli", kind="cli", healthCheckedAt=None)]
        unhealthy_fresh = [
            _status(
                id="claude_code_cli",
                kind="cli",
                healthStatus="unhealthy",
                healthCheckedAt=_iso_at(start + timedelta(seconds=65)),
            )
        ]

        def refresh_at(seconds: float, statuses: list[dict]) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=statuses, now=start + timedelta(seconds=seconds), backoff=backoff
            )
            runtime.connection.execute("UPDATE operational_executions SET status='completed'")
            return result

        assert refresh_at(0, stale)["enqueued"] == 1
        assert refresh_at(61, stale)["enqueued"] == 1
        assert refresh_at(70, unhealthy_fresh)["enqueued"] == 0
        cooling = refresh_at(300, stale)
        assert cooling["enqueued"] == 0
        assert cooling["coolingDown"] == ["claude_code_cli"]
        assert refresh_at(362, stale)["enqueued"] == 1
    finally:
        runtime.close()


def test_a_cancelled_refresh_counts_as_unproductive(tmp_path: Path) -> None:
    """Una ejecución cancelada no renovó la evidencia: escala el cooldown igual que un fallo."""
    runtime = _platform(tmp_path)
    try:
        start = datetime.now(UTC)
        backoff = HealthRefreshBackoff()
        stale = [_status(id="codex_cli", kind="cli", healthCheckedAt=None)]

        def refresh_at(seconds: float) -> dict:
            result = enqueue_stale_health_checks(
                runtime, statuses=stale, now=start + timedelta(seconds=seconds), backoff=backoff
            )
            runtime.connection.execute("UPDATE operational_executions SET status='cancelled'")
            return result

        assert refresh_at(0)["enqueued"] == 1
        assert refresh_at(30)["coolingDown"] == ["codex_cli"]
        assert refresh_at(61)["enqueued"] == 1
        assert refresh_at(300)["coolingDown"] == ["codex_cli"]
    finally:
        runtime.close()
```
y agregar `HealthRefreshBackoff,` al import de `local_control_center.agents.runtime_health_refresh` (`:18-24`, orden alfabético: después de `PROVIDER_HEALTH_OPERATION,`).
En `test_refresh_deduplicates_targets_within_one_batch_and_allows_terminal_retry` (`:356-370`) reemplazar:
```python
        result = enqueue_stale_health_checks(runtime, statuses=statuses)
```
por:
```python
        result = enqueue_stale_health_checks(
            runtime, statuses=statuses, now=datetime.now(UTC) + timedelta(seconds=61)
        )
```
(El reintento tras un terminal sigue permitido, pero ahora después del primer cooldown de 60 s.)

- [ ] **Step 2: Ejecutar y ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_health_refresh.py','-q','-p','no:randomly']))"
```
Expected: error de colección `ImportError: cannot import name 'HealthRefreshBackoff'` (el módulo aún no lo define); tras agregar sólo la clase vacía fallarían con `KeyError: 'coolingDown'` / `assert 1 == 0`, porque hoy se reencola en cada ciclo.

- [ ] **Step 3: Implementación mínima**

Imports (`:19-25`), reemplazar:
```python
import logging
from datetime import UTC, datetime
from typing import Any
```
por:
```python
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
```
Después de la definición de `REFRESH_POLL_SECONDS` y su docstring (antes de `logger = logging.getLogger(__name__)`), agregar:
```python
BACKOFF_SCHEDULE_SECONDS = (60, 300, 900)
"""Espera tras el 1.er, 2.º y 3.er (o posterior) refresco improductivo seguido de un objetivo."""


@dataclass
class _TargetBackoff:
    """Historial de refrescos improductivos de un objetivo."""

    failures: int = 0
    awaiting_outcome: bool = False
    attempted_at: datetime | None = None
    retry_after: datetime | None = None


class HealthRefreshBackoff:
    """Cooldown exponencial por objetivo para el refresco automático de salud.

    Un refresco es improductivo cuando su ejecución ya no está pendiente y no dejó evidencia
    vigente y ``healthy``: falló, se canceló o el runtime quedó unhealthy (cada check escribe su
    marca aunque falle, ``provider_accounts.py:605-630``). Tras cada improductivo el objetivo espera
    60 s, luego 5 min y después 15 min como máximo, contados desde el intento; solo una evidencia
    vigente con ``healthStatus == "healthy"`` (el mismo criterio de ``healthy_evidence`` en
    readiness) lo reinicia. Solo gobierna el refresco automático: el health check que dispara el
    operador por la API no pasa por aquí.
    """

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], _TargetBackoff] = {}

    def reset(self, target: tuple[str, str]) -> None:
        """Olvida el historial del objetivo porque su evidencia volvió a estar vigente."""
        self._targets.pop(target, None)

    def allows(self, target: tuple[str, str], *, now: datetime) -> bool:
        """Registra el desenlace improductivo pendiente y dice si el objetivo puede encolarse ya."""
        state = self._targets.get(target)
        if state is None:
            return True
        if state.awaiting_outcome and state.attempted_at is not None:
            state.awaiting_outcome = False
            state.failures += 1
            delay = BACKOFF_SCHEDULE_SECONDS[min(state.failures, len(BACKOFF_SCHEDULE_SECONDS)) - 1]
            state.retry_after = state.attempted_at + timedelta(seconds=delay)
        return state.retry_after is None or now >= state.retry_after

    def record_enqueued(self, target: tuple[str, str], *, now: datetime) -> None:
        """Marca un refresco en vuelo cuyo desenlace se evalúa en el próximo ciclo."""
        state = self._targets.setdefault(target, _TargetBackoff())
        state.awaiting_outcome = True
        state.attempted_at = now


_BACKOFF_BY_DATABASE: dict[str, HealthRefreshBackoff] = {}


def backoff_for(database: Any) -> HealthRefreshBackoff:
    """Devuelve el cooldown del proceso para esa base; el worker vive lo suficiente para recordarlo."""
    key = str(Path(str(database)).resolve(strict=False))
    return _BACKOFF_BY_DATABASE.setdefault(key, HealthRefreshBackoff())
```
Reemplazar `stale_health_targets` completo (`:72-92`) por:
```python
def _refresh_target(status: dict[str, Any]) -> tuple[str, str, str] | None:
    """``(operación, argumento, runtime)`` de un runtime refrescable, o ``None`` si no aplica."""
    kind = str(status.get("kind") or "")
    if kind in SKIPPED_KINDS or not status.get("configured"):
        return None
    if kind == "cli":
        return (CLI_HEALTH_OPERATION, CLI_ARGUMENT, str(status["id"]))
    return (PROVIDER_HEALTH_OPERATION, PROVIDER_ARGUMENT, str(status["id"]))


def stale_health_targets(
    statuses: list[dict[str, Any]], *, now: datetime | None = None
) -> list[tuple[str, str, str]]:
    """Devuelve ``(operación, nombre del argumento, runtime)`` por runtime a refrescar.

    Sólo entra lo que está configurado: refrescar un proveedor sin credencial gastaría una
    ejecución y una llamada de red que se sabe de antemano que va a fallar. ``manual`` queda fuera
    porque no es un runtime automatizado y readiness ya lo trata aparte.
    """
    moment = now or datetime.now(UTC)
    return [
        target
        for status in statuses
        if (target := _refresh_target(status)) is not None and needs_refresh(status, now=moment)
    ]
```
En `enqueue_stale_health_checks` (`:145-190`): cambiar la firma a
```python
def enqueue_stale_health_checks(
    platform: Any,
    *,
    statuses: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    backoff: HealthRefreshBackoff | None = None,
) -> dict[str, Any]:
```
y reemplazar el bloque desde `pending, pending_targets, unresolved_operations = _pending_health_checks(platform)` hasta el `return {...}` final (inclusive) por:
```python
    pending, pending_targets, unresolved_operations = _pending_health_checks(platform)
    moment = now or datetime.now(UTC)
    cooldown = backoff or backoff_for(platform.db_path)
    for status in resolved:
        fresh_target = _refresh_target(status)
        if (
            fresh_target is not None
            and status.get("healthStatus") == "healthy"
            and not needs_refresh(status, now=moment)
        ):
            cooldown.reset((fresh_target[0], fresh_target[2]))

    targets = stale_health_targets(resolved, now=moment)
    handlers = getattr(platform, "execution_handlers", {}) or {}
    enqueued: list[str] = []
    cooling: list[str] = []
    failed = 0
    for operation, argument, runtime_id in targets:
        target = (operation, runtime_id)
        if target in pending_targets or operation in unresolved_operations:
            continue
        if not cooldown.allows(target, now=moment):
            cooling.append(runtime_id)
            continue
        registered = handlers.get(operation)
        if registered is None:
            failed += 1
            logger.warning("Runtime health refresh skipped unregistered operation %s.", operation)
            continue
        try:
            enqueue_registered_operation(platform, registered[0], {argument: runtime_id})
            enqueued.append(runtime_id)
            pending_targets.add(target)
            cooldown.record_enqueued(target, now=moment)
        except Exception as error:  # pragma: no cover - un runtime no puede bloquear a los demás
            failed += 1
            logger.warning("Runtime health refresh failed to enqueue %s: %s", runtime_id, error)
    if enqueued:
        logger.info("Queued %d runtime health checks: %s", len(enqueued), ", ".join(enqueued))
    return {
        "considered": len(resolved),
        "enqueued": len(enqueued),
        "failed": failed,
        "runtimes": enqueued,
        "pending": pending,
        "coolingDown": cooling,
    }
```
(Las líneas `# pragma: no cover` ya existen en el archivo y se conservan tal cual.)

- [ ] **Step 4: Ejecutar tests + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_health_refresh.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/agents/runtime_health_refresh.py tests_py/test_runtime_health_refresh.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/agents/runtime_health_refresh.py tests_py/test_runtime_health_refresh.py
```
Expected: PASS de todo el archivo (incluidos los tests de dedupe/pending previos); ruff limpio.

- [ ] **Step 5: Commit**

```
git add local_control_center/agents/runtime_health_refresh.py tests_py/test_runtime_health_refresh.py
git commit -m "Fix (Runtime): el refresco automático de salud aplica cooldown exponencial por objetivo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `resource_wait` sin spam y con evento de hilo (§2.4 backend)

**Files:**
- Modify: `local_control_center/host_resources/governor.py:8-13` (imports), `:202-229` (`_set_job_status`)
- Test: `tests_py/test_host_resource_governor.py` (agregar al final)

**Interfaces:**
- Consumes: `JobsRepository.update_job_status` (`jobs_approvals/repository.py:746`), `ThreadsRepository.record_event` (`threads/repository.py:560`, reutiliza la transacción del caller vía `_transaction()` `:146-150`).
- Produces: evento de hilo `type="resource_wait"`, `agent_role="worker"`, payload `{"jobId": str, "reasonCode": str, "reason": str}`; un evento de job `job.resource_wait` sólo cuando el estado pasa a espera o cambia `reasonCode`.

- [ ] **Step 1: Test que falla**

Agregar imports en `tests_py/test_host_resource_governor.py`:
```python
from collections import Counter
```
(junto a los imports de stdlib) y
```python
from local_control_center.threads.repository import ThreadsRepository
```
(junto a los imports de `local_control_center`). Agregar al final:
```python
def test_resource_wait_is_written_once_per_reason_and_reaches_the_thread(tmp_path: Path) -> None:
    """El governor reevalúa cada ~2 s: sólo un cambio de motivo es noticia para el job y el hilo."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="AIDO", path=tmp_path, template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-capacity",
            title="Capacity wait",
            summary="",
        )
        job = JobsRepository(connection).create_job(
            project_id=project["id"], kind="thread.product_loop.run", payload={"threadId": thread["id"]}
        )["job"]
        governor = HostResourceGovernor(connection)
        for _ in range(5):
            governor.admit(
                _request(job["id"], "agent_cli", job_id=job["id"]),
                snapshot=_healthy_snapshot(available_memory_bytes=4 * GIB),
            )
        governor.admit(
            _request(job["id"], "agent_cli", job_id=job["id"]),
            snapshot=_healthy_snapshot(available_memory_bytes=12 * GIB),
        )
        job_events = [
            event
            for event in JobsRepository(connection).list_events(project["id"])
            if event["type"] == "job.resource_wait"
        ]
        thread_events = [
            event
            for event in ThreadsRepository(connection).list_events(thread["id"])
            if event["type"] == "resource_wait"
        ]

    assert Counter(event["payload"]["reasonCode"] for event in job_events) == {
        "hard_memory_floor": 1,
        "minimum_free_memory": 1,
    }
    assert [event["payload"]["reasonCode"] for event in thread_events] == [
        "hard_memory_floor",
        "minimum_free_memory",
    ]
    assert thread_events[0]["payload"]["jobId"] == job["id"]
    assert thread_events[0]["agentRole"] == "worker"
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_host_resource_governor.py','-q','-p','no:randomly']))"
```
Expected: FAIL en el test nuevo: `Counter` con `hard_memory_floor: 5` y `thread_events == []`.

- [ ] **Step 3: Implementación mínima**

Imports de `governor.py`, reemplazar:
```python
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.time import utc_now
```
por:
```python
from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_loads
from local_control_center.shared.time import utc_now
```
Reemplazar `_set_job_status` completo (`:202-229`) por:
```python
    def _set_job_status(
        self,
        job_id: str | None,
        status: str,
        decision: ResourceAdmissionDecision,
    ) -> None:
        if not job_id:
            return
        row = self.connection.execute("SELECT status, payload FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        current = str(row["status"])
        if status == "resource_wait" and current not in {"queued", "resource_wait"}:
            return
        if status == "queued" and current != "resource_wait":
            return
        payload = json_loads(row["payload"], {})
        if (
            status == "resource_wait"
            and current == "resource_wait"
            and _last_reason_code(payload) == decision.reason_code
        ):
            return
        JobsRepository(self.connection).update_job_status(
            job_id,
            status=status,
            metadata={
                "resourceAdmission": decision.status,
                "reasonCode": decision.reason_code,
                "reason": decision.reason,
                "leaseId": decision.lease.id if decision.lease else None,
            },
        )
        if status == "resource_wait":
            self._record_thread_resource_wait(job_id, payload, decision)

    def _record_thread_resource_wait(
        self, job_id: str, payload: dict[str, Any], decision: ResourceAdmissionDecision
    ) -> None:
        """Avisa al hilo dueño del job por qué espera capacidad; un hilo ya borrado se omite."""
        thread_id = str(payload.get("threadId") or "").strip()
        if not thread_id:
            return
        from local_control_center.threads.repository import ThreadsRepository

        try:
            ThreadsRepository(self.connection).record_event(
                thread_id=thread_id,
                type="resource_wait",
                agent_role="worker",
                payload={
                    "jobId": job_id,
                    "reasonCode": decision.reason_code,
                    "reason": decision.reason,
                },
            )
        except KeyError:
            return
```
Y a nivel de módulo (después de la clase o antes de ella, fuera de `HostResourceGovernor`):
```python
def _last_reason_code(payload: dict[str, Any]) -> str | None:
    """Motivo de admisión que el governor ya dejó escrito en ``payload.result`` del job."""
    result = payload.get("result") if isinstance(payload, dict) else None
    return str(result.get("reasonCode")) if isinstance(result, dict) and result.get("reasonCode") else None
```

- [ ] **Step 4: Tests + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_host_resource_governor.py','tests_py/test_local_worker_runtime.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/host_resources/governor.py tests_py/test_host_resource_governor.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/host_resources/governor.py tests_py/test_host_resource_governor.py
```
Expected: PASS en `test_host_resource_governor.py`; `test_local_worker_runtime.py` igual que antes de la tarea (su único rojo conocido es `test_worker_product_loop_exception_creates_worker_retry_remediation`, que resuelve Task 6); ruff limpio.

- [ ] **Step 5: Commit**

```
git add local_control_center/host_resources/governor.py tests_py/test_host_resource_governor.py
git commit -m "Fix (Recursos): resource_wait se escribe solo al cambiar el motivo y avisa al hilo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Reaper de jobs hijos inline zombi (§2.8)

**Files:**
- Modify: `local_control_center/jobs_approvals/repository.py` tras `:27` (`from .models import SENSITIVE_JOB_KINDS`), `:181-184` (`create_job`), `:976` (fin de `requeue_expired_jobs`), `:1064-1071` (`_complete_job_run_in_transaction`)
- Create: `tests_py/test_orphan_child_jobs.py`

**Interfaces:**
- Consumes: `CURRENT_EXECUTION` (`process_supervision/context.py:40`), `execution_scope` (`:48`), `update_job_status` (`:746`), `claim_next_job` (`:774`), `complete_job_run` (`:978`).
- Produces:
  - `NESTED_AGENT_JOB_KINDS: tuple[str, ...] = ("agent.product_owner", "agent.architect", "agent.devops", "agent.developer", "agent.project_assessment")`
  - `payload["parentJobId"]` en todo job creado `running` dentro de un `execution_scope` con `execution_id`.
  - `JobsRepository.fail_orphaned_child_jobs(*, parent_job_id: str | None = None, now_iso: str | None = None) -> list[str]` (ids fallados; SÓLO hijos con `parentJobId` comprobable cuyo padre ya no corre; job `failed` con `payload.result.reason == "parent_lease_expired"`, evento `job.failed`, `agent_runs`/`job_runs` `running` → `failed`). Sin regla por antigüedad: un hijo sin vínculo nunca se falla automáticamente.
  - `JobsRepository.fail_legacy_orphan_child_jobs(job_ids: Iterable[str], *, now_iso: str | None = None) -> list[str]`: saneamiento histórico por IDs explícitos, que sólo falla los que siguen cumpliendo `running` + sin lease + kind anidado + sin `parentJobId` (cualquier otro id se ignora). Lo invoca el operador en Task 15 para `job-31a6b390…`.
- Vínculo: `workflow_run_id` NO sirve para hallar al padre. Verificado read-only en la BD viva: el zombi tiene `workflow_run_id = 'product-loop-06816d8e-…'` (id del product loop, `product_loops.status='active'`, `state='discovery'`), es el único job con ese valor, y su padre real `thread.product_loop.run` (`job-fafc33ab…`, `completed`) tiene `workflow_run_id NULL`. El único vínculo comprobable es `parentJobId`, que este task escribe al crear el hijo; el "o" del spec §2.8 se satisface con él.

- [ ] **Step 1: Tests que fallan**

Crear `tests_py/test_orphan_child_jobs.py`:
```python
"""Jobs hijos inline zombi: un hijo `running` sin lease se falla cuando su padre ya no corre.

Los agentes anidados (ProductOwner, Architect, DevOps, Developer, assessment) crean su job `running`
sin lease dentro del job del worker; ``requeue_expired_jobs`` exige lease, así que cuando el padre
pierde el suyo el hijo quedaba `running` para siempre (caso vivo: job-31a6b390, desde 2026-07-18).

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def _project(connection: sqlite3.Connection, tmp_path: Path) -> dict:
    return ProjectsRepository(connection).create_project(
        name="Orphan children", path=tmp_path / "project", template_id="other"
    )


def _child_inside(jobs: JobsRepository, project_id: str, parent_id: str, db_path: Path) -> dict:
    with execution_scope(ProcessExecutionContext(db_path=db_path, execution_id=parent_id)):
        return jobs.create_job(
            project_id=project_id, kind="agent.product_owner", status="running", payload={"taskId": "child"}
        )["job"]


def _agent_run(connection: sqlite3.Connection, project_id: str, job_id: str) -> str:
    run_id = f"agent-run-{job_id}"
    connection.execute(
        """
        INSERT INTO agent_runs
            (id, project_id, job_id, workflow_run_id, workflow_step_id, status, input, output, metadata,
             created_at, updated_at)
        VALUES (?, ?, ?, NULL, NULL, 'running', '{}', '{}', '{}',
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (run_id, project_id, job_id),
    )
    return run_id


def _claimed_parent(jobs: JobsRepository, project_id: str) -> tuple[dict, dict]:
    parent = jobs.create_job(project_id=project_id, kind="thread.product_loop.run", payload={"threadId": "t"})[
        "job"
    ]
    claimed = jobs.claim_next_job(worker_id="worker-1", job_id=parent["id"])
    return claimed["job"], claimed["run"]


def test_child_created_inside_a_job_records_its_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)
        outside = jobs.create_job(project_id=project["id"], kind="agent.architect", status="running")["job"]

    assert child["payload"]["parentJobId"] == parent["id"]
    assert "parentJobId" not in outside["payload"]


def test_parent_lease_expiry_fails_its_running_child_and_agent_run(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)
        run_id = _agent_run(connection, project["id"], child["id"])
        connection.execute(
            "UPDATE jobs SET lease_expires_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (parent["id"],)
        )

        jobs.requeue_expired_jobs(now_iso="2026-01-01T00:05:00.000Z")
        child_after = jobs.get_job(child["id"])
        run_status = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()[0]
        parent_after = jobs.get_job(parent["id"])

    assert parent_after["status"] == "queued"
    assert child_after["status"] == "failed"
    assert child_after["payload"]["result"]["reason"] == "parent_lease_expired"
    assert run_status == "failed"


def test_completing_the_parent_fails_a_child_left_running(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)

        jobs.complete_job_run(job_id=parent["id"], run_id=run["id"], status="completed", summary="done")

        assert jobs.get_job(child["id"])["status"] == "failed"


def test_child_of_a_running_parent_is_left_alone(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        parent, _run = _claimed_parent(jobs, project["id"])
        child = _child_inside(jobs, project["id"], parent["id"], db_path)

        assert jobs.fail_orphaned_child_jobs() == []
        assert jobs.get_job(child["id"])["status"] == "running"


def test_child_without_a_provable_parent_is_never_reaped_by_age(tmp_path: Path) -> None:
    """Sin ``parentJobId`` no hay prueba de que el padre murió: la antigüedad sola no basta."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        old = jobs.create_job(
            project_id=project["id"],
            kind="agent.product_owner",
            status="running",
            workflow_run_id="product-loop-legacy",
        )["job"]
        connection.execute(
            "UPDATE jobs SET updated_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (old["id"],)
        )

        reaped = jobs.fail_orphaned_child_jobs(now_iso="2027-01-01T00:00:00.000Z")
        jobs.requeue_expired_jobs(now_iso="2027-01-01T00:00:00.000Z")

        assert reaped == []
        assert jobs.get_job(old["id"])["status"] == "running"


def test_legacy_cleanup_fails_only_validated_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "platform.sqlite"
    with closing(open_sqlite_connection(db_path)) as connection, connection:
        initialize_platform_schema(connection)
        project = _project(connection, tmp_path)
        jobs = JobsRepository(connection)
        legacy = jobs.create_job(project_id=project["id"], kind="agent.product_owner", status="running")["job"]
        run_id = _agent_run(connection, project["id"], legacy["id"])
        parent, _run = _claimed_parent(jobs, project["id"])
        linked = _child_inside(jobs, project["id"], parent["id"], db_path)
        finished = jobs.create_job(project_id=project["id"], kind="agent.developer", status="completed")["job"]

        reaped = jobs.fail_legacy_orphan_child_jobs(
            [legacy["id"], linked["id"], finished["id"], parent["id"], "job-missing"],
            now_iso="2026-01-01T00:05:00.000Z",
        )
        run_status = connection.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()[0]

        assert reaped == [legacy["id"]]
        assert jobs.get_job(legacy["id"])["payload"]["result"]["reason"] == "parent_lease_expired"
        assert run_status == "failed"
        assert jobs.get_job(linked["id"])["status"] == "running"
        assert jobs.get_job(finished["id"])["status"] == "completed"
        assert jobs.get_job(parent["id"])["status"] == "running"
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_orphan_child_jobs.py','-q','-p','no:randomly']))"
```
Expected: FAIL: `KeyError: 'parentJobId'`, hijos siguen `running`, y `AttributeError: 'JobsRepository' object has no attribute 'fail_orphaned_child_jobs'` / `'fail_legacy_orphan_child_jobs'`.

- [ ] **Step 3: Implementación mínima**

Imports (`:15-19`): sin cambios (`Iterable` ya está importado y lo usa `fail_legacy_orphan_child_jobs`).
Después de `from .models import SENSITIVE_JOB_KINDS` agregar la constante y el helper de módulo:
```python
NESTED_AGENT_JOB_KINDS = (
    "agent.product_owner",
    "agent.architect",
    "agent.devops",
    "agent.developer",
    "agent.project_assessment",
)
"""Kinds de agentes anidados que se crean `running` sin lease dentro del job del worker."""


def _current_parent_job_id() -> str | None:
    """Job del worker que ejecuta este código, si lo hay: es el padre de un job hijo inline."""
    from local_control_center.process_supervision.context import CURRENT_EXECUTION

    context = CURRENT_EXECUTION.get()
    return context.execution_id if context is not None and context.execution_id else None
```
En `create_job`, inmediatamente después de:
```python
        resolved_status = status or ("approval_required" if needs_approval else "queued")
```
agregar:
```python
        parent_job_id = _current_parent_job_id()
        if resolved_status == "running" and parent_job_id and "parentJobId" not in payload:
            payload = {**payload, "parentJobId": parent_job_id}
```
Al final de `requeue_expired_jobs`, reemplazar la línea `:976`:
```python
        return recovered
```
por:
```python
        self.fail_orphaned_child_jobs(now_iso=now_value)
        return recovered
```
En `_complete_job_run_in_transaction`, inmediatamente después del bloque:
```python
        job = self.get_job(job_id)
        self.record_event(
            project_id=job["projectId"],
            job_id=job_id,
            event_type=f"job.{job_status}",
            payload={"summary": clean_summary, "metadata": clean_metadata},
        )
```
agregar:
```python
        self.fail_orphaned_child_jobs(parent_job_id=job_id, now_iso=timestamp)
```
Agregar los métodos en `JobsRepository` (después de `requeue_expired_jobs`):
```python
    def fail_orphaned_child_jobs(
        self, *, parent_job_id: str | None = None, now_iso: str | None = None
    ) -> list[str]:
        """Falla los jobs hijos inline que quedaron `running` sin lease cuando su padre ya no corre.

        Un hijo creado `running` dentro de otro job no tiene lease propio y ``requeue_expired_jobs``
        nunca lo ve. Sólo se actúa con vínculo comprobable: si su ``parentJobId`` apunta a un job que
        ya no está `running` (terminó, se reencoló o no existe), el hijo es un zombi y se marca
        `failed` con motivo `parent_lease_expired`, cerrando sus `agent_runs` y `job_runs`. Un hijo
        sin ``parentJobId`` nunca se falla por antigüedad (``workflow_run_id`` identifica el product
        loop, no el job padre); el saneamiento histórico va por ``fail_legacy_orphan_child_jobs``.
        Usa la transacción del caller.
        """
        now_value = now_iso or utc_now()
        placeholders = ",".join("?" for _ in NESTED_AGENT_JOB_KINDS)
        rows = self.connection.execute(
            f"""
            SELECT child.id FROM jobs child
            LEFT JOIN jobs parent ON parent.id = json_extract(child.payload, '$.parentJobId')
            WHERE child.status = 'running'
              AND child.lease_expires_at IS NULL
              AND child.kind IN ({placeholders})
              AND json_extract(child.payload, '$.parentJobId') IS NOT NULL
              AND (? IS NULL OR json_extract(child.payload, '$.parentJobId') = ?)
              AND (parent.id IS NULL OR parent.status <> 'running')
            ORDER BY child.created_at ASC, child.rowid ASC
            """,
            (*NESTED_AGENT_JOB_KINDS, parent_job_id, parent_job_id),
        ).fetchall()
        return [self._fail_orphan_child(str(row["id"]), now_value) for row in rows]

    def fail_legacy_orphan_child_jobs(self, job_ids: Iterable[str], *, now_iso: str | None = None) -> list[str]:
        """Saneamiento histórico: falla hijos zombi anteriores a ``parentJobId`` por id explícito.

        Cada id sólo se falla si sigue `running`, sin lease, con kind anidado y sin ``parentJobId``;
        cualquier otro (inexistente, terminado, con padre vivo o con vínculo) se ignora. El operador
        entrega los ids tras inspeccionarlos (Task 15); nunca se invoca con una consulta amplia.
        """
        requested = [str(job_id) for job_id in job_ids if str(job_id).strip()]
        if not requested:
            return []
        now_value = now_iso or utc_now()
        kind_placeholders = ",".join("?" for _ in NESTED_AGENT_JOB_KINDS)
        id_placeholders = ",".join("?" for _ in requested)
        rows = self.connection.execute(
            f"""
            SELECT id FROM jobs
            WHERE id IN ({id_placeholders})
              AND status = 'running'
              AND lease_expires_at IS NULL
              AND kind IN ({kind_placeholders})
              AND json_extract(payload, '$.parentJobId') IS NULL
            ORDER BY created_at ASC, rowid ASC
            """,
            (*requested, *NESTED_AGENT_JOB_KINDS),
        ).fetchall()
        return [self._fail_orphan_child(str(row["id"]), now_value) for row in rows]

    def _fail_orphan_child(self, child_id: str, now_value: str) -> str:
        """Marca el hijo `failed` por `parent_lease_expired` y cierra sus `agent_runs`/`job_runs` abiertos."""
        self.update_job_status(child_id, status="failed", metadata={"reason": "parent_lease_expired"})
        self.connection.execute(
            "UPDATE agent_runs SET status = 'failed', updated_at = ? WHERE job_id = ? AND status = 'running'",
            (now_value, child_id),
        )
        self.connection.execute(
            """
            UPDATE job_runs SET status = 'failed', completed_at = ?, summary = 'parent_lease_expired'
            WHERE job_id = ? AND status = 'running'
            """,
            (now_value, child_id),
        )
        return child_id
```

- [ ] **Step 4: Tests + regresión del repositorio + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_orphan_child_jobs.py','tests_py/test_host_resource_governor.py','tests_py/test_execution_timeout_reconciliation.py','tests_py/test_remediation_job_lifecycle.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/jobs_approvals/repository.py tests_py/test_orphan_child_jobs.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/jobs_approvals/repository.py tests_py/test_orphan_child_jobs.py
```
Expected: PASS (los otros tres archivos ejercitan `requeue_expired_jobs`/`complete_job_run` y no deben cambiar); ruff limpio.

- [ ] **Step 5: Commit**

```
git add local_control_center/jobs_approvals/repository.py tests_py/test_orphan_child_jobs.py
git commit -m "Fix (Jobs): falla los jobs hijos inline que quedan running cuando su padre ya no corre" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: La funcionalidad existente sólo bloquea si su hilo entregó (§2.6)

**Files:**
- Modify: `local_control_center/threads/similarity.py:34-37` (constantes), `:212-235` (`ensure_project_functionality`), `:237-244` (`reindex_thread_memory`), `:246-255` (`reindex_project_memory`), `:375-387` (`find_existing_functionality`)
- Test: `tests_py/test_thread_memory_service.py` (agregar; sembrar loop entregado en `:89`, `:129`, `:164`, `:246`, `:271`; test nuevo de alta/confirmación), `tests_py/test_product_loop_coordinator.py:6803-6804,6888-6889,6927-6928,6991-6992,7065-7066` (sembrar loop entregado ANTES del reindex) + test nuevo

**Interfaces:**
- Consumes: `product_loops.context.durableRun.thread.projectThreadId` (enlace hilo↔loop verificado en la BD viva), `product_briefs.status='approved'` (escrito por `product_loop/api.py:300-306`).
- Produces: `_DELIVERY_EVIDENCE_CONDITION` (fragmento SQL con `{thread_id}`), `ThreadMemoryService.has_delivery_evidence(thread_id: str) -> bool`. Regla del spec §2.6 aplicada en los tres puntos: (1) **alta**: `ensure_project_functionality`, `reindex_thread_memory` y `reindex_project_memory` sólo materializan hilos `resolved|archived` con evidencia de entrega; (2) **confirmación**: esos mismos caminos dejan de re-upsertar (actualizar) registros de hilos sin evidencia; (3) **consulta**: `find_existing_functionality` ignora registros heredados (las 35 filas vivas ya materializadas) cuyo `sourceThreadId` no tenga evidencia, sin borrarlos. `mark_similarity` (`:479`) no cambia: registra una decisión explícita del operador sobre ese candidato, que es evidencia más fuerte que la heurística; el listado y su endpoint tampoco (spec §4).

- [ ] **Step 1: Tests que fallan**

En `tests_py/test_thread_memory_service.py` agregar `import json` al bloque de imports de stdlib (`:8-10`; el resto de imports que usa el código ya existe en `:9-18`) y al final:
```python
def _delivered_loop(connection, project_id: str, thread_id: str, *, state: str = "delivered", initiative_id=None) -> None:
    connection.execute(
        """
        INSERT INTO product_loops
            (id, project_id, initiative_id, title, state, previous_state, status, context, version,
             created_at, updated_at)
        VALUES (?, ?, ?, 'Seeded loop', ?, NULL, 'active', ?, 1,
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (
            f"loop-{state}-{thread_id}",
            project_id,
            initiative_id,
            state,
            json.dumps({"durableRun": {"thread": {"projectThreadId": thread_id}}}),
        ),
    )


def _archived_functionality_thread(connection, tmp_path) -> tuple[dict, dict]:
    project = ProjectsRepository(connection).create_project(
        name="Functionality evidence", path=tmp_path / "project", template_id="other"
    )
    repo = ThreadsRepository(connection)
    thread = repo.create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id="workspace-evidence",
        title="Workspace dashboard filters",
        summary="Filters for the active workspace dashboard.",
    )
    repo.set_status(thread["id"], "archived")
    ThreadMemoryService(connection).reindex_thread_memory(thread["id"])
    return project, thread


def test_functionality_from_a_thread_that_never_delivered_is_not_a_match(tmp_path) -> None:
    """Caso vivo: un hilo archivado sin ejecución quedó registrado y bloqueó al siguiente (0.627)."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(connection, project["id"], thread["id"], state="cancelled")

        matches = ThreadMemoryService(connection).find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert matches == []


def test_functionality_from_a_delivered_thread_still_matches(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(connection, project["id"], thread["id"])

        matches = ThreadMemoryService(connection).find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert [match["sourceThreadId"] for match in matches] == [thread["id"]]


def test_functionality_with_an_approved_brief_matches(tmp_path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        _delivered_loop(
            connection, project["id"], thread["id"], state="planning", initiative_id="initiative-approved"
        )
        connection.execute(
            """
            INSERT INTO product_briefs
                (id, project_id, initiative_id, title, status, summary, problem_statement, goals,
                 target_users, success_metrics, scope, out_of_scope, version, created_at, updated_at)
            VALUES ('brief-approved', ?, 'initiative-approved', 'Brief', 'approved', '', '', '[]', '[]',
                    '[]', '[]', '[]', 1, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
            """,
            (project["id"],),
        )

        service = ThreadMemoryService(connection)
        assert service.has_delivery_evidence(thread["id"]) is True
        matches = service.find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert [match["sourceThreadId"] for match in matches] == [thread["id"]]


def test_thread_without_delivery_evidence_neither_creates_nor_confirms_functionality(tmp_path) -> None:
    """Spec §2.6: el registro se crea o se confirma sólo con entrega; lo heredado no bloquea."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project, thread = _archived_functionality_thread(connection, tmp_path)
        service = ThreadMemoryService(connection)

        assert connection.execute("SELECT COUNT(*) FROM functionality_registry").fetchone()[0] == 0
        assert service.reindex_thread_memory(thread["id"])["functionality"] is None
        assert service.ensure_project_functionality(project["id"]) == 0

        connection.execute(
            """
            INSERT INTO functionality_registry
                (id, project_id, name, summary, normalized_name, fingerprint, source_thread_id, status,
                 file_paths_json, performance_notes_json, metadata, created_at, updated_at)
            VALUES ('functionality-legacy', ?, 'Workspace dashboard filters', '', 'workspace dashboard filters',
                    'legacy-fingerprint', ?, 'archived', '[]', '[]', '{}',
                    '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
            """,
            (project["id"], thread["id"]),
        )
        service.reindex_project_memory(project["id"])
        service.ensure_project_functionality(project["id"])
        legacy_updated_at = connection.execute(
            "SELECT updated_at FROM functionality_registry WHERE id = 'functionality-legacy'"
        ).fetchone()[0]
        matches = service.find_existing_functionality(
            project_id=project["id"], query="Improve the workspace dashboard filters"
        )

    assert legacy_updated_at == "2026-01-01T00:00:00.000Z"
    assert matches == []
```
En los 5 tests existentes de `tests_py/test_thread_memory_service.py` que esperan un registro materializado, sembrar el loop entregado inmediatamente ANTES del primer reindex (el helper `_delivered_loop` se define al final del módulo; se resuelve en tiempo de ejecución):
- `test_reindex_thread_memory_persists_resolved_functionality` (`:118-119`), `test_reindex_existing_functionality_updates_changed_fingerprint_without_id_collision` (`:144-145`) y `test_reindex_thread_memory_extracts_file_paths_from_artifacts_and_decisions` (`:198`): antes de la línea que llama `reindex_thread_memory(thread["id"])` por primera vez, agregar `        _delivered_loop(connection, project_id, thread["id"])`.
- `test_project_functionality_endpoint_lists_registry` (`:259`) y `test_reindex_endpoint_refreshes_thread_memory` (`:284`, antes de `response = client.post(`): agregar `        _delivered_loop(runtime.connection, project_id, thread["id"])`.
- `test_performance_pass_links_similarity_event_to_functionality_registry` (`:206`) no cambia: `mark_similarity` materializa por decisión explícita del operador.

En `tests_py/test_product_loop_coordinator.py` agregar el helper (a nivel de módulo, antes de `test_run_user_message_blocks_existing_functionality_before_runtime_execution`):
```python
def _seed_delivered_loop(connection, project_id: str, thread_id: str) -> None:
    """La funcionalidad sólo cuenta como existente si su hilo entregó: siembra ese loop entregado."""
    connection.execute(
        """
        INSERT INTO product_loops
            (id, project_id, initiative_id, title, state, previous_state, status, context, version,
             created_at, updated_at)
        VALUES (?, ?, NULL, 'Delivered loop', 'delivered', 'awaiting_approval', 'active', ?, 1,
                '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')
        """,
        (
            f"loop-delivered-{thread_id}",
            project_id,
            json.dumps({"durableRun": {"thread": {"projectThreadId": thread_id}}}),
        ),
    )
```
y en cada uno de los 5 tests del gate (líneas `:6804`, `:6889`, `:6928`, `:6992`, `:7066`), inmediatamente ANTES de:
```python
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])
```
agregar (antes, porque el reindex ya no materializa un hilo sin evidencia de entrega):
```python
        _seed_delivered_loop(connection, project["id"], existing["id"])
```
Agregar después de `test_run_user_message_blocks_existing_functionality_before_runtime_execution`:
```python
def test_run_user_message_ignores_functionality_from_a_thread_that_never_delivered(tmp_path: Path) -> None:
    runtime = _ControlledRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = _workspace_project(connection, tmp_path, "undelivered-functionality")
        repo = ThreadsRepository(connection)
        existing = repo.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id="workspace-existing",
            title="Workspace dashboard filters",
            summary="Cancelled before it ran.",
        )
        repo.set_status(existing["id"], "archived")
        ThreadMemoryService(connection).reindex_thread_memory(existing["id"])

        result = ProductLoopCoordinator(connection, root=tmp_path).run_user_message(
            project_id=project["id"],
            message="Improve the workspace dashboard filters and add a performance pass.",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
        )

    assert result["loop"]["context"]["durableRun"].get("blockedStage") != "functionality_memory"
    assert "existingFunctionality" not in result["loop"]["context"]["durableRun"]
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_memory_service.py','tests_py/test_product_loop_coordinator.py','-q','-p','no:randomly','-k','functionality']))"
```
Expected: FAIL en `test_functionality_from_a_thread_that_never_delivered_is_not_a_match` (devuelve 1 match), en `test_functionality_with_an_approved_brief_matches` (`AttributeError: has_delivery_evidence`), en `test_thread_without_delivery_evidence_neither_creates_nor_confirms_functionality` (el reindex materializa: `COUNT(*) == 1`) y en `test_run_user_message_ignores_functionality_from_a_thread_that_never_delivered` (`blockedStage == 'functionality_memory'`). Los 5 tests del gate y los 5 existentes del servicio siguen en verde (el loop sembrado no altera nada todavía).

- [ ] **Step 3: Implementación mínima**

En `threads/similarity.py`, después de `FUNCTIONALITY_MATCH_THRESHOLD = 0.45` agregar:
```python
FUNCTIONALITY_MATCH_CANDIDATES = 20
"""Candidatos léxicos revisados antes de exigir evidencia de entrega al mejor match."""
_DELIVERY_EVIDENCE_CONDITION = """
EXISTS (
    SELECT 1 FROM product_loops l
    WHERE json_extract(l.context, '$.durableRun.thread.projectThreadId') = {thread_id}
      AND (
        l.state = 'delivered'
        OR EXISTS (
            SELECT 1 FROM product_briefs b
            WHERE b.initiative_id = l.initiative_id AND b.status = 'approved'
        )
      )
)
"""
"""Condición SQL: el hilo ``{thread_id}`` tiene un loop entregado o un brief aprobado de su iniciativa."""
```
En `ensure_project_functionality` (`:212-235`): actualizar el docstring a `"""Materializa funcionalidad sólo para threads resueltos/archivados que entregaron y sin registro fresco.` (resto igual), agregar como primera línea del cuerpo tras el docstring `delivery = _DELIVERY_EVIDENCE_CONDITION.format(thread_id="t.id")` y, en el `WHERE` de la consulta, inmediatamente después de `AND t.status IN ({placeholders})` agregar la línea `              AND {delivery}` (la consulta ya es f-string).
En `reindex_thread_memory` (`:238-244`) reemplazar:
```python
        if index["status"] in FUNCTIONALITY_SOURCE_STATUSES:
            functionality = self.upsert_functionality_from_thread(thread_id)
```
por:
```python
        if index["status"] in FUNCTIONALITY_SOURCE_STATUSES and self.has_delivery_evidence(thread_id):
            functionality = self.upsert_functionality_from_thread(thread_id)
```
En `reindex_project_memory` (`:246-255`) reemplazar:
```python
            if row["status"] in FUNCTIONALITY_SOURCE_STATUSES:
                self.upsert_functionality_from_thread(row["id"])
```
por:
```python
            if row["status"] in FUNCTIONALITY_SOURCE_STATUSES and self.has_delivery_evidence(row["id"]):
                self.upsert_functionality_from_thread(row["id"])
```
Reemplazar `find_existing_functionality` (`:375-387`) por:
```python
    def find_existing_functionality(
        self,
        *,
        project_id: str,
        query: str,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Busca funcionalidad existente parecida a la solicitud y que su hilo haya entregado.

        Un registro materializado desde un hilo cancelado o archivado sin ejecución no prueba que
        la funcionalidad exista: sólo cuenta si el hilo tiene un loop ``delivered`` o un brief
        aprobado (``has_delivery_evidence``).
        """
        candidates = self.list_project_functionality(
            project_id=project_id, query=query, limit=FUNCTIONALITY_MATCH_CANDIDATES
        )
        matches = [
            item
            for item in candidates
            if float(item.get("score") or 0.0) >= FUNCTIONALITY_MATCH_THRESHOLD
            and self.has_delivery_evidence(str(item.get("sourceThreadId") or ""))
        ]
        return matches[: max(1, int(limit))]

    def has_delivery_evidence(self, thread_id: str) -> bool:
        """Dice si el hilo entregó: tiene un loop ``delivered`` o un brief aprobado de su iniciativa."""
        if not thread_id:
            return False
        condition = _DELIVERY_EVIDENCE_CONDITION.format(thread_id="?")
        row = self.connection.execute(f"SELECT {condition} AS delivered", (thread_id,)).fetchone()
        return bool(row[0])
```

- [ ] **Step 4: Tests + verificación contra la copia de la BD viva + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_memory_service.py','tests_py/test_thread_similarity_service.py','tests_py/test_product_loop_coordinator.py','tests_py/test_threads_coordinator.py','-q','-p','no:randomly','-k','functionality or similarity or memory']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/threads/similarity.py tests_py/test_thread_memory_service.py tests_py/test_product_loop_coordinator.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/threads/similarity.py tests_py/test_thread_memory_service.py tests_py/test_product_loop_coordinator.py
```
Expected: PASS; ruff limpio.

Punto de verificación del spec (read-only, sobre copia en el scratchpad del agente, nunca sobre la BD viva). Guardar como `<scratchpad>/verify_functionality.py` y ejecutar con `PYTHONPATH` = raíz del repo:
```python
import sqlite3
import sys

sys.modules["faiss"] = None
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.threads.similarity import ThreadMemoryService

copy_path = sys.argv[1]
source = sqlite3.connect("file:C:/Users/Rodd/.claude/local-control-center/platform.sqlite?mode=ro", uri=True)
target = sqlite3.connect(copy_path)
source.backup(target)
target.close()
source.close()
connection = open_sqlite_connection(copy_path)
thread_id = "thread-d3c5dc0d-446d-4a08-ade8-87dbaa07a243"
project_id = connection.execute("SELECT project_id FROM project_threads WHERE id = ?", (thread_id,)).fetchone()[0]
message = connection.execute(
    "SELECT content FROM thread_messages WHERE thread_id = ? AND kind = 'user' ORDER BY created_at LIMIT 1",
    (thread_id,),
).fetchone()[0]
print(ThreadMemoryService(connection).find_existing_functionality(project_id=project_id, query=message))
```
Expected: imprime `[]` (antes del fix devolvía `functionality-9fbcd16c…` con score 0.632).

- [ ] **Step 5: Commit**

```
git add local_control_center/threads/similarity.py tests_py/test_thread_memory_service.py tests_py/test_product_loop_coordinator.py
git commit -m "Fix (Threads): la funcionalidad solo se registra, confirma y bloquea si su hilo entregó" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: El fallo de un job enriquece la acción genérica del worker (§2.9c)

**Files:**
- Modify: `local_control_center/remediations/repository.py:109-113` (`same_action_identity`)
- Test: `tests_py/test_remediation_job_lifecycle.py` (agregar), `tests_py/test_local_worker_runtime.py:612-700` (existente, hoy en rojo)

**Interfaces:**
- Consumes: `RemediationActionsRepository.create_action` (`:60`), `BlockerRemediationService.ensure_worker_remediation` (`service.py:250-285`).
- Produces: regla de identidad de acciones sin loop — una acción pendiente **sin** `jobId` es adoptada (y su payload fusionado) por la primera acción con `jobId`; dos `jobId` distintos siguen siendo acciones distintas (invariante de 013b1b26 preservado).

- [ ] **Step 1: Diagnóstico reproducible antes de tocar código**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_local_worker_runtime.py::test_worker_product_loop_exception_creates_worker_retry_remediation','-q','-p','no:randomly']))"
```
Expected: FAIL con `KeyError: 'details'` en `:682` (`enriched_action["payload"]["details"]`). Criterio de decisión: el test afirma (`:681`) que la acción enriquecida es la MISMA genérica (`enriched_action["id"] == generic_action["id"]`), contrato previo a 013b1b26; el código actual crea una segunda acción porque `job_id('') != job_id(<job>)`. Diverge el código, no el test: el test documenta el contrato de "Run worker once" único por hilo en cola. Si el fallo es otro, detenerse y reportar el traceback (no corregir a ciegas).

- [ ] **Step 2: Test unitario que fija la regla**

En `tests_py/test_remediation_job_lifecycle.py` agregar al final:
```python
def test_job_failure_enriches_the_pending_generic_worker_action(lane):
    base = {
        "project_id": lane.project["id"],
        "thread_id": lane.thread["id"],
        "loop_id": None,
        "stage": "worker",
        "blocker_type": "worker_not_running",
        "title": "Run worker once",
        "description": "This thread is queued, but the local worker is not running.",
        "action_type": "run_worker_once",
    }
    generic = lane.service.repository.create_action(
        **base, payload={"projectId": lane.project["id"], "threadId": lane.thread["id"]}
    )
    failed_job = _job(lane, "failed")
    enriched = lane.service.repository.create_action(
        **base,
        payload={
            "projectId": lane.project["id"],
            "threadId": lane.thread["id"],
            "details": {"jobId": failed_job["id"]},
        },
    )
    other_job = _job(lane, "queued")
    separate = lane.service.repository.create_action(
        **base,
        payload={
            "projectId": lane.project["id"],
            "threadId": lane.thread["id"],
            "details": {"jobId": other_job["id"]},
        },
    )

    assert enriched["id"] == generic["id"]
    assert enriched["payload"]["details"]["jobId"] == failed_job["id"]
    assert separate["id"] != generic["id"]
```
Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_remediation_job_lifecycle.py','-q','-p','no:randomly']))"
```
Expected: FAIL en el test nuevo (`enriched["id"] != generic["id"]`).

- [ ] **Step 3: Implementación mínima**

En `remediations/repository.py`, dentro de `same_action_identity`, reemplazar:
```python
            if not clean_loop_id and job_id(existing_payload) != job_id(clean_payload):
                return False
```
por:
```python
            existing_job = job_id(existing_payload)
            if not clean_loop_id and existing_job and existing_job != job_id(clean_payload):
                return False
```

- [ ] **Step 4: Tests + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_remediation_job_lifecycle.py','tests_py/test_local_worker_runtime.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_remediation_display_payload.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/remediations/repository.py tests_py/test_remediation_job_lifecycle.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/remediations/repository.py tests_py/test_remediation_job_lifecycle.py
```
Expected: PASS de los 4 archivos, incluido `test_worker_product_loop_exception_creates_worker_retry_remediation` (memoria: `local_worker_runtime` puede ser flaky bajo carga; si falla otro test de ese archivo, re-ejecutarlo aislado antes de concluir).

- [ ] **Step 5: Commit**

```
git add local_control_center/remediations/repository.py tests_py/test_remediation_job_lifecycle.py
git commit -m "Fix (Remediaciones): el fallo de un job enriquece la acción genérica del worker" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Caché de evidencia inmutable compactada en el overview (§2.10)

**Files:**
- Modify: `local_control_center/evidence/repository.py` (constantes tras imports; métodos nuevos después de `list_evidence_packages`, `:503-520`)
- Modify: `local_control_center/control_plane/overview.py:13-15` (imports), después de `_compact_overview_record` (`:116-119`), `:187-195` (claves `evidencePackages` y `testResultRecords`)
- Create: `tests_py/test_overview_immutable_evidence_cache.py`

**Interfaces:**
- Consumes: `row_to_evidence_package` (`evidence/repository.py:34`), `row_to_test_result` (`:71`), `_compact_overview_record` (`overview.py:116`), `EvidenceRepository.update_evidence_links(evidence_id, *, qa_verdict=...)` (`:253`).
- Produces:
  - `IMMUTABLE_EVIDENCE_JSON_COLUMNS: dict[str, str]` = `{"test_results": "testResults", "logs": "logs", "diff_refs": "diffRefs", "screenshot_refs": "screenshotRefs"}`
  - `EvidenceRepository.list_evidence_package_heads(*, limit: int) -> list[dict]`
  - `EvidenceRepository.immutable_evidence_fields(package_ids: list[str]) -> dict[str, dict]`
  - `EvidenceRepository.list_test_result_ids(*, limit: int) -> list[str]`
  - `EvidenceRepository.load_test_results(result_ids: list[str]) -> dict[str, dict]`
  - `overview._IMMUTABLE_RECORDS: _ImmutableRecordCache`; salida de `evidencePackages`/`testResultRecords` idéntica a la compactación previa.

- [ ] **Step 1: Tests que fallan**

Crear `tests_py/test_overview_immutable_evidence_cache.py`:
```python
"""El overview reutiliza la compactación de filas inmutables entre polls sin cambiar su salida.

Medido sobre una copia de la BD viva: decodificar ~118 MB de JSON de evidencia por poll costaba
~2 s de los 2,8 s del overview. Esas columnas no se actualizan tras el INSERT.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from local_control_center.control_plane import overview as overview_module
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

HEAVY = "x" * (overview_module.OVERVIEW_EMBEDDED_VALUE_BYTE_LIMIT * 3)


def _seed(connection, tmp_path: Path) -> tuple[EvidenceRepository, dict]:
    project = ProjectsRepository(connection).create_project(
        name="Overview cache", path=tmp_path / "project", template_id="other"
    )
    evidence = EvidenceRepository(connection)
    package = evidence.create_evidence_package(
        project_id=project["id"],
        workflow_run_id=None,
        test_results=[{"command": "pytest", "status": "passed", "metadata": {"blob": HEAVY}}],
        logs=[HEAVY],
        diff_refs=[{"path": "a.py", "patch": HEAVY}],
    )
    return evidence, package


def test_overview_evidence_equals_the_full_compaction(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        evidence, _package = _seed(connection, tmp_path)

        snapshot = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)
        expected_packages = [
            overview_module._compact_overview_record(record)
            for record in evidence.list_evidence_packages(limit=overview_module.OVERVIEW_EVIDENCE_LIMIT)
        ]
        expected_results = [
            overview_module._compact_overview_record(record)
            for record in evidence.list_all_test_results(limit=overview_module.OVERVIEW_TEST_RESULT_LIMIT)
        ]

    assert snapshot["evidencePackages"] == expected_packages
    assert snapshot["testResultRecords"] == expected_results
    assert snapshot["evidencePackages"][0]["testResults"][0]["status"] == "passed"


def test_second_overview_does_not_reread_immutable_evidence(tmp_path: Path, monkeypatch) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        evidence, package = _seed(connection, tmp_path)
        first = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)

        def refuse(_self, ids):
            if ids:
                raise AssertionError(f"immutable evidence re-read: {ids}")
            return {}

        monkeypatch.setattr(EvidenceRepository, "immutable_evidence_fields", refuse)
        monkeypatch.setattr(EvidenceRepository, "load_test_results", refuse)
        second = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)
        evidence.update_evidence_links(package["id"], qa_verdict="passed")
        third = overview_module.build_overview_from_connection(connection=connection, cwd=tmp_path)

    assert second["evidencePackages"] == first["evidencePackages"]
    assert second["testResultRecords"] == first["testResultRecords"]
    assert third["evidencePackages"][0]["qaVerdict"] == "passed"
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_overview_immutable_evidence_cache.py','-q','-p','no:randomly']))"
```
Expected: el primer test PASA (línea base de equivalencia); el segundo FALLA con `AttributeError` al hacer `monkeypatch.setattr` sobre `immutable_evidence_fields` inexistente (`raising=True` por defecto).

- [ ] **Step 3: Implementación mínima**

En `evidence/repository.py`, después de los imports del módulo agregar:
```python
IMMUTABLE_EVIDENCE_JSON_COLUMNS = {
    "test_results": "testResults",
    "logs": "logs",
    "diff_refs": "diffRefs",
    "screenshot_refs": "screenshotRefs",
}
"""Columnas JSON de ``evidence_packages`` que se escriben al crear el paquete y nunca se actualizan."""

_EVIDENCE_HEAD_COLUMNS = (
    "id",
    "project_id",
    "workflow_run_id",
    "workflow_step_id",
    "agent_id",
    "agent_run_id",
    "job_id",
    "workspace_id",
    "runtime_id",
    "task_id",
    "test_plan",
    "acceptance_checklist",
    "risk_notes",
    "artifact_ids",
    "diff_summary",
    "runtime_health",
    "model_calls",
    "tool_calls",
    "policy_decisions",
    "approvals",
    "artifact_refs",
    "hashes",
    "evidence_source",
    "qa_verdict",
    "created_at",
)
```
Agregar en `EvidenceRepository`, después de `list_evidence_packages`:
```python
    def list_evidence_package_heads(self, *, limit: int) -> list[dict[str, Any]]:
        """Lista los N paquetes más recientes sin leer sus columnas JSON inmutables y pesadas.

        Esas columnas vuelven como listas vacías; quien las necesite las pide por id con
        ``immutable_evidence_fields``. El orden es el mismo de ``list_evidence_packages``.
        """
        placeholders = ", ".join(f"'[]' AS {column}" for column in IMMUTABLE_EVIDENCE_JSON_COLUMNS)
        rows = self.connection.execute(
            f"SELECT {', '.join(_EVIDENCE_HEAD_COLUMNS)}, {placeholders} "
            "FROM evidence_packages ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row_to_evidence_package(row) for row in rows]

    def immutable_evidence_fields(self, package_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Decodifica las columnas inmutables de los paquetes pedidos, indexadas por id."""
        if not package_ids:
            return {}
        placeholders = ", ".join("?" for _ in package_ids)
        columns = ", ".join(IMMUTABLE_EVIDENCE_JSON_COLUMNS)
        rows = self.connection.execute(
            f"SELECT id, {columns} FROM evidence_packages WHERE id IN ({placeholders})",
            tuple(package_ids),
        ).fetchall()
        return {
            row["id"]: {
                field: json_loads(row[column], []) for column, field in IMMUTABLE_EVIDENCE_JSON_COLUMNS.items()
            }
            for row in rows
        }

    def list_test_result_ids(self, *, limit: int) -> list[str]:
        """Ids de los N resultados de test más recientes; sus filas no se modifican tras insertarse."""
        rows = self.connection.execute(
            "SELECT id FROM test_results ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [row["id"] for row in rows]

    def load_test_results(self, result_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Decodifica los resultados de test pedidos, indexados por id."""
        if not result_ids:
            return {}
        placeholders = ", ".join("?" for _ in result_ids)
        rows = self.connection.execute(
            f"SELECT * FROM test_results WHERE id IN ({placeholders})", tuple(result_ids)
        ).fetchall()
        return {row["id"]: row_to_test_result(row) for row in rows}
```
En `control_plane/overview.py`, imports (`:13-15`), reemplazar:
```python
import sqlite3
from pathlib import Path
from typing import Any
```
por:
```python
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any
```
Después de `_compact_overview_record` agregar:
```python
OVERVIEW_IMMUTABLE_CACHE_ENTRIES = 1_024


class _ImmutableRecordCache:
    """LRU de fragmentos ya compactados de filas inmutables, por base, tipo e id.

    Paquetes de evidencia y resultados de test cargan blobs de varios MB que no cambian después
    de insertarse; decodificarlos y compactarlos en cada poll de 5 s dominaba el costo del
    overview. El fragmento guardado es exactamente el que produce ``_compact_overview_record``.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._entries: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str, str]) -> dict[str, Any] | None:
        """Devuelve el fragmento y lo marca como recién usado, o ``None`` si no está."""
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[str, str, str], value: dict[str, Any]) -> None:
        """Guarda el fragmento y descarta el menos usado al superar la capacidad."""
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)


_IMMUTABLE_RECORDS = _ImmutableRecordCache(OVERVIEW_IMMUTABLE_CACHE_ENTRIES)


def _database_identity(connection: sqlite3.Connection) -> str:
    """Archivo de la base principal: separa las cachés de distintas bases del mismo proceso."""
    row = connection.execute("PRAGMA database_list").fetchone()
    return str(row[2] or f"memory-{id(connection)}")


def _overview_evidence_packages(
    connection: sqlite3.Connection, evidence: EvidenceRepository
) -> list[dict[str, Any]]:
    """Paquetes recientes compactados; las columnas inmutables salen de la caché cuando existen."""
    database = _database_identity(connection)
    heads = evidence.list_evidence_package_heads(limit=OVERVIEW_EVIDENCE_LIMIT)
    missing = [head["id"] for head in heads if _IMMUTABLE_RECORDS.get((database, "evidence", head["id"])) is None]
    for package_id, fields in evidence.immutable_evidence_fields(missing).items():
        _IMMUTABLE_RECORDS.put((database, "evidence", package_id), _compact_overview_record(fields))
    packages: list[dict[str, Any]] = []
    for head in heads:
        immutable = _IMMUTABLE_RECORDS.get((database, "evidence", head["id"])) or {}
        compacted = _compact_overview_record(head)
        packages.append({key: immutable.get(key, value) for key, value in compacted.items()})
    return packages


def _overview_test_results(
    connection: sqlite3.Connection, evidence: EvidenceRepository
) -> list[dict[str, Any]]:
    """Resultados de test recientes compactados, leídos del disco sólo la primera vez."""
    database = _database_identity(connection)
    result_ids = evidence.list_test_result_ids(limit=OVERVIEW_TEST_RESULT_LIMIT)
    missing = [
        result_id
        for result_id in result_ids
        if _IMMUTABLE_RECORDS.get((database, "test_result", result_id)) is None
    ]
    for result_id, record in evidence.load_test_results(missing).items():
        _IMMUTABLE_RECORDS.put((database, "test_result", result_id), _compact_overview_record(record))
    records: list[dict[str, Any]] = []
    for result_id in result_ids:
        cached = _IMMUTABLE_RECORDS.get((database, "test_result", result_id))
        if cached is not None:
            records.append(cached)
    return records
```
En `build_overview_from_connection`, reemplazar:
```python
        "evidencePackages": [
            _compact_overview_record(record)
            for record in evidence.list_evidence_packages(limit=OVERVIEW_EVIDENCE_LIMIT)
        ],
```
por:
```python
        "evidencePackages": _overview_evidence_packages(connection, evidence),
```
y:
```python
        "testResultRecords": [
            _compact_overview_record(record)
            for record in evidence.list_all_test_results(limit=OVERVIEW_TEST_RESULT_LIMIT)
        ],
```
por:
```python
        "testResultRecords": _overview_test_results(connection, evidence),
```

- [ ] **Step 4: Tests + regresión del overview + medición + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_overview_immutable_evidence_cache.py','tests_py/test_control_plane_overview_caps.py','tests_py/test_overview_schema_drift.py','tests_py/test_operational_hardening_p0.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/evidence/repository.py local_control_center/control_plane/overview.py tests_py/test_overview_immutable_evidence_cache.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/evidence/repository.py local_control_center/control_plane/overview.py tests_py/test_overview_immutable_evidence_cache.py
```
Expected: PASS de los 4 archivos; ruff limpio.

Medición (evidencia de la meta < 1 s). Guardar como `<scratchpad>/profile_overview.py` y ejecutar con `PYTHONPATH` = raíz del repo, pasando como argumento una copia fresca de la BD (misma receta de copia de Task 5):
```python
import sys
import time
from pathlib import Path

sys.modules["faiss"] = None
from local_control_center.control_plane.overview import build_overview_from_connection
from local_control_center.shared.db import open_sqlite_connection

connection = open_sqlite_connection(Path(sys.argv[1]))
for label in ("cold", "warm-1", "warm-2"):
    started = time.perf_counter()
    build_overview_from_connection(connection=connection, cwd=Path.cwd())
    print(label, round(time.perf_counter() - started, 3))
```
Expected: `cold` ≈ 2,5–3 s (primer poll tras reinicio), `warm-1`/`warm-2` < 1,0 s. Si `warm` ≥ 1 s, detenerse, re-perfilar con `cProfile` y reportar el nuevo costo dominante antes de tocar nada más (spec §2.10: no recortar datos).

- [ ] **Step 5: Commit**

```
git add local_control_center/evidence/repository.py local_control_center/control_plane/overview.py tests_py/test_overview_immutable_evidence_cache.py
git commit -m "Perf (Overview): cachea la evidencia inmutable compactada entre polls" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Clasificador por puntaje relativo y preguntas con clave i18n (§2.1)

**Files:**
- Modify: `local_control_center/product_loop/intent_classifier.py:11-18` (imports), `:36-118` (`KEYWORDS`), después de `:127` (constantes), `:140-167` (`IntentClassification`), `:173-174` (`_normalize_text`), `:177-180` (`_contains_keyword`), `:339-347` (`_questions_for`), `:356-384` (`classify`), `:386-399` (`_validated`)
- Modify: `local_control_center/threads/coordinator.py:265`
- Modify: `local_control_center/i18n/default_catalog.json` (insertar tras `"app.threads.event.worker_claimed"`, `:1142-1145`)
- Test: `tests_py/test_intent_classifier.py`, `tests_py/test_threads_coordinator.py`

**Interfaces:**
- Consumes: `_intent_scores` (`:234`), `ThreadCoordinator._queue_research_run` / `_queue_product_loop_run`.
- Produces:
  - `CHANGE_PRODUCING_INTENTS`, `EXPLICIT_CHANGE_VERBS`, `READ_ONLY_MARKERS`, `QUESTION_COPY: dict[str, tuple[str, str]]`
  - `IntentClassification.scores: dict[str, int]`, `.research_only: bool`, `.question_keys: list[str]` (defaults vacíos; `to_dict()` agrega `"scores"`, `"researchOnly"`, `"questionKeys"`)
  - Claves: `app.threads.intake.question.runtime`, `.outcome`, `.migrationScope` (consumidas por Task 10 vía `metadata.questionKeys`).
  - Regla: research-only ⇔ marcador explícito de solo-lectura, o `scores.research > 0` y `scores.research ≥ max(scores[c] for c in CHANGE_PRODUCING_INTENTS)`; con verbo de cambio explícito el empate lo gana el cambio (`>`).

- [ ] **Step 1: Tests que fallan (corpus obligatorio del spec)**

En `tests_py/test_intent_classifier.py` agregar `import pytest` al bloque de imports y al final:
```python
ORIGINAL_VALIDATION_PROMPT = (
    "Investiga en modo solo lectura: ¿qué versión de Python exige pyproject.toml de este repo? "
    "Responde en una línea y no modifiques archivos."
)
CHANGE_PROMPTS = (
    "Analiza por qué el login falla al expirar la sesión y corrige el bug",
    "Compara la versión actual con la anterior y corrige el bug de sesión",
    "Corrige el bug de memoria sin modificar la API pública",
    "Fix the crash on startup; compare with the previous release",
)
RESEARCH_PROMPTS = (
    "Investiga qué tests cubren el login",
    "Research which feature flags exist",
    "Investiga por qué el build de refactor está lento, solo analiza, no modifiques nada",
    "Analiza el bug de memoria en modo solo lectura, no lo corrijas",
    "Evaluate migration options for Postgres 17 and cite sources",
    ORIGINAL_VALIDATION_PROMPT,
)


@pytest.mark.parametrize("prompt", CHANGE_PROMPTS)
def test_change_requests_are_never_research_only(prompt: str) -> None:
    assert classify(prompt).research_only is False


@pytest.mark.parametrize("prompt", RESEARCH_PROMPTS)
def test_read_only_questions_are_research_only(prompt: str) -> None:
    decision = classify(prompt)

    assert decision.research_only is True
    assert "research" in decision.intents
    assert decision.plan_mode not in {"ask", "blocked"}


def test_spanish_research_vocabulary_matches_with_and_without_accents() -> None:
    accented = classify("Evalúa y compara las opciones de caché")
    unaccented = classify("Evalua y compara las opciones de cache")

    assert accented.scores == unaccented.scores
    assert accented.scores["research"] == 2


def test_classification_exposes_scores_and_research_flag() -> None:
    decision = classify("Research which feature flags exist")

    assert decision.scores["research"] == 1
    assert decision.scores["feature"] == 1
    assert decision.to_dict()["researchOnly"] is True
    assert decision.to_dict()["scores"]["research"] == 1


def test_classifier_questions_carry_i18n_keys_aligned_with_the_english_text() -> None:
    ask = classify("help")
    blocked = classify("Add a dashboard", project_assessment=_runtime_unavailable_assessment())

    assert ask.question_keys == ["app.threads.intake.question.outcome"]
    assert ask.questions == ["What outcome should AIDO optimize for: diagnosis, implementation, or research?"]
    assert ask.to_dict()["questionKeys"] == ask.question_keys
    assert blocked.question_keys[0] == "app.threads.intake.question.runtime"
    assert len(blocked.question_keys) == len(blocked.questions)
```
En `tests_py/test_threads_coordinator.py` agregar a los imports:
```python
import pytest

from tests_py.test_intent_classifier import CHANGE_PROMPTS, RESEARCH_PROMPTS
```
(respetando el orden de ruff/isort) y al final:
```python
@pytest.mark.parametrize("prompt", RESEARCH_PROMPTS)
def test_intake_routes_read_only_questions_to_the_research_agent(tmp_path: Path, prompt: str) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        ThreadCoordinator(connection, root=tmp_path).post_message(
            thread_id=thread["id"], content=prompt, project_assessment=RUNTIME_AVAILABLE
        )
        jobs = JobsRepository(connection).list_jobs(thread["projectId"])

    assert [job["kind"] for job in jobs] == ["thread.research.run"]


@pytest.mark.parametrize("prompt", CHANGE_PROMPTS)
def test_intake_keeps_change_requests_in_the_product_loop(tmp_path: Path, prompt: str) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        ThreadCoordinator(connection, root=tmp_path).post_message(
            thread_id=thread["id"], content=prompt, project_assessment=RUNTIME_AVAILABLE
        )
        jobs = JobsRepository(connection).list_jobs(thread["projectId"])

    assert [job["kind"] for job in jobs] == ["thread.product_loop.run"]
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_intent_classifier.py','tests_py/test_threads_coordinator.py','-q','-p','no:randomly']))"
```
Expected: FAIL: `AttributeError: 'IntentClassification' object has no attribute 'research_only'` y, en el coordinador, los prompts de cambio con "compare"/"analiza" salen como `thread.research.run` y los españoles de research como `thread.product_loop.run` (o bloqueados en `ask`).

- [ ] **Step 3: Implementación mínima**

Imports (`:11-18`), agregar `import unicodedata` tras `import re`.

`KEYWORDS`:
- en `"bugfix"` agregar al final de la tupla: `"corrige", "corregir", "arregla", "arreglar",`
- en `"feature"` agregar: `"implementa", "implementar",`
- en `"migration"` eliminar la entrada `"migracion",` (con el plegado duplicaría a `"migración"`)
- reemplazar la línea `"research": ("research", "investigate", "compare", "evaluate", "benchmark", "source", "cite"),` por:
```python
    "research": (
        "research",
        "investigate",
        "compare",
        "evaluate",
        "benchmark",
        "source",
        "cite",
        "investiga",
        "investigar",
        "averigua",
        "averiguar",
        "analiza",
        "analizar",
        "compara",
        "comparar",
        "evalúa",
        "evaluar",
    ),
```
Después de `MIGRATION_PATH_MARKERS = (...)` agregar:
```python
CHANGE_PRODUCING_INTENTS = (
    "bugfix",
    "feature",
    "refactor",
    "migration",
    "tests",
    "security",
    "docs",
    "architecture",
    "cleanup",
)
EXPLICIT_CHANGE_VERBS = ("corrige", "corregir", "arregla", "arreglar", "implementa", "implementar", "fix", "implement")
READ_ONLY_MARKERS = (
    "solo lectura",
    "no modifiques",
    "sin modificar nada",
    "no cambies",
    "read-only",
    "read only",
    "don't change",
    "do not modify",
)
QUESTION_COPY: dict[str, tuple[str, str]] = {
    "runtime": (
        "app.threads.intake.question.runtime",
        "Configure an executable AIDO runtime before starting autonomous work.",
    ),
    "outcome": (
        "app.threads.intake.question.outcome",
        "What outcome should AIDO optimize for: diagnosis, implementation, or research?",
    ),
    "migrationScope": (
        "app.threads.intake.question.migrationScope",
        "Should schema/data migration be shipped in the same change as the refactor?",
    ),
}
"""Preguntas del intake: clave i18n y texto inglés persistido (el texto sigue siendo el fallback)."""
```
`IntentClassification`: agregar después de `user_mode: str`:
```python
    scores: dict[str, int] = field(default_factory=dict)
    research_only: bool = False
    question_keys: list[str] = field(default_factory=list)
```
y en `to_dict()`, antes de `"source": "deterministic_intent_classifier",` agregar:
```python
            "scores": dict(self.scores),
            "researchOnly": self.research_only,
            "questionKeys": list(self.question_keys),
```
Reemplazar `_normalize_text` y `_contains_keyword` (`:173-180`) por:
```python
def _fold_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", _fold_accents(value.strip().lower().replace("\u2019", "'")))


def _contains_keyword(text: str, keyword: str) -> bool:
    keyword = _fold_accents(keyword)
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _has_read_only_marker(prompt: str) -> bool:
    return any(_contains_keyword(prompt, marker) for marker in READ_ONLY_MARKERS)


def _is_research_only(scores: Mapping[str, int], prompt: str) -> bool:
    if _has_read_only_marker(prompt):
        return True
    research = scores.get("research", 0)
    if research <= 0:
        return False
    strongest_change = max(scores.get(intent, 0) for intent in CHANGE_PRODUCING_INTENTS)
    if any(_contains_keyword(prompt, verb) for verb in EXPLICIT_CHANGE_VERBS):
        return research > strongest_change
    return research >= strongest_change
```
Reemplazar `_questions_for` (`:339-347`) por:
```python
def _question_entries(
    plan_mode: str, intents: Sequence[str], runtime_available: bool | None
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if runtime_available is False:
        entries.append(QUESTION_COPY["runtime"])
    if plan_mode == "ask":
        entries.append(QUESTION_COPY["outcome"])
    if "migration" in intents and "refactor" in intents:
        entries.append(QUESTION_COPY["migrationScope"])
    return entries
```
En `classify`, reemplazar:
```python
        scores = _intent_scores(prompt, file_signals)
        intents = [intent for intent in INTENT_VALUES if scores[intent] > 0]
```
por:
```python
        scores = _intent_scores(prompt, file_signals)
        if _has_read_only_marker(prompt):
            scores["research"] += 1
        intents = [intent for intent in INTENT_VALUES if scores[intent] > 0]
```
reemplazar:
```python
            questions=_questions_for(plan_mode, intents, runtime_available),
            user_mode=user_mode,
        )
```
por:
```python
            questions=[text for _key, text in question_entries],
            user_mode=user_mode,
            scores=dict(scores),
            research_only=_is_research_only(scores, prompt),
            question_keys=[key for key, _text in question_entries],
        )
```
y justo antes de `decision = IntentClassification(` agregar:
```python
        question_entries = _question_entries(plan_mode, intents, runtime_available)
```
En `_validated`, reemplazar el `return IntentClassification(...)` por:
```python
        questions = list(dict.fromkeys(decision.questions))
        return IntentClassification(
            intents=intents or ["feature"],
            risk=risk,
            required_roles=list(dict.fromkeys(decision.required_roles)),
            required_gates=list(dict.fromkeys(decision.required_gates)),
            suggested_branch_name=decision.suggested_branch_name or "codex/task",
            plan_mode=plan_mode,
            confidence=max(0.0, min(1.0, float(decision.confidence))),
            questions=questions,
            user_mode=decision.user_mode,
            scores=dict(decision.scores),
            research_only=decision.research_only and "research" in intents,
            question_keys=list(decision.question_keys)
            if len(decision.question_keys) == len(questions)
            else [],
        )
```
En `threads/coordinator.py:265` reemplazar:
```python
            elif "research" in decision.intents:
```
por:
```python
            elif decision.research_only:
```
Catálogo: insertar inmediatamente después del bloque:
```json
    "app.threads.event.worker_claimed": {
      "en": "Worker assigned",
      "es": "Worker asignado"
    },
```
lo siguiente:
```json
    "app.threads.intake.question.runtime": {
      "en": "Configure an executable AIDO runtime before starting autonomous work.",
      "es": "Configura un entorno de ejecución de AIDO antes de iniciar trabajo autónomo."
    },
    "app.threads.intake.question.outcome": {
      "en": "What outcome should AIDO optimize for: diagnosis, implementation, or research?",
      "es": "¿Qué resultado debe priorizar AIDO: diagnóstico, implementación o investigación?"
    },
    "app.threads.intake.question.migrationScope": {
      "en": "Should schema/data migration be shipped in the same change as the refactor?",
      "es": "¿La migración de esquema o datos debe entregarse en el mismo cambio que el refactor?"
    },
```

- [ ] **Step 4: Tests + regresión + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_intent_classifier.py','tests_py/test_threads_coordinator.py','tests_py/test_i18n_platform.py','tests_py/test_git_workspace_api.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/intent_classifier.py local_control_center/threads/coordinator.py tests_py/test_intent_classifier.py tests_py/test_threads_coordinator.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/intent_classifier.py local_control_center/threads/coordinator.py tests_py/test_intent_classifier.py tests_py/test_threads_coordinator.py
```
Expected: PASS (los tests previos del clasificador —incluido `test_spanish_migration_and_architecture_request_requires_planning` y "aves migratorias"— siguen verdes; `test_git_workspace_api.py:216` cubre el otro consumidor, `git_workspace/service.py:1160`, que usa el nombre de rama sugerido). ruff limpio.

- [ ] **Step 5: Commit**

```
git add local_control_center/product_loop/intent_classifier.py local_control_center/threads/coordinator.py local_control_center/i18n/default_catalog.json tests_py/test_intent_classifier.py tests_py/test_threads_coordinator.py
git commit -m "Fix (Intake): research solo-lectura por puntaje relativo y preguntas con clave i18n" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Tras el merge a `dev`, la rama local `fix/intake-spanish-research` queda obsoleta: reportarla al operador para que decida borrarla (no borrarla por iniciativa propia).

---

### Task 9: Proveedor SearXNG propio y diagnóstico de proveedor bloqueado (§2.2 backend)

**Files:**
- Create: `local_control_center/research/web_search.py`
- Modify: `local_control_center/agents/research_agent.py:13-61` (imports/alias), `:125-128` (`_publisher_from_url`), `:131-176` (DDG), `:182-202` (`_fetch_url_text`), `:231`, `:316-319`, `:521-535` (`_remediation_for_blocker`), `:835`, `:999-1018`
- Modify: `local_control_center/settings/registry.py` (tras el descriptor `research.trustedDomains`, `:307-314`; `validate_value` `:468-469`)
- Modify: `local_control_center/remediations/payloads.py:728-747` (`_research_recovery_payload`), `:1334-1371` (`_research_required_specs`)
- Modify: `local_control_center/i18n/default_catalog.json` (tras `"app.settings.research.trustedDomains"`, `:11895-11898`)
- Create: `tests_py/test_research_web_search.py`
- Modify: `tests_py/test_research_agent.py`

**Interfaces:**
- Consumes: `assert_external_boundary` (`process_supervision/context.py:82`), `resolve_setting_value` (`settings/resolver.py:111`), `BlockerRemediationService.create_for_blocked_run` (`remediations/service.py:62`).
- Produces (`research/web_search.py`):
  - `WebSearchProvider = Callable[[str, int], list[dict[str, Any]]]`
  - `SEARXNG_PROVIDER = "searxng"`, `DUCKDUCKGO_PROVIDER = "duckduckgo"`, `DEFAULT_SEARXNG_BASE_URL = "http://127.0.0.1:8888"`, `PROVIDER_BLOCKED_CODE = "research_provider_blocked"`
  - `class ResearchWebSearchError(ValueError)`: la búsqueda no pudo completarse (red, timeout, límite de bytes, consulta vacía); mensaje `"ResearchAgent web search failed: <detalle>"` para red. `ResearchAgentRunner.run()` la captura y responde `research_blocked` (un `docker stop` del SearXNG no puede reventar el job).
  - `class ResearchProviderBlockedError(ResearchWebSearchError)` con `.provider`, `.detail`, `.code`; mensaje `"The web search provider blocked the query (<provider>: <detail>)."`
  - `class ResearchSourceUrlError(ValueError)`: destino de fuente (o redirect) no público.
  - `assert_public_source_url(url: str) -> None` (http/https, sin credenciales, y toda dirección literal o resuelta A/AAAA debe ser `is_global`: bloquea loopback, RFC 1918, link-local/metadata `169.254.169.254`, CGNAT, ULA, IPv4 mapeada en IPv6).
  - `class PublicRedirectHandler(HTTPRedirectHandler)`: revalida cada redirect con `assert_public_source_url`.
  - `open_public_source(url: str, *, headers: dict[str, str], timeout: float, urlopen_override: Callable[..., Any] | None = None)`: valida el destino inicial y abre con `PublicRedirectHandler`; `urlopen_override` conserva la costura de tests (patrón de `agents/providers/http_transport.py:36-46`).
  - `validate_search_base_url(value: str) -> str` (http/https, sin credenciales/query/fragment, sólo loopback; devuelve la URL sin `/` final)
  - `publisher_from_url(url: str) -> str`
  - `searxng_web_search_provider(base_url: str) -> WebSearchProvider`
  - `configured_web_search_provider(connection, *, project_id: str | None, duckduckgo: WebSearchProvider) -> WebSearchProvider`
- Settings: `research.webSearch.provider` (enum `searxng|duckduckgo`, default `searxng`), `research.webSearch.baseUrl` (string, default `http://127.0.0.1:8888`, validado con `validate_search_base_url`).
- ResearchAgent: el dict de `run()` agrega `"remediation"`; un bloqueo produce `status="research_blocked"` con `remediation.action == "research_provider_blocked"`.
- Remediaciones: todas las acciones de research llevan `payload.researchRemediation` (acción de la remediación del agente); con `research_provider_blocked` la primaria es `open_settings_section` con `section: "research"` (consumido por Task 11).

- [ ] **Step 1: Tests que fallan**

Crear `tests_py/test_research_web_search.py`:
```python
"""Proveedor SearXNG del ResearchAgent contra un servidor HTTP local de prueba, y su diagnóstico.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request

import pytest

import local_control_center.research.web_search as web_search_module
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.research.web_search import (
    PROVIDER_BLOCKED_CODE,
    PublicRedirectHandler,
    ResearchProviderBlockedError,
    ResearchSourceUrlError,
    ResearchWebSearchError,
    assert_public_source_url,
    configured_web_search_provider,
    searxng_web_search_provider,
    validate_search_base_url,
)
from local_control_center.settings.registry import descriptor_for, validate_value
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository


@contextmanager
def _search_server(status: int, body: bytes, content_type: str = "application/json") -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_searxng_provider_returns_deduplicated_http_sources() -> None:
    body = json.dumps(
        {
            "results": [
                {"url": "https://docs.python.org/3/", "title": "Python docs", "content": "Official."},
                {"url": "javascript:alert(1)", "title": "Not a source"},
                {"url": "https://docs.python.org/3/", "title": "Duplicate"},
            ]
        }
    ).encode("utf-8")
    with _search_server(200, body) as (base_url, requests):
        sources = searxng_web_search_provider(base_url)("python asyncio", 5)

    assert sources == [
        {
            "url": "https://docs.python.org/3/",
            "title": "Python docs",
            "publisher": "docs.python.org",
            "sourceType": "web_search",
        }
    ]
    assert requests and requests[0].startswith("/search?")
    assert "format=json" in requests[0]
    assert "q=python+asyncio" in requests[0]


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [
        (403, b"Forbidden", "text/plain"),
        (429, b"Too many requests", "text/plain"),
        (200, b"<html>json format disabled</html>", "text/html"),
    ],
)
def test_searxng_refusal_is_reported_as_provider_blocked(status: int, body: bytes, content_type: str) -> None:
    with (
        _search_server(status, body, content_type) as (base_url, _requests),
        pytest.raises(ResearchProviderBlockedError) as caught,
    ):
        searxng_web_search_provider(base_url)("python asyncio", 5)

    assert caught.value.code == PROVIDER_BLOCKED_CODE
    assert "blocked the query" in str(caught.value)
    assert "no sources" not in str(caught.value)


def _closed_loopback_url() -> str:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_searxng_down_is_a_typed_network_failure_not_a_block() -> None:
    """Contenedor detenido: conexión rechazada ⇒ error tipado de red, nunca ``ValueError`` suelto."""
    with pytest.raises(ResearchWebSearchError) as caught:
        searxng_web_search_provider(_closed_loopback_url())("python asyncio", 5)

    assert not isinstance(caught.value, ResearchProviderBlockedError)
    assert str(caught.value).startswith("ResearchAgent web search failed:")


def _resolve_to(address: str):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    return fake_getaddrinfo


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://127.0.0.1:4310/api/v1/overview",
        "http://localhost:4310/",
        "http://[::1]:4310/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fd00::1]/",
        "http://user:secret@docs.python.org/",
        "file:///etc/passwd",
    ],
)
def test_source_urls_to_non_public_destinations_are_rejected(url: str) -> None:
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url(url)


def test_public_name_resolving_to_a_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("127.0.0.1"))
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url("https://rebind.example/doc")

    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("169.254.169.254"))
    with pytest.raises(ResearchSourceUrlError):
        assert_public_source_url("https://metadata.example/latest")


def test_public_name_resolving_to_a_global_address_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("151.101.0.223"))

    assert assert_public_source_url("https://docs.python.org/3/") is None


def test_redirects_are_revalidated_before_being_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search_module.socket, "getaddrinfo", _resolve_to("151.101.0.223"))
    handler = PublicRedirectHandler()
    original = Request("https://docs.python.org/3/")

    with pytest.raises(ResearchSourceUrlError):
        handler.redirect_request(original, None, 302, "Found", {}, "http://127.0.0.1:4310/api/v1/overview")
    with pytest.raises(ResearchSourceUrlError):
        handler.redirect_request(original, None, 301, "Moved", {}, "http://169.254.169.254/latest/meta-data/")
    followed = handler.redirect_request(original, None, 302, "Found", {}, "https://docs.python.org/3.13/")
    assert followed is not None and followed.full_url == "https://docs.python.org/3.13/"


def test_search_base_url_accepts_only_loopback_without_credentials() -> None:
    assert validate_search_base_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    assert validate_search_base_url("http://localhost:8888") == "http://localhost:8888"
    for rejected in (
        "http://example.com:8888",
        "http://user:secret@127.0.0.1:8888",
        "http://127.0.0.1:8888/?token=x",
        "file:///etc/passwd",
        "",
    ):
        with pytest.raises(ValueError):
            validate_search_base_url(rejected)


def test_search_settings_are_registered_and_validated() -> None:
    provider = descriptor_for("research.webSearch.provider")
    base_url = descriptor_for("research.webSearch.baseUrl")

    assert provider is not None and provider.default == "searxng"
    assert provider.enum == ("searxng", "duckduckgo")
    assert base_url is not None and base_url.default == "http://127.0.0.1:8888"
    assert validate_value(base_url, "http://localhost:8888/") == "http://localhost:8888"
    with pytest.raises(ValueError):
        validate_value(base_url, "http://10.0.0.5:8888")


def test_configured_provider_follows_the_setting(tmp_path: Path) -> None:
    def duckduckgo(_query: str, _max_sources: int) -> list[dict]:
        return []

    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        default_provider = configured_web_search_provider(connection, project_id=None, duckduckgo=duckduckgo)
        SettingsRepository(connection).set_value("research.webSearch.provider", "general", None, "duckduckgo")
        chosen = configured_web_search_provider(connection, project_id=None, duckduckgo=duckduckgo)

    assert default_provider is not duckduckgo
    assert chosen is duckduckgo


def test_blocked_search_provider_offers_research_settings_first(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Research blocked", path=tmp_path / "project", template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Research question"
        )
        actions = BlockerRemediationService(connection, root=tmp_path).create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=None,
            stage="research",
            reason="The web search provider blocked the query (searxng: HTTP 403).",
            details={
                "status": "research_blocked",
                "jobId": "job-research",
                "remediation": {"action": PROVIDER_BLOCKED_CODE},
            },
        )

    assert (actions[0]["blockerType"], actions[0]["actionType"]) == ("research_required", "open_settings_section")
    assert actions[0]["payload"]["section"] == "research"
    assert actions[0]["payload"]["researchRemediation"] == PROVIDER_BLOCKED_CODE
    assert "check_network_access" not in {action["actionType"] for action in actions}
```
En `tests_py/test_research_agent.py`:
- agregar `import socket` al bloque stdlib (`:3-7`; `closing` y `Any` ya están importados en `:4` y `:6`) y estos imports:
```python
from local_control_center.research.web_search import ResearchProviderBlockedError, searxng_web_search_provider
from local_control_center.settings.repository import SettingsRepository
```
- en `test_research_agent_default_web_search_provider_fetches_and_prioritizes_official_sources` (`:341`), inmediatamente después de `store, _client, _headers = create_client(tmp_path, monkeypatch)` agregar:
```python
    SettingsRepository(store.connection).set_value("research.webSearch.provider", "general", None, "duckduckgo")
```
- en `test_research_agent_web_search_blocks_with_reason_when_internet_is_unavailable` (`:399`), inmediatamente después de `store, client, headers = create_client(tmp_path, monkeypatch)` agregar la misma línea (el test ejercita DDG offline; sin fijarlo tocaría el SearXNG local real).
- agregar al final:
```python
def test_duckduckgo_non_200_is_reported_as_provider_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Challenge(_FetchedResponse):
        status = 202

    monkeypatch.setattr(
        research_agent_module, "urlopen", lambda *_args, **_kwargs: _Challenge("<html>anomaly</html>")
    )

    with pytest.raises(ResearchProviderBlockedError) as caught:
        research_agent_module._duckduckgo_web_search_provider("python asyncio docs", 5)

    assert caught.value.code == "research_provider_blocked"


def test_research_run_reports_a_blocked_provider_instead_of_missing_sources(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-provider-blocked")

    def blocked_provider(_query: str, _max_sources: int) -> list[dict[str, Any]]:
        raise ResearchProviderBlockedError("searxng", "HTTP 403")

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=blocked_provider
    ).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert result["reason"] == "The web search provider blocked the query (searxng: HTTP 403)."
    assert result["remediation"]["action"] == "research_provider_blocked"


def test_research_run_with_searxng_down_blocks_with_network_remediation(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Réplica de ``docker stop aido-searxng``: el run termina ``research_blocked``, no con excepción."""
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-searxng-down")
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        closed_url = f"http://127.0.0.1:{probe.getsockname()[1]}"

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=searxng_web_search_provider(closed_url)
    ).run(
        research_request(
            project,
            workspace,
            query="Python asyncio TaskGroup official docs",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert result["reason"].startswith("ResearchAgent web search failed:")
    assert result["remediation"]["action"] == "check_network_access"


def test_research_run_never_fetches_a_non_public_source(
    create_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_urlopen(*_args: object, **_kwargs: object) -> _FetchedResponse:
        raise AssertionError("a non-public source must never be fetched")

    monkeypatch.setattr(research_agent_module, "urlopen", forbidden_urlopen)
    store, _client, _headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="research-ssrf")

    def metadata_provider(_query: str, _max_sources: int) -> list[dict[str, Any]]:
        return [
            {
                "url": "http://169.254.169.254/latest/meta-data/",
                "title": "Instance metadata",
                "publisher": "169.254.169.254",
                "sourceType": "web_search",
            }
        ]

    result = research_agent_module.ResearchAgentRunner(
        store.connection, root=tmp_path, web_search_provider=metadata_provider
    ).run(
        research_request(
            project,
            workspace,
            query="cloud instance metadata",
            maxSources=1,
            sources=[],
            metadata={"allowWebSearch": True},
        )
    )

    assert result["status"] == "research_blocked"
    assert "must target a public host" in result["reason"]
```

- [ ] **Step 2: Ver el fallo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_research_web_search.py','tests_py/test_research_agent.py','-q','-p','no:randomly']))"
```
Expected: error de colección `ModuleNotFoundError: No module named 'local_control_center.research.web_search'`.

- [ ] **Step 3: Implementación mínima**

Crear `local_control_center/research/web_search.py`:
```python
"""Proveedores de búsqueda web del ResearchAgent: SearXNG propio y diagnóstico de bloqueo.

El ResearchAgent descubre fuentes con el proveedor configurado en ``research.webSearch.provider``.
``searxng`` consulta una instancia propia por su API JSON (``GET {baseUrl}/search?q=...&format=json``,
que exige ``search.formats: [html, json]`` en la configuración del contenedor); ``duckduckgo``
conserva el scraping HTML previo. Una respuesta distinta de 200, un cuerpo que no es la API JSON o
una página de desafío se reportan como ``research_provider_blocked`` (el proveedor bloqueó la
consulta), nunca como "sin fuentes"; una caída de red o timeout es ``ResearchWebSearchError``, tipada
para que el runner responda ``research_blocked`` en vez de reventar el job. Nunca se evade la detección
de bots de un tercero cambiando el User-Agent. La URL base sólo puede apuntar a loopback; las fuentes
que el agente descarga, en cambio, sólo pueden apuntar a hosts públicos (``assert_public_source_url``),
revalidando cada redirect.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Callable
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from local_control_center.process_supervision.context import assert_external_boundary
from local_control_center.shared.redaction import redact_secrets

WebSearchProvider = Callable[[str, int], list[dict[str, Any]]]
SEARXNG_PROVIDER = "searxng"
DUCKDUCKGO_PROVIDER = "duckduckgo"
DEFAULT_SEARXNG_BASE_URL = "http://127.0.0.1:8888"
PROVIDER_BLOCKED_CODE = "research_provider_blocked"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SEARCH_USER_AGENT = "AIDO-ResearchAgent/1.0"
SEARCH_TIMEOUT_SECONDS = 10
MAX_SEARCH_RESPONSE_BYTES = 512_000
SEARCH_CANDIDATE_MULTIPLIER = 4
MIN_SEARCH_CANDIDATES = 10
MAX_SEARCH_CANDIDATES = 50


class ResearchWebSearchError(ValueError):
    """La búsqueda web no pudo completarse: red, timeout, límite de bytes o consulta vacía."""


class ResearchProviderBlockedError(ResearchWebSearchError):
    """El proveedor de búsqueda rechazó la consulta: HTTP distinto de 200 o página de desafío."""

    code = PROVIDER_BLOCKED_CODE

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"The web search provider blocked the query ({provider}: {detail}).")
        self.provider = provider
        self.detail = detail


class ResearchSourceUrlError(ValueError):
    """La URL de una fuente, o el destino de un redirect, no es un host público y no se descarga."""


def _unmapped(address: IPv4Address | IPv6Address) -> IPv4Address | IPv6Address:
    """IPv4 real de una IPv6 mapeada (``::ffff:a.b.c.d``); la misma dirección en otro caso."""
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _literal_address(host: str) -> IPv4Address | IPv6Address | None:
    """Dirección del host cuando es una IP literal; ``None`` si es un nombre."""
    try:
        return _unmapped(ip_address(host))
    except ValueError:
        return None


def _host_addresses(host: str, port: int) -> set[IPv4Address | IPv6Address]:
    """Direcciones a las que conectaría la descarga; vacío si el nombre no resuelve."""
    literal = _literal_address(host)
    if literal is not None:
        return {literal}
    if host == "localhost" or host.endswith(".localhost"):
        return {ip_address("127.0.0.1")}
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return set()
    return {_unmapped(ip_address(str(info[4][0]).split("%", 1)[0])) for info in infos}


def assert_public_source_url(url: str) -> None:
    """Rechaza fuentes que no sean http(s) hacia hosts públicos.

    Bloquea credenciales embebidas y todo destino cuya dirección literal o resuelta (A/AAAA) no sea
    global: loopback, RFC 1918, link-local (``169.254.169.254``, metadata de nube), CGNAT, ULA y
    reservadas, incluida la IPv4 mapeada en IPv6. Un nombre que no resuelve se deja pasar porque la
    descarga fallará igual con el mismo resolver; el intervalo entre esta resolución y la conexión
    (DNS rebinding) queda como riesgo residual documentado.
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ResearchSourceUrlError("ResearchAgent source URL must be absolute HTTP(S).")
    if parsed.username or parsed.password:
        raise ResearchSourceUrlError("ResearchAgent source URL must not contain credentials.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ResearchSourceUrlError("ResearchAgent source URL has an invalid port.") from error
    host = parsed.hostname.lower()
    if any(not address.is_global for address in _host_addresses(host, port)):
        raise ResearchSourceUrlError(f"ResearchAgent source URL must target a public host: {host}.")


class PublicRedirectHandler(HTTPRedirectHandler):
    """Revalida cada redirect con ``assert_public_source_url`` antes de seguirlo."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        """Sigue el redirect sólo si el destino es público; si no, aborta la descarga."""
        assert_public_source_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_PUBLIC_SOURCE_OPENER = build_opener(PublicRedirectHandler())
_STANDARD_URLOPEN = urlopen


def open_public_source(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    urlopen_override: Callable[..., Any] | None = None,
) -> Any:
    """Abre una fuente pública: valida el destino inicial y cada redirect antes de conectar.

    ``urlopen_override`` conserva la costura de los tests que parchean ``urlopen`` del módulo que
    llama (mismo patrón que ``agents/providers/http_transport.py``); la validación corre igual.
    """
    assert_public_source_url(url)
    request = Request(url, headers=headers)
    if urlopen_override is not None and urlopen_override is not _STANDARD_URLOPEN:
        return urlopen_override(request, timeout=timeout)
    return _PUBLIC_SOURCE_OPENER.open(request, timeout=timeout)


def validate_search_base_url(value: str) -> str:
    """Valida la URL base del proveedor: http(s), sin credenciales ni query, y sólo loopback."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("research.webSearch.baseUrl must be an absolute http(s) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("research.webSearch.baseUrl must not carry credentials, query or fragment.")
    if parsed.hostname.lower() not in LOOPBACK_HOSTS:
        raise ValueError("research.webSearch.baseUrl must point to a loopback host.")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def publisher_from_url(url: str) -> str:
    """Host del resultado sin ``www.``, usado como publisher de la fuente."""
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _candidate_limit(max_sources: int) -> int:
    return min(MAX_SEARCH_CANDIDATES, max(max_sources * SEARCH_CANDIDATE_MULTIPLIER, MIN_SEARCH_CANDIDATES))


def _searxng_sources(results: list[Any], *, limit: int) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "url": url,
                "title": str(result.get("title") or "").strip() or publisher_from_url(url),
                "publisher": publisher_from_url(url),
                "sourceType": "web_search",
            }
        )
        if len(sources) >= limit:
            break
    return sources


def searxng_web_search_provider(base_url: str) -> WebSearchProvider:
    """Construye el proveedor SearXNG para una instancia propia en loopback."""
    root = validate_search_base_url(base_url)

    def search(query: str, max_sources: int) -> list[dict[str, Any]]:
        safe_query = str(redact_secrets(query)).strip()
        if not safe_query or safe_query == "[redacted]":
            raise ResearchWebSearchError("ResearchAgent web search query is empty after secret redaction.")
        request = Request(
            f"{root}/search?{urlencode({'q': safe_query, 'format': 'json'})}",
            headers={"User-Agent": SEARCH_USER_AGENT, "Accept": "application/json"},
        )
        try:
            assert_external_boundary()
            with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                content = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise ResearchProviderBlockedError(SEARXNG_PROVIDER, f"HTTP {error.code}") from error
        except (URLError, TimeoutError) as error:
            raise ResearchWebSearchError(f"ResearchAgent web search failed: {error}") from error
        if status != 200:
            raise ResearchProviderBlockedError(SEARXNG_PROVIDER, f"HTTP {status}")
        if len(content) > MAX_SEARCH_RESPONSE_BYTES:
            raise ResearchWebSearchError("ResearchAgent web search response exceeds the fetch limit.")
        try:
            document = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as error:
            raise ResearchProviderBlockedError(
                SEARXNG_PROVIDER, "response is not the JSON API; enable search.formats json"
            ) from error
        results = document.get("results") if isinstance(document, dict) else None
        if not isinstance(results, list):
            raise ResearchProviderBlockedError(SEARXNG_PROVIDER, "response without results")
        return _searxng_sources(results, limit=_candidate_limit(max_sources))

    return search


def configured_web_search_provider(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    duckduckgo: WebSearchProvider,
) -> WebSearchProvider:
    """Resuelve el proveedor configurado con precedencia proyecto > general > default."""
    from local_control_center.settings.resolver import resolve_setting_value

    provider = resolve_setting_value(connection=connection, key="research.webSearch.provider", project_id=project_id)
    if provider == DUCKDUCKGO_PROVIDER:
        return duckduckgo
    base_url = resolve_setting_value(connection=connection, key="research.webSearch.baseUrl", project_id=project_id)
    return searxng_web_search_provider(str(base_url or DEFAULT_SEARXNG_BASE_URL))
```
`agents/research_agent.py`:
1. Agregar a los imports de `local_control_center`:
```python
from local_control_center.research.web_search import (
    DUCKDUCKGO_PROVIDER,
    ResearchProviderBlockedError,
    ResearchSourceUrlError,
    ResearchWebSearchError,
    WebSearchProvider,
    configured_web_search_provider,
    open_public_source,
    publisher_from_url,
)
```
2. Eliminar la línea `WebSearchProvider = Callable[[str, int], list[dict[str, Any]]]` (`:61`) y, si ruff reporta `F401` sobre `Callable`, eliminar `from collections.abc import Callable`.
3. Eliminar la función `_publisher_from_url` (`:125-128`) y reemplazar sus dos usos en el proveedor DDG (`:168-169`) por `publisher_from_url(url)`.
4. En `_duckduckgo_web_search_provider`, reemplazar desde `try:` hasta `parser.feed(content.decode(content_type, errors="replace"))` (inclusive) por:
```python
    try:
        assert_external_boundary()
        with urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
            content = response.read(MAX_WEB_SEARCH_BYTES + 1)
            if len(content) > MAX_WEB_SEARCH_BYTES:
                raise ResearchAgentValidationError(
                    "ResearchAgent web search response exceeds the fetch limit."
                )
            content_type = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        raise ResearchProviderBlockedError(DUCKDUCKGO_PROVIDER, f"HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise ResearchAgentValidationError(f"ResearchAgent web search failed: {error}") from error
    if status != 200:
        raise ResearchProviderBlockedError(DUCKDUCKGO_PROVIDER, f"HTTP {status}")

    text = content.decode(content_type, errors="replace")
    parser = _DuckDuckGoResultParser()
    parser.feed(text)
    if not parser.results and "anomaly" in text.lower():
        raise ResearchProviderBlockedError(DUCKDUCKGO_PROVIDER, "anti-bot challenge page")
```
5. `:231`: reemplazar `self.web_search_provider = web_search_provider or _duckduckgo_web_search_provider` por `self.web_search_provider = web_search_provider`.
6. `:316-319`: reemplazar:
```python
        if self.web_search_provider is None:
            raise ResearchAgentValidationError("ResearchAgent web search provider is not configured.")

        discovered = self.web_search_provider(query, self._max_sources(payload))
```
por:
```python
        provider = self.web_search_provider or configured_web_search_provider(
            self.connection,
            project_id=str(payload.get("projectId") or "").strip() or None,
            duckduckgo=_duckduckgo_web_search_provider,
        )
        discovered = provider(query, self._max_sources(payload))
```
7. `_remediation_for_blocker`: inmediatamente después de `reason_text = reason.lower()` agregar:
```python
        if "search provider blocked the query" in reason_text:
            return {
                "action": "research_provider_blocked",
                "summary": "The web search provider refused the query; this is not a lack of sources.",
                "steps": [
                    "Start the local SearXNG instance with the JSON format enabled.",
                    "Or choose another web search provider in Settings > Research.",
                    "Retry ResearchAgent with the same query.",
                ],
            }
```
8. `:835`: reemplazar `except (ResearchAgentValidationError, ResearchPolicyError) as error:` por `except (ResearchAgentValidationError, ResearchPolicyError, ResearchWebSearchError) as error:` (`ResearchWebSearchError` cubre `ResearchProviderBlockedError` y la caída de red de SearXNG; sin esto un `docker stop` propaga la excepción fuera de `run()` y el job falla sin remediación).
8b. Reemplazar `_fetch_url_text` completo (`:182-202`) por (las fuentes descubiertas se descargan solas, así que el guard pasa de "sólo loopback literal" a "sólo hosts públicos, revalidando redirects"):
```python
def _fetch_url_text(url: str) -> str:
    """Obtiene texto de una fuente HTTP(S) pública y acotada; revalida el destino y cada redirect."""
    try:
        assert_external_boundary()
        with open_public_source(
            url,
            headers={"User-Agent": "AIDO-ResearchAgent/1.0"},
            timeout=FETCH_TIMEOUT_SECONDS,
            urlopen_override=urlopen,
        ) as response:
            content = response.read(MAX_SOURCE_BYTES + 1)
            if len(content) > MAX_SOURCE_BYTES:
                raise ResearchAgentValidationError("ResearchAgent source content exceeds the fetch limit.")
            content_type = response.headers.get_content_charset() or "utf-8"
    except ResearchSourceUrlError as error:
        raise ResearchAgentValidationError(str(error)) from error
    except (HTTPError, URLError, TimeoutError) as error:
        raise ResearchAgentValidationError(f"ResearchAgent source fetch failed: {error}") from error
    return content.decode(content_type, errors="replace")
```
(`urlopen_override=urlopen` resuelve el nombre del módulo en cada llamada, así que los tests que parchean `research_agent_module.urlopen` siguen funcionando; `Request` y `urlopen` siguen importados porque los usa el proveedor DDG.)
9. En el `return {` final de `run()` (`:999-1018`), agregar después de `"reportArtifact": report_artifact,`:
```python
            "remediation": remediation,
```
(`ResearchAgentRunResponse`, `agents/contracts.py:805-823`, no declara `extra="forbid"`; la clave extra no cambia el contrato ni exige regen. `jobs_approvals/worker.py:942` ya la leía.)

`settings/registry.py`, después del descriptor `research.trustedDomains` agregar:
```python
    SettingDescriptor(
        key="research.webSearch.provider",
        section="research",
        project_section="internet",
        type="enum",
        default="searxng",
        enum=("searxng", "duckduckgo"),
        label_key="app.settings.research.webSearchProvider",
    ),
    SettingDescriptor(
        key="research.webSearch.baseUrl",
        section="research",
        project_section="internet",
        type="string",
        default="http://127.0.0.1:8888",
        label_key="app.settings.research.webSearchBaseUrl",
    ),
```
y en `validate_value`, inmediatamente antes de `if descriptor.type == "enum":` agregar:
```python
    if descriptor.key == "research.webSearch.baseUrl":
        from local_control_center.research.web_search import validate_search_base_url

        return validate_search_base_url(value if isinstance(value, str) else "")
```
`remediations/payloads.py`:
- agregar a los imports `from local_control_center.research.web_search import PROVIDER_BLOCKED_CODE`
- en `_research_recovery_payload`, antes de `policy = details.get("researchPolicy") ...` agregar:
```python
    remediation = details.get("remediation") if isinstance(details, dict) else None
    remediation_action = remediation.get("action") if isinstance(remediation, dict) else None
    if isinstance(remediation_action, str) and remediation_action:
        payload["researchRemediation"] = remediation_action
```
- en `_research_required_specs`, inmediatamente después de `research_recovery_payload = context.research_recovery_payload` agregar:
```python
    if research_recovery_payload.get("researchRemediation") == PROVIDER_BLOCKED_CODE:
        return [
            {
                "actionType": "open_settings_section",
                "title": "Open research settings",
                "description": "Choose a web search provider that accepts the query, such as your own SearXNG.",
                "payload": {"section": "research", **research_recovery_payload},
                "primary": True,
            },
            {
                "actionType": "run_worker_once",
                "title": "Run research worker once",
                "description": "Retry the ResearchAgent job after the search provider accepts queries.",
                "payload": research_recovery_payload,
            },
        ]
```
Catálogo: insertar inmediatamente después del bloque:
```json
    "app.settings.research.trustedDomains": {
      "en": "Trusted domains",
      "es": "Dominios de confianza"
    },
```
lo siguiente:
```json
    "app.settings.research.webSearchProvider": {
      "en": "Web search provider",
      "es": "Proveedor de búsqueda web"
    },
    "app.settings.research.webSearchBaseUrl": {
      "en": "Search provider URL (loopback only)",
      "es": "URL del proveedor de búsqueda (solo loopback)"
    },
```

- [ ] **Step 4: Tests + regresión + estilo**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_research_web_search.py','tests_py/test_research_agent.py','tests_py/test_research_resolution.py','tests_py/test_settings_slice.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_i18n_platform.py','tests_py/test_source_documentation_headers.py','-q','-p','no:randomly']))"
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/research/web_search.py local_control_center/agents/research_agent.py local_control_center/settings/registry.py local_control_center/remediations/payloads.py tests_py/test_research_web_search.py tests_py/test_research_agent.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/research/web_search.py local_control_center/agents/research_agent.py local_control_center/settings/registry.py local_control_center/remediations/payloads.py tests_py/test_research_web_search.py tests_py/test_research_agent.py
```
Expected: PASS (el test offline sigue devolviendo `"ResearchAgent web search failed: <urlopen error network unreachable>"` y "Check network access" porque fija `duckduckgo`); ruff limpio.

- [ ] **Step 5: Commit**

```
git add local_control_center/research/web_search.py local_control_center/agents/research_agent.py local_control_center/settings/registry.py local_control_center/remediations/payloads.py local_control_center/i18n/default_catalog.json tests_py/test_research_web_search.py tests_py/test_research_agent.py
git commit -m "Feature (Research): proveedor SearXNG propio y diagnóstico de proveedor bloqueado" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Decisiones con etiquetas legibles y preguntas del intake traducidas (§2.5 + UI de §2.1)

**Files:**
- Create: `local-control-center/web/src/features/shell/decisionOptionCopy.ts`
- Modify: `local-control-center/web/src/features/shell/ThreadConversation.tsx:86-98` (imports), `:561-573`
- Modify: `local-control-center/web/src/features/shell/ThreadBlockerCard.tsx:35-41` (imports), `:709-713`
- Modify: `local-control-center/web/src/design-system/layout.css` (tras `.thread-decision-options`, `:2258-2262`)
- Modify: `local_control_center/i18n/default_catalog.json` (tras `"app.threads.decisionTitle"`, `:814-817`)
- Create: `tests_web/thread-hardening.spec.js`

**Interfaces:**
- Consumes: `ThreadDecision` (`api/types.ts:92`, `metadata: Record<string, unknown>`), `metadata.questionKeys`/`metadata.questions` (Task 8).
- Produces: `decisionOptionLabel(option: string, t: Translate): string`, `decisionOptionDescription(option: string, t: Translate): string`, `decisionPromptText(decision: ThreadDecision, t: Translate): string`; helper de spec `openMockThread(page, {...})` reutilizado por Tasks 11–13. El valor enviado al resolver (`resolveDecision(decision.id, option)` / `runExecute(action, { answer })`) sigue siendo el código crudo.

- [ ] **Step 0: Preflight del toolchain web**

Run (PowerShell, raíz del repo):
```
node --version
corepack pnpm@10.24.0 --version
Test-Path node_modules/@playwright/test/cli.js
```
Expected: `v24.16.0`, `10.24.0`, `True`. Si no: aplicar el Preflight web de Global Constraints; si sigue fallando, detener Tasks 10–13 y reportar el bloqueo (el carril B backend T1/T8/T9 ya commiteado no se revierte).

- [ ] **Step 1: Spec Playwright que falla**

Crear `tests_web/thread-hardening.spec.js`:
```js
/**
 * Endurecimiento del panel de hilos: etiquetas legibles de decisión, tarjeta de research del intake,
 * espera por capacidad del equipo y pipeline detenido sin job activo. Cada caso mockea el overview,
 * el detalle, los eventos y las remediaciones del hilo para que el estado sea determinista.
 * @author Rodrigo Mason
 */
import { expect, test } from './fixtures/operations.js';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

async function openMockThread(
	page,
	{ id, title, status, events = [], decisions = [], remediations = [], running = false },
) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const createdAt = '2026-09-22T00:00:00Z';
	const thread = {
		id, projectId: project.id, ownerId: project.id, ownerType: 'workspace', title, summary: '',
		status, metadata: {}, createdAt, updatedAt: createdAt,
	};
	const eventRecords = events.map((item) => ({
		id: `${id}-event-${item.sequence}`, threadId: id, projectId: project.id, payload: {},
		metadata: {}, createdAt, ...item,
	}));
	const decisionRecords = decisions.map((item) => ({
		threadId: id, projectId: project.id, messageId: null, status: 'pending', resolution: null,
		decidedBy: null, decidedAt: null, metadata: {}, createdAt, updatedAt: createdAt, ...item,
	}));
	const remediationRecords = remediations.map((item) => ({
		projectId: project.id, threadId: id, loopId: '', title: 'Repair action',
		description: 'A persisted repair action.', payload: {}, status: 'pending',
		createdAt: '2026-09-22T00:00:00.000Z', resolvedAt: null, ...item,
	}));
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${id}`, (route) => route.fulfill({ json: {
		thread, messages: [], decisions: decisionRecords, artifacts: [], events: [],
	} }));
	await page.route(`**/api/v1/threads/${id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: remediationRecords } }),
	);
	await page.route(`**/api/v1/threads/${id}/events?*`, (route) => {
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		return route.fulfill({ json: {
			events: eventRecords.filter((item) => item.sequence > afterSeq),
			lastSeq: eventRecords.at(-1)?.sequence ?? 0, running, threadStatus: status,
		} });
	});
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	return page.getByRole('complementary', { name: 'Execution console' });
}

test('Threads: decision options show readable labels while sending the raw code', async ({ page }) => {
	await openMockThread(page, {
		id: 'thread-hardening-decision', title: 'Hardening decision labels', status: 'waiting_decision',
		decisions: [{
			id: 'decision-functionality', title: 'Existing functionality detected',
			prompt: 'Existing functionality detected: Workspace filters.',
			options: ['continue_existing', 'improve_existing', 'performance_pass', 'create_new_anyway'],
		}],
	});
	const decision = page.locator('.thread-decision-console').first();
	await expect(decision.getByRole('button', { name: 'Continue the existing work', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Create a new one anyway', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'continue_existing', exact: true })).toHaveCount(0);
	await expect(decision.getByText('Ignore the match and start separate work.')).toBeVisible();
});

test('Threads: intake questions render from their i18n key with readable options', async ({ page }) => {
	const question = 'What outcome should AIDO optimize for: diagnosis, implementation, or research?';
	await openMockThread(page, {
		id: 'thread-hardening-intake', title: 'Hardening intake question', status: 'waiting_decision',
		decisions: [{
			id: 'decision-intake', title: 'Decision needed (ask)', prompt: question,
			options: ['Diagnosis', 'Implementation', 'Research'],
			metadata: { questions: [question], questionKeys: ['app.threads.intake.question.outcome'] },
		}],
	});
	const decision = page.locator('.thread-decision-console').first();
	await expect(decision.getByText(question)).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Diagnose', exact: true })).toBeVisible();
	await expect(decision.getByRole('button', { name: 'Implement', exact: true })).toBeVisible();
});
```

- [ ] **Step 2: Ver el fallo**

Run (PowerShell, desde la raíz):
```
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js --project=desktop
```
Expected: FAIL en ambos tests (no existe botón "Continue the existing work" ni "Diagnose"; se ven los códigos crudos).

- [ ] **Step 3: Implementación mínima**

Crear `local-control-center/web/src/features/shell/decisionOptionCopy.ts`:
```ts
/**
 * Plain-language copy for the options a thread decision offers. The backend keeps sending and
 * receiving the raw code (`continue_existing`, `Research`…); only the visible label and a short
 * description are translated here, so an operator never has to read an internal identifier. Intake
 * questions travel with their i18n keys (`metadata.questionKeys`), aligned one-to-one with the
 * English text in `metadata.questions`, and render in the active language.
 * @author Rodrigo Mason
 */

import type { ThreadDecision } from '../../api/types';

type Translate = (key: string, fallback?: string) => string;

type OptionCopy = {
	labelKey: string;
	labelFallback: string;
	descriptionKey: string;
	descriptionFallback: string;
};

const OPTION_COPY: Record<string, OptionCopy> = {
	continue_existing: {
		labelKey: 'app.threads.decision.option.continueExisting.label',
		labelFallback: 'Continue the existing work',
		descriptionKey: 'app.threads.decision.option.continueExisting.description',
		descriptionFallback: 'Resume the thread that already built this functionality.',
	},
	improve_existing: {
		labelKey: 'app.threads.decision.option.improveExisting.label',
		labelFallback: 'Improve what exists',
		descriptionKey: 'app.threads.decision.option.improveExisting.description',
		descriptionFallback: 'Build on the existing functionality instead of starting over.',
	},
	performance_pass: {
		labelKey: 'app.threads.decision.option.performancePass.label',
		labelFallback: 'Performance pass',
		descriptionKey: 'app.threads.decision.option.performancePass.description',
		descriptionFallback: 'Keep the behavior and focus on making it faster.',
	},
	create_new_anyway: {
		labelKey: 'app.threads.decision.option.createNewAnyway.label',
		labelFallback: 'Create a new one anyway',
		descriptionKey: 'app.threads.decision.option.createNewAnyway.description',
		descriptionFallback: 'Ignore the match and start separate work.',
	},
	Diagnosis: {
		labelKey: 'app.threads.decision.option.diagnosis.label',
		labelFallback: 'Diagnose',
		descriptionKey: 'app.threads.decision.option.diagnosis.description',
		descriptionFallback: 'Investigate the cause before deciding on a change.',
	},
	Implementation: {
		labelKey: 'app.threads.decision.option.implementation.label',
		labelFallback: 'Implement',
		descriptionKey: 'app.threads.decision.option.implementation.description',
		descriptionFallback: 'Plan and build the change with the agent team.',
	},
	Research: {
		labelKey: 'app.threads.decision.option.research.label',
		labelFallback: 'Research',
		descriptionKey: 'app.threads.decision.option.research.description',
		descriptionFallback: 'Answer from cited web sources; no code is changed.',
	},
	'Continue in plan-only mode': {
		labelKey: 'app.threads.decision.option.planOnly.label',
		labelFallback: 'Continue in plan-only mode',
		descriptionKey: 'app.threads.decision.option.planOnly.description',
		descriptionFallback: 'Produce a plan without executing anything.',
	},
};

/** Visible label for a decision option; unknown options are shown verbatim. */
export function decisionOptionLabel(option: string, t: Translate): string {
	const copy = OPTION_COPY[option];
	return copy ? t(copy.labelKey, copy.labelFallback) : option;
}

/** One-line description for a decision option, or an empty string when there is none. */
export function decisionOptionDescription(option: string, t: Translate): string {
	const copy = OPTION_COPY[option];
	return copy ? t(copy.descriptionKey, copy.descriptionFallback) : '';
}

function stringList(value: unknown): string[] {
	return Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : [];
}

/** Decision prompt in the active language when the backend sent aligned question keys. */
export function decisionPromptText(decision: ThreadDecision, t: Translate): string {
	const metadata = decision.metadata ?? {};
	const keys = stringList(metadata.questionKeys);
	const questions = stringList(metadata.questions);
	if (!keys.length || keys.length !== questions.length) return decision.prompt;
	return keys.map((key, index) => t(key, questions[index])).join(' ');
}
```
`ThreadConversation.tsx`: agregar el import
```ts
import { decisionOptionDescription, decisionOptionLabel, decisionPromptText } from './decisionOptionCopy';
```
y reemplazar (`:561-573`):
```tsx
									<p>{decision.prompt}</p>
									<div className="thread-decision-options">
										{decision.options.map((option) => (
											<Button
												key={option}
												variant="secondary"
												disabled={busy}
												onClick={() => resolveDecision(decision.id, option)}
											>
												{option}
											</Button>
										))}
									</div>
```
por:
```tsx
									<p>{decisionPromptText(decision, t)}</p>
									<div className="thread-decision-options">
										{decision.options.map((option) => (
											<Button
												key={option}
												variant="secondary"
												disabled={busy}
												onClick={() => resolveDecision(decision.id, option)}
											>
												{decisionOptionLabel(option, t)}
											</Button>
										))}
									</div>
									{decision.options.some((option) => decisionOptionDescription(option, t)) ? (
										<ul className="thread-decision-option-help">
											{decision.options.map((option) => {
												const description = decisionOptionDescription(option, t);
												return description ? (
													<li key={option}>
														<strong>{decisionOptionLabel(option, t)}</strong> {description}
													</li>
												) : null;
											})}
										</ul>
									) : null}
```
`ThreadBlockerCard.tsx`: agregar `import { decisionOptionLabel } from './decisionOptionCopy';` y reemplazar:
```tsx
						{options.map((option) => (
							<option key={option} value={option}>
								{option}
```
por:
```tsx
						{options.map((option) => (
							<option key={option} value={option}>
								{decisionOptionLabel(option, t)}
```
`layout.css`, inmediatamente después del bloque `.thread-decision-options { ... }` (`:2258-2262`):
```css
.thread-decision-option-help {
	display: grid;
	gap: var(--space-1);
	margin: 0;
	padding-left: var(--space-4);
	color: var(--color-text-secondary);
	font-size: var(--font-size-xs);
}
```
Catálogo: insertar inmediatamente después del bloque:
```json
    "app.threads.decisionTitle": {
      "en": "Decision needed",
      "es": "Decisión requerida"
    },
```
lo siguiente:
```json
    "app.threads.decision.option.continueExisting.label": {
      "en": "Continue the existing work",
      "es": "Continuar el trabajo existente"
    },
    "app.threads.decision.option.continueExisting.description": {
      "en": "Resume the thread that already built this functionality.",
      "es": "Retoma el hilo que ya construyó esta funcionalidad."
    },
    "app.threads.decision.option.improveExisting.label": {
      "en": "Improve what exists",
      "es": "Mejorar lo existente"
    },
    "app.threads.decision.option.improveExisting.description": {
      "en": "Build on the existing functionality instead of starting over.",
      "es": "Construye sobre la funcionalidad existente en vez de empezar de cero."
    },
    "app.threads.decision.option.performancePass.label": {
      "en": "Performance pass",
      "es": "Pasada de rendimiento"
    },
    "app.threads.decision.option.performancePass.description": {
      "en": "Keep the behavior and focus on making it faster.",
      "es": "Mantiene el comportamiento y se enfoca en hacerlo más rápido."
    },
    "app.threads.decision.option.createNewAnyway.label": {
      "en": "Create a new one anyway",
      "es": "Crear uno nuevo de todos modos"
    },
    "app.threads.decision.option.createNewAnyway.description": {
      "en": "Ignore the match and start separate work.",
      "es": "Ignora la coincidencia e inicia un trabajo aparte."
    },
    "app.threads.decision.option.diagnosis.label": {
      "en": "Diagnose",
      "es": "Diagnosticar"
    },
    "app.threads.decision.option.diagnosis.description": {
      "en": "Investigate the cause before deciding on a change.",
      "es": "Investiga la causa antes de decidir un cambio."
    },
    "app.threads.decision.option.implementation.label": {
      "en": "Implement",
      "es": "Implementar"
    },
    "app.threads.decision.option.implementation.description": {
      "en": "Plan and build the change with the agent team.",
      "es": "Planifica y construye el cambio con el equipo de agentes."
    },
    "app.threads.decision.option.research.label": {
      "en": "Research",
      "es": "Investigar"
    },
    "app.threads.decision.option.research.description": {
      "en": "Answer from cited web sources; no code is changed.",
      "es": "Responde con fuentes web citadas; no se cambia código."
    },
    "app.threads.decision.option.planOnly.label": {
      "en": "Continue in plan-only mode",
      "es": "Continuar en modo solo plan"
    },
    "app.threads.decision.option.planOnly.description": {
      "en": "Produce a plan without executing anything.",
      "es": "Produce un plan sin ejecutar nada."
    },
```

- [ ] **Step 4: Verificación**

Run:
```
corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/shell/decisionOptionCopy.ts local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/features/shell/ThreadBlockerCard.tsx
corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/shell/decisionOptionCopy.ts local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/features/shell/ThreadBlockerCard.tsx local-control-center/web/src/design-system/layout.css
corepack pnpm@10.24.0 run typecheck:web
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_source_documentation_headers.py','-q','-p','no:randomly']))"
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js --project=desktop
```
Expected: biome sin errores; typecheck exit 0; pytest PASS (claves `t()` registradas, guardrails visuales, header `@author`); Playwright 2 passed.

- [ ] **Step 5: Commit**

```
git add local-control-center/web/src/features/shell/decisionOptionCopy.ts local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/features/shell/ThreadBlockerCard.tsx local-control-center/web/src/design-system/layout.css local_control_center/i18n/default_catalog.json tests_web/thread-hardening.spec.js
git commit -m "Fix (Threads): decisiones con etiquetas legibles y preguntas del intake traducidas" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: La tarjeta de research del intake explica el bloqueo real (UI de §2.2)

**Files:**
- Modify: `local-control-center/web/src/features/shell/remediationPresentation.ts` (constantes tras `WORKER_EXECUTION_INTERRUPTED`, `:86-94`; `buildBlockerCards` `:677-684`)
- Modify: `local_control_center/i18n/default_catalog.json` (tras `"app.threads.remediation.blocker.research_required.impact"`, `:11427-11430`)
- Modify: `tests_web/thread-hardening.spec.js` (agregar)

**Interfaces:**
- Consumes: `payload.researchRemediation` (Task 9), `payloadString` (`:624`), `openMockThread` (Task 10).
- Produces: `blockerCopyFor(first, records): BlockerCopy`; research sin loop usa `RESEARCH_INTAKE_BLOCKED` o `RESEARCH_PROVIDER_BLOCKED`; la research del loop conserva `BLOCKER_COPY.research_required` (sin cambios de tipos, así que los pins de `test_web_rework_architecture.py:468-471` siguen válidos).

- [ ] **Step 1: Tests que fallan**

Agregar a `tests_web/thread-hardening.spec.js`:
```js
test('Threads: an intake research blocked by the search provider explains the real cause', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-provider', title: 'Hardening research provider', status: 'blocked',
		events: [
			{ sequence: 1, type: 'research_running' },
			{ sequence: 2, type: 'blocked', payload: { stage: 'research', reason: 'The web search provider blocked the query (searxng: HTTP 403).' } },
		],
		remediations: [{
			id: 'remediation-research-settings', stage: 'research', blockerType: 'research_required',
			actionType: 'open_settings_section',
			payload: { section: 'research', status: 'research_blocked', researchRemediation: 'research_provider_blocked' },
		}],
	});
	await expect(execution.getByText('The search provider blocked the query', { exact: true })).toBeVisible();
	await expect(execution.getByText(/validated technical decision/)).toHaveCount(0);
});

test('Threads: a failed intake research does not talk about technical decisions', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-research', title: 'Hardening research failure', status: 'blocked',
		events: [
			{ sequence: 1, type: 'research_running' },
			{ sequence: 2, type: 'blocked', payload: { stage: 'research', reason: 'ResearchAgent web search returned no sources.' } },
		],
		remediations: [{
			id: 'remediation-research-worker', stage: 'research', blockerType: 'research_required',
			actionType: 'run_worker_once', payload: { status: 'research_blocked' },
		}],
	});
	await expect(execution.getByText('Research could not answer', { exact: true })).toBeVisible();
	await expect(execution.getByText(/validated technical decision/)).toHaveCount(0);
});
```
Run:
```
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js --project=desktop -g "intake research"
```
Expected: FAIL: se muestra "Research evidence required" con "…validated technical decision…".

- [ ] **Step 2: Implementación mínima**

Después de `WORKER_EXECUTION_INTERRUPTED` agregar:
```ts
/** Research the thread intake launched: it has no loop and no technical decision to adopt. */
const RESEARCH_INTAKE_BLOCKED: BlockerCopy = {
	titleKey: 'app.threads.remediation.blocker.research_intake.title',
	titleFallback: 'Research could not answer',
	explanationKey: 'app.threads.remediation.blocker.research_intake.explanation',
	explanationFallback:
		'ResearchAgent could not complete this question. Review the reported cause before retrying.',
	impactKey: 'app.threads.remediation.blocker.research_intake.impact',
	impactFallback: 'No code is changed; the question stays open until research completes.',
	settingsSection: 'research',
	settingsLabelKey: 'app.threads.remediation.action.openResearch',
	settingsLabelFallback: 'Open research',
};

/** The web search provider refused the query: a provider problem, not a lack of sources. */
const RESEARCH_PROVIDER_BLOCKED: BlockerCopy = {
	titleKey: 'app.threads.remediation.blocker.research_provider_blocked.title',
	titleFallback: 'The search provider blocked the query',
	explanationKey: 'app.threads.remediation.blocker.research_provider_blocked.explanation',
	explanationFallback:
		'The web search provider refused this query, so ResearchAgent got no sources. It is not a lack of results.',
	impactKey: 'app.threads.remediation.blocker.research_provider_blocked.impact',
	impactFallback:
		'The question stays unanswered until research uses a provider that accepts it, such as your own SearXNG.',
	settingsSection: 'research',
	settingsLabelKey: 'app.threads.remediation.action.openResearch',
	settingsLabelFallback: 'Open research',
};

/** Picks one card's copy; worker execution and intake research have their own variants. */
function blockerCopyFor(first: RemediationActionRecord, records: RemediationActionRecord[]): BlockerCopy {
	if (first.stage === 'worker' && first.blockerType === 'runtime_execution_failed') {
		return records.some((record) => payloadString(record.payload, 'interruptedExecutionId'))
			? WORKER_EXECUTION_INTERRUPTED
			: WORKER_EXECUTION_FAILED;
	}
	if (first.blockerType === 'research_required' && !first.loopId) {
		return records.some(
			(record) => payloadString(record.payload, 'researchRemediation') === 'research_provider_blocked',
		)
			? RESEARCH_PROVIDER_BLOCKED
			: RESEARCH_INTAKE_BLOCKED;
	}
	return BLOCKER_COPY[first.blockerType] ?? GENERIC_BLOCKER;
}
```
(`payloadString` es una `function` declarada más abajo en el mismo módulo: el hoisting permite usarla.) En `buildBlockerCards` reemplazar:
```ts
		const baseCopy =
			first.stage === 'worker' && first.blockerType === 'runtime_execution_failed'
				? records.some((record) => payloadString(record.payload, 'interruptedExecutionId'))
					? WORKER_EXECUTION_INTERRUPTED
					: WORKER_EXECUTION_FAILED
				: (BLOCKER_COPY[first.blockerType] ?? GENERIC_BLOCKER);
```
por:
```ts
		const baseCopy = blockerCopyFor(first, records);
```
Catálogo: insertar inmediatamente después de:
```json
    "app.threads.remediation.blocker.research_required.impact": {
      "en": "Existing decisions stay unchanged; a version is not accepted automatically.",
      "es": "Las decisiones existentes se conservan; no se acepta una versión automáticamente."
    },
```
lo siguiente:
```json
    "app.threads.remediation.blocker.research_intake.title": {
      "en": "Research could not answer",
      "es": "La investigación no pudo responder"
    },
    "app.threads.remediation.blocker.research_intake.explanation": {
      "en": "ResearchAgent could not complete this question. Review the reported cause before retrying.",
      "es": "ResearchAgent no pudo completar esta pregunta. Revisa la causa informada antes de reintentar."
    },
    "app.threads.remediation.blocker.research_intake.impact": {
      "en": "No code is changed; the question stays open until research completes.",
      "es": "No se cambia código; la pregunta queda abierta hasta que la investigación termine."
    },
    "app.threads.remediation.blocker.research_provider_blocked.title": {
      "en": "The search provider blocked the query",
      "es": "El proveedor de búsqueda bloqueó la consulta"
    },
    "app.threads.remediation.blocker.research_provider_blocked.explanation": {
      "en": "The web search provider refused this query, so ResearchAgent got no sources. It is not a lack of results.",
      "es": "El proveedor de búsqueda web rechazó esta consulta, por eso ResearchAgent no obtuvo fuentes. No es falta de resultados."
    },
    "app.threads.remediation.blocker.research_provider_blocked.impact": {
      "en": "The question stays unanswered until research uses a provider that accepts it, such as your own SearXNG.",
      "es": "La pregunta queda sin respuesta hasta que la investigación use un proveedor que la acepte, como tu propio SearXNG."
    },
```

- [ ] **Step 3: Verificación**

Run:
```
corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/shell/remediationPresentation.ts
corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/shell/remediationPresentation.ts
corepack pnpm@10.24.0 run typecheck:web
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','-q','-p','no:randomly']))"
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js tests_web/research-resolution.spec.js --project=desktop
```
Expected: todo en verde; `research-resolution.spec.js` (research del loop con `loopId`) conserva "Research evidence required".

- [ ] **Step 4: Comprobación del spec**

Run:
```
git grep -n -i "high-impact decision" -- local-control-center/web/src local_control_center/i18n
```
Expected: sin coincidencias (la frase no existía; el copy engañoso real era el de `research_required`, ahora reservado al loop).

- [ ] **Step 5: Commit**

```
git add local-control-center/web/src/features/shell/remediationPresentation.ts local_control_center/i18n/default_catalog.json tests_web/thread-hardening.spec.js
git commit -m "Fix (Threads): la tarjeta de research del intake explica el bloqueo real" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: El panel muestra la espera de capacidad del equipo con su motivo (UI de §2.4)

**Files:**
- Create: `local-control-center/web/src/features/runtime-setup/reasonCopy.ts`
- Modify: `local-control-center/web/src/features/runtime-setup/RuntimeHealthModal.tsx:37-96`
- Modify: `local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx:11-21` (imports), `:198-213` (helper), `:283-306` (banner), `:594-640` (`eventTitle`)
- Modify: `local_control_center/i18n/default_catalog.json` (tras `"app.threads.waitingWorker"` `:1106-1109`; antes de `"app.runtime.health.reason.minimum_free_disk"` `:13631`)
- Modify: `tests_web/thread-hardening.spec.js` (agregar)

**Interfaces:**
- Consumes: evento de hilo `resource_wait` con `payload.reasonCode` (Task 3).
- Produces: `REASON_COPY`, `describeReason(reason: string, t): string`, `describeReasonCode(code: string, t): string` (módulo compartido; el modal deja de definirlos).

- [ ] **Step 1: Test que falla**

Agregar a `tests_web/thread-hardening.spec.js`:
```js
test('Threads: a queued run waiting for machine capacity shows the readable reason', async ({ page }) => {
	await page.route('**/api/v1/workers/status', (route) => route.fulfill({ json: {
		status: 'running', running: true, paused: false, autostart: true, reason: '',
		maxConcurrentJobs: 1, pollIntervalSeconds: 1, inFlightJobs: 0, claimedJobs: 0,
		completedRuns: 0, failedRuns: 0,
	} }));
	const execution = await openMockThread(page, {
		id: 'thread-hardening-capacity', title: 'Hardening capacity wait', status: 'queued', running: true,
		events: [
			{ sequence: 1, type: 'run_queued' },
			{ sequence: 2, type: 'resource_wait', payload: {
				jobId: 'job-capacity', reasonCode: 'minimum_free_memory',
				reason: 'Available memory is below the configured workload admission threshold.',
			} },
		],
	});
	const banner = execution.getByRole('region', { name: 'Waiting for machine capacity' });
	await expect(banner.getByText('Waiting for machine capacity', { exact: true })).toBeVisible();
	await expect(execution.getByRole('region', { name: 'Waiting for worker' })).toHaveCount(0);
	await expect(banner).toContainText('This machine does not have enough free RAM right now');
});
```
Run (build + `-g "machine capacity"` como en Task 11). Expected: FAIL por timeout del locator: no existe la región "Waiting for machine capacity" (el banner se llama "Waiting for worker" y dice "Queued: waiting for a worker to pick this run up.").

- [ ] **Step 2: Implementación mínima**

Crear `local-control-center/web/src/features/runtime-setup/reasonCopy.ts`:
```ts
/**
 * Plain-language text for the machine reason codes that readiness and the host resource governor
 * report (`minimum_free_memory`, `host_cpu_saturated`…). Host-capacity codes say the machine lacks
 * room, so they never read as a configuration problem. Shared by the AI health modal and the thread
 * execution panel so both explain the same code with the same words.
 * @author Rodrigo Mason
 */

type Translate = (key: string, fallback?: string) => string;

export const REASON_COPY = new Map<string, { key: string; fallback: string }>([
	[
		'health_check_required',
		{
			key: 'app.runtime.health.reason.health_check_required',
			fallback: 'The runtime has not passed a recent health check.',
		},
	],
	[
		'minimum_free_memory',
		{
			key: 'app.runtime.health.reason.minimum_free_memory',
			fallback:
				'This machine does not have enough free RAM right now; this is not a configuration problem.',
		},
	],
	[
		'hard_memory_floor',
		{
			key: 'app.runtime.health.reason.hard_memory_floor',
			fallback:
				"This machine's free RAM is below its safety floor; this is not a configuration problem.",
		},
	],
	[
		'aggregate_memory_budget',
		{
			key: 'app.runtime.health.reason.aggregate_memory_budget',
			fallback:
				"The AI work already running uses this machine's whole RAM budget; this is not a configuration problem.",
		},
	],
	[
		'minimum_free_disk',
		{
			key: 'app.runtime.health.reason.minimum_free_disk',
			fallback:
				'This machine does not have enough free disk space; this is not a configuration problem.',
		},
	],
	[
		'host_cpu_saturated',
		{
			key: 'app.runtime.health.reason.host_cpu_saturated',
			fallback:
				"This machine's CPU is busy above the admission limit; this is not a configuration problem.",
		},
	],
	[
		'aggregate_cpu_budget',
		{
			key: 'app.runtime.health.reason.aggregate_cpu_budget',
			fallback:
				"The AI work already running uses this machine's whole CPU budget; this is not a configuration problem.",
		},
	],
	[
		'heavy_workload_capacity',
		{
			key: 'app.runtime.health.reason.heavy_workload_capacity',
			fallback: 'Another heavy job is already using the only heavy-work slot.',
		},
	],
	[
		'light_workload_capacity',
		{
			key: 'app.runtime.health.reason.light_workload_capacity',
			fallback: 'The limit of simultaneous light jobs is already in use.',
		},
	],
]);

/** Text for one reason code, or the code itself when it has no known copy. */
export function describeReasonCode(code: string, t: Translate): string {
	const copy = REASON_COPY.get(code);
	return copy ? t(copy.key, copy.fallback) : code;
}

/** Renders a comma-joined list of reason codes as text, keeping unknown codes (and prose) verbatim. */
export function describeReason(reason: string, t: Translate): string {
	const codes = reason.split(', ');
	if (!codes.some((code) => REASON_COPY.has(code))) return reason;
	return codes.map((code) => describeReasonCode(code, t)).join(' ');
}
```
`RuntimeHealthModal.tsx`: eliminar el bloque desde el JSDoc `/** Plain-language text for the machine reason codes…` (`:37`) hasta el cierre de `describeReason` (`:96`) inclusive, y agregar `import { describeReason } from './reasonCopy';` (los usos existentes de `describeReason(...)` quedan igual).

`ThreadExecutionPanel.tsx`: agregar `import { describeReasonCode } from '../runtime-setup/reasonCopy';`. Después de `REMEDIATION_EXCLUDE_IN_PANEL` agregar:
```ts
/** Reason code of the newest `resource_wait` the governor reported after the last hand-off to a worker. */
function latestResourceWait(events: ThreadAgentEvent[]): string {
	let code = '';
	for (const event of events) {
		if (event.type === 'resource_wait') code = textValue(safeRecord(event.payload).reasonCode) ?? '';
		else if (event.type === 'worker_claimed' || event.type === 'run_queued') code = '';
	}
	return code;
}

type QueuedBannerCopy = { label: string; title: string; body: string };

/**
 * Accessible name, title and body of the queued banner: starting, waiting for machine capacity, or for a worker.
 * The starting state keeps the historical "Waiting for worker" region name that existing specs locate.
 */
function queuedBannerCopy(workerBusy: boolean, resourceWaitCode: string, t: Translate): QueuedBannerCopy {
	if (workerBusy) {
		return {
			label: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
			title: t('app.threads.startingRunTitle', 'Starting run'),
			body: t('app.threads.startingRun', 'Starting this run — progress appears in the execution log below.'),
		};
	}
	if (resourceWaitCode) {
		return {
			label: t('app.threads.waitingCapacityTitle', 'Waiting for machine capacity'),
			title: t('app.threads.waitingCapacityTitle', 'Waiting for machine capacity'),
			body: `${t('app.threads.waitingCapacity', 'Waiting for machine capacity:')} ${describeReasonCode(resourceWaitCode, t)}`,
		};
	}
	return {
		label: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
		title: t('app.threads.waitingWorkerTitle', 'Waiting for worker'),
		body: t('app.threads.waitingWorker', 'Queued: waiting for a worker to pick this run up.'),
	};
}
```
Dentro de `ThreadExecutionPanel`, después de `const consoleEntries = useMemo(...)` agregar:
```ts
	const resourceWaitCode = useMemo(() => latestResourceWait(events), [events]);
	const queuedCopy = queuedBannerCopy(workerBusy, resourceWaitCode, t);
```
y reemplazar en el banner (`:289-304`), incluido su nombre accesible:
```tsx
				<section
					className="thread-queued-banner"
					aria-label={t('app.threads.waitingWorkerTitle', 'Waiting for worker')}
				>
					<strong>
						{workerBusy
							? t('app.threads.startingRunTitle', 'Starting run')
							: t('app.threads.waitingWorkerTitle', 'Waiting for worker')}
					</strong>
					<p>
						{workerBusy
							? t(
									'app.threads.startingRun',
									'Starting this run — progress appears in the execution log below.',
								)
							: t('app.threads.waitingWorker', 'Queued: waiting for a worker to pick this run up.')}
					</p>
```
por:
```tsx
				<section className="thread-queued-banner" aria-label={queuedCopy.label}>
					<strong>{queuedCopy.title}</strong>
					<p>{queuedCopy.body}</p>
```
En `eventTitle` agregar la entrada `resource_wait: 'Waiting for capacity',` después de `worker_claimed: 'Worker assigned',`.

Catálogo: insertar inmediatamente después de:
```json
    "app.threads.waitingWorker": {
      "en": "Queued: waiting for a worker to pick this run up.",
      "es": "En cola: esperando que un worker tome esta ejecución."
    },
```
lo siguiente:
```json
    "app.threads.waitingCapacityTitle": {
      "en": "Waiting for machine capacity",
      "es": "Esperando capacidad del equipo"
    },
    "app.threads.waitingCapacity": {
      "en": "Waiting for machine capacity:",
      "es": "Esperando capacidad del equipo:"
    },
    "app.threads.event.resource_wait": {
      "en": "Waiting for capacity",
      "es": "Esperando capacidad"
    },
```
e inmediatamente ANTES de la línea `    "app.runtime.health.reason.minimum_free_disk": {` (es la última entrada del catálogo):
```json
    "app.runtime.health.reason.host_cpu_saturated": {
      "en": "This machine's CPU is busy above the admission limit; this is not a configuration problem.",
      "es": "La CPU de este equipo está ocupada sobre el límite de admisión; no es un problema de configuración."
    },
    "app.runtime.health.reason.aggregate_cpu_budget": {
      "en": "The AI work already running uses this machine's whole CPU budget; this is not a configuration problem.",
      "es": "El trabajo de IA en curso ya usa todo el presupuesto de CPU del equipo; no es un problema de configuración."
    },
    "app.runtime.health.reason.heavy_workload_capacity": {
      "en": "Another heavy job is already using the only heavy-work slot.",
      "es": "Otro trabajo pesado ya ocupa el único cupo de trabajo pesado."
    },
    "app.runtime.health.reason.light_workload_capacity": {
      "en": "The limit of simultaneous light jobs is already in use.",
      "es": "El límite de trabajos livianos simultáneos ya está en uso."
    },
```

- [ ] **Step 3: Verificación**

Run:
```
corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/runtime-setup/reasonCopy.ts local-control-center/web/src/features/runtime-setup/RuntimeHealthModal.tsx local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx
corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/runtime-setup/reasonCopy.ts local-control-center/web/src/features/runtime-setup/RuntimeHealthModal.tsx local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx
corepack pnpm@10.24.0 run typecheck:web
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_source_documentation_headers.py','-q','-p','no:randomly']))"
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js tests_web/thread-pipeline.spec.js --project=desktop
```
Expected: todo verde; `thread-pipeline.spec.js` sigue viendo "Waiting for worker" cuando no hay `resource_wait`.

- [ ] **Step 4: Commit**

```
git add local-control-center/web/src/features/runtime-setup/reasonCopy.ts local-control-center/web/src/features/runtime-setup/RuntimeHealthModal.tsx local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx local_control_center/i18n/default_catalog.json tests_web/thread-hardening.spec.js
git commit -m "Feature (Threads): el panel muestra la espera de capacidad del equipo con su motivo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: El pipeline marca detenido el paso activo cuando no hay job (§2.7)

**Files:**
- Modify: `local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx:104` (`StepState`), `:125-196` (`derivePipeline`)
- Modify: `local-control-center/web/src/design-system/layout.css` (tras `:2080-2086`)
- Modify: `local_control_center/i18n/default_catalog.json` (tras `"app.threads.stepState.blocked"`, `:994-997`)
- Modify: `tests_web/thread-hardening.spec.js` (agregar)

**Interfaces:**
- Consumes: `threadStatus` (`ThreadStatus` Literal, `threads/contracts.py:33-43`).
- Produces: `StepState` agrega `'stopped'`; con `threadStatus` ∈ {`open`, `archived`} y sin bloqueo, el paso que estaba `active` pasa a `stopped` (los hitos `completed`/`delivered` ya cierran todo en `done` y `resolved` también). Los pins `blocked: isBlocked,` y `const remediationFallback = pipeline.blocked` (`test_web_rework_architecture.py:451+`) y el literal `MILESTONE_INDEX` (`test_product_loop_result_taxonomy.py`) no se tocan.

- [ ] **Step 1: Test que falla**

Agregar a `tests_web/thread-hardening.spec.js`:
```js
test('Threads: without an active job the pipeline stops the last active step', async ({ page }) => {
	const execution = await openMockThread(page, {
		id: 'thread-hardening-stopped', title: 'Hardening stopped pipeline', status: 'open',
		events: [
			{ sequence: 1, type: 'runtime_check' },
			{ sequence: 2, type: 'git_check' },
			{ sequence: 3, type: 'branch_ready' },
		],
	});
	await expect(execution.locator('.thread-pipeline-step', { hasText: 'Branch ready' })).toHaveAttribute('data-state', 'stopped');
	await expect(execution.locator('.thread-pipeline-step[data-state="active"]')).toHaveCount(0);
});
```
Run (build + `-g "stops the last active step"`). Expected: FAIL (`data-state="active"`).

- [ ] **Step 2: Implementación mínima**

`:104` reemplazar:
```ts
type StepState = 'pending' | 'active' | 'done' | 'blocked';
```
por:
```ts
type StepState = 'pending' | 'active' | 'done' | 'blocked' | 'stopped';

/** Thread states with no job in flight: nothing is running, so no step can still be active. */
const IDLE_THREAD_STATUSES = new Set(['open', 'archived']);
```
En `derivePipeline`, reemplazar:
```ts
	const doneAll = current >= PIPELINE_STEPS.length || threadStatus === 'resolved';
	const states = PIPELINE_STEPS.map((_, index): StepState => {
		if (doneAll) return 'done';
		if (index === current) return isBlocked ? 'blocked' : 'active';
```
por:
```ts
	const doneAll = current >= PIPELINE_STEPS.length || threadStatus === 'resolved';
	const idle = !isBlocked && IDLE_THREAD_STATUSES.has(threadStatus);
	const states = PIPELINE_STEPS.map((_, index): StepState => {
		if (doneAll) return 'done';
		if (index === current) {
			if (isBlocked) return 'blocked';
			return idle ? 'stopped' : 'active';
		}
```
`layout.css`, inmediatamente después del bloque `.thread-pipeline-step[data-state="blocked"] .thread-pipeline-dot { ... }`:
```css
.thread-pipeline-step[data-state="stopped"] {
	color: var(--color-text-secondary);
}

.thread-pipeline-step[data-state="stopped"] .thread-pipeline-dot {
	background: var(--color-status-warning);
}
```
Catálogo: insertar inmediatamente después de:
```json
    "app.threads.stepState.blocked": {
      "en": "blocked",
      "es": "bloqueado"
    },
```
lo siguiente:
```json
    "app.threads.stepState.stopped": {
      "en": "stopped",
      "es": "detenido"
    },
```

- [ ] **Step 3: Verificación**

Run:
```
corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx local-control-center/web/src/design-system/layout.css
corepack pnpm@10.24.0 run typecheck:web
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_product_loop_result_taxonomy.py','-q','-p','no:randomly']))"
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js tests_web/thread-pipeline.spec.js tests_web/research-resolution.spec.js --project=desktop
```
Expected: todo verde (los estados `queued`/`blocked`/`running` de `thread-pipeline.spec.js` conservan `active`/`blocked`).

- [ ] **Step 4: Commit**

```
git add local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx local-control-center/web/src/design-system/layout.css local_control_center/i18n/default_catalog.json tests_web/thread-hardening.spec.js
git commit -m "Fix (Threads): el pipeline marca detenido el paso activo cuando no hay job" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Gate final de verificación (integración de los tres carriles)

**Files:**
- Test: todos los archivos tocados; sin cambios de código salvo que un gate falle (en ese caso: detenerse y abrir el hallazgo en la tarea dueña del archivo, máximo dos vueltas).

**Interfaces:**
- Consumes: `dev` con los commits de Tasks 1–13.
- Produces: evidencia de cierre (salidas de comandos) para el informe final.

- [ ] **Step 1: Suite Python dirigida**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_source_documentation_headers.py','tests_py/test_i18n_platform.py','tests_py/test_runtime_health_refresh.py','tests_py/test_host_resource_governor.py','tests_py/test_orphan_child_jobs.py','tests_py/test_thread_memory_service.py','tests_py/test_thread_similarity_service.py','tests_py/test_product_loop_coordinator.py','tests_py/test_remediation_job_lifecycle.py','tests_py/test_local_worker_runtime.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_overview_immutable_evidence_cache.py','tests_py/test_control_plane_overview_caps.py','tests_py/test_overview_schema_drift.py','tests_py/test_intent_classifier.py','tests_py/test_threads_coordinator.py','tests_py/test_research_web_search.py','tests_py/test_research_agent.py','tests_py/test_research_resolution.py','tests_py/test_settings_slice.py','-q','-p','no:randomly']))"
corepack pnpm@10.24.0 run quality:architecture
```
Expected: PASS; `quality:architecture` exit 0.

- [ ] **Step 2: Ruff sobre todo lo tocado**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format --check local_control_center tests_py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center tests_py
```
Expected: sin archivos por reformatear ni errores.

- [ ] **Step 3: Contratos (sin drift de OpenAPI)**

Run:
```
corepack pnpm@10.24.0 run openapi:generate
git status --short local-control-center/web/src/api/generated
```
Expected: sin cambios en `generated/` (ningún `response_model` cambió). Si aparece diff, el cambio de contrato es no intencional: revertir el archivo generado y reportar qué modelo cambió.

- [ ] **Step 4: Web**

Run:
```
node --version
corepack pnpm@10.24.0 --version
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 run check:web
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT='9471'; $env:PLAYWRIGHT_DB_PATH='.tmp/pw-thread-hardening.sqlite'; node node_modules/@playwright/test/cli.js test tests_web/thread-hardening.spec.js tests_web/thread-pipeline.spec.js tests_web/thread-remediations.spec.js tests_web/research-resolution.spec.js --project=desktop --project=mobile
```
Expected: `v24.16.0` y `10.24.0` (si no, Preflight web de Global Constraints y detenerse si persiste); exit 0 en todos; Playwright verde en ambos proyectos.

- [ ] **Step 5: Finales de línea**

Run:
```
git diff --check 9f965e00..HEAD
git ls-files --eol -- local_control_center/research/web_search.py tests_py/test_orphan_child_jobs.py tests_py/test_overview_immutable_evidence_cache.py tests_py/test_research_web_search.py local-control-center/web/src/features/shell/decisionOptionCopy.ts local-control-center/web/src/features/runtime-setup/reasonCopy.ts tests_web/thread-hardening.spec.js local_control_center/i18n/default_catalog.json
```
Expected: sin errores de whitespace; todos `i/lf w/lf`.

---

### Task 15: Verificación en vivo (operador): SearXNG, overview, zombi y corpus

**Files:** ninguno del repo. Configuración de SearXNG fuera del repo en `%LOCALAPPDATA%\AIDO\searxng\settings.yml`.

**Interfaces:**
- Consumes: AIDO reiniciado con el código de `dev` (lo reinicia el operador), Docker Desktop.
- Produces: evidencia en vivo de §2.2, §2.3, §2.8 y §2.10; SearXNG queda corriendo al cerrar (es el proveedor por defecto).

- [ ] **Step 1: Confirmación previa (acción externa + descarga) — gate de operador**

El orquestador pide al operador, en una línea y al momento de ejecutar (no al aprobar el plan), confirmación explícita para: descargar la imagen oficial `searxng/searxng` (Docker Hub, tamaño a informar con `docker manifest inspect searxng/searxng` antes de descargar) y crear el contenedor `aido-searxng` en `127.0.0.1:8888`. Sin "sí" explícito, detenerse aquí y reportar el paso como pendiente. Antes de ejecutar, contrastar puerto interno (`8080`), ruta de configuración (`/etc/searxng`) y el requisito de `search.formats` con la documentación oficial vigente (https://docs.searxng.org/admin/installation-docker.html y https://docs.searxng.org/dev/search_api.html); si difieren, ajustar los comandos a la doc y anotarlo.

- [ ] **Step 2: Levantar SearXNG con JSON habilitado**

Crear `%LOCALAPPDATA%\AIDO\searxng\settings.yml`:
```yaml
use_default_settings: true
server:
  secret_key: "<generar con: python -c \"import secrets; print(secrets.token_hex(32))\">"
  limiter: false
search:
  formats:
    - html
    - json
```
Run (PowerShell; sólo tras el "sí" del Step 1, la descarga es explícita y no implícita en `docker run`):
```
docker pull searxng/searxng
docker run -d --name aido-searxng -p 127.0.0.1:8888:8080 -v "$env:LOCALAPPDATA\AIDO\searxng:/etc/searxng" searxng/searxng
Invoke-RestMethod "http://127.0.0.1:8888/search?q=python+pep+703&format=json" | Select-Object -ExpandProperty results | Select-Object -First 3 url,title
```
Expected: 3 resultados con `url` http(s). Si responde 403, el formato JSON no quedó habilitado: corregir `settings.yml` y reiniciar el contenedor.

- [ ] **Step 3: Research extremo a extremo con el mensaje original**

En la UI de AIDO (reiniciado por el operador), crear un hilo con: "Investiga en modo solo lectura: ¿qué versión de Python exige pyproject.toml de este repo? Responde en una línea y no modifiques archivos." y otro con "Research the official Python documentation and cite sources: which Python version introduced the free-threaded build (PEP 703)? Answer in one line."
Expected: ambos se rutean a `thread.research.run` sin decisión intermedia; el segundo termina `research_ready` con fuentes. El primero puede terminar sin respuesta útil porque el ResearchAgent es sólo web (limitación conocida, fuera de alcance): lo que se verifica es el ruteo y que un bloqueo, si ocurre, se rotule "The search provider blocked the query" y no "no sources". Prueba negativa con el proveedor caído (el caso automatizado equivalente es `test_research_run_with_searxng_down_blocks_with_network_remediation`, Task 9; aquí se verifica la UI). SearXNG es el proveedor por defecto desde Task 9, así que el contenedor se restaura SIEMPRE, también si la verificación falla:
```
docker stop aido-searxng
try {
    Read-Host "Repetir en la UI el hilo de PEP 703 y verificar la tarjeta; Enter para restaurar SearXNG"
} finally {
    docker start aido-searxng
}
docker inspect -f '{{.State.Running}}' aido-searxng
Invoke-RestMethod "http://127.0.0.1:8888/search?q=python+pep+703&format=json" | Select-Object -ExpandProperty results | Select-Object -First 3 url,title
```
(Lo ejecuta el operador en su terminal: la herramienta del agente no admite `Read-Host`; si lo corre un agente, separar en `docker stop`, verificación y `docker start` + smoke como pasos obligatorios aunque la verificación falle.)
Expected: con el contenedor detenido, la tarjeta dice "Research could not answer" con causa "ResearchAgent web search failed: …" (red, acción "Check network access"), no "blocked", y el job termina `research_blocked` (no `failed` por excepción). Tras el `finally`: `true` y 3 resultados con `url` http(s); repetir el segundo hilo una vez más y confirmar `research_ready` con fuentes antes de cerrar.

- [ ] **Step 3b: Saneamiento histórico del zombi (escritura en la BD viva) — gate de operador**

El orquestador pide al operador confirmación explícita, en una línea y al momento de ejecutar, para fallar `job-31a6b390-864a-4caf-96f0-980842fb6012` en la BD viva. Sin "sí", reportar el paso como pendiente y no ejecutar (1)–(3). Orden obligatorio:

(1) SELECT read-only (`mode=ro`) de precondiciones:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:C:/Users/Rodd/.claude/local-control-center/platform.sqlite?mode=ro', uri=True); print(c.execute(\"select status, lease_expires_at, kind, json_extract(payload,'$.parentJobId') from jobs where id='job-31a6b390-864a-4caf-96f0-980842fb6012'\").fetchone())"
```
Expected: `('running', None, 'agent.product_owner', None)`. Cualquier otro valor: detenerse y reportar.

(2) Respaldo inmediatamente antes de la escritura, con la API `backup` de sqlite3 (copia consistente aunque AIDO esté corriendo en WAL), y verificación de integridad de la copia:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sqlite3, sys; src=sqlite3.connect('file:C:/Users/Rodd/.claude/local-control-center/platform.sqlite?mode=ro', uri=True); dst=sqlite3.connect(sys.argv[1]); src.backup(dst); print(dst.execute('pragma integrity_check').fetchone()); print(dst.execute(\"select status from jobs where id='job-31a6b390-864a-4caf-96f0-980842fb6012'\").fetchone()); dst.close(); src.close()" "<scratchpad>\platform-before-zombie-cleanup.sqlite"
```
Expected: `('ok',)` y `('running',)`. Sin ambas líneas, no escribir. Restauración si hiciera falta (con AIDO detenido por el operador): apartar `platform.sqlite-wal`/`-shm` (moverlos, no borrarlos) y copiar ese archivo sobre `platform.sqlite`.

(3) Escritura. Guardar en el scratchpad `fail_legacy_zombie.py`:
```python
import sys
from contextlib import closing

sys.modules["faiss"] = None
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.shared.db import open_sqlite_connection

with closing(open_sqlite_connection(sys.argv[1])) as connection, connection:
    print(JobsRepository(connection).fail_legacy_orphan_child_jobs(sys.argv[2:]))
```
Run (con `PYTHONPATH` = raíz del repo):
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe <scratchpad>\fail_legacy_zombie.py C:/Users/Rodd/.claude/local-control-center/platform.sqlite job-31a6b390-864a-4caf-96f0-980842fb6012
```
Expected: `['job-31a6b390-864a-4caf-96f0-980842fb6012']` (una lista vacía significa que el job ya no cumple las condiciones: no reintentar con otra consulta; reportar).

- [ ] **Step 4: Evidencia read-only en la BD viva (sólo SELECT, `mode=ro`)**

Run:
```
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('file:C:/Users/Rodd/.claude/local-control-center/platform.sqlite?mode=ro', uri=True); print(c.execute(\"select status, json_extract(payload,'$.result.reason') from jobs where id='job-31a6b390-864a-4caf-96f0-980842fb6012'\").fetchone()); print(c.execute(\"select status from agent_runs where id like 'agent-run-905924ea%'\").fetchone()); print(c.execute(\"select operation, count(*) from operational_executions where operation in ('models.health_cli_runtime','models.provider_health_check') and created_at > strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 minutes') group by operation\").fetchall()); print(c.execute(\"select count(*) from events where type='job.resource_wait' and created_at > strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 minutes')\").fetchone())"
```
Expected: `('failed', 'parent_lease_expired')`; `('failed',)`; en 30 min ≤ 1 check cada 5 min por runtime estable y como máximo 1 cada 60 s–15 min para uno que no renueva evidencia (antes 3–5/min); `job.resource_wait` = número de cambios de motivo, no ~900.

- [ ] **Step 5: Overview en vivo**

Con AIDO corriendo, medir 3 llamadas consecutivas pasados 10 s del arranque:
```
1..3 | ForEach-Object { (Measure-Command { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:4310/api/v1/overview -Headers @{ 'X-Local-Control-Token' = '<token del handshake>' } | Out-Null }).TotalMilliseconds }
```
(el token lo obtiene el operador desde `GET /api/v1/security/handshake`; el agente no llama a `:4310` sin autorización del operador). Expected: < 1000 ms desde la segunda llamada.

---

## Self-Review

| Spec | Requisito | Task(s) |
|---|---|---|
| §2.1 | `IntentClassification` expone `scores` | T8 |
| §2.1 | Ruta solo-investigación por puntaje relativo o marcador explícito; verbo de cambio gana el empate | T8 (`_is_research_only`, `coordinator.py:265`) |
| §2.1 | Marcadores es/en con acentos plegados | T8 (`READ_ONLY_MARKERS`, `_fold_accents`) |
| §2.1 | Vocabulario español de research reincorporado con plegado | T8 |
| §2.1 | Preguntas del clasificador a claves i18n | T8 (backend) + T10 (render) |
| §2.1 | Corpus obligatorio en ambos sentidos, parametrizado | T8 (`CHANGE_PROMPTS`, `RESEARCH_PROMPTS`, incluye el mensaje original) |
| §2.2 | Proveedor `searxng` `GET {baseUrl}/search?q=…&format=json` | T9 |
| §2.2 | Settings `research.webSearch.provider` y `.baseUrl` validados | T9 (loopback; ver Execution Notes sobre "allowlist") |
| §2.2 | Riesgo residual SSRF por DNS rebinding sobre `baseUrl` aceptado y documentado; pinned-IP connect fuera de alcance | Execution Notes (Riesgos residuales) + Architect Decisions #1 (sin tarea de código) |
| §2.2 | `assert_external_boundary()` y límites de bytes/timeout conservados | T9 (`searxng_web_search_provider`, DDG intacto; caída de red tipada ⇒ `research_blocked`; descarga de fuentes endurecida contra SSRF con redirects revalidados) |
| §2.2 | No-200 / página de desafío ⇒ `research_provider_blocked`, distinto de "sin fuentes" | T9 (backend) + T11 (tarjeta) |
| §2.2 | SearXNG Docker en `127.0.0.1:8888` con JSON; `docker pull` como gate de operador al ejecutar | T15 Step 1 (gate) + Step 2 (`docker pull` explícito) |
| §2.2 | Copy de research de intake sin la frase de decisiones de alto impacto | T11 |
| §2.3 | Cooldown 60 s → 5 min → 15 min por objetivo, reinicio con éxito, operador sin cooldown | T2 |
| §2.4 | `job.resource_wait` sólo al cambiar `reasonCode` | T3 |
| §2.4 | Evento de hilo `resource_wait` deduplicado y banner con motivo legible reutilizando el mapeo del modal | T3 + T12 |
| §2.5 | Botones de decisión con etiqueta i18n y descripción; valor enviado sin cambios | T10 |
| §2.6 | Registro se crea o confirma sólo con loop `delivered` o brief aprobado; coincidencias contra hilos sin ejecución ignoradas; inspección previa del registro | Verified Facts + T5 (alta/confirmación en `ensure_project_functionality`/`reindex_*`, filtro en `find_existing_functionality`, paso de verificación sobre copia) |
| §2.7 | Sin job activo (`open`/`resolved`/`archived`) no queda paso activo | T13 (`resolved` ya cerraba en `done`) |
| §2.8 | Hijos `running` sin lease fallados al recuperar/completar el padre (sólo con vínculo `parentJobId` comprobable; `workflow_run_id` verificado como id del product loop, no del padre), `parentJobId` al crear, `agent_runs` cerrados, limpieza única de `job-31a6b390` por id validado, con gate de operador y respaldo previo | T4 (`fail_orphaned_child_jobs`, `fail_legacy_orphan_child_jobs`) + T15 Step 3b (gate de operador al ejecutar; precondiciones `mode=ro` → respaldo `backup` + `integrity_check` → escritura) |
| §2.9 | `@author` en 6 módulos; 2 claves `en==es`; `KeyError 'details'` diagnosticado antes de corregir | T1 + T6 |
| §2.10 | Perfilado con cProfile sobre copia, ataque al costo dominante, meta < 1 s, sin recortar datos | Verified Facts + T7 + T15 |
| §3 | TDD por ítem; corpus parametrizado; SearXNG con servidor HTTP local de prueba; verificación final con el contenedor real | T1–T13 (paso 1 falla), T9, T15 |

Sin placeholders: todo símbolo usado está definido en una tarea o citado con `archivo:línea`. Tipos consistentes entre tareas: `payload.researchRemediation` (T9→T11), `metadata.questionKeys` (T8→T10), evento `resource_wait.payload.reasonCode` (T3→T12).

## Execution Notes

- **Orquestación:** tres carriles en worktrees separados (`superpowers:using-git-worktrees`), máximo 3 agentes simultáneos: A1 (T2→T3→T4), A2 (T5→T6→T7), B (T1→T8→T9→T10→T11→T12→T13). Integrar a `dev` carril por carril con `git fetch` + rebase antes de cada merge: otro agente autónomo commitea a `dev` en este checkout (memoria `aido-concurrent-autonomous-writer`); revisar también `dev` local antes de integrar. Sólo `dev` admite push (ruleset). T14 corre una vez integrados los tres carriles; T15 lo ejecuta el operador o un agente con su autorización explícita paso a paso: el orquestador pide la confirmación en el momento de cada gate (`docker pull`, reinicio de AIDO, escritura del zombi); la aprobación del plan no cuenta como confirmación de esos pasos.
- **Modelo por tarea:** HIGH = T4, T7, T8, T9 (modelo más capaz, esfuerzo alto); PATTERN = T2, T3, T5, T6, T10, T11, T12, T13 (modelo eficiente, esfuerzo medio); MECHANICAL = T1, T14 (modelo local/eficiente; el resultado se acepta sólo con la salida de los tests). Verificador independiente por hito: al cerrar T8/T9 (clasificador y borde de red) y antes de declarar T14 listo; nunca el mismo agente que implementó.
- **Decisiones tomadas en el plan (con criterio):**
  - Default de `research.webSearch.provider` = `searxng`: DuckDuckGo está verificado como bloqueado para el UA honesto; dejarlo por defecto perpetúa "no sources". Los tests que ejercitan DDG fijan el setting explícitamente.
  - `research.webSearch.baseUrl` sólo acepta loopback. El spec dice "loopback o allowlist"; no hay una allowlist de hosts de búsqueda definida y reutilizar `research.trustedDomains` (fuentes confiables) mezclaría semánticas. Extender a allowlist es un cambio posterior acotado a `validate_search_base_url`.
  - §2.6 aplica la regla del spec en alta y confirmación (`ensure_project_functionality`, `reindex_thread_memory`, `reindex_project_memory`) y en la consulta que bloquea (`find_existing_functionality`), sin borrar las 35 filas heredadas (se ignoran al consultar). `mark_similarity` sigue materializando por decisión explícita del operador; eso no es rediseño del registro (spec §4).
  - §2.10: caché de proceso (LRU 1024) en vez de proyección SQL, porque conserva la salida idéntica (la UI lee `testResults[].status`, `review/model.ts:171-177`). El primer poll tras reiniciar sigue costando ~2,8 s.
  - §2.9c: diverge el código, no el test (criterio en T6 paso 1).
- **Riesgos residuales:**
  - El ResearchAgent sigue siendo sólo web: preguntas sobre archivos locales (el mensaje original de la validación) se rutean bien pero no tienen respuesta útil por esa vía. Fuera de alcance del spec; conviene un ítem aparte (research sobre el repo).
  - La caché del overview asume inmutabilidad de `evidence_packages.test_results/logs/diff_refs/screenshot_refs` y de `test_results`; si en el futuro se agrega un `UPDATE` a esas columnas, hay que invalidar la entrada (documentado en el docstring de `IMMUTABLE_EVIDENCE_JSON_COLUMNS`).
  - El cooldown de salud vive en memoria del worker: un reinicio del worker permite una ráfaga inicial por objetivo.
  - Hijos zombi anteriores a `parentJobId` distintos de `job-31a6b390` no se limpian solos (no hay vínculo comprobable; `workflow_run_id` apunta al product loop). Verificado read-only 2026-09-22: `job-31a6b390` es el único job `running` sin lease en la BD viva; si aparecen otros, se sanean con `fail_legacy_orphan_child_jobs` por id tras inspección.
  - Guard SSRF de fuentes: el intervalo entre la resolución DNS de `assert_public_source_url` y la conexión de urllib permite DNS rebinding (TOCTOU); cerrarlo exige conectar por IP fijada con SNI, fuera del cambio mínimo. Un nombre que no resuelve se deja pasar (la descarga falla igual). Una fuente no pública en los resultados bloquea el run completo (fail-closed) en vez de saltarse esa fuente.
  - SSRF por DNS rebinding sobre `research.webSearch.baseUrl` (ACEPTADO, Architect Decisions #1): el default es el literal `127.0.0.1` (no resuelve DNS) y `validate_search_base_url` sigue en loopback/allowlist, pero un nombre (`localhost` o un host futuro de allowlist) se resuelve de nuevo al conectar. Pinned-IP connect queda fuera de alcance; el setting sólo lo escribe el operador local.
  - Imagen Docker de terceros: pin por digest recomendado tras la primera descarga (`docker inspect --format='{{index .RepoDigests 0}}' searxng/searxng`).
  - `test_local_worker_runtime.py` es sensible a carga (memoria `aido-known-preexisting-test-failures`); no correr suites Python y Playwright en paralelo en esta máquina.
- **Rama aparcada:** `fix/intake-spanish-research` queda superada por T8; proponer al operador su borrado local tras el merge (no borrar por iniciativa propia).

## Cross-Model Review Log

Revisión independiente (perfil Codex `analysis`, id de modelo no impreso). Cada ítem se verificó contra el código y la BD viva (sólo lectura) antes de decidir.

| # | Severidad | Ítem | Decisión | Motivo (una línea) |
|---|---|---|---|---|
| 1 | blocker → minor | Toolchain web ausente para T10–T14 | ACCEPTED (parcial) | La premisa es falsa aquí (`node` v24.16.0 vía symlink nvm `C:\Program Files\nodejs`, corepack 0.35.0, pnpm 10.24.0 y `node_modules/@playwright/test/cli.js` presentes en Bash y PowerShell), pero un worker de otro runtime puede no tenerlo: se agregó Preflight web con regla de detención (Global Constraints, T10 Step 0, T14 Step 4). |
| 2 | major | SearXNG caído no produce `research_blocked` | ACCEPTED | Verificado: `run()` sólo captura `ResearchAgentValidationError`/`ResearchPolicyError` (`research_agent.py:835`) y el proveedor lanzaba `ValueError` suelto; ahora `ResearchWebSearchError` (base de `ResearchProviderBlockedError`) se captura, con tests de puerto cerrado directo y a través del runner. |
| 3 | major | El backoff se reinicia tras fallos reales | ACCEPTED | Verificado: `record_health_check` escribe `lastHealthCheckAt` para cualquier desenlace (`provider_accounts.py:605-630`) y readiness exige `healthy` (`runtime_readiness.py:111-120`); el reinicio ahora exige `healthStatus == "healthy"` + marca vigente, con tests separados de unhealthy fresco y cancelación. |
| 4 | major | Recuperación por antigüedad y sin `workflow_run_id` | ACCEPTED (parcial) | Se eliminó la regla de 24 h y el zombi histórico se sanea por id validado con confirmación (T4 `fail_legacy_orphan_child_jobs`, T15 Step 3b); el enlace por `workflow_run_id` se rechaza con evidencia: el zombi lleva `product-loop-06816d8e…` (id del loop, único job con ese valor) y su padre real `job-fafc33ab…` tiene `workflow_run_id NULL`. |
| 5 | major | T5 no aplica la regla en alta/confirmación | ACCEPTED | El spec §2.6 dice "se crea o se confirma solo cuando…"; `ensure_project_functionality`/`reindex_*` (`similarity.py:212-255`) materializaban todo hilo `resolved|archived`: ahora exigen evidencia, con test de que un hilo sin ejecución ni crea ni actualiza y siembra en los 5+5 tests existentes. |
| 6 | major | SSRF en la descarga de fuentes | ACCEPTED | Verificado: `_fetch_url_text` (`research_agent.py:182-202`) sólo bloquea nombres loopback literales y sigue redirects, y con SearXNG por defecto ese camino queda activo; se agregó `assert_public_source_url` (A/AAAA, `is_global`, IPv4 mapeada) + `PublicRedirectHandler`, con casos 169.254.169.254, RFC 1918, CGNAT, ULA, DNS y redirect a loopback. |
| 7 | major | T15 deja SearXNG detenido | ACCEPTED | Verificado: el Step 3 hacía `docker stop` sin reinicio; ahora `try/finally` con `docker start`, `docker inspect` y smoke JSON + un research exitoso antes de cerrar. |
| 8 | minor | Nombre accesible desfasado en el banner | ACCEPTED | Verificado `aria-label` fijo en `ThreadExecutionPanel.tsx:289-292`; ahora `queuedCopy.label` ("Waiting for machine capacity" en espera de capacidad). El estado "Starting run" conserva la región "Waiting for worker" porque 5 specs la ubican así (`thread-pipeline.spec.js:70`, `research-resolution.spec.js:191`, `risk-review.spec.js:148`, `thread-lifecycle-e2e.spec.js:575,697`). |

Totales: 8 ACCEPTED (2 parciales), 0 REJECTED.

## Architect Decisions (2026-09-22)

1. **SSRF por DNS rebinding sobre `research.webSearch.baseUrl`: ACEPTADO y documentado.** Default loopback literal `http://127.0.0.1:8888`; `validate_search_base_url` mantiene loopback/allowlist; conectar por IP fijada queda fuera de alcance. Reflejado en spec §2.2, Execution Notes (Riesgos residuales) y Self-Review. Sin cambios de código en T9.
2. **Gates de operador en T15 al momento de ejecutar.** El `docker pull searxng/searxng` (Step 1 gate + Step 2 explícito) y la limpieza del zombi `job-31a6b390` en la BD viva (Step 3b) exigen confirmación explícita del operador, que pide el orquestador en ese momento; sin "sí" quedan pendientes. Step 3b ejecuta en orden: precondiciones `mode=ro` → respaldo con la API `backup` de sqlite3 + `integrity_check` → escritura. Reflejado en spec §2.2/§2.8, Task Graph, T15, Self-Review y Execution Notes.
