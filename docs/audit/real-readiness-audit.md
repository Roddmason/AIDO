# AIDO Real Readiness Audit

Fecha: 2026-06-03

Rama auditada: `dev`

Estado: snapshot inicial pre-remediacion. Las filas de hallazgos describen el
estado encontrado durante la auditoria; los commits posteriores de esta rama
corrigen los P0/P1 operacionales mediante runtime/provider truth, ejecucion
fail-closed, OpenAPI actualizado, UI sin seleccion mock y tests negativos.

Estado post-remediacion legacy, 2026-06-06: `/route/execute-mock` ya no esta
montado como ruta productiva ni expuesto por el cliente OpenAPI generado. Las
referencias restantes en este documento son hallazgos historicos del snapshot
inicial y no describen superficie vigente.

## Alcance

Se inspeccionaron `README.md`, `docs/**`, `local_control_center/**`,
`local-control-center/web/**`, `tests_py/**`, `tests_web/**` y
`local-control-center/scripts/**`.

Patrones buscados: `mock`, `fake`, `dummy`, `sample`, `placeholder`, `demo`,
`internal_mock`, `available: true`, `NotImplemented`, `TODO`, `FIXME`,
`legacy`, `compatibility`, `deprecated`, `hardcoded` y `simulation`.

Regla usada para clasificar: un mock/fake es aceptable solo si vive en tests y
aisla red, filesystem o procesos externos. Si aparece en API, UI, runtime
provider, workflow, agent executor, model gateway, evidence, jobs o adapters, es
riesgo productivo aunque este marcado como simulacion.

## Severidad

- P0: puede mostrar `completed`, respuesta de modelo, benchmark o ejecucion real
  sin ejecucion real.
- P1: puede mostrar disponibilidad, salud, evidencia, coste, routing o UI como
  real sin verificacion suficiente.
- P2: deuda legacy o UX enganosa que degrada lectura operacional.
- P3: documentacion o tooling menor que no cambia estado productivo por si solo.

## Hallazgos accionables

