# Reconciliación del audit de limpieza contra HEAD — AIDO / Local Control Center

> **Propósito:** llevar `docs/cleanup/aggressive-cleanup-audit.md` (anclado en `d7d02ab`) al estado real de HEAD.
> **Base del audit:** `d7d02ab` · **HEAD:** `c1b117e` (rama `dev`) · **Distancia:** 23 commits · **Árbol:** limpio.
> **Fecha de reconciliación:** 2026-06-18 · **No se modificó código productivo** (solo documentación).
> **Acompaña a:** [current-baseline.md](current-baseline.md) (baseline reproducible verificable).

---

## Addendum 2026-08-02 — los 8 "borrados seguros pendientes" ya están ejecutados

Re-verificación contra HEAD actual de `dev` (posterior a `c1b117e`): los 8 items listados abajo
como **Borrado seguro pendiente** (§ Desglose y § 6) **ya no existen en HEAD**. Evidencia por item:

| Item | Verificación en HEAD |
|---|---|
| `FE-CORE-001/002/003` (CSS muerto) | Selectores ausentes; el propio `aggressive-cleanup-audit.md` los marca "✅ Resuelto (cleanup CSS, 2026-06-19)". |
| `FE-FEAT-B-001` (`features/memory/index.ts`) | El archivo no existe; la carpeta contiene solo `MemoryPage.tsx`. |
| `FE-CORE-005` (`createWorkflow` 3-arg) | 0 ocurrencias de `createWorkflow` a secas en `api/client.ts`; solo queda `createWorkflowWithBody` (tripwire conservado). |
| `BE-SLICES-B-001` (`execute_with_input`) | Ausente de `security_policy/sandbox.py`. |
| `DOCS-001` (`docs/HU/`) | La carpeta no existe. |
| `SCRIPTS-DEPS-001` (3 deps `@radix-ui`) | 0 ocurrencias en `package.json` y en `pnpm-lock.yaml`. |

Con esto, las **Fases 1, 2 y 3** del audit quedan completas. Los estados `still_open` de las
tablas siguientes se conservan como registro histórico de la reconciliación de 2026-06-18;
este addendum es la fuente vigente para esos 8 items.

---

## 1. Cómo se produjo esta reconciliación

- **Fan-out de verificación:** 11 agentes (uno por grupo de hallazgos) verificaron cada hallazgo **abriendo el archivo en HEAD** (`rg`/`git grep`/lectura directa), no infiriendo del diffstat.
- **Integración crítica (no delegada):** el líder re-verificó a mano los veredictos de mayor impacto con `git grep` sobre HEAD y `git show d7d02ab:…` sobre la base. Resultados confirmados 1:1 con los agentes (ver evidencia en cada fila).
- **Escala de estado:** `resolved` (la acción del audit ya está hecha) · `still_open` (la condición persiste igual) · `changed` (se tocó el código pero el hallazgo quedó parcial/movido) · `invalid` (el hallazgo era inexacto ya en la base).
- **Regla:** para hallazgos de *conservar* (seams/tripwires), `still_open` significa "el seam sigue intacto **como debe**" — es el estado correcto, no un defecto.

---

## 2. Resumen ejecutivo

| Estado | Conteo | Lectura |
| --- | ---: | --- |
| `resolved` | **8** | Borrados/dedup ya ejecutados (sobre todo backend + 2 frontend). |
| `changed` | **1** | `FE-APP-002`: duplicación reducida a medias; la extracción a `lib/format.ts` no se hizo. |
| `still_open` | **31** | Ver desglose por tipo abajo. |
| `invalid` | **1** | `BE-CORE-005`: el símbolo señalado nunca existió en `__init__.py`. |
| **Total** | **41** | Coincide con los 41 hallazgos del audit original. |

### Desglose de los 31 `still_open` por naturaleza

| Tipo | Conteo | IDs |
| --- | ---: | --- |
| **Seam / tripwire conservado (correcto — NO tocar)** | 5 | `FE-CORE-004`, `FE-CORE-006`, `BE-SLICES-B-004`, `BE-CORE-002`, `DOCS-005` |
| **Borrado seguro pendiente** | 8 | `FE-CORE-005`, `FE-CORE-001`, `FE-CORE-002`, `FE-CORE-003`, `FE-FEAT-B-001`, `BE-SLICES-B-001`, `DOCS-001`, `SCRIPTS-DEPS-001` |
| **Decisión de producto pendiente** | 3 | `BE-AGENTS-004`, `BE-SLICES-B-003`, `BE-CORE-001` |
| **Mejora pendiente (DRY / naming / perf / docs)** | 15 | `F2`, `F4`, `F5`, `F6`, `FE-FEAT-B-002`, `FE-FEAT-B-003`, `FE-FEAT-B-004`, `BE-SLICES-A-003`, `BE-SLICES-A-004`, `BE-SLICES-B-002`, `BE-SLICES-B-005`, `BE-CORE-003`, `BE-CORE-004`, `DOCS-002`, `TESTS-001` |

