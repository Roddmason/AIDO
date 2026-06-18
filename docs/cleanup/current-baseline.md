# Línea base reproducible — AIDO / Local Control Center

> **Propósito:** baseline verificable **antes** de borrar o refactorizar. Captura toolchain, resultado real de cada gate (exit code leído del runner) e inventario del repo en HEAD.
> **Rama:** `dev` · **HEAD:** `c1b117e18747791e8e921978cd6c1bf7ca0ab1a5` · **Árbol:** limpio (`git status` → *nothing to commit*).
> **Fecha:** 2026-06-18 · **Sistema:** Windows 11 Pro 10.0.26200.
> **No se modificó código productivo** (solo se crearon documentos bajo `docs/cleanup/`).
> **Reconciliación del audit contra este HEAD:** ver [audit-reconciliation-HEAD.md](audit-reconciliation-HEAD.md).

---

## 1. Toolchain verificado

| Herramienta | Versión observada | Fuente |
| --- | --- | --- |
| Node.js | `v24.16.0` | `node --version` (cumple `engines.node >=24.16.0 <25.0.0`) |
| pnpm | `10.24.0` | vía `corepack pnpm@10.24.0` (corepack `0.35.0`); `packageManager` declarado = `pnpm@10.24.0` |
| uv | `0.11.7` | `uv --version` |
| Python | `3.13.13` | `uv run python --version` (resuelve por encima de `requires-python >=3.11`) |
| lockfiles | `pnpm-lock.yaml` v`9.0` · `uv.lock` (98 paquetes) | — |

> **Aviso de reproducibilidad:** la versión de Python activa (3.13.13) supera el mínimo declarado (`>=3.11`). El repo **no** fija `.python-version`; para reproducibilidad estricta convendría pinear. Hecho observado, no recomendación de cambio en este baseline.

---

## 2. Resultado de los gates (exit codes reales)

Cada comando se ejecutó capturando salida y `EXITCODE` real (no la notificación del harness). Logs crudos en `.tmp/baseline/*.log` (gitignored).

| # | Comando | Exit | Resultado |
| --- | --- | :---: | --- |
| 1 | `git status` | 0 | Árbol limpio; `dev` al día con `origin/dev`. |
| 2 | `corepack pnpm@10.24.0 install --frozen-lockfile` | **0** | *Lockfile is up to date* · *Already up to date* · 952 ms. |
| 3 | `uv sync --extra dev --extra test` | **0** | *Resolved 98 packages* · *Checked 87 packages in 347 ms*. |
| 4 | `pnpm run typecheck:web` | **0** | `tsc --noEmit` sin diagnósticos. |
| 5 | `pnpm run build:control-center` | **0** | `vite v6.4.2`, 1995 módulos, *built in 7.50s*. |
| 6 | `pnpm run test:py` | **0** | **476 passed** in 457.99s (≈7 min 38 s). |
| 7 | `pnpm run test:web` | **0** | **142 passed** (71 desktop + 71 mobile), 36 chunks. |

**Estado de la línea base: VERDE** — los 7 comandos retornan exit 0.

### Detalle por gate

- **`install` (2):** advertencias no bloqueantes: *Ignored build scripts: esbuild* (requiere `pnpm approve-builds` si se quisiera; no afecta build/tests) y aviso de actualización pnpm `10.24.0 → 11.8.0` (no aplicado: el repo pinea 10.24.0).
- **`build:control-center` (5):** artefactos en `dist/web/`:
  | Asset | Tamaño | gzip |
  | --- | ---: | ---: |
  | `index.html` | 0.85 kB | 0.51 kB |
  | `assets/index-*.css` | 75.62 kB | 11.58 kB |
  | `assets/index-*.js` | 745.84 kB | 205.91 kB |
  Warning de Vite: *Some chunks are larger than 500 kB* (bundle único sin code-splitting). No bloquea; es deuda de performance conocida (no hay `manualChunks`/`import()` dinámico).
