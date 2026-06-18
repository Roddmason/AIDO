# Auditoría de limpieza agresiva — AIDO / Local Control Center

> ⚠️ **Documento anclado en la base `d7d02ab`.** Para el estado real contra HEAD (`c1b117e`, 23 commits después) ver **[audit-reconciliation-HEAD.md](audit-reconciliation-HEAD.md)** — reconciliación por hallazgo: 8 `resolved` · 1 `changed` · 31 `still_open` · 1 `invalid`. Baseline reproducible en **[current-baseline.md](current-baseline.md)**.

> **Estado:** auditoría únicamente. **No se ha borrado ni modificado código fuente.**
> **Fecha:** 2026-06-16 · **Rama:** `dev` · **Commit base:** `d7d02ab`
> **Alcance:** `local-control-center/web/src/**`, `local_control_center/**`, `tests_py/**`, `tests_web/**`, `docs/**`, `scripts/**`, `package.json`, `pyproject.toml`.

---

## 1. Resumen ejecutivo

Auditoría exhaustiva de los 369 archivos trackeados del repositorio, ejecutada con **11 agentes auditores por área** (las 12 clasificaciones pedidas) seguidos de **verificación adversarial por hallazgo de borrado** (un segundo agente intenta *refutar* la no-utilización buscando imports dinámicos, barrels, registros, tests, config y CSS). El líder técnico integró y, donde correspondió, **corrigió** la salida de los agentes contra la verdad de cableado del repo (`api.py`, `App.tsx`, tests de arquitectura).

| Métrica | Valor |
| --- | --- |
| Áreas auditadas | 11 |
| Hallazgos totales | 41 |
| Hallazgos que proponían borrado | 19 |
| **Confirmados seguros para borrar** (post-verificación) | **14** |
| **Refutados / re-encuadrados** (borrar tal cual rompería build o tests) | **5** |
| Hallazgos de mejora sin borrado (DRY, naming, perf, docs) | 22 |

**Conclusión clave para una limpieza *segura*:** este repositorio practica **"arquitectura como tests"**. Varios módulos *muertos en runtime* están **vivos porque un test de arquitectura los exige** (p.ej. `agents_runtime.py`, `runtime_integrations/`, los wrappers de cliente de agentes, `createWorkflowWithBody`). Además los headers JSDoc/PyDoc y cuatro documentos de iteración están pinneados por tests de higiene. **Borrar sin leer estos *tripwires* romperá `test:py`.** El gate maestro de validación para cualquier borrado es:

```
pnpm run quality   # typecheck:web + build:web + test:py (incluye gates de arquitectura) + test:web
```

Distribución por clasificación: `unused export` 15 · `duplicated logic` 9 · `dead CSS` 3 · `unused file` 3 · `naming issue` 3 · `obsolete docs` 3 · `broken flow` 2 · `duplicate component` 1 · `performance issue` 1 · `legacy UI` 1 · `over-commented code` 0 · `under-documented public contract` 0.

> Que **no haya** hallazgos de `over-commented code` ni `under-documented public contract` es un resultado positivo verificado: los comentarios son intencionales (el "porqué"), los banners de archivo son los headers exigidos por test, y los contratos públicos relevantes están documentados.

---

## 2. Metodología y criterio de evidencia

- **Finders por área** (no por dimensión) para que cada agente razone sobre referencias cruzadas dentro de su slice, pero con visibilidad de todo el repo vía grep.
- **Verificación adversarial**: cada hallazgo con `proposesRemoval=true` pasó por un verificador con la consigna de *refutar* la no-utilización. Por defecto marca `actually_used`/`uncertain` salvo que su propia búsqueda en todo el repo quede vacía.
- **Ground-truth leído por el líder** (no delegado): `api.py` (15 routers registrados), `App.tsx` (mapa página→componente), `test_vertical_slices_architecture.py`, `test_*_architecture.py` (tripwires).

**Escala de riesgo de borrado:** `low` (sin referencias en todo el repo, símbolo no dinámico) · `medium` (sin referencias pero superficie pública / re-export / posible consumidor futuro) · `high` (referencia real encontrada, o decisión de producto requerida).
**Veredicto de verificación:** `confirmed_unused` (refutación falló → seguro) · `actually_used` (se halló uso real → NO borrar tal cual) · `uncertain` (dinámico/ambiguo → revisión manual).

