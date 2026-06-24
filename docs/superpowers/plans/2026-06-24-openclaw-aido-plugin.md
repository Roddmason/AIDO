# Plugin OpenClaw `aido-control` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un plugin nativo de OpenClaw (`aido-control`) que, desde el OpenClaw del usuario, exponga tools para ver el estado de los proyectos de AIDO, listarlos, listar plantillas y crear un proyecto nuevo, hablando con la API local de AIDO.

**Architecture:** Paquete TypeScript ESM independiente en `integrations/openclaw-aido-plugin/`, generado con `openclaw plugins init` y construido con `defineToolPlugin`. Un cliente HTTP fino (`aidoClient.ts`) encapsula la API de AIDO (lecturas sin auth; escritura con token de handshake de loopback). El entry (`index.ts`) declara 4 tools que delegan en el cliente. El manifest `openclaw.plugin.json` se **genera** desde el código vía `openclaw plugins build`.

**Tech Stack:** OpenClaw 2026.6.9 (plugin SDK), TypeScript 5.9 (NodeNext, strict), typebox ^1.1.38, vitest ^3.2.0, `fetch` global de Node. AIDO = FastAPI en `http://127.0.0.1:4310`.

## Global Constraints

- OpenClaw instalado en el equipo: **2026.6.9**; `package.json.peerDependencies.openclaw` = `>=2026.5.17` (valor del scaffold; no bajar).
- Paquete **ESM** (`"type": "module"`), TS **NodeNext + strict**; el entry compilado es `./dist/index.js` (campo `openclaw.extensions`).
- El **manifest se genera** con `npm run plugin:build` (`openclaw plugins build --entry ./dist/index.js`). **No** editar a mano `contracts.tools` ni `toolMetadata`.
- AIDO base URL por defecto: `http://127.0.0.1:4310`. Escritura requiere `GET /api/v1/security/handshake` → header `X-Local-Control-Token: <token>` **y** `Origin: http://127.0.0.1`. Lecturas no requieren token.
- El token **nunca** se persiste ni se loguea.
- Tools: `aido_overview`, `aido_list_projects`, `aido_list_templates` (lectura); `aido_create_project` (escritura, `optional: true`).
- Ubicación: `integrations/openclaw-aido-plugin/` (fuera de `local_control_center/` Python y de `local-control-center/web/`). El paquete gestiona sus deps con **npm** y se mantiene fuera de cualquier workspace pnpm de AIDO.
- Commits formato AIDO: `Tipo (Ámbito): mensaje`. Un commit por task; `git add` solo de archivos del plugin. Push a `dev` al final (norma autónoma de AIDO).
- Verificación de gates AIDO al final con código de salida real (no la notificación): `corepack pnpm@10.24.0 run test:py`, `quality:productive-truth`, `quality:architecture` deben seguir en exit 0.
- Convención de cwd: los comandos del plugin usan `cd` **absoluto** a `H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin` para ser idempotentes entre steps (evita el doble-cd); los comandos `openclaw …` se lanzan con rutas absolutas o desde la raíz del repo.

---

### Task 1: Scaffold del plugin (baseline verde)

Genera el proyecto base con el scaffolder oficial, lo aísla del control de versiones (node_modules/dist) y deja el test del scaffold (`echo`) en verde.

**Files:**
- Create (vía CLI): `integrations/openclaw-aido-plugin/package.json`
- Create (vía CLI): `integrations/openclaw-aido-plugin/openclaw.plugin.json`
- Create (vía CLI): `integrations/openclaw-aido-plugin/tsconfig.json`
- Create (vía CLI): `integrations/openclaw-aido-plugin/src/index.ts`
- Create (vía CLI): `integrations/openclaw-aido-plugin/src/index.test.ts`
- Create (vía CLI): `integrations/openclaw-aido-plugin/README.md`
- Create: `integrations/openclaw-aido-plugin/.gitignore`

**Interfaces:**
- Produces: el paquete base con `defineToolPlugin` (id `aido-control`) y los scripts `build`, `plugin:build`, `plugin:validate`, `test` que las tasks siguientes reutilizan.

- [ ] **Step 1: Generar el scaffold**