> **Conclusión:** la **Fase 1** del audit (borrados puros) se ejecutó parcialmente: cayeron los 4 backend (`BE-AGENTS-001/002/003`, `BE-SLICES-A-001`) + `BE-SLICES-A-002` (Fase 4), y en frontend `FE-APP-001` y `F3`; **siguen pendientes** los borrados de CSS (`FE-CORE-001/002/003`), `memory/index.ts` (`FE-FEAT-B-001`), `createWorkflow` (`FE-CORE-005`), `execute_with_input` (`BE-SLICES-B-001`), `docs/HU` (`DOCS-001`) y las 3 deps `@radix-ui` (`SCRIPTS-DEPS-001`). Los 5 seams/tripwires se conservaron correctamente.

---

## 3. Reconciliados como `resolved` (8)

| ID | Acción del audit | Estado en HEAD | Evidencia (re-verificada) |
| --- | --- | --- | --- |
| `FE-APP-001` | Borrar copia muerta `sumRecordedCost` en `App.tsx` | Hecho | `git grep sumRecordedCost -- app/App.tsx` → **0**. |
| `F1` | Borrar `utils.Metric`; deduplicar copias locales en `ModelGatewayPage` | Hecho (dedup inverso) | commit `efc4e85` borró las copias locales y el page importa `{ Metric, money, text } from './utils'`; `utils.Metric` pasó de muerto a **vivo** (~24 usos). La duplicación ya no existe. |
| `F3` | Borrar export `timelineTone` | Hecho | `git grep timelineTone -- web/src` → **0**. |
| `BE-AGENTS-001` | Borrar wrapper `resolve_credential` | Hecho | `git grep "def resolve_credential"` → **0**; API real `CredentialResolver().resolve()`. |
| `BE-AGENTS-002` | Borrar wrapper `validate_credential_ref` | Hecho | `git grep "def validate_credential_ref"` → **0**. |
| `BE-AGENTS-003` | Borrar método `QAAgentRunner.contract`; conservar `qa_agent_contract()` | Hecho | `git grep "def contract\b"` → **0**; la free-fn `qa_agent_contract()` sigue viva (3 callers). |
| `BE-SLICES-A-001` | Borrar `evidence_package_is_completion_grade` | Hecho | `git grep …` → **0** en `.py`; `evidence_package_contract_errors` permanece. |
| `BE-SLICES-A-002` | Borrar `run_process_pool` + `_process_worker_once`; recortar `__all__` | Hecho | `git grep run_process_pool` → **0**; shim `worker.py` exporta solo `ConcurrentWorker, execute_job`; tripwire `ConcurrentWorker` intacto. |

---

## 4. Reconciliado como `changed` (1)

| ID | Acción del audit | Qué cambió | Re-enunciado para HEAD |
| --- | --- | --- | --- |
| `FE-APP-002` | Extraer 1 `sumRecordedCost` a `lib/format.ts`; importarlo en StatusBar y ModelGateway; borrar copias App+pages | La extracción a `lib/format.ts` **NO** se hizo (`lib/format.ts` solo tiene `shortId`/`redactVisibleSecret`/`toneForStatus`). App.tsx ya no la tiene (ver `FE-APP-001`); StatusBar tampoco — su cálculo se movió inline a `deriveShellStatus` (`app/shellStatus.ts:34-44`). | Quedan **3** implementaciones equivalentes del sumatorio: `ModelGatewayPage.tsx:168` (viva, usada en :339), `features/pages.tsx:50` (**muerta**, sin call-site) y la versión inline en `shellStatus.ts:34-44`. Enunciado original del audit desactualizado (App/StatusBar ya no aplican). **Cierre:** extraer un único helper en `lib/format.ts`, importarlo en ModelGatewayPage y shellStatus, y borrar la copia muerta de `pages.tsx`. |