---

## 3. Veredicto sobre los 19 candidatos a borrado

### 3.A — Confirmados SEGUROS (14) · veredicto `confirmed_unused`, riesgo final `low`

| ID | Archivo | Qué borrar | Test requerido |
| --- | --- | --- | --- |
| FE-APP-001 | `web/src/app/App.tsx` | Función privada `sumRecordedCost` (~L44-47): definida, nunca invocada (copia muerta) | `typecheck:web` + `build:web` |
| F3 | `web/src/features/workbench/workbenchSelectors.ts` | Export `timelineTone` (L118-123): 0 referencias; duplica `toneForStatus` | `typecheck:web` |
| FE-FEAT-B-001 | `web/src/features/memory/index.ts` | Stub `export {};` sin importadores + carpeta `features/memory/` vacía | `typecheck:web` + `build:web` |
| FE-CORE-001 | `web/src/design-system/motion.css` | Selectores huérfanos `.workspace-card/.task-card/.run-card/.setting-card` (solo en `motion.css`) | `build:web` + `test:web` |
| FE-CORE-002 | `web/src/design-system/layout.css` | Selectores pre-IDE-shell (`.app-shell`, `.sidebar`, `.split-pane`, `.brand-mark/-title/-kicker`, `.nav-primary`, `.workspace-meta`, `.progress-current`) — **quirúrgico** | `build:web` + `test:web` |
| FE-CORE-003 | `web/src/design-system/{components,layout,motion}.css` | 8 utilidades huérfanas (`.panel-tab` singular, `.wizard-layout`, `.wizard-tech-strip`, `.home-hero-error`, `.settings-defaults-trigger`, `.workbench-actions/-progress-grid/-progress-signals`) — **quirúrgico** | `build:web` + `test:web` |
| BE-AGENTS-001 | `agents/credentials.py` | Wrapper `resolve_credential` (L547-548): 0 callers; usan `CredentialResolver().resolve()` | `test:py` |
| BE-AGENTS-002 | `agents/credentials.py` | Wrapper `validate_credential_ref` (L551-552): 0 callers | `test:py` |
| BE-AGENTS-003 | `agents/qa_agent.py` | Método `QAAgentRunner.contract` (L212-213): 0 callers; no es protocolo compartido | `test:py` |
| BE-SLICES-A-001 | `evidence/quality.py` | `evidence_package_is_completion_grade` (L167-177): 0 referencias; callers usan `evidence_package_contract_errors` | `test:py` |
| BE-SLICES-A-002 | `jobs_approvals/worker.py` + `worker.py` | `run_process_pool` + helper `_process_worker_once`; trim de `__all__` — **quirúrgico** (ver tripwire) | `test_vertical_slices_architecture.py` + `test:py` |
| BE-SLICES-B-001 | `security_policy/sandbox.py` | `RestrictedSubprocessSandbox.execute_with_input` (L297-380): 0 callers reales | `test_execution_boundary_architecture.py` + `test:py` |
| DOCS-001 | `docs/HU/PIPE-0001-...md` | Stub HU fuera de dominio (Chile chilecompra/mercadopublico) + carpeta `docs/HU/` | `test:py` |
| SCRIPTS-DEPS-001 | `package.json` | 3 deps `@radix-ui/react-{dialog,dropdown-menu,tooltip}`: declaradas pero 0 imports (UI es hand-rolled) — usar `pnpm remove` | `typecheck:web` + `build:web` + `test:web` |

### 3.B — REFUTADOS / re-encuadrados (5) · borrar tal cual rompe build o `test:py`

La verificación adversarial **interceptó 5 propuestas de borrado peligrosas**. Esto es el principal valor de seguridad de la auditoría.