| Modulo | Archivo | Clasificacion | Problema | Impacto | Severidad | Decision tecnica | Prompt/fase |
|---|---|---|---|---|---|---|---|
| Provider Ollama | `local_control_center/agents/providers/ollama.py:14-46` | productivo falso, adapter incompleto | `chat_completion()` devuelve `mock local response` y `rawResponse={"mock": True}` incluso cuando el gateway instancia el provider con `mock=False`. | Una ruta local puede registrar respuesta de modelo sin llamar a Ollama. | P0 | Implementar llamada real a Ollama (`/api/chat` o `/api/generate`) con timeout y error redacted, o marcar el provider `unavailable/non_executable` hasta que exista. | R0 provider fail-closed |
| Provider Anthropic | `local_control_center/agents/providers/anthropic_api.py:9-32` | productivo falso, adapter incompleto | Health/list/chat son stub/mock y `chat_completion()` devuelve `mock anthropic response`. | Si se habilita, el gateway simula una respuesta Anthropic real. | P0 | Implementar Messages API real con credencial obligatoria y healthcheck, o desactivar como no ejecutable. | R0 provider fail-closed |
| Provider OpenAI-compatible | `local_control_center/agents/providers/openai_compatible.py:50-62` | provider sin healthcheck real | Con configuracion presente retorna `healthStatus="healthy"` sin llamada remota real. | Credenciales malas o endpoint caido pueden verse como `available/healthy`. | P1 | Healthcheck real contra `/models`; sin respuesta valida, `available=false` con `reason`. | R1 provider health |
| Model Gateway discovery | `local_control_center/agents/model_gateway_api.py:225-253` | productivo falso | `discover-models` usa modo mock cuando llamadas reales no estan habilitadas y persiste modelos `enabled`. | El catalogo y routing pueden usar modelos no descubiertos realmente. | P1 | No persistir modelos mock en catalogo productivo; si se conservan para tests, deben quedar `simulationOnly/testOnly` y no routables. | R1 catalog truth |
| Model Gateway execute mock | `local_control_center/agents/model_gateway_api.py:344-365` | productivo falso | Endpoint publico `/route/execute-mock` registra uso estimado con `mock=true`. | Clientes pueden depender de ejecucion falsa y contaminar ledger. | P1 | Mover a test-support protegido por flag de test o eliminar del contrato productivo. | R0 API contract |
| CLI runtimes | `local_control_center/agents/cli_runtimes/base.py:58-158` | productivo falso | `self.mock` o `request.mock` retornan `RuntimeResult(status="completed")`. | Puede existir `completed` sin proceso externo real. | P0 | Eliminar `mock` del DTO productivo; mantener dobles solo en tests por monkeypatch/inyeccion interna. | R0 runtime execution |
| Agent API default | `local_control_center/agents/api.py:71` | UI/API enganosa, workflow incompleto | Ausencia de runtime cae en `internal_mock`. | Configuracion faltante no bloquea por defecto; cae en simulacion visible. | P1 | Runtime requerido o default `manual/unconfigured`; `internal_mock` no debe ser fallback productivo. | R0 agent contract |
| Agent execution status | `local_control_center/agents/api.py:317-386` | productivo falso | Policy-allowed pero no ejecutado puede terminar `status="completed"`. | Confunde aprobacion de politica con ejecucion real. | P1 | Crear estado explicito `policy_allowed_not_executed` o `blocked`; `completed` solo con ejecucion y evidencia. | R0 agent status |
| Agent repository | `local_control_center/agents/repository.py:339-394` | productivo falso mitigado parcialmente | El repositorio degrada `internal_mock` a `simulation_only`, pero todavia registra tool/model call `internal_mock` y conserva nombres `complete`. | La simulacion sigue pareciendo ejecucion en superficies de auditoria. | P1 | Separar telemetry de test-support de ledger/evidence productivo; no usar `complete` para simulacion. | R1 evidence semantics |
| Runtime providers | `local_control_center/agents/runtime_status.py:13-218` | UI/API enganosa | `internal_mock` es parte del catalogo publico de runtimes y `available=true` aunque sea test/simulation-only. | El usuario ve un provider disponible que nunca debe completar trabajo real. | P2 | Si permanece, debe vivir bajo test-support o aparecer como `configuration_required/simulation_only`, no como runtime disponible productivo. | R1 provider contract |
| Runtime seeds | `local_control_center/shared/migrations.py:655,1218-1244` | productivo falso, hardcoded availability | Seeds insertan `internal_mock` y `manual` como healthy/enabled, con modelo mock y precio 0. | El estado inicial contiene availability y coste hardcoded. | P1 | Seeds solo declaran capacidad conocida; health, enabled y pricing real se calculan por checks. | R1 migrations |
| Model router | `local_control_center/agents/model_router.py:254-357` | provider sin healthcheck real | Permite provider `unknown` con score de confiabilidad 0.65. | Routing puede seleccionar proveedor no verificado. | P1 | En modo productivo, `unknown` debe bloquear o pedir configuracion; solo preview puede listar candidatos no verificados. | R1 routing |
| Pricing unknown | `local_control_center/agents/providers/nvidia_nim.py` y seeds en `shared/migrations.py` | hardcoded cost | Coste desconocido se modela como 0/free-tier. | Presupuestos y aprobaciones pueden tratar unknown como gratis. | P1 | `unknown` no es `0`; requiere snapshot de pricing o aprobacion conservadora. | R1 cost policy |
| Evidence API | `local_control_center/evidence/api.py:237-311` | workflow incompleto | `qaVerdict=passed` se acepta con test results, diff refs o screenshots; luego alimenta benchmark. | Evidencia manual/incompleta puede convertirse en exito de modelo. | P1 | `passed` productivo requiere QA normalizada, diff/artifacts con hashes y linkage de runtime/job/agent. | R0 evidence gate |
| Model benchmarks | `local_control_center/agents/model_benchmarks.py:198-239` | productivo falso | Convierte evidencia con `qaVerdict` en outcome de benchmark sin verificar ejecucion real/usage real. | Routing aprende exito de fuentes no confiables. | P1 | Separar `operator_assertion` de `verified_benchmark`; benchmark requiere evidencia completa y ejecucion real. | R1 benchmark integrity |
| Issue to Patch runner | `local_control_center/workflows/issue_to_patch_runner.py:211-560` | workflow incompleto | El runner ya bloquea `simulation_only`, pero aun depende de runtime status/CLI gateway que permiten mocks y `completed` falsos aguas arriba. | La garantia de `completed` queda rota si el runtime reporta falso positivo. | P1 | Mantener gates y endurecer inputs: runtime executable real, workspace aislado, artifacts, QA y approval resuelto. | R0 workflow gate |
| Command Center UI | `local-control-center/web/src/features/pages.tsx:65-258` | UI enganosa | Modo `simulationMode` convive con copia de runtime real; los badges dependen del provider seleccionado, pero no bloquean todos los caminos test-only. | El operador puede lanzar un flujo que parece real pero es simulacion. | P1 | UX separada para dry-run/test; submit productivo deshabilitado si runtime no es real executable. | R2 frontend truth |
| Legacy Model Gateway UI | `local-control-center/web/src/features/pages.tsx:632-700` | deuda legacy, UI enganosa | Vista legacy conserva `internal_mock/mock` como defaults. | Superficie duplicada puede reintroducir politicas mock. | P2 | Eliminar export legacy o dejarla inaccesible con test de contrato de rutas. | R2 frontend cleanup |
| Model Gateway UI defaults | `local-control-center/web/src/features/model-gateway/ModelGatewayPage.tsx:124-230` | UI enganosa | Formulario de policy arranca en `internal_mock/mock`. | Crea politicas hacia proveedor simulado por defecto. | P1 | Sin default productivo; seleccionar solo providers reales descubiertos o mostrar `configuration_required`. | R2 frontend truth |
| Manual benchmark UI | `local-control-center/web/src/features/model-gateway/ModelGatewayPage.tsx:362-367` | productivo falso | Registra `manual_benchmark_outcome` desde UI. | Fabrica metricas de benchmark sin evidencia normalizada. | P0 | Convertir a comentario/observacion no-verificada o eliminar; benchmarks solo backend verificado. | R0 benchmark integrity |
| Gateway metrics UI | `local-control-center/web/src/features/model-gateway/ModelGatewayPage.tsx:399-422` | UI enganosa | Muestra `healthy/available/executable` sin diferenciar unknown, test-only y simulation-only en las metricas superiores. | Lectura operacional demasiado optimista. | P2 | Metricas separadas: production-ready, configured-only, unavailable, test/simulation-only. | R2 frontend truth |
| Agents UI | `local-control-center/web/src/features/agents/AgentsPage.tsx:8-55,238-266` | UI enganosa | Opciones/fallbacks incluyen `internal_mock`; runtime de tabla cae en `internal_mock`. | Perfiles sin runtime pueden parecer asignados a mock. | P1 | Sin fallback mock; runtime requerido o `unassigned/configuration_required`. | R2 frontend truth |
| App status strip | `local-control-center/web/src/app/App.tsx:570-573` | UI enganosa, hardcoded success | Badges `SQLite canonical` y `Policy engine active` aparecen como OK estatico. | Estado global muestra salud no verificada. | P1 | Derivar de endpoint de health real o mostrar `unknown/not checked`. | R2 frontend health |
| Evidence UI | `local-control-center/web/src/features/pages.tsx:509-565` | UI enganosa | La vista presenta artifacts/evidence sin un estado claro de completitud/verificacion. | Evidencia incompleta puede verse equivalente a evidencia productiva. | P2 | Mostrar completeness, QA source, artifact hash, runtime/job/approval linkage e incomplete state. | R2 evidence UI |
| Workflows UI | `local-control-center/web/src/features/workflows/WorkflowsPage.tsx:39-89` | workflow incompleto | La vista depende de overview y no deja suficientemente claro el linkage runtime/job/evidence/approval para el workflow seleccionado. | Dificulta probar que `completed` fue real. | P2 | Scope por workflow seleccionado y grafo desde relaciones backend, no inferencias visuales. | R2 workflow UI |
| Release smoke | `local-control-center/scripts/smoke-runtime-adapters.ps1:232-374` | productivo falso | Smoke siempre ejecuta `internal_mock` y marca `completed=$true` para el script. | Puede usarse como evidencia falsa de runtime readiness. | P1 | Separar smoke interno de release; release smoke debe fallar sin runtime real opt-in y evidencia verificada. | R1 DevOps |
| Docs Model Gateway | `docs/model-gateway.md:24-73` | documentacion obsoleta | Documenta `/route/execute-mock` y health/discovery mock-first. | Contradice la regla de runtime real. | P1 | Marcar como superseded y actualizar contrato fail-closed. | R3 docs |
| Docs Provider Accounts | `docs/provider-accounts.md:46-60` | documentacion obsoleta | Presenta mock-default como comportamiento esperado. | Normaliza un estado prohibido para producto. | P1 | Reescribir como real-health-by-default; mocks solo tests. | R3 docs |
| Gap audit historico | `docs/model-runtime-gap-audit.md:95-140` | documentacion obsoleta, deuda legacy | Plan historico pide adapters mock/stub, route preview mock y legacy endpoints. | Puede guiar futuras iteraciones hacia arquitectura prohibida. | P2 | Mover a archivo historico o anteponer nota `superseded by real-readiness audit`. | R3 docs |
| Current state docs | `docs/aido-current-state-analysis.md`, `docs/aido-gap-analysis.md`, `docs/aido-implementation-plan.md` | documentacion obsoleta | Mezclan snapshot antiguo, placeholders y planes previos. | Dificulta distinguir estado real actual de backlog. | P2 | Normalizar como snapshots fechados y enlazar a este audit como fuente actual. | R3 docs |
| API generated | `local-control-center/web/src/api/generated/openapi.ts:320,712` | contrato publico obsoleto | OpenAPI expone `/route/execute-mock`. | El cliente generado conserva superficie mock. | P1 | Regenerar despues de retirar endpoint productivo. | R1 API contract |
| Hard-coded compatibility | `docs/api-contract-matrix.md`, `docs/model-gateway.md`, `docs/usage-ledger.md` | deuda legacy | Varias fuentes dicen que no hay compat routes activas, pero `model_policies`, `model_calls` y `cost_usage` siguen descritos como legacy/read-model. | Ambiguedad de contrato publico. | P2 | Declarar cuales tablas/endpoints son canonicos, read-model o removidos. | R3 docs/API |

