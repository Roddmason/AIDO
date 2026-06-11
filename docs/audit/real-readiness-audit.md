# AIDO Real Readiness Audit

Fecha: 2026-06-11

Rama auditada: `dev`

Tipo: reauditoria actual posterior a remediaciones no-mock/productive-truth.

Este documento reemplaza el snapshot inicial de 2026-06-03. No mezcla hallazgos
historicos con estado actual. Si una capacidad no aparece como `Closed`, no debe
tratarse como cerrada para release.

## Fuentes revisadas

- `docs/audit/real-readiness-audit.md`
- `docs/roadmap/no-mock-completion-plan.md`
- `local_control_center/agents/**`
- `local_control_center/workflows/issue_to_patch_runner.py`
- `local_control_center/evidence/**`
- `local_control_center/shared/migrations.py`
- `local-control-center/web/src/**`
- `scripts/productive-truth-scan.py`
- `tests_py/test_internal_mock_product_boundary.py`
- `tests_py/test_real_readiness_architecture.py`
- `tests_py/test_aido_real_runtime_slice.py`
- `tests_py/test_no_mock_productive_scanner.py`
- `tests_py/test_hard_cutover_no_removed_compat.py`
- `tests_py/test_ci_and_openapi_client.py`

## Verificacion local ejecutada

| Comando | Resultado |
| --- | --- |
| `corepack pnpm@10.24.0 run quality:productive-truth` | PASS. `Productive truth scan passed.` |
| `corepack pnpm@10.24.0 run quality:architecture` | PASS. `68 passed`. |
| `uv run pytest tests_py/test_aido_real_runtime_slice.py tests_py/test_model_runtime_gateway.py tests_py/test_web_rework_architecture.py -q` | PASS. `111 passed`. |
| `uv run pytest tests_py/test_aido_real_runtime_slice.py tests_py/test_no_mock_productive_scanner.py tests_py/test_hard_cutover_no_removed_compat.py tests_py/test_ci_and_openapi_client.py -q` | PASS. `39 passed`. |

No se ejecuto `corepack pnpm@10.24.0 run quality` completo en esta
reauditoria. Por tanto, el cierre del quality gate completo no se reclama como
evidencia actual.

## Estado de la matriz

| Capacidad | Estado actual | Evidencia | Riesgo residual |
| --- | --- | --- | --- |
| Product runtime catalog without internal mock provider | Closed | `RUNTIME_MODES` contiene `api`, `cli`, `ollama`, `hybrid`, `manual`; tests de boundary y OpenAPI pasan; no hay `internal_mock` en codigo productivo fuera de generado excluido. | Referencias en docs/tests siguen permitidas como historial o fixtures. |
| Provider readiness truth | Partial | `runtime_status.py` separa `configured`, `available`, `executable`, `reason`, `requiredConfiguration`, `healthStatus`, `healthCheckedAt` y `lastError`; OpenAI-compatible, OpenRouter, NVIDIA NIM y Anthropic fallan cerrado sin config, health, cuenta enabled y flag real. | No se validaron credenciales, endpoints ni healthchecks reales contra entornos externos en esta reauditoria. |
| `issue_to_patch` completion gate | Closed para contrato local | Tests cubren runtime unavailable, QA argv estructurado, falta de diff, falta de QA, evidencia incompleta, approval y QA failed. | No hubo ejecucion real de Codex CLI, Claude Code, OpenHands o SWE-agent contra un workspace productivo. |
| Quality no-mock scanner | Closed | `quality:productive-truth` paso y tests unitarios del scanner pasan. | El scanner no cubre todos los falsos exitos semanticos; ver Open sobre benchmarks manuales. |
| Local quality gate | Partial | Los dos gates pedidos y el set enfocado pasan. `scripts/quality-local.ps1` esta cableado para scanner, Python tests, web tests, build, typecheck, lint, arquitectura, gitleaks y Semgrep. | El comando completo `corepack pnpm@10.24.0 run quality` no fue ejecutado en esta reauditoria. |
| Evidence-backed blocked state | Partial | `issue_to_patch` devuelve `runtime_unavailable` con evidence y reason tecnico cuando falta runtime ejecutable. | La API general de evidencia todavia puede alimentar benchmark outcomes desde evidencia/usage manual; ver Open. |

## Closed

- `internal_mock` ya no es runtime productivo. El API de runtime providers no lo
  expone, los perfiles de agente lo rechazan, `issue_to_patch` lo rechaza y los
  seeds actuales no crean registros `internal_mock`.
- `/api/v1/model-gateway/route/execute-mock` ya no esta expuesto en API
  productiva ni en el cliente OpenAPI generado. Los tests de cutover y OpenAPI
  cubren esta ausencia.
- `OllamaProvider.chat_completion()` llama a `/api/chat`; ya no devuelve una
  respuesta local fabricada.
- `AnthropicAPIProvider` no simula respuesta. Ejecuta health/discovery contra
  `/v1/models`, chat contra `/v1/messages`, requiere
  `AIDO_ANTHROPIC_API_KEY` y `AIDO_ANTHROPIC_MODEL`, y deja tokens/costo en
  `unknown` si el proveedor no devuelve usage/pricing real.
