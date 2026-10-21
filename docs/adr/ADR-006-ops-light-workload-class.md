# ADR-006: Separar `ops_light` (git de AIDO y sondas de runtime) de `qa_light`

## Estado: Aceptada

## Contexto

ADR-004 dejó a `qa_light` reservando su tope completo (4 GiB) porque es bimodal: comparten clase
un `git` de decenas de MB y una suite de pytest de ~4 GB. Con el piso de memoria vigente, eso hace
que en un equipo con menos de ~5 GiB libres sobre el piso hasta un `git worktree add` o un
"Revalidate runtime" (`remediations.execute` con `revalidate_runtime`) quede en `resource_wait`.
Evidencia: build #25 de ci-hub (2026-10-17), el E2E lifecycle cayó en el paso 8 con
`aggregate_memory_budget: 4.0 GiB requested, 0.0 GiB already reserved, 0.9 GiB of headroom`.

Medición sobre una copia (`sqlite3.backup`) de la BD de la instalación real, 2026-10-18:
532 leases `qa_light` (2026-09-04 → 2026-09-26) y 1.881 procesos `qa_light`. Se mide el pico por
lease (máximo de `managed_processes.peak_memory_bytes` de sus procesos).

| origen de la lease `qa_light` | leases | p50 | p90 | p99 | máx |
|---|---|---|---|---|---|
| operaciones encoladas (`operation.execute`) | 82 | 126 MB | 332 MB | 361 MB | 365 MB |
| proceso suelto (`managed-process-*`: pytest/ruff/semgrep/git/gitleaks) | 448 | 91 MB | 3.314 MB | 4.338 MB | 4.340 MB |

Por operación (lease de la operación, pico del proceso más grande que cubrió):

| operación | leases | máx |
|---|---|---|
| `remediations.execute` (revalidate) | 24 | 365 MB |
| `models.detect_cli_runtime` | 32 | 132 MB |
| `git.refresh` (antes de pasar a `control_plane`) | 3 | 228 MB |
| `git.checkout_git_branch` | 1 | 94 MB |
| `models.codex_capabilities` | 1 | 75 MB |

De los procesos sueltos, 65 de 448 (14,5%) superan 1 GB y 304 quedan bajo 500 MB: la bimodalidad
está ahí, no en las operaciones. El argv sólo se persiste como hash (`command_fingerprint`), así
que los procesos sueltos no se pueden separar por ejecutable con los datos actuales.

Las mutaciones de git sin lease histórica propia se midieron el 2026-10-21 corriéndolas por
`run_git` (supervisor real, BD scratch) sobre un clon del propio repositorio AIDO (1.268 archivos,
`.git` de 124 MB): `init`, `remote add`, `branch` (crear, renombrar, borrar), `checkout`,
`worktree remove`/`prune` y `status` quedan entre 35,6 y 36,6 MB; `worktree add` llega a 71,3 MB.
Todas caben en el request con holgura. Un repositorio mucho más grande sube `worktree add`
(escribe el árbol completo), y por eso el tope sigue en 4 GiB.

## Decisión

Se evaluaron dos caminos:

1. **Dar a `qa_light` un request medido y dejar que el desalojo graduado (ADR-005) proteja el
   pico**, como se hizo con `agent_cli`. Rechazado: en `agent_cli` el request (1 GiB) cubre el p90
   y sólo la cola p99 queda al desalojo. En `qa_light` el p90 de los procesos sueltos ya es 3,3 GB;
   un request cercano al modo git dejaría ~15% de las leases muy por encima de su reserva. Con
   `resources.maxLightWorkloads` = 2, dos suites admitidas a la vez podrían sobrecomprometer ~7 GiB
   y el desalojo graduado pasaría a cancelar suites como rutina, no como excepción.
2. **Separar una clase liviana con request medido.** Elegida.

- **Nueva clase `ops_light`**: mismo tope que `qa_light` (4 GiB, 25% CPU,
  `TEST_RUNNER_PROCESS_LIMIT`), así nada que antes corría queda cortado por el Job Object, y
  `memory_request_bytes = 512 MiB` (máximo medido 365 MB + 40%, el mismo margen de ADR-004).