| ID | Propuesta original | Por qué es peligrosa (evidencia) | Acción correcta |
| --- | --- | --- | --- |
| FE-APP-002 | Borrar `sumRecordedCost` (DRY en 4 archivos) | `StatusBar.tsx:34` y `ModelGatewayPage.tsx:349` **lo llaman vivo** (StatusBar montado vía `AppShell`). Borrarlo rompe build/UI. | **Refactor**, no borrado: extraer 1 helper a `lib/format.ts`, importarlo en StatusBar y ModelGateway, borrar solo las 2 copias muertas (App.tsx, pages.tsx). |
| FE-CORE-004 | Borrar 9 wrappers `run*Agent`/`get*AgentStatus` en `api/client.ts` | **Pinneados por 2 tests Python no-skip**: `test_ci_and_openapi_client.py:298-304` y `test_developer_agent_real_runtime.py:447` asertan los nombres/operation-IDs en el texto de `client.ts`. Borrar rompe `test:py`. | **Conservar** (o, si se retiran, editar ambos tests + endpoints backend en el mismo cambio). |
| FE-CORE-005 | Borrar `createWorkflow` + `createWorkflowWithBody` | `createWorkflowWithBody` está pinneado por `test_web_rework_architecture.py:134` (asercion de firma sobre el texto de `client.ts`). | Borrar **solo** `createWorkflow` (3-arg, L344, sin guard). `createWorkflowWithBody` se conserva. |
| F1 | Borrar `text/money/Metric` de `model-gateway` | `text` y `money` de `utils.tsx` los importan **11 paneles** (9 y 7 resp.). Borrarlos rompe build. | Solo `utils.tsx::Metric` (L50-57) está muerto. Refactor: borrar copias locales de `text/money/Metric` en `ModelGatewayPage.tsx` e importar de `./utils`; borrar `Metric` de utils. |
| F2 | Borrar export `formatTime` (DRY) | El export `useWorkbenchData.ts::formatTime` lo importa y usa `WorkbenchPage.tsx:36/291` + uso interno L217. El hallazgo está **invertido**. | Mantener el export; deduplicar las **3 copias locales** (WorkbenchExplorer L13, LogsPanel L17, TimelinePanel L19) importando el canónico (idealmente moverlo a `lib/format.ts`). |

---

## 4. Hallazgos por clasificación (las 12)

> Riesgo mostrado = **riesgo final post-verificación**. "Veredicto" = resultado de la verificación adversarial.

### 4.1 `unused file` (3)

| ID | Archivo | Evidencia | Riesgo | Acción | Test | Veredicto |
| --- | --- | --- | --- | --- | --- | --- |
| FE-FEAT-B-001 | `web/src/features/memory/index.ts` | Solo banner + `export {};`; 0 importadores; `MemoryPage` vive en `pages.tsx:305` y se cablea en `App.tsx`. | low | Borrar archivo + carpeta vacía. | `typecheck:web`/`build:web` | `confirmed_unused` |
| DOCS-001 | `docs/HU/PIPE-0001-...md` | Stub fuera de dominio (procurement Chile), único archivo en `docs/HU/`, 0 referencias; sembrado en commit `20ae6dc`. | low | Borrar archivo + carpeta `docs/HU/`. | `test:py` | `confirmed_unused` |
| BE-SLICES-B-004 | `runtime_integrations/__init__.py` | Paquete vacío (`__all__ = []`). **Tripwire:** `test_vertical_slices_architecture.py:161` exige que exista (screaming architecture). | **high** | **NO borrar.** Es un placeholder de dominio reservado, exigido por test. | `test_vertical_slices_architecture.py` | — (no-borrado) |

