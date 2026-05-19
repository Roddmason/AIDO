# AI Frontend Upgrade Report

## Resumen ejecutivo

- El control plane ahora soporta múltiples workspaces persistentes y múltiples sesiones nombradas por workspace.
- La sesión local pasó a ser entidad real:
  - persiste,
  - aísla chats/pipelines/prompt packs,
  - y Claude Code recibe ese nombre por `--name`.
- La configuración runtime dejó de ser texto libre:
  - Claude Code, Codex, Gemini y Ollama exponen sólo opciones válidas del producto.
- La semántica de secretos ya no finge contexto:
  - host Windows y contenedor muestran estados distintos y razonados.
- El dashboard dejó de mezclar chat en todas las pantallas:
  - composer sólo en `Chats` y `Agents`.
- El pipeline board ahora soporta operaciones por stage:
  - reassign,
  - retry por etapa,
  - override manual de gate,
  - y apertura directa del transcript desde el board.
- La operación diaria quedó Windows-native:
  - `pnpm start` levanta el dashboard local con FastAPI,
  - escucha sólo en `127.0.0.1:4310`,
  - y el repo ya no depende de contenedores para operar.

## Archivos principales modificados

- `local_control_center/api.py`
- `local_control_center/store.py`
- `local_control_center/worker.py`
- `local_control_center/retrieval.py`
- `Legacy/Node.js/local-control-center/lib/*`
- `Legacy/Node.js/local-control-center/main.mjs`
- `Legacy/Node.js/local-control-center/app.mjs`
- `local-control-center/build.mjs`
- `local-control-center/web/index.html`
- `local-control-center/web/dashboard-client.jsx`
- `local-control-center/web/dashboard.css`
- `tests_py/test_python_control_center.py`
- `docs/ia-agent-local.md`
- `package.json`
- `pnpm-lock.yaml`
- `uv.lock`
- `docs/ai-frontend-upgrade-plan.md`
- `docs/ai-frontend-upgrade-report.md`

## Cambios UX/UI

- Se eliminó el bloque textual lateral redundante:
  - sin marketing copy,
  - sin `Dashboard:`,
  - sin `Workspace:` en sidebar.
- Nueva toolbar superior con:
  - selector de workspace,
  - apertura de carpeta/proyecto,
  - selector y CRUD de sesión,
  - rama actual y posture git,
  - estado resumido de runtimes.
- `Chats` y `Agents` son los únicos lugares con superficie conversacional.
- `Pipelines` ahora muestra:
  - selector de pipeline por sesión,
  - grafo operacional,
  - correction branches visibles,
  - fallback a tarjetas cuando el layout se estrecha.
- `Configs` separa:
  - defaults de workspace,
  - overrides de sesión,
  - secretos como salud operativa, no como pseudo-form.
- `Agents` quedó con controles estrictos por provider y actividad reciente del equipo.

## Cambios técnicos

### 1. Multi-workspace

- `global.json` migró a:
  - `activeWorkspaceId`
  - `workspaces[]`
- Cada workspace guarda:
  - `id`
  - `path`
  - `label`
  - `lastOpenedAt`
  - `activeSessionId`

### 2. Sesiones persistentes

- `team-workspace.json` ahora soporta:
  - `sessions[]`
  - `activeSessionId`
- `chat`, `pipeline` y `promptPack` reciben `sessionId`.
- Estados legacy sin sesiones migran automáticamente a `Main`.

### 3. Configuración estricta por runtime

- Claude Code:
  - `model`
  - `effort`
  - `permissionMode`
- Codex:
  - `model`
  - `sandbox`
  - `profile`
- Gemini:
  - `researchModel`
  - `mediaModel`
- Ollama:
  - modelos detectados localmente

Nota crítica:
- El plan original incluía flags tipo `approvalPolicy/search` para Codex.
- No los mantuve porque `codex exec --help` en esta máquina no los expone como flags directos.
- Preferí alinearme con el CLI real y no inventar una UI falsa.

### 4. Entorno y secretos

- Host Windows:
  - `process.env`
  - fallback a registro
- Estados:
  - `present`
  - `missing`
  - `host-only`

### 5. Core y server

- CRUD de sesiones en core y API:
  - `GET /api/sessions`
  - `POST /api/sessions`
  - `POST /api/sessions/select`
  - `PATCH /api/sessions/:id`
  - `DELETE /api/sessions/:id`
- Opciones runtime por API:
  - `GET /api/config/options`
- Routing conversacional desde web:
  - `POST /api/chats/send`

## Tests ejecutados

- `pnpm run test:py`
  - OK
  - 8 tests pasando
- `pnpm run build:control-center`
  - OK
- `pnpm start`
  - `GET /healthz` -> OK
  - `GET /api/state` -> `200`

## Resultado de build/lint/test

- `test`
  - OK
  - 37 tests pasando
- `build:control-center`
  - OK
- `pnpm start`
  - OK
  - `/healthz` y `/api/state` respondiendo en `127.0.0.1:4310`
- `lint`
  - no existe script

## Riesgos restantes

- El arranque del dashboard sigue siendo lento porque el core hace discovery/probes locales antes de levantar el servidor.
- El bundle web quedó en ~2 MB; aceptable para dashboard local, no ideal.
- El repo completo sigue sin `lint` ni build global confiable.

## Recomendaciones siguientes

1. Cachear probes no críticos para reducir el tiempo de `core.init()`.
2. Añadir edición más fina de capability routes desde UI.
3. Extender la UI de `n8n` y automatizaciones.
4. Reducir el tiempo de inicialización y el peso del bundle del dashboard.

## Qué no se hizo y por qué

- No dejé Gemini configurable como runtime de código.
  - Va contra la policy operativa pedida y sería mala arquitectura.
- No mantuve inputs libres para modelos o flags.
  - La iteración anterior ya era demasiado permisiva; repetir eso habría sido un error.
- No forcé paridad falsa entre Claude y Codex.
  - Cada runtime expone sólo lo que su CLI local soporta de forma verificable.
- No dejé múltiples entrypoints npm para el dashboard.
  - Operativamente era una mala decisión porque fomentaba procesos huérfanos y puertos paralelos.
