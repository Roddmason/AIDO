# Limpieza controlada posterior a P0 — AIDO

Fecha: 2026-09-05.

Las secciones iniciales registran la primera pasada. La continuación del
2026-09-06, al final, actualiza sus resultados y pendientes.

## Baseline y alcance

- HEAD de partida: `90d602c7c01a2cfee19c112dedd947767e2acbf8` en
  `codex/aido-operational-hardening-p0`.
- Rama de esta limpieza: `codex/aido-cleanup-post-p0`, creada desde ese HEAD.
- El manifiesto de cierre
  `.tmp/operational-hardening-p0/authorized-close/final-traceability.json`
  identifica `90d602c7` como `deliveryHead`. La aceptación P0 sigue en
  `BLOCKED`; este documento no la cambia ni la reinterpreta.
- El árbol versionado estaba limpio, no se detectaron procesos de AIDO ajenos
  al auditor y se registraron 170 worktrees. No se modificó ni eliminó ningún
  worktree.

## Inventario y resultado aplicado

| Categoría | Candidatos comprobados | Resultado |
| --- | ---: | --- |
| Cachés regenerables de código/pruebas propios | 48 directorios, 671 archivos, 11.232.703 bytes lógicos (10,712 MiB) | **No aplicado**: el terminal bloqueó la eliminación recursiva, incluso con una ruta literal ya verificada. |
| Código interno sin consumidores | Imports/variables no usados según Ruff | Sin candidatos: `F401`, `F811`, `ARG001`, `ARG003`, `ARG004` y `ARG005` pasan. |
| Dependencia directa `@tanstack/react-virtual@3.14.3` | Sin import, carga dinámica, entrypoint ni consumidor; sólo manifiesto, lockfile y una nota de uso planificado | **No aplicado**: `pnpm remove` no puede usar el store original enlazado desde `node_modules`; no se editó el lockfile manualmente ni se reinstaló. |

Los 48 candidatos eran `.pytest_cache`, `.ruff_cache` y `__pycache__` bajo
`local_control_center/`, `scripts/`, `local-control-center/scripts/` y
`tests_web/fixtures/`. Todos estaban bajo el repositorio, sin reparse points,
sin archivos versionados y sin procesos del repositorio en ejecución al
momento del dry-run.

## Elementos retenidos

- `tests_py/__pycache__/` se conservó completo: el informe P0 referencia un
  bytecode específico de ese directorio como evidencia de la exclusión de
  Semgrep.
- `.tmp/operational-hardening-p0/`, sus SQLite/WAL/SHM, manifiestos, respaldos
  y recibos se conservan sin modificación.
- `.venv/`, `.tmp/p0-python/`, `node_modules/`,
  `local-control-center/dist/`, `test-results/`, `.claude/`, `.git/`, los
  worktrees, migraciones, fixtures, licencias y políticas no fueron tocados.
- La instalación operativa externa de Claude está fuera de alcance y no fue
  leída ni escrita.

## Dependencias y lógica

Las dependencias runtime, dev, test y extras fueron revisadas separadamente.
Se conservaron los extras opcionales y las dependencias de plataforma aunque
no estén activas localmente. No se eliminó código, componente, script,
migración ni dependencia, por lo que no hay cambio de lógica productiva ni
lockfile en este lote.

Biome informó las seis advertencias preexistentes de `reloadToken`. No se
aplicaron: ese valor funciona como disparador de recarga y quitarlo alteraría
el comportamiento aunque el análisis léxico lo marque como redundante.

## Verificación y límites

- `uv run --extra dev ruff check . --select F401,F811,ARG001,ARG003,ARG004,ARG005`
  — exit 0.
- `corepack pnpm@10.24.0 run lint:web` — exit 0, seis advertencias preexistentes.
- `git diff --check` sobre el documento de esta limpieza — exit 0. No hubo
  lote de código o dependencias aplicado.
- El runner PR completo no se relanzó. Su implementación actual escribe
  recibos en la raíz de evidencia P0 protegida y la limpieza no debe mezclar
  resultados nuevos con ese cierre.

En la observación previa a la aplicación, `H:` tenía 377.425.129.472 bytes
libres (351,505 GiB). No hubo borrado, por lo que no existe ahorro lógico ni
variación de espacio físico atribuible a esta limpieza. El espacio libre de un
volumen tampoco permite atribuir ahorro físico a archivos lógicos cuando hay
hardlinks, compresión o actividad concurrente.

## Pendientes concretos