### 4.2 `unused export` (15)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| F3 | `workbench/workbenchSelectors.ts` · `timelineTone` | 0 referencias; el timeline usa `data-status` CSS + `toneForStatus`. | low | Borrar (L118-123). | `confirmed_unused` |
| BE-AGENTS-001 | `agents/credentials.py` · `resolve_credential` | Wrapper de módulo, 0 callers; usan la clase. | low | Borrar (L547-548). | `confirmed_unused` |
| BE-AGENTS-002 | `agents/credentials.py` · `validate_credential_ref` | Wrapper de módulo, 0 callers. | low | Borrar (L551-552). | `confirmed_unused` |
| BE-AGENTS-003 | `agents/qa_agent.py` · `QAAgentRunner.contract` | Método sin callers; no es protocolo compartido (único `def contract` del repo). | low | Borrar (L212-213); conservar la free-fn `qa_agent_contract()`. | `confirmed_unused` |
| BE-SLICES-A-001 | `evidence/quality.py` · `evidence_package_is_completion_grade` | Wrapper bool, 0 referencias; callers usan `evidence_package_contract_errors`. | low | Borrar (L167-177). | `confirmed_unused` |
| BE-SLICES-A-002 | `jobs_approvals/worker.py` · `run_process_pool` (+`_process_worker_once`) | Variante ProcessPool nunca invocada; path vivo es `ConcurrentWorker.run_batch`. **Tocar `__all__`** en `worker.py`. | low | Borrar ambas; trim `worker.py` import+`__all__` a `ConcurrentWorker, execute_job`. | `confirmed_unused` |
| BE-SLICES-B-001 | `security_policy/sandbox.py` · `RestrictedSubprocessSandbox.execute_with_input` | ~85 líneas sin caller; única referencia = 2 *guards negativos* en `test_execution_boundary_architecture.py`. | low | Borrar (L297-380); los guards siguen verdes. | `confirmed_unused` |
| SCRIPTS-DEPS-001 | `package.json` · 3 `@radix-ui/*` | Declaradas+lockeadas, 0 imports; Dialog/Modal/Tooltip son hand-rolled en `primitives.tsx`. | low | `pnpm remove` (regenera lockfile). | `confirmed_unused` |
| FE-CORE-004 | `api/client.ts` · 9 wrappers `run*Agent`/`get*AgentStatus` | **Pinneados por 2 tests Python.** | **high** | **Conservar** (o retirar con sus tests). | `actually_used` |
| FE-CORE-005 | `api/client.ts` · `createWorkflow`, `createWorkflowWithBody` | `createWorkflowWithBody` pinneado por `test_web_rework_architecture.py:134`. | **high** | Borrar solo `createWorkflow` (3-arg). | `actually_used` |
| FE-CORE-006 | `api/client.ts` · `getProjectFiles`, `ProjectFileNode`, `ProjectFilesResponse` | Seam de contrato **documentado** para endpoint futuro `/projects/{id}/files`. | **high** | **Conservar** (no es código muerto accidental). | — (no-borrado) |
| F6 | `workbench/WorkbenchTabs.tsx` · `WorkbenchTabDef` | Export usado solo en su archivo (L20); tabs se construyen inline. | low | Quitar `export` (hacerlo file-local), no borrar el tipo. | — (no-borrado) |
| FE-FEAT-B-002 | `review/model.ts` · `referenceIds`, `isSecurityFindingsArtifact`, `evidenceHasPassingQa`, `DONE_LIMIT` | 4 símbolos con `export` usados solo dentro de `model.ts`. | low | Quitar `export` (privatizar); **no borrar** las funciones. | — (no-borrado) |
| BE-CORE-005 | `local_control_center/__init__.py` · `create_app` | Tercer punto de exposición del mismo símbolo; 0 uso por raíz de paquete. | medium | Opcional: quitar el re-export raíz; verificar que ningún entrypoint externo lo use. | — (no-borrado) |
| BE-AGENTS-004 | `agents/model_gateway.py` · `ModelGateway.prepare_model_call` | Sin caller de producto; usado solo por 3 tests + 1 doc. Path vivo: `plan_model_call`+`execute_model_call`. | medium | Decisión de producto: ¿API pública vigente o legacy pre-router? Retirar **con** sus tests si es legacy. | — (no-borrado) |

### 4.3 `dead CSS` (3)

| ID | Archivo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| FE-CORE-001 | `design-system/motion.css` | `.workspace-card/.task-card/.run-card/.setting-card`: 0 usos en TSX. **Corrección del verificador:** *no* están en `components.css` (allí están `.card`/`.workspace-mode-card`/`.detection-card`, vivos); solo existen como selectores huérfanos en `motion.css` (L6-9, 47-50). | low | Quitarlos de las listas de transición/reduced-motion de `motion.css`; conservar `.card`. | `confirmed_unused` |
| FE-CORE-002 | `design-system/layout.css` | Selectores pre-IDE-shell huérfanos; el shell vivo usa `.app-shell-ide`/`.explorer-panel`/`.nav-section-label`/`.brand-wordmark`. **Caveat:** varios están agrupados con selectores vivos → quitar **solo el huérfano** de cada grupo, no el bloque. | low | Edición quirúrgica (ver detalle en finding). No tocar `.main-area`/`.console-grid` (asertados por test dark-surfaces). | `confirmed_unused` |
| FE-CORE-003 | `design-system/{components,layout,motion}.css` | 8 utilidades huérfanas; `.panel-tab` singular vs `.panel-tabs` (plural, vivo). Algunas comparten grupo con selectores vivos. | low | Quitar solo los 8 huérfanos de sus grupos; re-grep cada token justo antes de borrar. | `confirmed_unused` |