Run (desde la raíz del repo `H:\Proyectos\Personales\AIDO`):

```bash
openclaw plugins init aido-control --directory integrations/openclaw-aido-plugin --name "AIDO Control"
```

Expected: `Created .../integrations/openclaw-aido-plugin` y los archivos listados arriba. (Puede imprimir "Config warnings" del openclaw.json global del usuario; son inofensivos para el scaffold.)

- [ ] **Step 2: Añadir `.gitignore` del paquete**

Create `integrations/openclaw-aido-plugin/.gitignore`:

```gitignore
node_modules/
dist/
```

- [ ] **Step 3: Instalar dependencias**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npm install
```

Expected: instala `typebox`, `typescript`, `vitest` y `openclaw` (devDep) sin errores.

- [ ] **Step 4: Correr el test del scaffold (debe pasar)**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npm test
```

Expected: PASS — 1 test (`declares tool metadata` → `["echo"]`).

- [ ] **Step 5: Build (verifica toolchain TS)**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npm run build
```

Expected: genera `dist/index.js` sin errores de tipos.

- [ ] **Step 6: Commit**

```bash
git add integrations/openclaw-aido-plugin/package.json \
        integrations/openclaw-aido-plugin/openclaw.plugin.json \
        integrations/openclaw-aido-plugin/tsconfig.json \
        integrations/openclaw-aido-plugin/src/index.ts \
        integrations/openclaw-aido-plugin/src/index.test.ts \
        integrations/openclaw-aido-plugin/README.md \
        integrations/openclaw-aido-plugin/.gitignore
git commit -m "Feature (OpenClaw): scaffold del plugin aido-control (tool plugin base verde)"
```

---

### Task 2: Cliente HTTP de AIDO (`aidoClient.ts`)

Encapsula la API de AIDO: lecturas (`overview`/`projects`/`templates`) y escritura (`createProject`) con handshake del token, headers correctos, resumen curado del overview y errores legibles. TDD con `fetch` inyectado.

**Files:**
- Create: `integrations/openclaw-aido-plugin/src/types.ts`
- Create: `integrations/openclaw-aido-plugin/src/aidoClient.ts`
- Test: `integrations/openclaw-aido-plugin/src/aidoClient.test.ts`

**Interfaces:**
- Consumes: nada de tasks previas.
- Produces:
  - `class AidoClient` con constructor `new AidoClient({ baseUrl: string, fetchImpl?: typeof fetch })` y métodos:
    - `listProjects(signal?: AbortSignal): Promise<ProjectRecord[]>`
    - `listTemplates(signal?: AbortSignal): Promise<ProjectTemplateRecord[]>`
    - `getOverview(signal?: AbortSignal): Promise<OverviewSummary>`
    - `createProject(input: CreateProjectInput, signal?: AbortSignal): Promise<ProjectRecord>`
  - `function summarizeOverview(raw: unknown): OverviewSummary`
  - `class AidoError extends Error`
  - Tipos en `types.ts`: `ProjectRecord`, `ProjectTemplateRecord`, `SecurityPosture`, `CreateProjectInput`, `OverviewSummary`.

- [ ] **Step 1: Escribir los tipos**

Create `integrations/openclaw-aido-plugin/src/types.ts`:

```ts
export interface ProjectRecord {
  id: string;
  name: string;
  path: string;
  templateId: string;
  source: string;
  status: string;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectTemplateRecord {
  id: string;
  name: string;
  kind: string;
}

export interface SecurityPosture {
  loopbackOnly: boolean;
  writeTokenRequired: boolean;
}

export interface CreateProjectInput {
  name?: string;
  path?: string;
  workspaceBasePath?: string;
  projectDirectoryName?: string;
  templateId?: string;
  createDirectory?: boolean;
  metadata?: Record<string, unknown>;
}

export interface OverviewSummary {
  security: SecurityPosture;
  projects: Array<Pick<ProjectRecord, "id" | "name" | "path" | "status" | "templateId">>;
  counts: Record<string, number>;
  nextSteps: unknown[];
  riskRegister: unknown[];
}
```

- [ ] **Step 2: Escribir el test (debe fallar)**

Create `integrations/openclaw-aido-plugin/src/aidoClient.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { AidoClient, AidoError, summarizeOverview } from "./aidoClient.js";

const BASE = "http://127.0.0.1:4310";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("AidoClient reads", () => {
  it("listProjects calls GET /api/v1/projects and returns the projects array", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ projects: [{ id: "p1", name: "Demo" }] }),
    );
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const projects = await client.listProjects();
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/projects`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(projects).toEqual([{ id: "p1", name: "Demo" }]);
  });

  it("listTemplates calls GET /api/v1/project-templates and returns projectTemplates", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ projectTemplates: [{ id: "frontend", name: "Frontend", kind: "frontend" }] }),
    );
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const templates = await client.listTemplates();
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/project-templates`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(templates).toEqual([{ id: "frontend", name: "Frontend", kind: "frontend" }]);
  });
});

