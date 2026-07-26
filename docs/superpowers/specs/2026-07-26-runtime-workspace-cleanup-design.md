# Política de limpieza/archivado de workspaces runtime huérfanos

- Fecha: 2026-07-26
- Estado: aprobado para implementación (sesión autónoma; supuestos explícitos abajo)
- Módulos: `local_control_center/workspaces_projects/`, `local-control-center/web/src/features/workspaces/`

## Problema

`GET /api/v1/projects/{id}/git/status` tarda 5-7 s por proyecto porque `git worktree list
--porcelain` (~4,6 s) y `git branch --format` (~1,6 s) escalan con las decenas de worktrees
runtime acumulados en repos reales (66 workspaces activos en la BD). Todo `/api/` se serializa
bajo el lock global (`local_control_center/api.py:172`), así que cada status git congela el API
completo esos segundos.

Causa raíz de la acumulación: el product loop asigna UN workspace estable por hilo
(`reuse_existing=True`, task_id `product-loop-<suffix12>` / `product-owner-<suffix12>`, donde
`suffix12 = stable_task_suffix(thread_id)`) y por diseño NO lo archiva al terminar el turno —
la rama estable del hilo debe sobrevivir entre mensajes. Solo el aterrizaje exitoso
(`delivery.py`) archiva. Hilos archivados/eliminados, runs bloqueados y workspaces de PO nunca
se limpian, y sus worktrees físicos quedan registrados en el repo real del proyecto.

## Objetivo verificable

1. Un endpoint read-only produce el plan de limpieza por proyecto: candidatos clasificados con
   motivo, sin tocar disco ni repo.
2. Un endpoint de aplicación, gateado por el write-token y por una selección explícita de IDs
   confirmada por el usuario en la UI, archiva las filas y elimina los worktrees físicos
   (`git worktree remove --force` + `git worktree prune`), con guardas fail-closed.
3. Tests de backend cubren clasificación, guardas y aplicación sobre un repo git real en
   `tmp_path`; la UI pasa los gates i18n/arquitectura.

## Decisión de enfoque

Se evaluaron tres enfoques:

- (A) Daemon de auto-limpieza por TTL: rechazado — borra sin confirmación del usuario en repos
  reales; contradice el requisito explícito.
- (B) Cascada al archivar/eliminar hilo: parcial — no drena el backlog de 66 existentes, cambia
  flujos existentes y el archivo de hilo pasaría a tener efecto destructivo implícito en disco.
- (C) **Plan + aplicación confirmada (elegido)**: scan read-only clasifica huérfanos; el usuario
  revisa y confirma la selección en la UI; el apply re-valida cada ID server-side (fail-closed)
  y ejecuta la limpieza vía las primitivas ya existentes (`archive_workspace`,
  `remove_git_worktree`, `delete_git_branch`, git brokered por policy).

## Clasificación de candidatos (fail-closed)

Un workspace activo (status en `ACTIVE_WORKSPACE_STATUSES`) del proyecto es candidato solo si
cae en una de estas clases:

| Clase | Regla |
| --- | --- |
| `path_missing` | La ruta del workspace ya no existe en disco (fila rancia; el prune limpia la entrada admin del repo). |
| `thread_archived` | `task_id` calza `product-(loop\|owner)-<suffix12>` y el hilo con ese sufijo está `archived`. |
| `thread_deleted` | Ídem con hilo `deleted` (soft-delete). |
| `owner_missing` | `task_id` calza el patrón del product loop pero ningún hilo NI product_loop del proyecto produce ese sufijo (huérfano de BD divergente). |
| `workflow_finished` | `workflow_run_id` apunta a un run con `completed_at` no nulo o status terminal. |

Worktrees físicos sin fila activa (`repo_orphans`): entradas de `git worktree list --porcelain`
del repo del proyecto cuya ruta vive bajo `<root>/.tmp/workspaces/` y no corresponde a ningún
workspace activo de la BD. Se reportan aparte y se limpian con remove/prune si el usuario los
selecciona.

Guardas de exclusión (nunca candidatos):

- Workspaces actualizados en las últimas 24 h (`FRESH_WORKSPACE_GRACE_HOURS`), para no competir
  con runs en vuelo.
- Workspaces cuya ruta NO es descendiente estricta de `<root>/.tmp/workspaces/` (protege los
  control workspaces de `_ensure_control_workspace`, cuyo path es la raíz del repo real).
- Hilos `open`/activos: su workspace estable jamás se propone (archivarlo degradaría el
  aislamiento del siguiente turno a copia de fuente, porque la rama ya existe y
  `worktree add -b` fallaría).