### 4.4 `duplicated logic` (9)

| ID | Archivos · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| FE-APP-001 | `app/App.tsx` · `sumRecordedCost` | Copia muerta en App (nunca llamada). | low | **Borrar** la copia. | `confirmed_unused` |
| FE-APP-002 | App/StatusBar/pages/ModelGateway · `sumRecordedCost` | Misma función en 4 archivos; 2 vivas. | high (como borrado) | **Refactor** a `lib/format.ts`; borrar solo copias App+pages. | `actually_used` |
| F1 | `model-gateway/{ModelGatewayPage,utils}.tsx` · `text/money/Metric` | `text/money` de utils vivos (11 paneles); solo `utils.Metric` muerto. | high (como borrado) | Borrar `utils.Metric` + deduplicar locales del page. | `uncertain` |
| F2 | `workbench/*` · `formatTime` | Export vivo (WorkbenchPage); 3 copias locales son la duplicación. | high (como borrado) | Mantener export; deduplicar 3 copias. | `actually_used` |
| FE-FEAT-B-003 | home/active-projects/settings · `Language` | `export type Language='en'|'es'` duplicado; copia de HomePage sin consumidores externos. | low | Definir 1 vez en `i18n`/`lib`; al menos quitar `export` de la copia de HomePage. | — (no-borrado) |
| BE-SLICES-A-003 | `workflows/issue_to_{patch,pr}_runner.py` · `ISSUE_TO_PR_APPROVAL_ACTION` | Constante string duplicada (mismo valor) en 2 runners. | n/a | Opcional: importar la constante desde `issue_to_patch_runner` (1 fuente). Los helpers `_developer_instruction_for_issue` difieren legítimamente → dejar. | — (no-borrado) |
| BE-SLICES-B-002 | `workspaces_projects/{cleanup,repository}.py` · `IGNORED_PARTS`/`COPY_IGNORED_PARTS` | Set idéntico de 10 entradas en 2 módulos del mismo slice; riesgo de drift snapshot vs copia. | low | Extraer 1 constante `WORKSPACE_IGNORED_PARTS` e importar. | — (no-borrado) |
| BE-CORE-003 | `control_plane/{runtime,overview}.py` · `ensure_runtime_project` | 2 implementaciones casi idénticas de provisión de proyecto runtime. | n/a | Opcional: que `overview.ensure_runtime_project` delegue en la capa runtime. Baja prioridad (ambos callers vivos). | — (no-borrado) |
| TESTS-001 | `tests_py/*` · `auth_headers`/`make_app`/`ControlPlaneFixture(...)` | Boilerplate de harness copiado: `auth_headers` en 21 archivos, fixture en 23, header dict 34 veces/24 archivos. `conftest.py` ya existe como hogar natural. | n/a | Promover a `conftest.py`/`_helpers.py`; **no es** la repetición per-slice protegida. Revisión manual por amplitud. | — (no-borrado) |

### 4.5 `duplicate component` (1)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| F4 | `model-gateway/PanelShell.tsx` | Wrapper de `Surface` usado por 11 paneles pero **bypassado** por `ModelGatewayPage` (L590/609/738/801/862/873/881) que usa `Surface` directo. | medium | Unificar: que el page también use `PanelShell`, o inline el wrapper. No borrar (11 consumidores vivos). | — (no-borrado) |

### 4.6 `performance issue` (1)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| F5 | `workbench/useWorkbenchData.ts` | `teamRows` (L198), `deliveryRows` (L222), `runTimeline` (L231) sin memoizar → recomputan en cada keystroke del composer. | n/a | Envolver en `useMemo` con dependencias correctas. | — (no-borrado) |