- **`test:py` (6):** `uv run pytest tests_py -q` → 476 passed. Incluye los gates de arquitectura (`test_vertical_slices_architecture`, `test_execution_boundary_architecture`, `test_web_rework_architecture`, hygiene/headers) y los tripwires citados por el audit.
- **`test:web` (7):** runner custom `scripts/run-web-tests.mjs` (build interno *built in 5.48s* + dashboard Python por chunk + Playwright `desktop`/`mobile`). `process.exit(status)` = 0; 142/142 tests verdes.
  - **Artefacto benigno observado (no es fallo):** un `ConnectionResetError [WinError 10054]` desde `asyncio … _ProactorBasePipeTransport._call_connection_lost` durante el teardown de una conexión Playwright en el server uvicorn (log línea ~2473-2482). El test continuó y el run cerró con exit 0; es ruido conocido de cierre de socket en Windows, sin impacto en el veredicto.

> **Nota sobre el gate maestro:** el audit menciona `pnpm run quality` como `typecheck:web + build:web + test:py + test:web`. En HEAD, el script `quality` ejecuta el orquestador `scripts/quality-local.ps1` (no la cadena literal). Esta baseline corrió los comandos individuales pedidos, que cubren los gates de verificación frontend (`test:py` por sus gates i18n/arquitectura + `test:web`).

---

## 3. Inventario del repositorio (HEAD `c1b117e`)

Cifras obtenidas con comandos reales (`git ls-files`, `rg`, lectura de `package.json`/`pyproject.toml`/`api.py`/`App.tsx`). El repo tiene **dos** árboles top-level homónimos: `local-control-center/` (con guion) = **frontend** Node/Vite/TS; `local_control_center/` (con guion bajo) = **backend** Python.

### 3.1 Archivos

**386 archivos trackeados** (`git ls-files | wc -l`). Excluye `node_modules/`, `.venv/`, `dist/`, `.tmp/`.

| Área top-level | Archivos |
| --- | ---: |
| `local_control_center/` (backend Python) | 151 |
| `local-control-center/` (frontend: `web/` 95 + `scripts/` 17) | 112 |
| `tests_py/` | 48 |
| `docs/` | 41 |
| Config raíz (`package.json`, `pyproject.toml`, `pytest.ini`, lockfiles, `.gitleaks.toml`, `.semgrep.yml`, etc.) | 19 |
| `scripts/` (raíz) | 7 |
| `.github/` | 4 |
| `tests_web/` | 2 |
| `config/` · `.githooks/` | 1 · 1 |
| **Total** | **386** |

**Frontend `local-control-center/web/src` (91 archivos):** `features/` 54, `app/` 15, `lib/` 5, `design-system/` 5, `api/` 3, `i18n/` 2, `hooks/` 2, `components/` 2, `motion/` 1, sueltos 2.
**`features/*` (54):** model-gateway 14, workbench 13, review 8, home 7, workspace 3, runtime-setup 3, workflows 1, settings 1, memory 1, agents 1, active-projects 1, `pages.tsx` 1.
**Backend `local_control_center/` (151) por slice:** agents 52 (raíz 36 + providers 9 + cli_runtimes 7), shared 10, security_policy 9, evidence 8, projects 7, workspaces_projects 6, workflows 6, memory_retrieval 6, jobs_approvals 6, i18n 6, integrations 5, governance 5, sessions_chats 4, prompts 4, pipelines 4, control_plane 4, runtime_integrations 1, sueltos 8.

### 3.2 Exports e imports (frontend TS, `web/src`)

Patrones anclados a inicio de línea. El cliente generado `api/generated/openapi.ts` domina los tipos.