describe("summarizeOverview", () => {
  it("returns a curated summary with counts and a project subset, not the raw blob", () => {
    const summary = summarizeOverview({
      security: { loopbackOnly: true, writeTokenRequired: true },
      projects: [{ id: "p1", name: "Demo", path: "/x", status: "active", templateId: "t", extra: 1 }],
      workflows: [{}, {}],
      jobs: [{}],
      nextSteps: [{ id: "n1" }],
      riskRegister: [],
    });
    expect(summary.security).toEqual({ loopbackOnly: true, writeTokenRequired: true });
    expect(summary.projects).toEqual([
      { id: "p1", name: "Demo", path: "/x", status: "active", templateId: "t" },
    ]);
    expect(summary.counts.projects).toBe(1);
    expect(summary.counts.workflows).toBe(2);
    expect(summary.counts.jobs).toBe(1);
    expect(summary.counts.nextSteps).toBe(1);
    expect(summary.nextSteps).toEqual([{ id: "n1" }]);
  });
});

describe("AidoClient.createProject", () => {
  it("fetches the handshake token then POSTs with token + Origin headers and camelCase body", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") {
        return jsonResponse({ project: { id: "p9", name: "New", path: "/p", status: "active" } }, 201);
      }
      throw new Error(`unexpected ${init.method} ${url}`);
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const project = await client.createProject({ name: "New", workspaceBasePath: "/ws" });

    const post = fetchImpl.mock.calls.find(([, init]) => (init as RequestInit).method === "POST");
    const headers = (post![1] as RequestInit).headers as Record<string, string>;
    expect(headers["x-local-control-token"]).toBe("TKN");
    expect(headers.origin).toBe("http://127.0.0.1");
    expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual({
      createDirectory: true,
      name: "New",
      workspaceBasePath: "/ws",
    });
    expect(project.id).toBe("p9");
  });

  it("on a 403 refreshes the token once and retries", async () => {
    let posts = 0;
    const tokens = ["STALE", "FRESH"];
    let handshakes = 0;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: tokens[handshakes++] });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") {
        posts += 1;
        return posts === 1
          ? jsonResponse({ detail: "forbidden" }, 403)
          : jsonResponse({ project: { id: "p9" } }, 201);
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const project = await client.createProject({ path: "/p" });
    expect(posts).toBe(2);
    expect(handshakes).toBe(2);
    expect(project.id).toBe("p9");
  });
});

describe("AidoClient errors", () => {
  it("wraps connection failures in a readable AidoError", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    await expect(client.listProjects()).rejects.toBeInstanceOf(AidoError);
    await expect(client.listProjects()).rejects.toThrow(/AIDO no responde/);
  });
});
```

- [ ] **Step 3: Correr el test para verificar que falla**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npx vitest run src/aidoClient.test.ts
```

Expected: FAIL — `Cannot find module './aidoClient.js'` (aún no existe).

- [ ] **Step 4: Implementar el cliente**

Create `integrations/openclaw-aido-plugin/src/aidoClient.ts`:

```ts
import type {
  CreateProjectInput,
  OverviewSummary,
  ProjectRecord,
  ProjectTemplateRecord,
} from "./types.js";

const WRITE_ORIGIN = "http://127.0.0.1";

export interface AidoClientOptions {
  baseUrl: string;
  fetchImpl?: typeof fetch;
}

export class AidoError extends Error {}

export class AidoClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private cachedToken: string | null = null;

  constructor(options: AidoClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  async listProjects(signal?: AbortSignal): Promise<ProjectRecord[]> {
    const data = await this.getJson<{ projects: ProjectRecord[] }>("/api/v1/projects", signal);
    return data.projects ?? [];
  }

  async listTemplates(signal?: AbortSignal): Promise<ProjectTemplateRecord[]> {
    const data = await this.getJson<{ projectTemplates: ProjectTemplateRecord[] }>(
      "/api/v1/project-templates",
      signal,
    );
    return data.projectTemplates ?? [];
  }

  async getOverview(signal?: AbortSignal): Promise<OverviewSummary> {
    const raw = await this.getJson<unknown>("/api/v1/overview", signal);
    return summarizeOverview(raw);
  }

  async createProject(input: CreateProjectInput, signal?: AbortSignal): Promise<ProjectRecord> {
    const body = JSON.stringify({ createDirectory: true, ...input });
    let response = await this.postProjects(body, await this.token(signal), signal);
    if (response.status === 403) {
      this.cachedToken = null;
      response = await this.postProjects(body, await this.token(signal), signal);
    }
    if (!response.ok) {
      throw new AidoError(
        `AIDO create_project falló: HTTP ${response.status} ${await safeText(response)}`.trim(),
      );
    }
    const data = (await response.json()) as { project: ProjectRecord };
    return data.project;
  }

  private async token(signal?: AbortSignal): Promise<string> {
    if (this.cachedToken) return this.cachedToken;
    const data = await this.getJson<{ token: string }>("/api/v1/security/handshake", signal);
    this.cachedToken = data.token;
    return data.token;
  }

  private postProjects(body: string, token: string, signal?: AbortSignal): Promise<Response> {
    return this.request("/api/v1/projects", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-local-control-token": token,
        origin: WRITE_ORIGIN,
      },
      body,
      signal,
    });
  }

  private async getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await this.request(path, { method: "GET", signal });
    if (!response.ok) {
      throw new AidoError(`AIDO request falló: HTTP ${response.status} en ${path}`);
    }
    return (await response.json()) as T;
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    try {
      return await this.fetchImpl(`${this.baseUrl}${path}`, init);
    } catch (cause) {
      throw new AidoError(`AIDO no responde en ${this.baseUrl} (${(cause as Error).message})`);
    }
  }
}

export function summarizeOverview(raw: unknown): OverviewSummary {
  const data = (raw ?? {}) as Record<string, unknown>;
  const projects = Array.isArray(data.projects) ? (data.projects as ProjectRecord[]) : [];
  const security = (data.security as OverviewSummary["security"]) ?? {
    loopbackOnly: false,
    writeTokenRequired: false,
  };
  return {
    security,
    projects: projects.map((p) => ({
      id: p.id,
      name: p.name,
      path: p.path,
      status: p.status,
      templateId: p.templateId,
    })),
    counts: {
      projects: projects.length,
      workflows: len(data.workflows),
      workflowRuns: len(data.workflowRuns),
      jobs: len(data.jobs),
      agentRuns: len(data.agentRuns),
      nextSteps: len(data.nextSteps),
      riskRegister: len(data.riskRegister),
    },
    nextSteps: Array.isArray(data.nextSteps) ? (data.nextSteps as unknown[]) : [],
    riskRegister: Array.isArray(data.riskRegister) ? (data.riskRegister as unknown[]) : [],
  };
}

function len(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}
```

- [ ] **Step 5: Correr el test para verificar que pasa**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npx vitest run src/aidoClient.test.ts
```

Expected: PASS — todos los tests del cliente.

- [ ] **Step 6: Commit**

```bash
git add integrations/openclaw-aido-plugin/src/types.ts \
        integrations/openclaw-aido-plugin/src/aidoClient.ts \
        integrations/openclaw-aido-plugin/src/aidoClient.test.ts
