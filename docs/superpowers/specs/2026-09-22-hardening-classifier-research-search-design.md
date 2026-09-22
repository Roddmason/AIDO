# Endurecimiento pendiente + clasificador por puntaje relativo + búsqueda con SearXNG

**Fecha:** 2026-09-22
**Estado:** Diseño aprobado (sub-proyecto 3 de 3). Pendiente de plan de implementación.
**Autor:** Rodrigo Mason (diseño asistido)

## 1. Objetivo

Cerrar los defectos observados en la validación en vivo del 2026-09-22 y habilitar respuestas reales del
ResearchAgent. Cada ítem es un cambio acotado con su propia prueba; se entregan por separado.

## 2. Ítems

### 2.1 Clasificador de intención: puntaje relativo (decisión del operador)

**Problema (verificado):** el ruteo de intake es excluyente (`elif "research" in decision.intents`,
`threads/coordinator.py:265`) y el clasificador puntúa por presencia de palabras
(`product_loop/intent_classifier.py:36-118,177-180`). Tres intentos (vocabulario, poda, disjointness)
fallaron con prompts reales en ambos sentidos (ver memoria `aido-intake-research-routing-exclusive`).

**Diseño:**
- `IntentClassification` expone `scores` (ya calculados en `_intent_scores`, `:263`).
- Ruta solo-investigación **iff** `scores.research > 0` **y** `scores.research ≥ max(scores[c] for c in
  CHANGE_PRODUCING)` **o** hay un marcador explícito de solo-lectura. Marcadores (es/en, con acentos
  plegados): "solo lectura", "no modifiques", "sin modificar nada", "no cambies", "read-only",
  "don't change", "do not modify". Un verbo de cambio explícito ("corrige", "arregla", "implementa", "fix",
  "implement") sin marcador de solo-lectura gana a research en empate.
- Vocabulario español de investigación (rama local `fix/intake-spanish-research`, 4d0a5b16+833c475d) se
  reincorpora con plegado de acentos.
- Las preguntas del clasificador pasan a claves i18n (hoy texto inglés fijo, `intent_classifier.py:343`).
- **Corpus de prueba obligatorio** (ambos sentidos, del revisor): "Analiza por qué el login falla… y corrige
  el bug", "Compara … y corrige el bug…", "Corrige el bug de memoria sin modificar la API pública",
  "Fix the crash…; compare with the previous release" → **no** research; "Investiga qué tests cubren el
  login", "Research which feature flags exist", "Investiga por qué el build de refactor está lento, solo
  analiza, no modifiques nada", "Analiza el bug de memoria en modo solo lectura, no lo corrijas",
  "Evaluate migration options for Postgres 17 and cite sources", el mensaje original de la validación →
  research.

### 2.2 ResearchAgent con SearXNG propio (decisión del operador)

**Problema (verificado):** DuckDuckGo HTML responde HTTP 202 + página anti-bot al UA
`AIDO-ResearchAgent/1.0` → 0 resultados → "no sources" (`agents/research_agent.py:131-176`). No se evade
la detección con un UA de navegador.

**Diseño:**
- Proveedor `searxng`: `GET {baseUrl}/search?q=…&format=json` (requiere `search.formats: [html, json]` en
  la configuración del contenedor). Setting `research.webSearch.provider` (`searxng` | `duckduckgo`) y
  `research.webSearch.baseUrl` (default `http://127.0.0.1:8888`), validados (loopback o allowlist).
- `assert_external_boundary()` y límites de bytes/timeout existentes se conservan.
- **Diagnóstico honesto:** una respuesta no-200 o página de desafío se reporta como
  `research_provider_blocked` ("el proveedor de búsqueda bloqueó la consulta"), distinta de "sin fuentes".
- Levantar SearXNG: contenedor Docker oficial `searxng/searxng` en `127.0.0.1:8888` con `format json`
  habilitado. **La descarga de la imagen se confirma con el operador antes de ejecutarla.**
- Copy de la tarjeta de bloqueo de research sin la frase "high-impact decision" cuando el research viene
  del intake (hoy es engañosa).

### 2.3 Refresco de health checks con backoff

