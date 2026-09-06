# Limpieza controlada posterior a P0 — AIDO

Fecha: 2026-09-05.

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