Evidencia re-verificada: `git grep sumRecordedCost -- web/src` → `ModelGatewayPage.tsx:168` (def) + `:339` (call viva) + `pages.tsx:50` (def, sin call).

---

## 5. Reconciliado como `invalid` (1)

| ID | Afirmación del audit | Refutación |
| --- | --- | --- |
| `BE-CORE-005` | `local_control_center/__init__.py · create_app` es un "tercer punto de re-export raíz" con 0 uso. | `git show d7d02ab:…/__init__.py` y `git show HEAD:…/__init__.py` son **byte-idénticos** y contienen solo `__all__ = ["__version__"]` + `__version__`. **`create_app` nunca estuvo en `__init__.py`** (ni en la base ni en HEAD). El re-export canónico vive en `app.py:6/8` y la definición en `api.py`. **Eliminar este ítem del backlog: no hay acción.** |

---

## 6. Reconciliados como `still_open` (31)

### 6.1 Seam / tripwire conservado — estado correcto, NO tocar (5)

| ID | Qué se conserva | Guardián (verificado en HEAD) |
| --- | --- | --- |
| `FE-CORE-004` | 9 wrappers `run*Agent`/`get*AgentStatus` en `api/client.ts` (L131-159, L459-485) | `test_ci_and_openapi_client.py:298-304` + `test_developer_agent_real_runtime.py:447`. |
| `FE-CORE-006` | `getProjectFiles` + `ProjectFileNode`/`ProjectFilesResponse` (`client.ts:177-203`) | Doc-comment de contrato forward; consumidor previsto `ExplorerPanel.tsx`. |
| `BE-SLICES-B-004` | `runtime_integrations/__init__.py` (paquete vacío) | `test_vertical_slices_architecture.py:161` (screaming architecture). |
| `BE-CORE-002` | `agents_runtime.py::GatedAgentsPlanner` (seam SDK no cableado) | `test_vertical_slices_architecture.py:62,71-72`. |
| `DOCS-005` | 4 docs `docs/aido-{implementation-plan,current-state-analysis,architecture-diagram,gap-analysis}.md` | `test_project_hygiene_and_license.py:33-37`. |

### 6.2 Borrado seguro pendiente (8)

| ID | Archivo · símbolo | Estado en HEAD | Nota de acción |
| --- | --- | --- | --- |
| `FE-CORE-005` | `api/client.ts · createWorkflow` (L344, 3-arg) | Presente, sin call-sites en `src` | Borrar **solo** `createWorkflow`; **conservar** `createWorkflowWithBody` (L351, pinneado por `test_web_rework_architecture.py:134` vía el substring `body: MutationBody<'create_workflow_api_v1_workflows_post'>`, no por nombre). |
| `FE-CORE-001` | `design-system/motion.css` · 4 selectores de card | Huérfanos en L6-9 y L47-50 (0 usos en TSX) | Quitarlos de ambos bloques; conservar `.card`. No pinneados por tests. |
| `FE-CORE-002` | `design-system/layout.css` · selectores pre-IDE-shell | Huérfanos intactos, conviven con hermanos vivos | Quitar **solo el huérfano de cada grupo** (cuidado con reglas compuestas y `@media`). Vivos: `.app-shell-ide`, `.explorer-panel`, `.activity-bar`, `.brand-orb`, `.nav-item`. |
| `FE-CORE-003` | `{components,layout,motion}.css` · 8 utilidades | Huérfanas, 0 usos como className | Quitar las 8; conservar vecinas vivas (`.tabs`, `.wizard-mode-toggle`, `.wizard-steps`, `.workbench-layout`, `.workbench-side`). `.panel-tab` singular ≠ `.tabs`. |
| `FE-FEAT-B-001` | `features/memory/index.ts` | Stub `export {};` (único contenido de la carpeta), 0 importadores | Borrar archivo + carpeta `features/memory/`. |
| `BE-SLICES-B-001` | `security_policy/sandbox.py · RestrictedSubprocessSandbox.execute_with_input` (L297-380) | ~84 líneas sin caller real | Borrar; los 2 guards negativos (`test_execution_boundary_architecture.py:105,478`) siguen verdes. |
| `DOCS-001` | `docs/HU/PIPE-0001-…md` | Stub fuera de dominio, único archivo en `docs/HU/`, 0 referencias | Borrar archivo + carpeta `docs/HU/`. |
| `SCRIPTS-DEPS-001` | `package.json` · `@radix-ui/react-{dialog,dropdown-menu,tooltip}` | Declaradas (L54-56) + lockeadas; **0 imports** en `src` (UI hand-rolled en `primitives.tsx`) | `pnpm remove` (regenera lockfile) y actualizar docs de inventario. |