### 4.7 `naming issue` (3)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| FE-FEAT-B-004 | `workflows/WorkflowsPage.tsx` + `workbench/WorkflowTimeline.tsx` · `WorkflowTimeline` | Dos componentes **no relacionados** con el mismo nombre + dos `buildWorkflowTimeline`. Separación de slices legítima, pero ambigua para grep. | n/a | Renombrar el local de WorkflowsPage a `WorkflowRunTimeline`/`buildWorkflowRunTimeline`. | — (no-borrado) |
| BE-SLICES-A-004 | `evidence/quality.py` · `qa_passed_without_failed_results` | Alias de 1 línea de `evidence_has_real_qa_pass`; el nombre implica un chequeo más débil del que ejecuta (gate `pr_review`). | n/a | Renombrar a algo que refleje "real QA pass", o inline en `workflows/api.py:226`. | — (no-borrado) |
| BE-SLICES-B-005 | `workspaces_projects/` | El slice se llama `workspaces_projects` pero su dominio es exclusivamente workspaces efímeros; el catálogo vive en `projects`. **Sin** lógica duplicada, solo nombre confuso. | n/a | Rename opcional a `workspaces` (coordinar con `expected_packages` del test de arquitectura). Diferir salvo rename mayor. | — (no-borrado) |

### 4.8 `broken flow` (2)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| BE-SLICES-B-003 | `integrations/{api,repository,models}.py` · `list_integrations`/`IntegrationRecord` | `GET /api/v1/integrations` lee la tabla `integrations`, **creada en migraciones pero nunca poblada** (0 INSERT en todo el repo). El endpoint siempre devuelve `[]`. (Las tablas vivas son `mcp_servers`/`ide_connections`.) | high | Decisión de producto: cablear un writer/seed si está en roadmap, o retirar la rama vacía `integrations` (+`IntegrationRecord`/`row_to_integration`) si no. El frontend puede consumir la forma. | — (no-borrado) |
| BE-CORE-002 | `agents_runtime.py` · `GatedAgentsPlanner` | Gate del Agents SDK **no cableado** a ningún router/runtime. **Tripwire:** `test_vertical_slices_architecture.py:62,71-72` lee el archivo y exige `JobsRepository` / prohíbe `ControlPlaneFixture`. | high | **NO borrar.** Tarea "cablear o documentar": wirearlo a un job, o dejar nota de seam intencional. | — (no-borrado) |

### 4.9 `legacy UI` (1)

| ID | Archivo · símbolo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| BE-CORE-001 | `local_control_center/sandbox.py` · `WindowsSandbox` | Sandbox paralelo huérfano: ningún módulo de producto lo importa (el vivo es `security_policy/sandbox.py` → `DockerSandbox`/`RestrictedSubprocessSandbox`). Solo lo usa `test_python_control_center.py` (L17, L666). `run_low_risk()` siempre lanza `PermissionError` (placeholder). | medium | **Producto-muerto / test-only.** Confirmar con el dueño si es seam futuro de sandbox Windows-nativo; si no, borrar `sandbox.py` **y** su bloque de test (L17, ~660-712) en el mismo cambio (borrar solo el módulo rompe el import del test). | — (no-borrado) |

### 4.10 `obsolete docs` (3)

| ID | Archivo | Evidencia | Riesgo | Acción | Veredicto |
| --- | --- | --- | --- | --- | --- |
| BE-CORE-004 | `docs/backend.md` | L114 y L297 dicen que `local_control_center.api.create_app()` "is the only composition path". El path canónico **exigido por test** es `app.create_app` (`test_vertical_slices_architecture.py:145,148`). El doc apunta a la capa no-canónica. | n/a | Actualizar a `local_control_center.app.create_app()` (api.py es solo el sitio de definición). | — (no-borrado) |
| DOCS-002 | `docs/api-contract-matrix.md` | Omite rutas presentes en código: model-gateway benchmarks/benchmark-outcomes/pricing-snapshots y workflow gate-advance. | n/a | **Conservar y actualizar** (lo referencia `architecture-audit.md:63`). | — (no-borrado) |
| DOCS-005 | `docs/aido-{implementation-plan,current-state-analysis,architecture-diagram,gap-analysis}.md` | 4 docs de iteración "terminada". **Tripwire:** `test_project_hygiene_and_license.py:33-37` los lee y aserta su contenido (metadata_json, "Diagrama 1-4", headings de gaps, "No reconstruir el gateway"). | high | **Conservar.** Retirar solo editando el test en el mismo cambio. | — (no-borrado) |

### 4.11 `over-commented code` (0) · 4.12 `under-documented public contract` (0)

Sin hallazgos. Verificado: comentarios intencionales, banners exigidos por test, contratos públicos documentados.

---

## 5. Lista explícita de "NO TOCAR" (seams intencionales y *tripwires*)