- **Pasan a `ops_light`**: `remediations.execute` con `validate_runtime`/`revalidate_runtime`
  (`executions/workloads.py`), `models.detect_cli_runtime`, `models.codex_capabilities`, las
  mutaciones de git encoladas (`git.init_git_repository`, `add_git_remote`, `test_git_remote`,
  `create_git_branch`, `apply_git_branch_policy`, `checkout_git_branch`,
  `branch_manager_rename`/`prune`/`remove_worktrees`),
  `workspaces.allocate_workspace`/`archive_workspace`/`workspace_cleanup_apply`, y el git
  productivo de `security_policy/git_command_runner.py`.
- **Se quedan en `qa_light` (reservan 4 GiB)**: suites y linters (`classify_workload` de pytest,
  ruff, semgrep, gitleaks, y también un `git` que lance un agente), los pasos de `quality/`,
  `git.git_gitleaks_scan`, `git_branches.scan`, `retrieval.retrieval_reindex`, `agents.sync_skills`,
  contenedores Docker capados ≤ 4 GiB y `native_diagnostics`. No tienen medición propia o su uso
  real puede llegar al tope.
- **`git.branch_manager_delete` también se queda en `qa_light`**: con borrado remoto corre
  `git push --delete` (`git_workspace/branch_manager.py::_delete_remote`), que dispara `pre-push`,
  y ese hook suele correr la suite del repositorio. Ninguna operación `ops_light` corre `commit`,
  `merge` ni `push`; esos pasan por el broker (`classify_workload`, `qa_light`) o por la lease del
  job que los cubre.
- `ops_light` conserva la prioridad de cola de reparación que tenía como `qa_light`
  (`JobsRepository.peek_next_job`).

## Consecuencias positivas

- Un Revalidate o un git de workspace exige piso + 0,5 GiB en vez de piso + 4 GiB: se admite con
  la holgura de 0,9 GiB del build #25 (cubierto por test con 2,0 y 0,9 GiB). En ci-hub, el build
  #33 (47c62b02, que incluye este cambio) terminó SUCCESS con el E2E lifecycle completo en desktop
  y mobile.
- Las suites mantienen su reserva completa: el desalojo graduado sigue siendo excepción.

## Consecuencias negativas

- `checkout` y `worktree add` disparan `post-checkout`. Un hook así que lance una suite usaría
  memoria de suite con reserva de 512 MiB: el tope de 4 GiB lo contiene, pero queda
  sobrecomprometido contablemente hasta en ~3,5 GiB, y bajo presión el desalojo graduado lo elige
  antes que a una suite porque excede su reserva. Es raro (`post-checkout` suele ser liviano), pero
  queda abierto.
- La medición directa de las mutaciones git se hizo sobre un repositorio de tamaño medio; las
  leases `ops_light` reales permiten revisar el request con la próxima muestra.
- Una clase más en `WorkloadClass` y en el cliente OpenAPI generado.

## Alternativas consideradas

- **Request por operación en vez de por clase** (la lease ya guarda `memory_request_bytes`):
  descartado por ahora; introduce un segundo mecanismo de reserva paralelo a los perfiles.
- **Mover estas operaciones a `control_plane`**, como `git.refresh`: descartado; las mutaciones
  corren hooks y código del repo, y `control_plane` es esencial (la admisión no revisa su memoria).

## Impacto

`host_resources/{models,profiles}.py`, `executions/workloads.py`, `git_workspace/api.py`,
`workspaces_projects/api.py`, `agents/model_gateway_api.py`, `security_policy/git_command_runner.py`,
`jobs_approvals/repository.py`, `web/src/api/generated/openapi.ts`. Tests:
`tests_py/test_host_resource_governor.py` (nuevo caso de holgura),
`tests_py/{test_runtime_team_execution_gate,test_operational_http_boundaries}.py`.
