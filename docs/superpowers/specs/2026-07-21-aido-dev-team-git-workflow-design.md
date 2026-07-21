# AIDO como equipo de desarrollo real — git · PR · CI/CD

**Fecha:** 2026-07-21
**Estado:** Aprobado (diseño). Implementación por slices.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Contexto y objetivo

El product loop de AIDO orquesta proyectos externos (p.ej. `votacionenlinea`). Hoy crea **una rama por mensaje de usuario** (`codex/product-owner-<loopid>` / `codex/product-loop-<loopid>`), nunca la limpia, y **no hace ningún `git commit`** (el entregable es un patch + evidencia). El objetivo es que AIDO trabaje como un **equipo de desarrollo real**: base `dev`, una rama por unidad de trabajo (HU), commits reales, review de Technical Lead + QA, y aterrizaje configurable (push directo o PR) con borrado de rama, iterando la misma rama ante hallazgos.

## 2. Estado actual (causas raíz, verificadas)

- **Proliferación de ramas:** `product_loop/coordinator.py:4789` y `:5845` hardcodean `codex/product-{owner,loop}-{loop['id'][-12:]}`; cada mensaje acuña un loop nuevo (`coordinator.py:4408` → `create_loop`, `repository.py:89`), sin ruta "reusar loop activo del thread". Aun un turno solo de discovery crea rama+worktree.
- **Sin GC:** `archive_workspace` (`workspaces_projects/repository.py:461`) corre `git worktree remove --force` pero **nunca `git branch -D`**, y solo se invoca en el prompt-workspace efímero y en el endpoint HTTP manual; los worktrees durables del loop nunca se archivan.
- **Sin commits / sin push:** no hay `git commit` en loop/developer/coordinator/runner; el diff se captura como artefacto `git_patch`. **No existe `git push` en todo el repo.**
- **Base = `HEAD`:** `allocate_workspace` default `base_branch="HEAD"`; el coordinator no pasa base → forkea de lo que sea. El plumbing de base branch ya existe pero no se alimenta.
- **PR desconectado y roto:** `workflows/issue_to_pr` tiene gates reales (Developer→QA→Security→Architect→DevOps + rework acotado) pero está **desconectado del loop ágil**, la aprobación es **humana** (el `technical_lead` es etiqueta cosmética), y `create_pull_request` apunta a una rama local **nunca pusheada** → GitHub 422. No hay merge (`PUT /pulls/{n}/merge` inexistente).
- **Setting muerto:** `security.branch.policy` (`feature_branch|dev_direct`) existe en `settings/registry.py:201-209` + UI/i18n, pero **ningún runtime lo lee**.

## 3. Modelo objetivo

Por HU: rama `feature/<hu-slug>` off `dev`, estable y reusada entre turnos/rework; roles ejecutan en worktrees por rol pero **commitean a la rama HU**; gates QA/Security/Architect/DevOps con rework acotado en la misma rama; review del **Technical Lead**; aterrizaje según modo del proyecto; borrado de rama al mergear. HU nueva del PO → rama nueva. Loop continuo con perfiles + skills.

## 4. Decisiones (bloqueadas)

- **D1 — Commits reales.** El developer commitea su salida a la rama de la HU cada iteración. Squash opcional al mergear.
- **D2 — Identidad estable por HU.** Rama derivada del `storyId`/HU (no del loop id). Una rama por HU, compartida por todos los roles (worktree por rol internamente). 1 PR por HU.
- **D3 — 3 modos por proyecto:** `direct_push` (merge local a `dev`, sin PR), `auto_pr` (push + PR + LT auto-aprueba y mergea), `manual_pr` (igual, aprueba un humano). Sin remoto → `auto_pr`/`manual_pr` degradan a `direct_push` con aviso. Config: `project.git.integrationMode` + `project.git.baseBranch` (default `dev`).
- **D4 — LT auto-approve.** Gate LT real que auto-aprueba solo si `qa_verdict_allows_completion` ∧ Security no-blocked ∧ Architect/DevOps ok ∧ veredicto LT = approve. En `auto_pr`: aprueba y **mergea** (espera CI externa verde si hay). Rechazo (LT o QA) → iterar en la misma rama.
- **D5 — Aterrizaje.** `direct_push`: merge local `feature→dev` (+push si remoto). `auto_pr`/`manual_pr`: commit → push → PR → aprobación → merge API → borrar rama. Se agrega el `git push` inexistente (brokered).
- **D6 — Reuso.** Conectar el loop ágil a la máquina `issue_to_pr` (reusar gates + cliente PR + tabla `pull_requests`), arreglando push/422 y agregando merge + GC + LT auto-approve. CI baseline = gates internos; CI externa = slice final opcional.