- `OpenAICompatibleProvider` bloquea discovery y chat si
  `AIDO_ENABLE_REAL_PROVIDER_CALLS=false`, si falta base URL o si falta
  credencial. El healthcheck usa `/models` y no marca healthy sin respuesta.
- Los CLI runtimes ya no tienen modo `mock` productivo. La ejecucion pasa por
  argv estructurado, workspace registrado, policy engine, flag
  `AIDO_ENABLE_CLI_RUNTIMES` y sandbox restringido.
- `issue_to_patch` no marca `completed` sin runtime completado, QA real
  pasada, diff no vacio, artifact patch no vacio, evidence package creado y
  contrato de evidence completo.
- La ausencia de runtime ejecutable produce `runtime_unavailable` y
  `qaVerdict=blocked`, no success.
- El scanner productivo bloquea tokens de simulacion en codigo productivo,
  `available=True` hardcoded en rutas de runtime provider y `shell=True` fuera
  de tests permitidos.

## Partial

- Provider readiness esta cerrado como contrato local, no como validacion de
  entorno real. Sin credenciales, endpoint, flag de llamadas reales o CLI
  instalado, el estado correcto sigue siendo `configuration_required`,
  `unavailable` o `blocked`.
- El quality gate completo existe, pero esta reauditoria solo ejecuto
  `quality:productive-truth`, `quality:architecture` y tests enfocados. No hay
  evidencia fresca del pipeline completo con web tests, build, lint, gitleaks y
  Semgrep.
- La evidencia de `issue_to_patch` es completion-grade cuando viene del runner,
  pero la API general `/api/v1/evidence` acepta `qaVerdict=passed` con
  `testResults` o refs y puede generar benchmark outcome si el payload trae
  `providerId/model/usageLedgerId`.
- El catalogo de modelos ya distingue precios desconocidos con `None` en algunos
  proveedores, pero todavia hay seeds con modelos, ventanas de contexto, precios
  y free-tier estaticos etiquetados como `manual_seed` o `staleness unknown`.

## Open

- `local-control-center/web/src/features/model-gateway/ModelGatewayPage.tsx`
  mantiene defaults productivos de benchmark: `success=true`, `qaPass=true`,
  `cost=0.42`, `latency=1200`, y envia `taskId=manual_benchmark_outcome`.
  Esto permite crear metrica de exito/coste/latencia desde UI sin evidencia de
  runtime.
- `local-control-center/web/src/features/model-gateway/BenchmarksPanel.tsx`
  expone checkboxes `Benchmark success` y `Benchmark QA pass`, y el backend
  `POST /api/v1/model-gateway/benchmark-outcomes` acepta esos campos. Los
  benchmarks derivados alimentan el score de routing cuando hay muestra
  suficiente. Esto sigue siendo una superficie de outcome manual, no una prueba
  de benchmark verificado.
- `local_control_center/shared/migrations.py` conserva seeds de catalogo con
  modelos y precios estaticos para `codex_cli:gpt-5.5`,
  `claude_code_cli:sonnet`, `claude_code_cli:opus`, OpenAI-compatible y
  OpenRouter. Sin snapshot oficial o provider discovery real, esos valores no
  deben ser tratados como pricing/tokens reales.
- `local_control_center/agents/pricing_catalog.py` solo marca staleness
  `unknown` cuando `source == "manual_seed"`. Seeds como
  `manual_seed; staleness unknown` pueden quedar clasificados por fecha y
  presentarse como no-unknown. Eso contradice la regla de no costos/tokens
  inventados.

## Needs Real Environment Validation

- Ejecutar healthchecks reales de proveedores remotos con credenciales
  configuradas y `AIDO_ENABLE_REAL_PROVIDER_CALLS=true`.
- Ejecutar discovery real de modelos contra proveedores habilitados y persistir
  solo modelos devueltos por el proveedor.
- Validar Codex CLI, Claude Code, OpenHands y SWE-agent con comando configurado,
  version check, `AIDO_ENABLE_CLI_RUNTIMES=true`, workspace Git worktree y argv
  `issue_to_patch` real.
- Ejecutar un `issue_to_patch` end-to-end con runtime real, diff no vacio, QA
  real y approval segun corresponda.
- Ejecutar `corepack pnpm@10.24.0 run quality` completo si se quiere afirmar
  quality local total de hoy.
- Reemplazar pricing/model seeds estaticos por snapshots con fuente oficial o
  por `unknown` conservador antes de usar coste, tokens o score de routing para
  decision productiva.
- Ejecutar release runtime smoke con opt-in real, sin fallback simulado, y
  guardar evidencia de entorno.

## Decision tecnica

Las remediaciones cerraron el problema principal de `internal_mock` y los falsos
`completed` en `issue_to_patch`. No cerraron todo el contrato no-mock: el area
de benchmark manual y el catalogo seeded de pricing/model metadata todavia
permiten leer exito, coste o tokens como si fueran datos reales. Esos puntos no
deben aparecer como cerrados hasta que el producto deje de aceptar outcomes
manuales como benchmark o los degrade explicitamente a `operator_assertion`, y
hasta que pricing/tokens vengan de snapshots verificables o queden `unknown`.
