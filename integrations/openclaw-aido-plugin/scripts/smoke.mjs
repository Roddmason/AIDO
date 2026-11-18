// Smoke en vivo: requiere AIDO corriendo (npm run start -> http://127.0.0.1:4310).
// Uso (solo lectura):  node scripts/smoke.mjs
// Uso (+ escritura):   AIDO_SMOKE_WRITE=1 AIDO_SMOKE_PROJECT_ID=<id> node scripts/smoke.mjs
import { AidoClient } from "../dist/aidoClient.js";

const baseUrl = process.env.AIDO_BASE_URL ?? "http://127.0.0.1:4310";
const client = new AidoClient({ baseUrl });

try {
  const overview = await client.getOverview();
  console.log("security:", overview.security);
  console.log("counts:", overview.counts);

  const projects = await client.listProjects();
  console.log(`projects (${projects.length}):`, projects.map((p) => `${p.name} [${p.status}]`));

  const templates = await client.listTemplates();
  console.log(`templates (${templates.length}):`, templates.map((t) => t.id));

  const threads = await client.listThreads();
  console.log(`threads (${threads.length}):`, threads.map((t) => `${t.title} [${t.status}]`));

  const worker = await client.getWorkerStatus();
  console.log("worker status:", worker);

  // Camino de escritura: opt-in explícito, no corre por defecto.
  if (process.env.AIDO_SMOKE_WRITE === "1") {
    const projectId = process.env.AIDO_SMOKE_PROJECT_ID;
    if (!projectId) {
      throw new Error("AIDO_SMOKE_WRITE=1 requiere AIDO_SMOKE_PROJECT_ID=<id>");
    }
    const thread = await client.createThread({ projectId, title: "openclaw smoke test" });
    console.log("created thread:", `${thread.id} (${thread.title})`);

    const status = await client.getThreadStatus(thread.id);
    console.log("thread status:", { status: status.status, running: status.running, lastEvents: status.lastEvents.length });
  }
} catch (err) {
  console.error(String(err?.message ?? err));
  process.exit(1);
}