`agents/runtime_health_refresh.py` reencola un objetivo apenas su check anterior es terminal
(`:106-112,169`), sin memoria de fallos: con el token de Claude CLI vencido genera 3-5 jobs/min.
**Diseño:** cooldown exponencial por objetivo tras fallo o cancelación (60 s → 5 min → 15 min máx.),
reiniciado con un éxito; los checks disparados por el operador ignoran el cooldown.

### 2.4 Causa de `resource_wait` visible y sin spam de eventos

- El governor escribe `job.resource_wait` en cada reevaluación (~cada 2 s). **Diseño:** escribir solo
  cuando cambia `reasonCode` para ese job.
- La UI muestra "Queued: waiting for a worker" también en `resource_wait`. **Diseño:** el hilo recibe un
  evento `resource_wait` (deduplicado por motivo) y `ThreadExecutionPanel` muestra "Esperando capacidad del
  equipo: <motivo legible>" reutilizando el mapeo de códigos del modal de runtimes (2443dd7d).

### 2.5 Decisiones con códigos crudos

Botones `continue_existing` / `improve_existing` / `performance_pass` / `create_new_anyway` → etiquetas
i18n con descripción corta. El valor enviado no cambia.

### 2.6 Falso positivo de "funcionalidad existente"

Un hilo cancelado antes de correr registró una funcionalidad y bloqueó el hilo siguiente
(`functionality_registry`, score léxico 0.627). **Diseño:** el registro de funcionalidad se crea o se
confirma solo cuando el loop entrega (`delivered`) o, como mínimo, cuando el brief se aprueba; las
coincidencias contra funcionalidades de hilos cancelados sin ejecución se ignoran. Requiere inspección del
registro antes del plan (punto de verificación explícito del plan).

### 2.7 Panel de ejecución desfasado

Tras terminar el job, el panel sigue mostrando "Branch ready: active". **Diseño:** si el hilo no tiene job
activo (`open`, `resolved`, `archived`) la derivación de pipeline cierra los pasos activos como
detenidos/completos según el último evento terminal.

### 2.8 Reaper de jobs anidados zombi

Jobs hijos creados `running` sin lease (`agent.product_owner`, `agent.architect`, `agent.devops`,
`agent.developer`, `agent.project_assessment`) quedan `running` para siempre cuando el padre pierde su
lease (`product_owner_agent.py:1532-1539`, `requeue_expired_jobs` exige `lease_expires_at IS NOT NULL`,
`jobs_approvals/repository.py:884-895`). **Diseño:** al recuperar o completar el padre, fallar sus hijos
`running` sin lease (enlazados por `workflow_run_id` o `parentJobId` que se escribe al crearlos) con motivo
`parent_lease_expired`, y cerrar sus `agent_runs`. Limpieza única del zombi existente `job-31a6b390`.

### 2.9 Rojos preexistentes

- `@author Rodrigo Mason` en los 6 módulos de 013b1b26 (`agent_resource_policy.py`, `runtime_preflight.py`,
  `runtime_preflight_cli.py`, `timeout_reconciliation.py`, `research_resolution.py`,
  `runtime_risk_review.py`).
- Claves i18n con `en == es`: `app.threads.remediation.riskReview.field.runtime` y `.field.loopId`.
- `test_local_worker_runtime::test_worker_product_loop_exception_creates_worker_retry_remediation`
  (`KeyError 'details'`): diagnosticar si el test o el código divergen antes de corregir.

### 2.10 Costo de `GET /overview`

Ya no bloquea el event loop (3b1afa14) pero tarda ~3,5 s por llamada cada 5 s. **Diseño:** perfilar
(`cProfile` sobre `build_overview_from_connection` con la BD viva en copia) y atacar el mayor costo con la
evidencia; meta < 1 s. Si el costo dominante es inherente, reducir la frecuencia del poll de las piezas
pesadas en vez de recortar datos.

## 3. Pruebas

Cada ítem con prueba que falla antes y pasa después (TDD). Corpus del §2.1 como tests parametrizados. El
proveedor SearXNG se prueba con un servidor HTTP local de prueba en el borde; la verificación final usa el
contenedor real.

## 4. Fuera de alcance

- Resultados de búsqueda de proveedores con API de pago.
- Rediseño del registro de funcionalidades más allá del filtro de §2.6.
