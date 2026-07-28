# AIDO como proceso spec-driven — Constitución · Especificación · Clarificación · Plan · Tareas · Implementación

**Fecha:** 2026-07-28
**Estado:** Diseño propuesto (pendiente de aprobación). Implementación por slices.
**Autor:** Rodrigo Mason (diseño asistido)
**Inspiración:** [github/spec-kit](https://github.com/github/spec-kit) — `constitution → specify → clarify → plan → tasks → analyze → implement`

## 1. Objetivo

Que **todo proceso de AIDO** siga un ciclo spec-driven explícito: reglas del proyecto primero, luego el QUÉ, luego las dudas resueltas, luego el CÓMO, luego las tareas, y recién ahí la implementación con el equipo completo (developer, QA, security, technical lead, arquitecto, devops).

## 2. Hallazgo central

**AIDO ya tiene construido cerca del 70% de este proceso, pero desconectado.** El recorrido completo es —salvo tres aristas— un camino legal por la FSM actual de 24 estados. El trabajo no es inventar una máquina nueva: es cablear lo que existe, corregir lo que está mal conectado, y construir las dos piezas que genuinamente faltan (Constitución y un autor de Plan).

### 2.1 Verificado en código

| Hallazgo | Evidencia |
|---|---|
| `architecture_review` y `quality_review` no tienen ningún productor en código productivo | Barrido repo-wide de `to_state=`/`toState:`; solo alcanzables por el endpoint genérico `POST .../transition`, cuyo body no restringe destino (`ProductLoopTransitionRequest.to_state: str`, sin `Literal`) |
| `discovering` **sí** tiene productor, fuera del coordinator | `workflows/api.py:383`, en el arranque de `idea_to_pr`. El coordinator usa `discovery` (`coordinator.py:4952`): dos entradas de descubrimiento divergentes según quién arranque el loop |
| `iteration_planning` **sí** tiene dos productores | `product_loop/api.py:398` (aprobación de backlog) y `WorkbenchPage.tsx:445` ("Start iteration") |
| **Bug latente:** `iteration_planning → branch_ready` no existe | `ALLOWED_TRANSITIONS` (`coordinator.py:150-152`): `iteration_planning → {executing}`. Aprobar el backlog hoy deja el loop en un estado desde el cual `_prepare_developer_execution` no puede asignar worktree → `ProductLoopTransitionError` |
| El `ArchitectAgent` es un **revisor post-diff**, no un planificador | `architect_agent_contract.py:41` exige `diffArtifactId`; `outputSchema` = verdict/architectureFindings/risks/requiredChanges — sin stack, contratos ni migraciones. `architect_agent.py:383-388` rechaza artifacts cuyo kind no sea `{git_patch, git_diff, diff}` |
| El `DevOpsAgent` no es una llamada de modelo: es ejecución de shell | `devops_agent_contract.py:15` → `allowedTools = ["shell"]`; corre `buildScripts`/`qualityScripts`. En `issue_to_pr_runner.py:757-766` es gate **bloqueante** |
| Ambos agentes existen y el product loop nunca los invoca | Único orquestador que los usa: `workflows/issue_to_pr_runner.py:725,744`. `grep architect_agent|devops_agent` sobre `product_loop/` → cero |
| El `IterationPlanner` no fue olvidado: fue **superseded** | `TechnicalLeadPlanner` ocupa el mismo nicho y es el que el coordinator cablea (`coordinator.py:2220`). Único ejercitador del IterationPlanner: `tests_py/test_iteration_planner.py`. La API expone lectura (`product_loop/api.py:94`) de una tabla que ningún camino puede poblar |
| `discovery_sessions` / `conversation_messages` están 100% muertas | Sin escritores **ni lectores** productivos. Además `initiative_id TEXT NOT NULL` (`migrations.py:2575`) mientras el `initiativeId` del loop es nullable, y no existe servicio que cree iniciativas |
| El plan del TL se calcula y se bota | `_quality_gates(tasks, intent)`, handoffs agregados y `branch_worktree_plan` no se persisten; es el único lugar donde `intentClassification.requiredGates` se materializa, y se pierde en el mismo stack frame |
| `.aido/` ya está gitignoreado por AIDO | `git_workspace/service.py:56`, "Local AIDO artifacts". Solo se escribe si el repo no tiene `.gitignore` (`_ensure_initial_gitignore`), así que el comportamiento difiere entre proyectos nuevos y existentes |
| Ya existe precedente de artefacto Markdown | El QAAgent escribe `<artifact-id>.qa-spec.md` (`text/markdown`). Los artefactos de evidencia son **archivos** bajo `<root>/.tmp/evidence-artifacts/`, no blobs en SQLite |

### 2.2 Modos de fallo que hundieron el diseño inicial

Un panel adversarial de tres lentes (arquitectura, producto, riesgo) votó **REWORK unánime**. Las fallas fatales, todas incorporadas abajo:

1. **Escribir `.md` en la raíz del proyecto auto-bloquea AIDO.** `dirty` incluye untracked (`git_workspace/service.py:773`) y el loop bloquea todo run con árbol sucio (`coordinator.py:4711`). Determinista.
2. **Escribir `.md` en el worktree contamina `changedFiles`**, que es el guard fail-closed de `technical_lead_gate` (`delivery.py:52`) y de la detección de no-op (`coordinator.py:6482`). Un developer que no escribió código pasaría el gate y auto-mergearía.
3. **Los artefactos tempranos caerían en la rama equivocada.** El loop asigna dos workspaces: el del PO (`{prefix}/product-owner-{suffix}`) y el del developer (`{prefix}/product-loop-{suffix}`). Solo el del developer aterriza (`delivery.py:109-120`).
4. **`quality_review` como fase post-security es ilegal**, y desde su única entrada legal (`executing`) **saltaría QA y Security completos** — regresión de seguridad.
5. **La constitución escrita en el workspace es escribible por el agente que gobierna.** El DeveloperAgent tiene permiso sobre todo el workspace por contrato de prompt (`runtime_registry.py:281`). Derivar límites de un archivo que el enforcado puede editar es escalada de privilegios.
6. **Statuses nuevos envenenan la evidencia y pintan el hilo en rojo.** `_record_run_evidence` (`coordinator.py:1109-1122`) marca `blocked` + severidad `high` para todo status fuera de un set cerrado; `worker.py:477-485` cae por default a evento `blocked`.

## 3. Decisiones bloqueadas

- **D1 — El hilo es la feature.** No se crea entidad nueva. `spec/plan/tasks` se generan en el **primer loop del hilo** y los mensajes siguientes los **amplían** (sección `Cambio N` + tareas nuevas), nunca los sobrescriben. Se persiste `specPath`/`specVersion` en el contexto del **hilo**, no del loop.
- **D2 — Motor único: `product_loop/phases/`.** Las fases salen de `coordinator.py` a un paquete propio, un módulo por fase con firma tipada. `coordinator.py` queda como driver del FSM y dueño de la transacción (`_transition_run_state`, `_apply_transition`, `_block_run`). **Precondición no negociable:** terminar antes la extracción del bloque puente de ~550 líneas (`coordinator.py:3999`, `# Puente temporal mientras se extraen las fases restantes.`).
- **D3 — Cero estados nuevos.** El proceso se expresa sobre los 24 estados existentes. Solo se corrigen **tres aristas** de `ALLOWED_TRANSITIONS`.
- **D4 — La BD es la fuente de verdad; los `.md` son renders.** Constitución, spec, plan y tareas viven versionados en SQLite. Los archivos se materializan como render derivado, con encabezado `generated — edit via AIDO`, en **un solo punto seguro**.
- **D5 — La constitución es dato de proyecto, no estado del FSM.** Se resuelve como `project.goal.statement` y se verifica dentro de las fases existentes. Enforcement **solo desde BD**, nunca desde el archivo.
- **D6 — Auto-aprobación por riesgo + carril rápido.** Brief auto-aprobado cuando `risk == low`; aprobación humana una vez por hilo en `medium+`. Mensajes de bajo riesgo saltan Plan/Tareas/Análisis con motivo visible.
- **D7 — Architect va post-diff; DevOps va detrás de un setting.** El ArchitectAgent se cablea donde su contrato funciona (después de security, con el `patchArtifactId`). El DevOpsAgent arranca **no bloqueante** y con scripts vacíos por defecto.

## 4. Mapeo de fases → FSM

| Fase Spec Kit | Estados AIDO | Qué se construye |
|---|---|---|
| **Constitución** | *(ninguno — dato de proyecto)* | Tabla versionada + resolver + override de perfil. Verificada al inicio de cada run |
| **Especificación** | `discovery → brief_ready` | Ya existe (`product_briefs` versionado). Se agrega el render `spec.md` |
| **Clarificación** | `awaiting_user ↔ discovering` | Ya existe el motor de preguntas. Se acota a 2 rondas con criterio de progreso |
| **Plan** | `brief_ready → architecture_review → backlog_ready` | **Contrato nuevo** `plan_author` (sin `diffArtifactId`). Render `plan.md` |
| **Tareas** | `planning → backlog_ready → iteration_planning → branch_ready` | `TechnicalLeadPlanner` (ya cableado) + persistir el plan que hoy se bota. Render `tasks.md` |
| **Implementación** | `branch_ready → executing → qa_running → security_running` | Ya existe |
| **Análisis** | `security_running → quality_review → review_ready` | Consistencia cruzada spec↔plan↔tasks↔diff, con el diff ya disponible |
| **Entrega** | `awaiting_approval → delivered` | Ya existe + Architect (post-diff) y DevOps (opcional) |

### 4.1 Cambios a `ALLOWED_TRANSITIONS` (los únicos)

```
+ "iteration_planning": {"branch_ready", "executing", ...}   # corrige el bug latente
+ "security_running":   {"quality_review", "review_ready", ...}
+ "quality_review":     {"review_ready", "awaiting_approval", "reworking", ...}
- "executing":          {... "quality_review" ...}           # elimina el bypass de QA+Security
```

Test que ancla la propiedad: **no existe ningún camino de `executing` a `awaiting_approval` que no pase por `qa_running` y `security_running`.**

## 5. Modelo de artefactos

### 5.1 Ubicación y momento

| Artefacto | Fuente de verdad | Render en disco | Cuándo se escribe |
|---|---|---|---|
| Constitución | tabla `project_constitutions` + `_versions` | `.aido/memory/constitution.md` (read-only, hash-verificado) | Copiada al worktree del developer tras `branch_ready` |
| Spec | `product_briefs` + `acceptance_criteria` | `.aido/specs/NNN-slug/spec.md` | Idem |
| Plan | tabla `technical_plans` (nueva) | `.aido/specs/NNN-slug/plan.md` | Idem |
| Tareas | `agent_tasks` + `task_dependencies` | `.aido/specs/NNN-slug/tasks.md` | Idem |
| Análisis | evidencia del run | `.aido/specs/NNN-slug/analysis.md` | Tras `quality_review` |

**Nunca se escribe nada en la raíz del proyecto.** Todo se materializa dentro del worktree asignado, en un único punto justo después de `branch_ready` (`coordinator.py:6088`), antes de ejecutar al developer.

### 5.2 Los tres guardrails obligatorios

1. **Exclusión del diff.** Un helper compartido excluye el prefijo `.aido/` del cálculo de `changedFiles`, consumido tanto por `_changed_files_from_diff` (`coordinator.py:408`) como por `delivery.technical_lead_gate`. Test: **un run con solo `.md` cambiados sigue bloqueando.**
2. **Gitignore con negación.** Se agregan `!.aido/memory/` y `!.aido/specs/` a `DEFAULT_GITIGNORE_LINES`, mismo patrón que ese archivo ya usa para `.env*` / `!.env.example`. Test: dos runs seguidos y el segundo **no** bloquea por `dirty`.
3. **Numeración transaccional.** El `NNN` de `NNN-slug` se reserva en BD bajo `immediate_transaction` y se pasa al worktree — nunca se calcula leyendo el filesystem, porque varios hilos del mismo proyecto corren en paralelo (`ThreadBusyError` es por hilo, no por proyecto).

## 6. Constitución

### 6.1 Modelo

Tabla `project_constitutions` + `project_constitution_versions`, calcando el par `product_briefs`/`product_brief_versions` (versión, `change_summary`, `authored_by`, `UNIQUE(constitution_id, version)`). Escrita **solo por el operador vía API**.

### 6.2 Inyección en prompts

Bloque opcional siguiendo el patrón `spec_block` de `runtime_registry.py:264-287`, que garantiza prompt **byte-idéntico** cuando el bloque es `None` — propiedad necesaria para no invalidar los tests que anclan el prompt legado. Renderer con tope de caracteres y marcador `[truncated]` propio, como `STORY_SPEC_PROMPT_CHAR_LIMIT`.

### 6.3 Enforcement — solo desde BD

La constitución **no se enforcea desde el archivo**. El archivo es una copia read-only cuyo hash se verifica contra la BD antes de cada fase; si difiere, se ignora y se registra un evento de tampering.

Los límites ejecutables se materializan escribiendo `agent_profile_project_overrides` (`allowed_tools`, `allowed_providers`, `allowed_runtimes`, `max_cost_per_run`, `max_tokens_per_run`, `quality_gates`), que el `ToolBroker` **ya evalúa**. Esto evita tocar sus 18 call sites en 11 módulos.

Los gates derivados viajan como campo explícito de `IntentClassificationInput`, calculado por el llamador. **No se toca `_gates_for`**: es una función pura sobre un dataclass frozen, firmada `deterministic_intent_classifier`; leer BD ahí rompe esa garantía y sus tests.

### 6.4 Proyectos sin constitución

Ausente ⇒ se materializa automáticamente una constitución base derivada de settings existentes (`project.goal.statement`, `teamMode`, `forceLocal`, gates del clasificador), marcada `source: 'bootstrapped'`, y el run **continúa**. Solo bloquea si el operador la marcó `enforced`. Se agrega el paso al wizard de proyecto.

Cada run sella `constitutionVersion` + hash en la metadata, siguiendo `seal_operator_cost_decision` (`product_loop/metadata.py:32-63`).

## 7. Fase Plan — contrato nuevo

El `ArchitectAgent` **no sirve** aquí (§2.1). La fase Plan necesita un contrato propio:

- **Input:** spec + assessment del proyecto + árbol del repo. **Sin `diffArtifactId`.**
- **Output:** `stack`, `contracts`, `dataModel`, `migrations`, `risks`, `openQuestions`.
- **Ubicación:** `architecture_review`, entre `brief_ready` y `backlog_ready`.

El `ArchitectAgent` actual se cablea **post-diff**, después de `security_running`, alimentado con el `patchArtifactId` exactamente como hace `issue_to_pr_runner.py:725`. Ahí su contrato funciona sin cambios.

## 8. Carril rápido y ciclo de vida

### 8.1 Bypass por riesgo

El `IntentClassifier` ya entrega `risk` e `intents`. Si `risk == low` y hay un solo intent en `{docs, tests, cleanup, bugfix}` ⇒ se saltan Plan/Tareas/Análisis y se registra `specDriven: 'skipped_low_risk'` con el motivo visible en la UI. Las fases pesadas **nunca son incondicionales**: `architecture_review` solo con intent `architecture`/`refactor` o `risk high|critical` — exactamente la regla que `_gates_for` ya aplica (`intent_classifier.py:290-291`).

### 8.2 Clarificación acotada

Máximo **2 rondas** por hilo (mismo patrón que `DEFAULT_AUTO_REWORK_ROUNDS`), y cada ronda debe subir el score o se corta. Tras la segunda, se continúa registrando **supuestos explícitos** en `spec.md` en vez de bloquear. Esto evita reabrir el bucle de repreguntas que `_has_actionable_product_owner_input` ya existe para prevenir.

### 8.3 Ampliación, no sobrescritura

Mensaje 2..N del hilo: se lee `specPath`/`specVersion` del contexto del hilo, se **anexa** una sección `Cambio N` y se generan solo las tareas nuevas. Test: dos mensajes en el mismo hilo ⇒ la ruta `NNN-slug` no cambia y `spec.md` solo crece.

## 9. Plan de slices

Cada slice es verde por sí solo y reversible. **Ninguno emite estados nuevos hasta el slice 3.**

| # | Slice | Contenido | Verificación |
|---|---|---|---|
| 0 | **Matriz FSM** | Las 3 aristas de §4.1 + reorden de `PRODUCT_LOOP_STATES` para que `iteration_planning` quede antes de `branch_ready` + espejo en `productLoopModel.ts` | Test de camino feliz completo; test de no-bypass de QA/Security. Corrige un bug latente actual |
| 1 | **Taxonomía** | Whitelist de `_record_run_evidence`, mapas de `worker.py` (eliminar el default a `blocked`), `MILESTONE_INDEX`, `ArtifactKind` + `openapi:generate` | Tests de exhaustividad: todo status que `_run_user_message` puede retornar mapea a un verdict y a un par (threadStatus, eventType) elegidos a propósito |
| 2 | **Extracción** | `product_loop/phases/` + terminar el bloque puente de ~550 líneas | Tests existentes verdes; `coordinator.py` baja de 7.479 líneas |
| 3 | **Constitución** | Migración, slice, resolver, override de perfil, wizard, UI | Constitución bootstrapped no bloquea; hash mismatch registra tampering |
| 4 | **Prompt** | Bloque opcional en developer/PO/QA/security/architect | Prompt byte-idéntico sin constitución |
| 5 | **Render de artefactos** | Escritura al worktree post-`branch_ready` + exclusión del diff + gitignore + numeración transaccional | Run con solo `.md` sigue bloqueando; segundo run no bloquea por dirty |
| 6 | **Plan** | Contrato `plan_author` + `technical_plans` + persistir el plan que hoy se bota | `architecture_review` alcanzable y con artefacto real |
| 7 | **Análisis** | `quality_review` post-security | Consistencia cruzada con el diff disponible |
| 8 | **Equipo** | Architect post-diff + DevOps detrás de setting, no bloqueante | Wall time medido sobre hilos reales antes de promover DevOps a gate |

El slice 1 se hace **inmediatamente después de un fetch**, en commit propio y mínimo: `openapi:generate` hornea el WIP del escritor autónomo concurrente si lo encuentra en el árbol.

## 10. Deuda que este diseño expone pero no resuelve

Se reporta, no se toca (fuera de alcance):

- **Doble entrada de descubrimiento:** `workflows/api.py:383` emite `discovering` y el coordinator emite `discovery`. Unificar es parte del slice 3 solo si aparece en el camino; si no, queda anotado.
- **`IterationPlanner` duplicado:** la decisión real es **borrarlo** (junto con la tabla `iterations`, su empty-state en la UI y el campo `iterations` de `ProductLoopStateResponse`) o portarle a `TechnicalLeadPlanner` lo que solo él tiene: `_workspace_strategy`, security gates por riesgo y `_estimated_cost`. No cablearlo.
- **`discovery_sessions` / `conversation_messages` muertas:** exigen `initiative_id NOT NULL` y no hay servicio que cree iniciativas. Se dejan quietas; el transcript de clarificación usa los thread messages que ya existen y ya se renderizan.
- **Sin resume durable por fase:** `resume()` solo lee estado, no reejecuta. Más fases = ventana de caída más ancha. Si el wall-clock crece más de lo previsto en el slice 8, el re-entry durable entra como slice 9.
- **Orden frontend/backend ya divergente:** `productLoopModel.ts` lista `iteration_planning` antes de `branch_ready` y el gate solo compara pertenencia, nunca orden. El slice 0 lo alinea; extender el gate a orden queda propuesto.
- **Dos destinos de evidencia:** según el camino de entrada, el root es `platform.cwd` (AIDO) o `projects.path` (repo del usuario). Inconsistencia previa que conviene cerrar antes del slice 5.

## 11. No-objetivos

- No se migra el product loop al DAG de `workflows/`. Se reusan sus runners, no su representación.
- No se agrega aprobación humana por fase: el loop ya tiene dos paradas humanas reales (`awaiting_user`, `awaiting_approval`) y duplicarlas es churn.
- No se toca `ToolBroker.evaluate_tool_call` ni `_gates_for`.
- No se crea entidad "feature": el hilo lo es.
