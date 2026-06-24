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