| Tipo de export | Total | Solo `openapi.ts` | Sin generado |
| --- | ---: | ---: | ---: |
| `export function` | 230 | 3 | 227 |
| `export const` | 21 | 4 | 17 |
| `export type` | 417 | 286 | 131 |
| `export interface` | 5 | 0 | 5 |
| `export default` | **0** | 0 | 0 |
| `export class` | **0** | 0 | 0 |
| `export {` (barrel) | **1** | 0 | 1 |

- **0** `export default` y **0** `export class` en todo `src`. El patrón **no usa barrels**: el único `index.ts` es `features/memory/index.ts` con `export {};` vacío (candidato a borrado — `FE-FEAT-B-001`).
- **Top archivos por exports:** `api/generated/openapi.ts` (293), `api/client.ts` (118), `api/types.ts` (47), `features/review/model.ts` (28), `app/navigation.ts` (14), `features/workbench/workbenchSelectors.ts` (13), `features/home/homeModel.ts` (12).

**Imports externos — solo 4 paquetes npm** (todos los imports internos son relativos; no hay alias `@/`):

| Módulo | #archivos | Nota |
| --- | ---: | --- |
| `react` | 41 | Núcleo. |
| `lucide-react` | 27 | Iconos. |
| `@xyflow/react` | 2 | `WorkflowsPage.tsx` + CSS side-effect en `main.tsx`. |
| `react-dom` | 2 | `primitives.tsx` (createPortal) + `main.tsx` (`react-dom/client`). |
| `@radix-ui/*` | **0** | **Cero ocurrencias en `src`** (clave para `SCRIPTS-DEPS-001`). |
| `zustand` | **0** | Cero ocurrencias en `src`. |

### 3.3 Dependencias npm (`package.json`)

`packageManager = pnpm@10.24.0` · `engines.node = >=24.16.0 <25.0.0` · `type = module`.

| Paquete | Tipo | Rango | #imports en `src` | Nota |
| --- | --- | --- | ---: | --- |
| `@radix-ui/react-dialog` | dep | `^1.1.15` | **0** | Huérfano (`SCRIPTS-DEPS-001`); Dialog hand-rolled en `primitives.tsx`. |
| `@radix-ui/react-dropdown-menu` | dep | `^2.1.16` | **0** | Huérfano. |
| `@radix-ui/react-tooltip` | dep | `^1.2.8` | **0** | Huérfano. |
| `@xyflow/react` | dep | `^12.10.2` | 2 | — |
| `lucide-react` | dep | `^1.16.0` | 27 | — |
| `react` | dep | `^18.3.1` | 41 | — |
| `react-dom` | dep | `^18.3.1` | 2 | — |
| `@playwright/test` | dev | `^1.60.0` | n/a | Tests web. |
| `@types/node` | dev | `^25.5.0` | n/a | Build-time. |
| `@types/react` | dev | `^18.3.24` | n/a | Build-time. |
| `@types/react-dom` | dev | `^18.3.7` | n/a | Build-time. |
| `@vitejs/plugin-react` | dev | `^4.7.0` | n/a | Vite. |
| `typescript` | dev | `^6.0.2` | n/a | `tsc`. |
| `vite` | dev | `^6.4.2` | n/a | Bundler. |

**3 de 7 dependencies de runtime tienen 0 imports** (las `@radix-ui/*`).

### 3.4 Paquetes Python (`pyproject.toml` / `uv.lock`)

`name = local-control-center` · `version = 0.1.0` · `requires-python = >=3.11` · sin `[build-system]` (los módulos se resuelven vía `pythonpath = ["."]` en pytest). `uv.lock`: **98 paquetes** resueltos.

| Grupo | Paquetes (rango) |
| --- | --- |
| runtime | `fastapi >=0.136.0`, `uvicorn >=0.37.0`, `numpy >=1.26.0` |
| `agents` | `openai-agents >=0.4.0` |
| `faiss` | `faiss-cpu >=1.13.0` |
| `otel` | `opentelemetry-sdk >=1.30.0`, `opentelemetry-exporter-otlp-proto-http >=1.30.0` |
| `dev` | `ruff >=0.14.9`, `pre-commit >=4.5.0`, `pip-licenses >=5.5.0`, `semgrep >=1.145.0` |
| `test` | `pytest >=9.0.0`, `httpx >=0.28.0` |

