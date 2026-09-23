# Ejecución por historia + tablero del PO + layout de desarrollo

**Fecha:** 2026-09-22
**Estado:** Diseño aprobado — enfoque A (sub-proyecto 2 de 3). Pendiente de plan de implementación.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Objetivo

Cuando un hilo llega a desarrollo, el área principal muestra el **tablero armado por el PO** (Por hacer ·
En curso · QA · Listo) y el chat pasa a una columna a la derecha. Las tarjetas avanzan **una a una** porque
el developer trabaja **historia por historia**, cada una con su propio QA.

Criterios de éxito verificables:

1. Con un backlog de N historias, el developer corre N veces (una por historia, más reworks), cada run
   recibe solo las tareas y el spec de su historia.
2. Cada historia pasa `todo → in_progress → qa → done` (o `blocked`) y ese estado se persiste en
   `user_stories`/`agent_tasks`; el tablero lo refleja en ≤ 2 s tras cada transición.
3. Seguridad y aprobación evalúan el **diff acumulado** de todas las historias, no solo la última.
4. Al entrar a `executing` con tablero disponible, la UI cambia sola a modo desarrollo; el operador puede
   volver al modo chat con un toggle.
5. Un reintento tras un bloqueo **salta las historias ya `done`**.

## 2. Estado actual (verificado)

| Hecho | Evidencia |
|---|---|
| Un solo run del developer con todas las tareas y specs | `phases/execution.py:230-280` (`storySpecs` de todas las historias), driver `coordinator.py:4195-4210` |
| QA ya corre dentro de cada run del developer | `agents/developer_agent.py:656-662`; gate `phases/qa_gate.py:21` |
| Rework: 2 rondas, contador de loop | `DEFAULT_AUTO_REWORK_ROUNDS` `coordinator.py:137`; `fsm.usage.reworkRounds` `~5201` |
| FSM: desde `qa_running` no se vuelve a `executing` | `ALLOWED_TRANSITIONS` `coordinator.py:154-178` |
| Estados de historias/tareas nunca avanzan | BD viva: 30/30 historias `draft`, 219/219 tareas `todo`; solo feedback escribe estado (`coordinator.py:5446,5600,5643`) |
| Estado es string libre (sin `Literal`) | `UserStoryRecord.status: str` `product_loop/models.py:214`, `AgentTaskRecord.status` `:262` |
| Un worktree/rama por hilo, commits acumulan, diff por run = solo ese run | `stable_task_suffix` `workspaces_projects/repository.py:27`, `capture_git_diff` `git_worktrees.py:971`, `commit_workspace_changes` `:859` (commit antes del QA gate) |
| Seguridad y aprobación usan solo el último run | `security.py:54` + SecurityAgent (`patchArtifactId` del último run); `approval.py:21` (`run.review`) |
| Sin prioridad efectiva en historias | BD viva: todas `medium`; el PO no emite prioridad → orden = orden de emisión del backlog |
| Sin endpoint de backlog por hilo | `GET /api/v1/projects/{id}/product-loop` es por proyecto, sin paginar (`product_loop/api.py:119`) |
| Enlace hilo→loop→backlog | `product_loops.context.durableRun.thread.projectThreadId`; tareas `metadata.loopId`; historias vía `productOwnerOutputId` |
| Layout del hilo | `.thread-live-body` fila flex: transcript (flex 1) + `ThreadExecutionPanel` (`layout.css:1686,1778`); apila a 960 px (`:2184`) |
| Tablero reutilizable | `features/review/ReviewColumn.tsx`/`ReviewCard.tsx`, CSS `.review-board` `layout.css:4216-4285` |

## 3. Diseño — ejecución por historia (enfoque A)

### 3.1 Orden de historias

`_ordered_stories(agent_tasks)`: agrupa tareas por `storyId`; orden = rango de prioridad
(`critical > high > medium > low`, desconocido = `medium`) y, en empate, orden de emisión del backlog
(`_stories_from_backlog`, `coordinator.py:2176`). Tareas sin historia se agrupan en una historia sintética
"Tareas generales" al final.

### 3.2 Iteración

El driver `coordinator.py:4195-4210` pasa de `while True: execute → capture → qa` a:

```
for story in ordered_stories (saltando done / done_noop del cursor):
    mark story+tasks in_progress; evento story_progress
    rework_round = 0            # por historia
    loop:
        execute_developer_phase(tasks=story.tasks, storySpecs=[story.spec], task_id=base:s<k>[:r<n>])
        capture_review_evidence  # commit por historia (ya ocurre)
        si no hubo cambios → story done_noop; break
        mark story qa; evento
        evaluate_qa_gate
        pasa → mark story done; evento; break
        rework (hasta 2 por historia) → mark in_progress; continue
        agota → mark story blocked; loop blocked (cursor durable); return
diff acumulado base..HEAD → security → análisis → aprobación
```

- **FSM:** se agrega la arista `qa_running → executing` (trigger `next_story`). Sigue siendo imposible
  llegar a `security_running` sin pasar por QA (`test_product_loop_fsm_matrix.py:59` se mantiene).
- **Rework por historia:** `run.rework_round` se reinicia por historia; el tope de loop
  (`fsm.maxReworkRounds`) pasa a evaluarse por historia (el límite configurado por el operador aplica por
  historia; `test_product_loop_coordinator.py:8539` se adapta a esa semántica).
- **Cursor durable:** `durableRun.storyProgress = [{storyId, status, commit, qaVerdict, runs}]` vía el
  patch existente (`coordinator.py:623`). Se diseña para que el enfoque B (un job por historia) lo reuse
  sin migración.
- **Historia sin tareas (decisión 2026-09-22):** si el Technical Lead LLM deja una historia del PO sin
  tareas, no se bloquea: esa historia se planifica con el `TechnicalLeadPlanner` determinista (el planner
  por defecto, `coordinator.py:2299`), sus tareas quedan con `metadata.technicalLeadFallback=true` y se
  emite el evento de hilo `technical_lead_fallback` `{loopId, storyId}`. Solo si el planner determinista
  tampoco produce tareas para ella, el loop se bloquea en `technical_lead` antes del primer run.
- **Commit por historia obligatorio (decisión 2026-09-22):** en un worktree git, un commit fallido bloquea
  la historia antes del QA. Ante fallos se corrige la causa (policy, identidad git); la guarda no se relaja.
- **Reintento:** `retry_loop` reutiliza tareas (`coordinator.py:2273-2275`) y el cursor → las historias
  `done` se saltan.
- **Cancelación:** sin cambios; `_transition_run_state` sigue siendo el único punto de corte y se cruza en
  cada historia.

### 3.3 Diff acumulado para seguridad y aprobación

Nueva captura `capture_cumulative_diff(workspace, base_ref)` = `git diff merge-base(base)..HEAD` como
artefacto `git_patch`. `security.py` y `approval.py` consumen ese artefacto en vez del último
`runtime_result.diffSummary`. Las historias `done_noop` no aportan diff.

### 3.4 Estados persistidos

| Momento | Historia | Tareas |
|---|---|---|
| Backlog listo | `todo` | `todo` |
| Inicio de su run | `in_progress` | `in_progress` |
| QA gate | `qa` | `qa` |
| QA pasa | `done` | `done` |
| Sin cambios | `done` (con `metadata.outcome=noop`) | `done` |
| Agota rework / bloqueo | `blocked` | `blocked` |

Escritura con `BacklogRepository.update_user_story/update_agent_task` (strings libres, sin drift de
OpenAPI). **No** se tocan `agent_assignments` (sus estados de inicio disparan gates de handoff,
`backlog/repository.py:1083,1304`). Cada cambio emite el evento de hilo `story_progress`
`{storyId, status, index, total}`.

### 3.5 Límites

- Cada run del developer conserva su plazo (`remaining_execution_timeout(900)`), así que N historias
  pueden tomar hasta N × 15 min en un job con un lease `agent_cli`. Aceptado en el enfoque A y visible en la
  UI (historia k de N).
- `maxTokensPerRun` es por llamada; el costo total escala con N. `ThreadCostPanel` ya lo muestra por run.

## 4. Diseño — tablero

- **Endpoint:** `GET /api/v1/threads/{thread_id}/board` → resuelve el loop activo del hilo
  (`durableRun.thread.projectThreadId`), sus historias (`productOwnerOutputId`) y tareas (`metadata.loopId`).
  Respuesta: `{loopId, loopState, stage: executing|security|approval|delivered|blocked, columns:
  [{id: todo|in_progress|qa|done, cards:[…]}], progress: {done, total}}`. Tarjeta: historia (título,
  "Como/Quiero/Para", criterios), tareas con su rol, `runtime` del developer, `blocked` + causa. Payload
  acotado al hilo (no el agregado del proyecto).
- **Mapeo de estado → columna** en un solo módulo backend (`backlog/board.py`), reflejado en el tipo
  generado.