### 6.3 Decisión de producto pendiente (3)

| ID | Archivo · símbolo | Estado en HEAD | Decisión requerida |
| --- | --- | --- | --- |
| `BE-AGENTS-004` | `agents/model_gateway.py · ModelGateway.prepare_model_call` (L518) | Sin caller de producto (solo 3 tests + 1 doc); path vivo `plan_model_call`+`execute_model_call` | ¿API pública vigente o legacy pre-router? Si legacy, retirar con sus 3 tests. |
| `BE-SLICES-B-003` | `integrations` · `list_integrations`/`IntegrationRecord` | Tabla `integrations` creada en migraciones (`migrations.py:220,826`) pero **0 INSERT** en todo el repo; `GET /api/v1/integrations` siempre `[]` | ¿Cablear writer/seed o retirar la rama vacía? |
| `BE-CORE-001` | `local_control_center/sandbox.py · WindowsSandbox` | Sandbox paralelo test-only (solo lo usa `test_python_control_center.py`); `run_low_risk()` siempre `PermissionError`; stack real en `security_policy/sandbox.py` | Confirmar dueño: ¿seam futuro o legacy a borrar (con su test)? |

### 6.4 Mejora pendiente — DRY / naming / perf / docs (15)

| ID | Archivo · tema | Estado en HEAD | Nota de acción |
| --- | --- | --- | --- |
| `F2` | `workbench · formatTime` (DRY) | Export vivo en `useWorkbenchData.ts:30`; 3 copias locales | Reemplazar copias en `WorkbenchExplorer.tsx:13`, `panels/LogsPanel.tsx:17`, `panels/TimelinePanel.tsx:19` por el helper. **Ruta actualizada:** Logs/Timeline ahora viven en `workbench/panels/`. |
| `F4` | `model-gateway/PanelShell.tsx` (duplicate component) | 11 paneles usan `PanelShell`; `ModelGatewayPage` lo bypassa con `<Surface>` directo (7 usos) | Unificar (no borrar). Sin commits desde la base. |
| `F5` | `workbench/useWorkbenchData.ts` (perf) | `teamRows`(:198), `deliveryRows`(:222), `runTimeline`(:231) sin memoizar | Envolver en `useMemo` con dependencias reales. |
| `F6` | `workbench/WorkbenchTabs.tsx · WorkbenchTabDef` | Export usado solo en su archivo | Quitar `export` (file-local); no borrar el tipo. |
| `FE-FEAT-B-002` | `review/model.ts` · 4 símbolos | `referenceIds`, `isSecurityFindingsArtifact`, `evidenceHasPassingQa`, `DONE_LIMIT` exportados, uso 100% interno | Privatizar (quitar `export`); no borrar. |
| `FE-FEAT-B-003` | `type Language='en'\|'es'` (DRY) | 2 defs (`active-projects` + `home`); la de HomePage es export que nadie importa | Centralizar en `i18n/` o, mínimo, quitar la copia de `HomePage.tsx:16`. **Corrección:** eran 2 defs, no 3 (settings ya importaba en la base). |
| `FE-FEAT-B-004` | `WorkflowTimeline` (naming collision) | 2 `WorkflowTimeline` + 2 `buildWorkflowTimeline` homónimos no relacionados | Renombrar los locales de `WorkflowsPage.tsx` (`:286`, `:368`). **Rutas actualizadas:** el par del workbench se movió a `workbench/WorkflowTimeline.tsx` + `workbench/timelineModel.ts`. |
| `BE-SLICES-A-003` | `ISSUE_TO_PR_APPROVAL_ACTION` (DRY) | Constante duplicada idéntica en `issue_to_patch_runner.py:52` + `issue_to_pr_runner.py:50` | Opcional: importar de una sola fuente. Bajo riesgo. |
| `BE-SLICES-A-004` | `evidence/quality.py · qa_passed_without_failed_results` (naming) | Alias de 1 línea de `evidence_has_real_qa_pass`; nombre engañoso; usado en `workflows/api.py:226` | Inline o renombrar a algo que refleje "real QA pass". |
| `BE-SLICES-B-002` | `workspaces_projects · IGNORED_PARTS`/`COPY_IGNORED_PARTS` (DRY) | Set idéntico de 10 entradas en `cleanup.py:13` + `repository.py:23` | Extraer 1 constante compartida. |
| `BE-SLICES-B-005` | `workspaces_projects` (naming) | Paquete sin rename; sin lógica duplicada entre dominios | Rename opcional a `workspaces` (coordinar con `expected_packages` del test de arquitectura). Cosmético. |
| `BE-CORE-003` | `control_plane · ensure_runtime_project` (DRY) | 2 impls (`runtime.py:52` con auditoría; `overview.py:29` sin auditoría) | Opcional: que overview delegue en runtime. **Ojo:** no son 100% equivalentes (overview omite `EventBus.record_audit`). |
| `BE-CORE-004` | `docs/backend.md` (obsolete docs) | L114/L297 afirman que `api.create_app()` es el "único composition path"; el canónico exigido por test es `app.create_app` | Actualizar a `local_control_center.app.create_app()`. |
| `DOCS-002` | `docs/api-contract-matrix.md` (obsolete docs) | Omite rutas vivas: model-gateway `benchmarks`/`benchmark-outcomes`/`pricing-snapshots` y workflow `gate-advance` | Conservar y actualizar (agregar las 4+ rutas). |
| `TESTS-001` | `tests_py · auth_headers`/header dict (DRY) | `auth_headers` duplicado en 21 archivos; `X-Local-Control-Token` 39×/27 archivos; sin `_helpers`/conftest compartido | Promover a `conftest.py`/`_helpers.py`. **Corrección:** `ControlPlaneFixture` **no** es boilerplate duplicado (ya centralizado en `tests_py/control_plane_fixture.py` desde la base); el header es `X-Local-Control-Token`, no `X-Local-Token`. |

