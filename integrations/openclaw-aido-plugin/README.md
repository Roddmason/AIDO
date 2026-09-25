# AIDO Control — OpenClaw plugin

Tools de OpenClaw para inspeccionar y controlar tu **AIDO Local Control Center**
(FastAPI en `http://127.0.0.1:4310`).

## Tools

| Tool | Tipo | Qué hace |
|---|---|---|
| `aido_overview` | lectura | Resumen curado del estado (proyectos, conteos, seguridad, next steps). |
| `aido_list_projects` | lectura | Lista los proyectos registrados. |
| `aido_list_templates` | lectura | Lista plantillas (usa el `id` como `templateId` al crear). |
| `aido_create_project` | escritura | Crea un proyecto (asíncrono: hace polling de la ejecución hasta que termina). |
| `aido_list_threads` | lectura | Lista threads, opcionalmente filtrados por `projectId` / `includeArchived`. |
| `aido_create_thread` | escritura | Crea un thread en un proyecto. `ownerType`/`ownerId` por defecto: `workspace` + el propio `projectId`. |
| `aido_send_message` | escritura | Envía un mensaje a un thread e inicia un run. Falla con error claro (409) si ya hay un run en cola o corriendo. |
| `aido_thread_status` | lectura | Estado compacto de un thread: `id`, `title`, `status`, `running` y los últimos N eventos (no la timeline completa). |
| `aido_execution_status` | lectura | Estado crudo de una ejecución asíncrona de AIDO por `executionId`. |
| `aido_worker_status` | lectura | Estado del worker en background de AIDO. |
| `aido_worker_resume` | escritura | Reanuda el worker (arranca pausado; sin esto no progresa nada). |

Ninguna tool está marcada `optional`: con `tools.profile: "coding"`, OpenClaw elimina del
agente las tools opcionales de un plugin, y `tools.allow` actúa como allowlist exclusiva
(intersección) — la combinación podía dejar al agente sin ninguna tool callable. Las 4
tools de escritura declaran esto en su propia `description` ("Acción de escritura: confirma
con el usuario antes de ejecutarla salvo que la haya pedido explícitamente"); ver Seguridad.

`aido_create_project` es asíncrona en AIDO: el `POST` responde `202` con `{ executionId, ... }`
y el cliente hace polling a `/api/v1/executions/{executionId}` (intervalo 1 s, timeout 60 s)
hasta un status terminal (`completed`, `failed`, `blocked`, `cancelled`, `interrupted`).

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

- Las tools de lectura no requieren token.
- `aido_create_project`, `aido_create_thread`, `aido_send_message` y `aido_worker_resume`
  **no** son `optional` (no se puede confiar en `tools.allow`/`tools.profile` de OpenClaw
  como opt-in: con el perfil `coding`, las tools opcionales de un plugin se eliminan, y
  `tools.allow` es una allowlist exclusiva que puede dejar al agente sin tools). La
  salvaguarda para estas 4 tools es doble:
  1. Comportamiento del agente: su `description` le indica que confirme con el usuario
     antes de ejecutarlas, salvo que el usuario ya las haya pedido explícitamente.
  2. AIDO del lado servidor: el token de escritura se obtiene por handshake y se cachea
     en memoria del proceso (un `AidoClient` por `baseUrl`); si AIDO responde `403`
     (reinició y el token cambió), el cliente lo refresca y reintenta la escritura una
     vez. El token **no** se persiste ni se loguea. Cualquier approval/confirmación que
     AIDO exija del lado servidor sigue aplicando igual.
- El plugin solo habla a `127.0.0.1`.

## Smoke en vivo

Con AIDO corriendo:

```bash
node scripts/smoke.mjs
```
