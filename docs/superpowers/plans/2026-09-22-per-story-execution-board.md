# Per-Story Execution Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El DeveloperAgent ejecuta el backlog del PO historia por historia (cada una con su QA y su presupuesto de rework), el estado `todo → in_progress → qa → done|blocked` se persiste en `user_stories`/`agent_tasks` con evento `story_progress` y cursor durable, Security y la aprobación revisan el diff acumulado de todas las historias, un reintento salta las historias ya terminadas, y el hilo cambia solo a un "modo desarrollo" con el tablero en el área principal y el chat como columna derecha (con toggle manual).

**Architecture:** Enfoque A del spec: un bucle en proceso dentro de `_run_user_message` (nueva fase `product_loop/phases/story_loop.py`) reemplaza el `while True` de un solo run; la FSM gana la arista `qa_running → executing` (trigger `next_story`) que además reinicia `fsm.usage.reworkRounds` vía `_fsm_patch`. El orden, el "done", la huella de spec, la forma del cursor y el mapeo estado→columna viven en un único módulo puro `backlog/board.py` que consumen el loop y el nuevo endpoint `GET /api/v1/threads/{thread_id}/board`. El diff acumulado se captura con `git diff <base>...HEAD` por el ToolBroker (subcomando `diff` ya allowlisted) y se guarda como artefacto `git_patch` para el SecurityAgent. En la UI, `ThreadConversation` deriva la etapa con el mismo `derivePipeline` del panel de ejecución, carga el tablero con refetch por eventos y cambia `.thread-live-body[data-mode]` entre `chat` y `board` (grid con `@container`, sin media queries de viewport dentro del pane).

**Tech Stack:** Python 3.11 + FastAPI + Pydantic v2 + SQLite (`local_control_center`), pytest; React 19 + TypeScript + Vite + `motion` (LazyMotion `m.*`) + Biome 2.5; Playwright para E2E de UI; OpenAPI → `local-control-center/web/src/api/generated/openapi.ts`.

**Spec:** `docs/superpowers/specs/2026-09-22-per-story-execution-board-design.md`

## Global Constraints

- Python tests (desde la raíz del repo): `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['<test files>','-q','-p','no:randomly']))"` (workaround del access-violation de faiss).
- Ruff format: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format <py files>` — nunca sobre `.json`.
- Ruff lint: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check <py files>` — nunca sobre `.json`.
- Web typecheck: `corepack pnpm@10.24.0 run typecheck:web`.
- Web lint/format: `corepack pnpm@10.24.0 exec biome check <files>` (se permite `corepack pnpm@10.24.0 exec biome check --write <files>` antes, para organizeImports/format).
- Web build: `corepack pnpm@10.24.0 run build:web`.
- OpenAPI tras cualquier cambio de API/`response_model`: `corepack pnpm@10.24.0 run openapi:generate` (escribe LF).
- Todo módulo productivo nuevo lleva header semántico (docstring Python / JSDoc `/** */` TS) con `@author Rodrigo Mason`.
- Python productivo: solo docstrings (sin comentarios `#` sueltos); docstring en toda API pública (ruff D101-D103).
- Copy de UI solo vía `t('key','English fallback')`; claves bilingües (en != es) en `local_control_center/i18n/default_catalog.json`, editado quirúrgicamente preservando formato (2 espacios, UTF-8 con tildes reales, LF).
- Guardrails visuales CSS: prohibido `Inter`, `cyan`, `purple`, `radial-gradient`, `background-clip: text`, `border-left|right` ≥ 2px; colores vía tokens `oklch`/`var(--color-*)`.
- Nuevos `kind` de artefacto o roles de agente exigen el `Literal` + regen OpenAPI (drift de `response_model`).
- Finales de línea LF en todo archivo tocado.
- Formato de commit: `Tipo (Ámbito): mensaje en español` + trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage solo rutas explícitas (`git add <paths>`); nunca `--no-verify`.
- Playwright: correr specs directo con build fresco y puerto fijo, p. ej. `corepack pnpm@10.24.0 run build:web` y luego `$env:PLAYWRIGHT_DASHBOARD_PORT='9437'; node node_modules/@playwright/test/cli.js test tests_web/<spec>.js --reporter=line` (`run-web-tests.mjs` ignora argumentos).

---

## File Map

| Archivo | Acción | Responsabilidad única |
|---|---|---|
| `local_control_center/backlog/board.py` | Crear | Módulo puro: orden de historias (prioridad + emisión del backlog), "done", huella de spec, entrada del cursor, estado→columna, estado FSM→etapa, construcción del tablero. |
| `local_control_center/backlog/repository.py` | Modificar (tras `list_user_stories` `:578-594`) | `list_user_stories_for_output`: historias de un output del PO en orden de emisión (`rowid`). |
| `local_control_center/workspaces_projects/git_worktrees.py` | Modificar (append al final, tras `capture_git_diff` `:971-1171`) | `capture_cumulative_diff`: diff `<base>...HEAD` de la rama del hilo por ToolBroker. |
| `local_control_center/product_loop/coordinator.py` | Modificar | Arista `qa_running→executing` (`:169`), `fsm_patch` en `_transition_run_state` (`:1122-1147`), campos de `_UserMessageRun` (`:381-391`), import de la fase (`:92-99`), driver (`:4194-4213`), wrapper `_capture_cumulative_review` (`:5006-5007`). |
| `local_control_center/product_loop/phases/qa_gate.py` | Modificar (`:168-172`) | Agotar el rework automático bloquea (`qa_rework`) en vez de estacionar en `reworking`. |
| `local_control_center/product_loop/phases/story_loop.py` | Crear | Driver por historia: cobertura historia→tareas, estados, cursor, eventos `story_progress`, rework por historia, `noop`, QA/evidencia acumuladas, carry-over uno a uno de reintento. |
| `local_control_center/product_loop/phases/execution.py` | Modificar (`:19`, `:256`, `:461-501`, `:502-518`, append) | Payload acotado a la historia, historia sin cambios = `noop`, commit obligatorio por historia, guard "sin cambios", review acumulada + artefacto `git_patch` + rechazo de trabajo sin commitear. |
| `local_control_center/product_loop/phases/security.py` | Modificar (`:113`) | El SecurityAgent recibe el artefacto del diff acumulado. |
| `local_control_center/product_loop/phases/approval.py` | Modificar (`:119-125`, `:169-175`) | El job y la ActionRequest de aprobación referencian el artefacto del diff acumulado (`patchArtifactIds`). |
| `local_control_center/product_loop/repository.py` | Modificar (tras `list_loops` `:138-147`) | `latest_planned_loop_for_thread`: loop más reciente del hilo con tareas planificadas. |
| `local_control_center/product_loop/thread_board.py` | Crear | `ThreadBoardService`: hilo → loop → historias del output del PO/tareas/criterios/cursor → tablero. |
| `local_control_center/product_loop/models.py` | Modificar (tras `StorySpecResponse` `:428-436`) | Modelos de respuesta del tablero con `Literal` de columna y etapa. |
| `local_control_center/product_loop/api.py` | Modificar (`:35-45`, tras `get_story_spec` `:125-139`) | Ruta `GET /api/v1/threads/{thread_id}/board` (handler `def`, threadpool). |
| `local-control-center/web/src/api/generated/openapi.ts` | Regenerar | Tipos y operación del tablero. |
| `local-control-center/web/src/api/client.ts` | Modificar (`:119-121`, tras `:1303-1312`) | `getThreadBoard` + tipos `ThreadBoardResponse`/`ThreadBoardCard`. |
| `local-control-center/web/src/features/shell/threadBoardModel.ts` | Crear | Vocabulario de columnas/etapas (claves i18n + tonos), eventos que refrescan el tablero, tipo `BoardMode`. |
| `local-control-center/web/src/features/shell/useThreadBoard.ts` | Crear | Hook de lectura del tablero con refetch por secuencia de evento. |
| `local-control-center/web/src/features/shell/useThreadStage.ts` | Crear | Señal de etapa (`inDevelopment`) desde `derivePipeline`. |
| `local-control-center/web/src/features/shell/useThreadEventStream.ts` | Modificar (`:11-14`, `:55-91`) | `wakeThreadEventStream`: re-poll inmediato del stream de un hilo tras una acción del operador. |
| `local-control-center/web/src/features/shell/useThreadRemediations.ts` | Modificar (import, `:112-128`) | Una remediación exitosa despierta el stream del hilo (SLA ≤ 2 s tras reintentar). |
| `local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx` | Modificar (`:47-63`, `:110-118`, `:125`, `:183-189`, `:26-45`, `:208-224`, `:258-267`) | Exporta `derivePipeline`/`EXECUTING_STEP_INDEX`, expone `current`, prop `presentation` (franja). |
| `local-control-center/web/src/features/shell/ThreadBoard.tsx` | Crear | Componente del tablero (franja de etapa + 4 columnas + tarjetas). |
| `local-control-center/web/src/features/shell/useShellInspector.ts` | Crear | Contexto para plegar el inspector al entrar en modo desarrollo. |
| `local-control-center/web/src/features/shell/ThreadConversation.tsx` | Modificar (`:1-15`, `:64-72`, `:85-98`, tras `:198`, `:506-516`, `:609-626`) | Modo `chat|board`, toggle, montaje del tablero, franja del panel, plegado del inspector. |
| `local-control-center/web/src/app/AppShell.tsx` | Modificar (`:22`, tras `:180-187`, `:260-262`) | Provee `ShellInspectorContext` (solo desktop). |
| `local-control-center/web/src/design-system/layout.css` | Modificar (append al final, tras `.review-board` `:4214-4290`) | Estilos del tablero y del layout de modo desarrollo con `@container`. |
| `local_control_center/i18n/default_catalog.json` | Modificar (tras la entrada `app.threads.consoleTitle` `:1078-1081`) | Claves bilingües del tablero, del toggle y del evento `story_progress`. |
| `tests_py/test_product_loop_fsm_matrix.py` | Modificar (append) | Arista `qa_running→executing` y reinicio del presupuesto de rework. |
| `tests_py/test_product_loop_coordinator.py` | Modificar (`:7398-7399`, `:8513-8536`) | Anclas de rework agotado adaptadas a bloqueo `qa_rework`. |
| `tests_py/test_backlog_board.py` | Crear | Pruebas del módulo puro del tablero. |
| `tests_py/test_git_cumulative_diff.py` | Crear | Pruebas del diff acumulado en worktree real. |
| `tests_py/test_product_loop_story_execution.py` | Crear | Pruebas del loop por historia, diff acumulado y reintento. |
| `tests_py/test_thread_board_api.py` | Crear | Pruebas del endpoint del tablero. |
| `tests_py/test_thread_board_frontend_contract.py` | Crear | Contrato fuente del frontend (cliente, modelo, etapa, layout). |
| `tests_web/thread-board.spec.js` | Crear | E2E: cambio a modo desarrollo, toggle, tripwires desktop y móvil, reintento bloqueado→tarjeta movida en ≤ 2 s. |

## Task Graph

```
Lane B (puro + git)      T1 ──────────────┬──────────────► T8 (API tablero + OpenAPI) ──► Lane C
                         T2 ─────────┐    │
Lane A (loop, secuencial) T3 ─► T4 ─► T5 ─┴─► T6 ─► T7
Lane C (frontend)                              T9 ─► T10 ─► T11
Gate final                                                          T12 (todas)
```

| Task | Depende de | Archivos (resumen) | Tier |
|---|---|---|---|
| T1 Módulo puro `backlog/board.py` + historias por output | — | board.py, backlog/repository.py, test_backlog_board.py | PATTERN |
| T2 `capture_cumulative_diff` | — | git_worktrees.py, test_git_cumulative_diff.py | PATTERN |
| T3 Arista FSM + `fsm_patch` | — | coordinator.py (`:169`, `:1122-1147`), test_product_loop_fsm_matrix.py | MECHANICAL |
| T4 Rework agotado → bloqueo | T3 | qa_gate.py, test_product_loop_coordinator.py | MECHANICAL |
| T5 Loop por historia (cobertura con respaldo determinista del TL, QA/evidencia acumuladas) | T1, T3, T4 | story_loop.py, execution.py, team_planning.py, coordinator.py, test_product_loop_story_execution.py, test_product_loop_coordinator.py (`:4716`) | HIGH |
| T6 Diff acumulado para Security/aprobación + commit obligatorio | T2, T5 | execution.py, security.py, approval.py, coordinator.py, test_product_loop_story_execution.py | HIGH |
| T7 Reintento salta historias `done` | T5 (y T6 por archivo compartido) | story_loop.py, test_product_loop_story_execution.py | HIGH |
| T8 Endpoint del tablero + OpenAPI | T1 | repository.py, thread_board.py, models.py, api.py, openapi.ts, test_thread_board_api.py | PATTERN |
| T9 Cliente, modelo, hooks, etapa, despertador del stream | T8 | client.ts, threadBoardModel.ts, useThreadBoard.ts, useThreadStage.ts, ThreadExecutionPanel.tsx, useThreadEventStream.ts, useThreadRemediations.ts, test_thread_board_frontend_contract.py | PATTERN |
| T10 Componente `ThreadBoard` + CSS + i18n | T9 | ThreadBoard.tsx, layout.css, default_catalog.json, contract test | PATTERN |
| T11 Modo desarrollo + toggle + inspector + E2E | T10 | ThreadConversation.tsx, AppShell.tsx, useShellInspector.ts, layout.css, default_catalog.json, thread-board.spec.js, contract test | HIGH |
| T12 Verificación integral + push | T1-T11 | ninguno nuevo | PATTERN (verificador independiente) |

**Parallel lanes (conjuntos de archivos disjuntos):**
- Arranque en paralelo: **T1**, **T2**, **T3** (tres agentes; ningún archivo compartido).
- **T8** puede correr en paralelo con **T4-T7** (Lane A) apenas T1 esté integrado: no comparte archivos con `coordinator.py`/`phases/*`. Restricción: `openapi:generate` importa la app completa; ejecútalo cuando Lane A esté en un commit verde.
- **Lane C (T9 → T10 → T11)** es secuencial (comparte `layout.css`, `default_catalog.json`, `test_thread_board_frontend_contract.py`) y arranca cuando T8 dejó `openapi.ts` regenerado. Puede correr en paralelo con T5-T7.
- **MECHANICAL** (delegable a modelo local, verificado por tests): T3, T4. **PATTERN** (modelo eficiente): T1, T2, T8, T9, T10, T12. **HIGH** (modelo más capaz): T5, T6, T7, T11.

---

### Task 1: Módulo puro del tablero por historia (`backlog/board.py`)

**Files:**
- Create: `local_control_center/backlog/board.py`
- Modify: `local_control_center/backlog/repository.py` (después de `list_user_stories` `:578-594`)
- Test: `tests_py/test_backlog_board.py`

**Interfaces:**
- Consumes: `board.py` es puro (solo `hashlib`/`json`); el método de repositorio usa la tabla `user_stories` (`metadata` JSON con `productOwnerOutputId`, escrito por `persist_product_owner_backlog` en `agents/product_owner_agent.py:476-479`); fixture `make_app` (`tests_py/test_workspace_isolation_contract.py:29`).
- Produces:
  - Constantes `STORY_PRIORITY_RANK`, `GENERAL_BATCH_ID = "__general__"`, `STORY_STATUS_TODO|IN_PROGRESS|QA|DONE|BLOCKED`, `BOARD_COLUMNS = ("todo", "in_progress", "qa", "done")`.
  - `priority_rank(priority: str | None) -> int`
  - `order_story_batches(agent_tasks: list[dict], stories_by_id: dict[str, dict], *, story_order: list[str] | None = None) -> list[dict]` (lote = `{"storyId", "story", "tasks"}`; `story_order` = orden de emisión del backlog del PO; toda historia de `story_order` presente en `stories_by_id` tiene lote aunque no tenga tareas)
  - `BacklogRepository.list_user_stories_for_output(project_id: str, product_owner_output_id: str) -> list[dict[str, Any]]` (historias del output del PO en orden de emisión, `rowid ASC`)
  - `story_batch_is_done(batch: dict) -> bool`
  - `story_fingerprint(story: dict, criteria: list[str]) -> str` (sha256 hex, 64 chars)
  - `story_progress_entry(*, story_id, index, title, status, fingerprint="", commit=None, qa_verdict=None, runs=0, runtime=None, reason=None, outcome=None) -> dict`
  - `column_for_status(status: str | None) -> str`
  - `stage_for_loop_state(state: str | None) -> str` (`planning|executing|security|approval|delivered|blocked`)
  - `empty_board() -> dict`
  - `build_board(*, loop_id, loop_state, agent_tasks, stories_by_id, criteria_by_story, progress_by_story, story_order=None) -> dict`

Decisión verificada: el Technical Lead puede devolver tareas en cualquier orden y se persisten en ese orden (`coordinator.py:2296-2340`, bucle sobre `specs`), así que la primera aparición en `agent_tasks` **no** es el orden de emisión del backlog (spec §3.1). El orden de emisión sale de las filas de `user_stories` del output del PO (creadas en orden de `output["userStories"]`, `agents/product_owner_agent.py:443-484`); `list_user_stories` no sirve porque ordena por `updated_at DESC` (`backlog/repository.py:592`).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests_py/test_backlog_board.py`:

```python
"""Módulo puro del tablero por historia: orden, "done", huella de spec, columnas y etapas.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.backlog.board import (
    BOARD_COLUMNS,
    GENERAL_BATCH_ID,
    build_board,
    column_for_status,
    empty_board,
    order_story_batches,
    stage_for_loop_state,
    story_batch_is_done,
    story_fingerprint,
    story_progress_entry,
)
from local_control_center.backlog.repository import BacklogRepository
from tests_py.test_workspace_isolation_contract import make_app as make_app


def _story(story_id: str, *, priority: str = "medium", status: str = "draft", title: str | None = None) -> dict:
    return {
        "id": story_id,
        "title": title or f"Story {story_id}",
        "asA": "operator",
        "iWant": "x",
        "soThat": "y",
        "priority": priority,
        "status": status,
        "metadata": {},
    }


def _task(task_id: str, story_id: str, *, status: str = "todo", role: str = "backend_engineer") -> dict:
    return {"id": task_id, "storyId": story_id, "title": f"Task {task_id}", "role": role, "status": status}


def test_batches_follow_priority_then_emission_order() -> None:
    stories = {
        "s1": _story("s1", priority="low"),
        "s2": _story("s2"),
        "s3": _story("s3", priority="critical"),
        "s4": _story("s4", priority="unknown"),
    }
    tasks = [_task("t1", "s1"), _task("t2", "s2"), _task("t3", "s3"), _task("t4", "s4"), _task("t5", "s2")]

    batches = order_story_batches(tasks, stories)

    assert [batch["storyId"] for batch in batches] == ["s3", "s2", "s4", "s1"]
    assert [task["id"] for task in batches[1]["tasks"]] == ["t2", "t5"]
    assert batches[0]["story"] is stories["s3"]


def test_tasks_without_a_resolvable_story_form_a_trailing_general_batch() -> None:
    batches = order_story_batches([_task("t1", "ghost"), _task("t2", "s1")], {"s1": _story("s1")})

    assert [batch["storyId"] for batch in batches] == ["s1", GENERAL_BATCH_ID]
    assert batches[-1]["story"] is None
    assert [task["id"] for task in batches[-1]["tasks"]] == ["t1"]


def test_emission_order_comes_from_the_backlog_not_from_the_task_order() -> None:
    stories = {"s1": _story("s1"), "s2": _story("s2"), "s3": _story("s3")}
    reversed_tasks = [_task("t3", "s3"), _task("t2", "s2"), _task("t1", "s1")]

    batches = order_story_batches(reversed_tasks, stories, story_order=["s1", "s2", "s3"])

    assert [batch["storyId"] for batch in batches] == ["s1", "s2", "s3"]


def test_a_backlog_story_without_tasks_still_gets_an_empty_batch() -> None:
    stories = {"s1": _story("s1"), "s2": _story("s2")}

    batches = order_story_batches([_task("t1", "s1")], stories, story_order=["s1", "s2"])

    assert [(batch["storyId"], len(batch["tasks"])) for batch in batches] == [("s1", 1), ("s2", 0)]
    assert not story_batch_is_done({"storyId": "s2", "story": _story("s2", status="done"), "tasks": []})


@pytest.mark.usefixtures("controlled_domain_host")
def test_output_stories_are_listed_in_emission_order(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project_path = tmp_path / "emission-project"
    project_path.mkdir()
    project = store.create_project(name="emission-project", path=project_path, template_id="other")
    backlog = BacklogRepository(store.connection)
    epic = backlog.create_epic({"projectId": project["id"], "title": "Onboarding"})

    def story(title: str, output_id: str) -> dict:
        return backlog.create_user_story(
            {
                "projectId": project["id"],
                "epicId": epic["id"],
                "title": title,
                "metadata": {"productOwnerOutputId": output_id},
            }
        )

    first = story("First", "po-output-1")
    story("Other output", "po-output-2")
    second = story("Second", "po-output-1")
    backlog.update_user_story(first["id"], {"status": "done"})

    listed = backlog.list_user_stories_for_output(project["id"], "po-output-1")

    assert [item["id"] for item in listed] == [first["id"], second["id"]]


def test_a_story_is_done_only_when_it_and_all_its_tasks_are_done() -> None:
    done_story = _story("s1", status="done")
    assert story_batch_is_done({"storyId": "s1", "story": done_story, "tasks": [_task("t1", "s1", status="done")]})
    assert not story_batch_is_done(
        {
            "storyId": "s1",
            "story": done_story,
            "tasks": [_task("t1", "s1", status="done"), _task("t2", "s1", status="todo")],
        }
    )
    assert not story_batch_is_done(
        {"storyId": "s1", "story": _story("s1", status="qa"), "tasks": [_task("t1", "s1", status="done")]}
    )
    assert story_batch_is_done(
        {"storyId": GENERAL_BATCH_ID, "story": None, "tasks": [_task("t1", "ghost", status="done")]}
    )


def test_story_fingerprint_ignores_whitespace_and_case_but_not_content() -> None:
    base = story_fingerprint(_story("s1", title="Readiness checklist"), ["Given X, then Y."])
    same = story_fingerprint(_story("s9", title="  readiness   CHECKLIST "), ["given x,  then y."])
    other = story_fingerprint(_story("s1", title="Readiness checklist"), ["Given X, then Z."])

    assert base == same
    assert base != other
    assert len(base) == 64


@pytest.mark.parametrize(
    ("status", "column"),
    [
        ("draft", "todo"),
        ("todo", "todo"),
        ("reopened", "todo"),
        ("in_progress", "in_progress"),
        ("qa", "qa"),
        ("done", "done"),
        ("blocked", "in_progress"),
        ("", "todo"),
    ],
)
def test_column_for_status(status: str, column: str) -> None:
    assert column_for_status(status) == column


@pytest.mark.parametrize(
    ("state", "stage"),
    [
        ("backlog_ready", "planning"),
        ("branch_ready", "planning"),
        ("executing", "executing"),
        ("qa_running", "executing"),
        ("reworking", "executing"),
        ("security_running", "security"),
        ("quality_review", "security"),
        ("review_ready", "approval"),
        ("awaiting_approval", "approval"),
        ("awaiting_feedback", "approval"),
        ("delivered", "delivered"),
        ("blocked", "blocked"),
        ("cancelled", "blocked"),
    ],
)
def test_stage_for_loop_state(state: str, stage: str) -> None:
    assert stage_for_loop_state(state) == stage


def test_build_board_places_cards_and_counts_progress() -> None:
    stories = {
        "s1": _story("s1", status="done", priority="high"),
        "s2": _story("s2", status="qa"),
        "s3": _story("s3", status="blocked", priority="low"),
    }
    tasks = [_task("t1", "s1", status="done"), _task("t2", "s2", status="qa"), _task("t3", "s3", status="blocked")]

    board = build_board(
        loop_id="loop-1",
        loop_state="qa_running",
        agent_tasks=tasks,
        stories_by_id=stories,
        criteria_by_story={"s1": ["c1"]},
        progress_by_story={"s3": {"reason": "QA failed twice", "runtime": "codex_cli"}},
    )

    assert board["loopId"] == "loop-1"
    assert board["stage"] == "executing"
    assert [column["id"] for column in board["columns"]] == list(BOARD_COLUMNS)
    by_column = {column["id"]: [card["storyId"] for card in column["cards"]] for column in board["columns"]}
    assert by_column == {"todo": [], "in_progress": ["s3"], "qa": ["s2"], "done": ["s1"]}
    blocked = board["columns"][1]["cards"][0]
    assert blocked["blocked"] is True
    assert blocked["blockedReason"] == "QA failed twice"
    assert blocked["runtime"] == "codex_cli"
    assert blocked["index"] == 3
    done_card = board["columns"][3]["cards"][0]
    assert done_card["index"] == 1
    assert done_card["acceptanceCriteria"] == ["c1"]
    assert done_card["tasks"] == [{"id": "t1", "title": "Task t1", "role": "backend_engineer", "status": "done"}]
    assert board["progress"] == {"done": 1, "total": 3}


def test_empty_board_has_four_empty_columns() -> None:
    board = empty_board()

    assert board["loopId"] is None
    assert board["loopState"] is None
    assert board["stage"] == "planning"
    assert board["progress"] == {"done": 0, "total": 0}
    assert [column["id"] for column in board["columns"]] == list(BOARD_COLUMNS)
    assert all(not column["cards"] for column in board["columns"])


def test_story_progress_entry_has_a_stable_shape() -> None:
    entry = story_progress_entry(
        story_id="s1",
        index=2,
        title="T",
        status="done",
        fingerprint="f",
        commit="abc",
        qa_verdict="passed",
        runs=2,
        runtime="codex_cli",
    )

    assert entry == {
        "storyId": "s1",
        "index": 2,
        "title": "T",
        "status": "done",
        "fingerprint": "f",
        "commit": "abc",
        "qaVerdict": "passed",
        "runs": 2,
        "runtime": "codex_cli",
        "reason": None,
        "outcome": None,
    }
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_backlog_board.py','-q','-p','no:randomly']))"`
Expected: FAIL en colección con `ModuleNotFoundError: No module named 'local_control_center.backlog.board'`.

- [ ] **Step 3: Implementación mínima**

Crear `local_control_center/backlog/board.py`:

```python
"""Tablero por historia del product loop: orden de ejecución, estado persistido y columnas del hilo.

Módulo puro (sin base de datos ni FSM) compartido por el loop y por el endpoint del tablero: decide en
qué orden ejecuta el developer las historias de un loop (prioridad ``critical > high > medium > low``,
desconocida = ``medium``, y en empate el orden de emisión del backlog), cuándo una historia ya está
terminada, la huella estable de su spec para reconocerla en un reintento, la forma de cada entrada del
cursor durable ``durableRun.storyProgress`` y cómo se proyectan historias, tareas y cursor a las cuatro
columnas del tablero (``todo``/``in_progress``/``qa``/``done``). Diseño:
docs/superpowers/specs/2026-09-22-per-story-execution-board-design.md §3.1, §3.4 y §4.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STORY_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
DEFAULT_PRIORITY_RANK = STORY_PRIORITY_RANK["medium"]
GENERAL_BATCH_ID = "__general__"
STORY_STATUS_TODO = "todo"
STORY_STATUS_IN_PROGRESS = "in_progress"
STORY_STATUS_QA = "qa"
STORY_STATUS_DONE = "done"
STORY_STATUS_BLOCKED = "blocked"
BOARD_COLUMNS = ("todo", "in_progress", "qa", "done")
_COLUMN_BY_STATUS = {
    STORY_STATUS_IN_PROGRESS: "in_progress",
    STORY_STATUS_QA: "qa",
    STORY_STATUS_DONE: "done",
    STORY_STATUS_BLOCKED: "in_progress",
}
_STAGE_BY_LOOP_STATE = {
    "executing": "executing",
    "qa_running": "executing",
    "reworking": "executing",
    "security_running": "security",
    "quality_review": "security",
    "review_ready": "approval",
    "awaiting_approval": "approval",
    "awaiting_feedback": "approval",
    "delivered": "delivered",
    "blocked": "blocked",
    "cancelled": "blocked",
}


