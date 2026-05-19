# AI Frontend Upgrade Plan

## Diagnóstico

- El problema real no era sólo visual. El dashboard modelaba mal el producto:
  - un único workspace activo,
  - sin sesiones nombradas por workspace,
  - configuración runtime demasiado libre,
  - chat/composer fuera de contexto,
  - semántica de secretos poco honesta según entorno,
  - y pipelines mostrados como tarjetas, no como flujo operativo.
- La superficie correcta sigue siendo híbrida:
  - dashboard web local como front principal,
  - TUI como fallback operativo,
  - ambos sobre el mismo core.

## Decisiones técnicas

1. Modelar `workspace -> session -> chat/pipeline/promptPack`.
   - `global.json` pasa a catálogo de workspaces con IDs.
   - `.claude/team-workspace.json` incorpora sesiones persistentes.

2. Mantener sesiones como fuente de verdad local.
   - Claude Code recibe `--name`.
   - Codex conserva el nombre sólo como metadata local porque el CLI no expone paridad nativa.

3. Endurecer configuración runtime.
   - Claude Code: `model`, `effort`, `permissionMode`.
   - Codex: `model`, `sandbox`, `profile`.
   - Gemini: `researchModel`, `mediaModel`.
   - Ollama: sólo modelos detectados localmente.
   - No hay modo libre/raw en esta iteración.

4. Separar alcance de configuración.
   - Defaults por workspace.
   - Overrides por sesión para políticas.
   - Roster y conectores siguen siendo workspace-scoped.

5. Mostrar secretos según contexto real del host.
   - Windows: `process.env` + registro.
   - Estados:
     - `present`
     - `missing`
     - `host-only`

6. Rehacer la UI como control plane operacional.
   - Toolbar superior con selector de workspace, sesión y postura git/runtime.
   - Sidebar sin marketing ni metadata redundante.
   - Chat/composer sólo en `Chats` y `Agents`.
   - Pipelines con grafo vía `@xyflow/react` + `dagre`.

## Componentes a crear o modificar

### Estado y dominio (ahora archivado en Node legacy)

- `Legacy/Node.js/local-control-center/lib/global-state.mjs`
- `Legacy/Node.js/local-control-center/lib/state.mjs`
- `Legacy/Node.js/local-control-center/lib/pipeline.mjs`
- `Legacy/Node.js/local-control-center/lib/core.mjs`
- `Legacy/Node.js/local-control-center/lib/orchestrator.mjs`
- `Legacy/Node.js/local-control-center/lib/runtime.mjs`

### API y entrypoints

- `local_control_center/api.py`
- `local_control_center/store.py`
- `local_control_center/worker.py`
- `local-control-center/build.mjs`

### Dashboard web

- `local-control-center/web/index.html`
- `local-control-center/web/dashboard-client.jsx`
- `local-control-center/web/dashboard.css`

### Validación

- `tests_py/test_python_control_center.py`
- `Legacy/Node.js/local-control-center/tests/*` queda sólo como referencia histórica.

## Riesgos

- `core.init()` sigue siendo lento por discovery/probes locales.
- El warning `DEP0190` sigue presente por `shell: true` en Windows.
- El repo completo sigue sin `lint` ni build global confiable.
- El bundle web del dashboard ronda ~2 MB; aceptable para local, pero no fino.

## Plan de implementación

1. Migrar `global.json` a catálogo real de workspaces.
2. Introducir sesiones persistentes en el state local y migración retrocompatible.
3. Propagar `sessionId` a chats, pipelines y prompt packs.
4. Añadir CRUD de sesiones en core, server y TUI.
5. Pasar nombre de sesión a Claude Code con `--name`.
6. Endurecer runtime options con un registry basado en flags reales del CLI local.
7. Cambiar la semántica de secretos según el runtime local real.
8. Exponer `/api/config/options` y endpoints de sesiones.
9. Rehacer el dashboard:
   - top toolbar,
   - selectors de workspace y sesión,
   - config estricta por provider,
   - chat sólo en `Chats` y `Agents`,
   - graph de pipelines.
10. Verificar con tests, build y smoke del dashboard servido por el binario.

## Checklist de validación

- Un estado legacy sin sesiones migra a una sesión `Main`.
- Se pueden crear, renombrar, seleccionar y archivar sesiones.
- Cada sesión mantiene sus chats y pipelines aislados.
- Claude Code recibe `--name`.
- La UI no ofrece inputs libres para modelos/runtime settings.
- `GEMINI_API_KEY` se reporta distinto en host y en contenedor.
- El dashboard no muestra chat/composer en `Overview`, `Configs`, `Pipelines` ni `Extensions`.
- `Pipelines` usa grafo en desktop y fallback razonable en layouts estrechos.
- `pnpm run test:py` pasa.
- `pnpm run build:control-center` pasa.
- El dashboard responde vía `main.mjs --dashboard-only`.

## Comandos útiles

- `corepack pnpm@10.24.0 install`
- `uv sync --extra test`
- `pnpm run test:py`
- `pnpm run build:control-center`
- `pnpm start`
- `uv run python -m local_control_center --help`

## Pendientes razonables

- Reducir el tiempo de arranque del core.
- Añadir edición más fina de capability routes desde UI.
- Integrar handoffs más profundos con `n8n`.
- Resolver `DEP0190` sin introducir regresiones en Windows.