Para que una futura auditoría/limpieza no confunda esto con código muerto:

| Elemento | Por qué se conserva | Guardián |
| --- | --- | --- |
| `getProjectFiles` + tipos (FE-CORE-006) | Contrato forward documentado para endpoint futuro | docstring en `client.ts` |
| `runtime_integrations/__init__.py` (BE-SLICES-B-004) | Placeholder de dominio reservado | `test_vertical_slices_architecture.py:161` |
| `agents_runtime.py::GatedAgentsPlanner` (BE-CORE-002) | Seam SDK no-cableado, intencional | `test_vertical_slices_architecture.py:62,71-72` |
| 9 wrappers de agentes en `client.ts` (FE-CORE-004) | Contrato OpenAPI pinneado | `test_ci_and_openapi_client.py`, `test_developer_agent_real_runtime.py` |
| `createWorkflowWithBody` (FE-CORE-005) | Firma pinneada | `test_web_rework_architecture.py:134` |
| `worker.py` / `agents_runtime.py` (fachadas) | Re-exports exigidos | `test_vertical_slices_architecture.py:55,62` |
| `app.create_app` como path público | Facade canónica | `test_vertical_slices_architecture.py:145,148` |
| 4 docs `aido-*` + `architecture-audit.md` (DOCS-005) | Contenido asertado | `test_project_hygiene_and_license.py` |
| Banners `@file/@copyright/@author` | Headers exigidos | `test_source_documentation_headers.py` |
| Repetición per-slice `api/models/repository` | Patrón de arquitectura intencional | tests de arquitectura |
| `WindowsSandbox` (BE-CORE-001) | Test-only; requiere confirmación del dueño antes de retirar | `test_python_control_center.py` |

---

## 6. Orden seguro de limpieza (por fases, menor riesgo primero)

> **Antes de cada fase:** crear rama desde `dev`. **Después de cada fase:** `pnpm run quality` (gate maestro) y commit por funcionalidad. Re-grep de cada símbolo corto inmediatamente antes de borrar.

### Fase 1 — Borrados puros sin tocar tests ni gates *(riesgo mínimo)*
Símbolos privados/exports con 0 referencias en todo el repo y sin tripwire. Cada uno es un no-op de runtime.
1. `DOCS-001` — borrar `docs/HU/PIPE-0001-...md` + carpeta `docs/HU/`.
2. `FE-FEAT-B-001` — borrar `web/src/features/memory/index.ts` + carpeta.
3. `FE-APP-001` — borrar copia muerta `sumRecordedCost` en `App.tsx`.
4. `F3` — borrar `timelineTone` en `workbenchSelectors.ts`.
5. `BE-AGENTS-001`, `BE-AGENTS-002` — borrar wrappers `resolve_credential`/`validate_credential_ref`.
6. `BE-AGENTS-003` — borrar `QAAgentRunner.contract`.
7. `BE-SLICES-A-001` — borrar `evidence_package_is_completion_grade`.
8. `BE-SLICES-B-001` — borrar `RestrictedSubprocessSandbox.execute_with_input`.

**Gate:** `pnpm run quality`.

### Fase 2 — CSS muerto (quirúrgico) *(riesgo bajo, validación visual)*
9. `FE-CORE-001` — limpiar `motion.css` (card taxonomy huérfana).
10. `FE-CORE-002` — limpiar `layout.css` (pre-IDE-shell), **solo el huérfano de cada grupo**.
11. `FE-CORE-003` — limpiar 8 utilidades huérfanas en `{components,layout,motion}.css`.

**Gate:** `build:web` + `test:web` (specs renderizan cards/workbench/inspector/tabs) + verificación visual del shell.

### Fase 3 — Dependencias *(riesgo bajo)*
12. `SCRIPTS-DEPS-001` — `pnpm remove @radix-ui/react-dialog @radix-ui/react-dropdown-menu @radix-ui/react-tooltip` (regenera lockfile). Actualizar las 3 docs de inventario (`architecture-audit.md`, `license-audit.md`, `model-runtime-gap-audit.md`).

**Gate:** `pnpm run quality`.

### Fase 4 — Re-export con tripwire *(riesgo bajo, tocar `__all__`)*
13. `BE-SLICES-A-002` — borrar `run_process_pool` + `_process_worker_once`; trim import/`__all__` de `worker.py` a `ConcurrentWorker, execute_job`.

