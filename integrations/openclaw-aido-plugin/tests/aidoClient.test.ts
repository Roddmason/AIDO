import { describe, expect, it, vi } from "vitest";
import { AidoClient, AidoError, summarizeOverview } from "../src/aidoClient.js";

const BASE = "http://127.0.0.1:4310";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function findPost(fetchImpl: ReturnType<typeof vi.fn>) {
  const call = fetchImpl.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST");
  if (!call) throw new Error("no POST call recorded");
  return call as [string, RequestInit];
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

  it("getOverview calls GET /api/v1/overview and returns a curated summary", async () => {
    const rawOverview = {
      security: { loopbackOnly: true, writeTokenRequired: true },
      projects: [{ id: "p1", name: "Demo", path: "/demo", status: "active", templateId: "t1" }],
      workflows: [{}, {}],
      jobs: [{}],
      nextSteps: [{ id: "ns1" }],
      riskRegister: [],
    };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(rawOverview));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const summary = await client.getOverview();

    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/overview`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(summary.security).toEqual({ loopbackOnly: true, writeTokenRequired: true });
    expect(summary.counts.projects).toBe(1);
    expect(summary.projects).toEqual([
      { id: "p1", name: "Demo", path: "/demo", status: "active", templateId: "t1" },
    ]);
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

describe("AidoClient.listThreads", () => {
  it("GETs /api/v1/threads with query params and returns the threads array", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ threads: [{ id: "t1" }] }));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const threads = await client.listThreads({ projectId: "p1", includeArchived: true });
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/threads?projectId=p1&includeArchived=true`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(threads).toEqual([{ id: "t1" }]);
  });

  it("GETs without a query string when no params are given", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ threads: [] }));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const threads = await client.listThreads();
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/threads`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(threads).toEqual([]);
  });
});

describe("AidoClient.getThreadStatus", () => {
  // GET /api/v1/threads/{id} devuelve ThreadDetailResponse = { thread, messages, artifacts, decisions, events },
  // NO un ThreadRecord plano. `events` ahí es tail-safe (list_events limit=500, DESC+reversed).
  function detailResponse(threadOverrides: Record<string, unknown>, events: unknown[]) {
    return {
      thread: {
        id: "t1",
        projectId: "p1",
        ownerType: "workspace",
        ownerId: "p1",
        title: "Mi hilo",
        status: "running",
        createdAt: "x",
        updatedAt: "y",
        ...threadOverrides,
      },
      messages: [],
      artifacts: [],
      decisions: [],
      events,
    };
  }

  it("reads id/title from detail.thread and lastEvents from detail.events (tail-safe); status/running from the events endpoint", async () => {
    const events = Array.from({ length: 15 }, (_, i) => ({ sequence: i + 1, type: "message", content: `e${i + 1}` }));
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/threads/t1")) return jsonResponse(detailResponse({}, events));
      if (url.endsWith("/api/v1/threads/t1/events")) {
        // Ventana ASC distinta (afterSeq=0, tope 300): a propósito trae otra cosa para
        // probar que lastEvents NO sale de acá.
        return jsonResponse({ events: events.slice(0, 3), threadStatus: "running", running: true });
      }
      throw new Error(`unexpected ${url}`);
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const status = await client.getThreadStatus("t1");
    expect(status).toEqual({
      id: "t1",
      title: "Mi hilo",
      status: "running",
      running: true,
      lastEvents: events.slice(-10),
    });
  });

  it("honors a custom lastEventsLimit", async () => {
    const events = Array.from({ length: 5 }, (_, i) => ({ sequence: i + 1 }));
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/threads/t1")) return jsonResponse(detailResponse({ title: "T" }, events));
      if (url.endsWith("/api/v1/threads/t1/events")) {
        return jsonResponse({ events: [], threadStatus: "idle", running: false });
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const status = await client.getThreadStatus("t1", 2);
    expect(status.lastEvents).toEqual(events.slice(-2));
  });

  it("returns the true tail with >300 events (el cap tail-safe del detalle es 500, no 300)", async () => {
    const events = Array.from({ length: 305 }, (_, i) => ({ sequence: i + 1, type: "message" }));
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/threads/t1")) return jsonResponse(detailResponse({}, events));
      if (url.endsWith("/api/v1/threads/t1/events")) {
        // afterSeq=0 + tope 300 en ESTE endpoint trae las 300 MÁS VIEJAS (seq 1..300);
        // si lastEvents saliera de acá, el resultado sería incorrecto.
        return jsonResponse({ events: events.slice(0, 300), threadStatus: "running", running: true });
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const status = await client.getThreadStatus("t1", 10);
    expect((status.lastEvents as Array<{ sequence: number }>).map((e) => e.sequence)).toEqual([
      296, 297, 298, 299, 300, 301, 302, 303, 304, 305,
    ]);
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

describe("AidoClient.createProject (submit 202 + poll executions)", () => {
  function submission(overrides: Record<string, unknown> = {}) {
    return { executionId: "exec-1", jobId: "job-1", operation: "create_project", status: "queued", ...overrides };
  }

  it("submits via POST (202), polls until completed, and resolves with result.project", async () => {
    let executionCalls = 0;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") return jsonResponse(submission(), 202);
      if (url.endsWith("/api/v1/executions/exec-1")) {
        executionCalls += 1;
        if (executionCalls < 3) return jsonResponse(submission({ status: "running" }));
        return jsonResponse({
          ...submission({ status: "completed" }),
          resultStatusCode: 201,
          result: { project: { id: "p9", name: "New", path: "/p", status: "active" } },
        });
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    const sleepImpl = vi.fn().mockResolvedValue(undefined);
    const client = new AidoClient({ baseUrl: BASE, fetchImpl, sleepImpl, pollIntervalMs: 5, pollTimeoutMs: 50 });

    const project = await client.createProject({ name: "New", workspaceBasePath: "/ws" });

    expect(project.id).toBe("p9");
    expect(executionCalls).toBe(3);
    expect(sleepImpl).toHaveBeenCalledTimes(2);
    expect(sleepImpl).toHaveBeenCalledWith(5);

    const post = findPost(fetchImpl);
    expect(JSON.parse(post[1].body as string)).toEqual({
      createDirectory: true,
      name: "New",
      workspaceBasePath: "/ws",
    });
  });

  it("throws an AidoError when the execution ends in a failed status", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") return jsonResponse(submission(), 202);
      if (url.endsWith("/api/v1/executions/exec-1")) {
        return jsonResponse({ ...submission({ status: "failed" }), resultStatusCode: 500 });
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({
      baseUrl: BASE,
      fetchImpl,
      sleepImpl: vi.fn().mockResolvedValue(undefined),
      pollIntervalMs: 5,
      pollTimeoutMs: 50,
    });
    await expect(client.createProject({ path: "/p" })).rejects.toBeInstanceOf(AidoError);
    await expect(client.createProject({ path: "/p" })).rejects.toThrow(/failed/);
  });

  it("throws a timeout AidoError when polling never reaches a terminal status", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") return jsonResponse(submission(), 202);
      if (url.endsWith("/api/v1/executions/exec-1")) return jsonResponse(submission({ status: "running" }));
      throw new Error("unexpected");
    });
    const sleepImpl = vi.fn().mockResolvedValue(undefined);
    const client = new AidoClient({ baseUrl: BASE, fetchImpl, sleepImpl, pollIntervalMs: 10, pollTimeoutMs: 30 });

    await expect(client.createProject({ path: "/p" })).rejects.toThrow(/no termin/i);

    const executionCalls = fetchImpl.mock.calls.filter(([url]) => (url as string).endsWith("/api/v1/executions/exec-1")).length;
    expect(executionCalls).toBe(3);
    expect(sleepImpl).toHaveBeenCalledTimes(2);
  });

  it("on a 403 during submission refreshes the token once, retries, then polls to completion", async () => {
    let posts = 0;
    const tokens = ["STALE", "FRESH"];
    let handshakes = 0;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: tokens[handshakes++] });
      if (url.endsWith("/api/v1/projects") && init.method === "POST") {
        posts += 1;
        return posts === 1 ? jsonResponse({ detail: "forbidden" }, 403) : jsonResponse(submission(), 202);
      }
      if (url.endsWith("/api/v1/executions/exec-1")) {
        return jsonResponse({ ...submission({ status: "completed" }), result: { project: { id: "p9" } } });
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl, sleepImpl: vi.fn().mockResolvedValue(undefined) });

    const project = await client.createProject({ path: "/p" });
    expect(posts).toBe(2);
    expect(handshakes).toBe(2);
    expect(project.id).toBe("p9");

    const postCalls = fetchImpl.mock.calls.filter(([, init]) => (init as RequestInit)?.method === "POST");
    const secondPostHeaders = (postCalls[1][1] as RequestInit).headers as Record<string, string>;
    expect(secondPostHeaders["x-local-control-token"]).toBe("FRESH");
  });
});

describe("AidoClient.createThread", () => {
  // POST /api/v1/threads también responde con el envelope ThreadDetailResponse
  // (thread_detail() en threads/api.py), no un ThreadRecord plano: hay que desenvolver `.thread`.
  function threadDetailEnvelope(thread: Record<string, unknown>) {
    return { thread, messages: [], artifacts: [], decisions: [], events: [] };
  }

  it("POSTs with default ownerType=workspace and ownerId=projectId when omitted, and unwraps thread from the detail envelope", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/threads") && init.method === "POST") {
        return jsonResponse(
          threadDetailEnvelope({
            id: "t1",
            projectId: "p1",
            ownerType: "workspace",
            ownerId: "p1",
            title: "Nuevo",
            status: "open",
            createdAt: "x",
            updatedAt: "y",
          }),
          201,
        );
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const thread = await client.createThread({ projectId: "p1", title: "Nuevo" });

    const post = findPost(fetchImpl);
    expect(JSON.parse(post[1].body as string)).toEqual({
      projectId: "p1",
      ownerType: "workspace",
      ownerId: "p1",
      title: "Nuevo",
    });
    expect(thread).toEqual({
      id: "t1",
      projectId: "p1",
      ownerType: "workspace",
      ownerId: "p1",
      title: "Nuevo",
      status: "open",
      createdAt: "x",
      updatedAt: "y",
    });
  });

  it("respects an explicit ownerType/ownerId when provided", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/threads") && init.method === "POST") {
        return jsonResponse(threadDetailEnvelope({ id: "t2", title: "X" }), 201);
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const thread = await client.createThread({ projectId: "p1", title: "X", ownerType: "loop", ownerId: "loop-9" });

    const post = findPost(fetchImpl);
    expect(JSON.parse(post[1].body as string)).toEqual({
      projectId: "p1",
      ownerType: "loop",
      ownerId: "loop-9",
      title: "X",
    });
    expect(thread).toEqual({ id: "t2", title: "X" });
  });
});

describe("AidoClient.sendMessage", () => {
  it("POSTs { content } with the auth token and returns the ack", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/threads/t1/messages") && init.method === "POST") {
        return jsonResponse({ status: "queued" }, 200);
      }
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const ack = await client.sendMessage("t1", "hola");

    const post = findPost(fetchImpl);
    const headers = post[1].headers as Record<string, string>;
    expect(headers["x-local-control-token"]).toBe("TKN");
    expect(JSON.parse(post[1].body as string)).toEqual({ content: "hola" });
    expect(ack).toEqual({ status: "queued" });
  });

  it("throws a clear AidoError on 409 when a run is already queued or running", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/threads/t1/messages")) return jsonResponse({ detail: "busy" }, 409);
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    await expect(client.sendMessage("t1", "hola")).rejects.toBeInstanceOf(AidoError);
    await expect(client.sendMessage("t1", "hola")).rejects.toThrow(/409|cola|corriendo/i);
  });
});

describe("AidoClient.getExecutionStatus", () => {
  it("GETs /api/v1/executions/{id} and returns the raw execution record", async () => {
    const record = { executionId: "e1", jobId: "j1", operation: "create_project", status: "running" };
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(record));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const result = await client.getExecutionStatus("e1");
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/executions/e1`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(result).toEqual(record);
  });
});