**16 módulos de dominio** (con `__init__.py`) bajo `local_control_center/`: `agents`, `control_plane`, `evidence`, `governance`, `i18n`, `integrations`, `jobs_approvals`, `memory_retrieval`, `pipelines`, `projects`, `prompts`, `runtime_integrations`, `security_policy`, `sessions_chats`, `shared`, `workspaces_projects` (+ anidados `agents.cli_runtimes`, `agents.providers`).

### 3.5 Scripts

**`package.json` — 38 scripts** por categoría:

| Categoría | Scripts |
| --- | --- |
| Build (4) | `prestart`, `build`, `build:web`, `build:control-center` |
| Start (3) | `start`, `start:py`, `start:windows` |
| OpenAPI (1) | `openapi:generate` |
| Test (5) | `test`, `test:py`, `test:all`, `test:web`, `typecheck:web` |
| Quality (5) | `lint`, `lint:py`, `quality:productive-truth`, `quality:architecture`, `quality` (→ `scripts/quality-local.ps1`) |
| Security (5) | `security:licenses:py`, `security:licenses:js`, `security:secrets` (gitleaks), `security:sast` (semgrep), `security:credentials:preflight` |
| Smoke (7) | `smoke:runtime:preflight`, `smoke:runtime:release:preflight`, `smoke:runtime:release`, `smoke:codex:release`, `smoke:claude:release`, `smoke:openhands:release`, `smoke:swe-agent:release` |
| Codegraph (5) | `codegraph`, `:init`, `:index`, `:status`, `:files` |
| Otros (2) | `release:certify`, `node:use` |

**`scripts/` (raíz, 7):** `productive-truth-scan.py` (anti-mock), `quality-local.ps1` (orquestador de calidad), `release-certify.ps1`, `run-web-tests.mjs`, `cleanup-playwright-webserver.mjs`, `install-local.sh`, `install-local.ps1`.
**`local-control-center/scripts/` (17):** `start_control_center.py`, `start-control-center.ps1`, `generate_openapi_client.py`, `check-credentials.py`, `release_validate_{codex,claude_code}_cli.py`, `release_validate_optional_cli_runtime.py`, `smoke-runtime-adapters.ps1`, `smoke-otel-exporter.ps1`, `use-node.ps1`, `register-autostart.ps1`, `unregister-autostart.ps1`, `register-service-winsw.example.ps1`, `register-local-dns.ps1`, `unregister-local-dns.ps1`, `protect-repository-branches.ps1`, `protect-main-branch.ps1` (deprecated, removal 2026-09-01).

### 3.6 Rutas FastAPI

App ensamblada en `local_control_center/api.py::create_app` (`app.py` solo re-exporta). **15 routers** vía `include_router` + endpoints app-level. **140 endpoints** totales (133 en routers + 7 app-level, 2 de ellos condicionales a `static_dir`).

| Router | Prefijo | Endpoints |
| --- | --- | ---: |
| model-gateway (`agents/model_gateway_api.py`) | `/api/v1/model-gateway` | 36 |
| agents (`agents/api.py`) | — | 17 |
| workflows (`workflows/api.py`) | — | 16 |
| evidence (`evidence/api.py`) | — | 9 |
| governance (`governance/api.py`) | — | 9 |
| jobs_approvals (`jobs_approvals/api.py`) | — | 8 |
| projects (`projects/api.py`) | — | 8 |
| memory_retrieval (`memory_retrieval/api.py`) | — | 6 |
| security_policy (`security_policy/api.py`) | — | 6 |
| integrations (`integrations/api.py`) | — | 5 |
| sessions_chats (`sessions_chats/api.py`) | — | 4 |
| workspaces_projects (`workspaces_projects/api.py`) | — | 3 |
| pipelines (`pipelines/api.py`) | — | 2 |
| prompts (`prompts/api.py`) | — | 2 |
| i18n (`i18n/api.py`) | — | 2 |
| **app-level** (`api.py`) | — | 7 (`/healthz`, `/api/v1/security/handshake`, `/api/v1/overview`, `/api/v1/telemetry/status`, `/api/v1/events`, `+2` estáticos condicionales) |