1. Aplicar los 48 directorios de caché sólo desde un entorno que permita
   eliminación explícita de las rutas ya verificadas.
2. Restaurar o seleccionar el store `H:\.pnpm-store\v10` de forma explícita
   antes de retirar `@tanstack/react-virtual` mediante pnpm; no reconstruir ni
   migrar `node_modules` como parte de esta limpieza.
3. Mantener los bloqueos de aceptación P0 y sus recibos tal como están.

## Continuación aplicada — 2026-09-06

Baseline real: `c1dbc86be3a0b8c4f531345f6ff50f91231e4b1e`, árbol limpio en
`codex/aido-cleanup-post-p0`; sin nueva rama ni procesos AIDO detectados antes
de los cambios. Se respetó §14 de la aceptación. Intérprete conservado:
`.venv/Scripts/python.exe`, CPython 3.13.15, ligado a `.tmp/p0-python/`.

| Acción | Aplicada o no | Evidencia | Pendiente exacto |
| --- | --- | --- | --- |
| Retirar `@tanstack/react-virtual@3.14.3` | Sí, del manifiesto/lockfile | Commit `014ec7eccacf3642ca112fda98bb0540ba5a52c3`; sin imports, reexports, carga dinámica, configuración o entrypoints consumidores | No se desinstaló del `node_modules` activo |
| Regenerar lockfile con pnpm 10.24.0 | Sí, exit 0 | Sólo 20 líneas eliminadas: dependencia y transitiva exclusiva `@tanstack/virtual-core@3.17.1`; sin upgrades ajenos | Ninguno para coherencia manifiesto/lockfile; no hace falta restaurar el store antiguo para esta operación |
| Instalar candidato aislado | No, admisión exit 75 | Una copia independiente de 836 archivos versionados, idénticos por SHA-256; sin `.git`, `.venv` ni `node_modules` copiados/enlazados | `aggregate_memory_budget` y episodios `host_cpu_saturated`; instalar con capacidad disponible, luego typecheck/build |
| Pruebas focalizadas | Sí, 56 PASS, exit 0 | Ejecutadas sobre esa copia con Python supervisado; hashes del candidato en recibos | No prueban la instalación JavaScript sin TanStack |
| Lint web | Sí, exit 0 | 205 archivos, seis advertencias `reloadToken` preexistentes; sin fixes | Se usó la herramienta instalada, no como prueba de reinstalación |
| Cachés | No; 0 archivos eliminados | Revalidación de las mismas 48 rutas: 671 archivos, 11.232.703 bytes lógicos, sin archivos versionados ni reparse points | No se repitió la eliminación rechazada por política en la primera pasada; revisar excepciones indicadas abajo |
| Worktrees | No; 0 retirados | 170 registrados; Git status e identidad de rama/HEAD comprobados, sin errores | 1 checkout de limpieza; 1 con contenido ajeno no preservado; 168 referenciados por manifiestos; 0 desechables demostrados; 0 sin categoría |

Comandos de validación (salvo Git, mediante el ejecutor supervisado existente
de `local_control_center.quality`, sin cambiar reservas):

- `corepack pnpm@10.24.0 install --lockfile-only --ignore-scripts --no-frozen-lockfile`
  — exit 0; no modificación de la instalación activa.
- `corepack pnpm@10.24.0 install --frozen-lockfile --ignore-scripts`, solicitado
  en la copia — **no llegó a ejecutarse**; admisión agotada tras 120 segundos,
  exit 75. No se autorizaron scripts ni se cambió el store global.
- `.venv/Scripts/python.exe -B -m pytest -q tests_py/test_project_hygiene_and_license.py tests_py/test_web_rework_architecture.py tests_py/test_ci_and_openapi_client.py`
  — exit 0, 56 passed en 3,20 s; ejecutable de la venv preservada y cwd de la copia.
- `corepack pnpm@10.24.0 run lint:web` — exit 0.
- `node node_modules/@biomejs/biome/bin/biome check package.json` — exit 1,
  **0 archivos procesados**, porque la configuración existente ignora ese archivo;
  no es PASS ni se alteró el gate. El manifiesto sí fue procesado por pnpm y tests.
- `git diff --check` — exit 0; hook Gitleaks del commit de dependencia — exit 0.

Los recibos propios están en `.tmp/post-p0-cleanup-20260906/`, ignorada por Git:
`lockfile.json`, `install.json`, `tests.json`, `lint.json`, `lint-web.json`,
`tested-candidate.json`, `worktrees.json`, `cache-dry-run.json`,
`protected-after.json` y `volumes-after.json`. No sustituyen recibos P0.