## Mocks y fakes legitimos de test

Estos hallazgos son aceptables si permanecen en tests y no cruzan hacia API/UI:

| Archivo | Uso | Decision |
|---|---|---|
| `tests_py/test_aido_real_runtime_slice.py:89-92` | `FakePatchRuntime` aisla ejecucion externa. | Mantener como doble unitario; no exportar al runtime registry productivo. |
| `tests_py/test_phase3_to_6_control_plane_runtime.py:424-1046` | `fake_docker_execute` aisla Docker/sandbox. | Mantener como monkeypatch de test. |
| `tests_py/test_phase3_to_6_control_plane_runtime.py:1203-1210` | `fake_run` aisla subprocess. | Mantener como monkeypatch de test. |
| `tests_py/test_model_runtime_gateway.py:276-379,1081-1099` | HTTP/model list fake para aislar red. | Mantener, pero cambiar asserts que esperan mock productivo. |
| `tests_py/test_observability_telemetry.py:234-262` | `FakeExporter` aisla exportador externo. | Mantener como test-only. |
| `tests_web/control-center.spec.js:246-981` | Fixtures Playwright para UI. | Mantener como fixtures, pero actualizar expectativas que normalizan `internal_mock` productivo. |

## Tests que normalizan comportamiento prohibido

| Archivo | Problema | Decision |
|---|---|---|
| `tests_py/test_model_runtime_gateway.py` | Hay tests que esperan discovery mock-default y `/route/execute-mock`. | Reescribir como tests negativos: mocks no se exponen productivamente. |
| `tests_py/test_phase3_to_6_control_plane_runtime.py` | Tests historicos verifican ledger/model calls de `internal_mock`. | Mantener solo si el estado final es `simulation_only` y no benchmark/productive evidence. |
| `tests_web/control-center.spec.js` | Varias expectativas exigen ver `internal_mock` en UI productiva. | Reubicar a test-support UI o cambiar a `simulation-only/test-only` no seleccionable. |
| `tests_py/test_optional_smoke_profiles.py` | Verifica contenido de smoke con `internal_mock`. | Cambiar a smoke interno no-release o exigir opt-in. |