## 5. Slices (cada uno: spec → plan → implementación → tests → commit)

### Slice 1 — Identidad estable por HU + commits + GC (fundacional)
**Objetivo:** matar la proliferación y que las ramas lleven commits.
- Derivar el nombre de rama de una **clave estable** (thread/HU id), reutilizando el loop/rama entre turnos de la misma spec (revisar `coordinator.py:4408` regeneración de loop id; cuidar `[[aido-functionality-blocker-escape]]`, `[[aido-thread-cancel-worker-race]]`).
- Paso de **commit** del developer a la rama de trabajo tras cada iteración (brokered `git add`/`git commit`).
- **GC:** `git branch -D` al mergear/abandonar; cablear `archive_workspace` a estado terminal del loop.
- **Base = `dev`** pasada a `allocate_workspace` (leerá el setting en Slice 2; aquí default `dev`).
**Criterio de éxito:** N turnos sobre el mismo thread ⇒ 1 rama estable (no N); rama con commits; al terminar/abandonar la rama se borra. **Tests:** unit del branch-naming estable + reuse; e2e que verifica 1 rama tras 2 turnos y su borrado; regresión de la que hoy asume `codex/product-loop-` (`test_aido_product_loop_real_e2e.py:326`).

### Slice 2 — Config `project.git.*`
- `SettingDescriptor` `project.git.baseBranch` (string, default `dev`) y `project.git.integrationMode` (enum `direct_push|auto_pr|manual_pr`, default `manual_pr`). Sección UI `git` en `sections.tsx` + i18n bilingüe. Reemplaza/retira el `security.branch.policy` muerto.
- Consumo vía `resolve_setting_value` en el seam `_with_operator_cost_decision` (estampar en job metadata) + `_prepare_developer_execution`.
**Éxito/tests:** el loop forkea del baseBranch configurado; el modo se propaga al job; tests de resolución + degradación sin remoto.

### Slice 3 — Aterrizaje `direct_push`
- `git push` brokered en `git_workspace/service.py` + `merge` local (`feature→dev`) + borrado de rama.
- Nuevo paso de delivery en el coordinator tras `awaiting_approval` cuando el modo = `direct_push`.
**Éxito/tests:** con `direct_push`, aprobar ⇒ commits en `dev` + rama borrada; sin remoto funciona; con remoto pushea.

### Slice 4 — PR real (`manual_pr`)
- Arreglar `promote_patch_to_branch`/`create_pull_request` (pushear la rama antes del POST → sin 422). Merge API `PUT /pulls/{n}/merge`. Borrado de rama post-merge.
- Conectar el loop a la creación de PR en modo `manual_pr` (aprueba humano).
**Éxito/tests:** con `manual_pr`, se abre PR real (mock GitHub), aprobación humana ⇒ merge + rama borrada; el 422 no reaparece.

### Slice 5 — LT auto-approve (`auto_pr`) + iterar-en-la-misma-rama
- Gate LT real (reemplaza la etiqueta cosmética) que auto-aprueba según QA/Security/Architect/DevOps + veredicto propio; en `auto_pr` aprueba y mergea.
- Rechazo LT/QA ⇒ `rework_task` en la misma rama (más commits) ⇒ re-review.
**Éxito/tests:** `auto_pr` end-to-end sin humano; rechazo ⇒ segunda ronda de commits en la misma rama ⇒ aprueba ⇒ merge.

### Slice 6 — CI/CD externo + pulido del loop continuo
- Hook opcional: en `auto_pr`, esperar el status verde de un check externo (GH Actions) antes del merge.
- Pulido del loop continuo (perfiles/skills/mejora continua).
**Éxito/tests:** merge bloqueado si el status externo no es verde (mock).

## 6. Riesgos / constraints

- `git push` no existe → agregarlo brokered con la policy de `run_brokered_git`.
- Reusar el loop toca `coordinator.py:4408`; no reintroducir el bug de re-pregunta ni la race de cancel/worker.
- Gates del repo: **i18n bilingüe (es≠en)**, **openapi regen**, arch tests, `test:py`, `check:web`, doc-author (`@author Rodrigo Mason`), el pre-commit ruff nuevo.
- El writer autónomo concurrente edita este checkout y commitea a `dev` a mitad de sesión → commits acotados por slice, re-fetch/rebase antes de push.
- Proyectos externos pueden ser locales (sin remoto) → degradación de `auto_pr`/`manual_pr` a `direct_push`.

## 7. Secuencia

1 → 2 → 3 → 4 → 5 → 6. Cada slice se entrega verde (tests + gates) y se commitea/pushea a `dev` por funcionalidad.