def priority_rank(priority: str | None) -> int:
    """Rango numérico de la prioridad de una historia; vacía o desconocida cuenta como ``medium``."""
    return STORY_PRIORITY_RANK.get(str(priority or "").strip().lower(), DEFAULT_PRIORITY_RANK)


def order_story_batches(
    agent_tasks: list[dict[str, Any]],
    stories_by_id: dict[str, dict[str, Any]],
    *,
    story_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Agrupa las tareas por historia y ordena los lotes por prioridad y orden de emisión.

    El orden de emisión es ``story_order`` (las historias del output del PO en el orden en que las
    emitió); las historias que no figuran en él van después, por primera aparición en
    ``agent_tasks`` (el Technical Lead puede devolver las tareas en cualquier orden). Toda historia de
    ``story_order`` presente en ``stories_by_id`` recibe su lote aunque no tenga tareas, para que el
    loop detecte la historia sin planificar y el tablero la muestre. Las tareas cuya historia no
    resuelve forman un lote sin historia (``GENERAL_BATCH_ID``) que siempre va al final. Cada lote es
    ``{"storyId", "story", "tasks"}`` y conserva los dicts originales de las tareas.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    general: list[dict[str, Any]] = []
    for task in agent_tasks or []:
        story_id = str(task.get("storyId") or "").strip()
        if story_id and story_id in stories_by_id:
            grouped.setdefault(story_id, []).append(task)
        else:
            general.append(task)
    backlog_order = [story_id for story_id in story_order or [] if story_id in stories_by_id]
    for story_id in backlog_order:
        grouped.setdefault(story_id, [])
    emission = {
        story_id: position for position, story_id in enumerate(dict.fromkeys([*backlog_order, *grouped]))
    }
    ordered = sorted(
        grouped,
        key=lambda story_id: (priority_rank(stories_by_id[story_id].get("priority")), emission[story_id]),
    )
    batches = [
        {"storyId": story_id, "story": stories_by_id[story_id], "tasks": grouped[story_id]}
        for story_id in ordered
    ]
    if general:
        batches.append({"storyId": GENERAL_BATCH_ID, "story": None, "tasks": general})
    return batches


def story_batch_is_done(batch: dict[str, Any]) -> bool:
    """Indica si el lote ya terminó: la historia y todas sus tareas en ``done``.

    Un ``request_changes`` que devuelve una tarea a ``todo`` o un ``reopen_story`` reabren el lote.
    """
    tasks = batch.get("tasks") or []
    tasks_done = bool(tasks) and all(str(task.get("status") or "") == STORY_STATUS_DONE for task in tasks)
    story = batch.get("story")
    if story is None:
        return tasks_done
    return tasks_done and str(story.get("status") or "") == STORY_STATUS_DONE


def story_fingerprint(story: dict[str, Any], criteria: list[str]) -> str:
    """Huella sha256 del valor de una historia: título, Como/Quiero/Para y criterios normalizados.

    Ignora mayúsculas y espacios para reconocer la misma historia cuando el PO la vuelve a emitir en
    un reintento (filas nuevas, mismo contenido); cualquier cambio de texto produce otra huella.
    """
    payload = {
        "title": _normalized(story.get("title")),
        "asA": _normalized(story.get("asA")),
        "iWant": _normalized(story.get("iWant")),
        "soThat": _normalized(story.get("soThat")),
        "criteria": [_normalized(criterion) for criterion in criteria],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def story_progress_entry(
    *,
    story_id: str,
    index: int,
    title: str,
    status: str,
    fingerprint: str = "",
    commit: str | None = None,
    qa_verdict: str | None = None,
    runs: int = 0,
    runtime: str | None = None,
    reason: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Entrada del cursor durable ``durableRun.storyProgress`` para una historia del loop.

    La forma es estable para que una ejecución futura de un job por historia (enfoque B del spec)
    la reutilice sin migración.
    """
    return {
        "storyId": story_id,
        "index": index,
        "title": title,
        "status": status,
        "fingerprint": fingerprint,
        "commit": commit,
        "qaVerdict": qa_verdict,
        "runs": runs,
        "runtime": runtime,
        "reason": reason,
        "outcome": outcome,
    }


def column_for_status(status: str | None) -> str:
    """Columna del tablero para un estado persistido; ``blocked`` se muestra en curso con su marca."""
    return _COLUMN_BY_STATUS.get(str(status or "").strip().lower(), "todo")


def stage_for_loop_state(state: str | None) -> str:
    """Etapa del tablero para el estado FSM del loop; todo lo previo a ``executing`` es ``planning``."""
    return _STAGE_BY_LOOP_STATE.get(str(state or ""), "planning")


def empty_board() -> dict[str, Any]:
    """Tablero vacío para un hilo que todavía no tiene un loop con tareas planificadas."""
    return {
        "loopId": None,
        "loopState": None,
        "stage": "planning",
        "columns": [{"id": column, "cards": []} for column in BOARD_COLUMNS],
        "progress": {"done": 0, "total": 0},
    }


def build_board(
    *,
    loop_id: str,
    loop_state: str,
    agent_tasks: list[dict[str, Any]],
    stories_by_id: dict[str, dict[str, Any]],
    criteria_by_story: dict[str, list[str]],
    progress_by_story: dict[str, dict[str, Any]],
    story_order: list[str] | None = None,
) -> dict[str, Any]:
    """Proyecta historias, tareas y cursor del loop a las cuatro columnas y el progreso del tablero.

    ``story_order`` (historias del output del PO en orden de emisión) fija el desempate y hace
    visibles las historias sin tareas planificadas.
    """
    columns: dict[str, list[dict[str, Any]]] = {column: [] for column in BOARD_COLUMNS}
    batches = order_story_batches(agent_tasks, stories_by_id, story_order=story_order)
    for index, batch in enumerate(batches, start=1):
        card = _board_card(
            batch,
            index=index,
            criteria=criteria_by_story.get(batch["storyId"]) or [],
            progress=progress_by_story.get(batch["storyId"]) or {},
        )
        columns[card["column"]].append(card)
    return {
        "loopId": loop_id,
        "loopState": loop_state,
        "stage": stage_for_loop_state(loop_state),
        "columns": [{"id": column, "cards": columns[column]} for column in BOARD_COLUMNS],
        "progress": {"done": len(columns["done"]), "total": len(batches)},
    }


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def _batch_status(batch: dict[str, Any]) -> str:
    story = batch.get("story")
    if story is not None:
        return str(story.get("status") or "")
    statuses = {str(task.get("status") or "") for task in batch.get("tasks") or []}
    if statuses == {STORY_STATUS_DONE}:
        return STORY_STATUS_DONE
    for status in (STORY_STATUS_BLOCKED, STORY_STATUS_QA, STORY_STATUS_IN_PROGRESS):
        if status in statuses:
            return status
    return STORY_STATUS_TODO


def _board_card(
    batch: dict[str, Any], *, index: int, criteria: list[str], progress: dict[str, Any]
) -> dict[str, Any]:
    story = batch.get("story") or {}
    status = _batch_status(batch)
    blocked = status == STORY_STATUS_BLOCKED
    metadata = story.get("metadata") if isinstance(story.get("metadata"), dict) else {}
    return {
        "storyId": batch["storyId"],
        "index": index,
        "synthetic": batch.get("story") is None,
        "title": str(story.get("title") or ""),
        "asA": str(story.get("asA") or ""),
        "iWant": str(story.get("iWant") or ""),
        "soThat": str(story.get("soThat") or ""),
        "priority": str(story.get("priority") or "medium"),
        "status": status,
        "column": column_for_status(status),
        "blocked": blocked,
        "blockedReason": (progress.get("reason") or metadata.get("blockedReason") or None) if blocked else None,
        "outcome": metadata.get("outcome") or progress.get("outcome") or None,
        "runtime": progress.get("runtime") or None,
        "acceptanceCriteria": [str(criterion) for criterion in criteria],
        "tasks": [
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "role": str(task.get("role") or ""),
                "status": str(task.get("status") or ""),
            }
            for task in batch.get("tasks") or []
        ],
    }
```

En `local_control_center/backlog/repository.py`, después de `list_user_stories` (`:578-594`), agregar:

```python
    def list_user_stories_for_output(self, project_id: str, product_owner_output_id: str) -> list[dict[str, Any]]:
        """Lista las historias emitidas por un output del PO en su orden de emisión.

        El orden de emisión es el de inserción (``rowid``): ``persist_product_owner_backlog`` crea las
        filas recorriendo ``output["userStories"]``. Es el desempate del orden de ejecución por
        historia y la fuente de las historias del tablero (incluidas las que no tienen tareas).
        """
        rows = self.connection.execute(
            """
            SELECT * FROM user_stories
            WHERE project_id = ? AND json_extract(metadata, '$.productOwnerOutputId') = ?
            ORDER BY rowid ASC
            """,
            (project_id, product_owner_output_id),
        ).fetchall()
        return [row_to_user_story(row) for row in rows]
```

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_backlog_board.py','tests_py/test_source_documentation_headers.py','tests_py/test_backlog_slice.py','-q','-p','no:randomly']))"`
Expected: PASS (todos).
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/backlog/board.py local_control_center/backlog/repository.py tests_py/test_backlog_board.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/backlog/board.py local_control_center/backlog/repository.py tests_py/test_backlog_board.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/backlog/board.py local_control_center/backlog/repository.py tests_py/test_backlog_board.py
git commit -m "Feature (Backlog): módulo puro del tablero por historia con orden por prioridad, huella y columnas" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Diff acumulado de la rama del hilo (`capture_cumulative_diff`)

**Files:**
- Modify: `local_control_center/workspaces_projects/git_worktrees.py` (append al final del archivo, después de `capture_git_diff` `:971-1171`)
- Test: `tests_py/test_git_cumulative_diff.py`

**Interfaces:**
- Consumes: `run_brokered_git` (`git_worktrees.py:192`), `_policy_ids` (`:277`), `_parse_porcelain_status` (`:956`), `run_git`, `git_available` (import `:21`); fixtures `make_app` (`tests_py/test_workspace_isolation_contract.py:29`) y `_workspace_with_repo` (`tests_py/test_git_diff_capture.py:35`).
- Produces: `capture_cumulative_diff(workspace_path: Path, *, base_refs: list[str], connection: sqlite3.Connection, root: Path, project_id: str, workspace_id: str, task_id: str = "cumulative_diff") -> dict[str, Any]` con claves `kind, state, baseRef, branch, headCommit, statusRaw, status, nameOnly, diffStat, patch, patchFull, patchSizeBytes, truncated, toolCalls, policyDecisionIds` (`state="captured"`; `status` = cambios **sin commitear** del worktree, parseados de `status --porcelain=v1`), o `state` en `capture_failed|degraded_git_unavailable` con `stderr`.

Decisión verificada: `merge-base` no está allowlisted en el ToolBroker (`security_policy/policy_engine.py:48` solo `status, diff, log, rev-parse`), así que el rango se expresa como `git diff <base>...HEAD` (tres puntos = desde el merge-base), y la base se valida con `rev-parse --verify`. Ese rango **excluye** el working tree: por eso la captura también devuelve `status` (lo sin commitear) y el consumidor (T6) bloquea si queda trabajo sin commitear fuera de `.aido/`; este módulo no decide qué rutas ignorar (los renders `.aido/` son conocimiento de `product_loop/spec_artifacts.py`, no de la capa git).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests_py/test_git_cumulative_diff.py`:

```python
"""Diff acumulado de la rama del hilo contra su base: evidencia de todas las historias del loop.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_control_center.security_policy.git_command_runner import git_available, run_git
from local_control_center.workspaces_projects.git_worktrees import capture_cumulative_diff
from tests_py.test_git_diff_capture import _workspace_with_repo
from tests_py.test_workspace_isolation_contract import make_app as make_app

pytestmark = [
    pytest.mark.skipif(not git_available(), reason="git CLI is not available"),
    pytest.mark.usefixtures("controlled_domain_host"),
]


def _commit(worktree: Path, relative: str, content: str) -> None:
    target = worktree / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert run_git(["add", relative], cwd=worktree).returncode == 0
    commit = run_git(
        ["-c", "user.name=AIDO Tests", "-c", "user.email=aido@example.test", "commit", "-m", f"story {relative}"],
        cwd=worktree,
    )
    assert commit.returncode == 0, commit.stderr


def _capture(
    store: Any, tmp_path: Path, project: dict[str, Any], workspace: dict[str, Any], base_refs: list[str]
) -> dict[str, Any]:
    return capture_cumulative_diff(
        Path(workspace["path"]),
        base_refs=base_refs,
        connection=store.connection,
        root=tmp_path,
        project_id=project["id"],
        workspace_id=workspace["id"],
    )


def test_cumulative_diff_spans_every_story_commit_since_the_base(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-two")
    worktree = Path(workspace["path"])
    _commit(worktree, "src/story_one.py", "ONE = 1\n")
    _commit(worktree, "src/story_two.py", "TWO = 2\n")

    diff = _capture(store, tmp_path, project, workspace, ["missing-base", "devbase"])

    assert diff["state"] == "captured"
    assert diff["baseRef"] == "devbase"
    assert diff["nameOnly"] == ["src/story_one.py", "src/story_two.py"]
    assert "ONE = 1" in diff["patchFull"]
    assert "TWO = 2" in diff["patchFull"]
    assert diff["headCommit"]
    assert diff["policyDecisionIds"]


def test_cumulative_diff_fails_closed_when_no_base_ref_resolves(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-no-base")

    diff = _capture(store, tmp_path, project, workspace, ["missing-base", "HEAD", "-evil", ""])

    assert diff["state"] == "capture_failed"
    assert "base" in diff["stderr"].lower()


def test_cumulative_diff_without_story_commits_is_captured_and_empty(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-empty")

    diff = _capture(store, tmp_path, project, workspace, ["devbase"])

    assert diff["state"] == "captured"
    assert diff["nameOnly"] == []
    assert diff["patchFull"] == ""
    assert diff["status"] == []


def test_cumulative_diff_reports_uncommitted_work_outside_the_range(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _client, _headers = make_app(tmp_path, monkeypatch)
    project, workspace = _workspace_with_repo(store, tmp_path, name="cumulative-dirty")
    worktree = Path(workspace["path"])
    _commit(worktree, "src/story_one.py", "ONE = 1\n")
    (worktree / "src" / "story_two.py").write_text("TWO = 2\n", encoding="utf-8")

    diff = _capture(store, tmp_path, project, workspace, ["devbase"])

    assert diff["state"] == "captured"
    assert diff["nameOnly"] == ["src/story_one.py"]
    assert "TWO = 2" not in diff["patchFull"]
    assert [entry["path"] for entry in diff["status"]] == ["src/story_two.py"]
    assert diff["statusRaw"].strip()
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_git_cumulative_diff.py','-q','-p','no:randomly']))"`
Expected: FAIL en colección con `ImportError: cannot import name 'capture_cumulative_diff'`.

- [ ] **Step 3: Implementación mínima**

Append al final de `local_control_center/workspaces_projects/git_worktrees.py`:

```python
def _usable_base_ref(ref: str) -> bool:
    return bool(ref) and ref != "HEAD" and not ref.startswith("-") and ".." not in ref and not any(
        character.isspace() for character in ref
    )


def capture_cumulative_diff(
    workspace_path: Path,
    *,
    base_refs: list[str],
    connection: sqlite3.Connection,
    root: Path,
    project_id: str,
    workspace_id: str,
    task_id: str = "cumulative_diff",
) -> dict[str, Any]:
    """Captura el diff acumulado de la rama del workspace desde su base (``<base>...HEAD``).

    Es la evidencia que Security y la aprobación revisan cuando el loop ejecuta varias historias:
    cada historia deja su commit en la rama estable del hilo y este diff las cubre todas. Prueba
    ``base_refs`` en orden (rama base configurada y, como respaldo, el commit desde el que se creó
    el worktree) y usa el primero que ``rev-parse --verify`` resuelve; ``HEAD``, refs vacías o con
    forma de opción se descartan. El rango de tres puntos equivale a ``merge-base(base)..HEAD`` sin
    requerir ``merge-base`` (no allowlisted). Ese rango excluye el working tree, así que ``status``
    devuelve lo que queda sin commitear (``status --porcelain=v1``) para que el consumidor rechace
    un diff incompleto. Los comandos de estado van por el ToolBroker; el patch íntegro se lee con
    ``run_git`` de solo lectura, igual que ``capture_git_diff``, porque el broker trunca salidas
    largas. Fail-closed: sin git, sin base resoluble o con un comando fallido devuelve un estado
    distinto de ``captured`` en lugar de un diff parcial.
    """
    if not git_available():
        return {"kind": "git_diff", "state": "degraded_git_unavailable", "statusRaw": "", "status": []}
    traces: list[dict[str, Any]] = []

    def brokered(args: list[str], suffix: str) -> dict[str, Any]:
        result = run_brokered_git(
            connection=connection,
            root=root,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            cwd=workspace_path,
            args=args,
            task_id=f"{task_id}.{suffix}",
        )
        traces.append(result["trace"])
        return result

    base_ref: str | None = None
    for candidate in base_refs:
        ref = str(candidate or "").strip()
        if _usable_base_ref(ref) and brokered(["rev-parse", "--verify", ref], "base")["returnCode"] == 0:
            base_ref = ref
            break
    if base_ref is None:
        return {
            "kind": "git_diff",
            "state": "capture_failed",
            "stderr": "No base ref resolved for the cumulative diff.",
            "files": [],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    revision_range = f"{base_ref}...HEAD"
    name_result = brokered(["diff", "--name-only", revision_range], "name_only")
    stat_result = brokered(["diff", "--stat", revision_range], "stat")
    branch_result = brokered(["branch", "--show-current"], "branch")
    head_result = brokered(["rev-parse", "HEAD"], "head")
    status_result = brokered(["status", "--porcelain=v1"], "status")
    patch_direct = run_git(["-C", str(workspace_path), "diff", revision_range])
    if name_result["returnCode"] != 0 or status_result["returnCode"] != 0 or patch_direct.returncode != 0:
        return {
            "kind": "git_diff",
            "state": "capture_failed",
            "stderr": (name_result["stderr"] or status_result["stderr"] or patch_direct.stderr or "").strip()[:2000]
            or name_result["reason"]
            or status_result["reason"],
            "files": [],
            "toolCalls": traces,
            "policyDecisionIds": _policy_ids(traces),
        }
    patch = patch_direct.stdout
    return {
        "kind": "git_diff",
        "state": "captured",
        "baseRef": base_ref,
        "branch": branch_result["stdout"].strip() if branch_result["returnCode"] == 0 else None,
        "headCommit": head_result["stdout"].strip() if head_result["returnCode"] == 0 else None,
        "statusRaw": status_result["stdout"],
        "status": _parse_porcelain_status(status_result["stdout"]),
        "nameOnly": [line.strip() for line in name_result["stdout"].splitlines() if line.strip()],
        "diffStat": stat_result["stdout"][:4000] if stat_result["returnCode"] == 0 else "",
        "patch": patch[:12000],
        "patchFull": patch,
        "patchSizeBytes": len(patch.encode("utf-8")),
        "truncated": len(patch) > 12000,
        "toolCalls": traces,
        "policyDecisionIds": _policy_ids(traces),
    }
```

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_git_cumulative_diff.py','tests_py/test_git_diff_capture.py','tests_py/test_execution_boundary_architecture.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/workspaces_projects/git_worktrees.py tests_py/test_git_cumulative_diff.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/workspaces_projects/git_worktrees.py tests_py/test_git_cumulative_diff.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/workspaces_projects/git_worktrees.py tests_py/test_git_cumulative_diff.py
git commit -m "Feature (Git): diff acumulado de la rama del hilo contra su base para Security y aprobación" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Arista FSM `qa_running → executing` y `fsm_patch` en `_transition_run_state`

**Files:**
- Modify: `local_control_center/product_loop/coordinator.py:169` y `:1122-1147`
- Test: `tests_py/test_product_loop_fsm_matrix.py` (append)

**Interfaces:**
- Consumes: `ProductLoopCoordinator.transition(..., _fsm_patch=...)` (`coordinator.py:5087-5135`), `_deep_merge_fsm` (`:445`).
- Produces: `ALLOWED_TRANSITIONS["qa_running"]` incluye `"executing"`; `_transition_run_state(self, loop, *, to_state, reason, trigger, actor, context_patch=None, metadata=None, thread_id=None, fsm_patch: dict[str, Any] | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al inicio de imports de `tests_py/test_product_loop_fsm_matrix.py` (después de `from itertools import pairwise`):

```python
from contextlib import closing
from pathlib import Path
```

y reemplazar el bloque de import del coordinator por:

```python
from local_control_center.product_loop.coordinator import (
    ALLOWED_TRANSITIONS,
    PRODUCT_LOOP_STATES,
    TERMINAL_STATES,
    ProductLoopCoordinator,
)
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
```

Append al final del archivo:

```python
def test_qa_running_can_hand_off_to_the_next_story() -> None:
    """Tras aprobar QA de una historia el loop vuelve a ``executing`` para la siguiente (spec §3.2)."""
    assert "executing" in ALLOWED_TRANSITIONS["qa_running"]


def test_next_story_transition_resets_the_rework_budget(tmp_path: Path) -> None:
    """``fsm_patch`` reinicia ``reworkRounds`` al pasar de historia: el tope aplica por historia."""
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        project_path = tmp_path / "next-story"
        project_path.mkdir()
        project = ProjectsRepository(connection).create_project(
            name="next-story", path=project_path, template_id="other"
        )
        coordinator = ProductLoopCoordinator(connection)
        loop = coordinator.start(project_id=project["id"], title="Per-story rework budget")
        for state in (
            "workspace_check",
            "git_check",
            "discovery",
            "planning",
            "backlog_ready",
            "branch_ready",
            "executing",
            "qa_running",
            "reworking",
            "executing",
            "qa_running",
        ):
            loop = coordinator.transition(loop["id"], to_state=state)
        assert loop["context"]["fsm"]["usage"]["reworkRounds"] == 1

        loop = coordinator._transition_run_state(
            loop,
            to_state="executing",
            reason="Next story.",
            trigger="next_story",
            actor="operator",
            fsm_patch={"usage": {"reworkRounds": 0}},
        )

        assert loop["state"] == "executing"
        assert loop["context"]["fsm"]["usage"]["reworkRounds"] == 0
```

- [ ] **Step 2: Correr el test y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_fsm_matrix.py','-q','-p','no:randomly']))"`
Expected: FAIL — `test_qa_running_can_hand_off_to_the_next_story` con `AssertionError` (arista ausente) y `test_next_story_transition_resets_the_rework_budget` con `TypeError: ... unexpected keyword argument 'fsm_patch'`.

- [ ] **Step 3: Implementación mínima**

En `coordinator.py:169` reemplazar:

```python
    "qa_running": {"security_running", "reworking", "blocked", "cancelled"},
```

por:

```python
    "qa_running": {"security_running", "reworking", "executing", "blocked", "cancelled"},
```

En `_transition_run_state` (`coordinator.py:1122-1147`): agregar el parámetro `fsm_patch: dict[str, Any] | None = None,` después de `thread_id: str | None = None,` (línea `:1132`), agregar docstring inmediatamente bajo la firma (el método hoy no tiene docstring; los comentarios `#` existentes de `:1134-1136` se conservan tal cual por ser preexistentes):

```python
        """Transiciona el loop registrando evento de loop y de hilo; punto único de cancelación.

        ``fsm_patch`` se mezcla sobre ``context['fsm']`` en la misma transacción (lo usa la arista
        ``next_story`` para reiniciar ``reworkRounds`` por historia).
        """
```

y pasar el patch a `self.transition(...)` (`:1139-1147`) agregando el argumento final `_fsm_patch=fsm_patch,`.

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_fsm_matrix.py','tests_py/test_product_loop_state_order_frontend.py','tests_py/test_product_loop_result_taxonomy.py','-q','-p','no:randomly']))"`
Expected: PASS (incluye `test_no_path_from_executing_skips_qa_and_security` intacto: desde `executing` solo se llega a `qa_running`).
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/coordinator.py tests_py/test_product_loop_fsm_matrix.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/coordinator.py tests_py/test_product_loop_fsm_matrix.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/coordinator.py tests_py/test_product_loop_fsm_matrix.py
git commit -m "Feature (ProductLoop): arista qa_running a executing para la siguiente historia y reinicio de rondas de rework" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Agotar el rework automático bloquea el loop (`qa_rework`)

**Files:**
- Modify: `local_control_center/product_loop/phases/qa_gate.py:21-27` (docstring) y `:168-172`
- Test: `tests_py/test_product_loop_coordinator.py:7398-7399` y `:8513-8536`

**Interfaces:**
- Consumes: `coordinator._block_run(loop, *, stage, reason, actor, details=None, durable_context=None, thread_id=None)` (`coordinator.py:1325`).
- Produces: `evaluate_qa_gate` devuelve un resultado `status="blocked"` con `durableRun.blockedStage == "qa_rework"` al agotar `DEFAULT_AUTO_REWORK_ROUNDS` (spec §3.2: "agota → story blocked; loop blocked"). Habilita el `retry_loop` de remediaciones que usa T7.

- [ ] **Step 1: Adaptar las anclas (test que falla)**

En `tests_py/test_product_loop_coordinator.py:7398-7399` (`test_run_user_message_opens_rework_when_qa_fails`) reemplazar:

```python
        assert result["status"] == "reworking"
        assert result["loop"]["state"] == "reworking"
```

por:

```python
        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "qa_rework"
```

(Las aserciones siguientes sobre `durableRun.rework` y `"reworking" in transitions` se conservan.)

Reemplazar la función completa `:8513-8536` por:

```python
def test_qa_failure_exhausts_auto_rework_and_blocks_the_loop(tmp_path: Path) -> None:
    runtime = _ControlledRuntime(status="qa_failed")
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "auto-rework-exhausted")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)

        result = coordinator.run_user_message(
            project_id=project["id"],
            message="Implement the onboarding dashboard while QA always fails.",
            preferred_runtime="controlled_test_runtime",
            runtime_runner=runtime,
            git_service=_GitGate(),
            product_owner_runner=_backlog_ready_po(),
            assessment_runner=_AssessmentRunner(),
            technical_lead_runner=_TechnicalLeadPlanner(),
            security_runner=_SecurityGate(),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["state"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "qa_rework"
        assert len(runtime.run_payloads) == 1 + DEFAULT_AUTO_REWORK_ROUNDS
        assert result["loop"]["context"]["fsm"]["usage"]["reworkRounds"] == 1 + DEFAULT_AUTO_REWORK_ROUNDS
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_coordinator.py','-q','-p','no:randomly','-k','opens_rework_when_qa_fails or exhausts_auto_rework']))"`
Expected: FAIL con `AssertionError: assert 'reworking' == 'blocked'` en ambos.

- [ ] **Step 3: Implementación mínima**

En `qa_gate.py`, docstring de `evaluate_qa_gate` (`:22-27`), reemplazar la primera oración del segundo párrafo por:

```python
    Devuelve un dict terminal (bloqueo, incluido el de rondas de rework agotadas en ``qa_rework``)
    o ``None`` para continuar; cuando QA falla dentro del presupuesto de rondas deja
    ``run.should_rework`` en ``True`` para que el driver re-ejecute al developer con el feedback
    compactado.
```

Reemplazar `qa_gate.py:169-172`:

```python
        if run.rework_round >= DEFAULT_AUTO_REWORK_ROUNDS:
            return coordinator._run_result(
                reworked, status=REWORK_STATE, reason=reason, evidence_package=evidence
            )
```

por:

```python
        if run.rework_round >= DEFAULT_AUTO_REWORK_ROUNDS:
            return coordinator._block_run(
                reworked,
                stage="qa_rework",
                reason=reason,
                actor=actor,
                details={
                    "status": "rework_exhausted",
                    "reason": reason,
                    "runtimeStatus": runtime_status,
                    "qaVerdict": qa_verdict,
                    "reworkRound": run.rework_round,
                    "qaResults": qa_results,
                    "evidenceRef": evidence["id"],
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
```

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_coordinator.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_remediation_blocker_experience.py','-q','-p','no:randomly']))"`
Expected: PASS (la suite del coordinator tarda varios minutos; correr en background y leer el exit real).
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/phases/qa_gate.py tests_py/test_product_loop_coordinator.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/phases/qa_gate.py tests_py/test_product_loop_coordinator.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/phases/qa_gate.py tests_py/test_product_loop_coordinator.py
git commit -m "Fix (ProductLoop): agotar el rework automático bloquea el loop en qa_rework en vez de dejarlo en reworking" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Ejecución historia por historia con estados, eventos y cursor durable

**Files:**
- Create: `local_control_center/product_loop/phases/story_loop.py`
- Modify: `local_control_center/product_loop/phases/execution.py:19` (`__all__`), `:256`, `:461-501`, append al final
- Modify: `local_control_center/product_loop/coordinator.py:92-99` (import), `:381-391` (campos), `:2296-2306` (respaldo determinista del TL), `:4194-4213` (driver), `:5006-5007` (wrapper)
- Modify: `local_control_center/product_loop/phases/team_planning.py:158` (evento `technical_lead_fallback`)
- Test: `tests_py/test_product_loop_story_execution.py`
- Modify (test existente): `tests_py/test_product_loop_coordinator.py:4716-4719` (`test_run_user_message_does_not_execute_developer_without_backlog_tasks`: el respaldo determinista también debe quedar vacío)

**Interfaces:**
- Consumes: T1 (`order_story_batches(..., story_order=...)`, `story_batch_is_done`, `story_fingerprint`, `story_progress_entry`, `STORY_STATUS_*`, `BacklogRepository.list_user_stories_for_output`), T3 (`_transition_run_state(..., fsm_patch=...)`, arista `qa_running→executing`), T4 (bloqueo `qa_rework`); `run.product_owner_output_record["id"]` (lo usa ya `execution.py:275`); `run.runtime_result["runtime"]["id"]` (runtime realmente usado, también tras failover; el runtime controlado lo reporta en `tests_py/test_product_loop_coordinator.py:263`); `BacklogRepository.update_user_story` (`backlog/repository.py:596`), `update_agent_task` (`:843`), `list_acceptance_criteria` (`:687`), `get_user_story` (`:567`); `ProductLoopRepository.get_loop` (`product_loop/repository.py:127`), `update_loop_context` (`:175`); `coordinator._record_thread_event` (`:999`), `_durable_run_context` (`:610`), `_durable_run_patch` (`:623`), `_execute_developer_phase` (`:5003`), `_capture_review_evidence` (`:5006`), `_evaluate_qa_gate` (`:4215`), `_block_run` (`:1325`), `_record_resource_learning_best_effort`, `_attach_resource_learning_to_result` (usados hoy en `execution.py:486-500`).
- Produces:
  - `story_loop.run_story_batches(coordinator, run) -> dict[str, Any] | None`
  - `execution.block_without_changed_files(coordinator, run, review, *, runtime_status: str) -> dict[str, Any]`
  - `execution.aggregate_story_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]`
  - `execution.capture_cumulative_review(coordinator, run) -> dict[str, Any] | None` (versión agregada; T6 agrega la rama git)
  - `ProductLoopCoordinator._capture_cumulative_review(run)`
  - Campos `_UserMessageRun.active_story_tasks: list[dict[str, Any]] | None = None`, `story_noop: bool = False`, `story_reviews: list[dict[str, Any]] = field(default_factory=list)`, `story_qa_results: list[Any] = field(default_factory=list)`, `story_evidence_ids: list[str] = field(default_factory=list)`
  - Al cerrar todas las historias: `run.qa_results` = QA del intento aprobado de **cada** historia (en orden) y `run.evidence_ids` = evidencias de cada historia sin duplicados, que consumen `security.py:40` y `approval.py:40-41,58,118` (hoy cada historia los sobrescribe: `execution.py:520`, `qa_gate.py:272`).
  - `ProductLoopCoordinator._with_deterministic_fallback(payload, stories, specs, dependency_specs) -> tuple[list[Any], list[Any], list[str]]`: cuando un TL LLM (`technical_lead_runner.generate_agent_tasks`) deja historias del PO sin tareas, las planifica con `TechnicalLeadPlanner().plan` (el planner por defecto, `coordinator.py:2299`), marca sus tareas con `metadata.technicalLeadFallback=True` y expone los ids cubiertos en `plan_sink["technicalLeadFallbackStoryIds"]`.
  - Evento de hilo `technical_lead_fallback` `{loopId, storyId}` por cada historia cubierta por el respaldo (emitido en `team_planning.py`, que conoce `thread_id`).
  - Bloqueo `technical_lead` fail-closed **solo** si una historia sigue sin tareas tras el respaldo determinista (hoy `team_planning.py:158` solo exige que exista alguna tarea global).
  - Trigger `stories_already_done` (`executing → qa_running`) cuando no queda ninguna historia pendiente: no se re-ejecuta el developer (spec §3.2 y §1.5).
  - Evento de hilo `story_progress` con payload `{loopId, storyId, title, status, index, total[, outcome]}`
  - Cursor `durableRun.storyProgress: list[story_progress_entry]`
  - `task_id` por historia: `<base>:s<k>` y en rework `<base>:s<k>:r<n>` (el prefijo `product-loop-<suffix>` se conserva, así `threads/cost_performance.py:39-45` sigue correlacionando costos).

Invariante crítico (no romper): `_durable_run_patch(loop, ...)` reemplaza `durableRun` completo con el del `loop` que recibe, así que `run.loop` debe refrescarse **después de cada escritura del cursor** (lo hacen `_save_progress`/`_update_progress`), y toda transición posterior usa `run.loop`. Un `run.loop` viejo borraría `storyProgress`.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests_py/test_product_loop_story_execution.py`:

```python
"""Ejecución por historia del product loop: un run del developer por historia, con QA y rework propios.

Fija los criterios del diseño 2026-09-22 (§1 y §3): N historias → N runs acotados a su historia,
estados ``todo → in_progress → qa → done`` persistidos con eventos ``story_progress`` y cursor
durable, presupuesto de rework por historia, historia sin cambios cerrada como ``noop`` y bloqueo
con cursor al agotar el rework.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import DEFAULT_AUTO_REWORK_ROUNDS, ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository
from tests_py.test_product_loop_coordinator import (
    _AssessmentRunner,
    _ControlledRuntime,
    _GitGate,
    _product_owner_result,
    _ProductOwnerRunner,
    _RoleTaskPlanner,
    _SecurityGate,
    _seed_ai_resource,
    _TechnicalLeadPlanner,
    _workspace_project,
)
from tests_py.test_product_loop_coordinator import _controlled_ollama_daemon as _controlled_ollama_daemon

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

SECOND_STORY = {
    "epicTitle": "Onboarding readiness",
    "title": "Setup reminders",
    "asA": "operations lead",
    "iWant": "to be reminded of pending setup steps",
    "soThat": "I do not forget onboarding work",
    "businessValue": "medium",
    "acceptanceCriteria": ["Given a pending step, when a day passes, then a reminder is shown."],
}


def _two_story_po() -> _ProductOwnerRunner:
    result = _product_owner_result("backlog_ready")
    stories = [*result["userStories"], dict(SECOND_STORY)]
    result["userStories"] = stories
    result["output"]["userStories"] = stories
    return _ProductOwnerRunner(result)


class _PerStoryRuntime(_ControlledRuntime):
    """Runtime controlado cuyo resultado depende de la posición de la historia del payload."""

    def __init__(
        self,
        *,
        qa_failures: dict[int, int] | None = None,
        changed_files: dict[int, list[str]] | None = None,
    ) -> None:
        super().__init__()
        self.qa_failures = dict(qa_failures or {})
        self.changed_by_story = dict(changed_files or {})
        self.story_order: list[str] = []

    def story_position(self, payload: dict[str, Any]) -> int:
        story_id = str(payload["agentTasks"][0]["storyId"])
        if story_id not in self.story_order:
            self.story_order.append(story_id)
        return self.story_order.index(story_id) + 1

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.story_position(payload)
        remaining = self.qa_failures.get(position, 0)
        self.status_value = "qa_failed" if remaining else "completed"
        if remaining:
            self.qa_failures[position] = remaining - 1
        self.changed_files = self.changed_by_story.get(position, [f"src/story_{position}.py"])
        result = super().run(payload)
        result["evidencePackage"] = {**result["evidencePackage"], "id": f"evidence-story-{position}"}
        result["qaResults"] = [{**item, "command": f"qa story {position}"} for item in self.qa_results]
        return result


def _run(
    coordinator: ProductLoopCoordinator,
    project: dict[str, Any],
    runtime: _ControlledRuntime,
    *,
    security: _SecurityGate | None = None,
    technical_lead: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return coordinator.run_user_message(
        project_id=project["id"],
        message="Implement the onboarding readiness experience story by story.",
        preferred_runtime="controlled_test_runtime",
        runtime_runner=runtime,
        git_service=_GitGate(),
        product_owner_runner=_two_story_po(),
        assessment_runner=_AssessmentRunner(),
        technical_lead_runner=technical_lead or _TechnicalLeadPlanner(),
        security_runner=security or _SecurityGate(),
        **kwargs,
    )


def test_each_story_gets_its_own_scoped_developer_run(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-scoped")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        assert len(runtime.run_payloads) == 2
        first, second = runtime.run_payloads
        assert {task["storyId"] for task in first["agentTasks"]} == {runtime.story_order[0]}
        assert {task["storyId"] for task in second["agentTasks"]} == {runtime.story_order[1]}
        assert "Readiness checklist" in first["storySpecs"]
        assert "Setup reminders" not in first["storySpecs"]
        assert "Setup reminders" in second["storySpecs"]
        assert "Readiness checklist" not in second["storySpecs"]
        assert first["taskId"].endswith(":s1")
        assert second["taskId"].endswith(":s2")
        triggers = [item.get("trigger") for item in result["transitions"]]
        assert triggers.count("next_story") == 1
        review = result["loop"]["context"]["durableRun"]["review"]
        assert review["changedFiles"] == ["src/story_1.py", "src/story_2.py"]


def test_story_statuses_events_and_cursor_follow_each_story(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-statuses")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        backlog = BacklogRepository(connection)
        assert {story["status"] for story in backlog.list_user_stories(project_id=project["id"])} == {"done"}
        assert {task["status"] for task in backlog.list_agent_tasks(project_id=project["id"])} == {"done"}
        thread_id = result["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        progress_events = [
            (
                event["payload"]["storyId"],
                event["payload"]["status"],
                event["payload"]["index"],
                event["payload"]["total"],
            )
            for event in ThreadsRepository(connection).list_events(thread_id)
            if event["type"] == "story_progress"
        ]
        first, second = runtime.story_order
        assert progress_events == [
            (first, "in_progress", 1, 2),
            (first, "qa", 1, 2),
            (first, "done", 1, 2),
            (second, "in_progress", 2, 2),
            (second, "qa", 2, 2),
            (second, "done", 2, 2),
        ]
        cursor = result["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["storyId"], entry["status"], entry["runs"], entry["qaVerdict"]) for entry in cursor] == [
            (first, "done", 1, "passed"),
            (second, "done", 1, "passed"),
        ]
        assert all(len(entry["fingerprint"]) == 64 for entry in cursor)


def test_the_rework_budget_resets_for_every_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _PerStoryRuntime(qa_failures={1: 1, 2: 1})
    original_start = ProductLoopCoordinator.start

    def start_with_policy(self: ProductLoopCoordinator, **kwargs: Any) -> dict[str, Any]:
        return original_start(self, **{**kwargs, "max_rework_rounds": 1})

    monkeypatch.setattr(ProductLoopCoordinator, "start", start_with_policy)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-rework-budget")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        suffixes = [payload["taskId"].split(":", 1)[1] for payload in runtime.run_payloads]
        assert suffixes == ["s1", "s1:r1", "s2", "s2:r1"]


def test_a_story_that_exhausts_rework_blocks_the_loop_and_keeps_the_cursor(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime(qa_failures={2: 99})
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-exhausted")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "blocked"
        durable = result["loop"]["context"]["durableRun"]
        assert durable["blockedStage"] == "qa_rework"
        assert len(runtime.run_payloads) == 1 + 1 + DEFAULT_AUTO_REWORK_ROUNDS
        first, second = runtime.story_order
        assert [(entry["storyId"], entry["status"]) for entry in durable["storyProgress"]] == [
            (first, "done"),
            (second, "blocked"),
        ]
        assert durable["storyProgress"][1]["reason"]
        backlog = BacklogRepository(connection)
        assert backlog.get_user_story(first)["status"] == "done"
        blocked_story = backlog.get_user_story(second)
        assert blocked_story["status"] == "blocked"
        assert blocked_story["metadata"]["blockedReason"]


def test_a_story_without_changes_is_closed_as_noop(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime(changed_files={1: []})
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-noop")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        first, _second = runtime.story_order
        story = BacklogRepository(connection).get_user_story(first)
        assert story["status"] == "done"
        assert story["metadata"]["outcome"] == "noop"
        assert "story_noop" in [item.get("trigger") for item in result["transitions"]]
        assert result["loop"]["context"]["durableRun"]["review"]["changedFiles"] == ["src/story_2.py"]


def test_approval_carries_the_qa_and_evidence_of_every_story(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-evidence")

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime)

        assert result["status"] == "awaiting_approval"
        durable = result["loop"]["context"]["durableRun"]
        job = JobsRepository(connection).get_job(durable["approval"]["jobId"])
        action = JobsRepository(connection).get_action_request(durable["approval"]["actionRequestId"])
        for payload in (job["payload"], action["payload"]):
            assert "evidence-story-1" in payload["evidenceRefs"]
            assert "evidence-story-2" in payload["evidenceRefs"]
        security_evidence = EvidenceRepository(connection).get_evidence_package(job["payload"]["evidenceRefs"][-1])
        commands = [item.get("command") for item in security_evidence["testResults"]]
        assert "qa story 1" in commands
        assert "qa story 2" in commands
        cursor = durable["storyProgress"]
        assert {entry["runtime"] for entry in cursor} == {"controlled_test_runtime"}


def test_a_story_without_llm_tasks_is_planned_by_the_deterministic_fallback(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-tl-fallback")

        result = _run(
            ProductLoopCoordinator(connection, root=tmp_path),
            project,
            runtime,
            technical_lead=_RoleTaskPlanner(["backend_engineer"]),
        )

        durable = result["loop"]["context"]["durableRun"]
        assert durable.get("blockedStage") != "technical_lead"
        assert len(runtime.run_payloads) == 2
        backlog = BacklogRepository(connection)
        reminders = next(
            story
            for story in backlog.list_user_stories(project_id=project["id"])
            if story["title"] == "Setup reminders"
        )
        fallback_story_ids = {
            task["storyId"]
            for task in backlog.list_agent_tasks(project_id=project["id"])
            if task["metadata"].get("technicalLeadFallback") is True
        }
        assert fallback_story_ids == {reminders["id"]}
        thread_id = durable["thread"]["projectThreadId"]
        fallback_events = [
            event["payload"]["storyId"]
            for event in ThreadsRepository(connection).list_events(thread_id)
            if event["type"] == "technical_lead_fallback"
        ]
        assert fallback_events == [reminders["id"]]


def test_a_story_uncovered_by_llm_and_fallback_blocks_before_any_developer_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyDeterministicPlanner:
        def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"agent_tasks": [], "task_dependencies": []}

    monkeypatch.setattr(coordinator_module, "TechnicalLeadPlanner", _EmptyDeterministicPlanner)
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-uncovered")

        result = _run(
            ProductLoopCoordinator(connection, root=tmp_path),
            project,
            runtime,
            technical_lead=_RoleTaskPlanner(["backend_engineer"]),
        )

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "technical_lead"
        assert "Setup reminders" in result["reason"]
        assert runtime.run_payloads == []
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','-q','-p','no:randomly']))"`
Expected: FAIL — `assert 1 == 2` en `len(runtime.run_payloads)` (hoy un solo run con todas las tareas), `KeyError: 'storyProgress'`, el test `noop` bloquea en `review` (hoy "sin cambios" bloquea), el de evidencias falla en `assert "evidence-story-2" in payload["evidenceRefs"]` (hoy hay un solo run, así que solo existe `evidence-story-1`), el del respaldo determinista falla en `assert fallback_story_ids == {reminders["id"]}` (hoy no hay respaldo: "Setup reminders" queda sin tareas y sin evento `technical_lead_fallback`) y el de historia sin tareas tras el respaldo termina `awaiting_approval` en vez de `blocked` (hoy solo se exige alguna tarea global, `team_planning.py:158`).

- [ ] **Step 3: Implementación mínima**

3a. Crear `local_control_center/product_loop/phases/story_loop.py`:

```python
"""Fase de ejecución por historia: el developer trabaja una historia a la vez, cada una con su QA.

Fase del pipeline spec-driven (docs/superpowers/specs/2026-09-22-per-story-execution-board-design.md
§3): agrupa las ``agent_tasks`` por historia en el orden de ``backlog.board.order_story_batches`` y,
por cada historia pendiente, corre developer → evidencia → gate QA con su propio presupuesto de
rework (la arista ``qa_running → executing`` con trigger ``next_story`` reinicia ``reworkRounds``).
Cada cambio de estado se persiste en ``user_stories``/``agent_tasks`` (nunca en
``agent_assignments``: sus estados de inicio disparan gates de handoff), en el cursor durable
``durableRun.storyProgress`` y como evento de hilo ``story_progress``. Una historia sin cambios queda
``done`` con ``metadata.outcome=noop``. Los helpers del coordinator se importan de forma diferida para
evitar el ciclo de imports (el coordinator importa este módulo al cargar).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.backlog.board import (
    STORY_STATUS_BLOCKED,
    STORY_STATUS_DONE,
    STORY_STATUS_IN_PROGRESS,
    STORY_STATUS_QA,
    STORY_STATUS_TODO,
    order_story_batches,
    story_batch_is_done,
    story_fingerprint,
    story_progress_entry,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun

__all__ = ["run_story_batches"]

STORY_EVENT_TYPE = "story_progress"
NOOP_OUTCOME = "noop"


def run_story_batches(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any] | None:
    """Ejecuta las historias pendientes en orden: developer → evidencia → QA con rework por historia.

    Devuelve el resultado terminal cuando una historia se bloquea (la historia queda ``blocked`` y el
    cursor lo registra), cuando una historia del output del PO sigue sin tareas incluso tras el respaldo
    determinista del Technical Lead (bloqueo ``technical_lead``: N historias exigen N runs) o ``None`` cuando todas quedaron ``done``: el loop
    termina en ``qa_running`` con la QA y la evidencia de cada historia en ``run.qa_results`` /
    ``run.evidence_ids``, listo para el diff acumulado y Security. Si ninguna historia está pendiente
    no se re-ejecuta el developer: el loop pasa directo a ``qa_running`` (``stories_already_done``).
    """
    stories_by_id, story_order = _backlog_stories(coordinator, run)
    batches = order_story_batches(run.agent_tasks, stories_by_id, story_order=story_order)
    uncovered = [batch for batch in batches if batch["story"] is not None and not batch["tasks"]]
    if uncovered:
        return _block_uncovered_stories(coordinator, run, uncovered)
    fingerprints = {batch["storyId"]: _batch_fingerprint(coordinator, batch) for batch in batches}
    pending = [batch for batch in batches if not story_batch_is_done(batch)]
    positions = {batch["storyId"]: index for index, batch in enumerate(batches, start=1)}
    _initialize_progress(coordinator, run, batches, pending=pending, fingerprints=fingerprints)
    root_task_id = run.base_task_id
    run.story_qa_results = []
    run.story_evidence_ids = []
    if not pending:
        _skip_finished_backlog(coordinator, run, total=len(batches))
    for order, batch in enumerate(pending):
        result = _run_story(
            coordinator,
            run,
            batch,
            index=positions[batch["storyId"]],
            total=len(batches),
            root_task_id=root_task_id,
            first=order == 0,
        )
        if result is not None:
            return result
    run.active_story_tasks = None
    run.task_id = root_task_id
    run.base_task_id = root_task_id
    run.qa_results = list(run.story_qa_results)
    run.evidence_ids = list(dict.fromkeys(run.story_evidence_ids))
    return None


def _skip_finished_backlog(coordinator: ProductLoopCoordinator, run: _UserMessageRun, *, total: int) -> None:
    run.loop = coordinator._transition_run_state(
        run.loop,
        to_state="qa_running",
        reason=f"All {total} stories are already done; the DeveloperAgent has nothing left to execute.",
        trigger="stories_already_done",
        actor=run.actor,
        context_patch=coordinator._durable_run_patch(run.loop, {"status": "qa_running"}),
        thread_id=run.thread_id,
    )


def _block_uncovered_stories(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, uncovered: list[dict[str, Any]]
) -> dict[str, Any]:
    titles = ", ".join(_batch_title(batch) or batch["storyId"] for batch in uncovered)
    reason = (
        "TechnicalLead and the deterministic TechnicalLeadPlanner fallback did not plan agent_tasks "
        f"for every user story; uncovered: {titles}."
    )
    return coordinator._block_run(
        run.loop,
        stage="technical_lead",
        reason=reason,
        actor=run.actor,
        details={
            "reason": reason,
            "uncoveredStoryIds": [batch["storyId"] for batch in uncovered],
            "productOwnerOutputId": (run.product_owner_output_record or {}).get("id"),
            "agentTaskIds": [task["id"] for task in run.agent_tasks],
        },
        thread_id=run.thread_id,
    )


def _run_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    *,
    index: int,
    total: int,
    root_task_id: str,
    first: bool,
) -> dict[str, Any] | None:
    if not first:
        run.loop = coordinator._transition_run_state(
            run.loop,
            to_state="executing",
            reason=f"Story {index} of {total}: executing the DeveloperAgent for the next story.",
            trigger="next_story",
            actor=run.actor,
            context_patch=coordinator._durable_run_patch(run.loop, {"status": "executing"}),
            thread_id=run.thread_id,
            fsm_patch={"usage": {"reworkRounds": 0}},
        )
    run.active_story_tasks = batch["tasks"]
    run.rework_round = 0
    run.rework_feedback = None
    run.base_task_id = f"{root_task_id}:s{index}"
    run.task_id = run.base_task_id
    _mark_story(coordinator, run, batch, STORY_STATUS_IN_PROGRESS, index=index, total=total)
    runs = 0
    while True:
        runs += 1
        run.story_noop = False
        result = coordinator._execute_developer_phase(run)
        if result is None:
            result = coordinator._capture_review_evidence(run)
        if result is not None:
            return _block_story(coordinator, run, batch, result, index=index, total=total, runs=runs)
        run.story_reviews.append(run.review)
        if run.story_noop:
            _finish_noop_story(coordinator, run, batch, index=index, total=total, runs=runs)
            return None
        _mark_story(coordinator, run, batch, STORY_STATUS_QA, index=index, total=total, runs=runs)
        result = coordinator._evaluate_qa_gate(run)
        if result is not None:
            return _block_story(coordinator, run, batch, result, index=index, total=total, runs=runs)
        if not run.should_rework:
            run.story_qa_results.extend(run.qa_results)
            run.story_evidence_ids.extend(run.evidence_ids)
            _mark_story(
                coordinator,
                run,
                batch,
                STORY_STATUS_DONE,
                index=index,
                total=total,
                runs=runs,
                qa_verdict=run.qa_verdict,
                commit=_review_commit(run.review),
            )
            return None
        _mark_story(coordinator, run, batch, STORY_STATUS_IN_PROGRESS, index=index, total=total, runs=runs)


def _finish_noop_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    *,
    index: int,
    total: int,
    runs: int,
) -> None:
    run.loop = coordinator._transition_run_state(
        run.loop,
        to_state="qa_running",
        reason=f"Story {index} of {total} produced no changes; there is nothing for QA to verify.",
        trigger="story_noop",
        actor=run.actor,
        context_patch=coordinator._durable_run_patch(
            run.loop, {"status": "qa_running", "runtimeResult": run.runtime_result}
        ),
        thread_id=run.thread_id,
    )
    run.story_evidence_ids.extend(run.evidence_ids)
    _mark_story(
        coordinator, run, batch, STORY_STATUS_DONE, index=index, total=total, runs=runs, outcome=NOOP_OUTCOME
    )


def _block_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    result: dict[str, Any],
    *,
    index: int,
    total: int,
    runs: int,
) -> dict[str, Any]:
    reason = str(result.get("reason") or "")
    _mark_story(
        coordinator, run, batch, STORY_STATUS_BLOCKED, index=index, total=total, runs=runs, reason=reason
    )
    return {**result, "loop": coordinator.repository.get_loop(run.loop["id"])}


def _mark_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    status: str,
    *,
    index: int,
    total: int,
    runs: int = 0,
    outcome: str | None = None,
    reason: str | None = None,
    qa_verdict: str | None = None,
    commit: str | None = None,
) -> None:
    clean_reason = str(redact_secrets(reason)) if reason else None
    _write_statuses(coordinator, batch, status, outcome=outcome, reason=clean_reason)
    _update_progress(
        coordinator,
        run,
        batch["storyId"],
        {
            "status": status,
            "runs": runs,
            "runtime": _runtime_id(run),
            "outcome": outcome,
            "reason": clean_reason,
            "qaVerdict": qa_verdict,
            "commit": commit,
        },
    )
    payload: dict[str, Any] = {
        "loopId": run.loop["id"],
        "storyId": batch["storyId"],
        "title": _batch_title(batch),
        "status": status,
        "index": index,
        "total": total,
    }
    if outcome:
        payload["outcome"] = outcome
    coordinator._record_thread_event(
        thread_id=run.thread_id, event_type=STORY_EVENT_TYPE, agent_role="developer", payload=payload
    )


def _write_statuses(
    coordinator: ProductLoopCoordinator,
    batch: dict[str, Any],
    status: str,
    *,
    outcome: str | None = None,
    reason: str | None = None,
) -> None:
    story = batch.get("story")
    if story is not None:
        metadata = {
            key: value
            for key, value in (story.get("metadata") or {}).items()
            if key not in {"outcome", "blockedReason"}
        }
        if outcome:
            metadata["outcome"] = outcome
        if reason and status == STORY_STATUS_BLOCKED:
            metadata["blockedReason"] = reason
        updated = coordinator.backlog.update_user_story(story["id"], {"status": status, "metadata": metadata})
        story["status"] = updated["status"]
        story["metadata"] = updated["metadata"]
    for task in batch.get("tasks") or []:
        task["status"] = coordinator.backlog.update_agent_task(task["id"], {"status": status})["status"]


def _initialize_progress(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batches: list[dict[str, Any]],
    *,
    pending: list[dict[str, Any]],
    fingerprints: dict[str, str],
) -> None:
    pending_ids = {batch["storyId"] for batch in pending}
    entries: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        if batch["storyId"] in pending_ids:
            _write_statuses(coordinator, batch, STORY_STATUS_TODO)
            status, outcome = STORY_STATUS_TODO, None
        else:
            status = STORY_STATUS_DONE
            outcome = ((batch.get("story") or {}).get("metadata") or {}).get("outcome")
        entries.append(
            story_progress_entry(
                story_id=batch["storyId"],
                index=index,
                title=_batch_title(batch),
                status=status,
                fingerprint=fingerprints.get(batch["storyId"], ""),
                outcome=outcome,
            )
        )
    _save_progress(coordinator, run, entries)


def _update_progress(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, story_id: str, changes: dict[str, Any]
) -> None:
    loop = coordinator.repository.get_loop(run.loop["id"])
    entries = [
        dict(entry)
        for entry in coordinator._durable_run_context(loop).get("storyProgress") or []
        if isinstance(entry, dict)
    ]
    for entry in entries:
        if entry.get("storyId") == story_id:
            entry.update({key: value for key, value in changes.items() if value is not None})
    _save_progress(coordinator, run, entries)


def _save_progress(coordinator: ProductLoopCoordinator, run: _UserMessageRun, entries: list[dict[str, Any]]) -> None:
    loop = coordinator.repository.get_loop(run.loop["id"])
    durable = coordinator._durable_run_context(loop)
    run.loop = coordinator.repository.update_loop_context(
        loop["id"],
        context={**loop["context"], "durableRun": {**durable, "storyProgress": entries, "updatedAt": utc_now()}},
    )


def _backlog_stories(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output_id = str((run.product_owner_output_record or {}).get("id") or "")
    emitted = coordinator.backlog.list_user_stories_for_output(run.project_id, output_id) if output_id else []
    records: dict[str, dict[str, Any]] = {story["id"]: story for story in emitted}
    for task in run.agent_tasks or []:
        story_id = str(task.get("storyId") or "").strip()
        if not story_id or story_id in records:
            continue
        try:
            records[story_id] = coordinator.backlog.get_user_story(story_id)
        except KeyError:
            continue
    return records, [story["id"] for story in emitted]


def _batch_fingerprint(coordinator: ProductLoopCoordinator, batch: dict[str, Any]) -> str:
    story = batch.get("story")
    if story is None:
        return ""
    criteria = [
        str(criterion.get("criterion") or "")
        for criterion in coordinator.backlog.list_acceptance_criteria(story_id=story["id"])
    ]
    return story_fingerprint(story, criteria)


def _batch_title(batch: dict[str, Any]) -> str:
    return str((batch.get("story") or {}).get("title") or "")


def _runtime_id(run: _UserMessageRun) -> str | None:
    used = (run.runtime_result or {}).get("runtime")
    used_id = str(used.get("id") or "") if isinstance(used, dict) else ""
    return (
        used_id
        or str((run.readiness or {}).get("selectedRuntimeId") or run.effective_preferred_runtime or "")
        or None
    )


def _review_commit(review: dict[str, Any]) -> str | None:
    commit = (review or {}).get("commit")
    if not isinstance(commit, dict):
        return None
    return str(commit.get("commit") or "") or None
```

3b. `execution.py`:
- `:19` reemplazar `__all__` por:

```python
__all__ = [
    "aggregate_story_reviews",
    "block_without_changed_files",
    "capture_cumulative_review",
    "capture_review_evidence",
    "execute_developer_phase",
    "prepare_developer_execution",
]
```

- `:256` reemplazar `    agent_tasks = run.agent_tasks` (dentro de `execute_developer_phase`) por:

```python
    agent_tasks = run.active_story_tasks if run.active_story_tasks is not None else run.agent_tasks
```

- `:461-501` (bloque `if not review["changedFiles"]:` completo de `capture_review_evidence`) reemplazar por:

```python
    if not review["changedFiles"]:
        if run.active_story_tasks is not None and runtime_status in {"completed", "evidence_ready"}:
            run.runtime_status = runtime_status
            run.evidence_ids = evidence_ids
            run.review = review
            run.story_noop = True
            return None
        return block_without_changed_files(coordinator, run, review, runtime_status=runtime_status)
```

- Append al final de `execution.py`:

```python
def block_without_changed_files(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    review: dict[str, Any],
    *,
    runtime_status: str,
) -> dict[str, Any]:
    """Bloquea el run en ``review`` cuando no hay archivos cambiados reales y registra el learning.

    Guard fail-closed de "el runtime terminó sin trabajo": lo usan la captura por historia (runtime
    no sano sin cambios) y el cierre acumulado (ninguna historia del run dejó cambios).
    """
    workspace = run.workspace
    reason = (
        "Product Loop runtime completed without real changed files in the assigned worktree."
        if workspace["isolationType"] == "git_worktree"
        else "Product Loop runtime completed without changed files evidence."
    )
    blocked_result = coordinator._block_run(
        run.loop,
        stage="review",
        reason=reason,
        actor=run.actor,
        details={
            "status": str(review.get("state") or "diff_unavailable"),
            "reason": reason,
            "workspaceId": workspace["id"],
            "workspacePath": workspace["path"],
            "runtimeStatus": runtime_status,
            "runtimeResult": run.runtime_result,
            "review": review,
            "teamSchedule": run.team_schedule,
            "agentTaskIds": [task["id"] for task in run.agent_tasks],
        },
        thread_id=run.thread_id,
    )
    evidence_ref = str((blocked_result.get("evidencePackage") or {}).get("id") or "").strip()
    if evidence_ref:
        resource_learning = coordinator._record_resource_learning_best_effort(
            project_id=run.project_id,
            loop_id=run.loop["id"],
            team_schedule=run.team_schedule,
            runtime_result=run.runtime_result,
            evidence_ref=evidence_ref,
            success=False,
            rework=True,
            quality_score=0.0,
        )
        blocked_result = coordinator._attach_resource_learning_to_result(blocked_result, resource_learning)
    return blocked_result


def aggregate_story_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Une las reviews por historia del run en una vista acumulada (archivos únicos en orden)."""
    changed: list[str] = []
    patches: list[str] = []
    stats: list[str] = []
    tool_calls: list[Any] = []
    policy_ids: list[Any] = []
    for review in reviews:
        for path in review.get("changedFiles") or []:
            if path not in changed:
                changed.append(path)
        if review.get("patch"):
            patches.append(str(review["patch"]))
        if review.get("diffStat"):
            stats.append(str(review["diffStat"]))
        tool_calls.extend(review.get("toolCalls") or [])
        policy_ids.extend(review.get("policyDecisionIds") or [])
    last = reviews[-1] if reviews else {}
    patch = "\n".join(patches)
    return {
        "state": last.get("state") or "runtime_reported",
        "changedFiles": changed,
        "branch": last.get("branch"),
        "headCommit": last.get("headCommit"),
        "diffStat": "\n".join(stats)[:4000],
        "patch": patch[:12000],
        "patchSizeBytes": sum(int(review.get("patchSizeBytes") or 0) for review in reviews),
        "truncated": len(patch) > 12000 or any(bool(review.get("truncated")) for review in reviews),
        "toolCalls": tool_calls,
        "policyDecisionIds": policy_ids,
    }


def capture_cumulative_review(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Consolida la evidencia de review de todas las historias del run antes de Security.

    Devuelve el bloqueo ``review`` cuando ninguna historia dejó cambios; si no, deja en
    ``run.review`` la vista acumulada que consumen Security y la aprobación.
    """
    review = aggregate_story_reviews(run.story_reviews)
    if not review["changedFiles"]:
        return block_without_changed_files(coordinator, run, review, runtime_status=run.runtime_status)
    run.review = review
    return None
```

3c. `coordinator.py`:
- Tras `from .phases import security as security_phase` (`:98`) agregar `from .phases import story_loop as story_loop_phase` (orden alfabético del bloque).
- En `_UserMessageRun`, después de `resource_learning: dict[str, Any] = field(default_factory=dict)` (`:391`) agregar:

```python
    active_story_tasks: list[dict[str, Any]] | None = None
    story_noop: bool = False
    story_reviews: list[dict[str, Any]] = field(default_factory=list)
    story_qa_results: list[Any] = field(default_factory=list)
    story_evidence_ids: list[str] = field(default_factory=list)
```

- Reemplazar el driver `:4194-4208` (desde `run.base_task_id = run.task_id` hasta `result = self._run_security_phase(run)` inclusive) por:

```python
        run.base_task_id = run.task_id
        result = story_loop_phase.run_story_batches(self, run)
        if result is not None:
            return result
        result = self._capture_cumulative_review(run)
        if result is not None:
            return result
        result = self._run_security_phase(run)
```

- Después de `_capture_review_evidence` (`:5006-5007`) agregar:

```python
    def _capture_cumulative_review(self, run: _UserMessageRun) -> dict[str, Any] | None:
        return execution_phase.capture_cumulative_review(self, run)
```

- Respaldo determinista del Technical Lead (decisión de arquitecto 2026-09-22). En `_generate_agent_tasks`, inmediatamente después del bloque `if isinstance(planned, dict): ... else: ...` que fija `specs`/`dependency_specs` (`:2302-2306`) y antes de `tasks: list[dict[str, Any]] = []`, insertar:

```python
        if technical_lead_runner is not None and hasattr(technical_lead_runner, "generate_agent_tasks"):
            specs, dependency_specs, fallback_story_ids = self._with_deterministic_fallback(
                payload, stories, list(specs or []), list(dependency_specs or [])
            )
            if plan_sink is not None and fallback_story_ids:
                plan_sink["technicalLeadFallbackStoryIds"] = fallback_story_ids
```

  Y agregar el método justo después de `_generate_agent_tasks` (antes de `_team_mode`):

```python
    def _with_deterministic_fallback(
        self,
        payload: dict[str, Any],
        stories: list[dict[str, Any]],
        specs: list[Any],
        dependency_specs: list[Any],
    ) -> tuple[list[Any], list[Any], list[str]]:
        """Completa con el ``TechnicalLeadPlanner`` determinista las historias que el TL LLM dejó sin tareas.

        Decisión de arquitecto 2026-09-22: una historia del PO sin tareas no bloquea si el planner
        determinista (el planner por defecto) la cubre; sus tareas quedan marcadas con
        ``metadata.technicalLeadFallback``. Si tampoco la cubre, la fase por historia bloquea en
        ``technical_lead`` (``phases/story_loop.py``). Un error del planner se propaga y la fase de
        planificación bloquea en ``technical_lead`` (``phases/team_planning.py:138-156``).
        """
        covered = {str(spec.get("storyId") or "").strip() for spec in specs if isinstance(spec, dict)}
        uncovered = [story for story in stories if str(story.get("id") or "") not in covered]
        if not uncovered:
            return specs, dependency_specs, []
        fallback = TechnicalLeadPlanner().plan({**payload, "userStories": uncovered})
        fallback_specs = [
            {**spec, "metadata": {**dict(spec.get("metadata") or {}), "technicalLeadFallback": True}}
            for spec in fallback.get("agent_tasks") or []
            if isinstance(spec, dict)
        ]
        planned_story_ids = {str(spec.get("storyId") or "").strip() for spec in fallback_specs}
        fallback_story_ids = [str(story["id"]) for story in uncovered if str(story["id"]) in planned_story_ids]
        return (
            [*specs, *fallback_specs],
            [*dependency_specs, *(fallback.get("task_dependencies") or [])],
            fallback_story_ids,
        )
```

3d. `phases/team_planning.py`: justo antes de `if not agent_tasks:` (`:158`) insertar:

```python
    for story_id in raw_plan.get("technicalLeadFallbackStoryIds") or []:
        coordinator._record_thread_event(
            thread_id=thread_id,
            event_type="technical_lead_fallback",
            agent_role="technical_lead",
            payload={"loopId": loop["id"], "storyId": story_id},
        )
```

3e. Adaptar `tests_py/test_product_loop_coordinator.py:4716-4719`: con el respaldo, un TL LLM que no devuelve ninguna tarea ya no bloquea (el planner determinista cubre todas las historias). El test protege "sin tareas no corre el developer y se ofrece remediación `technical_lead_planning_failed`"; para conservar exactamente eso, el respaldo también debe quedar vacío. Reemplazar la firma y el arranque:

```python
def test_run_user_message_does_not_execute_developer_without_backlog_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _EmptyDeterministicPlanner:
        def plan(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"agent_tasks": [], "task_dependencies": []}

    monkeypatch.setattr(coordinator_module, "TechnicalLeadPlanner", _EmptyDeterministicPlanner)
    runtime = _ControlledRuntime()
    git = _GitGate()
    technical_lead = _TechnicalLeadPlanner(generate_tasks=False)
```

  (el resto del cuerpo queda igual). El archivo no importa el módulo (solo `from local_control_center.product_loop.coordinator import (...)`, `:25`): agregar `from local_control_center.product_loop import coordinator as coordinator_module` justo antes de esa línea.

- [ ] **Step 4: Correr tests y lint**

Run (nuevo + anclas de un solo run): `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','tests_py/test_product_loop_coordinator.py','tests_py/test_product_loop_fsm_matrix.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_story_spec.py','tests_py/test_thread_cost_performance.py','-q','-p','no:randomly']))"`
Expected: PASS. En particular siguen verdes las anclas `:3049`, `:3097`, `:3133`, `:5045` (backlog de una historia ⇒ `run_payloads[0]` sigue con todas las tareas), `:8478` (2 payloads, `taskId` termina en `:r1`), `:8539` (tope de operador 1 ⇒ 2 payloads y bloqueo `qa_rework`), `:8644`, `:8407`, `:5483`/`:5562` (sin cambios ⇒ bloqueo `review` con el mismo texto y learning `recorded`), y `:4716` adaptado en 3e (TL LLM y respaldo determinista vacíos ⇒ bloqueo `technical_lead` con remediación `technical_lead_planning_failed`). Cualquier otro test que use un TL LLM que omite historias (p. ej. `_RoleTaskPlanner` con backlog de varias historias) ahora recibe tareas del respaldo para las demás: adaptarlo a esa semántica sin relajar lo que protege y listarlo en el informe.
Run (regresión del loop, background por duración): `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_aido_product_loop_real_e2e.py','tests_py/test_decision_engine_integration.py','tests_py/test_execution_timeout_reconciliation.py','tests_py/test_jev_resource_selection.py','tests_py/test_product_loop_api.py','tests_py/test_product_loop_no_reask_e2e.py','tests_py/test_product_loop_persistence_reconciliation.py','tests_py/test_product_loop_reentry.py','tests_py/test_product_owner_auto_recovery.py','tests_py/test_project_constitution_slice.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_research_resolution.py','tests_py/test_runtime_risk_review.py','tests_py/test_threads_api.py','tests_py/test_threads_operator_notes.py','tests_py/test_product_loop_delivery.py','-q','-p','no:randomly']))"`
Expected: PASS. Si un test fija "un solo run del developer" con backlog de varias historias, adaptarlo a "un run por historia" sin relajar lo que protege y listarlo en el informe.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/phases/story_loop.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/team_planning.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py tests_py/test_product_loop_coordinator.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/phases/story_loop.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/team_planning.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py tests_py/test_product_loop_coordinator.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/phases/story_loop.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/team_planning.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py tests_py/test_product_loop_coordinator.py
git commit -m "Feature (ProductLoop): el developer ejecuta historia por historia con QA, estados persistidos y cursor durable" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Security y aprobación revisan el diff acumulado (worktree git)

**Files:**
- Modify: `local_control_center/product_loop/phases/execution.py` (imports `:11-14`, bloque de commit best-effort `:502-518` de `capture_review_evidence`, reemplazo de `capture_cumulative_review` creado en T5, helper nuevo)
- Modify: `local_control_center/product_loop/phases/security.py:113`
- Modify: `local_control_center/product_loop/phases/approval.py` (`approval_payload` `:119-125` y dict `approval` `:169-175`)
- Modify: `local_control_center/product_loop/coordinator.py` (campo en `_UserMessageRun`, junto a los de T5)
- Test: `tests_py/test_product_loop_story_execution.py` (append)

**Interfaces:**
- Consumes: T2 `capture_cumulative_diff` (incluye `status` = trabajo sin commitear); `coordinator._resolve_project_base_branch` (`coordinator.py:4785`); `workspace["metadata"]["gitWorktree"]["sourceCommit"]` (`git_worktrees.py:402-408`, persistido por `workspaces_projects/repository.py:176-240`); `_review_from_diff` (`coordinator.py:490`); `exclude_aido_artifacts` (`product_loop/spec_artifacts.py:57`); `commit_workspace_changes` (`git_worktrees.py:859`, estados `committed|nothing_to_commit|commit_failed|commit_failed_git_unavailable`, importado de forma diferida en `execution.py:375-377`); `write_text_artifact` (`evidence/artifacts.py:41`); `EvidenceRepository.create_artifact` (`evidence/repository.py:336`), `get_artifact_by_id` (usado en `agents/security_agent.py:217`); `JobsRepository.get_job` (`jobs_approvals/repository.py:237`), `get_action_request` (`:356`); `_git_workspace_project` (`tests_py/test_product_loop_coordinator.py:849`); `WorkspacesRepository.get_workspace` (`workspaces_projects/repository.py:456`).
- Produces: `_UserMessageRun.cumulative_patch_artifact_id: str = ""`; artefacto `kind="git_patch"` con `metadata.name == "product-loop-cumulative.diff"`; `security_payload["diffArtifactId"]` = ese artefacto; `approval_payload["patchArtifactIds"]` y `durableRun.approval.patchArtifactIds` = `[ese artefacto]` (job `product_loop_delivery_approval` y su ActionRequest); `run.review` = review del diff `<base>...HEAD` (lo usan `approval.py:46-47,124`); en un run por historia (`run.active_story_tasks is not None`) sobre `git_worktree`, un commit de historia que no queda `committed` bloquea en `review` (deja de ser best-effort); el diff acumulado con trabajo sin commitear fuera de `.aido/` bloquea en `review`.

Decisión verificada (bloqueante de la revisión cruzada): `git diff <base>...HEAD` no ve el working tree y el commit por historia hoy es best-effort (`execution.py:502-518`: si falla, el loop sigue). Sin las dos guardas, Security y la aprobación podrían revisar un diff incompleto. Se exigen ambas: commit obligatorio por historia (causa) y rechazo del diff acumulado con cambios sin commitear (defensa en profundidad; `.aido/` se ignora porque sus renders pueden quedar sin commitear en una historia `noop`, `coordinator.py:4226-4232`).

- [ ] **Step 1: Escribir el test que falla**

Append a `tests_py/test_product_loop_story_execution.py` (agregar imports arriba: `from local_control_center.security_policy.git_command_runner import git_available`, `from local_control_center.workspaces_projects import git_worktrees`, `from local_control_center.workspaces_projects.repository import WorkspacesRepository`, y `_git_workspace_project` en el import desde `tests_py.test_product_loop_coordinator`; `EvidenceRepository` y `JobsRepository` ya los importó T5):

```python
class _WorkspaceWritingRuntime(_PerStoryRuntime):
    """Escribe un archivo real por historia en el worktree asignado, como haría un runtime de código."""

    def __init__(self, connection: Any, root: Path) -> None:
        super().__init__()
        self.connection = connection
        self.root = root

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        position = self.story_position(payload)
        workspace = WorkspacesRepository(self.connection, root=self.root).get_workspace(payload["workspaceId"])
        relative = f"src/story_{position}.py"
        target = Path(workspace["path"]) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"STORY = {position}\n", encoding="utf-8")
        self.changed_by_story[position] = [relative]
        return super().run(payload)


def test_security_and_approval_review_the_cumulative_git_diff(tmp_path: Path) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    security = _SecurityGate()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "cumulative-git")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "awaiting_approval"
        review = result["loop"]["context"]["durableRun"]["review"]
        assert review["state"] == "captured"
        assert review["changedFiles"] == ["src/story_1.py", "src/story_2.py"]
        artifact_id = security.run_payloads[0]["diffArtifactId"]
        assert artifact_id
        artifact = EvidenceRepository(connection).get_artifact_by_id(artifact_id)
        assert artifact["kind"] == "git_patch"
        assert artifact["metadata"]["name"] == "product-loop-cumulative.diff"
        patch = Path(artifact["path"]).read_text(encoding="utf-8")
        assert "src/story_1.py" in patch
        assert "src/story_2.py" in patch
        approval = result["loop"]["context"]["durableRun"]["approval"]
        assert approval["patchArtifactIds"] == [artifact_id]
        jobs = JobsRepository(connection)
        assert jobs.get_job(approval["jobId"])["payload"]["patchArtifactIds"] == [artifact_id]
        assert jobs.get_action_request(approval["actionRequestId"])["payload"]["patchArtifactIds"] == [artifact_id]


def test_a_story_commit_failure_blocks_before_qa_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the per-story commit")
    security = _SecurityGate()

    def failing_commit(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "commit_failed", "stderr": "forced commit failure"}

    monkeypatch.setattr(git_worktrees, "commit_workspace_changes", failing_commit)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "commit-failure")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "review"
        assert "commit" in result["reason"].lower()
        assert "forced commit failure" in result["reason"]
        assert len(runtime.run_payloads) == 1
        assert security.run_payloads == []
        first = runtime.story_order[0]
        assert BacklogRepository(connection).get_user_story(first)["status"] == "blocked"


def test_uncommitted_work_blocks_the_cumulative_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    security = _SecurityGate()

    def commit_that_leaves_the_tree_dirty(**_kwargs: Any) -> dict[str, Any]:
        return {"status": "committed", "commit": "0" * 40}

    monkeypatch.setattr(git_worktrees, "commit_workspace_changes", commit_that_leaves_the_tree_dirty)
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "cumulative-dirty")
        runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        result = _run(ProductLoopCoordinator(connection, root=tmp_path), project, runtime, security=security)

        assert result["status"] == "blocked"
        assert result["loop"]["context"]["durableRun"]["blockedStage"] == "review"
        assert "uncommitted" in result["reason"].lower()
        assert "src/" in result["reason"]
        assert security.run_payloads == []
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','-q','-p','no:randomly','-k','cumulative_git_diff or commit_failure or uncommitted_work']))"`
Expected: FAIL — `assert artifact_id` (hoy `diffArtifactId` es `None`: el `_ControlledRuntime` no reporta `patchArtifactId` y Security solo lee el último run); el test de commit fallido termina `awaiting_approval` (hoy el commit es best-effort) y el de trabajo sin commitear también llega a Security (`security.run_payloads` no vacío).

- [ ] **Step 3: Implementación mínima**

- `execution.py` imports (`:11-14`): agregar `import hashlib` y `import uuid` antes de `from pathlib import Path`.
- En `capture_review_evidence`, dentro del bloque `if workspace["isolationType"] == "git_worktree" and review.get("changedFiles"):` (`:502-518`), inmediatamente después de la línea `        review = {**review, "commit": commit_result}` y antes de `    run.runtime_status = runtime_status`, insertar (mismo nivel de indentación que el `try:`):

```python
        if run.active_story_tasks is not None and commit_result.get("status") != "committed":
            reason = (
                "Product Loop could not commit the story changes to the thread branch, so the "
                "cumulative diff for Security and approval would be incomplete: "
                f"{commit_result.get('stderr') or commit_result.get('reason') or commit_result.get('status')}"
            )
            return coordinator._block_run(
                loop,
                stage="review",
                reason=reason,
                actor=actor,
                details={
                    "status": str(commit_result.get("status") or "commit_failed"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "runtimeStatus": runtime_status,
                    "review": review,
                    "teamSchedule": team_schedule,
                    "agentTaskIds": [task["id"] for task in agent_tasks],
                },
                thread_id=thread_id,
            )
```

  Los comentarios `#` de ese bloque (`:503-505`, "El trabajo capturado ... Best-effort", y `:516`, "Señal auxiliar") quedan desactualizados: eliminarlos (Python productivo solo admite docstrings) y agregar al docstring de `capture_review_evidence` la frase "El trabajo capturado se persiste como commit en la rama del hilo: en un run por historia el commit es obligatorio (el diff acumulado de Security y la aprobación lo necesita) y su fallo bloquea en ``review``; en un run único sigue siendo best-effort." Los nombres `loop`, `actor`, `workspace`, `runtime_status`, `team_schedule`, `agent_tasks` y `thread_id` ya son locales de la función (`execution.py:379-390`). La rama de un solo run (`active_story_tasks is None`) no cambia.
- Reemplazar la función `capture_cumulative_review` creada en T5 por:

```python
def capture_cumulative_review(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> dict[str, Any] | None:
    """Consolida la evidencia de todas las historias del run antes de Security y la aprobación.

    En un worktree git captura el diff acumulado ``<base>...HEAD`` de la rama del hilo (base
    configurada o, como respaldo, el commit desde el que se creó el worktree) y lo guarda como
    artefacto ``git_patch`` para el SecurityAgent; fuera de git agrega las reviews por historia.
    Devuelve el bloqueo ``review`` si la captura falla, si queda trabajo sin commitear fuera de
    ``.aido/`` (el rango ``<base>...HEAD`` no lo vería y Security revisaría un diff incompleto) o si
    ninguna historia dejó cambios; si no, deja la vista acumulada en ``run.review``.
    """
    from local_control_center.product_loop.coordinator import _review_from_diff
    from local_control_center.product_loop.spec_artifacts import exclude_aido_artifacts
    from local_control_center.shared.redaction import redact_secrets
    from local_control_center.workspaces_projects.git_worktrees import capture_cumulative_diff

    workspace = run.workspace
    if workspace["isolationType"] != "git_worktree":
        review = aggregate_story_reviews(run.story_reviews)
    else:
        worktree = (workspace.get("metadata") or {}).get("gitWorktree") or {}
        base_refs = [
            coordinator._resolve_project_base_branch(run.project_id),
            str(worktree.get("sourceCommit") or ""),
        ]
        try:
            diff = capture_cumulative_diff(
                Path(workspace["path"]),
                base_refs=base_refs,
                connection=coordinator.connection,
                root=run.effective_root,
                project_id=run.project_id,
                workspace_id=workspace["id"],
                task_id=f"{run.task_id}.cumulative_diff",
            )
        except Exception as error:
            diff = {"kind": "git_diff", "state": "capture_failed", "stderr": str(redact_secrets(str(error)))}
        review = _review_from_diff(diff)
        uncommitted = exclude_aido_artifacts(
            [str(item.get("path") or "") for item in diff.get("status") or [] if isinstance(item, dict)]
        )
        if review["state"] == "captured" and uncommitted:
            review = {**review, "state": "uncommitted_changes"}
            diff = {
                **diff,
                "stderr": "uncommitted changes remain outside the story commits: " + ", ".join(uncommitted[:20]),
            }
        if review["state"] != "captured":
            reason = (
                "Product Loop cumulative diff could not be captured from the thread branch: "
                f"{diff.get('stderr') or review['state']}"
            )
            return coordinator._block_run(
                run.loop,
                stage="review",
                reason=reason,
                actor=run.actor,
                details={
                    "status": str(review.get("state") or "capture_failed"),
                    "reason": reason,
                    "workspaceId": workspace["id"],
                    "workspacePath": workspace["path"],
                    "review": review,
                    "baseRefs": base_refs,
                    "teamSchedule": run.team_schedule,
                    "agentTaskIds": [task["id"] for task in run.agent_tasks],
                },
                thread_id=run.thread_id,
            )
        run.cumulative_patch_artifact_id = _write_cumulative_patch_artifact(coordinator, run, diff)
    if not review["changedFiles"]:
        return block_without_changed_files(coordinator, run, review, runtime_status=run.runtime_status)
    run.review = review
    return None


def _write_cumulative_patch_artifact(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, diff: dict[str, Any]
) -> str:
    from local_control_center.evidence.artifacts import write_text_artifact

    patch = str(diff.get("patchFull") or "")
    root = run.effective_root or coordinator.root
    if not patch or root is None:
        return ""
    artifact_id = f"artifact-{uuid.uuid4()}"
    artifact_file = write_text_artifact(root=Path(root), artifact_id=artifact_id, suffix=".patch", content=patch)
    artifact = coordinator.evidence.create_artifact(
        artifact_id=artifact_id,
        project_id=run.project_id,
        evidence_package_id=None,
        kind="git_patch",
        path=artifact_file["path"],
        content_hash=artifact_file["hash"] or hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        metadata={
            "name": "product-loop-cumulative.diff",
            "source": "product_loop_coordinator",
            "mimeType": "text/x-diff",
            "sizeBytes": artifact_file["sizeBytes"],
            "hashAlgorithm": "sha256",
            "baseRef": diff.get("baseRef"),
            "loopId": run.loop["id"],
        },
    )
    return str(artifact["id"])
```

- `security.py:113` reemplazar:

```python
    patch_artifact_id = str((runtime_result.get("diffSummary") or {}).get("patchArtifactId") or "")
```

por:

```python
    patch_artifact_id = run.cumulative_patch_artifact_id or str(
        (runtime_result.get("diffSummary") or {}).get("patchArtifactId") or ""
    )
```

- `approval.py`: reemplazar el literal `approval_payload = {...}` (`:119-125`) por:

```python
    approval_payload = {
        "loopId": loop["id"],
        "workspaceId": workspace["id"],
        "evidenceRefs": approval_evidence_ids,
        "diffRefs": [diff_ref],
        "changedFiles": review["changedFiles"],
    }
    if run.cumulative_patch_artifact_id:
        approval_payload["patchArtifactIds"] = [run.cumulative_patch_artifact_id]
```

  y en el dict `approval = {...}` (`:169-175`) agregar tras `"diffRefs": [diff_ref],` la línea `"patchArtifactIds": approval_payload.get("patchArtifactIds") or [],`. Actualizar el docstring de `finalize_delivery_approval`: "La aprobación referencia el artefacto ``git_patch`` del diff acumulado de todas las historias (``patchArtifactIds``) cuando el run lo capturó."
- `coordinator.py`, en `_UserMessageRun` junto a los campos de T5 agregar `cumulative_patch_artifact_id: str = ""`.

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','tests_py/test_product_loop_coordinator.py','tests_py/test_git_cumulative_diff.py','tests_py/test_product_loop_delivery.py','tests_py/test_execution_boundary_architecture.py','-q','-p','no:randomly']))"`
Expected: PASS (`:5483` sigue bloqueando en `review` con "without real changed files": base `dev` inexistente ⇒ respaldo `sourceCommit` ⇒ diff vacío). Si un test existente con worktree git dependía de un commit fallido tolerado en un run por historia, investigar la causa del fallo del commit (policy/identidad git) y corregirla; nunca relajar la guarda.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py local_control_center/product_loop/phases/approval.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py local_control_center/product_loop/phases/approval.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py local_control_center/product_loop/phases/approval.py local_control_center/product_loop/coordinator.py tests_py/test_product_loop_story_execution.py
git commit -m "Feature (ProductLoop): Security y aprobación revisan el diff acumulado de todas las historias" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Un reintento salta las historias ya terminadas en el loop de origen

**Files:**
- Modify: `local_control_center/product_loop/phases/story_loop.py` (`__all__`, `run_story_batches`, `_initialize_progress`, helpers nuevos)
- Test: `tests_py/test_product_loop_story_execution.py` (append)

**Interfaces:**
- Consumes: `run.request_meta["retryOfLoopId"]` (lo inyecta `remediations/service.py:1732-1740` y lo deja pasar `phases/intake.py:105`); cursor `durableRun.storyProgress[].fingerprint|status` del loop origen (T5); `story_fingerprint` (T1); `_skip_finished_backlog` (T5, trigger `stories_already_done`); `_WorkspaceWritingRuntime` y `_git_workspace_project` (T6).
- Produces: `match_carried_over(batches: list[dict[str, Any]], fingerprints: dict[str, str], done_entries: list[dict[str, Any]]) -> set[str]` (pura, emparejamiento uno a uno); `_carry_over_from_retry_source(coordinator, run, batches, fingerprints) -> set[str]`; historias arrastradas quedan `done` con `metadata.outcome = "carried_over"` y entrada de cursor `status="done", outcome="carried_over"`; si todas quedan arrastradas no corre ningún developer (`pending == []`).

Hecho verificado que motiva la huella: un `retry_loop` re-ejecuta el mensaje completo (PO incluido) y `_persist_product_owner_output_record` (`coordinator.py:1900-1936`) crea un output nuevo ⇒ filas de historia nuevas; reconocer "la misma historia" exige comparar contenido, no `storyId`. Guardas: el loop origen debe ser del mismo proyecto **y** del mismo hilo. El emparejamiento es **uno a uno** (cada entrada `done` del origen consume a lo sumo una historia actual): con un conjunto de huellas, una sola historia terminada saltaría todas las historias actuales de contenido idéntico.

- [ ] **Step 1: Escribir el test que falla**

Append a `tests_py/test_product_loop_story_execution.py` (agregar `from local_control_center.backlog.board import story_fingerprint` y `from local_control_center.product_loop.phases.story_loop import match_carried_over` a los imports):

```python
def test_a_retry_skips_stories_already_done_in_the_source_loop(tmp_path: Path) -> None:
    first_runtime = _PerStoryRuntime(qa_failures={2: 99})
    retry_runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-retry")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked = _run(coordinator, project, first_runtime)
        assert blocked["status"] == "blocked"
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]

        retried = _run(
            coordinator,
            project,
            retry_runtime,
            thread_id=thread_id,
            run_metadata={"retryOfLoopId": blocked["loop"]["id"], "functionalityDecision": "continue_existing"},
        )

        assert retried["status"] == "awaiting_approval"
        assert len(retry_runtime.run_payloads) == 1
        payload = retry_runtime.run_payloads[0]
        assert "Setup reminders" in payload["storySpecs"]
        assert "Readiness checklist" not in payload["storySpecs"]
        assert payload["taskId"].endswith(":s2")
        cursor = retried["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["status"], entry["outcome"]) for entry in cursor] == [
            ("done", "carried_over"),
            ("done", None),
        ]


def test_a_retry_marker_from_another_thread_never_skips_stories(tmp_path: Path) -> None:
    runtime = _PerStoryRuntime()
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _workspace_project(connection, tmp_path, "per-story-foreign-retry")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        emitted = _two_story_po().result["userStories"]
        progress = [
            {
                "storyId": f"foreign-{index}",
                "index": index,
                "title": story["title"],
                "status": "done",
                "fingerprint": story_fingerprint(story, story["acceptanceCriteria"]),
                "outcome": None,
            }
            for index, story in enumerate(emitted, start=1)
        ]
        foreign = coordinator.repository.create_loop(
            {
                "projectId": project["id"],
                "title": "Foreign loop",
                "state": "blocked",
                "status": "blocked",
                "context": {"durableRun": {"thread": {"projectThreadId": "thread-foreign"}, "storyProgress": progress}},
            }
        )

        result = _run(
            coordinator,
            project,
            runtime,
            run_metadata={"retryOfLoopId": foreign["id"], "functionalityDecision": "continue_existing"},
        )

        assert result["status"] == "awaiting_approval"
        assert len(runtime.run_payloads) == 2


def test_a_retry_with_every_story_done_runs_no_developer(tmp_path: Path) -> None:
    if not git_available():
        pytest.skip("git CLI is required for the cumulative worktree diff")
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection, connection:
        initialize_platform_schema(connection)
        _seed_ai_resource(connection)
        project = _git_workspace_project(connection, tmp_path, "per-story-retry-all-done")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked = _run(
            coordinator,
            project,
            _WorkspaceWritingRuntime(connection, tmp_path),
            security=_SecurityGate(verdict="blocked", reason="Critical security finding blocks completion."),
        )
        assert blocked["status"] == "blocked"
        assert {entry["status"] for entry in blocked["loop"]["context"]["durableRun"]["storyProgress"]} == {"done"}
        thread_id = blocked["loop"]["context"]["durableRun"]["thread"]["projectThreadId"]
        retry_runtime = _WorkspaceWritingRuntime(connection, tmp_path)

        retried = _run(
            coordinator,
            project,
            retry_runtime,
            thread_id=thread_id,
            run_metadata={"retryOfLoopId": blocked["loop"]["id"], "functionalityDecision": "continue_existing"},
        )

        assert retried["status"] == "awaiting_approval"
        assert retry_runtime.run_payloads == []
        assert "stories_already_done" in [item.get("trigger") for item in retried["transitions"]]
        cursor = retried["loop"]["context"]["durableRun"]["storyProgress"]
        assert [(entry["status"], entry["outcome"]) for entry in cursor] == [
            ("done", "carried_over"),
            ("done", "carried_over"),
        ]
        assert retried["loop"]["context"]["durableRun"]["review"]["changedFiles"] == [
            "src/story_1.py",
            "src/story_2.py",
        ]


def test_carried_over_matching_is_one_to_one_by_fingerprint() -> None:
    batches = [
        {"storyId": "current-1", "story": {"id": "current-1"}, "tasks": [{"id": "t1"}]},
        {"storyId": "current-2", "story": {"id": "current-2"}, "tasks": [{"id": "t2"}]},
        {"storyId": "current-3", "story": {"id": "current-3"}, "tasks": [{"id": "t3"}]},
    ]
    fingerprints = {"current-1": "same", "current-2": "same", "current-3": "other"}
    done_entries = [{"storyId": "source-1", "status": "done", "fingerprint": "same"}]

    assert match_carried_over(batches, fingerprints, done_entries) == {"current-1"}
    assert match_carried_over(
        batches, fingerprints, [{"storyId": "current-3", "status": "done", "fingerprint": ""}]
    ) == {"current-3"}
    assert match_carried_over(batches, fingerprints, []) == set()
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','-q','-p','no:randomly','-k','retry or carried_over']))"`
Expected: FAIL en colección con `ImportError: cannot import name 'match_carried_over'`; tras crear solo el nombre, `test_a_retry_skips_stories_already_done_in_the_source_loop` falla con `assert 2 == 1` (las historias del reintento son filas nuevas y hoy se re-ejecutan todas) y `test_a_retry_with_every_story_done_runs_no_developer` falla con `assert [..2 payloads..] == []`. El test del hilo ajeno pasa ya (red de regresión de la guarda).

- [ ] **Step 3: Implementación mínima**

En `story_loop.py`:
- Agregar constante `CARRIED_OVER_OUTCOME = "carried_over"` bajo `NOOP_OUTCOME` y cambiar `__all__` a `["match_carried_over", "run_story_batches"]`.
- En `run_story_batches`, reemplazar las líneas (dejadas por T5):

```python
    pending = [batch for batch in batches if not story_batch_is_done(batch)]
    positions = {batch["storyId"]: index for index, batch in enumerate(batches, start=1)}
    _initialize_progress(coordinator, run, batches, pending=pending, fingerprints=fingerprints)
```

por (sin respaldo "re-ejecutar todo": si nada queda pendiente, T5 ya transiciona con `stories_already_done`):

```python
    carried = _carry_over_from_retry_source(coordinator, run, batches, fingerprints)
    pending = [
        batch for batch in batches if batch["storyId"] not in carried and not story_batch_is_done(batch)
    ]
    positions = {batch["storyId"]: index for index, batch in enumerate(batches, start=1)}
    _initialize_progress(
        coordinator, run, batches, pending=pending, fingerprints=fingerprints, carried=carried
    )
```

- Reemplazar `_initialize_progress` completo por:

```python
def _initialize_progress(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batches: list[dict[str, Any]],
    *,
    pending: list[dict[str, Any]],
    fingerprints: dict[str, str],
    carried: set[str],
) -> None:
    pending_ids = {batch["storyId"] for batch in pending}
    entries: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        if batch["storyId"] in pending_ids:
            _write_statuses(coordinator, batch, STORY_STATUS_TODO)
            status, outcome = STORY_STATUS_TODO, None
        elif batch["storyId"] in carried:
            _write_statuses(coordinator, batch, STORY_STATUS_DONE, outcome=CARRIED_OVER_OUTCOME)
            status, outcome = STORY_STATUS_DONE, CARRIED_OVER_OUTCOME
        else:
            status = STORY_STATUS_DONE
            outcome = ((batch.get("story") or {}).get("metadata") or {}).get("outcome")
        entries.append(
            story_progress_entry(
                story_id=batch["storyId"],
                index=index,
                title=_batch_title(batch),
                status=status,
                fingerprint=fingerprints.get(batch["storyId"], ""),
                outcome=outcome,
            )
        )
    _save_progress(coordinator, run, entries)
```

- Agregar los helpers (después de `_initialize_progress`):

```python
def match_carried_over(
    batches: list[dict[str, Any]], fingerprints: dict[str, str], done_entries: list[dict[str, Any]]
) -> set[str]:
    """Empareja uno a uno las historias actuales con las entradas ``done`` del loop de origen.

    Primero por ``storyId`` (misma fila reutilizada) y después por huella de spec (filas nuevas del
    mismo contenido). Cada entrada del origen consume a lo sumo una historia actual, en orden de
    ejecución: dos historias idénticas con una sola terminada dejan la otra pendiente.
    """
    remaining = [entry for entry in done_entries if entry.get("status") == STORY_STATUS_DONE]
    carried: set[str] = set()
    for batch in batches:
        if batch.get("story") is None:
            continue
        match = next((entry for entry in remaining if str(entry.get("storyId") or "") == batch["storyId"]), None)
        if match is None:
            fingerprint = fingerprints.get(batch["storyId"]) or ""
            match = next(
                (entry for entry in remaining if fingerprint and str(entry.get("fingerprint") or "") == fingerprint),
                None,
            )
        if match is not None:
            remaining.remove(match)
            carried.add(batch["storyId"])
    return carried


def _carry_over_from_retry_source(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batches: list[dict[str, Any]],
    fingerprints: dict[str, str],
) -> set[str]:
    source_id = str((run.request_meta or {}).get("retryOfLoopId") or "").strip()
    if not source_id:
        return set()
    try:
        source = coordinator.repository.get_loop(source_id)
    except KeyError:
        return set()
    source_durable = coordinator._durable_run_context(source)
    source_thread = str((source_durable.get("thread") or {}).get("projectThreadId") or "")
    if source["projectId"] != run.project_id or not run.thread_id or source_thread != str(run.thread_id):
        return set()
    done_entries = [entry for entry in source_durable.get("storyProgress") or [] if isinstance(entry, dict)]
    return match_carried_over(batches, fingerprints, done_entries)
```

- Actualizar el docstring del módulo: agregar al final del primer párrafo "Un reintento (``retryOfLoopId`` del mismo proyecto e hilo) salta las historias cuya huella de spec ya quedó ``done`` en el loop de origen (``metadata.outcome=carried_over``)."

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_story_execution.py','tests_py/test_product_loop_reentry.py','tests_py/test_remediation_blocker_experience.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/phases/story_loop.py tests_py/test_product_loop_story_execution.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/phases/story_loop.py tests_py/test_product_loop_story_execution.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/phases/story_loop.py tests_py/test_product_loop_story_execution.py
git commit -m "Feature (ProductLoop): un reintento salta las historias ya terminadas en el loop de origen" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Endpoint `GET /api/v1/threads/{thread_id}/board` + OpenAPI

**Files:**
- Modify: `local_control_center/product_loop/repository.py` (después de `list_loops` `:138-147`)
- Create: `local_control_center/product_loop/thread_board.py`
- Modify: `local_control_center/product_loop/models.py` (después de `StorySpecResponse` `:428-436`)
- Modify: `local_control_center/product_loop/api.py:35-45` (imports) y después de `get_story_spec` (`:125-139`)
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_thread_board_api.py`

**Interfaces:**
- Consumes: T1 `build_board(..., story_order=...)`, `empty_board`, `BacklogRepository.list_user_stories_for_output`; `durableRun.productOwner.productOwnerOutputId` (escrito por `phases/discovery.py:622-626` y persistido en cada contexto durable de `phases/team_planning.py`); `ThreadsRepository.get_thread` (lanza `KeyError`, patrón en `threads/cost_performance.py:69-75`); `BacklogRepository.get_agent_task` (`:808`), `get_user_story` (`:567`), `list_acceptance_criteria` (`:687`); `ProductLoopRepository`/`row_to_product_loop`; fixture `make_app` (`tests_py/test_workspace_isolation_contract.py:29`).
- Produces:
  - `ProductLoopRepository.latest_planned_loop_for_thread(thread_id: str) -> dict[str, Any] | None`
  - `ThreadBoardService(connection).board(thread_id: str) -> dict[str, Any]` (lanza `KeyError` si el hilo no existe)
  - Modelos `ThreadBoardTask`, `ThreadBoardCard`, `ThreadBoardColumn`, `ThreadBoardProgress`, `ThreadBoardResponse`, `ThreadBoardColumnId = Literal["todo","in_progress","qa","done"]`, `ThreadBoardStage = Literal["planning","executing","security","approval","delivered","blocked"]`
  - Operación OpenAPI `get_thread_board_api_v1_threads__thread_id__board_get`.

Decisión verificada: el tablero lee los ids de `durableRun.agentTasks` del loop (persistido en `phases/team_planning.py:357-417`) y relee filas frescas; **no** filtra por `agent_tasks.metadata.loopId` porque las tareas reutilizadas entre loops (`coordinator.py:2273-2275`) conservan el `loopId` del loop que las creó. Las historias salen del output del PO (`durableRun.productOwner.productOwnerOutputId` → `list_user_stories_for_output`, spec §4), no solo de las tareas: una historia sin tareas aparece en "Por hacer" y cuenta en el total (solo ocurre si el respaldo determinista del Technical Lead tampoco la planificó; el loop la bloquea en `technical_lead`, T5).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests_py/test_thread_board_api.py`:

```python
"""Endpoint del tablero por historia de un hilo: resolución hilo→loop, columnas y payload acotado.

@author Rodrigo Mason
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from local_control_center.backlog.repository import BacklogRepository
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.threads.repository import ThreadsRepository
from tests_py.test_workspace_isolation_contract import make_app as make_app

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _durable(
    thread_id: str, tasks: list[dict[str, Any]], progress: list[dict[str, Any]], output_id: str | None = None
) -> dict[str, Any]:
    durable: dict[str, Any] = {"thread": {"projectThreadId": thread_id}, "agentTasks": tasks, "storyProgress": progress}
    if output_id:
        durable["productOwner"] = {"productOwnerOutputId": output_id}
    return {"durableRun": durable}


def _seed(store: Any, tmp_path: Path) -> dict[str, Any]:
    connection = store.connection
    project_path = tmp_path / "board-project"
    project_path.mkdir()
    project = store.create_project(name="board-project", path=project_path, template_id="other")
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Board thread"
    )
    other = threads.create_thread(
        project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Other thread"
    )
    backlog = BacklogRepository(connection)
    epic = backlog.create_epic({"projectId": project["id"], "title": "Onboarding"})

    def story(title: str, status: str, priority: str, output_id: str = "po-output-board") -> dict[str, Any]:
        return backlog.create_user_story(
            {
                "projectId": project["id"],
                "epicId": epic["id"],
                "title": title,
                "asA": "operator",
                "iWant": f"to {title.lower()}",
                "soThat": "setup ends",
                "status": status,
                "priority": priority,
                "acceptanceCriteria": [f"{title} works."],
                "metadata": {"productOwnerOutputId": output_id},
            }
        )

    def task(for_story: dict[str, Any], status: str) -> dict[str, Any]:
        return backlog.create_agent_task(
            {
                "projectId": project["id"],
                "storyId": for_story["id"],
                "title": f"Build {for_story['title']}",
                "role": "backend_engineer",
                "status": status,
            }
        )

    done = story("Readiness checklist", "done", "high")
    in_qa = story("Setup reminders", "qa", "medium")
    blocked = story("Invite teammates", "blocked", "low")
    todo = story("Export report", "draft", "medium")
    story("Audit trail", "draft", "low")
    tasks = [task(todo, "todo"), task(blocked, "blocked"), task(in_qa, "qa"), task(done, "done")]
    loops = ProductLoopRepository(connection)
    loop = loops.create_loop(
        {
            "projectId": project["id"],
            "title": "Board loop",
            "state": "qa_running",
            "status": "active",
            "context": _durable(
                thread["id"],
                tasks,
                [
                    {
                        "storyId": blocked["id"],
                        "status": "blocked",
                        "reason": "QA failed after 2 rework rounds.",
                        "runtime": "codex_cli",
                    }
                ],
                "po-output-board",
            ),
        }
    )
    foreign_story = story("Other thread story", "draft", "critical", "po-output-other")
    loops.create_loop(
        {
            "projectId": project["id"],
            "title": "Other loop",
            "state": "executing",
            "status": "active",
            "context": _durable(other["id"], [task(foreign_story, "todo")], [], "po-output-other"),
        }
    )
    connection.commit()
    return {"project": project, "thread": thread, "loop": loop, "backlog": backlog}


def test_thread_board_projects_story_statuses_into_columns(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)

    response = client.get(f"/api/v1/threads/{seeded['thread']['id']}/board")

    assert response.status_code == 200
    body = response.json()
    assert body["loopId"] == seeded["loop"]["id"]
    assert body["loopState"] == "qa_running"
    assert body["stage"] == "executing"
    columns = {column["id"]: [card["title"] for card in column["cards"]] for column in body["columns"]}
    assert columns == {
        "todo": ["Export report", "Audit trail"],
        "in_progress": ["Invite teammates"],
        "qa": ["Setup reminders"],
        "done": ["Readiness checklist"],
    }
    audit = body["columns"][0]["cards"][1]
    assert audit["tasks"] == []
    assert audit["index"] == 5
    blocked = next(card for column in body["columns"] for card in column["cards"] if card["blocked"])
    assert blocked["blockedReason"] == "QA failed after 2 rework rounds."
    assert blocked["runtime"] == "codex_cli"
    assert blocked["index"] == 4
    assert body["progress"] == {"done": 1, "total": 5}
    assert "Other thread story" not in json.dumps(body)


def test_thread_board_reads_the_most_recent_planned_loop(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)
    backlog: BacklogRepository = seeded["backlog"]
    epic = backlog.list_epics(seeded["project"]["id"])[0]
    newer_story = backlog.create_user_story(
        {
            "projectId": seeded["project"]["id"],
            "epicId": epic["id"],
            "title": "Newer story",
            "asA": "operator",
            "iWant": "a newer loop",
            "soThat": "the board follows it",
            "acceptanceCriteria": ["Newer works."],
        }
    )
    newer_task = backlog.create_agent_task(
        {"projectId": seeded["project"]["id"], "storyId": newer_story["id"], "title": "Newer", "role": "developer"}
    )
    newer_loop = ProductLoopRepository(store.connection).create_loop(
        {
            "projectId": seeded["project"]["id"],
            "title": "Newer loop",
            "state": "executing",
            "status": "active",
            "context": _durable(seeded["thread"]["id"], [newer_task], []),
        }
    )
    store.connection.commit()

    body = client.get(f"/api/v1/threads/{seeded['thread']['id']}/board").json()

    assert body["loopId"] == newer_loop["id"]
    assert [card["title"] for column in body["columns"] for card in column["cards"]] == ["Newer story"]


def test_thread_board_without_a_planned_loop_is_an_empty_planning_board(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, _headers = make_app(tmp_path, monkeypatch)
    seeded = _seed(store, tmp_path)
    fresh = ThreadsRepository(store.connection).create_thread(
        project_id=seeded["project"]["id"],
        owner_type="workspace",
        owner_id=seeded["project"]["id"],
        title="Fresh thread",
    )
    store.connection.commit()

    body = client.get(f"/api/v1/threads/{fresh['id']}/board").json()

    assert body["loopId"] is None
    assert body["stage"] == "planning"
    assert body["progress"] == {"done": 0, "total": 0}
    assert [column["id"] for column in body["columns"]] == ["todo", "in_progress", "qa", "done"]


def test_thread_board_unknown_thread_is_404(make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store, client, _headers = make_app(tmp_path, monkeypatch)

    assert client.get("/api/v1/threads/thread-missing/board").status_code == 404


def test_thread_board_endpoint_runs_in_the_threadpool(
    make_app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, client, _headers = make_app(tmp_path, monkeypatch)

    route = next(
        route for route in client.app.routes if getattr(route, "path", "") == "/api/v1/threads/{thread_id}/board"
    )

    assert not inspect.iscoroutinefunction(route.endpoint)
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_api.py','-q','-p','no:randomly']))"`
Expected: FAIL — `assert 404 == 200` (ruta inexistente) y `StopIteration` en el test del threadpool.

- [ ] **Step 3: Implementación mínima**

3a. `product_loop/repository.py`, después de `list_loops`:

```python
    def latest_planned_loop_for_thread(self, thread_id: str) -> dict[str, Any] | None:
        """Loop más reciente del hilo que ya tiene tareas planificadas (``durableRun.agentTasks``).

        El vínculo hilo→loop vive en ``context.durableRun.thread.projectThreadId``. Se desempata por
        ``rowid`` (orden de inserción) y no por timestamps de igual milésima. Devuelve ``None`` si el
        hilo no tiene ningún loop planificado. Lectura de una sola sentencia: no abre transacción.
        """
        row = self.connection.execute(
            """
            SELECT * FROM product_loops
            WHERE json_extract(context, '$.durableRun.thread.projectThreadId') = ?
              AND json_array_length(json_extract(context, '$.durableRun.agentTasks')) > 0
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
        return row_to_product_loop(row) if row else None
```

3b. Crear `local_control_center/product_loop/thread_board.py`:

```python
"""Lectura del tablero por historia de un hilo: resuelve su loop planificado y proyecta el backlog.

Resuelve el loop más reciente del hilo con tareas planificadas, relee sus tareas e historias frescas
desde el backlog (los ids salen de ``durableRun.agentTasks`` porque las tareas reutilizadas entre loops
conservan el ``metadata.loopId`` de su loop de origen), todas las historias del output del PO del loop
(``durableRun.productOwner.productOwnerOutputId``, en orden de emisión e incluidas las que no tienen
tareas), los criterios de aceptación y el cursor ``durableRun.storyProgress``, y delega el mapeo a
columnas en ``backlog.board.build_board``. El payload
queda acotado al hilo (nunca el agregado del proyecto). Solo lectura: no abre transacciones.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.backlog.board import build_board, empty_board
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.threads.repository import ThreadsRepository

from .repository import ProductLoopRepository


class ThreadBoardService:
    """Arma el tablero de un hilo leyendo solo su loop planificado, sus historias y sus tareas."""

    def __init__(self, connection: sqlite3.Connection):
        self.threads = ThreadsRepository(connection)
        self.loops = ProductLoopRepository(connection)
        self.backlog = BacklogRepository(connection)

    def board(self, thread_id: str) -> dict[str, Any]:
        """Devuelve el tablero del hilo; sin loop planificado devuelve el tablero vacío en ``planning``.

        Raises:
            KeyError: si el hilo no existe.
        """
        self.threads.get_thread(thread_id)
        loop = self.loops.latest_planned_loop_for_thread(thread_id)
        if loop is None:
            return empty_board()
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        tasks = self._fresh_tasks(durable.get("agentTasks") or [])
        output_id = str(((durable.get("productOwner") or {}).get("productOwnerOutputId")) or "")
        emitted = self.backlog.list_user_stories_for_output(loop["projectId"], output_id) if output_id else []
        stories = self._stories(tasks, {story["id"]: story for story in emitted})
        criteria = {
            story_id: [
                str(item.get("criterion") or "") for item in self.backlog.list_acceptance_criteria(story_id=story_id)
            ]
            for story_id in stories
        }
        progress = {
            str(entry.get("storyId") or ""): entry
            for entry in durable.get("storyProgress") or []
            if isinstance(entry, dict)
        }
        return build_board(
            loop_id=loop["id"],
            loop_state=loop["state"],
            agent_tasks=tasks,
            stories_by_id=stories,
            criteria_by_story=criteria,
            progress_by_story=progress,
            story_order=[story["id"] for story in emitted],
        )

    def _fresh_tasks(self, planned: list[Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for item in planned:
            task_id = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
            if not task_id:
                continue
            try:
                tasks.append(self.backlog.get_agent_task(task_id))
            except KeyError:
                continue
        return tasks

    def _stories(
        self, tasks: list[dict[str, Any]], emitted: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        stories: dict[str, dict[str, Any]] = dict(emitted)
        for task in tasks:
            story_id = str(task.get("storyId") or "").strip()
            if not story_id or story_id in stories:
                continue
            try:
                stories[story_id] = self.backlog.get_user_story(story_id)
            except KeyError:
                continue
        return stories
```

3c. `product_loop/models.py`, después de `StorySpecResponse`:

```python
ThreadBoardColumnId = Literal["todo", "in_progress", "qa", "done"]
ThreadBoardStage = Literal["planning", "executing", "security", "approval", "delivered", "blocked"]


class ThreadBoardTask(BaseModel):
    """Tarea de agente dentro de una tarjeta del tablero (rol y estado persistido)."""

    id: str
    title: str
    role: str
    status: str


class ThreadBoardCard(BaseModel):
    """Tarjeta del tablero: una historia con su estado, marca de bloqueo, runtime y tareas."""

    story_id: str = Field(alias="storyId")
    index: int
    synthetic: bool
    title: str
    as_a: str = Field(alias="asA")
    i_want: str = Field(alias="iWant")
    so_that: str = Field(alias="soThat")
    priority: str
    status: str
    column: ThreadBoardColumnId
    blocked: bool
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    outcome: str | None = None
    runtime: str | None = None
    acceptance_criteria: list[str] = Field(alias="acceptanceCriteria")
    tasks: list[ThreadBoardTask]


class ThreadBoardColumn(BaseModel):
    """Columna del tablero (Por hacer, En curso, QA, Listo) con sus tarjetas en orden de ejecución."""

    id: ThreadBoardColumnId
    cards: list[ThreadBoardCard]


class ThreadBoardProgress(BaseModel):
    """Progreso del loop del hilo: historias terminadas sobre el total."""

    done: int
    total: int


class ThreadBoardResponse(BaseModel):
    """Tablero por historia del hilo: loop resuelto, etapa, cuatro columnas y progreso."""

    loop_id: str | None = Field(default=None, alias="loopId")
    loop_state: str | None = Field(default=None, alias="loopState")
    stage: ThreadBoardStage
    columns: list[ThreadBoardColumn]
    progress: ThreadBoardProgress
```

3d. `product_loop/api.py`: agregar `ThreadBoardResponse,` al import de `.models` (orden alfabético, tras `StorySpecResponse,`) y `from .thread_board import ThreadBoardService` tras `from .repository import ProductLoopRepository`. Después de `get_story_spec` agregar:

```python
    @router.get("/api/v1/threads/{thread_id}/board", response_model=ThreadBoardResponse)
    def get_thread_board(thread_id: str) -> dict[str, Any]:
        """Devuelve el tablero por historia del loop planificado del hilo (lectura acotada, sin token)."""
        try:
            return ThreadBoardService(platform.connection).board(thread_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
```

3e. Regenerar OpenAPI (con Lane A en un commit verde): `corepack pnpm@10.24.0 run openapi:generate`. Verificar con `git diff --stat local-control-center/web/src/api/generated/openapi.ts` que aparecen `ThreadBoardResponse` y `get_thread_board_api_v1_threads__thread_id__board_get`.

- [ ] **Step 4: Correr tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_api.py','tests_py/test_ci_and_openapi_client.py','tests_py/test_vertical_slices_architecture.py','tests_py/test_source_documentation_headers.py','tests_py/test_product_loop_api.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `corepack pnpm@10.24.0 run typecheck:web`. Expected: exit 0.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/product_loop/repository.py local_control_center/product_loop/thread_board.py local_control_center/product_loop/models.py local_control_center/product_loop/api.py tests_py/test_thread_board_api.py`
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/product_loop/repository.py local_control_center/product_loop/thread_board.py local_control_center/product_loop/models.py local_control_center/product_loop/api.py tests_py/test_thread_board_api.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/repository.py local_control_center/product_loop/thread_board.py local_control_center/product_loop/models.py local_control_center/product_loop/api.py local-control-center/web/src/api/generated/openapi.ts tests_py/test_thread_board_api.py
git commit -m "Feature (API): endpoint GET /threads/{id}/board con el tablero por historia del hilo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Cliente, modelo, hooks y señal de etapa del tablero (frontend)

**Files:**
- Modify: `local-control-center/web/src/api/client.ts` (`:119-121` tipos; después de `getThreadCostPerformance` `:1303-1312`)
- Create: `local-control-center/web/src/features/shell/threadBoardModel.ts`
- Create: `local-control-center/web/src/features/shell/useThreadBoard.ts`
- Create: `local-control-center/web/src/features/shell/useThreadStage.ts`
- Modify: `local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx` (`:26-45`, `:47-63`, `:110-118`, `:125`, `:183-189`, `:208-224`, `:258-267`)
- Modify: `local-control-center/web/src/features/shell/useThreadEventStream.ts` (`:11-14`, `:55-91`)
- Modify: `local-control-center/web/src/features/shell/useThreadRemediations.ts` (import, `execute` `:112-128`)
- Test: `tests_py/test_thread_board_frontend_contract.py`

Decisión verificada (SLA ≤ 2 s tras reintentar): el stream baja a `IDLE_POLL_INTERVAL_MS = 15000` cuando `running` es falso y hubo 5 polls vacíos (`useThreadEventStream.ts:12,72-77`), y ejecutar una remediación solo refresca las tarjetas (`useThreadRemediations.ts:112-128`), no el stream. Un hilo bloqueado que se reintenta podía tardar hasta 15 s en ver el primer `story_progress`. Se agrega un "despertador" por hilo, a nivel de módulo, que `execute` dispara tras una remediación exitosa: el stream re-polea al instante y vuelve a la cadencia de 1 s. No se reutiliza `ThreadRefreshContext.invalidate` porque `useThreadConversation.reload` lo llama en cada secuencia nueva (`useThreadConversation.ts:60-63`) y reiniciar el stream con él vaciaría la consola en cada evento.

**Interfaces:**
- Consumes: operación generada `get_thread_board_api_v1_threads__thread_id__board_get` (T8); `requestGeneratedOperation` (`client.ts:11`); `toneForStatus` (`lib/format.ts:74`); `StatusTone` (`components/ui/index.ts`); `ThreadAgentEvent` (`api/types.ts:91`).
- Produces:
  - `client.ts`: `export type ThreadBoardResponse`, `export type ThreadBoardCard`, `export function getThreadBoard(threadId: string, signal?: AbortSignal)`.
  - `threadBoardModel.ts`: `BoardMode = 'chat' | 'board'`, `BoardColumnId`, `BoardStage`, `BOARD_COLUMN_META`, `BOARD_STAGE_META`, `BOARD_REFRESH_EVENT_TYPES`, `latestBoardRefreshSequence(events)`.
  - `useThreadBoard(threadId: string | null, refreshSequence: number): { data, loading, error }`.
  - `useThreadStage(events, threadStatus): { current, blocked, inDevelopment }`.
  - `ThreadExecutionPanel.tsx`: `export function derivePipeline`, `export const EXECUTING_STEP_INDEX`, `PipelineSnapshot.current`, prop `presentation?: 'column' | 'strip'` → `data-presentation`.
  - `useThreadEventStream.ts`: `export function wakeThreadEventStream(threadId: string): void` (re-poll inmediato del stream montado de ese hilo y reinicio de la cadencia rápida).
  - `useThreadRemediations.ts`: `execute` llama `wakeThreadEventStream(threadId)` tras una remediación exitosa (cubre `ThreadConversation` e `ThreadInspector`, ambos llamadores del hook).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests_py/test_thread_board_frontend_contract.py`:

```python
"""Contrato fuente del tablero por historia en el frontend: cliente, modelo, etapa y layout.

Fija lo que la UI necesita del slice (spec §4-§5) sin depender de un navegador: la lectura
generada del tablero, los eventos que lo refrescan, la señal de etapa derivada del mismo pipeline
del panel de ejecución (``MILESTONE_INDEX`` sigue literal por ``test_product_loop_result_taxonomy``)
y los tripwires de clases del layout.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "local-control-center" / "web" / "src"
SHELL = WEB / "features" / "shell"
CLIENT = WEB / "api" / "client.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_client_exposes_the_thread_board_read() -> None:
    source = _read(CLIENT)
    assert "export function getThreadBoard(threadId: string, signal?: AbortSignal)" in source
    assert "'get_thread_board_api_v1_threads__thread_id__board_get'" in source


def test_board_refreshes_on_story_progress_and_delivery_milestones() -> None:
    source = _read(SHELL / "threadBoardModel.ts")
    for event_type in (
        "story_progress",
        "executing",
        "qa_running",
        "security_running",
        "awaiting_approval",
        "delivered",
    ):
        assert f"'{event_type}'" in source, event_type
    assert "export function latestBoardRefreshSequence(" in source


def test_stage_signal_reuses_the_execution_panel_pipeline() -> None:
    panel = _read(SHELL / "ThreadExecutionPanel.tsx")
    stage = _read(SHELL / "useThreadStage.ts")
    assert "export function derivePipeline(" in panel
    assert "const MILESTONE_INDEX: Record<string, number> = {" in panel
    assert "blocked: isBlocked," in panel
    assert "data-presentation={presentation}" in panel
    assert "derivePipeline(events, threadStatus" in stage
    assert "inDevelopment" in stage


def test_board_hook_reads_the_thread_board() -> None:
    source = _read(SHELL / "useThreadBoard.ts")
    assert "getThreadBoard(threadId, controller.signal)" in source


def test_a_successful_remediation_wakes_the_idle_event_stream() -> None:
    stream = _read(SHELL / "useThreadEventStream.ts")
    remediations = _read(SHELL / "useThreadRemediations.ts")
    assert "export function wakeThreadEventStream(threadId: string): void" in stream
    assert "const streamWakers = new Map<string, Set<() => void>>();" in stream
    assert "wakeThreadEventStream(threadId);" in remediations
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','-q','-p','no:randomly']))"`
Expected: FAIL — `AssertionError` en el cliente y en el despertador del stream (`wakeThreadEventStream` no existe), y `FileNotFoundError` para `threadBoardModel.ts`/`useThreadStage.ts`/`useThreadBoard.ts`.

- [ ] **Step 3: Implementación mínima**

3a. `client.ts`, después de la línea `export type ThreadCostPerformanceRecord = ThreadCostPerformanceResponse['costPerformance'];` (`:121`):

```ts
export type ThreadBoardResponse =
	OperationResponse<'get_thread_board_api_v1_threads__thread_id__board_get'>;
export type ThreadBoardCard = ThreadBoardResponse['columns'][number]['cards'][number];
```

y después de `getThreadCostPerformance` (`:1303-1312`):

```ts
/** Reads the per-story board of the thread's latest planned loop; a read, so no token needed. */
export function getThreadBoard(threadId: string, signal?: AbortSignal) {
	return requestGeneratedOperation<
		'get_thread_board_api_v1_threads__thread_id__board_get',
		ThreadBoardResponse
	>('get_thread_board_api_v1_threads__thread_id__board_get', {
		pathParams: { thread_id: threadId },
		signal,
	});
}
```

3b. Crear `features/shell/threadBoardModel.ts`:

```ts
/**
 * Presentation model of the thread story board: the lane and stage vocabulary (i18n keys, English
 * fallbacks and tones), the thread event types that make the board stale, and the view mode of the
 * thread layout (chat-first or board-first).
 * @author Rodrigo Mason
 */
import type { ThreadBoardResponse } from '../../api/client';
import type { ThreadAgentEvent } from '../../api/types';
import type { StatusTone } from '../../components/ui';

export type BoardMode = 'chat' | 'board';
export type BoardColumnId = ThreadBoardResponse['columns'][number]['id'];
export type BoardStage = ThreadBoardResponse['stage'];

type CopyMeta = { labelKey: string; fallback: string; tone: StatusTone };
type ColumnMeta = CopyMeta & { emptyKey: string; emptyFallback: string };

export const BOARD_COLUMN_META: Record<BoardColumnId, ColumnMeta> = {
	todo: {
		labelKey: 'app.threads.board.column.todo',
		fallback: 'To do',
		tone: 'pending',
		emptyKey: 'app.threads.board.empty.todo',
		emptyFallback: 'Nothing waiting',
	},
	in_progress: {
		labelKey: 'app.threads.board.column.inProgress',
		fallback: 'In progress',
		tone: 'info',
		emptyKey: 'app.threads.board.empty.inProgress',
		emptyFallback: 'No story in progress',
	},
	qa: {
		labelKey: 'app.threads.board.column.qa',
		fallback: 'QA',
		tone: 'warn',
		emptyKey: 'app.threads.board.empty.qa',
		emptyFallback: 'Nothing under QA',
	},
	done: {
		labelKey: 'app.threads.board.column.done',
		fallback: 'Done',
		tone: 'ok',
		emptyKey: 'app.threads.board.empty.done',
		emptyFallback: 'No story done yet',
	},
};

export const BOARD_STAGE_META: Record<BoardStage, CopyMeta> = {
	planning: { labelKey: 'app.threads.board.stage.planning', fallback: 'Planning', tone: 'pending' },
	executing: {
		labelKey: 'app.threads.board.stage.executing',
		fallback: 'Executing stories',
		tone: 'info',
	},
	security: { labelKey: 'app.threads.board.stage.security', fallback: 'Security review', tone: 'warn' },
	approval: {
		labelKey: 'app.threads.board.stage.approval',
		fallback: 'Awaiting approval',
		tone: 'warn',
	},
	delivered: { labelKey: 'app.threads.board.stage.delivered', fallback: 'Delivered', tone: 'ok' },
	blocked: { labelKey: 'app.threads.board.stage.blocked', fallback: 'Blocked', tone: 'danger' },
};

/** Thread events after which the board can have moved (story transitions and delivery stages). */
export const BOARD_REFRESH_EVENT_TYPES: ReadonlySet<string> = new Set([
	'story_progress',
	'executing',
	'qa_running',
	'security_running',
	'awaiting_approval',
	'delivered',
]);

/** Sequence of the newest board-relevant event; a new value means the board must be refetched. */
export function latestBoardRefreshSequence(events: ThreadAgentEvent[]): number {
	return events.findLast((event) => BOARD_REFRESH_EVENT_TYPES.has(event.type))?.sequence ?? 0;
}
```

3c. Crear `features/shell/useThreadBoard.ts`:

```ts
/**
 * Loads the per-story board of a thread and refetches it whenever a board-relevant event (story
 * progress or a delivery-stage milestone) lands in the thread event stream, so a card move shows up
 * within one event poll plus one read.
 * @author Rodrigo Mason
 */
import { useEffect, useState } from 'react';

import { getThreadBoard, type ThreadBoardResponse } from '../../api/client';

export type ThreadBoardState = {
	data: ThreadBoardResponse | null;
	loading: boolean;
	error: string;
};

type Snapshot = { threadId: string | null; board: ThreadBoardResponse | null };

/** Fetches `GET /threads/{id}/board` for `threadId` and again on every `refreshSequence` change. */
export function useThreadBoard(threadId: string | null, refreshSequence: number): ThreadBoardState {
	const [snapshot, setSnapshot] = useState<Snapshot>({ threadId: null, board: null });
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState('');

	// biome-ignore lint/correctness/useExhaustiveDependencies: refreshSequence is an explicit refetch trigger, not read inside the effect body.
	useEffect(() => {
		if (!threadId) {
			setSnapshot({ threadId: null, board: null });
			setLoading(false);
			setError('');
			return;
		}
		const controller = new AbortController();
		setLoading(true);
		getThreadBoard(threadId, controller.signal)
			.then((board) => {
				if (controller.signal.aborted) return;
				setSnapshot({ threadId, board });
				setError('');
			})
			.catch((reason: unknown) => {
				if (controller.signal.aborted) return;
				setError(reason instanceof Error ? reason.message : String(reason));
			})
			.finally(() => {
				if (!controller.signal.aborted) setLoading(false);
			});
		return () => controller.abort();
	}, [threadId, refreshSequence]);

	return { data: snapshot.threadId === threadId ? snapshot.board : null, loading, error };
}
```

3d. Crear `features/shell/useThreadStage.ts`:

```ts
/**
 * Development-stage signal of a thread, derived from the same event log and pipeline derivation the
 * execution panel renders, so the conversation switches into the story board layout exactly when the
 * DeveloperAgent starts executing stories (and back when a new objective restarts planning).
 * @author Rodrigo Mason
 */
import { useMemo } from 'react';

import type { ThreadAgentEvent } from '../../api/types';
import { derivePipeline, EXECUTING_STEP_INDEX } from './ThreadExecutionPanel';

export type ThreadStage = {
	/** Index of the pipeline step the newest milestone made current. */
	current: number;
	blocked: boolean;
	/** True once the newest milestone reached executing (including QA, security, approval, delivery). */
	inDevelopment: boolean;
};

/** Pipeline position of the thread for the layout switch. */
export function useThreadStage(events: ThreadAgentEvent[], threadStatus: string): ThreadStage {
	return useMemo(() => {
		const pipeline = derivePipeline(events, threadStatus, '');
		return {
			current: pipeline.current,
			blocked: pipeline.blocked,
			inDevelopment: pipeline.current >= EXECUTING_STEP_INDEX,
		};
	}, [events, threadStatus]);
}
```

3e. `ThreadExecutionPanel.tsx`:
- Tras el cierre `] as const;` de `PIPELINE_STEPS` (`:63`) agregar:

```ts

/** Step index of `executing`: from here on the thread is in development (board layout). */
export const EXECUTING_STEP_INDEX = PIPELINE_STEPS.findIndex((step) => step.id === 'executing');
```

- En `type PipelineSnapshot` (`:110-118`) agregar como primer campo:

```ts
	/** Index of the step the newest milestone made current (>= PIPELINE_STEPS.length once delivered). */
	current: number;
```

- `:125` `function derivePipeline(` → `export function derivePipeline(`.
- En el `return {` final de `derivePipeline` (`:183`) agregar `current,` como primera propiedad (se conserva la línea `blocked: isBlocked,`).
- En `ThreadExecutionPanelProps` (`:26-45`) agregar antes del cierre:

```ts
	/** `strip` compacts the panel into a horizontal band above the story board (development mode). */
	presentation?: 'column' | 'strip';
```

- En la desestructuración de props (`:208-224`) agregar `presentation = 'column',` tras `onOpenSettings,`.
- En `<m.aside className="thread-execution-pane"` (`:258-260`) agregar el atributo `data-presentation={presentation}` después de `className`.

3f. `features/shell/useThreadEventStream.ts`:
- Tras `const EMPTY_BOOTSTRAP_POLLS = 5;` (`:14`) agregar:

```ts

/** Wakers of the mounted streams, by thread: an explicit action (a retry) asks for an immediate poll. */
const streamWakers = new Map<string, Set<() => void>>();

/**
 * Polls `threadId`'s mounted event streams right away and restores their fast cadence. Used after an
 * operator action that restarts work (a remediation such as `retry_loop`), so an idle stream on its
 * 15 s cadence reflects the first story transition within one fast poll instead of up to 15 s later.
 */
export function wakeThreadEventStream(threadId: string): void {
	for (const wake of streamWakers.get(threadId) ?? []) wake();
}
```

- Una sola cadena de polls por stream: si el despertador llega con un poll en vuelo, no arranca otro (duplicaría la cadena de timers y los eventos); marca `wakeRequested` y ese poll reprograma con retardo 0. Dentro del `useEffect`:
  - Tras `let emptyPolls = 0;` (`:55`) agregar:

```ts
		let inFlight = false;
		let wakeRequested = false;
		const schedule = (delay: number) => {
			timer = window.setTimeout(poll, wakeRequested ? 0 : delay);
			wakeRequested = false;
		};
```

  - En `poll` (`:57-85`): agregar `inFlight = true;` como primera sentencia del cuerpo (antes del `try`), agregar `finally { inFlight = false; }` al `try/catch` existente, y reemplazar las dos llamadas `timer = window.setTimeout(poll, <delay>);` por `schedule(<delay>);` conservando cada expresión de retardo tal cual (`page.running || emptyPolls < EMPTY_BOOTSTRAP_POLLS ? POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS` en el `try`, `IDLE_POLL_INTERVAL_MS` en el `catch`). `schedule` es una `const` declarada antes de `poll` pero solo se invoca dentro de `poll` ya definido, así que no hay TDZ.
  - Justo antes de `void poll();` (`:86`) agregar:

```ts
		const wake = () => {
			if (!active) return;
			emptyPolls = 0;
			if (inFlight) {
				wakeRequested = true;
				return;
			}
			if (timer) window.clearTimeout(timer);
			void poll();
		};
		const wakers = streamWakers.get(threadId) ?? new Set<() => void>();
		wakers.add(wake);
		streamWakers.set(threadId, wakers);
```

- En el cleanup del effect (`:87-90`), antes de `active = false;`, agregar:

```ts
			wakers.delete(wake);
			if (!wakers.size) streamWakers.delete(threadId);
```

- Actualizar el JSDoc del módulo (`:1-5`) agregando: "An explicit operator action can wake an idle stream through `wakeThreadEventStream`."

3g. `features/shell/useThreadRemediations.ts`:
- Agregar `import { wakeThreadEventStream } from './useThreadEventStream';` después del import de `./remediationPresentation` (`:23-27`).
- En `execute` (`:112-128`), reemplazar (bloque único: `dismiss` no tiene `return result;`):

```ts
				await refetch();
				return result;
			} finally {
				setBusyId(null);
			}
		},
		[busyId, mutate, refetch],
```

por:

```ts
				await refetch();
				if (threadId) wakeThreadEventStream(threadId);
				return result;
			} finally {
				setBusyId(null);
			}
		},
		[busyId, mutate, refetch, threadId],
```

  (El contrato fuente busca la subcadena `wakeThreadEventStream(threadId);`, presente en esa línea.)
- Actualizar el JSDoc del módulo (`:1-10`) agregando: "A successful `execute` also wakes the thread's event stream so the restarted work shows up without waiting for the idle poll."

- [ ] **Step 4: Correr tests, typecheck y Biome**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_web_rework_architecture.py','tests_py/test_source_documentation_headers.py','tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `corepack pnpm@10.24.0 run typecheck:web` → exit 0.
Run: `corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/api/client.ts local-control-center/web/src/features/shell/threadBoardModel.ts local-control-center/web/src/features/shell/useThreadBoard.ts local-control-center/web/src/features/shell/useThreadStage.ts local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx local-control-center/web/src/features/shell/useThreadEventStream.ts local-control-center/web/src/features/shell/useThreadRemediations.ts` y luego el mismo comando sin `--write` → sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local-control-center/web/src/api/client.ts local-control-center/web/src/features/shell/threadBoardModel.ts local-control-center/web/src/features/shell/useThreadBoard.ts local-control-center/web/src/features/shell/useThreadStage.ts local-control-center/web/src/features/shell/ThreadExecutionPanel.tsx local-control-center/web/src/features/shell/useThreadEventStream.ts local-control-center/web/src/features/shell/useThreadRemediations.ts tests_py/test_thread_board_frontend_contract.py
git commit -m "Feature (Threads): cliente, modelo y señal de etapa del tablero por historia; un reintento despierta el stream del hilo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Componente `ThreadBoard` con columnas, franja de etapa, CSS e i18n

**Files:**
- Create: `local-control-center/web/src/features/shell/ThreadBoard.tsx`
- Modify: `local-control-center/web/src/design-system/layout.css` (append al final, tras `.review-board` `:4214-4290`)
- Modify: `local_control_center/i18n/default_catalog.json` (insertar tras la entrada `"app.threads.consoleTitle"` `:1078-1081`)
- Test: `tests_py/test_thread_board_frontend_contract.py` (append)

**Interfaces:**
- Consumes: T9 (`ThreadBoardResponse`, `ThreadBoardCard`, `BOARD_COLUMN_META`, `BOARD_STAGE_META`); `EmptyState`, `StatusChip`, `StatusDot` (`components/ui`); `crossfade` (`motion/variants.ts:62`); `toneForStatus` (`lib/format.ts:74`).
- Produces: `export function ThreadBoard({ board, error }: ThreadBoardProps)`; región accesible "Story board"; `.thread-board-column[data-column]`, `.thread-board-card[data-story-id][data-blocked]`.

Tripwire: **no** reutilizar la clase `.review-board`: `tests_web/thread-lifecycle-e2e.spec.js` hace `page.locator('.review-board')` en modo estricto y dos coincidencias rompen el paso 12.

- [ ] **Step 1: Escribir el test que falla**

Append a `tests_py/test_thread_board_frontend_contract.py` (agregar `LAYOUT_CSS = WEB / "design-system" / "layout.css"` junto a las constantes):

```python
def test_thread_board_component_never_reuses_review_board_classes() -> None:
    source = _read(SHELL / "ThreadBoard.tsx")
    assert 'className="thread-board"' in source
    assert "review-board" not in source
    assert "review-column" not in source
    css = _read(LAYOUT_CSS)
    assert "@container thread-board" in css
    assert ".thread-board-column-scroll" in css
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','-q','-p','no:randomly','-k','review_board']))"`
Expected: FAIL con `FileNotFoundError` (`ThreadBoard.tsx`).

- [ ] **Step 3: Implementación mínima**

3a. Crear `features/shell/ThreadBoard.tsx`:

```tsx
/**
 * Per-story execution board of a thread (To do · In progress · QA · Done), fed by
 * `GET /threads/{id}/board`. The product loop moves the cards — there is no drag and drop — so the
 * board is a read-only projection: a stage strip (planning / executing / security / approval /
 * delivered / blocked) with the done-of-total progress, and four lanes with fixed headers and their
 * own scroll, one card per user story with its value statement, criteria and agent tasks.
 * @author Rodrigo Mason
 */
import { AlertTriangle } from 'lucide-react';
import { m } from 'motion/react';

import type { ThreadBoardCard, ThreadBoardResponse } from '../../api/client';
import { EmptyState, StatusChip, StatusDot } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { toneForStatus } from '../../lib/format';
import { crossfade } from '../../motion/variants';
import { BOARD_COLUMN_META, BOARD_STAGE_META } from './threadBoardModel';

export type ThreadBoardProps = {
	board: ThreadBoardResponse;
	/** Last refetch error; the previous board stays on screen while it is shown. */
	error: string;
};

/** The thread's story board: stage strip plus the four status lanes. */
export function ThreadBoard({ board, error }: ThreadBoardProps) {
	const { t } = useI18n();
	const stage = BOARD_STAGE_META[board.stage];
	return (
		<m.section
			className="thread-board"
			aria-label={t('app.threads.board.region', 'Story board')}
			layout
			variants={crossfade}
			initial="initial"
			animate="animate"
		>
			<header className="thread-board-stage">
				<StatusChip tone={stage.tone}>{t(stage.labelKey, stage.fallback)}</StatusChip>
				<span className="thread-board-progress">
					{t('app.threads.board.progress', '{done} of {total} stories done')
						.replace('{done}', String(board.progress.done))
						.replace('{total}', String(board.progress.total))}
				</span>
				{error ? (
					<span className="thread-board-error" role="status">
						{error}
					</span>
				) : null}
			</header>
			<div className="thread-board-columns">
				{board.columns.map((column) => {
					const meta = BOARD_COLUMN_META[column.id];
					const headerId = `thread-board-col-${column.id}`;
					return (
						<section
							className="thread-board-column"
							data-column={column.id}
							aria-labelledby={headerId}
							key={column.id}
						>
							<header className="thread-board-column-header">
								<StatusDot tone={meta.tone} />
								<h3 id={headerId} className="thread-board-column-title">
									{t(meta.labelKey, meta.fallback)}
								</h3>
								<span className="thread-board-column-count">
									<StatusChip tone={column.cards.length ? meta.tone : undefined}>
										{column.cards.length}
									</StatusChip>
								</span>
							</header>
							<div className="thread-board-column-scroll">
								{column.cards.length === 0 ? (
									<EmptyState title={t(meta.emptyKey, meta.emptyFallback)} body="" />
								) : (
									column.cards.map((card) => <ThreadBoardStoryCard key={card.storyId} card={card} />)
								)}
							</div>
						</section>
					);
				})}
			</div>
		</m.section>
	);
}

/** One story card: title, value statement, blocker, outcome chip, criteria and agent tasks. */
function ThreadBoardStoryCard({ card }: { card: ThreadBoardCard }) {
	const { t } = useI18n();
	const title = card.synthetic ? t('app.threads.board.generalTasks', 'General tasks') : card.title;
	return (
		<m.article
			className="thread-board-card"
			data-story-id={card.storyId}
			data-blocked={card.blocked ? 'true' : undefined}
			layout="position"
			layoutId={`thread-board-${card.storyId}`}
		>
			<div className="thread-board-card-head">
				<span className="thread-board-card-index mono">#{card.index}</span>
				<h4 className="thread-board-card-title">{title}</h4>
			</div>
			{card.asA || card.iWant || card.soThat ? (
				<p className="thread-board-card-story">
					{t('app.threads.board.storyLine', 'As {asA}, I want {iWant} so that {soThat}')
						.replace('{asA}', card.asA)
						.replace('{iWant}', card.iWant)
						.replace('{soThat}', card.soThat)}
				</p>
			) : null}
			{card.blocked ? (
				<p className="thread-board-card-blocked" role="status">
					<AlertTriangle aria-hidden="true" size={13} />
					{card.blockedReason ||
						t(
							'app.threads.board.blockedFallback',
							'Blocked: QA or the runtime could not finish this story.',
						)}
				</p>
			) : null}
			{card.outcome === 'noop' ? (
				<StatusChip tone="info">{t('app.threads.board.outcomeNoop', 'No changes needed')}</StatusChip>
			) : null}
			{card.outcome === 'carried_over' ? (
				<StatusChip tone="ok">
					{t('app.threads.board.outcomeCarried', 'Done in a previous run')}
				</StatusChip>
			) : null}
			{card.acceptanceCriteria.length ? (
				<details className="thread-board-criteria">
					<summary>
						{t('app.threads.board.criteria', 'Acceptance criteria ({count})').replace(
							'{count}',
							String(card.acceptanceCriteria.length),
						)}
					</summary>
					<ul>
						{card.acceptanceCriteria.map((criterion) => (
							<li key={criterion}>{criterion}</li>
						))}
					</ul>
				</details>
			) : null}
			<ul className="thread-board-tasks" aria-label={t('app.threads.board.tasks', 'Tasks')}>
				{card.tasks.map((task) => (
					<li className="thread-board-task" key={task.id}>
						<span className="thread-board-task-title">{task.title}</span>
						<span className="thread-board-task-role mono">{task.role}</span>
						<StatusChip tone={toneForStatus(task.status)}>{task.status.replace(/_/g, ' ')}</StatusChip>
					</li>
				))}
			</ul>
			{card.runtime ? <span className="thread-board-card-runtime mono">{card.runtime}</span> : null}
		</m.article>
	);
}
```

3b. Append al final de `layout.css`:

```css
/* ---- Thread story board: per-story lanes moved by the product loop ---- */
.thread-board {
	container: thread-board / inline-size;
	display: grid;
	grid-template-rows: auto minmax(0, 1fr);
	gap: var(--space-3);
	min-width: 0;
	padding: var(--space-3) var(--space-4);
	background: var(--color-surface-workbench);
}

.thread-board-stage {
	display: flex;
	flex-wrap: wrap;
	align-items: center;
	gap: var(--space-2) var(--space-3);
	color: var(--color-text-secondary);
	font-size: var(--font-size-sm);
}

.thread-board-error {
	color: var(--color-status-danger);
	overflow-wrap: anywhere;
}

.thread-board-columns {
	display: grid;
	grid-template-columns: repeat(4, minmax(0, 1fr));
	gap: var(--space-3);
	min-height: 0;
}

.thread-board-column {
	display: grid;
	grid-template-rows: auto minmax(0, 1fr);
	gap: var(--space-2);
	min-width: 0;
	min-height: 0;
}

.thread-board-column-header {
	display: flex;
	align-items: center;
	gap: var(--space-2);
	padding: var(--space-2) var(--space-3);
	background: var(--color-surface-inset);
	border: 1px solid var(--color-border-subtle);
	border-radius: var(--radius-md);
	color: var(--color-text-secondary);
}

.thread-board-column-title {
	margin: 0;
	font-size: var(--font-size-sm);
	font-weight: var(--weight-semibold);
	color: var(--color-text-primary);
}

.thread-board-column-count {
	margin-left: auto;
}

.thread-board-column-scroll {
	display: grid;
	align-content: start;
	gap: var(--space-2);
	min-height: 0;
	overflow-y: auto;
}

.thread-board-card {
	display: grid;
	gap: var(--space-2);
	min-width: 0;
	padding: var(--space-3);
	background: var(--color-surface-panel);
	border: 1px solid var(--color-border-subtle);
	border-radius: var(--radius-md);
}

.thread-board-card[data-blocked="true"] {
	border-color: color-mix(in oklch, var(--color-status-danger) 55%, var(--color-border-subtle));
}

.thread-board-card-head {
	display: flex;
	align-items: baseline;
	gap: var(--space-2);
	min-width: 0;
}

.thread-board-card-index,
.thread-board-task-role,
.thread-board-card-runtime {
	color: var(--color-text-muted);
	font-size: var(--font-size-xs);
}

.thread-board-card-title {
	margin: 0;
	font-size: var(--font-size-sm);
	font-weight: var(--weight-semibold);
	color: var(--color-text-primary);
	overflow-wrap: anywhere;
}

.thread-board-card-story,
.thread-board-card-blocked {
	margin: 0;
	font-size: var(--font-size-xs);
	color: var(--color-text-secondary);
	overflow-wrap: anywhere;
}

.thread-board-card-blocked {
	display: flex;
	align-items: flex-start;
	gap: var(--space-1);
	color: var(--color-status-danger);
}

.thread-board-criteria {
	font-size: var(--font-size-xs);
	color: var(--color-text-secondary);
}

.thread-board-criteria ul {
	display: grid;
	gap: var(--space-1);
	margin: var(--space-1) 0 0;
	padding-left: var(--space-4);
}

.thread-board-tasks {
	display: grid;
	gap: var(--space-1);
	margin: 0;
	padding: 0;
	list-style: none;
}

.thread-board-task {
	display: flex;
	align-items: center;
	gap: var(--space-2);
	min-width: 0;
	font-size: var(--font-size-xs);
}

.thread-board-task-title {
	flex: 1 1 auto;
	min-width: 0;
	overflow: hidden;
	text-overflow: ellipsis;
	white-space: nowrap;
}

@container thread-board (max-width: 44rem) {
	.thread-board-columns {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
}

@container thread-board (max-width: 24rem) {
	.thread-board-columns {
		grid-template-columns: minmax(0, 1fr);
	}
}
```

3c. `default_catalog.json`: insertar inmediatamente después del bloque

```json
    "app.threads.consoleTitle": {
      "en": "Execution",
      "es": "Ejecución"
    },
```

las entradas (misma indentación, UTF-8 real, LF):

```json
    "app.threads.board.region": {
      "en": "Story board",
      "es": "Tablero de historias"
    },
    "app.threads.board.progress": {
      "en": "{done} of {total} stories done",
      "es": "{done} de {total} historias listas"
    },
    "app.threads.board.column.todo": {
      "en": "To do",
      "es": "Por hacer"
    },
    "app.threads.board.column.inProgress": {
      "en": "In progress",
      "es": "En curso"
    },
    "app.threads.board.column.qa": {
      "en": "QA",
      "es": "Pruebas QA"
    },
    "app.threads.board.column.done": {
      "en": "Done",
      "es": "Listo"
    },
    "app.threads.board.empty.todo": {
      "en": "Nothing waiting",
      "es": "Nada pendiente"
    },
    "app.threads.board.empty.inProgress": {
      "en": "No story in progress",
      "es": "Ninguna historia en curso"
    },
    "app.threads.board.empty.qa": {
      "en": "Nothing under QA",
      "es": "Nada en pruebas"
    },
    "app.threads.board.empty.done": {
      "en": "No story done yet",
      "es": "Aún no hay historias listas"
    },
    "app.threads.board.stage.planning": {
      "en": "Planning",
      "es": "Planificación"
    },
    "app.threads.board.stage.executing": {
      "en": "Executing stories",
      "es": "Ejecutando historias"
    },
    "app.threads.board.stage.security": {
      "en": "Security review",
      "es": "Revisión de seguridad"
    },
    "app.threads.board.stage.approval": {
      "en": "Awaiting approval",
      "es": "Esperando aprobación"
    },
    "app.threads.board.stage.delivered": {
      "en": "Delivered",
      "es": "Entregado"
    },
    "app.threads.board.stage.blocked": {
      "en": "Blocked",
      "es": "Bloqueado"
    },
    "app.threads.board.generalTasks": {
      "en": "General tasks",
      "es": "Tareas generales"
    },
    "app.threads.board.storyLine": {
      "en": "As {asA}, I want {iWant} so that {soThat}",
      "es": "Como {asA}, quiero {iWant} para {soThat}"
    },
    "app.threads.board.blockedFallback": {
      "en": "Blocked: QA or the runtime could not finish this story.",
      "es": "Bloqueada: QA o el runtime no pudieron terminar esta historia."
    },
    "app.threads.board.outcomeNoop": {
      "en": "No changes needed",
      "es": "Sin cambios necesarios"
    },
    "app.threads.board.outcomeCarried": {
      "en": "Done in a previous run",
      "es": "Lista en una ejecución anterior"
    },
    "app.threads.board.criteria": {
      "en": "Acceptance criteria ({count})",
      "es": "Criterios de aceptación ({count})"
    },
    "app.threads.board.tasks": {
      "en": "Tasks",
      "es": "Tareas"
    },
    "app.threads.event.story_progress": {
      "en": "Story progress",
      "es": "Avance de historia"
    },
```

Verificar tras editar: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import json;json.load(open('local_control_center/i18n/default_catalog.json',encoding='utf-8'))"` (JSON válido) y `git diff --stat` sin aviso de CRLF.

- [ ] **Step 4: Correr tests, typecheck, Biome y build**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_source_documentation_headers.py','-q','-p','no:randomly']))"`
Expected: PASS (guardrails visuales y catálogo bilingüe completo).
Run: `corepack pnpm@10.24.0 run typecheck:web` → exit 0.
Run: `corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/shell/ThreadBoard.tsx local-control-center/web/src/design-system/layout.css` y luego sin `--write` → sin errores.
Run: `corepack pnpm@10.24.0 run build:web` → exit 0.

- [ ] **Step 5: Commit**

```powershell
git add local-control-center/web/src/features/shell/ThreadBoard.tsx local-control-center/web/src/design-system/layout.css local_control_center/i18n/default_catalog.json tests_py/test_thread_board_frontend_contract.py
git commit -m "Feature (Threads): componente ThreadBoard con columnas por estado y franja de etapa" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Modo desarrollo del hilo (tablero principal + chat lateral + toggle + inspector) y E2E

**Files:**
- Create: `local-control-center/web/src/features/shell/useShellInspector.ts`
- Modify: `local-control-center/web/src/app/AppShell.tsx` (`:22` import; después de `expandInspector` `:180-187`; bloque `workbench` `:260-262`)
- Modify: `local-control-center/web/src/features/shell/ThreadConversation.tsx` (`:1-15` JSDoc, `:64-72` import ui, `:85-98` imports locales, tras `:198`, `:506-516`, `:609-626`)
- Modify: `local-control-center/web/src/design-system/layout.css` (append al final, tras el bloque de T10)
- Modify: `local_control_center/i18n/default_catalog.json` (tras las claves de T10)
- Create: `tests_web/thread-board.spec.js`
- Test: `tests_py/test_thread_board_frontend_contract.py` (append)

**Interfaces:**
- Consumes: T9 (`useThreadBoard`, `useThreadStage`, `latestBoardRefreshSequence`, `BoardMode`, prop `presentation`, `wakeThreadEventStream` vía `useThreadRemediations.execute`), T10 (`ThreadBoard`); la lista de bloqueos `ThreadBlockerList` del panel (`ThreadExecutionPanel.tsx:365-370`) debe seguir visible y operable en la presentación `strip` (el reintento se hace desde ahí mientras el inspector está plegado); `SegmentedControl` (`components/ui/SegmentedControl.tsx`, `role="radiogroup"` con opciones `role="radio"`); `mergeConsoleEvents` (`ThreadConversation.tsx:852`); `inspectorPanelRef`, `setInspectorCollapsed`, `isDesktop` (`AppShell.tsx:121-144`).
- Produces: `ShellInspectorContext`, `useShellInspector(): ShellInspectorControl | null`; `.thread-live-body[data-mode="chat"|"board"]`; toggle "Chat | Board" (grupo "Thread view"); el inspector se pliega una vez por hilo al entrar automáticamente en modo tablero (desktop).

- [ ] **Step 1: Escribir los tests que fallan**

1a. Append a `tests_py/test_thread_board_frontend_contract.py` (agregar `APP = WEB / "app"` a las constantes):

```python
def test_thread_conversation_switches_into_board_mode() -> None:
    source = _read(SHELL / "ThreadConversation.tsx")
    assert "data-mode={boardMode}" in source
    assert "<ThreadBoard" in source
    assert "presentation={boardMode === 'board' ? 'strip' : 'column'}" in source
    shell = _read(APP / "AppShell.tsx")
    assert "<ShellInspectorContext.Provider value={shellInspectorContext}>" in shell
    css = _read(LAYOUT_CSS)
    assert '.thread-live-body[data-mode="board"]' in css
    assert "@container thread-live" in css
    assert ".thread-transcript-pane" in css
    assert ".thread-live-scroll" in css
    assert ".thread-composer-dock" in css
    assert ".thread-execution-pane" in css
    assert ".thread-pipeline-step" in css
```

1b. Crear `tests_web/thread-board.spec.js`:

```js
/**
 * Thread story board: once a thread reaches execution with a planned backlog the center switches to
 * the development layout (board in the main area, chat as a column), the operator can flip back to
 * chat, the layout tripwires (execution pane, pipeline steps, composer) hold on desktop and on a
 * phone, and retrying a blocked story moves its card within the 2 s refresh SLA even when the event
 * stream had gone idle. Fixture thread with routed board/events/remediations; the backend contract
 * is covered by tests_py.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

async function openFixtureThread(page, { blocked = false } = {}) {
	const { projects } = await (await page.request.get('/api/v1/projects')).json();
	const project = projects.find((entry) => entry.status === 'active');
	expect(project).toBeTruthy();
	const state = { retried: false, eventRequests: 0 };
	const thread = {
		id: 'thread-story-board-fixture',
		projectId: project.id,
		ownerId: project.id,
		ownerType: 'workspace',
		title: 'Story board fixture',
		summary: '',
		status: blocked ? 'blocked' : 'running',
		metadata: {},
		createdAt: '2026-09-22T00:00:00Z',
		updatedAt: '2026-09-22T00:00:00Z',
	};
	const event = (sequence, type, payload = {}) => ({
		id: `story-board-${sequence}`,
		threadId: thread.id,
		projectId: project.id,
		sequence,
		type,
		payload,
		metadata: {},
		createdAt: thread.createdAt,
	});
	const progress = (sequence, status) =>
		event(sequence, 'story_progress', { loopId: 'loop-board-fixture', storyId: 'story-1', status, index: 1, total: 2 });
	const startEvents = [
		event(1, 'run_queued', { status: 'queued' }),
		event(2, 'worker_claimed', { workerId: 'worker-board' }),
		event(3, 'branch_ready', { loopId: 'loop-board-fixture' }),
		event(4, 'executing', { loopId: 'loop-board-fixture', toState: 'executing' }),
	];
	const runningEvents = [...startEvents, progress(5, 'qa')];
	const blockedEvents = [
		...startEvents,
		progress(5, 'blocked'),
		event(6, 'blocked', { stage: 'qa_rework', reason: 'QA failed after 2 rework rounds.' }),
	];
	const retriedEvents = [
		...blockedEvents,
		event(7, 'run_queued', { status: 'queued' }),
		event(8, 'executing', { loopId: 'loop-board-fixture', toState: 'executing' }),
		progress(9, 'done'),
	];
	const currentEvents = () => (!blocked ? runningEvents : state.retried ? retriedEvents : blockedEvents);
	const card = (storyId, index, title, status, column, task, extra = {}) => ({
		storyId,
		index,
		synthetic: false,
		title,
		asA: 'operations lead',
		iWant: `to see ${title.toLowerCase()}`,
		soThat: 'setup ends without support',
		priority: 'medium',
		status,
		column,
		blocked: false,
		blockedReason: null,
		outcome: null,
		runtime: 'codex_cli',
		acceptanceCriteria: [`${title} is verifiable.`],
		tasks: [task],
		...extra,
	});
	const todoCard = card('story-2', 2, 'Setup reminders', 'todo', 'todo', {
		id: 'task-2',
		title: 'Build reminders',
		role: 'backend_engineer',
		status: 'todo',
	});
	const checklistTask = { id: 'task-1', title: 'Build checklist', role: 'frontend_engineer' };
	const boardWith = (column, status, extra = {}) => ({
		loopId: 'loop-board-fixture',
		loopState: status === 'blocked' ? 'blocked' : 'qa_running',
		stage: status === 'blocked' ? 'blocked' : 'executing',
		progress: { done: column === 'done' ? 1 : 0, total: 2 },
		columns: ['todo', 'in_progress', 'qa', 'done'].map((id) => ({
			id,
			cards: [
				...(id === 'todo' ? [todoCard] : []),
				...(id === column
					? [card('story-1', 1, 'Readiness checklist', status, column, { ...checklistTask, status }, extra)]
					: []),
			],
		})),
	});
	const currentBoard = () =>
		!blocked
			? boardWith('qa', 'qa')
			: state.retried
				? boardWith('done', 'done')
				: boardWith('in_progress', 'blocked', { blocked: true, blockedReason: 'QA failed after 2 rework rounds.' });
	const retryAction = {
		id: 'remediation-board-retry',
		projectId: project.id,
		threadId: thread.id,
		loopId: 'loop-board-fixture',
		stage: 'qa_rework',
		blockerType: 'qa_failed',
		title: 'QA failed',
		description: 'QA failed after 2 rework rounds.',
		actionType: 'retry_loop',
		payload: { reason: 'QA failed after 2 rework rounds.' },
		status: 'pending',
		createdAt: thread.createdAt,
		resolvedAt: null,
	};
	await page.route('**/api/v1/overview', async (route) => {
		const response = await route.fetch();
		const overview = await response.json();
		await route.fulfill({ response, json: { ...overview, threads: [...overview.threads, thread] } });
	});
	await page.route(`**/api/v1/threads/${thread.id}`, (route) =>
		route.fulfill({ json: { thread, messages: [], decisions: [], artifacts: [], events: [] } }),
	);
	await page.route(`**/api/v1/threads/${thread.id}/remediations`, (route) =>
		route.fulfill({ json: { remediations: blocked && !state.retried ? [retryAction] : [] } }),
	);
	await page.route(`**/api/v1/remediations/${retryAction.id}/execute`, (route) => {
		state.retried = true;
		return route.fulfill({
			json: { remediation: { ...retryAction, status: 'executed' }, execution: { status: 'queued' } },
		});
	});
	await page.route(`**/api/v1/threads/${thread.id}/board`, (route) => route.fulfill({ json: currentBoard() }));
	await page.route(`**/api/v1/threads/${thread.id}/events?*`, (route) => {
		state.eventRequests += 1;
		const afterSeq = Number(new URL(route.request().url()).searchParams.get('afterSeq'));
		const events = currentEvents();
		const running = !blocked || state.retried;
		return route.fulfill({
			json: {
				events: events.filter((item) => item.sequence > afterSeq),
				lastSeq: events.at(-1).sequence,
				running,
				threadStatus: running ? 'running' : 'blocked',
			},
		});
	});
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const threadButton = page.getByRole('button', { name: thread.title, exact: true });
	if (!(await threadButton.isVisible())) {
		await page.locator('.thread-workspace-head').filter({ hasText: project.name }).click();
	}
	await threadButton.click();
	return state;
}

async function expectComposerReachable(page) {
	const composer = page.locator('.thread-composer-dock');
	await composer.scrollIntoViewIfNeeded();
	await expect(composer).toBeInViewport();
	const covered = await composer.evaluate((node) => {
		const rect = node.getBoundingClientRect();
		const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + Math.min(rect.height / 2, 20));
		return !node.contains(hit);
	});
	expect(covered).toBe(false);
}

test('Threads: a thread in execution switches to the story board and back to chat', async ({ page, isMobile }) => {
	test.skip(isMobile, 'desktop layout assertions');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		await openFixtureThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board).toBeVisible();
		await expect(board.locator('.thread-board-column')).toHaveCount(4);
		await expect(board.locator('.thread-board-column[data-column="qa"]')).toContainText('Readiness checklist');
		await expect(board.locator('.thread-board-column[data-column="todo"]')).toContainText('Setup reminders');
		await expect(page.getByRole('complementary', { name: 'Inspector', exact: true })).toBeHidden();
		const boardBox = await board.boundingBox();
		const chatBox = await page.locator('.thread-transcript-pane').boundingBox();
		expect(boardBox.x + boardBox.width).toBeLessThanOrEqual(chatBox.x + 1);
		await expect(page.locator('.thread-pipeline-step')).toHaveCount(7);
		const pane = page.locator('.thread-execution-pane');
		await expect(pane).toBeVisible();
		expect(await pane.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
		await expectComposerReachable(page);

		await page.getByRole('radio', { name: 'Chat' }).click();
		await expect(body).toHaveAttribute('data-mode', 'chat');
		await expect(page.locator('.thread-board')).toHaveCount(0);
		await expectComposerReachable(page);
		await page.getByRole('radio', { name: 'Board' }).click();
		await expect(body).toHaveAttribute('data-mode', 'board');
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads: retrying a blocked story moves its card within the refresh SLA', async ({ page, isMobile }) => {
	test.skip(isMobile, 'desktop layout assertions');
	await page.setViewportSize({ width: 1440, height: 900 });
	try {
		const state = await openFixtureThread(page, { blocked: true });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board.locator('.thread-board-column[data-column="in_progress"]')).toContainText(
			'Readiness checklist',
			{ timeout: 20_000 },
		);
		await expect.poll(() => state.eventRequests, { timeout: 20_000 }).toBeGreaterThanOrEqual(6);
		const idleRequests = state.eventRequests;
		const retry = page
			.locator('.thread-execution-pane .thread-remediation-card')
			.getByRole('button', { name: /Retry loop|Reintentar loop/ });
		await retry.click();
		const clickedAt = Date.now();
		await expect(board.locator('.thread-board-column[data-column="done"]')).toContainText('Readiness checklist', {
			timeout: 2_000,
		});
		expect(Date.now() - clickedAt).toBeLessThanOrEqual(2_000);
		expect(state.eventRequests).toBeGreaterThan(idleRequests);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});

test('Threads: the story board stacks above the chat on a phone without covering the composer', async ({ page, isMobile }) => {
	test.skip(!isMobile, 'stacked layout assertions');
	try {
		await openFixtureThread(page);
		const body = page.locator('.thread-live-body');
		await expect(body).toHaveAttribute('data-mode', 'board', { timeout: 20_000 });
		const board = page.getByRole('region', { name: 'Story board' });
		await expect(board).toBeVisible();
		const boardBox = await board.boundingBox();
		const chatBox = await page.locator('.thread-transcript-pane').boundingBox();
		expect(boardBox.y).toBeLessThan(chatBox.y);
		await expectComposerReachable(page);
		expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
		const pane = page.locator('.thread-execution-pane');
		expect(await pane.evaluate((node) => node.scrollWidth - node.clientWidth)).toBe(0);
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','-q','-p','no:randomly','-k','board_mode']))"`
Expected: FAIL (`data-mode={boardMode}` ausente).
Run: `corepack pnpm@10.24.0 run build:web` y `$env:PLAYWRIGHT_DASHBOARD_PORT='9437'; node node_modules/@playwright/test/cli.js test tests_web/thread-board.spec.js --reporter=line`
Expected: FAIL en ambos proyectos por timeout de `toHaveAttribute('data-mode', 'board')` (atributo inexistente); el test del reintento falla porque la región "Story board" no existe. Como prueba de que el SLA depende del despertador de T9 (no del azar del poll), tras dejar verde el resto correr una vez ese test con `wakeThreadEventStream(threadId);` comentado en `useThreadRemediations.ts` (build fresco): debe fallar en el `toContainText` de la columna `done` con timeout de 2 s (el stream inactivo espera 15 s); restaurar la línea antes de seguir.

- [ ] **Step 3: Implementación mínima**

3a. Crear `features/shell/useShellInspector.ts`:

```ts
/**
 * Lets the thread conversation fold the shell inspector away when it switches into the story board
 * layout, so the board, the chat column and the explorer fit without the inspector auto-opening;
 * the inspector stays one click away.
 * @author Rodrigo Mason
 */
import { createContext, useContext } from 'react';

export type ShellInspectorControl = { collapseInspector: () => void };

export const ShellInspectorContext = createContext<ShellInspectorControl | null>(null);

/** Returns the shell-owned inspector control, or null outside the shell (callers then no-op). */
export function useShellInspector(): ShellInspectorControl | null {
	return useContext(ShellInspectorContext);
}
```

3b. `AppShell.tsx`:
- Tras `import { ThreadRefreshContext } from '../features/shell/useThreadRefresh';` (`:22`) agregar `import { ShellInspectorContext } from '../features/shell/useShellInspector';` (Biome ordena).
- Después de `expandInspector` (`:180-187`) agregar:

```tsx
	const collapseInspector = useCallback(() => {
		if (!isDesktop) return;
		const handle = inspectorPanelRef.current;
		if (handle) {
			if (!handle.isCollapsed()) handle.collapse();
		} else {
			setInspectorCollapsed(true);
		}
	}, [inspectorPanelRef, isDesktop]);
	const shellInspectorContext = useMemo(() => ({ collapseInspector }), [collapseInspector]);
```

- En el bloque `workbench` (`:260-262`) reemplazar:

```tsx
				<ThreadRefreshContext.Provider value={threadRefreshContext}>
					{children}
				</ThreadRefreshContext.Provider>
```

por:

```tsx
				<ThreadRefreshContext.Provider value={threadRefreshContext}>
					<ShellInspectorContext.Provider value={shellInspectorContext}>
						{children}
					</ShellInspectorContext.Provider>
				</ThreadRefreshContext.Provider>
```

3c. `ThreadConversation.tsx`:
- JSDoc de cabecera (`:1-15`): agregar antes de `@author` la oración: "Once the thread reaches execution with a planned backlog the live body switches to the development layout (`data-mode="board"`): the story board ({@link ThreadBoard}) takes the main area, the execution panel becomes a strip above it and the transcript + composer move to a side column; a Chat | Board toggle overrides the automatic choice per thread."
- Import de `components/ui` (`:64-72`): agregar `SegmentedControl,` en orden alfabético (entre `EmptyState,` y `Skeleton,`).
- Imports locales (`:85-98`): agregar `import { ThreadBoard } from './ThreadBoard';`, `import { type BoardMode, latestBoardRefreshSequence } from './threadBoardModel';`, `import { useShellInspector } from './useShellInspector';`, `import { useThreadBoard } from './useThreadBoard';`, `import { useThreadStage } from './useThreadStage';` (luego `biome check --write` fija el orden).
- Después de `const remediations = useThreadRemediations(activeThreadId, mutate, latestEventSequence);` (`:198`) agregar:

```tsx
	// Development mode: the story board takes the main area once the loop executes stories.
	const stageEvents = useMemo(
		() => mergeConsoleEvents(detail?.events ?? [], eventStream.events),
		[detail?.events, eventStream.events],
	);
	const stage = useThreadStage(stageEvents, eventStream.threadStatus ?? detail?.thread.status ?? '');
	const board = useThreadBoard(activeThreadId, latestBoardRefreshSequence(stageEvents));
	const [manualBoardModes, setManualBoardModes] = useState<Record<string, BoardMode>>({});
	const manualBoardMode = activeThreadId ? manualBoardModes[activeThreadId] : undefined;
	const boardAvailable = (board.data?.progress.total ?? 0) > 0;
	const boardMode: BoardMode = boardAvailable
		? (manualBoardMode ?? (stage.inDevelopment ? 'board' : 'chat'))
		: 'chat';
	const selectBoardMode = useCallback(
		(mode: BoardMode) => {
			if (!activeThreadId) return;
			setManualBoardModes((current) => ({ ...current, [activeThreadId]: mode }));
		},
		[activeThreadId],
	);
	const shellInspector = useShellInspector();
	const inspectorFoldedForRef = useRef<string | null>(null);
	useEffect(() => {
		if (!activeThreadId || boardMode !== 'board' || manualBoardMode) return;
		if (inspectorFoldedForRef.current === activeThreadId) return;
		inspectorFoldedForRef.current = activeThreadId;
		shellInspector?.collapseInspector();
	}, [activeThreadId, boardMode, manualBoardMode, shellInspector]);
```

- Header (`:506-514`) reemplazar por:

```tsx
				<header className="thread-conversation-head">
					<div className="thread-conversation-title">
						<MessageSquare aria-hidden="true" size={16} />
						<h2>{detail.thread.title}</h2>
					</div>
					<div className="thread-conversation-actions">
						{boardAvailable ? (
							<SegmentedControl<BoardMode>
								className="thread-mode-toggle"
								label={t('app.threads.board.modeLabel', 'Thread view')}
								value={boardMode}
								onChange={selectBoardMode}
								options={[
									{ value: 'chat', label: t('app.threads.board.modeChat', 'Chat') },
									{ value: 'board', label: t('app.threads.board.modeBoard', 'Board') },
								]}
							/>
						) : null}
						<StatusChip tone={threadStatusTone(threadStatus)}>
							{threadStatus.replace(/_/g, ' ')}
						</StatusChip>
					</div>
				</header>
```

- `:516` `<div className="thread-live-body">` → `<div className="thread-live-body" data-mode={boardMode}>`.
- En `<ThreadExecutionPanel` (`:609-625`) agregar la prop `presentation={boardMode === 'board' ? 'strip' : 'column'}` tras `onOpenSettings={onOpenSettings}`.
- Justo antes del `</div>` que cierra `.thread-live-body` (`:626`) agregar:

```tsx
					{boardMode === 'board' && board.data ? (
						<ThreadBoard board={board.data} error={board.error} />
					) : null}
```

3d. Append al final de `layout.css`:

```css
/* ---- Thread development mode: story board in the main area, chat as a side column ---- */
.thread-live-layout {
	container: thread-live / inline-size;
}

.thread-conversation-actions {
	display: inline-flex;
	flex-wrap: wrap;
	align-items: center;
	justify-content: flex-end;
	gap: var(--space-2);
	min-width: 0;
}

.thread-live-body[data-mode="board"] {
	display: grid;
	grid-template-columns: minmax(0, 1fr) clamp(20rem, 30cqw, 28rem);
	grid-template-rows: auto minmax(0, 1fr);
	grid-template-areas:
		"strip chat"
		"board chat";
}

.thread-live-body[data-mode="board"] > .thread-transcript-pane {
	grid-area: chat;
	min-height: 0;
	border-left: 1px solid var(--color-border-subtle);
}

.thread-live-body[data-mode="board"] > .thread-execution-pane {
	grid-area: strip;
	max-height: 12rem;
	border-left: none;
	border-top: none;
	border-bottom: 1px solid var(--color-border-subtle);
}

.thread-live-body[data-mode="board"] > .thread-board {
	grid-area: board;
	min-height: 0;
}

.thread-execution-pane[data-presentation="strip"] .thread-pipeline {
	display: flex;
	flex-wrap: wrap;
	gap: var(--space-1) var(--space-4);
}

.thread-execution-pane[data-presentation="strip"] .thread-pipeline-step {
	grid-template-columns: auto auto auto;
}

/* Narrow center (or a phone): board above, chat below with its own height; the body scrolls so the
   composer (.thread-composer-dock) is always reachable and never painted over. The chat-mode
   contract (.thread-live-scroll as the only scroll region) is untouched. */
@container thread-live (max-width: 56rem) {
	.thread-live-body[data-mode="board"] {
		grid-template-columns: minmax(0, 1fr);
		grid-template-rows: auto auto auto;
		grid-template-areas:
			"strip"
			"board"
			"chat";
		min-height: 0;
		overflow-y: auto;
	}

	.thread-live-body[data-mode="board"] > .thread-transcript-pane {
		min-height: 24rem;
		border-left: none;
		border-top: 1px solid var(--color-border-subtle);
	}

	.thread-live-body[data-mode="board"] .thread-board-column-scroll {
		max-height: 50dvh;
	}
}
```

3e. `default_catalog.json`: insertar tras `"app.threads.event.story_progress"` (agregada en T10):

```json
    "app.threads.board.modeLabel": {
      "en": "Thread view",
      "es": "Vista del hilo"
    },
    "app.threads.board.modeChat": {
      "en": "Chat",
      "es": "Conversación"
    },
    "app.threads.board.modeBoard": {
      "en": "Board",
      "es": "Tablero"
    },
```

- [ ] **Step 4: Correr tests, gates web y E2E**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_thread_board_frontend_contract.py','tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_source_documentation_headers.py','tests_py/test_product_loop_result_taxonomy.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `corepack pnpm@10.24.0 run typecheck:web` → exit 0.
Run: `corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/features/shell/useShellInspector.ts local-control-center/web/src/app/AppShell.tsx local-control-center/web/src/design-system/layout.css` y luego sin `--write` → sin errores.
Run: `corepack pnpm@10.24.0 run build:web` → exit 0.
Run: `$env:PLAYWRIGHT_DASHBOARD_PORT='9437'; node node_modules/@playwright/test/cli.js test tests_web/thread-board.spec.js tests_web/threads.spec.js tests_web/thread-pipeline.spec.js tests_web/thread-inspector.spec.js tests_web/ide-resizable-shell.spec.js --reporter=line`
Expected: PASS en `desktop` y `mobile` (los tests de modo condicionados por `isMobile` quedan `skipped` en el proyecto opuesto). Si otro agente ocupa el puerto, usar otro de 94xx y confirmar propietario del listener antes de confiar en el resultado.

- [ ] **Step 5: Commit**

```powershell
git add local-control-center/web/src/features/shell/useShellInspector.ts local-control-center/web/src/app/AppShell.tsx local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/design-system/layout.css local_control_center/i18n/default_catalog.json tests_web/thread-board.spec.js tests_py/test_thread_board_frontend_contract.py
git commit -m "Feature (Threads): modo desarrollo con tablero principal, chat lateral y toggle manual" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Verificación integral (verificador independiente) y push

**Files:**
- Ninguno nuevo. Solo corrige lo que la verificación demuestre roto, dentro del alcance de T1-T11.

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: evidencia de verde por comando y la rama `dev` publicada.

- [ ] **Step 1: Suites Python del slice y regresión (background, leer exit real)**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_backlog_board.py','tests_py/test_git_cumulative_diff.py','tests_py/test_git_diff_capture.py','tests_py/test_product_loop_story_execution.py','tests_py/test_product_loop_coordinator.py','tests_py/test_product_loop_fsm_matrix.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_product_loop_state_order_frontend.py','tests_py/test_product_loop_reentry.py','tests_py/test_product_loop_delivery.py','tests_py/test_product_loop_api.py','tests_py/test_story_spec.py','tests_py/test_thread_cost_performance.py','tests_py/test_thread_board_api.py','tests_py/test_thread_board_frontend_contract.py','tests_py/test_aido_product_loop_real_e2e.py','tests_py/test_decision_engine_integration.py','tests_py/test_execution_timeout_reconciliation.py','tests_py/test_jev_resource_selection.py','tests_py/test_product_loop_no_reask_e2e.py','tests_py/test_product_loop_persistence_reconciliation.py','tests_py/test_product_owner_auto_recovery.py','tests_py/test_project_constitution_slice.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_research_resolution.py','tests_py/test_runtime_risk_review.py','tests_py/test_threads_api.py','tests_py/test_threads_operator_notes.py','tests_py/test_python_control_center.py','tests_py/test_local_worker_runtime.py','-q','-p','no:randomly']))"`
Expected: PASS. `test_local_worker_runtime.py` es flaky conocido bajo carga: si falla, re-ejecutarlo aislado antes de atribuirlo al slice.

- [ ] **Step 2: Gates de arquitectura, documentación, i18n y OpenAPI**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_real_readiness_architecture.py','tests_py/test_internal_mock_product_boundary.py','tests_py/test_execution_boundary_architecture.py','tests_py/test_vertical_slices_architecture.py','tests_py/test_web_rework_architecture.py','tests_py/test_source_documentation_headers.py','tests_py/test_i18n_platform.py','tests_py/test_ci_and_openapi_client.py','tests_py/test_operational_hardening_p0.py','tests_py/test_operational_http_boundaries.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe scripts/productive-truth-scan.py` → exit 0.
Run: `corepack pnpm@10.24.0 run openapi:generate` y `git status --porcelain local-control-center/web/src/api/generated/openapi.ts` → vacío (sin drift).
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format --check local_control_center/backlog/board.py local_control_center/backlog/repository.py local_control_center/workspaces_projects/git_worktrees.py local_control_center/product_loop/coordinator.py local_control_center/product_loop/phases/qa_gate.py local_control_center/product_loop/phases/story_loop.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py local_control_center/product_loop/phases/approval.py local_control_center/product_loop/repository.py local_control_center/product_loop/thread_board.py local_control_center/product_loop/models.py local_control_center/product_loop/api.py tests_py/test_backlog_board.py tests_py/test_git_cumulative_diff.py tests_py/test_product_loop_fsm_matrix.py tests_py/test_product_loop_coordinator.py tests_py/test_product_loop_story_execution.py tests_py/test_thread_board_api.py tests_py/test_thread_board_frontend_contract.py` → sin cambios pendientes.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/backlog/board.py local_control_center/backlog/repository.py local_control_center/workspaces_projects/git_worktrees.py local_control_center/product_loop/coordinator.py local_control_center/product_loop/phases/qa_gate.py local_control_center/product_loop/phases/story_loop.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py local_control_center/product_loop/phases/approval.py local_control_center/product_loop/repository.py local_control_center/product_loop/thread_board.py local_control_center/product_loop/models.py local_control_center/product_loop/api.py tests_py/test_backlog_board.py tests_py/test_git_cumulative_diff.py tests_py/test_product_loop_fsm_matrix.py tests_py/test_product_loop_coordinator.py tests_py/test_product_loop_story_execution.py tests_py/test_thread_board_api.py tests_py/test_thread_board_frontend_contract.py` → `All checks passed!`

- [ ] **Step 3: Gates web y E2E de regresión**

Run: `corepack pnpm@10.24.0 run typecheck:web` → exit 0.
Run: `corepack pnpm@10.24.0 exec biome check local-control-center/web/src/api/client.ts local-control-center/web/src/app/AppShell.tsx local-control-center/web/src/features/shell local-control-center/web/src/design-system/layout.css` → sin errores.
Run: `corepack pnpm@10.24.0 run build:web` → exit 0.
Run: `$env:PLAYWRIGHT_DASHBOARD_PORT='9438'; node node_modules/@playwright/test/cli.js test tests_web/thread-board.spec.js tests_web/threads.spec.js tests_web/thread-pipeline.spec.js tests_web/thread-inspector.spec.js tests_web/thread-remediations.spec.js tests_web/research-resolution.spec.js tests_web/risk-review.spec.js tests_web/thread-stream.spec.js tests_web/thread-cost.spec.js tests_web/ide-resizable-shell.spec.js --reporter=line` → PASS.
Run: `$env:PLAYWRIGHT_DASHBOARD_PORT='9439'; node node_modules/@playwright/test/cli.js test tests_web/thread-lifecycle-e2e.spec.js --project=desktop --reporter=line` → PASS (pipeline real de 14 pasos: verifica `durable.review.changedFiles == [GENERATED_FILE]` con el diff acumulado y que `.review-board` sigue siendo único).

- [ ] **Step 4: Revisión independiente contra el spec**

Un agente distinto del implementador (`code-reviewer-senior` + `reality-checker-senior`) contrasta el diff completo (`git diff <commit previo a T1>..HEAD`) con §1-§6 del spec y responde "¿en qué caso concreto falla?" para: orden por prioridad, cursor sobrevive a transiciones (invariante de `run.loop` fresco), bloqueo `qa_rework` con `retry_loop`, diff acumulado con base `dev` inexistente, reintento con PO que reescribe títulos (debe re-ejecutar, no saltar), modo tablero sin tablero disponible (debe quedar en chat), TL LLM que omite historias (debe caer al respaldo determinista con evento `technical_lead_fallback`, no bloquear).

- [ ] **Step 5: Publicar (política AIDO: commit+push por funcionalidad a `dev`)**

```powershell
git fetch origin dev
git status -sb
git pull --rebase origin dev
git push origin dev
```

Si el `pull --rebase` trae commits del escritor concurrente, re-ejecutar Steps 1-3 antes del push.

---

## Self-Review

**Cobertura del spec (sección → task):**

| Spec | Requisito | Task(s) |
|---|---|---|
| §1.1 | N historias ⇒ N runs, cada uno con solo sus tareas y su spec | T1 (historias del output del PO, lote vacío para historia sin tareas), T5 (`test_each_story_gets_its_own_scoped_developer_run`, `test_a_story_without_llm_tasks_is_planned_by_the_deterministic_fallback`, `test_a_story_uncovered_by_llm_and_fallback_blocks_before_any_developer_run`) |
| §1.2 | `todo → in_progress → qa → done|blocked` persistido; tablero refleja en ≤ 2 s | T5 (estados + eventos), T8 (endpoint), T9 (refetch por `story_progress`; poll de 1 s `useThreadEventStream.ts:11`; `wakeThreadEventStream` tras una remediación porque el stream inactivo baja a 15 s, `:12,72-77`), T11 (E2E bloqueado→reintento→tarjeta en `done` en ≤ 2 s) |
| §1.3 | Seguridad y aprobación sobre el diff acumulado | T2 (diff + trabajo sin commitear), T5 (QA y evidencias de todas las historias hacia la aprobación), T6 (artefacto para Security y `patchArtifactIds` en job/ActionRequest de aprobación; commit por historia obligatorio; rechazo de trabajo sin commitear) |
| §1.4 | Cambio automático a modo desarrollo + toggle | T9 (`useThreadStage`), T11 |
| §1.5 | Reintento salta historias `done` | T5 (sin pendientes ⇒ `stories_already_done`, sin developer), T7 (carry-over uno a uno; todas terminadas ⇒ 0 runs) |
| §3.1 | Orden por prioridad y emisión del backlog (no de las tareas); lote "Tareas generales" al final | T1 (`order_story_batches(..., story_order=...)`, `list_user_stories_for_output`, test con tareas invertidas), T5, T8 |
| §3.2 | Iteración por historia, `noop`, rework por historia, bloqueo con cursor, arista `next_story`, cursor durable, reintento, cancelación sin cambios | T3, T4, T5, T7 (cancelación: `_transition_run_state` sigue siendo el único corte, cruzado en cada historia) |
| §3.3 | `capture_cumulative_diff` como `git_patch`; `security.py`/`approval.py` lo consumen; `noop` no aporta | T2, T6 (`security.py:113` recibe el artefacto; `approval.py` recibe `run.review` acumulada **y** `patchArtifactIds` en job, ActionRequest y `durableRun.approval`) |
| §3.4 | Tabla de estados; escritura con `update_user_story/update_agent_task`; no tocar `agent_assignments`; evento `story_progress {storyId,status,index,total}` | T5 |
| §3.5 | Límites N×15 min / costo | Sin código; visibles como progreso k/N (T10) — riesgo aceptado por el spec |
| §4 | Endpoint `/threads/{id}/board` (historias vía `productOwnerOutputId`, tareas vía el loop), mapeo en `backlog/board.py`, `ThreadBoard` con patrón de columnas, sin drag & drop, refresco por eventos | T1, T8 (historia sin tareas visible y contada), T10, T11 |
| §5 | `useThreadStage` (MILESTONE_INDEX literal), `data-mode="board"`, columna `clamp(20rem, 30cqw, 28rem)`, panel en franja, toggle `SegmentedControl`, inspector no auto-abre, `@container`, transición `m.* layout` + `crossfade`, tripwires | T9, T10, T11 |
| §6 | Pruebas backend y frontend listadas; anclas de un solo run adaptadas | T1-T11 (anclas `:7377`, `:8513` adaptadas en T4; `:3049,:3097,:3133,:5045,:8478,:8539,:8644,:8407` siguen verdes con backlog de 1 historia, verificado en T5 Step 4) |

**Brechas del spec resueltas en este plan (decisiones, no preguntas abiertas):**
1. Reintento: `retry_loop` re-ejecuta el mensaje completo y el PO crea filas nuevas (`remediations/service.py:1732-1740`, `coordinator.py:1900-1936`), así que "saltar `done`" se resuelve por huella de spec del cursor del loop origen (mismo proyecto e hilo), no por `storyId`.
2. Base del diff acumulado: `project.git.baseBranch` (default `dev`, `coordinator.py:4785-4794`) y, si no resuelve, el `sourceCommit` del worktree; `merge-base` no está allowlisted, por eso `diff <base>...HEAD`.
3. Etapa `planning` agregada al `Literal` para loops aún no en ejecución y hilos sin loop (tablero vacío).
4. Tarjetas `blocked` se muestran en "En curso" con marca y causa (el spec fija 4 columnas).
5. Historia `noop`: transición `executing → qa_running` (trigger `story_noop`) para mantener la invariante "Security solo tras `qa_running`".
6. Sin historias pendientes (todas `done` o arrastradas) ⇒ no corre el developer: `executing → qa_running` con trigger `stories_already_done` y el loop sigue al diff acumulado (el caso típico es un reintento tras un bloqueo en Security). En un workspace no-git no hay diff acumulado que revisar y el guard "sin cambios" bloquea en `review` (fail-closed).
7. `agentAssignments` del payload sigue siendo el roster del equipo (una asignación por rol, `coordinator.py:3010-3050`); solo `agentTasks` y `storySpecs` se acotan a la historia.
8. Inspector: `AppShell` sigue auto-abriéndolo al seleccionar; al entrar automáticamente en modo tablero se pliega una vez por hilo (queda a un clic).
9. Fuente del tablero: ids de `durableRun.agentTasks` + filas frescas (las tareas reutilizadas conservan el `metadata.loopId` original) + todas las historias del output del PO (`durableRun.productOwner.productOwnerOutputId`).
10. Historia del PO sin tareas de un Technical Lead LLM ⇒ **no** bloquea: se planifica con el `TechnicalLeadPlanner` determinista (`coordinator.py:2299`, cubre todas las historias, `backlog/technical_lead_planner.py:840-856`), sus tareas llevan `metadata.technicalLeadFallback=True` y se emite `technical_lead_fallback {loopId, storyId}`. Solo si el respaldo tampoco produce tareas para ella ⇒ bloqueo `technical_lead` fail-closed antes del primer run del developer (decisión de arquitecto 2026-09-22, ver §Architect Decisions).
11. QA y evidencias: la aprobación y el SecurityAgent reciben las del intento aprobado de cada historia (`run.qa_results`/`run.evidence_ids` acumulados), no solo las de la última.
12. Commit por historia obligatorio en `git_worktree` (en un run único sigue best-effort) + rechazo del diff acumulado con trabajo sin commitear fuera de `.aido/`. Ante fallos de commit se corrige la causa raíz; la guarda no se relaja (decisión 2026-09-22).
13. Workspace no-git + reintento con todas las historias `done` ⇒ bloqueo en `review` aceptado (fail-closed; spec §8).

**Placeholder scan:** sin TBD/TODO; cada función referida está definida en una task o existe en el repo con `archivo:línea`.

**Consistencia de nombres:** `run_story_batches`, `match_carried_over`, `list_user_stories_for_output`, `wakeThreadEventStream`, `capture_cumulative_review`, `block_without_changed_files`, `aggregate_story_reviews`, `capture_cumulative_diff`, `latest_planned_loop_for_thread`, `ThreadBoardService.board`, `get_thread_board` ⇒ operación `get_thread_board_api_v1_threads__thread_id__board_get`, `getThreadBoard`, `useThreadBoard`, `useThreadStage`, `derivePipeline`, `EXECUTING_STEP_INDEX`, `presentation`, `ShellInspectorContext`, `useShellInspector` — usados igual en todas las tasks.

## Execution Notes

- **Lanes y orden recomendado:**
  1. Ola 1 (paralelo, 3 agentes): T1 (PATTERN), T2 (PATTERN), T3 (MECHANICAL).
  2. Ola 2: T4 (MECHANICAL) en Lane A; T8 (PATTERN) en Lane B apenas T1 esté integrado (regen OpenAPI con Lane A verde).
  3. Ola 3: T5 (HIGH) en Lane A; T9 (PATTERN) en Lane C tras T8.
  4. Ola 4: T6 (HIGH) → T7 (HIGH) en Lane A; T10 (PATTERN) → T11 (HIGH) en Lane C.
  5. Gate final: T12 con verificador independiente (no quien implementó).
  Máximo 2-3 agentes simultáneos; integrar y correr la suite de la task antes de abrir la siguiente ola.
- **Tier por task:** HIGH (modelo más capaz, esfuerzo alto): T5, T6, T7, T11. PATTERN (modelo eficiente): T1, T2, T8, T9, T10, T12. MECHANICAL (delegable a modelo local y verificado por tests): T3, T4.
- **Riesgos y trampas:**
  - Invariante del cursor: `_durable_run_patch` reemplaza `durableRun` completo con el del `loop` recibido; `story_loop._save_progress` refresca `run.loop` tras cada escritura. Cualquier fase nueva que transicione con un `loop` viejo borraría `storyProgress`.
  - `test_product_loop_coordinator.py` es largo (8.780 líneas): correrlo en background y leer el exit real; no correr dos suites pesadas ni dos Playwright a la vez.
  - Escritor concurrente en `dev`: `git fetch` antes de cada commit grande; `openapi:generate` hornea el WIP ajeno si el árbol está sucio — revisar `git diff` del `openapi.ts` antes de stagear.
  - El commit por historia pasa a ser obligatorio en runs por historia (T6, confirmado por decisión 2026-09-22): si algún test existente con worktree git tenía el commit fallando en silencio (policy o identidad git), ahora bloquea en `review`. Investigar y corregir la causa raíz del fallo; nunca relajar la guarda. El diff acumulado además rechaza trabajo sin commitear fuera de `.aido/`.
  - Respaldo determinista del TL (T5): un TL LLM que omita historias ya no las deja fuera en silencio ni bloquea: el `TechnicalLeadPlanner` determinista las planifica y queda el evento `technical_lead_fallback` por historia. Vigilar en la primera corrida real cuántas historias caen al respaldo (señal de un TL LLM débil). Solo bloquea en `technical_lead` si el respaldo tampoco planifica; `test_product_loop_coordinator.py:4716` se adapta a esa semántica (3e).
  - `stories_already_done` en workspace no-git termina bloqueado en `review` (sin diff acumulado): **aceptado** como fail-closed (decisión 2026-09-22, spec §8); no agregar un bypass. En git el reintento tras Security revisa el diff de la rama estable del hilo (una rama por hilo, spec §2).
  - El despertador del stream (T9) vive a nivel de módulo; su cleanup elimina el waker al desmontar. Si aparece otro llamador de `useThreadEventStream` (hoy `ThreadConversation` y `BottomPanel`), también despierta — es intencional.
  - Job largo: N historias ≈ N × 900 s en un lease `agent_cli` (spec §3.5); el envelope compartido del runner nativo (`process_supervision/context.py:131`) puede cortar backlogs grandes — mitigación futura = enfoque B, para el que el cursor ya queda listo.
  - Cancelación a mitad de historia deja esa historia en `in_progress` en BD; el siguiente run la re-ejecuta (no está `done`). Aceptado por el spec ("Cancelación: sin cambios").
  - Edit-tool en Windows puede volver CRLF un archivo LF: verificar `git diff` sin aviso "CRLF will be replaced" en cada commit (catálogo JSON, CSS, TSX).
  - `.review-board` debe seguir siendo único en el DOM (lifecycle E2E en modo estricto); el tablero del hilo usa exclusivamente `.thread-board*`.
- **Orden de imports (ruff `I001`):** los bloques de import mostrados pueden no quedar en el orden exacto de isort (p. ej. `contextlib`/`pathlib` en `test_product_loop_fsm_matrix.py`); si `ruff check` reporta `I001`, aplicar `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check --fix <py files>` y re-ejecutar `ruff check`. En TS, `biome check --write` resuelve organizeImports.
- **Evidencia requerida por task:** salida del comando pytest/tsc/biome/build/Playwright con su exit code; no declarar verde sin haberlo visto.

## Cross-Model Review Log

Revisión cruzada (otra familia de modelos, perfil "analysis"). Cada ítem se verificó contra el código actual antes de decidir.

| # | Severidad | Ítem | Decisión | Motivo (una línea) |
|---|---|---|---|---|
| 1 | blocker | `git diff <base>...HEAD` excluye lo no commiteado y el commit por historia es best-effort | ACCEPTED | Verificado `execution.py:502-518` (commit en `try/except`, el loop sigue) y rango de tres puntos sin working tree; T2 devuelve `status`, T6 exige commit por historia y rechaza trabajo sin commitear, con dos tests nuevos. |
| 2 | major | Approval no consume el artefacto acumulado | ACCEPTED | Parcial pero real: `approval.py:46-47,119-125` solo recibe `run.review` (archivos/diffRef), sin el `git_patch`; T6 agrega `patchArtifactIds` a job, ActionRequest y `durableRun.approval`, y lo prueba. |
| 3 | major | QA y evidencias de cada historia se sobrescriben | ACCEPTED | Verificado `execution.py:520` y `qa_gate.py:272`; T5 acumula `story_qa_results`/`story_evidence_ids`, los vuelca en `run.qa_results`/`run.evidence_ids` y prueba dos historias con evidencias y QA distintas en el job y en la evidencia de seguridad. |
| 4 | major | `or list(batches)` re-ejecuta todo y el set de huellas salta duplicados | ACCEPTED | Contradecía spec §3.2/§1.5; T5 permite `pending=[]` con `stories_already_done` (sin developer), T7 empareja uno a uno con `match_carried_over` y prueba ambos casos. |
| 5 | major | Desempate por orden de tareas, no del backlog | ACCEPTED | Verificado: el TL puede devolver tareas en cualquier orden y se persisten así (`coordinator.py:2296-2340`); T1 recibe `story_order` desde `list_user_stories_for_output` (orden `rowid`) y prueba tareas invertidas. |
| 6 | major | Historia del PO sin tareas desaparece | ACCEPTED | Verificado `team_planning.py:158` (solo exige alguna tarea); T5 la planifica con el respaldo determinista del TL (decisión 2026-09-22) y bloquea `technical_lead` solo si el respaldo tampoco la cubre; T8 muestra todas las historias del output del PO (tarjeta sin tareas contada en el total). |
| 7 | major | Refresco ≤ 2 s no garantizado tras reintentar | ACCEPTED | Verificado `useThreadEventStream.ts:12,72-77` (15 s en reposo) y `useThreadRemediations.ts:112-128` (no toca el stream); T9 agrega `wakeThreadEventStream` y T11 un E2E bloqueado→reintento→tarjeta en `done` en ≤ 2 s, con contraprueba sin el despertador. |
| 8 | minor | `_runtime_id` guarda el runtime preferido, no el usado | ACCEPTED | `run.runtime_result["runtime"]["id"]` existe (`test_product_loop_coordinator.py:263`); T5 lo prioriza y el test de evidencias lo verifica en el cursor. |
| 9 | minor | Comandos ruff no ejecutables | ACCEPTED | Las líneas abreviadas (`... -m ruff`, "ruff format + ruff check sobre") se expandieron a comandos completos con la ruta del intérprete, format y check por separado, incluida la lista explícita de T12. |

**Resultado:** 9 ACCEPTED, 0 REJECTED. Sin cambios en la cantidad de tasks (12); T1, T6 y T9 suman archivos (`backlog/repository.py`, `phases/approval.py`, `useThreadEventStream.ts`, `useThreadRemediations.ts`) sin romper la disjunción de las lanes.

## Architect Decisions (2026-09-22)

| # | Decisión | Impacto en el plan |
|---|---|---|
| 1 | Una historia del PO sin tareas de un Technical Lead LLM **no** bloquea: se auto-planifica con el `TechnicalLeadPlanner` determinista (planner por defecto, `coordinator.py:2299`), sus tareas llevan `metadata.technicalLeadFallback=True` y se registra el evento de hilo `technical_lead_fallback {loopId, storyId}`. Solo si el planner determinista tampoco produce tareas ⇒ bloqueo `technical_lead`. | T5: `_with_deterministic_fallback` en `_generate_agent_tasks`, evento en `team_planning.py:158`, dos tests (respaldo y bloqueo de último recurso), `:4716` adaptado; T8 texto; Task Graph, Self-Review §1.1 e ítem 10, Execution Notes, Review Log #6; spec §3.2, §6, §8. |
| 2 | Workspace no-git + reintento con todas las historias `done` ⇒ bloqueo en `review` aceptado (fail-closed). | Sin código nuevo; spec §8 (riesgo aceptado), Self-Review ítem 13, Execution Notes. |
| 3 | Commit por historia obligatorio: ante fallos se corrige la causa raíz, nunca se relaja la guarda. | Sin cambios de código respecto de T6; reafirmado en spec §3.2/§8, Self-Review ítem 12 y Execution Notes. |