## Test arquitectonico agregado

Se agrego `tests_py/test_real_readiness_architecture.py`.

El test recorre codigo productivo en:

- `local_control_center/**`
- `local-control-center/web/src/**`

Excluye:

- `local-control-center/web/src/api/generated/**` porque refleja OpenAPI generado.
- `local_control_center/i18n/**` porque contiene catalogo de textos traducibles.

Patrones que falla fuera de tests:

- `mock`
- `fake`
- `dummy`
- `demo`
- `internal_mock`
- `available: true`

En el estado auditado, este test debia fallar. Despues de aplicar las
correcciones, debe pasar y servir como guardia contra nuevos mocks productivos.

## Fases de correccion recomendadas

| Fase | Objetivo | Criterio de salida |
|---|---|---|
| R0 runtime truth | Eliminar `completed` falso en providers, CLI runtimes, agents, benchmarks y `/execute-mock`. | Ningun camino productivo puede registrar `completed`, respuesta de modelo o benchmark sin runtime/evidencia/QA reales. |
| R1 provider and catalog truth | Health real, catalogo no mock, pricing unknown conservador y seeds sin disponibilidad hardcoded. | `/api/v1/runtime/providers` distingue `configured`, `available`, `executable`, `testOnly`, `simulationOnly` y `reason` con fuentes reales. |
| R2 frontend truth | UI no selecciona ni promociona mocks; estados globales vienen de health real. | Command Center, Agents, Model Gateway, Workflows y Evidence muestran `unavailable/configuration_required/blocked` con reason. |
| R3 docs and contracts | Documentacion y OpenAPI dejan de recomendar mocks productivos. | Docs declaran contrato real-first/fail-closed y marcan snapshots historicos como superseded. |

## Decision tecnica

El snapshot auditado ya habia avanzado hacia metadatos de runtime, pero todavia
conservaba mocks como superficie publica. Bajo la regla de este audit, eso no
era una deuda cosmetica: era un fallo de contrato operacional. La correccion
correcta no era renombrar `internal_mock`, sino retirarlo de las superficies
productivas y hacer que toda ausencia de credencial, CLI, endpoint, binario,
workspace, permisos o configuracion sea `unavailable`,
`configuration_required` o `blocked`.