git commit -m "Feature (OpenClaw): cliente HTTP de AIDO (overview/projects/templates/create + handshake)"
```

---

### Task 3: Tools del plugin + manifest generado

Reemplaza la tool `echo` por las 4 tools reales que delegan en `AidoClient`, añade `configSchema.baseUrl`, marca `aido_create_project` como `optional`, y regenera/valida el manifest desde el código.

**Files:**
- Modify: `integrations/openclaw-aido-plugin/src/index.ts` (reemplazo completo)
- Modify: `integrations/openclaw-aido-plugin/src/index.test.ts` (reemplazo completo)
- Regenerado (CLI): `integrations/openclaw-aido-plugin/openclaw.plugin.json`

**Interfaces:**
- Consumes: `AidoClient` de Task 2 (constructor `{ baseUrl, fetchImpl? }` y métodos `getOverview`/`listProjects`/`listTemplates`/`createProject`).
- Produces: el `defineToolPlugin` por defecto con las tools `aido_overview`, `aido_list_projects`, `aido_list_templates`, `aido_create_project` y `configSchema` `{ baseUrl?: string }`.

- [ ] **Step 1: Reescribir el test de metadata (debe fallar)**

Replace el contenido de `integrations/openclaw-aido-plugin/src/index.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import entry from "./index.js";

describe("aido-control plugin", () => {
  it("declares the four AIDO tools in order", () => {
    const names = getToolPluginMetadata(entry)?.tools.map((tool) => tool.name);
    expect(names).toEqual([
      "aido_overview",
      "aido_list_projects",
      "aido_list_templates",
      "aido_create_project",
    ]);
  });
});
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npx vitest run src/index.test.ts
```

Expected: FAIL — los nombres aún son `["echo"]`.

- [ ] **Step 3: Reescribir el entry con las 4 tools**

Replace el contenido de `integrations/openclaw-aido-plugin/src/index.ts`:

```ts
import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { AidoClient } from "./aidoClient.js";

const DEFAULT_BASE_URL = "http://127.0.0.1:4310";

interface AidoConfig {
  baseUrl?: string;
}

// `config` llega tipado por el SDK desde configSchema; lo tratamos como unknown
// y leemos baseUrl defensivamente para no acoplarnos al genérico del SDK.
function client(config: unknown): AidoClient {
  const baseUrl = (config as AidoConfig | undefined)?.baseUrl ?? DEFAULT_BASE_URL;
  return new AidoClient({ baseUrl });
}

export default defineToolPlugin({
  id: "aido-control",
  name: "AIDO Control",
  description: "Inspect and control your AIDO Local Control Center projects.",
  configSchema: Type.Object({
    baseUrl: Type.Optional(
      Type.String({
        description: "AIDO base URL (loopback).",
        default: DEFAULT_BASE_URL,
      }),
    ),
  }),
  tools: (tool) => [
    tool({
      name: "aido_overview",
      label: "AIDO Overview",
      description:
        "Get a curated status summary of the AIDO control center: projects, counts, security posture and next steps.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).getOverview(context?.signal),
    }),
    tool({
      name: "aido_list_projects",
      label: "AIDO List Projects",
      description: "List all projects registered in AIDO.",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).listProjects(context?.signal),
    }),
    tool({
      name: "aido_list_templates",
      label: "AIDO List Templates",
      description: "List available AIDO project templates (use the id as templateId when creating).",
      parameters: Type.Object({}),
      execute: async (_params, config, context) =>
        client(config).listTemplates(context?.signal),
    }),
    tool({
      name: "aido_create_project",
      label: "AIDO Create Project",
      description:
        "Create a new project in AIDO. Provide either a path, or a name together with workspaceBasePath.",
      optional: true,
      parameters: Type.Object({
        name: Type.Optional(Type.String()),
        path: Type.Optional(Type.String()),
        workspaceBasePath: Type.Optional(Type.String()),
        projectDirectoryName: Type.Optional(Type.String()),
        templateId: Type.Optional(Type.String()),
        createDirectory: Type.Optional(Type.Boolean({ default: true })),
        metadata: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
      }),
      execute: async (params, config, context) =>
        client(config).createProject(params, context?.signal),
    }),
  ],
});
```

- [ ] **Step 4: Correr el test para verificar que pasa**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npx vitest run
```