## Contrato API

Router de `workspaces_projects` (misma app, mismo `require_write`):

- `GET /api/v1/projects/{project_id}/workspaces/cleanup/plan`
  → `{ projectId, generatedAt, candidates: [...], repoOrphans: [...], summary: {...} }`.
  Cada candidato: `workspaceId, taskId, ownerAgentId, path, pathExists, isolationType, branch,
  reason, threadId?, threadTitle?, updatedAt`. Read-only: una sola pasada de
  `git worktree list --porcelain` brokered + queries SQLite.
- `POST /api/v1/projects/{project_id}/workspaces/cleanup` (write token) con body
  `{ workspaceIds: [...], orphanWorktreePaths: [...], deleteBranches: bool, reason: str }`.
  - Rechaza selección vacía (422). No existe modo "all": la confirmación es la lista concreta
    que el usuario revisó.
  - Por cada `workspaceId`: re-clasifica; si ya no es candidato → `skipped_not_candidate`
    (nunca falla el batch completo). Si sigue siéndolo → `archive_workspace(reason,
    delete_branch=deleteBranches)` (remueve worktree brokered, archiva rama, libera allocation).
  - Por cada `orphanWorktreePath`: verifica que sigue listado como worktree del repo Y que es
    descendiente estricto de `<root>/.tmp/workspaces/`; si el directorio existe →
    `worktree remove --force`; si no existe, lo cubrirá el prune. Fuera de la raíz → `refused`.
  - Al final: `git worktree prune` (brokered) una sola vez.
  - Respuesta: resultado por ítem + `prune` + `summary`; evento
    `workspace.cleanup.applied` en el EventBus con el resumen.

Advertencia explícita del contrato (se muestra en la UI): `worktree remove --force` descarta
cambios NO commiteados del worktree; los commits sobreviven en la rama salvo que se marque
`deleteBranches`. No se calcula `git status` por worktree en el plan (66 × status sería otra
espera de minutos bajo el lock global); la confirmación advierte esto en texto.

## UI (WorkspacesPage)

- Nueva sección "Limpieza de workspaces" bajo el inventario actual: selector de proyecto,
  botón "Analizar candidatos" (GET plan), tabla con checkbox por candidato (razón, hilo, rama,
  ruta, pathExists) + lista de repoOrphans, checkbox "Eliminar también las ramas de trabajo"
  (default off).
- Botón "Limpiar seleccionados" abre `Dialog` de confirmación con el conteo exacto, la
  advertencia de descarte de cambios sin commit y el nombre del repo; el POST solo se dispara
  desde ese diálogo. Toast con el resumen y re-fetch del plan al terminar.
- Componentes de `components/ui`, copy vía `t()` con claves bilingües registradas; sin CSS
  nuevo fuera de utilidades existentes.

## Errores y observabilidad

- Cada resultado por ítem lleva `status` + `reason` (`archived`, `removed`, `refused_*`,
  `skipped_not_candidate`, `cleanup_failed`); nada lanza a mitad de batch.
- Git siempre vía `run_brokered_git`/primitivas existentes → policy + agent runs + trazas.
- Evento `workspace.cleanup.applied` con conteos para auditoría.

## Testing

- `tests_py/test_workspace_cleanup.py`:
  - clasificación: hilo archivado → candidato; hilo abierto → excluido; path faltante →
    candidato; workspace fresco (<24 h) → excluido; control workspace (path = raíz del repo) →
    jamás; workflow run terminal → candidato.
  - aplicación sobre repo git real en `tmp_path`: worktree removido del disco y de
    `git worktree list`, fila `archived`, allocation `released`, prune ejecutado, rama borrada
    solo con `deleteBranches`, path fuera de la raíz → `refused`.
  - API: roundtrip TestClient con token; selección vacía → 422.
- Web: gates `test:py` (i18n + arquitectura web) y suite `test:web`; spec Playwright dirigido
  con mocks de los dos endpoints para el flujo analizar → seleccionar → confirmar → resumen.

## Supuestos declarados (sesión autónoma)

1. La confirmación requerida es la del panel (selección explícita + diálogo), no un
   ActionRequest en `/approvals`; el write-token ya gatea la mutación.
2. No se auto-engancha la limpieza al archivo/borrado de hilos (cambio de comportamiento de
   flujos existentes); queda como siguiente slice si se quiere prevención además de drenaje.
3. `deleteBranches` default off: las ramas `codex/product-*` son recuperables y su borrado es
   opt-in por limpieza (la lentitud de `git branch` también se mitiga al removerlas, pero la
   pérdida de refs no debe ser silenciosa).
