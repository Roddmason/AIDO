# Diseño — Plugin OpenClaw `aido-control`

- **Fecha:** 2026-06-24
- **Estado:** Aprobado (diseño). Pendiente: spec review → plan de implementación.
- **Autor:** equipo senior (AIDO)

## 1. Contexto y objetivo

[OpenClaw](https://docs.openclaw.ai/) es el agente personal de IA open-source (self-hosted, Node.js)
que el usuario ejecuta en su equipo. **AIDO** (`local_control_center`) es el *Local Control Center*:
una app FastAPI que gobierna proyectos, product-loop, agentes y workflows, servida por defecto en
`http://127.0.0.1:4310` con escritura protegida por un token de loopback (handshake).

El objetivo es conectar **OpenClaw → AIDO** mediante un **plugin nativo de OpenClaw** (mecanismo
elegido por el usuario sobre la alternativa de servidor MCP) para, desde lenguaje natural en OpenClaw:

- conocer el **estado** de los proyectos,
- **listar** proyectos y plantillas,
- **crear** un proyecto nuevo.

### Criterio de éxito verificable

1. Las 4 tools aparecen registradas en OpenClaw (`openclaw plugins inspect aido-control`).
2. Las 3 tools de lectura devuelven datos reales de la API de AIDO.
3. `aido_create_project` crea un proyecto que luego aparece en `aido_list_projects`.

### Fuera de alcance (YAGNI)

- Iniciar/transicionar el product-loop, lanzar workflows o correr agents (sería "control amplio").
- Exponer AIDO fuera de loopback / OpenClaw remoto.
- Publicar el plugin en ClawHub.

## 2. Decisiones (con razón)

| Decisión | Elección | Razón |
|---|---|---|
| Mecanismo | Plugin nativo OpenClaw (JS/TS) | Elegido por el usuario; mecanismo de primera clase de OpenClaw. Costo asumido: codebase JS separado del Python de AIDO. |
| Topología | Misma máquina | El plugin corre en el runtime Node de OpenClaw y habla a `127.0.0.1`; sin superficie de red. |
| Alcance | Lectura + crear proyecto | Exactamente lo pedido, con superficie de escritura mínima. |
| Entry SDK | `defineToolPlugin` | Inyecta `config` tipado (desde `configSchema`) y `context.signal`; más limpio que `definePluginEntry` para un plugin de solo-tools. |
| Ubicación | `integrations/openclaw-aido-plugin/` | Fuera del package Python `local_control_center/` y de `local-control-center/web/`, para no entrar en los gates de Ruff/Biome/scanners de AIDO. |

## 3. Arquitectura del paquete

Paquete ESM TypeScript independiente, instalable en el OpenClaw del usuario.

```
integrations/openclaw-aido-plugin/
  package.json            # type:module + campo openclaw.extensions + compat
  openclaw.plugin.json    # manifest: id, configSchema, contracts.tools, uiHints
  src/
    index.ts              # defineToolPlugin(...) — configSchema + las 4 tools
    aidoClient.ts         # cliente HTTP fino sobre la API de AIDO
    types.ts              # tipos de respuesta de AIDO usados por las tools
  tests/
    aidoClient.test.ts
    tools.test.ts
  README.md               # instalación + bloque openclaw.json + allowlist
  tsconfig.json
  vitest.config.ts
```

### Unidades y responsabilidades

- **`aidoClient.ts`** — *qué hace:* envuelve la API HTTP de AIDO; expone `getOverview()`,
  `listProjects()`, `listTemplates()`, `createProject(body)`. *cómo se usa:* se instancia con
  `{ baseUrl }`. *de qué depende:* `fetch` global y la API de AIDO. Internamente resuelve el token
  de escritura por handshake (con cache + TTL corto) y añade headers en mutaciones. Es la única
  unidad que conoce detalles de transporte/seguridad.
- **`index.ts`** — *qué hace:* define el plugin y declara las 4 tools, traduciendo parámetros
  typebox ↔ llamadas del cliente y formateando la salida para el modelo. No contiene lógica HTTP.
- **`types.ts`** — contratos de datos de AIDO (solo tipos).

## 4. Contratos de AIDO usados (verificados en el repo)

| Endpoint | Método | Auth | Uso |
|---|---|---|---|
| `/api/v1/overview` | GET | no | estado general del control center |
| `/api/v1/projects` | GET | no | listado de proyectos |
| `/api/v1/project-templates` | GET | no | plantillas para elegir `templateId` |
| `/api/v1/security/handshake` | GET | no | obtener el token de escritura |
| `/api/v1/projects` | POST | **token** | crear proyecto |

`ProjectCreateRequest` (alias camelCase, todos opcionales salvo la combinación válida ruta vs.
nombre+workspace): `name`, `path`, `workspaceBasePath`, `projectDirectoryName`, `templateId`,
`createDirectory` (default `true`), `metadata` (default `{}`).

`require_write` en AIDO compara `X-Local-Control-Token` contra `platform.get_handshake()["token"]`.
El cliente de tests de AIDO acompaña ese token con `Origin: http://127.0.0.1`; el plugin replica
ambos headers en las mutaciones.

## 5. Tools expuestas

| Tool | Tipo | Parámetros (typebox) | Acción |
|---|---|---|---|
| `aido_overview` | read | — | `GET /overview`; devuelve el snapshot de estado |
| `aido_list_projects` | read | — | `GET /projects`; devuelve `id, name, path, status, templateId` por proyecto |
| `aido_list_templates` | read | — | `GET /project-templates`; devuelve `id, name, kind` |
| `aido_create_project` | **write** | `name?`, `path?`, `workspaceBasePath?`, `projectDirectoryName?`, `templateId?`, `createDirectory=true`, `metadata?` | handshake → `POST /projects`; marcada `optional: true` |

Las tools `read` devuelven JSON (el modelo ve JSON formateado). `aido_create_project` devuelve un
resumen legible del proyecto creado (`id`, `name`, `path`, `status`).

## 6. Flujo de datos (escritura)

El token de escritura es de loopback y rota: **no se persiste**. En cada `aido_create_project`:

1. `GET /api/v1/security/handshake` → `token` (cache en memoria con TTL corto para evitar un
   round-trip por llamada; se renueva ante `403`).
2. `POST /api/v1/projects` con headers `X-Local-Control-Token: <token>` y `Origin: http://127.0.0.1`,
   body en camelCase.
3. Respeta `context.signal` (abort) en ambas llamadas.

Las tools de lectura no requieren token.

## 7. Seguridad / guardrails (least-privilege)

- `aido_create_project` marcada **`optional: true`**: el modelo no puede invocarla hasta que el
  usuario la habilite explícitamente en `openclaw.json`. Las lecturas quedan disponibles por defecto.
- Solo habla a `127.0.0.1` (`config.baseUrl`, default `http://127.0.0.1:4310`); sin red.
- El token nunca se persiste ni se loguea (redacción en cualquier traza de error).
- Recomendación operativa: rutear la tool de escritura a **un único agente** OpenClaw vía
  `agents.<id>` allowlist, no global.
- `configSchema` con `additionalProperties: false` (regla del manifest).

## 8. Manejo de errores

- AIDO no responde en `baseUrl` → error legible "AIDO no responde en {baseUrl}".
- `403` en POST → "handshake inválido/expirado"; se renueva el token una vez y se reintenta; si
  persiste, se propaga.
- Otros `4xx/5xx` → `status` + cuerpo (redactado).
- Nunca se traga la excepción; el mensaje es accionable para el modelo/usuario.

## 9. Testing

- **Vitest** con un fake de la API de AIDO:
  - cada read llama el endpoint correcto y mapea la salida;
  - create hace handshake→POST con headers correctos y body camelCase;
  - `403` dispara un único refresh+retry y luego propaga;
  - error de conexión produce mensaje legible.
- Aserción de que `aido_create_project` está declarada `optional` en el manifest.
- Smoke manual opcional contra una instancia AIDO real corriendo.

## 10. Instalación (en el OpenClaw del usuario)

`~/.openclaw/openclaw.json` (esquema ilustrativo, a ajustar a la versión instalada):

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

Habilitar la tool de escritura solo en el agente elegido (allowlist).

## 11. Supuestos / a verificar en implementación (no se inventan)

1. **Comando exacto para instalar un plugin local no publicado**: la doc de *building-plugins* no lo
   fija. Confirmar con `openclaw plugins --help` en el OpenClaw del usuario antes de cerrar el README.
2. **Versión de `pluginApi`/SDK** del manifest debe coincidir con el OpenClaw instalado (la doc
   muestra `2026.3.24-beta.2`); ajustar a la versión real.
3. Confirmar que `integrations/openclaw-aido-plugin/` **no** es recogido por los scanners Python de
   AIDO (`test:py`) ni por el scope Biome de `local-control-center/web`.
4. Confirmar la forma exacta del payload de `/api/v1/overview` (`OverviewResponse`) para el formateo
   de `aido_overview`.

## 12. Fuentes

- [OpenClaw — tools/overview](https://docs.openclaw.ai/tools)
- [Building plugins](https://docs.openclaw.ai/plugins/building-plugins)
- [Tool plugins](https://docs.openclaw.ai/plugins/tool-plugins)
- [Plugin manifest](https://docs.openclaw.ai/plugins/manifest)
- [MCP en OpenClaw](https://docs.openclaw.ai/cli/mcp)