---

## 7. Correcciones al audit original (precisión)

La reconciliación detectó imprecisiones en el enunciado del audit (no errores de veredicto, sino datos a corregir):

1. **`BE-CORE-005` (invalid):** `create_app` nunca estuvo en `__init__.py`. Retirar del backlog.
2. **`FE-FEAT-B-003`:** eran **2** definiciones de `Language`, no 3 — `settings` ya importaba de `active-projects` en la base.
3. **`TESTS-001`:** `ControlPlaneFixture` ya estaba centralizado en la base (no es duplicación); el header real es `X-Local-Control-Token` (el audit decía `X-Local-Token`, 34×; real 39×/27).
4. **Rutas movidas por refactors** (citas de línea del audit a actualizar): `LogsPanel`/`TimelinePanel` → `workbench/panels/`; el `WorkflowTimeline`/`buildWorkflowTimeline` del workbench → `workbench/WorkflowTimeline.tsx` + `timelineModel.ts`; varias citas de línea backend (p.ej. `credentials.py` ahora 545 líneas) ya no apuntan al símbolo borrado.

---

## 8. Estado del orden de limpieza por fases (§6 del audit)

| Fase del audit | Estado |
| --- | --- |
| **Fase 1** (borrados puros) | **Parcial:** hechos `DOCS-001`?→ **no**, `FE-FEAT-B-001`→ no, `FE-APP-001`→ **sí**, `F3`→ **sí**, `BE-AGENTS-001/002/003`→ **sí**, `BE-SLICES-A-001`→ **sí**, `BE-SLICES-B-001`→ no. (Backend completo; frontend a medias; `DOCS-001` pendiente.) |
| **Fase 2** (CSS muerto) | **Pendiente** (`FE-CORE-001/002/003`). |
| **Fase 3** (deps `@radix-ui`) | **Pendiente** (`SCRIPTS-DEPS-001`). |
| **Fase 4** (re-export con tripwire) | **Hecho** (`BE-SLICES-A-002`). |
| **Fase 5** (refactors DRY) | **Parcial:** `F1` hecho (vía dedup inverso); pendientes `F2`, `FE-APP-002`, `FE-CORE-005`. |
| **Fase 6** (decisiones de producto) | **Pendiente** (`FE-CORE-004` = conservar, `BE-AGENTS-004`, `BE-SLICES-B-003`, `BE-CORE-001`, `BE-CORE-005`=invalid). |
| **Fase 7** (mejoras sin borrado) | **Pendiente** (privatizar exports, naming, perf, DRY interno, docs). |

> Los tripwires de §5 del audit siguen vigentes y verificados en HEAD. Antes de cualquier borrado: re-grep del símbolo + `pnpm run quality` como gate maestro (ver baseline).