> Catorce routers declaran paths absolutos (sin `prefix=`); solo `model_gateway` usa `APIRouter(prefix=…)`. Observación: `GET /api/v1/agents` existe tanto en `projects` (catálogo) como bajo el sub-namespace de `agents` (`/api/v1/agents/{devops,security,…}`) — sin solape exacto de path. La ruta vacía-de-datos `GET /api/v1/integrations` (sin INSERT en el repo) es el hallazgo `BE-SLICES-B-003`.

### 3.7 Componentes montados (frontend)

No usa react-router: shell IDE con enrutamiento por hash. `main.tsx` monta `<React.StrictMode><I18nProvider><App/></I18nProvider></…>` (único provider de contexto; tema aplicado imperativo vía `applyStoredTheme`). `App.tsx::pageContent()` mapea **24 rutas (`PageId`) → 15 componentes de página** (8 `*Page.tsx` + 7 funciones en `features/pages.tsx`).

| Mecanismo | Detalle |
| --- | --- |
| Páginas con archivo propio (8) | `HomePage`, `WorkbenchPage`, `ActiveProjectsPage` (4 rutas), `WorkflowsPage`, `ReviewPage`, `AgentsPage`, `ModelGatewayPage`, `SettingsPage` (7 rutas) |
| Páginas en `features/pages.tsx` (7) | `WorkspacesPage`, `PolicySecurityPage`, `MemoryPage`, `EvidencePage`, `GovernancePage`, `AuditPage`, `IntegrationsPage` |
| Chrome (AppShell) | `ActivityBar` (siempre), `ExplorerPanel` (si no colapsado), `WorkbenchHeader` (siempre), `InspectorPanel` (si abierto), `StatusBar` (siempre) |
| Overlays (App) | `NewWorkspaceDialog`, `ApprovalsDrawer`, `EventsDrawer`, `CommandPalette` (por flag de estado) |

**Componentes de página definidos pero NO montados: ninguno.** Verificado: los 15 nombres aparecen en `App.tsx` (import + uso). Consistente con el commit `af0950e` que purgó símbolos huérfanos.

---

## 4. Cómo reproducir esta baseline

```bash
git checkout dev && git rev-parse HEAD          # c1b117e, árbol limpio
corepack pnpm@10.24.0 install --frozen-lockfile # exit 0
uv sync --extra dev --extra test                # exit 0
pnpm run typecheck:web                           # exit 0
pnpm run build:control-center                    # exit 0
pnpm run test:py                                 # 476 passed
pnpm run test:web                                # 142 passed (71 desktop + 71 mobile)
```

> Requisitos: Node `24.16.x`, `uv` ≥ 0.11, corepack habilitado, Playwright instalado (lo cubre `scripts/install-local.*`). `test:web` levanta el dashboard Python en un puerto efímero por chunk; no requiere servidor previo.

---

## 5. Aceptación

- **Baseline verificable:** ✅ 7/7 gates en exit 0, con cifras y logs crudos en `.tmp/baseline/`.
- **Auditoría reflejada contra HEAD:** ✅ los 41 hallazgos reconciliados en [audit-reconciliation-HEAD.md](audit-reconciliation-HEAD.md) (8 resolved · 1 changed · 31 still_open · 1 invalid), no contra el commit base `d7d02ab`.
- **Sin cambios de código productivo:** ✅ solo documentación nueva bajo `docs/cleanup/`.