- **UI:** componente `ThreadBoard` reutilizando el patrón `ReviewColumn`/`.review-board` (4 columnas,
  cabeceras fijas, scroll por columna), `StatusChip`, contador por columna, franja superior con la etapa
  de loop (seguridad/aprobación/entregado). Sin drag & drop: el loop mueve las tarjetas; reordenar sigue
  siendo la acción existente `reprioritize`.
- **Refresco:** refetch al llegar eventos `story_progress`, `executing`, `qa_running`,
  `security_running`, `awaiting_approval`, `delivered` del stream existente (`useThreadEventStream`).

## 5. Diseño — layout de desarrollo

- Señal de etapa: exportar la derivación de pipeline de `ThreadExecutionPanel` (`derivePipeline`, hoy
  privada) como hook `useThreadStage`; `MILESTONE_INDEX` sigue siendo literal (lo lee
  `test_product_loop_result_taxonomy.py`).
- **Modo desarrollo** automático al primer `executing` con tablero no vacío:
  `.thread-live-body[data-mode="board"]` → tablero en el área principal (flex 1) y columna derecha fija
  (`clamp(20rem, 30cqw, 28rem)`) con transcript + composer. El `ThreadExecutionPanel` se compacta a una
  franja horizontal sobre el tablero (mismos pasos, `.thread-pipeline-step` conservado).
- **Toggle manual** `SegmentedControl` "Chat | Tablero" en la cabecera del hilo; la elección manual gana
  sobre la automática para ese hilo.
- **Inspector:** en modo desarrollo no se auto-abre (hoy se abre al seleccionar hilo en desktop,
  `AppShell.tsx:193-201`); queda a un clic.
- **Angosto / móvil:** `@container` (no media query de viewport dentro del pane): tablero arriba, chat
  abajo con altura propia; el composer nunca queda tapado.
- **Transición:** `m.div layout` + `crossfade` existentes, respetando `reducedMotion`.
- **Tripwires:** se mantienen `.thread-transcript-pane`, `.thread-live-scroll`, `.thread-composer-dock`,
  `.thread-execution-pane`, `.thread-pipeline-step`; los specs de `threads.spec.js:261-331` se cumplen en
  ambos modos.

## 6. Pruebas

- Backend: orden de historias; N historias → N runs con payload acotado; rework por historia; historia
  `noop`; historia sin tareas del TL LLM planificada por el respaldo determinista (y bloqueo
  `technical_lead` si el respaldo tampoco la cubre); bloqueo al agotar rework con cursor; reintento que salta `done`; arista `qa_running → executing`
  en la matriz FSM; seguridad/aprobación con diff acumulado; estados escritos y eventos `story_progress`;
  endpoint de tablero (resolución hilo→loop, columnas, payload acotado).
- Se adaptan los anclajes de semántica de un solo run (`test_product_loop_coordinator.py:3049,3097,3133,
  5045,8478,8513,8535,8539,8644,8407`) a la semántica por historia, sin relajar lo que protegen.
- Frontend: `ThreadBoard` con fixtures; spec Playwright que verifica el cambio a modo desarrollo, el
  toggle y los tripwires de scroll/overflow en ambos modos y en móvil.

## 7. Fuera de alcance

- Enfoque B (un job por historia con reanudación real) — el cursor queda listo para ello.
- Ejecución paralela de historias.
- Edición manual de estados de tarjetas.

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| Job largo con N historias | Progreso k/N visible; cancelación por historia; enfoque B posterior |
| Código de una historia fallida queda commiteado (commit antes del QA) | La historia queda `blocked`, el loop se detiene; el diff acumulado lo muestra en aprobación |
| Tests que fijan un solo run | Se adaptan explícitamente, listados en §6 |
| Workspace no-git + reintento con todas las historias `done` ⇒ bloqueo en `review` | Aceptado (fail-closed, decisión 2026-09-22): sin git no hay diff acumulado que revisar y el guard "sin cambios" bloquea; el operador ve la causa en la tarjeta de bloqueo |
| TL LLM omite historias | Respaldo con el `TechnicalLeadPlanner` determinista + evento `technical_lead_fallback`; bloqueo `technical_lead` solo si el respaldo tampoco planifica |
| Commit por historia falla | Bloquea la historia (fail-closed); se corrige la causa raíz, nunca se relaja la guarda |
| Ancho: explorer + tablero + chat + inspector | Inspector no se auto-abre en modo desarrollo; `@container` |
