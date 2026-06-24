import { describe, expect, it, vi } from "vitest";
import { AidoClient, AidoError, summarizeOverview } from "../src/aidoClient.js";

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

    // The SECOND POST must carry the fresh token, not the stale one.
    const postCalls = fetchImpl.mock.calls.filter(
      ([, init]) => (init as RequestInit).method === "POST",
    );
    const secondPostHeaders = (postCalls[1][1] as RequestInit).headers as Record<string, string>;
    expect(secondPostHeaders["x-local-control-token"]).toBe("FRESH");
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