Expected: PASS — metadata (4 tools en orden) + todos los tests del cliente.

- [ ] **Step 5: Build + regenerar manifest + validar**

Run:

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npm run build && npm run plugin:build && npm run plugin:validate
```

Expected: `plugin:build` regenera `openclaw.plugin.json` con `contracts.tools` = las 4 tools; `plugin:validate` termina sin errores.

- [ ] **Step 6: Verificar que la tool de escritura quedó `optional` en el manifest generado**

Open `integrations/openclaw-aido-plugin/openclaw.plugin.json` y confirma que `toolMetadata.aido_create_project.optional` es `true` (lo escribe `plugin:build` desde `optional: true` del código). Si no aparece, revisa que `optional: true` esté en la tool antes de re-ejecutar `npm run plugin:build`.

- [ ] **Step 7: Commit**

```bash
git add integrations/openclaw-aido-plugin/src/index.ts \
        integrations/openclaw-aido-plugin/src/index.test.ts \
        integrations/openclaw-aido-plugin/openclaw.plugin.json
git commit -m "Feature (OpenClaw): tools aido_overview/list_projects/list_templates/create_project (create optional)"
```

---

### Task 4: Instalación local, smoke en vivo, docs y verificación de gates

Instala el plugin en el OpenClaw del usuario, confirma el registro, documenta instalación/config/allowlist, hace un smoke opcional contra AIDO en vivo, y verifica que el repo de AIDO sigue verde.

**Files:**
- Modify: `integrations/openclaw-aido-plugin/README.md` (reemplazo completo)
- Create: `integrations/openclaw-aido-plugin/scripts/smoke.mjs`

**Interfaces:**
- Consumes: el paquete construido (`dist/`) de Task 3 y `AidoClient`.
- Produces: documentación de instalación + script de smoke. No expone API nueva a otras tasks.

- [ ] **Step 1: Build e instalar el plugin (link local)**

Run (todo desde la raíz del repo):

```bash
( cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && npm run build )
openclaw plugins install H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin --link
```

(`--link` enlaza el path local en vez de copiarlo, ideal para desarrollo. La ruta absoluta evita depender del cwd.)

Expected: confirma la instalación de `aido-control`. Si ya estaba instalado, añade `--force`.

- [ ] **Step 2: Confirmar el registro y las tools**

Run:

```bash
openclaw plugins inspect aido-control --runtime --json
```

Expected: JSON con las 4 tools (`aido_overview`, `aido_list_projects`, `aido_list_templates`, `aido_create_project`) y `aido_create_project` marcada como opcional. Verificar también con `openclaw plugins doctor` que no hay issues de carga.

- [ ] **Step 3: Escribir el script de smoke en vivo (opcional)**

Create `integrations/openclaw-aido-plugin/scripts/smoke.mjs`:

```js
// Smoke en vivo: requiere AIDO corriendo (npm run start -> http://127.0.0.1:4310).
// Uso: node scripts/smoke.mjs
import { AidoClient } from "../dist/aidoClient.js";

const baseUrl = process.env.AIDO_BASE_URL ?? "http://127.0.0.1:4310";
const client = new AidoClient({ baseUrl });

const overview = await client.getOverview();
console.log("security:", overview.security);
console.log("counts:", overview.counts);

const projects = await client.listProjects();
console.log(`projects (${projects.length}):`, projects.map((p) => `${p.name} [${p.status}]`));

const templates = await client.listTemplates();
console.log(`templates (${templates.length}):`, templates.map((t) => t.id));
```

- [ ] **Step 4: Ejecutar el smoke contra AIDO en vivo (si AIDO está corriendo)**

Run (con AIDO levantado en otra terminal vía `corepack pnpm@10.24.0 run start`):

```bash
cd H:/Proyectos/Personales/AIDO/integrations/openclaw-aido-plugin && node scripts/smoke.mjs
```

Expected: imprime `security`, `counts`, la lista de proyectos y de templates reales de AIDO. Si AIDO no está corriendo, el error debe ser legible (`AIDO no responde en http://127.0.0.1:4310`); en ese caso documenta el bloqueo y continúa (paso opcional).