**Gate:** `test_vertical_slices_architecture.py` + `test_python_control_center.py` + `test:py`.

### Fase 5 — Refactors DRY que tocan código vivo *(riesgo medio)*
14. `F1` — borrar `utils.Metric`; deduplicar `text/money/Metric` locales de `ModelGatewayPage` importando de `./utils`.
15. `F2` — deduplicar las 3 copias de `formatTime`; **mantener** el export (idealmente moverlo a `lib/format.ts`).
16. `FE-APP-002` — extraer 1 `sumRecordedCost` a `lib/format.ts`; importarlo en StatusBar y ModelGateway; borrar copia de `pages.tsx` (la de App ya cae en Fase 1).
17. `FE-CORE-005` — borrar solo `createWorkflow` (3-arg); conservar `createWorkflowWithBody`.

**Gate:** `pnpm run quality` (incluye `test:web` por estar cableado al shell).

### Fase 6 — Decisiones de producto (requieren confirmación del dueño antes de actuar)
- `FE-CORE-004` — ¿retirar los 9 wrappers de agentes? (editar 2 tests + endpoints).
- `BE-AGENTS-004` — ¿`prepare_model_call` es API pública o legacy pre-router?
- `BE-SLICES-B-003` — ¿cablear writer de `integrations` o retirar la rama vacía?
- `BE-CORE-001` — ¿`WindowsSandbox` es seam futuro o legacy a borrar (con su test)?
- `BE-CORE-005` — ¿retirar el re-export raíz `create_app`?

### Fase 7 — Mejoras sin borrado (cuando se toquen esos archivos)
- Privatizar exports: `F6` (`WorkbenchTabDef`), `FE-FEAT-B-002` (4 símbolos de `model.ts`).
- Naming: `FE-FEAT-B-004`, `BE-SLICES-A-004`, `BE-SLICES-B-005`.
- Performance: `F5` (memoizar en `useWorkbenchData`).
- DRY interno: `BE-SLICES-B-002`, `BE-SLICES-A-003`, `BE-CORE-003`, `TESTS-001`, `FE-FEAT-B-003`.
- Docs: `BE-CORE-004`, `DOCS-002`.
- Refactor opcional `F4` (unificar `PanelShell` en `ModelGatewayPage`).

---

## 7. Recomendaciones globales (deuda transversal)

1. **Activar `noUnusedLocals` y `noUnusedParameters`** en `web/tsconfig.json`. Habrían atrapado automáticamente FE-APP-001, F3 y varios exports muertos; hoy `tsc` no los detecta.
2. **Añadir `knip` o `ts-prune`** al pipeline web para detección continua de exports/archivos muertos (el repo no tiene ninguno hoy).
3. **Helper de costo compartido**: extraer un único `sumRecordedCost`/`totalRecordedCostUsd` a `lib/format.ts` elimina 4 copias (raíz de FE-APP-001/002).
4. **Documentar los *tripwires***: el patrón "arquitectura como aserciones de texto" es potente pero acopla docs/símbolos a tests. Conviene un mapa "borrar X requiere editar test Y" (esta auditoría lo provee en §5) para que la limpieza no rompa `test:py`.
5. **`.tmp/` acumula artefactos de runtime** (sqlite de Playwright, screenshots, ~95 clones de `tests_py/conftest.py` en workspaces). Está gitignored y fuera de colección de pytest, pero conviene una limpieza periódica de disco (no es deuda de código).

---

## 8. Validación de esta sesión

- **Entregable:** este documento (`docs/cleanup/aggressive-cleanup-audit.md`). No se modificó código fuente.
- **Seguridad del entregable verificada:** `test_project_hygiene_and_license.py` lee docs por nombre específico (no enumera `docs/`); `test_source_documentation_headers.py` solo escanea `.ts/.tsx/.py`. Un `.md` nuevo bajo `docs/cleanup/` no rompe ningún gate.
- **Tests ejecutados al cierre:** ver el informe final de la sesión.

> **Próximo paso recomendado:** ejecutar la **Fase 1** en una rama dedicada (`chore/cleanup-phase-1`), validar con `pnpm run quality`, y avanzar fase por fase. Las Fases 1-4 son borrado seguro; la Fase 6 requiere tu confirmación explícita.