Intervención manual pendiente para cachés: revisar **sólo** las rutas exactas
de `cache-paths.txt` contra `cache-dry-run.json`, en un entorno autorizado,
revalidando uso/referencias/identidad antes de borrar. No es una aprobación
masiva: hay 10 bytecodes sin fuente actual bajo los caches de
`local_control_center/`, `agents/` y `backlog/`. Los otros dos avisos del dry-run
corresponden a bytecode pytest de `evidence/test_results.py`, cuya fuente sí existe.
Se retienen hasta resolver las dudas; no se cambió de herramienta ni se elevaron
permisos para eludir el rechazo anterior. `tests_py/__pycache__` sigue excluido.

El worktree `.claude/worktrees/strange-bell-806bff` conserva cambios ajenos en
`tests_web/threads.spec.js`, además de `.tmp`, `.venv`, `node_modules`, build y
resultados ignorados. Los otros 168 tienen ruta, proyecto y tarea enlazados en
sus manifiestos de ejecución: Git limpio no demuestra fin de ejecución ni
ausencia de leases o referencias persistidas. Se requiere acreditar ese estado
y la coherencia de su retirada antes de usar `git worktree remove` sin `--force`.
No se llamó al limpiador existente: su vía de archivado actualiza registros y
allocations; no se autoriza esa reparación en esta tarea. Los tamaños parciales
de los manifiestos truncados no se suman como tamaño total ni ahorro.

Archivos eliminados y bytes lógicos liberados por borrado: **0 / 0 B**.
Entre 04:55:10 y 05:06:02 UTC, C: pasó de 87.991.578.624 a 87.979.610.112 B
libres (delta **−11.968.512 B**); H: de 377.363.988.480 a 377.345.888.256 B
(delta **−18.100.224 B**). Son observaciones del volumen, no ahorro atribuible:
la copia/recibos propios añaden archivos y existe actividad externa; no se midió
asignación física, compresión ni hardlinks. La dependencia sigue instalada en
el checkout activo. No hay cambio de lógica, funciones o componentes retirados.

SHA-256, tamaño y mtime sin cambios para `.venv/pyvenv.cfg`, los dos metadatos
de `node_modules`, el manifiesto final P0 y el documento de aceptación.
No se escribió sobre bases operativas, evidencia P0, backups, credenciales,
sesiones, intérprete base ni build servido. No hubo inferencia, login, cambios
de proveedor, push, merge o cutover. Typecheck/build aislados y PR completo
siguen pendientes; P0 **BLOCKED**, smoke y watchdog conservan su estado anterior.

## Incidente del watchdog — 2026-09-06, limpieza detenida

No se retiraron otras dependencias, cachés ni worktrees, ni se reconstruyeron node_modules/venv.
La validación ligera del manifiesto con Node procesó JSON real y comprobó las secciones
`dependencies`, `devDependencies`, `optionalDependencies` y `peerDependencies`: exit **0**;
`@tanstack/react-virtual` y `@tanstack/virtual-core` ausentes. El diff frente al padre de
`014ec7ec` sigue limitado a **1 línea eliminada en package.json y 20 en pnpm-lock.yaml**.
Ese desglose corrige el conteo agregado impreciso anterior, sin ampliar la retirada.

Biome excluye el manifiesto por `biome.json:files.includes`, que admite sólo TS/TSX/CSS/JSON
del frontend. El contrato PR ejecuta Biome sobre `local-control-center/web`, no sobre el root.
Se conserva esa política: el exit 1 histórico con cero archivos **no es PASS** y no se oculta
con `--no-errors-on-unmatched`. Validación del JSON y lint frontend son resultados distintos.

La corrección del watchdog y sus 184 pruebas, el único smoke real fallido por stack overflow
nativo y la trazabilidad se agregaron a §14.7 de `docs/operational-hardening/p0-operational-acceptance.md`.
No se presenta QA backend como validación de la eliminación de la dependencia.

Instalación congelada en copia aislada, typecheck/build con esa instalación, PR completo y release
siguen pendientes. Snapshot `2026-09-06T06:58:11.531Z`: 31,497 GiB disponibles, `build_heavy` 16 GiB
+ reserva de seguridad 16 GiB; déficit **540.205.056 bytes**, cero reservas activas,
`aggregate_memory_budget`. No se lanzaron fuera de admisión. Evidencia en la colección P0 existente,
`N/watchdog-resources.json` y `N/watchdog-final-traceability.json`; no se creó otra colección de informes.