- [ ] **Step 5: Reescribir el README con instalación, config y allowlist**

Replace el contenido de `integrations/openclaw-aido-plugin/README.md`:

````markdown
# AIDO Control — OpenClaw plugin

Tools de OpenClaw para inspeccionar y controlar tu **AIDO Local Control Center**
(FastAPI en `http://127.0.0.1:4310`).

## Tools

| Tool | Tipo | Qué hace |
|---|---|---|
| `aido_overview` | lectura | Resumen curado del estado (proyectos, conteos, seguridad, next steps). |
| `aido_list_projects` | lectura | Lista los proyectos registrados. |
| `aido_list_templates` | lectura | Lista plantillas (usa el `id` como `templateId` al crear). |
| `aido_create_project` | escritura | Crea un proyecto. Marcada `optional`: debes habilitarla explícitamente. |

## Build e instalación

```bash
npm install
npm run build
npm run plugin:validate
# Desde la raíz del repo AIDO:
openclaw plugins install ./integrations/openclaw-aido-plugin --link
openclaw plugins inspect aido-control --runtime --json
```

## Configuración (`~/.openclaw/openclaw.json`)

```json
{
  "plugins": {
    "entries": {
      "aido-control": {
        "config": { "baseUrl": "http://127.0.0.1:4310" }
      }
    }
  }
}
```

`baseUrl` es opcional; por defecto `http://127.0.0.1:4310`.

## Seguridad

- Las 3 tools de lectura no requieren token.
- `aido_create_project` es `optional`: habilítala solo en el agente que quieras
  (allowlist de tools del agente). El token de escritura de AIDO se obtiene por
  handshake en cada llamada y **no** se persiste ni se loguea.
- El plugin solo habla a `127.0.0.1`.

## Smoke en vivo

Con AIDO corriendo:

```bash
node scripts/smoke.mjs
```
````

- [ ] **Step 6: Verificar que los gates de AIDO siguen verdes**

Run (desde la raíz del repo; lee el **exit code real**, no la notificación):

```bash
corepack pnpm@10.24.0 run test:py
corepack pnpm@10.24.0 run quality:productive-truth
corepack pnpm@10.24.0 run quality:architecture
```

Expected: exit 0 en los tres. El folder `integrations/openclaw-aido-plugin/` no está en el scope de Ruff/Biome ni de los scanners Python (semgrep apunta a `local_control_center`/`web/src`); si algún scanner lo recogiera, añade la exclusión correspondiente en su config y vuelve a correr.

- [ ] **Step 7: Commit y push**

```bash
git add integrations/openclaw-aido-plugin/README.md \
        integrations/openclaw-aido-plugin/scripts/smoke.mjs
git commit -m "Docs (OpenClaw): instalación, smoke y verificación de gates del plugin aido-control"
git push origin dev
```

---

## Self-Review (cobertura del spec)

- **Mecanismo plugin JS / `defineToolPlugin`** → Tasks 1, 3.
- **Topología misma máquina / loopback** → `aidoClient` apunta a `127.0.0.1`; sin red (Tasks 2, 3, README).
- **Alcance lectura + crear** → 4 tools (Task 3); cliente (Task 2).
- **Estado de proyectos (`overview` curado)** → `summarizeOverview` (Task 2), tool `aido_overview` (Task 3).
- **Crear proyecto con handshake + headers correctos** → `createProject` (Task 2), tool (Task 3).
- **Guardrail least-privilege** → `aido_create_project` `optional` verificada en manifest (Task 3, Step 6) + README allowlist (Task 4).
- **Token nunca persistido/logueado** → token en headers, errores sin token (Task 2).
- **Ubicación fuera de gates** → `integrations/...`; verificación de gates (Task 4, Step 6).
- **Instalación local (supuesto #1 resuelto)** → `openclaw plugins install ... --link` (Task 4).
- **Versión SDK (supuesto #2 resuelto)** → scaffold fija `openclaw >=2026.5.17` para 2026.6.9 (Task 1, Global Constraints).
- **Manejo de errores legible** → tests + implementación (Task 2).
- **Testing** → vitest en Tasks 2 y 3; smoke en Task 4.