describe("AidoClient worker", () => {
  it("getWorkerStatus GETs /api/v1/workers/status", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ paused: true }));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const status = await client.getWorkerStatus();
    expect(fetchImpl).toHaveBeenCalledWith(
      `${BASE}/api/v1/workers/status`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(status).toEqual({ paused: true });
  });

  it("resumeWorker POSTs /api/v1/workers/resume with the auth token", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith("/api/v1/security/handshake")) return jsonResponse({ token: "TKN" });
      if (url.endsWith("/api/v1/workers/resume") && init.method === "POST") return jsonResponse({ paused: false }, 200);
      throw new Error("unexpected");
    });
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    const result = await client.resumeWorker();

    const post = findPost(fetchImpl);
    const headers = post[1].headers as Record<string, string>;
    expect(headers["x-local-control-token"]).toBe("TKN");
    expect(result).toEqual({ paused: false });
  });
});

describe("AidoClient errors", () => {
  it("wraps connection failures in a readable AidoError", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    await expect(client.listProjects()).rejects.toBeInstanceOf(AidoError);
    await expect(client.listProjects()).rejects.toThrow(/AIDO no responde/);
  });

  it("wraps an invalid JSON body on a 2xx response in a readable AidoError instead of a raw SyntaxError", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response("not-json{", { status: 200, headers: { "content-type": "application/json" } }),
    );
    const client = new AidoClient({ baseUrl: BASE, fetchImpl });
    await expect(client.listProjects()).rejects.toBeInstanceOf(AidoError);
  });
});
