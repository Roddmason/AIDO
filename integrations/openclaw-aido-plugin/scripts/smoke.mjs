// Smoke en vivo: requiere AIDO corriendo (npm run start -> http://127.0.0.1:4310).
// Uso: node scripts/smoke.mjs
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
} catch (err) {
  console.error(String(err?.message ?? err));
  process.exit(1);
}
