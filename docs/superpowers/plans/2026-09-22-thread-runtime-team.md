# Thread Runtime Team Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el operador elija por hilo qué runtimes de IA participan y cuál cumple cada rol (PO, Developer, Arquitecto, Seguridad), que solo sean seleccionables los runtimes con una prueba real de ida y vuelta de los últimos 30 minutos, y que el product loop del hilo quede confinado a ese conjunto sin failover silencioso fuera de él.

**Architecture:** Paquete nuevo `local_control_center/runtime_team/` con cinco capas finas: `roles.py` (elegibilidad y reparto puros), `validation.py` (frescura de solo lectura sobre `model_execution_health`), `probe.py` (prueba real reutilizando `_run_provider_test_prompt` y `validate_cli_candidate`), `facts.py`/`configuration.py` (equipo guardado en `project_threads.metadata.runConfiguration`, sellado en la metadata del run como `runtimeTeam`) y `candidates.py` (lectura para el panel). El loop consume el equipo sellado vía `AIResourceRequest.allowed_provider_ids` en los 4 sitios de selección, pasa `preferredRuntime` al Arquitecto y bloquea en `runtime_check` con la remediación `revalidate_runtime` si un runtime perdió su validación. La UI agrega un chip en el composer que abre un `Drawer` con checkboxes, grilla rol → runtime precargada con el reparto calculado por el backend y filas fijas para los roles deterministas.

**Tech Stack:** Python 3.11 · FastAPI ≥0.136 · Pydantic v2 · SQLite (autocommit) · React 18 · TypeScript 6 · Vite 6 · Biome 2.5 · Playwright 1.60 · lucide-react.

**Spec:** `docs/superpowers/specs/2026-09-22-thread-runtime-team-design.md`

## Global Constraints

- Tests Python desde la raíz del repo: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['<test files>','-q','-p','no:randomly']))"` (workaround del access violation de faiss).
- Ruff: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format <py files>` y `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check <py files>` (nunca sobre `.json`).
- Web: `corepack pnpm@10.24.0 run typecheck:web`, `corepack pnpm@10.24.0 exec biome check <files>`, `corepack pnpm@10.24.0 run build:web`.
- Regenerar OpenAPI tras cualquier cambio de API/response_model: `corepack pnpm@10.24.0 run openapi:generate` (LF).
- Todo módulo productivo nuevo lleva header semántico (docstring/JSDoc) con `@author Rodrigo Mason`.
- Python productivo: solo docstrings (sin comentarios `#` sueltos); docstrings en API pública (ruff D101-D103).
- Copy de UI solo vía `t('key','English fallback')` con claves bilingües (en != es) en `local_control_center/i18n/default_catalog.json`, editado quirúrgicamente preservando el formato.
- Guardrails visuales: nada de Inter/cyan/purple/radial-gradient/`border-left|right` ≥ 2px; tokens oklch.
- Nuevos kinds de artifact / roles de agente / Literals de contrato ⇒ Literal + regen (drift de response_model).
- Finales de línea LF (el Edit en Windows puede introducir CRLF: verificar que `git diff` no advierta "CRLF will be replaced").
- Formato de commit `Tipo (Ámbito): mensaje en español` + trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage solo de rutas explícitas; nunca `--no-verify`.
- Specs Playwright se ejecutan directo con `PLAYWRIGHT_DASHBOARD_PORT` fijo y build fresco (`run-web-tests.mjs` ignora argumentos).

---

## Decisiones tomadas sobre el spec (evidencia)

1. **Solo PO y Developer son obligatorios; Arquitecto y Seguridad son opcionales (Architect Decision 1, 2026-09-22; spec §3.4/§3.6).** `REQUIRED_TEAM_ROLES = ("product_owner", "developer")` y `OPTIONAL_TEAM_ROLES = ("architect", "security")`. Solo un rol obligatorio sin runtime elegible bloquea el envío (422) y el gate de ejecución. Un rol opcional sin elegibles queda sin asignar: Arquitecto sin asignar ⇒ `ArchitectAgent` no corre en el hilo (review `skipped` con causa); Seguridad sin asignar ⇒ la fase de seguridad corre solo sus scanners deterministas (gitleaks, semgrep, chequeos locales) sin `runModelAnalysis` (`phases/security.py:128-133`). Consecuencia: un equipo de un solo runtime elegible para PO y Developer (p. ej. un solo CLI) puede enviar ("si selecciono 1 hace todo"), aunque Seguridad solo ejecute runtimes de modelo (`security_agent_contract.py:97-111`) y el Arquitecto solo acepte `claude_code_cli` como CLI (`architect_agent_contract.py:21,126-139`). Las ramas de Task 8 (Arquitecto `skipped`, Seguridad sin análisis) pasan a ser el camino normal de un rol opcional sin asignar y nunca caen a un runtime fuera del conjunto.
2. **Elegibilidad = predicado real del runner ∧ capacidad que exige el rol del scheduler.** `eligible_team_roles` llama a `is_product_owner_runtime` (`product_owner_agent_contract.py:337`), `is_developer_runtime` (`developer_agent_contract.py:94`), `is_architect_runtime` (`architect_agent_contract.py:126`) e `is_security_model_runtime` (`security_agent_contract.py:97`) sobre el estado del runtime con la ejecutabilidad forzada (la ejecutabilidad la prueba `models.validate_runtime`, no la elegibilidad). Encima exige lo que pide `AIResourceManager` para el rol: Developer de modelo necesita `code_edit`/`issue_to_patch`/`code` (→ `code`, `ai_resource_manager.py:1042-1043`; `_resource_required_capabilities`, `product_loop/coordinator.py:2516-2524`) y Seguridad necesita `chat` (predicado del runner) **y** `code_review`/`review` (→ `review`, `ai_resource_manager.py:1044-1045`; Architect Decision 2, reflejada en la tabla del spec §3.3). Así el panel nunca ofrece un runtime que el runner o el gestor rechazarían (p. ej. Developer CLI sin `code_edit`, o Seguridad sin `chat`).
3. **Sin espejo del reparto en la UI.** `GET /api/v1/runtime/team-candidates?selected=` devuelve `suggestedRoleRuntimes` calculado en backend; una sola fuente de verdad (el repo no tiene runner de tests TS para vigilar un espejo).
4. **`running` es solo de UI.** Los inputs de operaciones encoladas quedan sellados y opacos (`executions/runner.py:52-54` lee `sealedInput`), así que el backend no puede consultar "prueba en curso por proveedor"; el panel marca `running` mientras espera `requestCompletedOperation`.
5. **Dos ventanas:** al sellar el mensaje se exige 30 min (spec §3.4); al ejecutar (`runtime_check`) se exige la validación de 24 h + fingerprint (`VALIDATION_TTL_SECONDS`, `model_execution_health.py:18`), porque un run puede esperar en cola más de 30 min legítimamente. Ambos usan el mismo predicado con distinta ventana.
6. **El PATCH no exige frescura**, solo existencia, habilitación, pertenencia y elegibilidad; la frescura la re-verifica el sellado (spec §3.4).
7. **`revalidate_runtime` encola una operación `models.validate_runtime` por runtime vencido** vía `enqueue_registered_operation` (`executions/router.py:112-157`, precedente `agents/runtime_health_refresh.py:153-177`), así `operation_workload` (`executions/workloads.py:24-80`) clasifica cada proveedor (CLI → `agent_cli`, loopback → `local_gpu_model`, API → `remote_llm_light`). La remediación queda `pending` con estado `validating`; cuando el handler de `models.validate_runtime` termina `validated`, llama a `BlockerRemediationService.resume_after_runtime_validation(provider_id)`, que re-ejecuta la remediación cuando todos sus runtimes ya tienen validación vigente y ésta reanuda el `retry_loop` pendiente. `remediations.execute` de `revalidate_runtime` solo encola, por eso reserva `qa_light` como `validate_runtime`.
8. **Roles del scheduler que no son del equipo** (aido_lead, technical_lead, qa_engineer, researcher, etc.) quedan confinados al conjunto completo del hilo, no a un runtime único.
9. **Dos alcances de readiness:** el gate de envío revisa todos los runtimes seleccionados (spec §3.4, "re-verifica cada runtime"); el gate de ejecución revisa solo los runtimes con rol asignado (spec §1.4/§3.5, "runtime asignado"). Un runtime seleccionado sin rol que venza no detiene el loop: si un rol no-equipo lo pidiera, `AIResourceManager` ya lo descarta por la validación de 24 h (`ai_resource_manager.py:509`).
10. **Atomicidad:** el PATCH revisa estado, escribe y registra el evento dentro de un `immediate_transaction` (`shared/db.py:74`); `post_message` evalúa el gate de equipo como primera sentencia de su `immediate_transaction` (`threads/coordinator.py:152`), la misma transacción donde ya se sella el run (`threads/coordinator.py:293,928`). `BEGIN IMMEDIATE` serializa ambos escritores, así un PATCH no puede colarse entre el gate y el sellado, y los read-modify-write de `runConfiguration` (`product_loop/metadata.py:50-64`) no se pisan.
11. **Runtimes denegados por la política del proyecto (Architect Decision 3).** `load_runtime_facts` evalúa `RuntimeConfigRepository.runtime_policy_decision` (`runtime_integrations/repository.py:186`) por runtime y proyecto y lo guarda en `RuntimeFacts.policy_denied_reason`. `team-candidates` lo devuelve como `validation.status = "policy_denied"` con la causa, lo excluye del reparto sugerido, y `write_thread_runtime_team` (PATCH) lo rechaza con 422: nunca es seleccionable en ese proyecto aunque tenga una validación vigente de otro proyecto.
12. **`deferred` no es evidencia.** Una prueba no intentada (sin proyecto, preflight diferido o admisión de recursos) no se persiste en `model_execution_health` porque no dice nada del runtime y persistirla como falla invalidaría una validación vigente. Se hace visible en el panel: la fila conserva el último resultado de su prueba (`deferred`/`failed` + causa) y, mientras la ejecución espera en `resource_wait`, muestra la razón del governor (`ExecutionResponse.reason`, `executions/models.py:36-48`).

## File Map

| Archivo | Acción | Responsabilidad única |
|---|---|---|
| `local_control_center/runtime_team/__init__.py` | Crear | Header del paquete. |
| `local_control_center/runtime_team/roles.py` | Crear | Roles del equipo, elegibilidad por contrato, mapeo rol-scheduler → rol-equipo, reparto automático puro. |
| `local_control_center/runtime_team/validation.py` | Crear | Estado de validación por runtime (validated/stale/failed/never) y predicado `runtime_validated_within`. |
| `local_control_center/runtime_team/probe.py` | Crear | `RuntimeValidationService`: prueba real API/local/CLI, auditoría de aprobación, evidencia de fallo. |
| `local_control_center/runtime_team/contracts.py` | Crear | Modelos Pydantic de validación, candidatos y `RoleRuntimesRecord`. |
| `local_control_center/runtime_team/facts.py` | Crear | Hechos por runtime habilitado (tipo, etiqueta, roles elegibles) desde cuentas + estado de runtime. |
| `local_control_center/runtime_team/configuration.py` | Crear | Lectura/escritura del equipo del hilo, estrechamiento por proyecto, readiness, sellado y helpers de `request_meta`. |
| `local_control_center/runtime_team/candidates.py` | Crear | Servicio de lectura de candidatos + reparto sugerido. |
| `local_control_center/agents/provider_catalog.py` | Modificar | Entrada `llama_cpp`. |
| `local_control_center/agents/provider_catalog_api.py` | Modificar | Sembrar `chat/code_edit/code_review` también para `llama_cpp`. |
| `local_control_center/agents/model_gateway_api.py` | Modificar | Ruta `POST /providers/{provider_id}/validate-runtime`; tras un `validated` reanuda los loops bloqueados por ese runtime. |
| `local_control_center/executions/workloads.py` | Modificar | `models.validate_runtime` en `INFERENCE_OPERATIONS` (clase por runtime); `revalidate_runtime` reserva `qa_light` porque solo encola. |
| `local_control_center/agents/api.py` | Modificar | Ruta `GET /api/v1/runtime/team-candidates`. |
| `local_control_center/threads/contracts.py` | Modificar | Request/response del PATCH run-configuration. |
| `local_control_center/threads/api.py` | Modificar | Ruta `PATCH /api/v1/threads/{thread_id}/run-configuration`: estado, escritura y evento en una sola transacción. |
| `local_control_center/threads/coordinator.py` | Modificar | Gate de envío (422) dentro de la transacción del mensaje y `runtimeTeam` como clave protegida. |
| `local_control_center/product_loop/metadata.py` | Modificar | El sellado estampa el equipo efectivo. |
| `local_control_center/product_loop/coordinator.py` | Modificar | Allowlist en equipo/PO/failover; developer/seguridad/arquitecto respetan la asignación. |
| `local_control_center/product_loop/phases/execution.py` | Modificar | Pasa `run.request_meta` al recurso del developer. |
| `local_control_center/product_loop/phases/security.py` | Modificar | Pasa `run.request_meta` al recurso de seguridad. |
| `local_control_center/product_loop/phases/environment.py` | Modificar | Gate de ejecución `runtime_team` en `runtime_check`. |
| `local_control_center/remediations/contracts.py` | Modificar | Blocker `runtime_team_validation_expired` y acción `revalidate_runtime`. |
| `local_control_center/remediations/payloads.py` | Modificar | Specs de remediación del nuevo blocker. |
| `local_control_center/remediations/service.py` | Modificar | Clasificación de etapa, `revalidate_runtime` (encola una validación por runtime) y reanudación del retry tras validar. |
| `local_control_center/i18n/default_catalog.json` | Modificar | Claves bilingües (llama.cpp, remediación, panel). |
| `local-control-center/web/src/api/generated/openapi.ts` | Regenerar | Cliente generado. |
| `local-control-center/web/src/features/runtime-setup/runtimeSetup.ts` | Modificar | Tarjeta llama.cpp en el catálogo de setup. |
| `local-control-center/web/src/features/shell/remediationPresentation.ts` | Modificar | Copy del blocker y de la acción nueva. |
| `local-control-center/web/src/api/execution-client.ts` | Modificar | Callback opcional por observación de la ejecución encolada (estado `resource_wait` + razón del governor). |
| `local-control-center/web/src/api/client.ts` | Modificar | Llamadas tipadas: candidatos, validate-runtime (con observación), run-configuration. |
| `local-control-center/web/src/features/runtime-team/runtimeTeamModel.ts` | Crear | Tipos y helpers puros del equipo (lectura de metadata, merge de roles automáticos vs manuales, tono). |
| `local-control-center/web/src/features/runtime-team/useRuntimeTeamCandidates.ts` | Crear | Hook de carga de candidatos con abort. |
| `local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx` | Crear | Cuerpo del drawer: lista, prueba, grilla de roles, guardado. |
| `local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx` | Crear | Chip del composer + host del drawer. |
| `local-control-center/web/src/design-system/layout.css` | Modificar | Estilos `.runtime-team-*`. |
| `local-control-center/web/src/features/shell/ThreadConversation.tsx` | Modificar | Slot `teamControl` en el composer; PATCH antes del primer mensaje. |
| `tests_py/test_runtime_team_roles.py` | Crear | Tests de elegibilidad y reparto. |
| `tests_py/test_runtime_team_validation.py` | Crear | Tests de frescura y de la prueba real. |
| `tests_py/test_llama_cpp_provider.py` | Crear | Tests del catálogo llama.cpp. |
| `tests_py/test_runtime_team_validate_api.py` | Crear | Tests de la operación encolada. |
| `tests_py/test_runtime_team_configuration.py` | Crear | Tests de guardado, sellado y readiness. |
| `tests_py/test_runtime_team_candidates_api.py` | Crear | Tests del endpoint de candidatos. |
| `tests_py/test_runtime_team_thread_api.py` | Crear | Tests del PATCH, gate de envío y sellado. |
| `tests_py/test_runtime_team_loop_enforcement.py` | Crear | Tests de allowlist en el loop. |
| `tests_py/test_runtime_team_execution_gate.py` | Crear | Tests del gate de ejecución y la remediación. |
| `tests_web/thread-runtime-team.spec.js` | Crear | Spec Playwright del panel. |

## Task Graph

| Task | Depende de | Complejidad | Puntos de serialización |
|---|---|---|---|
| 1 Roles y reparto | — | PATTERN | — |
| 2 Frescura + prueba real | 1 | HIGH | — |
| 3 llama.cpp | — | MECHANICAL | `default_catalog.json` |
| 4 Endpoint validate-runtime | 2 | PATTERN | `openapi.ts` |
| 5 Configuración del hilo | 1, 2 | HIGH | — |
| 6 Endpoint candidatos | 4, 5 | PATTERN | `openapi.ts`, `runtime_team/contracts.py` |
| 7 PATCH + sellado + gate de envío | 5, 6 | HIGH | `openapi.ts` |
| 8 Enforcement en el loop | 5 | HIGH | `product_loop/coordinator.py` |
| 9 Gate de ejecución + remediación | 2, 4, 5, 7 | HIGH | `openapi.ts`, `default_catalog.json`, `model_gateway_api.py`, `workloads.py` |
| 10 Cliente web + modelo | 4, 6, 7, 9 | MECHANICAL | — |
| 11 Panel + composer | 10 | HIGH | `default_catalog.json`, `ThreadConversation.tsx` |
| 12 Spec Playwright | 11 | PATTERN | — |
| 13 Verificación final | 1–12 | MECHANICAL | — |

**Parallel lanes (conjuntos de archivos disjuntos):**

- **Ola 1:** Task 1 ∥ Task 3.
- **Ola 2:** Task 2.
- **Ola 3:** Task 4 ∥ Task 5.
- **Ola 4:** Task 6 ∥ Task 8 (Task 8 sigue en su carril mientras avanza la cadena de regen).
- **Ola 5:** Task 7 (puede solaparse con Task 8: archivos disjuntos).
- **Ola 6:** Task 9.
- **Ola 7:** Task 10 → Task 11 → Task 12.
- **Ola 8:** Task 13.

Carril "API/regen" (serial, un solo agente a la vez sobre `openapi.ts`): 4 → 6 → 7 → 9. Carril "loop": 5 → 8. Carril "catálogo": 3. Carril "web": 10 → 11 → 12.

Architect Decisions (2026-09-22) no agregan tasks ni dependencias: roles obligatorios/opcionales viven en Task 1 y se propagan por las aristas existentes 1 → 5 → 9 y 6 → 10 → 11 → 12; `policy_denied` nace en Task 5 (`facts.py`, `RuntimeFacts.policy_denied_reason` de Task 1) y llega a la UI por 5 → 6 → 10 → 11 (el Literal de Task 6 cambia el `openapi.ts` que ya regenera ese task).

---

### Task 1: Roles del equipo, elegibilidad y reparto automático

**Files:**
- Create: `local_control_center/runtime_team/__init__.py`
- Create: `local_control_center/runtime_team/roles.py`
- Test: `tests_py/test_runtime_team_roles.py`

**Interfaces:**
- Consumes: `is_product_owner_runtime` (`agents/product_owner_agent_contract.py:337`), `is_developer_runtime`, `DEVELOPER_AGENT_CLI_RUNTIMES` (`agents/developer_agent_contract.py:94,23`), `is_architect_runtime` (`agents/architect_agent_contract.py:126`), `is_security_model_runtime` (`agents/security_agent_contract.py:97`). Todos reciben el dict de estado de runtime (`id`, `providerFamily`, `capabilities`, `models`, flags de ejecutabilidad).
- Produces: `TEAM_ROLES: tuple[str, ...]`, `REQUIRED_TEAM_ROLES = ("product_owner", "developer")`, `OPTIONAL_TEAM_ROLES = ("architect", "security")`, `ASSIGNMENT_ORDER`, `RuntimeFacts(provider_id: str, label: str, kind: str, eligible_roles: tuple[str, ...], policy_denied_reason: str | None = None)`, `eligible_team_roles(runtime_status: Mapping[str, Any]) -> tuple[str, ...]`, `team_role_for(role: str, *, kind: str = "", capabilities: Iterable[str] = ()) -> str | None`, `auto_assign_roles(runtimes: Sequence[RuntimeFacts], runtime_order: Sequence[str] = ()) -> dict[str, str]`, `missing_required_roles(role_runtimes: Mapping[str, str]) -> list[str]`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_roles.py`:

```python
"""Elegibilidad por rol y reparto automático del equipo de runtimes por hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

import pytest

from local_control_center.runtime_team.roles import (
    OPTIONAL_TEAM_ROLES,
    REQUIRED_TEAM_ROLES,
    TEAM_ROLES,
    RuntimeFacts,
    auto_assign_roles,
    eligible_team_roles,
    missing_required_roles,
    team_role_for,
)

CLAUDE = RuntimeFacts("claude_code_cli", "Claude Code CLI", "cli", ("product_owner", "developer", "architect"))
CODEX = RuntimeFacts("codex_cli", "Codex CLI", "cli", ("product_owner", "developer"))
OMNIROUTE = RuntimeFacts(
    "omniroute", "OmniRoute", "gateway", ("product_owner", "developer", "architect", "security")
)
OLLAMA = RuntimeFacts("ollama", "Ollama", "local", ("product_owner", "architect", "security"))
CLI_CAPABILITIES = ["chat", "code_edit", "issue_to_patch", "review"]


def _status(runtime_id: str, family: str | None, capabilities: list[str], **extra) -> dict:
    return {"id": runtime_id, "providerFamily": family, "capabilities": capabilities, **extra}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_status("claude_code_cli", None, CLI_CAPABILITIES), ("product_owner", "developer", "architect")),
        (_status("codex_cli", None, CLI_CAPABILITIES), ("product_owner", "developer")),
        (
            _status("omniroute", "openai_compatible", ["chat", "code_edit", "code_review"]),
            ("product_owner", "developer", "architect", "security"),
        ),
        (
            _status("ollama", "ollama", ["chat", "local", "private", "streaming"], models=["local_default"]),
            ("product_owner", "architect"),
        ),
        (
            _status("ollama", "ollama", ["chat", "code_review"], models=["local_default"]),
            ("product_owner", "architect", "security"),
        ),
        (_status("ollama", "ollama", ["chat", "code_review"], models=[]), ()),
        (_status("gemini", "gemini", ["chat", "json", "tools"]), ("architect",)),
        (_status("openhands", "openhands", ["chat"]), ()),
    ],
)
def test_eligibility_follows_each_runner_and_the_scheduler_capability(status, expected):
    assert eligible_team_roles(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_status("codex_cli", None, ["chat", "issue_to_patch"]), ("product_owner",)),
        (_status("claude_code_cli", None, ["code_edit"]), ("developer",)),
        (_status("custom_gateway", "openai_compatible", ["code_edit", "code_review"]), ()),
        (_status("custom_gateway", "openai_compatible", ["chat", "review"]), ("product_owner", "architect", "security")),
    ],
)
def test_runtimes_the_runners_reject_are_never_eligible(status, expected):
    assert eligible_team_roles(status) == expected


@pytest.mark.parametrize(
    ("role", "kind", "capabilities", "expected"),
    [
        ("product_owner", "reason", ["product_discovery"], "product_owner"),
        ("architect", "reason", ["system_design"], "architect"),
        ("developer", "", [], "developer"),
        ("backend_engineer", "build", ["code_edit"], "developer"),
        ("database_engineer", "build", ["schema_design"], "developer"),
        ("security_engineer", "review", ["security_review"], "security"),
        ("qa_engineer", "review", ["test_design"], None),
        ("technical_lead", "reason", ["code_review"], None),
    ],
)
def test_scheduler_roles_map_to_team_roles(role, kind, capabilities, expected):
    assert team_role_for(role, kind=kind, capabilities=capabilities) == expected


def test_a_single_runtime_takes_every_role_it_can_fill():
    assert auto_assign_roles([CLAUDE]) == {
        "developer": "claude_code_cli",
        "product_owner": "claude_code_cli",
        "architect": "claude_code_cli",
    }


def test_two_runtimes_put_the_cli_on_development_and_spread_the_rest():
    assert auto_assign_roles([OMNIROUTE, CODEX]) == {
        "developer": "codex_cli",
        "product_owner": "omniroute",
        "architect": "omniroute",
        "security": "omniroute",
    }


def test_four_runtimes_follow_runtime_order_before_recycling():
    assignment = auto_assign_roles([OMNIROUTE, OLLAMA, CODEX, CLAUDE], ["claude_code_cli", "ollama"])
    assert assignment == {
        "developer": "claude_code_cli",
        "product_owner": "codex_cli",
        "architect": "ollama",
        "security": "omniroute",
    }


def test_a_single_runtime_for_po_and_developer_is_a_complete_team():
    assignment = auto_assign_roles([CODEX])
    assert assignment == {"developer": "codex_cli", "product_owner": "codex_cli"}
    assert missing_required_roles(assignment) == []
    assert missing_required_roles({"developer": "codex_cli"}) == ["product_owner"]
    assert missing_required_roles({"architect": "ollama", "security": "ollama"}) == ["product_owner", "developer"]
    assert missing_required_roles(auto_assign_roles([OLLAMA])) == ["developer"]


def test_ties_break_alphabetically_for_determinism():
    first = RuntimeFacts("aaa_gateway", "AAA", "gateway", ("developer",))
    last = RuntimeFacts("zzz_gateway", "ZZZ", "gateway", ("developer",))
    assert auto_assign_roles([last, first]) == {"developer": "aaa_gateway"}


def test_only_product_owner_and_developer_are_required():
    assert TEAM_ROLES == ("product_owner", "developer", "architect", "security")
    assert REQUIRED_TEAM_ROLES == ("product_owner", "developer")
    assert OPTIONAL_TEAM_ROLES == ("architect", "security")
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_roles.py','-q','-p','no:randomly']))"`
Expected: FAIL con `ModuleNotFoundError: No module named 'local_control_center.runtime_team'`.

- [ ] **Step 3: Implementación mínima**

`local_control_center/runtime_team/__init__.py`:

```python
"""Equipo de runtimes por hilo: selección validada, asignación por rol y enforcement en el loop.

@author Rodrigo Mason
"""
```

`local_control_center/runtime_team/roles.py`:

```python
"""Roles del equipo de runtimes por hilo: elegibilidad por contrato y reparto automático determinista.

Funciones puras: no leen la base ni el reloj. La elegibilidad llama a los mismos predicados que usa
cada runner (ProductOwner, Developer, Architect, Security) y exige además la capacidad que
``AIResourceManager`` pide al rol del scheduler, para que el panel nunca ofrezca un runtime que el
ejecutor o el gestor rechazarían después. El reparto prioriza CLI para desarrollo, luego el orden
global de runtimes y por último el ``provider_id`` alfabético, así el mismo conjunto da siempre el
mismo resultado. Solo PO y Developer son obligatorios: Arquitecto y Seguridad pueden quedar sin
asignar (el Arquitecto no corre y Seguridad queda en sus scanners deterministas).

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_control_center.agents.architect_agent_contract import is_architect_runtime
from local_control_center.agents.developer_agent_contract import (
    DEVELOPER_AGENT_CLI_RUNTIMES,
    is_developer_runtime,
)
from local_control_center.agents.product_owner_agent_contract import is_product_owner_runtime
from local_control_center.agents.security_agent_contract import is_security_model_runtime

TEAM_ROLES = ("product_owner", "developer", "architect", "security")
REQUIRED_TEAM_ROLES = ("product_owner", "developer")
OPTIONAL_TEAM_ROLES = ("architect", "security")
ASSIGNMENT_ORDER = ("developer", "product_owner", "architect", "security")
_DIRECT_TEAM_ROLES = frozenset({"product_owner", "developer", "architect"})
_CODE_CAPABILITIES = frozenset({"code_edit", "issue_to_patch", "code"})
_REVIEW_CAPABILITIES = frozenset({"code_review", "review"})
_CONTRACT_EXECUTABILITY = {
    "executable": True,
    "canRunPrompt": True,
    "canEditWorkspace": True,
    "productOwnerExecutable": True,
}
"""La ejecutabilidad la demuestra ``models.validate_runtime``; aquí solo se evalúa el contrato."""


@dataclass(frozen=True)
class RuntimeFacts:
    """Hechos de un runtime configurado que deciden su elegibilidad y su rango en el reparto."""

    provider_id: str
    label: str
    kind: str
    eligible_roles: tuple[str, ...]
    policy_denied_reason: str | None = None
    """Causa de ``runtime_policy_decision`` si el proyecto deniega el runtime; ``None`` si lo permite."""


def eligible_team_roles(runtime_status: Mapping[str, Any]) -> tuple[str, ...]:
    """Roles del equipo que el runtime puede cumplir: predicado real del runner ∧ capacidad del rol.

    ``runtime_status`` es el dict de ``RuntimeStatusService`` (``id``, ``providerFamily``,
    ``capabilities``, ``models``). Developer de modelo exige además una capacidad de código y
    Seguridad, además del ``chat`` que exige su runner, una de revisión (``code_review``/``review``),
    porque ``AIResourceManager`` las pide a esos roles del scheduler.
    """
    capabilities = {str(item).strip().lower() for item in runtime_status.get("capabilities") or []}
    view = {**runtime_status, **_CONTRACT_EXECUTABILITY, "capabilities": sorted(capabilities)}
    is_cli = str(view.get("id") or "") in DEVELOPER_AGENT_CLI_RUNTIMES
    roles: list[str] = []
    if is_product_owner_runtime(view):
        roles.append("product_owner")
    if is_developer_runtime(view) and (is_cli or capabilities & _CODE_CAPABILITIES):
        roles.append("developer")
    if is_architect_runtime(view):
        roles.append("architect")
    if is_security_model_runtime(view) and capabilities & _REVIEW_CAPABILITIES:
        roles.append("security")
    return tuple(roles)


def team_role_for(role: str, *, kind: str = "", capabilities: Iterable[str] = ()) -> str | None:
    """Traduce un rol del scheduler (o el rol de failover) al rol del equipo que lo gobierna.

    Devuelve ``None`` para roles sin asignación propia (aido_lead, qa_engineer, technical_lead...):
    esos quedan confinados al conjunto completo del hilo, no a un runtime único.
    """
    normalized = str(role or "").strip().lower()
    caps = {str(item).strip().lower() for item in capabilities}
    if normalized in _DIRECT_TEAM_ROLES:
        return normalized
    if normalized.startswith("security") or "security_review" in caps:
        return "security"
    if str(kind or "").strip().lower() == "build" or "code_edit" in caps:
        return "developer"
    return None


def auto_assign_roles(runtimes: Sequence[RuntimeFacts], runtime_order: Sequence[str] = ()) -> dict[str, str]:
    """Reparte los roles entre los runtimes dados; un rol sin elegibles queda fuera del resultado.

    Cada rol toma el primer runtime elegible aún no usado; si todos se usaron, recicla desde el
    primero del ranking (CLI primero, luego ``runtime_order``, luego ``provider_id``).
    """
    order = {provider_id: index for index, provider_id in enumerate(runtime_order)}
    ranked = sorted(
        runtimes,
        key=lambda item: (item.kind != "cli", order.get(item.provider_id, len(order)), item.provider_id),
    )
    used: list[str] = []
    assignment: dict[str, str] = {}
    for role in ASSIGNMENT_ORDER:
        eligible = [item.provider_id for item in ranked if role in item.eligible_roles]
        if not eligible:
            continue
        chosen = next((provider_id for provider_id in eligible if provider_id not in used), eligible[0])
        assignment[role] = chosen
        if chosen not in used:
            used.append(chosen)
    return assignment


def missing_required_roles(role_runtimes: Mapping[str, str]) -> list[str]:
    """Roles obligatorios (PO y Developer) sin runtime asignado; los opcionales nunca faltan."""
    return [role for role in REQUIRED_TEAM_ROLES if not str(role_runtimes.get(role) or "").strip()]
```

- [ ] **Step 4: Ejecutar tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_roles.py','-q','-p','no:randomly']))"`
Expected: PASS (26 tests).
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format local_control_center/runtime_team tests_py/test_runtime_team_roles.py` y `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center/runtime_team tests_py/test_runtime_team_roles.py`
Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/runtime_team/__init__.py local_control_center/runtime_team/roles.py tests_py/test_runtime_team_roles.py
git commit -m "Feature (RuntimeTeam): elegibilidad por contrato de agente, reparto automático determinista y solo PO/Developer obligatorios" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Frescura de validación y prueba real del runtime

**Files:**
- Create: `local_control_center/runtime_team/validation.py`
- Create: `local_control_center/runtime_team/probe.py`
- Test: `tests_py/test_runtime_team_validation.py`

**Interfaces:**
- Consumes: `provider_configuration_fingerprint`, `record_model_execution` (`agents/model_execution_health.py:42,78`); `_requires_remote_provider_call`, `_validate_real_discovery_credentials`, `_run_provider_test_prompt` (`agents/model_gateway_api.py:165,169,204`, import diferido: `model_gateway_api` importará este módulo en Task 4); `provider_instance` (`agents/model_gateway.py:71`); `ProviderAdapterResolutionError`, `provider_account_policy_kind` (`agents/providers/factory.py:29,118`); `validate_cli_candidate` (`agents/runtime_preflight_cli.py:121`, que ya clasifica y registra los intentos CLI fallidos: `:359-367,487-495,501-506`); `classify_runtime_failure`, `EVIDENCE_LIMIT` (`agents/runtime_failure_classifier.py:111,40`); `AIResourceRequest` (`agents/ai_resource_manager.py:67-106`); `RuntimeStatusService(connection, probe_runtime_ids=...)` (`agents/runtime_status.py:732-748,839`); `RuntimeConfigRepository.runtime_policy_decision` (`runtime_integrations/repository.py:186`); `EventBus.record_audit` (`shared/event_bus.py:114`).
- Produces: `RUNTIME_TEAM_FRESHNESS_SECONDS = 1800`; `RuntimeValidationState(status, checked_at, latency_ms, model, reason)` con `.to_record() -> dict`; `runtime_validation_state(connection, provider_id, *, max_age_seconds, now=None) -> RuntimeValidationState`; `runtime_validated_within(connection, provider_id, seconds=1800) -> bool`; `OPERATOR_APPROVAL_AUDIT_ACTION = "runtime.validation.operator_approved"`; `RuntimeValidationService(connection).validate(provider_id, *, project_id, actor="operator") -> dict` con claves `providerId, kind, status ('validated'|'failed'|'deferred'), model, latencyMs, reason, evidence, checkedAt`, donde `reason` es una causa estable (código propio o `RuntimeFailureCause` del clasificador compartido) y `evidence` el texto redactado y acotado; lanza `KeyError` (desconocido) y `ValueError` (manual).
- Regla de evidencia: todo retorno `failed` persiste un registro fallido en `model_execution_health` (con el modelo probado o, si no hubo modelo, el último registrado del proveedor; sin registro previo no hay nada que invalidar), salvo `policy_denied`, que es una decisión del proyecto y no evidencia del runtime. `deferred` nunca se persiste (no hubo prueba).

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_validation.py`:

```python
"""Frescura de 30 minutos y prueba real de runtimes para el equipo del hilo.

La frescura se lee de la misma evidencia de ejecución que usa la validación de 24 h; la prueba real
reusa el prompt fijo de test-prompt (API/local) y el preflight CLI con la aprobación explícita del
operador. Los CLIs se simulan en el borde del proceso: ``validate_cli_candidate`` y el estado de runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from local_control_center.agents import model_gateway_api
from local_control_center.agents.model_execution_health import (
    VALIDATION_TTL_SECONDS,
    model_validation_rejection,
    record_model_execution,
)
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.agents.providers.factory import ProviderAdapterResolutionError
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team import probe
from local_control_center.runtime_team.probe import OPERATOR_APPROVAL_AUDIT_ACTION, RuntimeValidationService
from local_control_center.runtime_team.validation import (
    RUNTIME_TEAM_FRESHNESS_SECONDS,
    runtime_validated_within,
    runtime_validation_state,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

API_PROVIDER = "openai_compatible"


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as handle:
        initialize_platform_schema(handle)
        yield handle


def _ago(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _state(connection, provider_id: str, seconds: int = RUNTIME_TEAM_FRESHNESS_SECONDS):
    return runtime_validation_state(connection, provider_id, max_age_seconds=seconds)


class _Provider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def chat_completion(self, request):
        if self.error is not None:
            raise self.error
        return ModelResponse(providerId="ollama", model=request.model, content="ok", usage=UsageRecord())


class _Statuses:
    def __init__(self, connection, **kwargs) -> None:
        self.kwargs = kwargs

    def list_provider_statuses(self, *, project_id=None):
        return [{"id": "codex_cli", "kind": "cli", "detectedCommand": "codex"}]


def test_a_never_validated_runtime_reports_never(connection):
    assert _state(connection, API_PROVIDER).status == "never"
    assert runtime_validated_within(connection, API_PROVIDER) is False


def test_a_recent_success_is_validated_with_latency(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(1))
    state = _state(connection, API_PROVIDER)
    assert state.status == "validated"
    assert state.model == "m"
    assert state.latency_ms is not None and state.latency_ms >= 0
    assert runtime_validated_within(connection, API_PROVIDER) is True


def test_a_success_older_than_thirty_minutes_is_stale_but_inside_the_daily_window(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(31))
    assert (_state(connection, API_PROVIDER).status, _state(connection, API_PROVIDER).reason) == (
        "stale",
        "runtime_validation_expired",
    )
    assert _state(connection, API_PROVIDER, VALIDATION_TTL_SECONDS).status == "validated"


def test_a_configuration_change_after_validation_makes_it_stale(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(1))
    ProviderAccountStore(connection).patch_provider_account(
        API_PROVIDER, {"baseUrl": "https://changed.example.invalid/v1"}
    )
    state = _state(connection, API_PROVIDER)
    assert (state.status, state.reason) == ("stale", "runtime_validation_configuration_changed")


def test_a_later_failure_invalidates_a_fresh_success(connection):
    record_model_execution(connection, API_PROVIDER, "m", True, "test_prompt", started_at=_ago(2))
    record_model_execution(connection, API_PROVIDER, "m", False, "tool_broker", started_at=_ago(1))
    assert _state(connection, API_PROVIDER).status == "failed"


def test_model_runtime_probe_records_fresh_evidence(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    monkeypatch.setattr(probe, "provider_instance", lambda provider_id, *, connection: _Provider())
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert result["status"] == "validated"
    assert result["model"] == "local_default"
    assert runtime_validated_within(connection, "ollama") is True


def test_a_failed_probe_invalidates_the_previous_validation(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt", started_at=_ago(1))
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _Provider(error=RuntimeError("daemon down")),
    )
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "runtime_validation_failed")
    assert "daemon down" in result["evidence"]
    assert _state(connection, "ollama").status == "failed"
    assert model_validation_rejection(connection, "ollama", "local_default") == "model_validation_failed"


def test_a_failed_probe_reports_the_shared_classifier_cause(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: _Provider(error=ConnectionRefusedError("connection refused")),
    )
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "provider_unreachable")
    assert "connection refused" in result["evidence"]


def _break_model(connection, monkeypatch):
    connection.execute("UPDATE model_catalog SET enabled = 0 WHERE provider_id = 'ollama'")


def _break_adapter(connection, monkeypatch):
    def _raise(provider_id, *, connection):
        raise ProviderAdapterResolutionError("adapter unavailable")

    monkeypatch.setattr(probe, "provider_instance", _raise)


def _break_credentials(connection, monkeypatch):
    def _raise(account):
        raise HTTPException(status_code=400, detail="Credential ref env:MISSING is missing.")

    monkeypatch.setattr(model_gateway_api, "_validate_real_discovery_credentials", _raise)


@pytest.mark.parametrize(
    ("breakage", "reason"),
    [
        (_break_model, "model_required"),
        (_break_adapter, "provider_adapter_resolution_failed"),
        (_break_credentials, "credential_invalid"),
    ],
)
def test_every_early_failure_invalidates_a_fresh_success(connection, monkeypatch, breakage, reason):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt", started_at=_ago(1))
    breakage(connection, monkeypatch)
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", reason)
    assert _state(connection, "ollama").status == "failed"


def test_a_cli_without_runtime_status_invalidates_a_fresh_success(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt", started_at=_ago(1))

    class _NoStatuses(_Statuses):
        def list_provider_statuses(self, *, project_id=None):
            return []

    monkeypatch.setattr(probe, "RuntimeStatusService", _NoStatuses)
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])
    assert (result["status"], result["reason"]) == ("failed", "runtime_status_unavailable")
    assert _state(connection, "codex_cli").status == "failed"


def test_a_failure_without_any_prior_evidence_leaves_the_runtime_never_validated(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("ollama", {"enabled": True})
    _break_model(connection, monkeypatch)
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert result["status"] == "failed"
    assert _state(connection, "ollama").status == "never"


def test_a_disabled_runtime_is_reported_without_calling_it(connection, monkeypatch):
    monkeypatch.setattr(probe, "provider_instance", lambda *args, **kwargs: pytest.fail("must not call"))
    result = RuntimeValidationService(connection).validate("ollama", project_id=None)
    assert (result["status"], result["reason"]) == ("failed", "provider_disabled")


def test_manual_and_unknown_runtimes_are_rejected(connection):
    service = RuntimeValidationService(connection)
    with pytest.raises(ValueError):
        service.validate("manual", project_id=None)
    with pytest.raises(KeyError):
        service.validate("does_not_exist", project_id=None)


def test_cli_probe_is_operator_approved_and_audited(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    captured: dict = {}

    def fake_validate(conn, *, model, runtime_status, request):
        captured.update(model=model, runtime_status=runtime_status, request=request)
        record_model_execution(conn, "codex_cli", model["model"], True, "test_prompt")
        return {"status": "completed", "success": True, "attempted": True, "reason": None}

    monkeypatch.setattr(probe, "RuntimeStatusService", _Statuses)
    monkeypatch.setattr(probe, "validate_cli_candidate", fake_validate)
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])

    assert result["status"] == "validated"
    request = captured["request"]
    assert request.allow_unknown_cost is True
    assert request.require_approval_for_unknown_cost is False
    assert request.project_id == project["id"]
    assert request.allowed_provider_ids == ["codex_cli"]
    assert captured["model"] == {"providerId": "codex_cli", "model": "gpt-5.5"}
    audit = connection.execute(
        "SELECT project_id FROM audit_events WHERE action = ? AND target = ?",
        (OPERATOR_APPROVAL_AUDIT_ACTION, "codex_cli"),
    ).fetchone()
    assert audit is not None and audit["project_id"] == project["id"]
    assert runtime_validated_within(connection, "codex_cli") is True


def test_cli_probe_without_a_project_is_deferred(connection, monkeypatch):
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    monkeypatch.setattr(probe, "validate_cli_candidate", lambda *args, **kwargs: pytest.fail("no project"))
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=None)
    assert (result["status"], result["reason"]) == ("deferred", "project_required_for_cli_validation")


def test_cli_probe_deferred_by_the_preflight_keeps_its_reason(connection, tmp_path, monkeypatch):
    project = ProjectsRepository(connection).create_project(
        name="Runtime team", path=tmp_path / "project", template_id="other"
    )
    ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    connection.execute(
        "UPDATE model_catalog SET enabled = 1 WHERE provider_id = 'codex_cli' AND model = 'gpt-5.5'"
    )
    monkeypatch.setattr(probe, "RuntimeStatusService", _Statuses)
    monkeypatch.setattr(
        probe,
        "validate_cli_candidate",
        lambda *args, **kwargs: {
            "status": "deferred",
            "success": False,
            "attempted": False,
            "reason": "preflight_requires_execution_context",
        },
    )
    result = RuntimeValidationService(connection).validate("codex_cli", project_id=project["id"])
    assert (result["status"], result["reason"]) == ("deferred", "preflight_requires_execution_context")
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_validation.py','-q','-p','no:randomly']))"`
Expected: FAIL con `ModuleNotFoundError: No module named 'local_control_center.runtime_team.probe'`.

- [ ] **Step 3: Implementación mínima**

`local_control_center/runtime_team/validation.py`:

```python
"""Frescura de validación por runtime para el equipo del hilo (ventana corta, solo lectura).

Lee la misma evidencia de ejecución real que ``model_execution_health`` usa para su TTL de 24 h,
pero con una ventana propia: el selector del hilo exige una prueba de ida y vuelta reciente con la
configuración actual. Los consumidores de 24 h no cambian.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from local_control_center.agents.model_execution_health import provider_configuration_fingerprint

RUNTIME_TEAM_FRESHNESS_SECONDS = 30 * 60


@dataclass(frozen=True)
class RuntimeValidationState:
    """Última evidencia de un runtime traducida al estado que muestra y exige el selector."""

    status: str
    checked_at: str | None = None
    latency_ms: int | None = None
    model: str | None = None
    reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        """Serializa el estado con el contrato camelCase de la API."""
        return {
            "status": self.status,
            "checkedAt": self.checked_at,
            "latencyMs": self.latency_ms,
            "model": self.model,
            "reason": self.reason,
        }


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def runtime_validation_state(
    connection: sqlite3.Connection,
    provider_id: str,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> RuntimeValidationState:
    """Clasifica la última ejecución registrada del runtime: validated, stale, failed o never.

    Una falla posterior invalida cualquier éxito anterior y un cambio de configuración vuelve
    ``stale`` la evidencia, igual que ``model_validation_rejection`` pero a nivel de runtime.
    """
    row = connection.execute(
        """SELECT model, configuration_fingerprint, success, started_at, observed_at
           FROM model_execution_health WHERE provider_id = ?
           ORDER BY started_at DESC, id DESC LIMIT 1""",
        (provider_id,),
    ).fetchone()
    if row is None:
        return RuntimeValidationState("never", reason="runtime_validation_required")
    started = _parse_timestamp(row["started_at"])
    observed = _parse_timestamp(row["observed_at"])
    latency = (
        int((observed - started).total_seconds() * 1000)
        if started and observed and observed >= started
        else None
    )
    evidence = {"checked_at": row["observed_at"], "latency_ms": latency, "model": row["model"]}
    if not row["success"]:
        return RuntimeValidationState("failed", reason="runtime_validation_failed", **evidence)
    if row["configuration_fingerprint"] != provider_configuration_fingerprint(connection, provider_id):
        return RuntimeValidationState("stale", reason="runtime_validation_configuration_changed", **evidence)
    age = ((now or datetime.now(UTC)) - started).total_seconds() if started else -1.0
    if not 0 <= age < max_age_seconds:
        return RuntimeValidationState("stale", reason="runtime_validation_expired", **evidence)
    return RuntimeValidationState("validated", **evidence)


def runtime_validated_within(
    connection: sqlite3.Connection, provider_id: str, seconds: int = RUNTIME_TEAM_FRESHNESS_SECONDS
) -> bool:
    """Indica si el runtime respondió una prueba real dentro de ``seconds`` con su configuración actual."""
    return runtime_validation_state(connection, provider_id, max_age_seconds=seconds).status == "validated"
```

`local_control_center/runtime_team/probe.py`:

```python
"""Prueba de ida y vuelta de un runtime a pedido del operador (operación ``models.validate_runtime``).

API, gateway y local reusan la completion fija de test-prompt; los CLI reusan el preflight del loop
con un ``AIResourceRequest`` del operador que autoriza el costo desconocido, porque el clic es la
aprobación: queda auditado como ``runtime.validation.operator_approved`` y consume cuota de
suscripción. Toda falla deja evidencia (salvo una denegación de política del proyecto), así una
prueba fallida invalida también la validación de 24 h; la causa sale del clasificador compartido.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from local_control_center.agents.ai_resource_manager import AIResourceRequest
from local_control_center.agents.model_execution_health import (
    provider_configuration_fingerprint,
    record_model_execution,
)
from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import (
    ProviderAdapterResolutionError,
    provider_account_policy_kind,
)
from local_control_center.agents.runtime_failure_classifier import EVIDENCE_LIMIT, classify_runtime_failure
from local_control_center.agents.runtime_preflight_cli import validate_cli_candidate
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.redaction import redact_secrets

OPERATOR_APPROVAL_AUDIT_ACTION = "runtime.validation.operator_approved"
_STABLE_CODE = re.compile(r"[a-z0-9_.:-]+")


def _result(
    provider_id: str,
    kind: str,
    status: str,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
    reason: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "kind": kind,
        "status": status,
        "model": model,
        "latencyMs": latency_ms,
        "reason": str(redact_secrets(reason)) if reason else None,
        "evidence": str(redact_secrets(evidence))[:EVIDENCE_LIMIT] if evidence else None,
        "checkedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _failure_cause(provider_id: str, detail: str, *, fallback: str) -> tuple[str, str]:
    """Causa estable del clasificador compartido (o ``fallback``) y evidencia redactada de la falla."""
    failure = classify_runtime_failure(runtime_id=provider_id, return_code=None, stdout="", stderr=detail)
    if failure is not None and failure.cause != "unknown":
        return failure.cause, failure.evidence
    return fallback, str(redact_secrets(detail))[:EVIDENCE_LIMIT]


class RuntimeValidationService:
    """Ejecuta la prueba real de un runtime y deja la evidencia en ``model_execution_health``."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = ProviderAccountStore(connection)

    def validate(self, provider_id: str, *, project_id: str | None, actor: str = "operator") -> dict[str, Any]:
        """Prueba un runtime y devuelve un resultado redactado (validated, failed o deferred).

        Raises:
            KeyError: el runtime no existe.
            ValueError: el runtime es manual y no admite una prueba automática.
        """
        account = self.accounts.get_provider_account(provider_id)
        kind = str(account.get("providerType") or "")
        if kind == "manual":
            raise ValueError(f"Runtime {provider_id} is manual and cannot be probed.")
        if not account.get("enabled"):
            return self._failed(provider_id, kind, reason="provider_disabled")
        if kind == "cli":
            return self._validate_cli(account, project_id=project_id, actor=actor)
        return self._validate_model_provider(account, project_id=project_id)

    def _first_enabled_model(self, provider_id: str) -> str | None:
        for model in self.accounts.list_models():
            if model.get("providerId") == provider_id and model.get("enabled"):
                return str(model.get("model"))
        return None

    def _last_recorded_model(self, provider_id: str) -> str | None:
        row = self.connection.execute(
            """SELECT model FROM model_execution_health WHERE provider_id = ?
               ORDER BY started_at DESC, id DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        return str(row["model"]) if row is not None else None

    def _record_failure(
        self,
        provider_id: str,
        model: str | None,
        *,
        fingerprint: str | None = None,
        started_at: str | None = None,
    ) -> None:
        """Deja evidencia fallida salvo que la prueba ya la haya dejado desde ``started_at``.

        Sin modelo probado usa el último modelo registrado del proveedor; sin registro previo no hay
        validación que invalidar y no se escribe nada.
        """
        if started_at is not None and self.connection.execute(
            "SELECT 1 FROM model_execution_health WHERE provider_id = ? AND started_at >= ? LIMIT 1",
            (provider_id, started_at),
        ).fetchone():
            return
        evidence_model = model or self._last_recorded_model(provider_id)
        if evidence_model is None:
            return
        record_model_execution(
            self.connection,
            provider_id,
            evidence_model,
            False,
            "test_prompt",
            configuration_fingerprint=fingerprint,
            started_at=started_at,
        )

    def _failed(
        self,
        provider_id: str,
        kind: str,
        *,
        reason: str,
        model: str | None = None,
        latency_ms: int | None = None,
        evidence: str | None = None,
        fingerprint: str | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        """Registra la falla (invalida la validación vigente) y devuelve el resultado ``failed``."""
        self._record_failure(provider_id, model, fingerprint=fingerprint, started_at=started_at)
        return _result(
            provider_id, kind, "failed", model=model, latency_ms=latency_ms, reason=reason, evidence=evidence
        )

    def _validate_model_provider(self, account: dict[str, Any], *, project_id: str | None) -> dict[str, Any]:
        """Prueba API/gateway/local con el prompt fijo de test-prompt.

        El import es diferido porque ``model_gateway_api`` registra la ruta que usa este servicio.
        """
        from local_control_center.agents.model_gateway_api import (
            _requires_remote_provider_call,
            _run_provider_test_prompt,
            _validate_real_discovery_credentials,
        )

        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if _requires_remote_provider_call(account):
            decision = RuntimeConfigRepository(self.connection).runtime_policy_decision(
                provider_id=provider_id,
                provider_family=str(account.get("providerFamily") or ""),
                kind=provider_account_policy_kind(account),
                project_id=project_id,
            )
            if not decision.get("allowed"):
                return _result(
                    provider_id,
                    kind,
                    "failed",
                    reason="policy_denied",
                    evidence=str(decision.get("reason") or "Runtime policy denies this provider."),
                )
        try:
            _validate_real_discovery_credentials(account)
        except HTTPException as error:
            cause, evidence = _failure_cause(provider_id, str(error.detail), fallback="credential_invalid")
            return self._failed(provider_id, kind, reason=cause, evidence=evidence)
        model = self._first_enabled_model(provider_id)
        if model is None:
            return self._failed(provider_id, kind, reason="model_required")
        try:
            provider = provider_instance(provider_id, connection=self.connection)
        except ProviderAdapterResolutionError as error:
            code = str(getattr(error, "public_code", error.code))
            return self._failed(provider_id, kind, model=model, reason=code, evidence=str(error))
        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        fingerprint = provider_configuration_fingerprint(self.connection, provider_id)
        outcome = _run_provider_test_prompt(provider_id, model, connection=self.connection, provider=provider)
        if outcome["ok"]:
            return _result(provider_id, kind, "validated", model=model, latency_ms=outcome["latencyMs"])
        error_text = str(outcome.get("error") or "")
        fallback = error_text if _STABLE_CODE.fullmatch(error_text) else "runtime_validation_failed"
        cause, evidence = _failure_cause(provider_id, error_text, fallback=fallback)
        return self._failed(
            provider_id,
            kind,
            model=model,
            latency_ms=outcome["latencyMs"],
            reason=cause,
            evidence=evidence,
            fingerprint=fingerprint,
            started_at=started_at,
        )

    def _validate_cli(self, account: dict[str, Any], *, project_id: str | None, actor: str) -> dict[str, Any]:
        """Prueba un CLI con el preflight del loop; el clic del operador es la aprobación del costo."""
        provider_id = str(account["providerId"])
        if not project_id:
            return _result(provider_id, "cli", "deferred", reason="project_required_for_cli_validation")
        model = self._first_enabled_model(provider_id)
        if model is None:
            return self._failed(provider_id, "cli", reason="model_required")
        statuses = RuntimeStatusService(self.connection, probe_runtime_ids={provider_id}).list_provider_statuses(
            project_id=project_id
        )
        runtime_status = next((item for item in statuses if item.get("id") == provider_id), None)
        if runtime_status is None:
            return self._failed(provider_id, "cli", model=model, reason="runtime_status_unavailable")
        EventBus(self.connection).record_audit(
            action=OPERATOR_APPROVAL_AUDIT_ACTION,
            target=provider_id,
            project_id=project_id,
            actor=actor,
            payload={"providerId": provider_id, "projectId": project_id, "model": model},
        )
        request = AIResourceRequest(
            task_type="runtime_team.validate",
            project_id=project_id,
            agent_id="runtime_team_validation",
            task_id=f"runtime-validation-{provider_id}",
            required_capabilities=["chat"],
            allowed_provider_ids=[provider_id],
            allow_unknown_cost=True,
            require_approval_for_unknown_cost=False,
        )
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat(timespec="microseconds")
        outcome = validate_cli_candidate(
            self.connection,
            model={"providerId": provider_id, "model": model},
            runtime_status=runtime_status,
            request=request,
        )
        latency = int((time.monotonic() - started) * 1000)
        if outcome.get("success"):
            return _result(provider_id, "cli", "validated", model=model, latency_ms=latency)
        if not outcome.get("attempted"):
            reason = str(outcome.get("reason") or "preflight_not_executed")
            return _result(provider_id, "cli", "deferred", model=model, reason=reason)
        reason = str(outcome.get("failureCause") or outcome.get("reason") or "cli_preflight_unverified")
        return self._failed(
            provider_id,
            "cli",
            model=model,
            latency_ms=latency,
            reason=reason,
            evidence=outcome.get("failureEvidence"),
            started_at=started_at,
        )
```

(El preflight CLI ya clasifica con `classify_runtime_failure` y registra el intento; `_failed` con `started_at` no duplica ese registro y solo escribe si el preflight no lo hizo.)

- [ ] **Step 4: Ejecutar tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_validation.py','tests_py/test_model_execution_health.py','-q','-p','no:randomly']))"`
Expected: PASS (los 18 nuevos + el archivo existente intacto).
Run ruff format/check sobre `local_control_center/runtime_team tests_py/test_runtime_team_validation.py`. Expected: sin errores (si `ruff format` reparte líneas largas, aceptar).

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/runtime_team/validation.py local_control_center/runtime_team/probe.py tests_py/test_runtime_team_validation.py
git commit -m "Feature (RuntimeTeam): frescura de 30 minutos y prueba real de runtimes con aprobación auditada del operador" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: llama.cpp como proveedor local OpenAI-compatible

**Files:**
- Modify: `local_control_center/agents/provider_catalog.py` (insertar entre la entrada `ollama_remote` y `openai_compatible`, ~`:410`)
- Modify: `local_control_center/agents/provider_catalog_api.py:62-88` y `:387-388`
- Modify: `local-control-center/web/src/features/runtime-setup/runtimeSetup.ts:109-121` (después de `ollama_remote`)
- Modify: `local_control_center/i18n/default_catalog.json` (después de la entrada `app.runtime.instructions.omniroute`, `:9090-9093`)
- Test: `tests_py/test_llama_cpp_provider.py`

**Interfaces:**
- Consumes: `ProviderCatalogEntry` (`agents/provider_catalog.py:20-44`), `OPENAI_COMPATIBLE_SYNC` (`:73`), `_seed_omniroute_runtime_capabilities` (`provider_catalog_api.py:65`), helpers de test `auth_headers`, `catalog_by_id`, `create_client` (`tests_py/test_provider_setup_catalog.py:97-132`).
- Produces: entrada de catálogo `llama_cpp`; `BUILD_REVIEW_SEEDED_CATALOG_IDS = frozenset({"omniroute", "llama_cpp"})`; tarjeta `llama_cpp` en `PROVIDER_CATALOG` TS; clave i18n `app.runtime.instructions.llama_cpp`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_llama_cpp_provider.py`:

```python
"""llama.cpp (llama-server) entra al catálogo como runtime local OpenAI-compatible.

Familia ``openai_compatible`` para quedar en ``MODEL_RUNTIME_TOOLS`` (PO y Developer por patch sin
adaptadores nuevos) y siembra de ``code_edit``/``code_review`` como OmniRoute, o los roles de build
y review la descartan por capacidades faltantes.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents.model_gateway import provider_instance
from local_control_center.agents.providers.openai_compatible import OpenAICompatibleProvider
from local_control_center.shared.db import open_sqlite_connection
from tests_py.test_provider_setup_catalog import auth_headers, catalog_by_id, create_client

LLAMA_CPP_BASE_URL = "http://127.0.0.1:8082/v1"


def test_catalog_exposes_llama_cpp_as_a_local_openai_compatible_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = catalog_by_id(create_client(tmp_path, monkeypatch))["llama_cpp"]
    assert entry["providerType"] == "local"
    assert entry["apiFormat"] == "openai_compatible"
    assert entry["providerFamily"] == "openai_compatible"
    assert entry["defaultBaseUrl"] == LLAMA_CPP_BASE_URL
    assert entry["credentialKind"] == "optional_bearer_token"
    assert entry["requiredFields"] == []


def test_from_catalog_llama_cpp_seeds_build_and_review_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/provider-accounts/from-catalog",
        headers=auth_headers(client),
        json={"providerId": "llama_cpp", "enabled": True},
    )
    assert response.status_code == 201, response.text
    provider = response.json()["provider"]
    assert provider["providerId"] == "llama_cpp"
    assert provider["baseUrl"] == LLAMA_CPP_BASE_URL
    with (
        closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"]))) as connection,
        connection,
    ):
        rows = connection.execute(
            "SELECT capability FROM runtime_capabilities WHERE runtime = 'llama_cpp' AND enabled = 1"
        ).fetchall()
        instance = provider_instance("llama_cpp", connection=connection)
    assert {str(row["capability"]) for row in rows} == {"chat", "code_edit", "code_review"}
    assert isinstance(instance, OpenAICompatibleProvider)
    assert instance.base_url == LLAMA_CPP_BASE_URL
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_llama_cpp_provider.py','-q','-p','no:randomly']))"`
Expected: FAIL con `KeyError: 'llama_cpp'` (el catálogo no la tiene).

- [ ] **Step 3: Implementación mínima**

En `local_control_center/agents/provider_catalog.py`, insertar inmediatamente antes de la entrada que empieza con `    ProviderCatalogEntry(\n        id="openai_compatible",` (el orden importa: `openai_compatible` debe seguir siendo la última entrada de su familia para `runtime_adapters/registry.py:35-45`):

```python
    ProviderCatalogEntry(
        id="llama_cpp",
        display_name="llama.cpp",
        provider_type="local",
        api_format="openai_compatible",
        default_base_url="http://127.0.0.1:8082/v1",
        required_fields=(),
        credential_kind="optional_bearer_token",
        known_models=(),
        model_sync=OPENAI_COMPATIBLE_SYNC,
        capabilities=("chat", "local", "private", "streaming"),
        docs_url="https://github.com/ggml-org/llama.cpp/tree/master/tools/server",
        pricing_source="local_runtime_cost_only",
        provider_family="openai_compatible",
        aliases=("llama_server", "llamacpp"),
    ),
```

`deployment_mode` se deja en el default `"custom"` a propósito: `self_hosted_development` está en `DEPLOYMENT_MODES_REQUIRING_BASE_URL` (`provider_catalog_api.py:54-56,212-216`) y haría fallar con 422 el alta desde el wizard, que no envía `baseUrl` cuando `needsBaseUrl` es `false`.

En `local_control_center/agents/provider_catalog_api.py`, reemplazar el bloque de líneas 59-62:

```python
# Mismas capabilities que scripts/setup_omniroute.py: `chat` es obligatorio para ejecutar y las
# filas de runtime_capabilities por provider_id ocultan por completo las de la familia, así que sin
# esta siembra una cuenta OmniRoute creada desde el wizard queda solo con el `chat` de la familia
# openai_compatible y los roles de build (code) y review la descartan con missing_capabilities.
OMNIROUTE_RUNTIME_CAPABILITIES = ("chat", "code_edit", "code_review")
```

por:

```python
# Mismas capabilities que scripts/setup_omniroute.py: `chat` es obligatorio para ejecutar y las
# filas de runtime_capabilities por provider_id ocultan por completo las de la familia, así que sin
# esta siembra una cuenta OmniRoute o llama.cpp creada desde el wizard queda solo con el `chat` de la
# familia openai_compatible y los roles de build (code) y review la descartan con missing_capabilities.
OMNIROUTE_RUNTIME_CAPABILITIES = ("chat", "code_edit", "code_review")
BUILD_REVIEW_SEEDED_CATALOG_IDS = frozenset({"omniroute", "llama_cpp"})
```

y en el handler `create_account_from_catalog` (`:387`) reemplazar `        if entry.id == "omniroute":` por `        if entry.id in BUILD_REVIEW_SEEDED_CATALOG_IDS:`.

En `local-control-center/web/src/features/runtime-setup/runtimeSetup.ts`, insertar después del objeto `ollama_remote` (el que termina en `instructionsKey: 'app.runtime.instructions.ollama_remote',\n\t},`):

```ts
	{
		id: 'llama_cpp',
		displayName: 'llama.cpp',
		group: 'local',
		Icon: Server,
		providerType: 'local',
		apiFormat: 'openai_compatible',
		defaultBaseUrl: 'http://127.0.0.1:8082/v1',
		needsBaseUrl: false,
		authKind: 'optional_api_key',
		capabilities: ['chat', 'local', 'private'],
		instructionsKey: 'app.runtime.instructions.llama_cpp',
	},
```

En `local_control_center/i18n/default_catalog.json`, con Edit (old_string = la entrada completa de `app.runtime.instructions.omniroute`, 4 líneas), agregar a continuación:

```json
    "app.runtime.instructions.llama_cpp": {
      "en": "Run llama-server with its OpenAI-compatible API (for example: llama-server -m model.gguf --port 8082), optionally protect it with --api-key and save that key as the credential, then sync models and test the runtime.",
      "es": "Levanta llama-server con su API compatible con OpenAI (por ejemplo: llama-server -m model.gguf --port 8082), opcionalmente protégelo con --api-key y guarda esa clave como credencial; luego sincroniza modelos y prueba el runtime."
    },
```

- [ ] **Step 4: Ejecutar tests, lint y gates web**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_llama_cpp_provider.py','tests_py/test_provider_setup_catalog.py','tests_py/test_all_provider_defaults.py','tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"`
Expected: PASS (el test de defaults es dinámico sobre `PROVIDER_CATALOG`, `team_scheduler/scheduler.py:217-233`).
Run: ruff format/check sobre los dos `.py` y el test; `corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/runtime-setup/runtimeSetup.ts`; `corepack pnpm@10.24.0 run typecheck:web`.
Expected: sin errores. `git diff --stat local_control_center/i18n/default_catalog.json` muestra solo `+4`.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/agents/provider_catalog.py local_control_center/agents/provider_catalog_api.py local-control-center/web/src/features/runtime-setup/runtimeSetup.ts local_control_center/i18n/default_catalog.json tests_py/test_llama_cpp_provider.py
git commit -m "Feature (Providers): llama.cpp como runtime local OpenAI-compatible con capacidades de build y review" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Operación encolada `models.validate_runtime`

**Files:**
- Create: `local_control_center/runtime_team/contracts.py`
- Modify: `local_control_center/agents/model_gateway_api.py` (imports `:22-29`; ruta nueva inmediatamente después del handler `test_prompt`, que termina en `:920` con `return {"test": result}`)
- Modify: `local_control_center/executions/workloads.py:11-21`
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_runtime_team_validate_api.py`

**Interfaces:**
- Consumes: `RuntimeValidationService` (Task 2), `queued_operation` (`executions/router.py:41`), helpers `auth_headers`, `create_client`, `enable_remote_provider` (`tests_py/test_provider_setup_catalog.py:97-124`).
- Produces: `RuntimeValidationRequest(projectId?: str)`, `RuntimeValidationResultRecord`, `RuntimeValidationResponse{validation}` en `runtime_team/contracts.py`; `POST /api/v1/model-gateway/providers/{provider_id}/validate-runtime` → 202 + ejecución; operationId esperado `validate_runtime_api_v1_model_gateway_providers__provider_id__validate_runtime_post`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_validate_api.py`:

```python
"""La prueba de runtime es una operación encolada por runtime con su propia clase de carga.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.base import ModelResponse, UsageRecord
from local_control_center.executions.router import OperationSpec
from local_control_center.executions.workloads import INFERENCE_OPERATIONS, operation_workload
from local_control_center.runtime_team import probe
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.test_provider_setup_catalog import auth_headers, create_client, enable_remote_provider


def _db():
    return closing(open_sqlite_connection(Path(os.environ["LOCAL_CONTROL_CENTER_DB"])))


def test_validate_runtime_is_an_inference_operation() -> None:
    assert "models.validate_runtime" in INFERENCE_OPERATIONS


def test_each_validation_reserves_the_workload_of_its_own_runtime(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        ProviderAccountStore(connection).patch_provider_account(
            "ollama", {"enabled": True, "baseUrl": "http://127.0.0.1:11434"}
        )
        spec = OperationSpec("models.validate_runtime", "remote_llm_light")
        assert operation_workload(connection, spec, {"provider_id": "codex_cli", "body": {}}) == "agent_cli"
        assert operation_workload(connection, spec, {"provider_id": "ollama", "body": {}}) == "local_gpu_model"


def test_validate_runtime_probes_a_model_provider_through_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client, headers, "deepseek", base_url="https://example.invalid/v1", credential_ref="env:AIDO_TEAM_TEST_KEY"
    )
    with _db() as connection, connection:
        ProviderAccountStore(connection).upsert_model(
            {"providerId": "deepseek", "model": "deepseek-chat", "enabled": True}
        )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: SimpleNamespace(
            chat_completion=lambda request: ModelResponse(
                providerId="deepseek", model=request.model, content="ok", usage=UsageRecord()
            )
        ),
    )
    response = client.post("/api/v1/model-gateway/providers/deepseek/validate-runtime", json={}, headers=headers)
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert validation["status"] == "validated"
    assert validation["model"]
    assert "unit-test-team-key" not in response.text


def test_validate_runtime_rejects_unknown_and_manual_runtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    unknown = client.post("/api/v1/model-gateway/providers/nope/validate-runtime", json={}, headers=headers)
    manual = client.post("/api/v1/model-gateway/providers/manual/validate-runtime", json={}, headers=headers)
    assert unknown.status_code == 404
    assert manual.status_code == 422


def test_cli_validation_without_a_project_is_deferred_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    with _db() as connection, connection:
        ProviderAccountStore(connection).patch_provider_account("codex_cli", {"enabled": True})
    response = client.post("/api/v1/model-gateway/providers/codex_cli/validate-runtime", json={}, headers=headers)
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert (validation["status"], validation["reason"]) == ("deferred", "project_required_for_cli_validation")
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_validate_api.py','-q','-p','no:randomly']))"`
Expected: FAIL: `assert 'models.validate_runtime' in INFERENCE_OPERATIONS` y 404/405 en las rutas.

- [ ] **Step 3: Implementación mínima**

`local_control_center/runtime_team/contracts.py`:

```python
"""Contratos HTTP del equipo de runtimes por hilo: prueba de runtime, candidatos y roles.

Literales acotados para que el cliente generado no degrade a ``string`` (drift de response_model).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeValidationOutcome = Literal["validated", "failed", "deferred"]


class RuntimeValidationRequest(BaseModel):
    """Cuerpo de la prueba: el proyecto es obligatorio para CLI (preflight con contexto de ejecución)."""

    project_id: str | None = Field(default=None, alias="projectId")


class RuntimeValidationResultRecord(BaseModel):
    """Resultado redactado de una prueba de ida y vuelta de un runtime."""

    provider_id: str = Field(alias="providerId")
    kind: str
    status: RuntimeValidationOutcome
    model: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    reason: str | None = None
    evidence: str | None = None
    checked_at: str = Field(alias="checkedAt")


class RuntimeValidationResponse(BaseModel):
    """Respuesta de ``models.validate_runtime``."""

    validation: RuntimeValidationResultRecord
```

En `local_control_center/agents/model_gateway_api.py`, después de `from local_control_center.runtime_integrations.repository import RuntimeConfigRepository` agregar:

```python
from local_control_center.runtime_team.contracts import RuntimeValidationRequest, RuntimeValidationResponse
from local_control_center.runtime_team.probe import RuntimeValidationService
```

y justo después del `return {"test": result}` del handler `test_prompt` agregar:

```python
    @router.post("/providers/{provider_id}/validate-runtime", response_model=RuntimeValidationResponse)
    @queued_operation("models.validate_runtime", workload_class="remote_llm_light")
    async def validate_runtime(
        provider_id: str, body: RuntimeValidationRequest, request: Request
    ) -> dict[str, Any]:
        """Prueba real de ida y vuelta de un runtime para el equipo del hilo (una operación por runtime).

        La clase de carga se reclasifica por runtime (CLI → agent_cli, loopback → local_gpu_model).
        Un CLI consume cuota de suscripción; el pedido del operador es la aprobación y queda auditado.
        """
        require_write(request)
        try:
            result = RuntimeValidationService(platform.connection).validate(
                provider_id, project_id=body.project_id
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"validation": result}
```

En `local_control_center/executions/workloads.py`, agregar `"models.validate_runtime",` después de `"models.test_prompt",`.

- [ ] **Step 4: Ejecutar tests, lint y regen**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_validate_api.py','tests_py/test_provider_setup_catalog.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run ruff format/check sobre `local_control_center/runtime_team/contracts.py local_control_center/agents/model_gateway_api.py local_control_center/executions/workloads.py tests_py/test_runtime_team_validate_api.py`.
Run: `corepack pnpm@10.24.0 run openapi:generate`, luego `rg -n "validate_runtime_api_v1_model_gateway_providers__provider_id__validate_runtime_post" local-control-center/web/src/api/generated/openapi.ts` (debe aparecer en `API_ENDPOINTS` y en `EXECUTION_OPERATIONS`) y `corepack pnpm@10.24.0 run typecheck:web`.
Expected: operationId presente; typecheck OK.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/runtime_team/contracts.py local_control_center/agents/model_gateway_api.py local_control_center/executions/workloads.py local-control-center/web/src/api/generated/openapi.ts tests_py/test_runtime_team_validate_api.py
git commit -m "Feature (ModelGateway): operación encolada models.validate_runtime por runtime con clase de carga propia" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Equipo del hilo: guardado, estrechamiento, readiness y sellado

**Files:**
- Create: `local_control_center/runtime_team/facts.py`
- Create: `local_control_center/runtime_team/configuration.py`
- Test: `tests_py/test_runtime_team_configuration.py`

**Interfaces:**
- Consumes: `RuntimeFacts` (incluido `policy_denied_reason`), `eligible_team_roles`, `TEAM_ROLES`, `REQUIRED_TEAM_ROLES` (= PO + Developer) (Task 1); `runtime_validation_state`, `RUNTIME_TEAM_FRESHNESS_SECONDS` (Task 2); `RuntimeStatusService.list_provider_statuses` (`agents/runtime_status.py:839`); `ProviderAccountStore.list_provider_accounts`; `RuntimeConfigRepository.runtime_policy_decision` (`runtime_integrations/repository.py:186`); `provider_account_policy_kind` (`agents/providers/factory.py:118`); `resolve_setting_value` (`settings/resolver.py:111`); `json_dumps/json_loads`, `utc_now`.
- Produces:
  - `facts.load_runtime_facts(connection, *, project_id) -> dict[str, RuntimeFacts]` (con `policy_denied_reason` por runtime según la política del proyecto)
  - `configuration.THREAD_RUN_CONFIGURATION_KEY = "runConfiguration"`, `RUNTIME_TEAM_METADATA_KEY = "runtimeTeam"`, `ALLOWED_RUNTIMES_KEY = "allowedRuntimes"`, `ROLE_RUNTIMES_KEY = "roleRuntimes"`
  - `read_thread_runtime_team(connection, thread_id) -> dict | None`
  - `write_thread_runtime_team(connection, *, thread_id, project_id, allowed_runtimes, role_runtimes, facts=None) -> dict | None` (`KeyError` si no hay hilo, `ValueError` si inválido o denegado por la política del proyecto; `facts` permite cargar `load_runtime_facts` fuera de la transacción del PATCH)
  - `narrow_runtime_team(connection, *, project_id, team) -> dict | None`
  - `RuntimeTeamReadiness(missing_roles, stale_runtimes)` con `.ready`, `.reason()`, `.details()`
  - `assess_runtime_team(connection, team, *, max_age_seconds, only_assigned=False) -> RuntimeTeamReadiness` (`only_assigned=True` = gate de ejecución: solo runtimes con rol)
  - `RuntimeTeamNotReadyError(ValueError)`; `ensure_thread_runtime_team_ready(connection, *, project_id, thread_id) -> None`
  - `seal_thread_runtime_team(connection, *, project_id, thread_id, metadata) -> dict`
  - `runtime_team_of(request_meta) -> dict | None`, `assigned_runtime(request_meta, team_role) -> str | None`, `role_allowlist(request_meta, team_role) -> list[str] | None`, `restrict_to_allowlist(provider_ids, allowlist) -> list[str]`

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_configuration.py`:

```python
"""Equipo de runtimes guardado en el hilo: validación, estrechamiento, sellado y readiness.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_team.configuration import (
    RUNTIME_TEAM_METADATA_KEY,
    RuntimeTeamNotReadyError,
    assess_runtime_team,
    assigned_runtime,
    ensure_thread_runtime_team_ready,
    read_thread_runtime_team,
    restrict_to_allowlist,
    role_allowlist,
    runtime_team_of,
    seal_thread_runtime_team,
    write_thread_runtime_team,
)
from local_control_center.runtime_team.validation import RUNTIME_TEAM_FRESHNESS_SECONDS
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.threads.repository import ThreadsRepository

ROLES = {"developer": "codex_cli", "product_owner": "ollama", "architect": "ollama", "security": "ollama"}


def grant_review_capability(connection, runtime_id: str) -> None:
    """Ollama solo es elegible para Seguridad si anuncia una capacidad de revisión (rol ``review``)."""
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """INSERT INTO runtime_capabilities (id, runtime, capability, enabled, metadata, created_at, updated_at)
           VALUES (?, ?, 'code_review', 1, '{}', ?, ?)
           ON CONFLICT(runtime, capability) DO UPDATE SET enabled = 1""",
        (f"{runtime_id}:code_review", runtime_id, now, now),
    )


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Runtime team", path=tmp_path / "project", template_id="other"
        )
        thread = ThreadsRepository(connection).create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Team"
        )
        store = ProviderAccountStore(connection)
        store.patch_provider_account("codex_cli", {"enabled": True})
        store.patch_provider_account("ollama", {"enabled": True})
        grant_review_capability(connection, "ollama")
        yield connection, project, thread


def _save(connection, project, thread, roles=ROLES):
    return write_thread_runtime_team(
        connection,
        thread_id=thread["id"],
        project_id=project["id"],
        allowed_runtimes=["codex_cli", "ollama"],
        role_runtimes=roles,
    )


def _validate(connection, provider_id: str, model: str, minutes_ago: int = 1) -> None:
    started = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="microseconds")
    record_model_execution(connection, provider_id, model, True, "test_prompt", started_at=started)


def test_a_saved_team_is_read_back_and_keeps_the_remembered_team_mode(lane):
    connection, project, thread = lane
    connection.execute(
        "UPDATE project_threads SET metadata = ? WHERE id = ?",
        (json_dumps({"runConfiguration": {"teamMode": "critical"}}), thread["id"]),
    )
    saved = _save(connection, project, thread)
    assert saved == {"allowedRuntimes": ["codex_cli", "ollama"], "roleRuntimes": ROLES}
    assert read_thread_runtime_team(connection, thread["id"]) == saved
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread["id"],)).fetchone()
    assert json_loads(row["metadata"], {})["runConfiguration"]["teamMode"] == "critical"


def test_an_empty_selection_clears_the_team(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    cleared = write_thread_runtime_team(
        connection, thread_id=thread["id"], project_id=project["id"], allowed_runtimes=[], role_runtimes={}
    )
    assert cleared is None
    assert read_thread_runtime_team(connection, thread["id"]) is None


@pytest.mark.parametrize(
    ("allowed", "roles", "message"),
    [
        (["claude_code_cli"], {}, "claude_code_cli"),
        (["codex_cli"], {"product_owner": "ollama"}, "not selected"),
        (["codex_cli", "ollama"], {"security": "codex_cli"}, "not eligible"),
    ],
)
def test_an_invalid_team_is_rejected(lane, allowed, roles, message):
    connection, project, thread = lane
    with pytest.raises(ValueError, match=message):
        write_thread_runtime_team(
            connection,
            thread_id=thread["id"],
            project_id=project["id"],
            allowed_runtimes=allowed,
            role_runtimes=roles,
        )


def test_an_unknown_thread_is_a_key_error(lane):
    connection, project, _thread = lane
    with pytest.raises(KeyError):
        write_thread_runtime_team(
            connection, thread_id="thread-missing", project_id=project["id"], allowed_runtimes=[], role_runtimes={}
        )


def test_the_project_allowlist_can_only_narrow_the_sealed_team(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    SettingsRepository(connection).set_value(
        "project.runtime.allowedProviders", "project", project["id"], ["codex_cli"]
    )
    sealed = seal_thread_runtime_team(
        connection,
        project_id=project["id"],
        thread_id=thread["id"],
        metadata={RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": ["claude_code_cli"]}},
    )
    assert sealed[RUNTIME_TEAM_METADATA_KEY] == {
        "allowedRuntimes": ["codex_cli"],
        "roleRuntimes": {"developer": "codex_cli"},
    }


def test_sealing_without_a_team_drops_any_forged_value(lane):
    connection, project, thread = lane
    sealed = seal_thread_runtime_team(
        connection,
        project_id=project["id"],
        thread_id=thread["id"],
        metadata={RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": ["claude_code_cli"]}, "teamMode": "economy"},
    )
    assert sealed == {"teamMode": "economy"}


def test_the_send_gate_requires_a_fresh_validation_of_every_selected_runtime(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    with pytest.raises(RuntimeTeamNotReadyError, match="codex_cli"):
        ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default", minutes_ago=31)
    with pytest.raises(RuntimeTeamNotReadyError, match="ollama"):
        ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])
    _validate(connection, "ollama", "local_default")
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])


def test_a_missing_required_role_blocks_even_with_fresh_runtimes(lane):
    connection, project, thread = lane
    _save(connection, project, thread, roles={"developer": "codex_cli"})
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "local_default")
    readiness = assess_runtime_team(
        connection,
        read_thread_runtime_team(connection, thread["id"]),
        max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
    )
    assert readiness.missing_roles == ("product_owner",)
    assert readiness.ready is False
    assert readiness.details()["runtimeIds"] == []


def test_a_single_runtime_covering_po_and_developer_can_send(lane):
    connection, project, thread = lane
    write_thread_runtime_team(
        connection,
        thread_id=thread["id"],
        project_id=project["id"],
        allowed_runtimes=["codex_cli"],
        role_runtimes={"developer": "codex_cli", "product_owner": "codex_cli"},
    )
    _validate(connection, "codex_cli", "gpt-5.5")
    readiness = assess_runtime_team(
        connection,
        read_thread_runtime_team(connection, thread["id"]),
        max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS,
    )
    assert readiness.missing_roles == ()
    assert readiness.ready is True
    ensure_thread_runtime_team_ready(connection, project_id=project["id"], thread_id=thread["id"])


def test_a_runtime_denied_by_the_project_policy_cannot_be_selected(lane):
    connection, project, thread = lane
    SettingsRepository(connection).set_value(
        "project.runtime.allowedProviders", "project", project["id"], ["ollama"]
    )
    with pytest.raises(ValueError, match="denied by the project runtime policy: codex_cli"):
        _save(connection, project, thread)
    assert read_thread_runtime_team(connection, thread["id"]) is None


def test_the_execution_scope_only_checks_runtimes_with_a_role(lane):
    connection, _project, _thread = lane
    team = {
        "allowedRuntimes": ["codex_cli", "ollama"],
        "roleRuntimes": {role: "codex_cli" for role in ("product_owner", "developer", "architect", "security")},
    }
    _validate(connection, "codex_cli", "gpt-5.5")
    selected = assess_runtime_team(connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS)
    assigned = assess_runtime_team(
        connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS, only_assigned=True
    )
    assert selected.details()["runtimeIds"] == ["ollama"]
    assert assigned.ready is True


def test_request_meta_helpers_scope_each_role():
    meta = {RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": ["codex_cli", "ollama"], "roleRuntimes": {"developer": "codex_cli"}}}
    assert runtime_team_of({}) is None
    assert role_allowlist({}, "developer") is None
    assert role_allowlist(meta, "developer") == ["codex_cli"]
    assert role_allowlist(meta, "architect") == ["codex_cli", "ollama"]
    assert role_allowlist(meta, None) == ["codex_cli", "ollama"]
    assert assigned_runtime(meta, "security") is None
    assert restrict_to_allowlist(["claude_code_cli", "codex_cli", "ollama"], ["ollama"]) == ["ollama"]
    assert restrict_to_allowlist(["codex_cli"], None) == ["codex_cli"]
    narrowed_to_nothing = {RUNTIME_TEAM_METADATA_KEY: {"allowedRuntimes": [], "roleRuntimes": {}}}
    assert role_allowlist(narrowed_to_nothing, "developer") == []
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_configuration.py','-q','-p','no:randomly']))"`
Expected: FAIL con `ModuleNotFoundError: No module named 'local_control_center.runtime_team.configuration'`.

- [ ] **Step 3: Implementación mínima**

`local_control_center/runtime_team/facts.py`:

```python
"""Hechos por runtime configurado para el equipo del hilo: tipo, etiqueta y roles elegibles.

Las capacidades salen del mismo estado de runtime que usa la selección de recursos, sin sondas:
el panel y la validación del PATCH ven exactamente lo que verá el loop. La política de runtimes del
proyecto (``runtime_policy_decision``) se evalúa aquí para que un runtime denegado nunca sea
seleccionable en ese proyecto.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.agents.providers.factory import provider_account_policy_kind
from local_control_center.agents.runtime_status import RuntimeStatusService
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .roles import RuntimeFacts, eligible_team_roles

TEAM_RUNTIME_KINDS = frozenset({"cli", "api", "gateway", "local"})


def load_runtime_facts(connection: sqlite3.Connection, *, project_id: str | None) -> dict[str, RuntimeFacts]:
    """Devuelve los runtimes habilitados y no manuales con roles elegibles y veto de política, por id."""
    statuses = {
        str(status.get("id") or ""): status
        for status in RuntimeStatusService(connection).list_provider_statuses(project_id=project_id)
    }
    policy = RuntimeConfigRepository(connection)
    facts: dict[str, RuntimeFacts] = {}
    for account in ProviderAccountStore(connection).list_provider_accounts():
        provider_id = str(account["providerId"])
        kind = str(account.get("providerType") or "")
        if not account.get("enabled") or kind not in TEAM_RUNTIME_KINDS:
            continue
        status = statuses.get(provider_id) or {}
        runtime = {
            **status,
            "id": provider_id,
            "providerFamily": status.get("providerFamily") or account.get("providerFamily"),
            "capabilities": status.get("capabilities") or [],
        }
        decision = policy.runtime_policy_decision(
            provider_id=provider_id,
            kind=provider_account_policy_kind(account),
            project_id=project_id,
            account=account,
        )
        facts[provider_id] = RuntimeFacts(
            provider_id=provider_id,
            label=str(status.get("displayName") or account.get("displayName") or provider_id),
            kind=kind,
            eligible_roles=eligible_team_roles(runtime),
            policy_denied_reason=None
            if decision.get("allowed")
            else str(decision.get("reason") or "Runtime policy denies this provider."),
        )
    return facts
```

`local_control_center/runtime_team/configuration.py`:

```python
"""Equipo de runtimes del hilo: persistencia en ``runConfiguration``, sellado y readiness.

El operador guarda ``allowedRuntimes`` y ``roleRuntimes`` en el hilo (mismo patrón que ``teamMode``).
Al sellar un mensaje, la metadata del run recibe ``runtimeTeam`` estrechado por
``project.runtime.allowedProviders``: la política del proyecto solo restringe, nunca amplía, y un
valor entrante del cliente se descarta siempre. Un equipo estrechado a vacío se conserva vacío para
fallar cerrado en el loop en vez de volver al ruteo automático.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

from .facts import load_runtime_facts
from .roles import REQUIRED_TEAM_ROLES, TEAM_ROLES, RuntimeFacts
from .validation import RUNTIME_TEAM_FRESHNESS_SECONDS, runtime_validation_state

THREAD_RUN_CONFIGURATION_KEY = "runConfiguration"
"""Clave dentro de `project_threads.metadata` donde el hilo recuerda lo que el operador eligió."""
RUNTIME_TEAM_METADATA_KEY = "runtimeTeam"
"""Clave sellada en la metadata del run con el equipo efectivo; nunca se acepta del cliente."""
ALLOWED_RUNTIMES_KEY = "allowedRuntimes"
ROLE_RUNTIMES_KEY = "roleRuntimes"


class RuntimeTeamNotReadyError(ValueError):
    """El equipo del hilo no puede ejecutar: rol obligatorio sin runtime o runtime sin validación fresca."""


@dataclass(frozen=True)
class RuntimeTeamReadiness:
    """Resultado de revisar un equipo contra la evidencia de validación vigente."""

    missing_roles: tuple[str, ...]
    stale_runtimes: tuple[dict[str, str], ...]

    @property
    def ready(self) -> bool:
        """Verdadero si no faltan roles obligatorios ni hay runtimes sin validación fresca."""
        return not self.missing_roles and not self.stale_runtimes

    def reason(self) -> str:
        """Causa legible para el 422 del envío o para el bloqueo del loop."""
        parts: list[str] = []
        if self.missing_roles:
            parts.append(f"Roles without an assigned runtime: {', '.join(self.missing_roles)}.")
        if self.stale_runtimes:
            listed = ", ".join(f"{item['providerId']} ({item['reason']})" for item in self.stale_runtimes)
            parts.append(f"Runtimes without a fresh validation: {listed}.")
        return " ".join(parts)

    def details(self) -> dict[str, Any]:
        """Evidencia estructurada para la remediación (ids a re-probar y roles faltantes)."""
        return {
            "missingRoles": list(self.missing_roles),
            "staleRuntimes": [dict(item) for item in self.stale_runtimes],
            "runtimeIds": [item["providerId"] for item in self.stale_runtimes],
        }


def _clean_ids(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip()))


def _team_from(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or ALLOWED_RUNTIMES_KEY not in raw:
        return None
    allowed = _clean_ids(raw.get(ALLOWED_RUNTIMES_KEY) or [])
    roles_raw = raw.get(ROLE_RUNTIMES_KEY) if isinstance(raw.get(ROLE_RUNTIMES_KEY), dict) else {}
    roles = {
        role: str(roles_raw[role]).strip()
        for role in TEAM_ROLES
        if str(roles_raw.get(role) or "").strip() in allowed
    }
    return {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}


def _thread_metadata(connection: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT metadata FROM project_threads WHERE id = ?", (thread_id,)).fetchone()
    return None if row is None else json_loads(row["metadata"], {})


def read_thread_runtime_team(connection: sqlite3.Connection, thread_id: str | None) -> dict[str, Any] | None:
    """Equipo guardado en el hilo, o ``None`` si el hilo usa el ruteo automático."""
    if not thread_id:
        return None
    configuration = (_thread_metadata(connection, thread_id) or {}).get(THREAD_RUN_CONFIGURATION_KEY) or {}
    team = _team_from(configuration)
    return team if team and team[ALLOWED_RUNTIMES_KEY] else None


def write_thread_runtime_team(
    connection: sqlite3.Connection,
    *,
    thread_id: str,
    project_id: str,
    allowed_runtimes: Iterable[str],
    role_runtimes: Mapping[str, str],
    facts: Mapping[str, RuntimeFacts] | None = None,
) -> dict[str, Any] | None:
    """Guarda (o limpia con una lista vacía) el equipo del hilo tras validarlo contra la configuración.

    ``facts`` evita recalcular el estado de runtimes dentro de la transacción del llamador.

    Raises:
        KeyError: el hilo no existe.
        ValueError: un runtime no está habilitado o la política del proyecto lo deniega, o un rol
            apunta fuera del conjunto o a un runtime que el contrato de ese rol no acepta.
    """
    metadata = _thread_metadata(connection, thread_id)
    if metadata is None:
        raise KeyError(f"Thread not found: {thread_id}")
    allowed = _clean_ids(allowed_runtimes)
    configuration = dict(metadata.get(THREAD_RUN_CONFIGURATION_KEY) or {})
    team: dict[str, Any] | None = None
    if allowed:
        facts = facts if facts is not None else load_runtime_facts(connection, project_id=project_id)
        unknown = [provider_id for provider_id in allowed if provider_id not in facts]
        if unknown:
            raise ValueError(f"Runtimes are not enabled for a thread team: {', '.join(unknown)}.")
        denied = [
            f"{provider_id} ({facts[provider_id].policy_denied_reason})"
            for provider_id in allowed
            if facts[provider_id].policy_denied_reason
        ]
        if denied:
            raise ValueError(f"Runtimes are denied by the project runtime policy: {', '.join(denied)}.")
        roles: dict[str, str] = {}
        for role, provider_id in role_runtimes.items():
            clean = str(provider_id or "").strip()
            if not clean:
                continue
            if role not in TEAM_ROLES:
                raise ValueError(f"Unknown team role: {role}.")
            if clean not in allowed:
                raise ValueError(f"Role {role} uses {clean}, which is not selected for this thread.")
            if role not in facts[clean].eligible_roles:
                raise ValueError(f"Runtime {clean} is not eligible for role {role}.")
            roles[role] = clean
        team = {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}
        configuration.update(team)
    else:
        configuration.pop(ALLOWED_RUNTIMES_KEY, None)
        configuration.pop(ROLE_RUNTIMES_KEY, None)
    metadata[THREAD_RUN_CONFIGURATION_KEY] = configuration
    connection.execute(
        "UPDATE project_threads SET metadata = ?, updated_at = ? WHERE id = ?",
        (json_dumps(metadata), utc_now(), thread_id),
    )
    return team


def narrow_runtime_team(
    connection: sqlite3.Connection, *, project_id: str, team: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Intersecta con ``project.runtime.allowedProviders``; una lista vacía del proyecto no restringe."""
    if team is None:
        return None
    project_allowed = set(
        _clean_ids(
            resolve_setting_value(
                connection=connection, key="project.runtime.allowedProviders", project_id=project_id
            )
            or []
        )
    )
    if not project_allowed:
        return team
    allowed = [provider_id for provider_id in team[ALLOWED_RUNTIMES_KEY] if provider_id in project_allowed]
    roles = {role: provider_id for role, provider_id in team[ROLE_RUNTIMES_KEY].items() if provider_id in allowed}
    return {ALLOWED_RUNTIMES_KEY: allowed, ROLE_RUNTIMES_KEY: roles}


def assess_runtime_team(
    connection: sqlite3.Connection,
    team: dict[str, Any],
    *,
    max_age_seconds: int,
    only_assigned: bool = False,
) -> RuntimeTeamReadiness:
    """Revisa roles obligatorios (PO y Developer) y la validación de los runtimes dentro de la ventana.

    Sin ``only_assigned`` revisa todo el conjunto seleccionado (gate de envío, spec §3.4); con él,
    solo los runtimes que tienen un rol asignado (gate de ejecución, spec §1.4).
    """
    roles = team.get(ROLE_RUNTIMES_KEY) or {}
    missing = tuple(role for role in REQUIRED_TEAM_ROLES if not roles.get(role))
    runtime_ids = (
        list(dict.fromkeys(roles[role] for role in TEAM_ROLES if roles.get(role)))
        if only_assigned
        else list(team.get(ALLOWED_RUNTIMES_KEY) or [])
    )
    stale: list[dict[str, str]] = []
    for provider_id in runtime_ids:
        state = runtime_validation_state(connection, provider_id, max_age_seconds=max_age_seconds)
        if state.status != "validated":
            stale.append({"providerId": provider_id, "status": state.status, "reason": state.reason or state.status})
    return RuntimeTeamReadiness(missing_roles=missing, stale_runtimes=tuple(stale))


def ensure_thread_runtime_team_ready(connection: sqlite3.Connection, *, project_id: str, thread_id: str) -> None:
    """Gate de envío: exige roles obligatorios y validación de 30 min de cada runtime del conjunto.

    Raises:
        RuntimeTeamNotReadyError: con la causa legible; no se escribe nada antes de este chequeo.
    """
    team = narrow_runtime_team(
        connection, project_id=project_id, team=read_thread_runtime_team(connection, thread_id)
    )
    if team is None:
        return
    readiness = assess_runtime_team(connection, team, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS)
    if not readiness.ready:
        raise RuntimeTeamNotReadyError(readiness.reason())


def seal_thread_runtime_team(
    connection: sqlite3.Connection, *, project_id: str, thread_id: str | None, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Sella en la metadata del run el equipo efectivo del hilo; descarta cualquier valor entrante."""
    stamped = dict(metadata)
    stamped.pop(RUNTIME_TEAM_METADATA_KEY, None)
    team = narrow_runtime_team(
        connection, project_id=project_id, team=read_thread_runtime_team(connection, thread_id)
    )
    if team is not None:
        stamped[RUNTIME_TEAM_METADATA_KEY] = team
    return stamped


def runtime_team_of(request_meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Equipo sellado en la metadata del run, o ``None`` cuando el hilo usa ruteo automático."""
    return _team_from((request_meta or {}).get(RUNTIME_TEAM_METADATA_KEY))


def assigned_runtime(request_meta: Mapping[str, Any] | None, team_role: str) -> str | None:
    """Runtime asignado al rol del equipo en el run sellado, si lo hay."""
    team = runtime_team_of(request_meta)
    return team[ROLE_RUNTIMES_KEY].get(team_role) if team else None


def role_allowlist(request_meta: Mapping[str, Any] | None, team_role: str | None) -> list[str] | None:
    """Allowlist dura para ``AIResourceRequest.allowed_provider_ids``.

    ``None`` sin equipo (comportamiento previo); un proveedor único para un rol asignado; el
    conjunto completo del hilo para roles sin asignación propia.
    """
    team = runtime_team_of(request_meta)
    if team is None:
        return None
    assigned = team[ROLE_RUNTIMES_KEY].get(team_role) if team_role else None
    return [assigned] if assigned else list(team[ALLOWED_RUNTIMES_KEY])


def restrict_to_allowlist(provider_ids: Iterable[str], allowlist: list[str] | None) -> list[str]:
    """Filtra ``provider_ids`` por la allowlist del hilo, conservando el orden original."""
    ids = list(provider_ids)
    if allowlist is None:
        return ids
    allowed = set(allowlist)
    return [provider_id for provider_id in ids if provider_id in allowed]
```

- [ ] **Step 4: Ejecutar tests y lint**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_configuration.py','-q','-p','no:randomly']))"`
Expected: PASS (14 tests).
Run ruff format/check sobre `local_control_center/runtime_team tests_py/test_runtime_team_configuration.py`. Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/runtime_team/facts.py local_control_center/runtime_team/configuration.py tests_py/test_runtime_team_configuration.py
git commit -m "Feature (RuntimeTeam): equipo por hilo en runConfiguration con estrechamiento por proyecto, readiness y sellado" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Endpoint `GET /api/v1/runtime/team-candidates`

**Files:**
- Create: `local_control_center/runtime_team/candidates.py`
- Modify: `local_control_center/runtime_team/contracts.py` (append)
- Modify: `local_control_center/agents/api.py` (import junto a `:19-21`; ruta después de `list_runtime_providers`, `:1255-1258`)
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_runtime_team_candidates_api.py`

**Interfaces:**
- Consumes: `load_runtime_facts` con `RuntimeFacts.policy_denied_reason` (Task 5), `runtime_validation_state`, `RUNTIME_TEAM_FRESHNESS_SECONDS` (Task 2), `auto_assign_roles` (Task 1), `RuntimeConfigRepository.get_preferences` (`runtime_integrations/repository.py:620`, lanza `KeyError` sin fila).
- Produces: `RuntimeTeamCandidatesService(connection).list_candidates(*, project_id, selected: list[str] | None) -> dict` (runtime denegado por la política del proyecto ⇒ `validation.status = "policy_denied"` con la causa en `reason`, fuera del reparto); modelos `RuntimeTeamValidationRecord`, `RuntimeTeamCandidateRecord`, `RoleRuntimesRecord`, `RuntimeTeamCandidatesResponse`; operationId esperado `list_runtime_team_candidates_api_v1_runtime_team_candidates_get`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_candidates_api.py`:

```python
"""Candidatos del equipo de runtimes: validación reciente, roles elegibles y reparto sugerido.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.settings.repository import SettingsRepository
from tests_py.execution_client import CompletedExecutionClient as TestClient

pytestmark = pytest.mark.usefixtures("controlled_domain_host")


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(create_app(runtime=runtime, static_dir=None))


def _prepare(runtime, tmp_path: Path) -> str:
    project = ProjectsRepository(runtime.connection).create_project(
        name="Team candidates", path=tmp_path / "project", template_id="other"
    )
    store = ProviderAccountStore(runtime.connection)
    store.patch_provider_account("codex_cli", {"enabled": True})
    store.patch_provider_account("ollama", {"enabled": True})
    record_model_execution(runtime.connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    return project["id"]


def test_candidates_report_validation_roles_and_the_backend_split(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        response = client.get("/api/v1/runtime/team-candidates", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        by_id = {item["providerId"]: item for item in body["candidates"]}
        assert by_id["codex_cli"]["validation"]["status"] == "validated"
        assert by_id["codex_cli"]["eligibleRoles"] == ["product_owner", "developer"]
        assert by_id["codex_cli"]["kind"] == "cli"
        assert by_id["ollama"]["validation"]["status"] == "never"
        assert "manual" not in by_id
        assert body["freshnessSeconds"] == 1800
        assert body["suggestedRoleRuntimes"]["developer"] == "codex_cli"
        assert body["suggestedRoleRuntimes"]["product_owner"] == "codex_cli"
        assert body["suggestedRoleRuntimes"]["architect"] is None
    finally:
        runtime.close()


def test_the_split_only_uses_selected_and_validated_runtimes(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        response = client.get(
            "/api/v1/runtime/team-candidates", params={"projectId": project_id, "selected": "ollama"}
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["suggestedRoleRuntimes"].values()) == {None}
        empty = client.get("/api/v1/runtime/team-candidates", params={"projectId": project_id, "selected": ""})
        assert set(empty.json()["suggestedRoleRuntimes"].values()) == {None}
    finally:
        runtime.close()


def test_a_runtime_denied_by_the_project_policy_is_flagged_and_never_suggested(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        SettingsRepository(runtime.connection).set_value(
            "project.runtime.allowedProviders", "project", project_id, ["ollama"]
        )
        response = client.get("/api/v1/runtime/team-candidates", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        codex = next(item for item in body["candidates"] if item["providerId"] == "codex_cli")
        assert codex["validation"]["status"] == "policy_denied"
        assert "allowedProviders" in codex["validation"]["reason"]
        assert set(body["suggestedRoleRuntimes"].values()) == {None}
    finally:
        runtime.close()
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_candidates_api.py','-q','-p','no:randomly']))"`
Expected: FAIL con `assert 404 == 200`.

- [ ] **Step 3: Implementación mínima**

Agregar al final de `local_control_center/runtime_team/contracts.py` (y cambiar el import a `from pydantic import BaseModel, ConfigDict, Field`):

```python
RuntimeTeamRole = Literal["product_owner", "developer", "architect", "security"]
RuntimeValidationStatus = Literal["validated", "stale", "failed", "never", "policy_denied"]
RuntimeTeamKind = Literal["cli", "api", "gateway", "local"]


class RuntimeTeamValidationRecord(BaseModel):
    """Estado de validación reciente de un runtime; ``policy_denied`` si el proyecto lo veta."""

    status: RuntimeValidationStatus
    checked_at: str | None = Field(default=None, alias="checkedAt")
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    model: str | None = None
    reason: str | None = None


class RuntimeTeamCandidateRecord(BaseModel):
    """Runtime configurado y habilitado que el operador puede sumar al equipo del hilo."""

    provider_id: str = Field(alias="providerId")
    label: str
    kind: RuntimeTeamKind
    validation: RuntimeTeamValidationRecord
    eligible_roles: list[RuntimeTeamRole] = Field(alias="eligibleRoles")


class RoleRuntimesRecord(BaseModel):
    """Runtime asignado por rol del equipo; un rol ausente queda sin asignación."""

    model_config = ConfigDict(extra="forbid")

    product_owner: str | None = None
    developer: str | None = None
    architect: str | None = None
    security: str | None = None


class RuntimeTeamCandidatesResponse(BaseModel):
    """Candidatos del equipo con la ventana de frescura y el reparto sugerido por el backend."""

    candidates: list[RuntimeTeamCandidateRecord]
    freshness_seconds: int = Field(alias="freshnessSeconds")
    suggested_role_runtimes: RoleRuntimesRecord = Field(alias="suggestedRoleRuntimes")
```

`local_control_center/runtime_team/candidates.py`:

```python
"""Lectura de candidatos para el panel del equipo del hilo con el reparto calculado en backend.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository

from .facts import load_runtime_facts
from .roles import RuntimeFacts, auto_assign_roles
from .validation import RUNTIME_TEAM_FRESHNESS_SECONDS, RuntimeValidationState, runtime_validation_state


def _validation_record(item: RuntimeFacts, state: RuntimeValidationState) -> dict[str, Any]:
    """La política del proyecto prevalece sobre la evidencia: un runtime vetado nunca se ofrece."""
    if item.policy_denied_reason:
        return {
            "status": "policy_denied",
            "checkedAt": None,
            "latencyMs": None,
            "model": None,
            "reason": item.policy_denied_reason,
        }
    return state.to_record()


class RuntimeTeamCandidatesService:
    """Arma la respuesta de candidatos: validación de 30 min, veto de política, roles y reparto."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_candidates(self, *, project_id: str | None, selected: list[str] | None) -> dict[str, Any]:
        """Lista runtimes habilitados; el reparto usa ``selected`` (o todos) filtrado a los validados."""
        facts = load_runtime_facts(self.connection, project_id=project_id)
        states = {
            provider_id: runtime_validation_state(
                self.connection, provider_id, max_age_seconds=RUNTIME_TEAM_FRESHNESS_SECONDS
            )
            for provider_id in facts
        }
        ordered = sorted(facts.values(), key=lambda item: (item.kind != "cli", item.label.lower(), item.provider_id))
        pool_ids = set(facts) if selected is None else set(selected)
        pool = [
            item
            for item in ordered
            if item.provider_id in pool_ids
            and not item.policy_denied_reason
            and states[item.provider_id].status == "validated"
        ]
        return {
            "candidates": [
                {
                    "providerId": item.provider_id,
                    "label": item.label,
                    "kind": item.kind,
                    "validation": _validation_record(item, states[item.provider_id]),
                    "eligibleRoles": list(item.eligible_roles),
                }
                for item in ordered
            ],
            "freshnessSeconds": RUNTIME_TEAM_FRESHNESS_SECONDS,
            "suggestedRoleRuntimes": auto_assign_roles(pool, self._runtime_order()),
        }

    def _runtime_order(self) -> list[str]:
        try:
            preferences = RuntimeConfigRepository(self.connection).get_preferences()
        except KeyError:
            return []
        return [str(item) for item in preferences.get("runtimeOrder") or [] if str(item).strip()]
```

En `local_control_center/agents/api.py`, después de `from local_control_center.executions.router import ExecutionRouter, queued_operation` agregar:

```python
from local_control_center.runtime_team.candidates import RuntimeTeamCandidatesService
from local_control_center.runtime_team.contracts import RuntimeTeamCandidatesResponse
```

y después del handler `list_runtime_providers` (`:1255-1258`):

```python
    @router.get("/api/v1/runtime/team-candidates", response_model=RuntimeTeamCandidatesResponse)
    def list_runtime_team_candidates(projectId: str | None = None, selected: str | None = None) -> dict[str, Any]:
        """Runtimes para el equipo del hilo: validación de 30 min, veto de política, roles y reparto.

        ``selected`` (ids separados por coma) acota el reparto a la selección actual del panel.
        """
        selected_ids = (
            None if selected is None else [item.strip() for item in selected.split(",") if item.strip()]
        )
        return RuntimeTeamCandidatesService(platform.connection).list_candidates(
            project_id=projectId, selected=selected_ids
        )
```

- [ ] **Step 4: Ejecutar tests, lint y regen**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_candidates_api.py','tests_py/test_runtime_team_roles.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run ruff format/check sobre los `.py` tocados; `corepack pnpm@10.24.0 run openapi:generate`; `rg -n "list_runtime_team_candidates_api_v1_runtime_team_candidates_get" local-control-center/web/src/api/generated/openapi.ts`; `corepack pnpm@10.24.0 run typecheck:web`.
Expected: operationId presente; typecheck OK.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/runtime_team/candidates.py local_control_center/runtime_team/contracts.py local_control_center/agents/api.py local-control-center/web/src/api/generated/openapi.ts tests_py/test_runtime_team_candidates_api.py
git commit -m "Feature (RuntimeTeam): candidatos del equipo con validación reciente, roles elegibles y reparto sugerido" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: PATCH run-configuration, sellado del run y gate de envío

**Files:**
- Modify: `local_control_center/threads/contracts.py` (import `:17`; clases nuevas después de `ThreadUpdateRequest`, `:198-201`)
- Modify: `local_control_center/threads/api.py` (imports `:19-48`; ruta nueva antes de `@router.get("/api/v1/threads/similar", ...)`, `:165`)
- Modify: `local_control_center/threads/coordinator.py` (imports `:38`; clave protegida `:60-78`; gate como primera sentencia del `with immediate_transaction(self.connection):` de `post_message`, `:152-153`)
- Modify: `local_control_center/product_loop/metadata.py` (imports `:11`; eliminar `:34-35`; retorno de `seal_operator_cost_decision`, `:109`)
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_runtime_team_thread_api.py`

**Interfaces:**
- Consumes: `write_thread_runtime_team`, `ensure_thread_runtime_team_ready`, `seal_thread_runtime_team`, `RUNTIME_TEAM_METADATA_KEY`, `THREAD_RUN_CONFIGURATION_KEY` (Task 5); `load_runtime_facts` (Task 5); `RoleRuntimesRecord` (Task 6); `ACTIVE_EXECUTION_STATUSES` (`threads/coordinator.py:57`); `ThreadsRepository.record_event` (`threads/repository.py:560`, reusa la transacción del llamador, `threads/repository.py:147-150`); `immediate_transaction` (`shared/db.py:74`).
- Produces: `ThreadRunConfigurationRequest{allowedRuntimes, roleRuntimes}`, `ThreadRuntimeTeamRecord`, `ThreadRunConfigurationResponse{threadId, runtimeTeam}`; `PATCH /api/v1/threads/{thread_id}/run-configuration` (409 si `queued`/`running`, 422 inválido, 404 sin hilo); evento `run_configuration_updated`; `runMetadata.runtimeTeam` sellado en el job `thread.product_loop.run`; `POST /threads/{id}/messages` → 422 sin escribir nada cuando el equipo no está listo; operationId esperado `update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_thread_api.py`:

```python
"""El hilo guarda su equipo de runtimes, el envío lo exige fresco y el run lo recibe sellado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.threads import api as threads_api
from local_control_center.threads import coordinator as threads_coordinator
from local_control_center.threads.repository import ThreadsRepository
from tests_py.execution_client import CompletedExecutionClient as TestClient
from tests_py.test_runtime_team_configuration import grant_review_capability

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

ROLES = {"developer": "codex_cli", "product_owner": "ollama", "architect": "ollama", "security": "ollama"}
TEAM = {"allowedRuntimes": ["codex_cli", "ollama"], "roleRuntimes": ROLES}
OBJECTIVE = "Add a new dashboard endpoint to list active workspaces."


def _client(tmp_path: Path):
    sys.modules["faiss"] = None
    from local_control_center.app import create_app
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    return runtime, TestClient(create_app(runtime=runtime, static_dir=None))


def _setup(runtime, client, tmp_path: Path) -> tuple[dict[str, str], dict]:
    headers = {"X-Local-Control-Token": runtime.get_handshake()["token"]}
    project = ProjectsRepository(runtime.connection).create_project(
        name="Team thread", path=tmp_path / "threads", template_id="other"
    )
    store = ProviderAccountStore(runtime.connection)
    store.patch_provider_account("codex_cli", {"enabled": True})
    store.patch_provider_account("ollama", {"enabled": True})
    grant_review_capability(runtime.connection, "ollama")
    response = client.post(
        "/api/v1/threads",
        headers=headers,
        json={"projectId": project["id"], "ownerType": "workspace", "ownerId": "workspace-1", "title": "Team"},
    )
    assert response.status_code == 201, response.text
    return headers, response.json()["thread"]


def _validate_team(runtime) -> None:
    record_model_execution(runtime.connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    record_model_execution(runtime.connection, "ollama", "local_default", True, "test_prompt")


def _product_loop_jobs(runtime) -> int:
    return runtime.connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'thread.product_loop.run'"
    ).fetchone()[0]


def test_patch_persists_the_team_and_records_an_event(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        response = client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        assert response.status_code == 200, response.text
        assert response.json()["runtimeTeam"]["roleRuntimes"]["developer"] == "codex_cli"
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert detail["thread"]["metadata"]["runConfiguration"]["allowedRuntimes"] == ["codex_cli", "ollama"]
        assert "run_configuration_updated" in [event["type"] for event in detail["events"]]
    finally:
        runtime.close()


def test_patch_rejects_an_ineligible_role_and_a_busy_thread(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        url = f"/api/v1/threads/{thread['id']}/run-configuration"
        invalid = client.patch(
            url, headers=headers, json={"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"security": "codex_cli"}}
        )
        assert invalid.status_code == 422
        unknown_role = client.patch(
            url, headers=headers, json={"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"qa": "codex_cli"}}
        )
        assert unknown_role.status_code == 422
        ThreadsRepository(runtime.connection).set_status(thread["id"], "queued")
        busy = client.patch(url, headers=headers, json=TEAM)
        assert busy.status_code == 409
        detail = client.get(f"/api/v1/threads/{thread['id']}").json()
        assert "allowedRuntimes" not in (detail["thread"]["metadata"].get("runConfiguration") or {})
        assert "run_configuration_updated" not in [event["type"] for event in detail["events"]]
    finally:
        runtime.close()


def test_the_patch_checks_status_and_writes_inside_one_transaction(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        original = threads_api.write_thread_runtime_team
        seen: list[bool] = []

        def spy(connection, **kwargs):
            seen.append(connection.in_transaction)
            return original(connection, **kwargs)

        monkeypatch.setattr(threads_api, "write_thread_runtime_team", spy)
        response = client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        assert response.status_code == 200, response.text
        assert seen == [True]
    finally:
        runtime.close()


def test_the_send_gate_runs_inside_the_message_transaction(tmp_path: Path, monkeypatch) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        _validate_team(runtime)
        original = threads_coordinator.ensure_thread_runtime_team_ready
        seen: list[bool] = []

        def spy(connection, **kwargs):
            seen.append(connection.in_transaction)
            return original(connection, **kwargs)

        monkeypatch.setattr(threads_coordinator, "ensure_thread_runtime_team_ready", spy)
        response = client.post(f"/api/v1/threads/{thread['id']}/messages", headers=headers, json={"content": OBJECTIVE})
        assert response.status_code == 200, response.text
        assert seen == [True]
    finally:
        runtime.close()


def test_a_message_is_rejected_before_any_write_when_the_team_is_not_validated(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        response = client.post(f"/api/v1/threads/{thread['id']}/messages", headers=headers, json={"content": OBJECTIVE})
        assert response.status_code == 422
        assert "codex_cli" in response.json()["detail"]
        assert client.get(f"/api/v1/threads/{thread['id']}").json()["messages"] == []
        assert _product_loop_jobs(runtime) == 0
    finally:
        runtime.close()


def test_a_validated_team_is_sealed_into_the_run_and_forged_metadata_is_ignored(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        client.patch(f"/api/v1/threads/{thread['id']}/run-configuration", headers=headers, json=TEAM)
        _validate_team(runtime)
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={
                "content": OBJECTIVE,
                "metadata": {"runtimeTeam": {"allowedRuntimes": ["claude_code_cli"], "roleRuntimes": {}}},
            },
        )
        assert response.status_code == 200, response.text
        job = JobsRepository(runtime.connection).get_job(response.json()["run"]["jobId"])
        assert job["payload"]["runMetadata"]["runtimeTeam"] == TEAM
    finally:
        runtime.close()


def test_clearing_the_team_restores_automatic_routing(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        headers, thread = _setup(runtime, client, tmp_path)
        url = f"/api/v1/threads/{thread['id']}/run-configuration"
        client.patch(url, headers=headers, json=TEAM)
        cleared = client.patch(url, headers=headers, json={"allowedRuntimes": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["runtimeTeam"] is None
        response = client.post(
            f"/api/v1/threads/{thread['id']}/messages",
            headers=headers,
            json={"content": OBJECTIVE, "metadata": {"runtimeTeam": TEAM}},
        )
        assert response.status_code == 200, response.text
        job = JobsRepository(runtime.connection).get_job(response.json()["run"]["jobId"])
        assert "runtimeTeam" not in job["payload"]["runMetadata"]
    finally:
        runtime.close()
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_thread_api.py','-q','-p','no:randomly']))"`
Expected: FAIL: PATCH responde 404/405 y el mensaje no se rechaza.

- [ ] **Step 3: Implementación mínima**

`local_control_center/threads/contracts.py`: agregar antes de `from local_control_center.shared.schemas import AuditEventRecord`:

```python
from local_control_center.runtime_team.contracts import RoleRuntimesRecord
```

y después de la clase `ThreadUpdateRequest`:

```python
class ThreadRunConfigurationRequest(BaseModel):
    """Equipo de runtimes del hilo; ``allowedRuntimes`` vacío vuelve al ruteo automático."""

    allowed_runtimes: list[str] = Field(default_factory=list, alias="allowedRuntimes")
    role_runtimes: RoleRuntimesRecord = Field(default_factory=RoleRuntimesRecord, alias="roleRuntimes")


class ThreadRuntimeTeamRecord(BaseModel):
    """Equipo guardado: runtimes permitidos y runtime asignado por rol."""

    allowed_runtimes: list[str] = Field(alias="allowedRuntimes")
    role_runtimes: RoleRuntimesRecord = Field(alias="roleRuntimes")


class ThreadRunConfigurationResponse(BaseModel):
    """Resultado del PATCH: el equipo vigente del hilo, o ``None`` si quedó en ruteo automático."""

    thread_id: str = Field(alias="threadId")
    runtime_team: ThreadRuntimeTeamRecord | None = Field(default=None, alias="runtimeTeam")
```

`local_control_center/threads/api.py`:
- En el import de `.contracts`, agregar `ThreadRunConfigurationRequest,` y `ThreadRunConfigurationResponse,` entre `ThreadNoteResponse,` y `ThreadSimilarityMarkRequest,`.
- Reemplazar `from .coordinator import ThreadBusyError, ThreadCoordinator` por `from .coordinator import ACTIVE_EXECUTION_STATUSES, ThreadBusyError, ThreadCoordinator`.
- Después de `from local_control_center.remediations.service import BlockerRemediationService` agregar:

```python
from local_control_center.runtime_team.configuration import write_thread_runtime_team
from local_control_center.runtime_team.facts import load_runtime_facts
from local_control_center.shared.db import immediate_transaction
```

- Insertar antes de `    @router.get("/api/v1/threads/similar", response_model=ThreadSimilarityResponse)`:

```python
    @router.patch(
        "/api/v1/threads/{thread_id}/run-configuration",
        response_model=ThreadRunConfigurationResponse,
    )
    def update_thread_run_configuration(
        thread_id: str,
        body: ThreadRunConfigurationRequest,
        request: Request,
    ) -> dict[str, Any]:
        """Fija o limpia el equipo de runtimes del hilo; solo mientras el hilo no está en ejecución.

        Estado, escritura y evento van en un ``BEGIN IMMEDIATE``: serializa contra ``post_message``,
        que evalúa el gate y sella el run en su propia transacción inmediata.
        """
        require_write(request)
        try:
            thread = repository().get_thread(thread_id)
            facts = load_runtime_facts(platform.connection, project_id=thread["projectId"])
            with immediate_transaction(platform.connection):
                current = repository().get_thread(thread_id)
                if current["status"] in ACTIVE_EXECUTION_STATUSES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Thread {thread_id} is {current['status']}: change its AI team after the current run.",
                    )
                team = write_thread_runtime_team(
                    platform.connection,
                    thread_id=thread_id,
                    project_id=current["projectId"],
                    allowed_runtimes=body.allowed_runtimes,
                    role_runtimes=body.role_runtimes.model_dump(exclude_none=True),
                    facts=facts,
                )
                repository().record_event(
                    thread_id=thread_id, type="run_configuration_updated", payload={"runtimeTeam": team}
                )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"threadId": thread_id, "runtimeTeam": team}
```

`local_control_center/threads/coordinator.py`:
- Después de `from local_control_center.remediations.service import BlockerRemediationService` agregar:

```python
from local_control_center.runtime_team.configuration import (
    RUNTIME_TEAM_METADATA_KEY,
    ensure_thread_runtime_team_ready,
)
```

- En `_PUBLIC_MESSAGE_PROTECTED_PRODUCT_LOOP_METADATA_KEYS`, agregar `RUNTIME_TEAM_METADATA_KEY,` después de `*RESOURCE_COST_POLICY_METADATA_KEYS,`.
- En `post_message`, reemplazar

```python
        with immediate_transaction(self.connection):
            user_message = self.repository.append_message(
```

por

```python
        with immediate_transaction(self.connection):
            ensure_thread_runtime_team_ready(
                self.connection, project_id=existing_thread["projectId"], thread_id=thread_id
            )
            user_message = self.repository.append_message(
```

y agregar al docstring de `post_message` la línea: `El gate del equipo de runtimes corre dentro de la misma transacción que sella el run, así un PATCH concurrente no puede colarse entre la verificación y el sellado.` (`RuntimeTeamNotReadyError` hereda de `ValueError`: el `ROLLBACK` de `immediate_transaction` descarta todo y la ruta ya mapea `ValueError` a 422 en `threads/api.py:401-402`.)

`local_control_center/product_loop/metadata.py`:
- Antes de `from local_control_center.settings.resolver import resolve_setting_value` agregar:

```python
from local_control_center.runtime_team.configuration import (
    THREAD_RUN_CONFIGURATION_KEY,
    seal_thread_runtime_team,
)
```

- Eliminar las líneas 34-35 (`THREAD_RUN_CONFIGURATION_KEY = "runConfiguration"` y su docstring de atributo): la constante ahora vive en `runtime_team/configuration.py` y se reexporta por este import.
- En el docstring de `seal_operator_cost_decision` agregar al final del primer párrafo: `El equipo de runtimes del hilo se sella como ``runtimeTeam`` y reemplaza cualquier valor entrante.`
- Reemplazar el `    return stamped` final de `seal_operator_cost_decision` por:

```python
    return seal_thread_runtime_team(connection, project_id=project_id, thread_id=thread_id, metadata=stamped)
```

- [ ] **Step 4: Ejecutar tests, lint y regen**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_thread_api.py','tests_py/test_thread_run_configuration.py','tests_py/test_threads_api.py','tests_py/test_threads_operator_notes.py','tests_py/test_threads_coordinator.py','-q','-p','no:randomly']))"`
Expected: PASS (nuevos + regresión de sellado/hilos).
Run ruff format/check sobre los 4 `.py` modificados y el test; `corepack pnpm@10.24.0 run openapi:generate`; `rg -n "update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch" local-control-center/web/src/api/generated/openapi.ts`; `corepack pnpm@10.24.0 run typecheck:web`.
Expected: operationId presente; typecheck OK.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/threads/contracts.py local_control_center/threads/api.py local_control_center/threads/coordinator.py local_control_center/product_loop/metadata.py local-control-center/web/src/api/generated/openapi.ts tests_py/test_runtime_team_thread_api.py
git commit -m "Feature (Threads): equipo de runtimes por hilo con PATCH, gate de envío validado y sellado en el run" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Enforcement del equipo en el product loop

**Files:**
- Modify: `local_control_center/product_loop/coordinator.py` (imports cerca de `:68`; `_team_resource_request` `:2920-2963`; `_product_owner_resource_selection` `:3239-3372`; `_developer_execution_resource` `:3374-3406`; `_security_execution_resource` `:3408-3425`; `_run_team_review_phase` `:4410-4432`; `_failover_replacement` `:4838-4861`)
- Modify: `local_control_center/product_loop/phases/execution.py:90` (firma del recurso + bloqueo del developer asignado sin decisión real)
- Modify: `local_control_center/product_loop/phases/security.py:128`
- Test: `tests_py/test_runtime_team_loop_enforcement.py`

**Interfaces:**
- Consumes: `role_allowlist`, `restrict_to_allowlist`, `assigned_runtime`, `runtime_team_of` (Task 5); `team_role_for` (Task 1); `AIResourceRequest.allowed_provider_ids` aplicado en `ai_resource_manager.py:1336` y `runtime_preflight_cli.py:158`.
- Produces: `ProductLoopCoordinator._developer_execution_resource(team_schedule, request_meta=None)`, `._assigned_developer_execution_resource(team_schedule, assigned) -> dict` (solo una decisión real de `AIResourceManager` cuyo `providerId` sea el asignado; si no hay, `{}`), `._developer_assignment_blocker(request_meta, execution_resource) -> dict | None`, `._security_execution_resource(team_schedule, request_meta=None)`; allowlist en `_team_resource_request`, `_product_owner_resource_selection`, `_failover_replacement`; bloqueo `resource_manager` en `prepare_developer_execution` cuando el developer asignado no tiene decisión real; payload del Arquitecto con `preferredRuntime` o review `skipped`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_loop_enforcement.py`:

```python
"""El product loop solo usa runtimes del equipo sellado del hilo y, por rol, el asignado.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.ai_resource_manager import AIResourceManager
from local_control_center.product_loop import coordinator as coordinator_module
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

TEAM = {
    "runtimeTeam": {
        "allowedRuntimes": ["codex_cli", "nvidia_nim"],
        "roleRuntimes": {"product_owner": "codex_cli", "developer": "codex_cli", "security": "nvidia_nim"},
    }
}
SCHEDULE = {"risk": "medium", "mode": "balanced"}


@pytest.fixture
def coordinator(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        yield ProductLoopCoordinator(connection, root=tmp_path)


def _capture_selection(monkeypatch) -> list:
    captured: list = []

    def _capture(self, request, *, record=True, allow_decision_inference=False, **kwargs):
        captured.append(request)
        raise RuntimeError("stop after capturing the request")

    monkeypatch.setattr(AIResourceManager, "select_resource", _capture)
    return captured


def _team_request(coordinator, role_plan, request_meta):
    return coordinator._team_resource_request(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=request_meta,
        team_schedule=SCHEDULE,
        role_plan=role_plan,
        agent_tasks=[{"id": "task-1", "role": role_plan["role"]}],
    )


def test_team_roles_are_confined_to_their_assigned_runtime(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    security = {"role": "security_engineer", "kind": "review", "capabilities": ["security_review"]}
    qa = {"role": "qa_engineer", "kind": "review", "capabilities": ["test_design"]}
    assert _team_request(coordinator, build, TEAM).allowed_provider_ids == ["codex_cli"]
    assert _team_request(coordinator, security, TEAM).allowed_provider_ids == ["nvidia_nim"]
    assert _team_request(coordinator, qa, TEAM).allowed_provider_ids == ["codex_cli", "nvidia_nim"]
    assert _team_request(coordinator, build, {}).allowed_provider_ids is None


def test_product_owner_selection_only_offers_the_assigned_runtime(coordinator, monkeypatch):
    captured = _capture_selection(monkeypatch)
    with pytest.raises(RuntimeError, match="stop"):
        coordinator._product_owner_resource_selection(
            project_id="project-team", loop_id="loop-1", task_id="po-task", request_meta=TEAM
        )
    assert captured[0].allowed_provider_ids == ["codex_cli"]
    with pytest.raises(RuntimeError, match="stop"):
        coordinator._product_owner_resource_selection(
            project_id="project-team", loop_id="loop-1", task_id="po-task", request_meta={}
        )
    assert "claude_code_cli" in captured[1].allowed_provider_ids


def test_failover_never_leaves_the_assigned_runtime(coordinator, monkeypatch):
    captured = _capture_selection(monkeypatch)
    run = SimpleNamespace(
        project_id="project-team",
        loop={"id": "loop-1"},
        task_id="task-1",
        team_schedule=SCHEDULE,
        request_meta=TEAM,
    )
    replacement = coordinator._failover_replacement(
        run=run,
        payload={},
        attempts=[{"failureClass": "quota", "providerId": "codex_cli", "model": "gpt-5.5"}],
        provider_id="codex_cli",
        failed_model="gpt-5.5",
        role="developer",
    )
    assert replacement is None
    assert captured[0].allowed_provider_ids == ["codex_cli"]


def _schedule_role(role: str, kind: str, capabilities: list[str], provider_id: str, runtime: str) -> dict:
    return {
        "role": role,
        "kind": kind,
        "capabilities": capabilities,
        "resourceDecision": {"selected": {"providerId": provider_id, "model": "m", "runtime": runtime}},
    }


def test_developer_execution_requires_a_real_decision_for_the_assigned_runtime(coordinator):
    qa_only = {"roles": [_schedule_role("qa_engineer", "review", ["test_design"], "nvidia_nim", "api")]}
    build = {"roles": [_schedule_role("backend_engineer", "build", ["code_edit"], "codex_cli", "cli")]}
    assert coordinator._developer_execution_resource(qa_only, TEAM) == {}
    blocker = coordinator._developer_assignment_blocker(TEAM, {})
    assert blocker is not None and blocker["role"] == "developer" and "codex_cli" in blocker["reason"]
    resource = coordinator._developer_execution_resource(build, TEAM)
    assert (resource["providerId"], resource["preferredRuntime"]) == ("codex_cli", "codex_cli")
    assert coordinator._developer_assignment_blocker(TEAM, resource) is None
    assert coordinator._developer_assignment_blocker({}, {}) is None
    assert coordinator._developer_execution_resource(qa_only)["preferredRuntime"] == "nvidia_nim"


def test_security_model_analysis_only_runs_on_the_assigned_runtime(coordinator):
    def schedule(provider_id: str) -> dict:
        return {
            "roles": [
                {
                    "role": "security_engineer",
                    "kind": "review",
                    "capabilities": ["security_review"],
                    "resourceDecision": {"selected": {"providerId": provider_id, "model": "m"}},
                }
            ]
        }

    assert coordinator._security_execution_resource(schedule("openai_compatible"), TEAM) == {}
    assert coordinator._security_execution_resource(schedule("nvidia_nim"), TEAM) == {
        "preferredRuntime": "nvidia_nim",
        "model": "m",
    }
    assert coordinator._security_execution_resource(schedule("openai_compatible")) == {
        "preferredRuntime": "openai_compatible",
        "model": "m",
    }
    no_security = {"runtimeTeam": {"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"developer": "codex_cli"}}}
    assert coordinator._security_execution_resource(schedule("nvidia_nim"), no_security) == {}


def _review_run(tmp_path: Path, request_meta: dict) -> SimpleNamespace:
    return SimpleNamespace(
        loop={"id": "loop-1", "context": {}},
        team_schedule={"intent": {"intents": ["architecture"], "risk": "high"}},
        runtime_result={"diffSummary": {"patchArtifactId": "artifact-1"}},
        project_id="project-team",
        workspace={"id": "workspace-1"},
        task_id="task-1",
        rework_round=0,
        resolved_title="Team",
        qa_results=[],
        constitution=None,
        effective_root=tmp_path,
        request_meta=request_meta,
        thread_id=None,
    )


def test_architect_receives_the_assigned_runtime_or_is_skipped(coordinator, tmp_path, monkeypatch):
    payloads: list[dict] = []
    reviews: list[dict] = []

    def fake_run(self, payload):
        payloads.append(payload)
        return {"status": "completed", "verdict": "approved", "reason": "", "evidencePackage": {"id": "ev"}}

    monkeypatch.setattr(coordinator_module.ArchitectAgentRunner, "run", fake_run)
    monkeypatch.setattr(
        coordinator.repository,
        "update_loop_context",
        lambda loop_id, *, context: reviews.append(context["durableRun"]["teamReviews"])
        or {"id": loop_id, "context": context},
    )
    monkeypatch.setattr(coordinator, "_record_loop_event", lambda **kwargs: None)
    with_architect = {
        "runtimeTeam": {
            "allowedRuntimes": ["codex_cli", "nvidia_nim"],
            "roleRuntimes": {"developer": "codex_cli", "product_owner": "codex_cli", "architect": "nvidia_nim"},
        }
    }
    coordinator._run_team_review_phase(_review_run(tmp_path, with_architect))
    assert payloads[-1]["preferredRuntime"] == "nvidia_nim"
    coordinator._run_team_review_phase(_review_run(tmp_path, TEAM))
    assert len(payloads) == 1
    assert reviews[-1]["architect"]["status"] == "skipped"
    coordinator._run_team_review_phase(_review_run(tmp_path, {}))
    assert "preferredRuntime" not in payloads[-1]
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_loop_enforcement.py','-q','-p','no:randomly']))"`
Expected: FAIL: `allowed_provider_ids is None != ['codex_cli']` y `TypeError: _developer_execution_resource() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Implementación mínima**

En `local_control_center/product_loop/coordinator.py`, después de `from local_control_center.product_loop.metadata import strip_untrusted_resource_cost_policy_metadata` agregar:

```python
from local_control_center.runtime_team.configuration import (
    assigned_runtime,
    restrict_to_allowlist,
    role_allowlist,
    runtime_team_of,
)
from local_control_center.runtime_team.roles import team_role_for
```

(Si ruff/isort lo reubica, aceptar el orden que imponga `ruff check --fix` sobre imports.)

**(a) `_team_resource_request`:** después de `        policy = self._resource_role_policy(role)` insertar:

```python
        team_role = team_role_for(
            role, kind=str(role_plan.get("kind") or ""), capabilities=role_plan.get("capabilities") or []
        )
```

y en el `AIResourceRequest(...)` de esa función, después de `            required_capabilities=self._resource_required_capabilities(role_plan),` insertar:

```python
            allowed_provider_ids=role_allowlist(request_meta, team_role),
```

**(b) `_product_owner_resource_selection`:** inmediatamente después de `        allowed_provider_ids = sorted(PRODUCT_OWNER_AGENT_CLI_RUNTIMES | model_provider_ids)` insertar:

```python
        allowed_provider_ids = restrict_to_allowlist(
            allowed_provider_ids, role_allowlist(request_meta, "product_owner")
        )
```

**(c) `_developer_execution_resource`:** reemplazar la firma

```python
    def _developer_execution_resource(self, team_schedule: dict[str, Any]) -> dict[str, Any]:
```

por

```python
    def _developer_execution_resource(
        self, team_schedule: dict[str, Any], request_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assigned = assigned_runtime(request_meta, "developer")
        if assigned:
            return self._assigned_developer_execution_resource(team_schedule, assigned)
```

y agregar el método nuevo inmediatamente antes de `    def _security_execution_resource(`:

```python
    def _assigned_developer_execution_resource(
        self, team_schedule: dict[str, Any], assigned: str
    ) -> dict[str, Any]:
        """Recurso del developer asignado por el hilo, solo si AIResourceManager lo seleccionó de verdad.

        Sin una decisión real (política, costo o aprobación la dejaron fuera) devuelve ``{}`` y
        ``_developer_assignment_blocker`` bloquea: nunca se fabrica una selección que salte los
        blockers del gestor ni se usa otro runtime del schedule.
        """
        for role_plan in team_schedule.get("roles") or []:
            decision = role_plan.get("resourceDecision") or {}
            selected = decision.get("selected") or {}
            if not isinstance(selected, dict) or str(selected.get("providerId") or "").strip() != assigned:
                continue
            preferred_runtime = self._developer_runtime_id_for_resource_selection(selected)
            if not preferred_runtime:
                continue
            return redact_secrets(
                {
                    "role": role_plan.get("role"),
                    "providerId": assigned,
                    "model": selected.get("model"),
                    "runtime": selected.get("runtime"),
                    "preferredRuntime": preferred_runtime,
                    "decisionReason": decision.get("decisionReason"),
                    "estimatedCostUsd": decision.get("estimatedCostUsd"),
                    "usageStatus": decision.get("usageStatus"),
                }
            )
        return {}

    def _developer_assignment_blocker(
        self, request_meta: dict[str, Any] | None, execution_resource: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Bloqueo cuando el hilo asignó un developer que AIResourceManager no seleccionó para ejecutar."""
        assigned = assigned_runtime(request_meta, "developer")
        if not assigned or str(execution_resource.get("providerId") or "") == assigned:
            return None
        return {
            "role": "developer",
            "reason": (
                f"The thread runtime team assigns {assigned} to development, but AIResourceManager did not "
                "select it for execution; AIDO will not switch to another runtime on its own."
            ),
            "decision": {},
        }
```

**(d) `_security_execution_resource`:** reemplazar la firma `    def _security_execution_resource(self, team_schedule: dict[str, Any]) -> dict[str, Any]:` por

```python
    def _security_execution_resource(
        self, team_schedule: dict[str, Any], request_meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
```

agregar al final del docstring existente la línea `Con equipo de runtimes en el hilo, solo el runtime asignado a seguridad puede analizar.`, insertar como primeras líneas del cuerpo (después del docstring):

```python
        team_configured = runtime_team_of(request_meta) is not None
        assigned = assigned_runtime(request_meta, "security")
```

y reemplazar

```python
            if not self._is_model_runtime_provider(provider_id):
                continue
            return {"preferredRuntime": provider_id, "model": selected.get("model")}
```

por

```python
            if not self._is_model_runtime_provider(provider_id):
                continue
            if team_configured and provider_id != assigned:
                continue
            return {"preferredRuntime": provider_id, "model": selected.get("model")}
```

**(e) `_failover_replacement`:** en la rama `else` (el `AIResourceRequest` con `task_type=f"{role}.implement",`), después de la línea `                        required_capabilities=["chat"],` que sigue a `routing_policy=str((run.team_schedule or {}).get("mode") or "balanced"),` insertar:

```python
                        allowed_provider_ids=role_allowlist(
                            getattr(run, "request_meta", None), team_role_for(role)
                        ),
```

(`getattr` es obligatorio: tests existentes, p. ej. `tests_py/test_product_loop_coordinator.py:8716-8721`, construyen el `run` sin `request_meta`.)

**(f) Arquitecto en `_run_team_review_phase`:** reemplazar el bloque que empieza en `            if patch_artifact_id and (` y termina en `                    reviews["architect"] = {"status": "failed", "reason": redact_secrets(str(error))}` por:

```python
            architect_runtime = assigned_runtime(run.request_meta, "architect")
            architect_unassigned = runtime_team_of(run.request_meta) is not None and not architect_runtime
            if patch_artifact_id and (
                intents & self._ARCHITECT_REVIEW_INTENTS or risk in self._ARCHITECT_REVIEW_RISKS
            ):
                if architect_unassigned:
                    reviews["architect"] = {
                        "status": "skipped",
                        "verdict": "",
                        "reason": "The thread runtime team assigns no architect runtime.",
                        "evidencePackageId": "",
                    }
                else:
                    try:
                        architect_payload: dict[str, Any] = {
                            "projectId": run.project_id,
                            "workspaceId": run.workspace["id"],
                            "taskId": f"{run.task_id}.architect",
                            "diffArtifactId": patch_artifact_id,
                            "workflowContext": {
                                "workflowKind": "product_loop",
                                "attempt": run.rework_round + 1,
                                "title": run.resolved_title,
                            },
                            "testResults": run.qa_results,
                            "constitution": render_constitution_prompt(run.constitution) or None,
                        }
                        if architect_runtime:
                            architect_payload["preferredRuntime"] = architect_runtime
                        architect = ArchitectAgentRunner(self.connection, root=run.effective_root).run(
                            architect_payload
                        )
                        reviews["architect"] = {
                            "status": str(architect.get("status") or ""),
                            "verdict": str(architect.get("verdict") or ""),
                            "reason": str(architect.get("reason") or ""),
                            "evidencePackageId": str((architect.get("evidencePackage") or {}).get("id") or ""),
                        }
                    except Exception as error:
                        reviews["architect"] = {"status": "failed", "reason": redact_secrets(str(error))}
```

**(g) Callers:** en `local_control_center/product_loop/phases/execution.py:90` reemplazar `    execution_resource = coordinator._developer_execution_resource(team_schedule)` por

```python
    execution_resource = coordinator._developer_execution_resource(team_schedule, run.request_meta)
    assignment_blocker = coordinator._developer_assignment_blocker(run.request_meta, execution_resource)
    if assignment_blocker is not None:
        return coordinator._block_run(
            loop,
            stage="resource_manager",
            reason=assignment_blocker["reason"],
            actor=actor,
            details={
                "resourceBlockers": [assignment_blocker],
                "teamSchedule": team_schedule,
                "agentTaskIds": [task["id"] for task in agent_tasks],
            },
            thread_id=thread_id,
        )
```

(mismo shape de `details` que el bloqueo de mapeo de `execution.py:91-110`; `decision: {}` no crea aprobación en `/approvals`, `coordinator.py:1408-1416`); en `local_control_center/product_loop/phases/security.py:128` reemplazar `    security_resource = coordinator._security_execution_resource(team_schedule)` por `    security_resource = coordinator._security_execution_resource(team_schedule, run.request_meta)`.

- [ ] **Step 4: Ejecutar tests y regresión dirigida**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_loop_enforcement.py','tests_py/test_product_owner_auto_recovery.py','tests_py/test_execution_timeout_reconciliation.py','tests_py/test_project_constitution_slice.py','tests_py/test_product_loop_fsm_matrix.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_coordinator.py','-q','-p','no:randomly','-k','failover or security or developer_execution or product_owner_resource or team_review or architect']))"`
Expected: PASS (subconjunto; la suite completa corre en Task 13).
Run ruff format/check sobre `local_control_center/product_loop/coordinator.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py tests_py/test_runtime_team_loop_enforcement.py`. Expected: sin errores.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/coordinator.py local_control_center/product_loop/phases/execution.py local_control_center/product_loop/phases/security.py tests_py/test_runtime_team_loop_enforcement.py
git commit -m "Feature (ProductLoop): el loop confina equipo, PO, failover, developer, seguridad y arquitecto al equipo sellado del hilo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Gate de ejecución `runtime_team` y remediación "Re-probar runtime"

**Files:**
- Modify: `local_control_center/product_loop/phases/environment.py:131-164` (imports diferidos y gate entre el `_transition_run_state(...)` y el `try:`)
- Modify: `local_control_center/remediations/contracts.py:12-124`
- Modify: `local_control_center/remediations/payloads.py` (builder antes de `Builder = Callable...`, `:1550`; entrada al final de `PAYLOAD_BUILDERS`, `:1586`)
- Modify: `local_control_center/remediations/service.py` (import; rama en `execute` antes del `else` final `:867`; `_should_resolve` `:1019-1037`; `_blocker_type` `:3355`; métodos nuevos junto a `_retry_loop` `:1570`)
- Modify: `local_control_center/executions/workloads.py:44-50` (`revalidate_runtime` reserva `qa_light`)
- Modify: `local_control_center/agents/model_gateway_api.py` (handler `validate_runtime` creado en Task 4: reanudación tras `validated`)
- Modify: `local-control-center/web/src/features/shell/remediationPresentation.ts` (`BLOCKER_COPY` `:97-475`, `ACTION_COPY` `:477-567`)
- Modify: `local_control_center/i18n/default_catalog.json` (después de `app.threads.remediation.blocker.runtime_auth_missing.impact` `:11147-11150` y de `app.threads.remediation.action.retryLoop` `:11595-11598`)
- Regenerate: `local-control-center/web/src/api/generated/openapi.ts`
- Test: `tests_py/test_runtime_team_execution_gate.py`; Modify test: `tests_py/test_runtime_team_validate_api.py` (Task 4)

**Interfaces:**
- Consumes: `runtime_team_of`, `assess_runtime_team(..., only_assigned=True)` (Task 5); `runtime_validated_within` (Task 2); `VALIDATION_TTL_SECONDS` (`model_execution_health.py:18`); `enqueue_registered_operation` (`executions/router.py:112`); `BlockerRemediationService._retry_loop` (`remediations/service.py:1570`); `RemediationActionsRepository.get`/`list_for_thread`/`mark_status` (`remediations/repository.py:184,439`).
- Produces: etapa de bloqueo `runtime_team`; blocker `runtime_team_validation_expired`; acción `revalidate_runtime` (payload `runtimeIds: list[str]`); `BlockerRemediationService._revalidate_runtime(*, action, payload, platform) -> dict` (estado `validating` con `executionIds`, o el resultado del retry reanudado); `BlockerRemediationService._resume_runtime_team_retry(action) -> dict`; `BlockerRemediationService.resume_after_runtime_validation(provider_id: str) -> list[dict]`.

- [ ] **Step 1: Escribir el test que falla**

`tests_py/test_runtime_team_execution_gate.py`:

```python
"""Un runtime asignado que perdió su validación bloquea el loop con "Re-probar runtime".

Nunca cae en silencio a otro runtime: la remediación encola una validación por runtime vencido y,
cuando todos responden, reanuda el retry pendiente del mismo loop.

@author Rodrigo Mason
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_control_center.agents.model_execution_health import record_model_execution
from local_control_center.executions import router as execution_router
from local_control_center.executions.router import OperationSpec
from local_control_center.executions.workloads import operation_workload
from local_control_center.product_loop.coordinator import ProductLoopCoordinator
from local_control_center.product_loop.phases.environment import select_product_owner_resources
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.remediations.service import BlockerRemediationService
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.repository import ThreadsRepository

pytestmark = pytest.mark.usefixtures("controlled_domain_host")

TEAM = {
    "runtimeTeam": {
        "allowedRuntimes": ["codex_cli", "ollama", "nvidia_nim"],
        "roleRuntimes": {
            "product_owner": "codex_cli",
            "developer": "codex_cli",
            "architect": "ollama",
            "security": "ollama",
        },
    }
}
STALE_DETAILS = {
    "runtimeIds": ["codex_cli"],
    "staleRuntimes": [{"providerId": "codex_cli", "status": "never", "reason": "runtime_validation_required"}],
    "missingRoles": [],
}


def _run(request_meta: dict) -> SimpleNamespace:
    return SimpleNamespace(
        project_id="project-gate",
        actor="aido_lead",
        loop={"id": "loop-1", "context": {}},
        thread_id=None,
        request_meta=request_meta,
        preferred_runtime=None,
        product_owner=SimpleNamespace(),
    )


@pytest.fixture
def gate(tmp_path: Path, monkeypatch):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        blocked: dict = {}

        def fake_block(loop, **kwargs):
            blocked.update(kwargs)
            return {"status": "blocked"}

        monkeypatch.setattr(coordinator, "_transition_run_state", lambda loop, **kwargs: loop)
        monkeypatch.setattr(coordinator, "_durable_run_patch", lambda loop, patch: patch)
        monkeypatch.setattr(coordinator, "_block_run", fake_block)
        monkeypatch.setattr(
            coordinator,
            "_product_owner_resource_selection",
            lambda **kwargs: ({"selected": None}, {"role": "product_owner", "reason": "gate passed", "taskId": "t"}),
        )
        yield connection, coordinator, blocked


def test_runtime_check_blocks_when_an_assigned_runtime_lost_its_validation(gate):
    _connection, coordinator, blocked = gate
    assert select_product_owner_resources(coordinator, _run(TEAM)) == {"status": "blocked"}
    assert blocked["stage"] == "runtime_team"
    assert blocked["details"]["runtimeIds"] == ["codex_cli", "ollama"]


def test_runtime_check_blocks_a_team_with_missing_roles(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    team = {"runtimeTeam": {"allowedRuntimes": ["codex_cli"], "roleRuntimes": {"developer": "codex_cli"}}}
    select_product_owner_resources(coordinator, _run(team))
    assert blocked["stage"] == "runtime_team"
    assert blocked["details"]["missingRoles"] == ["product_owner"]


def test_optional_roles_left_unassigned_do_not_block_the_run(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    team = {
        "runtimeTeam": {
            "allowedRuntimes": ["codex_cli"],
            "roleRuntimes": {"developer": "codex_cli", "product_owner": "codex_cli"},
        }
    }
    select_product_owner_resources(coordinator, _run(team))
    assert blocked["stage"] == "resource_manager"


def test_a_selected_runtime_without_a_role_does_not_block_the_run(gate):
    connection, coordinator, blocked = gate
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    record_model_execution(connection, "ollama", "local_default", True, "test_prompt")
    select_product_owner_resources(coordinator, _run(TEAM))
    assert blocked["stage"] == "resource_manager"


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Runtime team gate", path=tmp_path / "project", template_id="other"
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"], owner_type="workspace", owner_id=project["id"], title="Gate"
        )
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": project["id"],
                "title": "Gate",
                "state": "blocked",
                "status": "blocked",
                "context": {
                    "durableRun": {
                        "thread": {"projectThreadId": thread["id"]},
                        "blockedStage": "runtime_team",
                        "blockedReason": "Runtimes without a fresh validation: codex_cli.",
                    }
                },
            }
        )
        threads.set_status(thread["id"], "blocked")
        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.create_for_blocked_run(
            project_id=project["id"],
            thread_id=thread["id"],
            loop_id=loop["id"],
            stage="runtime_team",
            reason="Runtimes without a fresh validation: codex_cli (runtime_validation_required).",
            details=STALE_DETAILS,
        )
        yield service, actions, project["id"], connection


def _platform() -> SimpleNamespace:
    spec = OperationSpec("models.validate_runtime", "remote_llm_light")
    return SimpleNamespace(execution_handlers={"models.validate_runtime": (spec, lambda **kwargs: None)})


def test_the_blocker_offers_retest_first_and_retry_second(lane):
    _service, actions, _project_id, _connection = lane
    assert {action["blockerType"] for action in actions} == {"runtime_team_validation_expired"}
    assert [action["actionType"] for action in actions] == ["revalidate_runtime", "retry_loop"]
    assert actions[0]["primary"] is True
    assert actions[0]["payload"]["runtimeIds"] == ["codex_cli"]


def test_the_retest_remediation_only_enqueues_so_it_reserves_light_work(lane):
    _service, actions, _project_id, connection = lane
    spec = OperationSpec("remediations.execute", "agent_cli")
    assert operation_workload(connection, spec, {"remediation_id": actions[0]["id"]}) == "qa_light"


def test_retest_queues_one_validation_per_stale_runtime_and_keeps_the_run_blocked(lane, monkeypatch):
    service, actions, project_id, _connection = lane
    enqueued: list[tuple] = []

    def fake_enqueue(platform, spec, arguments, *, result_status_code=200, project_id=None):
        enqueued.append((spec.name, arguments, project_id))
        return {"executionId": f"exec-{len(enqueued)}", "jobId": "job", "operation": spec.name, "status": "queued"}

    monkeypatch.setattr(execution_router, "enqueue_registered_operation", fake_enqueue)
    monkeypatch.setattr(
        BlockerRemediationService, "_retry_loop", lambda self, **kwargs: pytest.fail("must not retry yet")
    )
    result = service.execute(actions[0]["id"], platform=_platform())
    assert result["execution"]["status"] == "validating"
    assert result["execution"]["executionIds"] == ["exec-1"]
    assert enqueued == [
        ("models.validate_runtime", {"provider_id": "codex_cli", "body": {"projectId": project_id}}, project_id)
    ]
    assert result["remediation"]["status"] == "pending"


def test_a_validated_runtime_resumes_the_pending_retry(lane, monkeypatch):
    service, actions, _project_id, connection = lane
    retried: list[str] = []

    def fake_retry(self, *, action, payload=None):
        retried.append(action["id"])
        return {"status": "queued", "action": "retry_loop", "jobId": "job-retry"}

    monkeypatch.setattr(BlockerRemediationService, "_retry_loop", fake_retry)
    assert service.resume_after_runtime_validation("codex_cli") == []
    assert retried == []
    record_model_execution(connection, "codex_cli", "gpt-5.5", True, "test_prompt")
    assert service.resume_after_runtime_validation("ollama") == []
    resumed = service.resume_after_runtime_validation("codex_cli")
    assert [item["execution"]["status"] for item in resumed] == ["queued"]
    assert retried == [actions[1]["id"]]
    assert service.repository.get(actions[1]["id"])["status"] == "resolved"
    assert service.repository.get(actions[0]["id"])["status"] == "resolved"


def test_retest_without_the_registered_operation_is_blocked_not_silent(lane, monkeypatch):
    service, actions, _project_id, _connection = lane
    result = service.execute(actions[0]["id"], platform=SimpleNamespace(execution_handlers={}))
    assert result["execution"]["status"] == "blocked"
    assert "models.validate_runtime" in result["execution"]["reason"]
    assert result["remediation"]["status"] == "pending"
```

- [ ] **Step 2: Ejecutarlo y ver que falla**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_execution_gate.py','-q','-p','no:randomly']))"`
Expected: FAIL: la etapa es `resource_manager` (no hay gate), las acciones no incluyen `revalidate_runtime`, `resume_after_runtime_validation` no existe (`AttributeError`) y `operation_workload` devuelve `agent_cli` para la re-prueba.

- [ ] **Step 3: Implementación mínima**

**Gate** en `local_control_center/product_loop/phases/environment.py`, dentro de `select_product_owner_resources`, agregar a los imports diferidos de la función:

```python
    from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS
    from local_control_center.runtime_team.configuration import assess_runtime_team, runtime_team_of
```

e insertar entre el cierre `    )` del `loop = coordinator._transition_run_state(...)` y la línea `    try:`:

```python
    runtime_team = runtime_team_of(request_meta)
    if runtime_team is not None:
        readiness = assess_runtime_team(
            coordinator.connection, runtime_team, max_age_seconds=VALIDATION_TTL_SECONDS, only_assigned=True
        )
        if not readiness.ready:
            return coordinator._block_run(
                loop,
                stage="runtime_team",
                reason=readiness.reason(),
                actor=actor,
                details=readiness.details(),
                durable_context={"runtimeTeam": {"status": "blocked", **readiness.details()}},
                thread_id=thread_id,
            )
```

Actualizar el docstring de la función: `Antes de seleccionar, los runtimes con rol asignado del equipo sellado deben seguir validados (24 h + fingerprint) y no pueden faltar roles; si no, bloquea en la etapa runtime_team.`

**Contratos** (`local_control_center/remediations/contracts.py`): agregar `"runtime_team_validation_expired",` como último elemento tanto de la tupla `BLOCKER_TYPES` como del `Literal` `BlockerType` (después de `"thread_intake_decision_required",`), y `"revalidate_runtime",` como último elemento de `REMEDIATION_ACTION_TYPES` y de `RemediationActionType` (después de `"save_patch",`).

**Payloads** (`local_control_center/remediations/payloads.py`): antes de `Builder = Callable[[BlockerPayloadContext], list[dict[str, Any]]]` agregar:

```python
def _runtime_team_validation_expired_specs(context: BlockerPayloadContext) -> list[dict[str, Any]]:
    """Re-prueba los runtimes vencidos del equipo del hilo; el retry queda para después de editar."""
    runtime_ids = [str(item) for item in context.details.get("runtimeIds") or [] if str(item).strip()]
    specs: list[dict[str, Any]] = []
    if runtime_ids:
        specs.append(
            {
                "actionType": "revalidate_runtime",
                "title": "Re-test runtime",
                "description": "Queue a real round trip per stale runtime; the run resumes once every runtime answers.",
                "payload": {"runtimeIds": runtime_ids},
            }
        )
    specs.append(
        {
            "actionType": "retry_loop",
            "title": "Retry loop",
            "description": "Retry after editing the thread's AI team or re-testing its runtimes.",
            "payload": {"retryTarget": "runtime_team"},
        }
    )
    return specs
```

y en `PAYLOAD_BUILDERS`, después de `    "thread_intake_decision_required": _thread_intake_decision_required_specs,` agregar `    "runtime_team_validation_expired": _runtime_team_validation_expired_specs,`.

**Servicio** (`local_control_center/remediations/service.py`):
- Imports (orden alfabético del bloque): `from local_control_center.agents.model_execution_health import VALIDATION_TTL_SECONDS` y `from local_control_center.runtime_team.validation import runtime_validated_within`.
- En `execute`, antes del `        else:` final que produce `"Unsupported remediation action."`, agregar:

```python
        elif action_type == "revalidate_runtime":
            execution = self._revalidate_runtime(action=action, payload=execution_payload, platform=platform)
```

- En `_should_resolve`, agregar `"revalidate_runtime",` al conjunto después de `"retry_loop",` (resuelve solo con `completed`/`queued`; `validating` la deja `pending` para la reanudación).
- En `_blocker_type`, inmediatamente después de `        if stage == "review":\n            return "review_diff_unavailable"` agregar:

```python
        if stage == "runtime_team":
            return "runtime_team_validation_expired"
```

- Agregar los métodos (junto a `_retry_loop`):

```python
    def _revalidate_runtime(
        self, *, action: dict[str, Any], payload: dict[str, Any], platform: Any
    ) -> dict[str, Any]:
        """Encola una ``models.validate_runtime`` por runtime vencido o reanuda el retry si ya responden.

        Una operación por runtime deja que ``operation_workload`` reserve la clase de cada proveedor
        (CLI, API o GPU local). La remediación queda ``pending`` (estado ``validating``) hasta que
        ``resume_after_runtime_validation`` la re-ejecuta con todos sus runtimes validados.
        """
        runtime_ids = [str(item) for item in payload.get("runtimeIds") or [] if str(item).strip()]
        if not runtime_ids:
            return {"status": "blocked", "action": "revalidate_runtime", "reason": "runtimeIds is required."}
        stale = [
            provider_id
            for provider_id in runtime_ids
            if not runtime_validated_within(self.connection, provider_id, VALIDATION_TTL_SECONDS)
        ]
        if not stale:
            return self._resume_runtime_team_retry(action)
        registered = (getattr(platform, "execution_handlers", None) or {}).get("models.validate_runtime")
        if registered is None:
            return {
                "status": "blocked",
                "action": "revalidate_runtime",
                "reason": "The models.validate_runtime operation is not registered in this process.",
            }
        from local_control_center.executions.router import enqueue_registered_operation

        accepted = [
            enqueue_registered_operation(
                platform,
                registered[0],
                {"provider_id": provider_id, "body": {"projectId": action["projectId"]}},
                project_id=action["projectId"],
            )
            for provider_id in stale
        ]
        return {
            "status": "validating",
            "action": "revalidate_runtime",
            "runtimeIds": stale,
            "executionIds": [str(item.get("executionId") or "") for item in accepted],
            "reason": "Runtime tests queued; the run resumes when every runtime answers.",
        }

    def _resume_runtime_team_retry(self, action: dict[str, Any]) -> dict[str, Any]:
        """Reanuda el ``retry_loop`` pendiente del mismo loop cuando el equipo ya está validado."""
        retry_action = next(
            (
                item
                for item in self.repository.list_for_thread(action["threadId"])
                if item["actionType"] == "retry_loop"
                and item["loopId"] == action["loopId"]
                and item["status"] == "pending"
            ),
            None,
        )
        if retry_action is None:
            return {
                "status": "completed",
                "action": "revalidate_runtime",
                "reason": "Runtimes revalidated; there is no pending retry to resume.",
            }
        retry = self._retry_loop(action=retry_action, payload={})
        if retry.get("status") in {"completed", "queued"}:
            self.repository.mark_status(retry_action["id"], "resolved")
        return {**retry, "action": "revalidate_runtime", "retry": retry}

    def resume_after_runtime_validation(self, provider_id: str) -> list[dict[str, Any]]:
        """Re-ejecuta las re-pruebas pendientes que incluyen ``provider_id`` y ya tienen todo validado.

        Lo invoca el handler de ``models.validate_runtime`` tras un ``validated``; una re-prueba con
        otro runtime aún vencido sigue esperando su propia validación.
        """
        rows = self.connection.execute(
            """SELECT id FROM remediation_actions
               WHERE action_type = 'revalidate_runtime' AND status = 'pending'
               ORDER BY created_at ASC, rowid ASC"""
        ).fetchall()
        resumed: list[dict[str, Any]] = []
        for row in rows:
            action = self.repository.get(row["id"])
            runtime_ids = [str(item) for item in (action["payload"] or {}).get("runtimeIds") or []]
            if provider_id not in runtime_ids:
                continue
            if all(
                runtime_validated_within(self.connection, runtime_id, VALIDATION_TTL_SECONDS)
                for runtime_id in runtime_ids
            ):
                resumed.append(self.execute(action["id"], platform=None))
        return resumed
```

**Clase de carga** (`local_control_center/executions/workloads.py`): en `operation_workload`, reemplazar `        if action and action["action_type"] == "validate_runtime":` por `        if action and action["action_type"] in {"validate_runtime", "revalidate_runtime"}:` (la re-prueba solo encola; cada `models.validate_runtime` reserva la clase de su runtime).

**Reanudación** (`local_control_center/agents/model_gateway_api.py`, handler `validate_runtime` de Task 4): dentro de `validate_runtime`, reemplazar

```python
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"validation": result}
```

por

```python
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if result["status"] == "validated":
            from local_control_center.remediations.service import BlockerRemediationService

            BlockerRemediationService(
                platform.connection, root=getattr(platform, "cwd", None)
            ).resume_after_runtime_validation(provider_id)
        return {"validation": result}
```

y agregar al docstring del handler: `Un runtime validado reanuda los loops bloqueados en runtime_team que solo esperaban esa prueba.` (Import diferido: `remediations.service` importa el coordinator del loop, que no debe cargarse al importar el router del gateway.)

**Test de cableado** (agregar al final de `tests_py/test_runtime_team_validate_api.py` y sumar `from local_control_center.remediations.service import BlockerRemediationService` a sus imports):

```python
def test_a_validated_probe_resumes_runs_blocked_on_that_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIDO_TEAM_TEST_KEY", "unit-test-team-key")
    client = create_client(tmp_path, monkeypatch)
    headers = auth_headers(client)
    enable_remote_provider(
        client, headers, "deepseek", base_url="https://example.invalid/v1", credential_ref="env:AIDO_TEAM_TEST_KEY"
    )
    with _db() as connection, connection:
        ProviderAccountStore(connection).upsert_model(
            {"providerId": "deepseek", "model": "deepseek-chat", "enabled": True}
        )
    monkeypatch.setattr(
        probe,
        "provider_instance",
        lambda provider_id, *, connection: SimpleNamespace(
            chat_completion=lambda request: ModelResponse(
                providerId="deepseek", model=request.model, content="ok", usage=UsageRecord()
            )
        ),
    )
    resumed: list[str] = []
    monkeypatch.setattr(
        BlockerRemediationService,
        "resume_after_runtime_validation",
        lambda self, provider_id: resumed.append(provider_id) or [],
    )
    response = client.post("/api/v1/model-gateway/providers/deepseek/validate-runtime", json={}, headers=headers)
    assert response.status_code == 200, response.text
    assert resumed == ["deepseek"]
```

**UI de remediación** (`local-control-center/web/src/features/shell/remediationPresentation.ts`): en `BLOCKER_COPY`, después de la entrada `thread_intake_decision_required: {...},` agregar:

```ts
	runtime_team_validation_expired: {
		titleKey: 'app.threads.remediation.blocker.runtime_team_validation_expired.title',
		titleFallback: 'Thread AI team needs a fresh test',
		explanationKey: 'app.threads.remediation.blocker.runtime_team_validation_expired.explanation',
		explanationFallback:
			'A runtime assigned to this thread no longer has a valid test with its current configuration.',
		impactKey: 'app.threads.remediation.blocker.runtime_team_validation_expired.impact',
		impactFallback:
			'AIDO will not switch to another runtime on its own; the run stays blocked until you re-test it.',
	},
```

y en `ACTION_COPY`, después de `save_patch: {...},`:

```ts
	revalidate_runtime: {
		labelKey: 'app.threads.remediation.action.revalidateRuntime',
		labelFallback: 'Re-test runtime',
		kind: 'execute',
	},
```

**i18n** (`default_catalog.json`, Edit con la entrada ancla completa como old_string): después de `app.threads.remediation.blocker.runtime_auth_missing.impact`:

```json
    "app.threads.remediation.blocker.runtime_team_validation_expired.title": {
      "en": "Thread AI team needs a fresh test",
      "es": "El equipo IA del hilo necesita una prueba reciente"
    },
    "app.threads.remediation.blocker.runtime_team_validation_expired.explanation": {
      "en": "A runtime assigned to this thread no longer has a valid test with its current configuration.",
      "es": "Un runtime asignado a este hilo ya no tiene una prueba válida con su configuración actual."
    },
    "app.threads.remediation.blocker.runtime_team_validation_expired.impact": {
      "en": "AIDO will not switch to another runtime on its own; the run stays blocked until you re-test it.",
      "es": "AIDO no cambiará a otro runtime por su cuenta; la ejecución sigue bloqueada hasta que lo vuelvas a probar."
    },
```

y después de `app.threads.remediation.action.retryLoop`:

```json
    "app.threads.remediation.action.revalidateRuntime": {
      "en": "Re-test runtime",
      "es": "Re-probar runtime"
    },
```

- [ ] **Step 4: Ejecutar tests, lint, regen y gates web**

Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_execution_gate.py','tests_py/test_runtime_team_validate_api.py','tests_py/test_decision_engine_remediation.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"`
Expected: PASS.
Run ruff format/check sobre `local_control_center/product_loop/phases/environment.py local_control_center/remediations/contracts.py local_control_center/remediations/payloads.py local_control_center/remediations/service.py local_control_center/executions/workloads.py local_control_center/agents/model_gateway_api.py tests_py/test_runtime_team_execution_gate.py tests_py/test_runtime_team_validate_api.py`.
Run: `corepack pnpm@10.24.0 run openapi:generate`, `corepack pnpm@10.24.0 run typecheck:web` (falla si falta alguna entrada en los `Record<BlockerType|ActionType, …>`), `corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/shell/remediationPresentation.ts`.
Expected: todo OK; `git diff --stat local_control_center/i18n/default_catalog.json` solo `+16`.

- [ ] **Step 5: Commit**

```powershell
git add local_control_center/product_loop/phases/environment.py local_control_center/remediations/contracts.py local_control_center/remediations/payloads.py local_control_center/remediations/service.py local_control_center/executions/workloads.py local_control_center/agents/model_gateway_api.py local-control-center/web/src/features/shell/remediationPresentation.ts local_control_center/i18n/default_catalog.json local-control-center/web/src/api/generated/openapi.ts tests_py/test_runtime_team_execution_gate.py tests_py/test_runtime_team_validate_api.py
git commit -m "Feature (Remediations): el loop se bloquea si un runtime asignado pierde su validación y Re-probar encola una prueba por runtime y reanuda el retry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Cliente web y modelo del equipo

**Files:**
- Modify: `local-control-center/web/src/api/execution-client.ts:43-94` (callback opcional de observación)
- Modify: `local-control-center/web/src/api/client.ts` (aliases después de `ModelGatewayTestPromptResponse`, `:57-60`; funciones después de `testPromptModelGatewayProvider`, `:2027-2040`)
- Create: `local-control-center/web/src/features/runtime-team/runtimeTeamModel.ts`

**Interfaces:**
- Consumes: operationIds regenerados en Tasks 4, 6 y 7; `requestGeneratedOperation` (alias de `requestCompletedOperation`, `client.ts:11`; el poll de `execution-client.ts:62-89` ya lee `ExecutionResponse` con `status`/`reason`); `StatusTone` (`components/ui/StatusChip.tsx:13`).
- Produces: tipos `RuntimeTeamCandidatesResponse`, `RuntimeTeamCandidate`, `RuntimeValidationResponse`, `ThreadRunConfigurationRequest`, `ThreadRunConfigurationResponse`; funciones `getRuntimeTeamCandidates(projectId, selected, signal?)`, `validateRuntime(token, providerId, projectId, signal?, onExecution?)`, `updateThreadRunConfiguration(token, threadId, body, signal?)`; en el modelo: `TEAM_ROLES`, `TeamRole`, `REQUIRED_TEAM_ROLES` (PO + Developer), `OPTIONAL_TEAM_ROLES` (Arquitecto + Seguridad), `RoleRuntimes`, `RuntimeTeamSelection`, `ValidationView`, `readRuntimeTeam(metadata)`, `toRoleRuntimes(record)`, `withRole(current, role, providerId)`, `isEligible(candidate, role)`, `mergeRoleRuntimes(current, suggested, candidates, allowed, manual)`, `missingRequiredRoles(roles)`, `validationTone(status)`, `runtimeTeamRequest(selection)`.

- [ ] **Step 1: "Test que falla" = typecheck contra los nombres nuevos**

Crear primero `runtimeTeamModel.ts` (Step 3) y correr el typecheck antes de tocar `client.ts`.
Run: `corepack pnpm@10.24.0 run typecheck:web`
Expected: FAIL con `Module '"../../api/client"' has no exported member 'RuntimeTeamCandidate'` (y los otros tipos).

- [ ] **Step 2: (sin test de runtime propio)** Este task no tiene runner de tests TS en el repo; su verificación ejecutable es typecheck + biome aquí y el spec Playwright de Task 12.

- [ ] **Step 3: Implementación mínima**

`local-control-center/web/src/features/runtime-team/runtimeTeamModel.ts`:

```ts
/**
 * Runtime team of a thread: selection shape, reader for the persisted `runConfiguration`, and the
 * helpers the composer panel uses to merge the backend's automatic split with manual choices.
 * Eligibility and the split are computed by the backend; nothing here re-derives them.
 * @author Rodrigo Mason
 */
import type {
	RuntimeTeamCandidate,
	RuntimeTeamCandidatesResponse,
	ThreadRunConfigurationRequest,
} from '../../api/client';
import type { StatusTone } from '../../components/ui';

export const TEAM_ROLES = ['product_owner', 'developer', 'architect', 'security'] as const;
export type TeamRole = (typeof TEAM_ROLES)[number];
/** The backend refuses to send or run while Product Owner or Developer lacks a runtime. */
export const REQUIRED_TEAM_ROLES: readonly TeamRole[] = ['product_owner', 'developer'];
/** Unassigned architect: the review is skipped. Unassigned security: deterministic scanners only. */
export const OPTIONAL_TEAM_ROLES: readonly TeamRole[] = ['architect', 'security'];
export type RoleRuntimes = Partial<Record<TeamRole, string>>;
export type RuntimeTeamSelection = { allowedRuntimes: string[]; roleRuntimes: RoleRuntimes };
/**
 * Persisted validation (including `policy_denied`, a project veto that is never selectable) plus the
 * panel-only states of an in-flight or not-attempted test.
 */
export type ValidationView = RuntimeTeamCandidate['validation']['status'] | 'running' | 'deferred';

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Reads `thread.metadata.runConfiguration`; null means the thread uses automatic routing. */
export function readRuntimeTeam(metadata: unknown): RuntimeTeamSelection | null {
	const configuration = isRecord(metadata) ? metadata.runConfiguration : null;
	if (!isRecord(configuration) || !Array.isArray(configuration.allowedRuntimes)) return null;
	const allowedRuntimes = configuration.allowedRuntimes.filter(
		(item): item is string => typeof item === 'string' && item.length > 0,
	);
	if (allowedRuntimes.length === 0) return null;
	const rawRoles = isRecord(configuration.roleRuntimes) ? configuration.roleRuntimes : {};
	const roleRuntimes: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const value = rawRoles[role];
		if (typeof value === 'string' && allowedRuntimes.includes(value)) roleRuntimes[role] = value;
	}
	return { allowedRuntimes, roleRuntimes };
}

/** Normalizes the backend's nullable role record into a sparse map. */
export function toRoleRuntimes(
	record: RuntimeTeamCandidatesResponse['suggestedRoleRuntimes'] | null | undefined,
): RoleRuntimes {
	const roles: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const value = record?.[role];
		if (typeof value === 'string' && value) roles[role] = value;
	}
	return roles;
}

/** Returns a copy with `role` set to `providerId`, or unassigned when `providerId` is empty. */
export function withRole(current: RoleRuntimes, role: TeamRole, providerId: string): RoleRuntimes {
	const next: RoleRuntimes = {};
	for (const item of TEAM_ROLES) {
		const value = item === role ? providerId : current[item];
		if (value) next[item] = value;
	}
	return next;
}

export function isEligible(candidate: RuntimeTeamCandidate, role: TeamRole): boolean {
	return candidate.eligibleRoles.includes(role);
}

/**
 * Manual roles keep a still-valid choice; every other role follows the backend split for the
 * current selection, so adding a runtime redistributes the automatic roles. A role whose split
 * has no valid proposal keeps its current valid runtime instead of flickering to empty.
 */
export function mergeRoleRuntimes(
	current: RoleRuntimes,
	suggested: RoleRuntimes,
	candidates: readonly RuntimeTeamCandidate[],
	allowed: readonly string[],
	manual: ReadonlySet<TeamRole>,
): RoleRuntimes {
	const valid = (providerId: string | undefined, role: TeamRole): providerId is string =>
		Boolean(providerId) &&
		allowed.includes(providerId as string) &&
		candidates.some((candidate) => candidate.providerId === providerId && isEligible(candidate, role));
	const merged: RoleRuntimes = {};
	for (const role of TEAM_ROLES) {
		const kept = current[role];
		const proposed = suggested[role];
		if (manual.has(role) && valid(kept, role)) merged[role] = kept;
		else if (valid(proposed, role)) merged[role] = proposed;
		else if (valid(kept, role)) merged[role] = kept;
	}
	return merged;
}

export function missingRequiredRoles(roleRuntimes: RoleRuntimes): TeamRole[] {
	return REQUIRED_TEAM_ROLES.filter((role) => !roleRuntimes[role]);
}

export function validationTone(status: ValidationView): StatusTone {
	switch (status) {
		case 'validated':
			return 'ok';
		case 'stale':
		case 'deferred':
			return 'warn';
		case 'failed':
		case 'policy_denied':
			return 'danger';
		case 'running':
			return 'info';
		default:
			return 'pending';
	}
}

/** Body for the run-configuration PATCH; `null` clears the team (automatic routing). */
export function runtimeTeamRequest(selection: RuntimeTeamSelection | null): ThreadRunConfigurationRequest {
	return {
		allowedRuntimes: selection?.allowedRuntimes ?? [],
		roleRuntimes: selection?.roleRuntimes ?? {},
	};
}
```

En `local-control-center/web/src/api/execution-client.ts`:

1. Después de la clase `ExecutionObservationError` agregar:

```ts
/** Sees every poll of a queued execution (status and reason) before it settles. */
export type ExecutionObserver = (execution: ExecutionResponse) => void;
```

2. En la firma de `requestCompletedOperation`, reemplazar

```ts
	options: GeneratedRequestOptions<OperationRequestBody<T>> = {},
): Promise<TResponse> {
```

por

```ts
	options: GeneratedRequestOptions<OperationRequestBody<T>> = {},
	onExecution?: ExecutionObserver,
): Promise<TResponse> {
```

3. Inmediatamente después del bloque `if (typeof window !== 'undefined') { window.dispatchEvent(...); }` agregar `		onExecution?.(execution);`. Los llamadores existentes no cambian (parámetro opcional al final).

En `local-control-center/web/src/api/client.ts`, reemplazar el import de `:11` por `import { type ExecutionObserver, requestCompletedOperation as requestGeneratedOperation } from './execution-client';` y, después de la definición de `ModelGatewayTestPromptResponse`, agregar:

```ts
export type RuntimeTeamCandidatesResponse =
	OperationResponse<'list_runtime_team_candidates_api_v1_runtime_team_candidates_get'>;
export type RuntimeTeamCandidate = RuntimeTeamCandidatesResponse['candidates'][number];
export type RuntimeValidationResponse =
	OperationResponse<'validate_runtime_api_v1_model_gateway_providers__provider_id__validate_runtime_post'>;
export type ThreadRunConfigurationRequest =
	MutationBody<'update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch'>;
export type ThreadRunConfigurationResponse =
	OperationResponse<'update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch'>;
```

y después de la función `testPromptModelGatewayProvider`:

```ts
/** Enabled runtimes with their 30-minute validation, eligible roles and the backend split for `selected`. */
export function getRuntimeTeamCandidates(
	projectId: string,
	selected: readonly string[] | null,
	signal?: AbortSignal,
) {
	return requestGeneratedOperation<
		'list_runtime_team_candidates_api_v1_runtime_team_candidates_get',
		RuntimeTeamCandidatesResponse
	>('list_runtime_team_candidates_api_v1_runtime_team_candidates_get', {
		query: { projectId, selected: selected === null ? undefined : selected.join(',') },
		signal,
	});
}

/**
 * Real round trip against one runtime (queued `models.validate_runtime`); requires the write token.
 * `onExecution` sees each poll of the queued execution, e.g. `resource_wait` with the governor reason.
 */
export function validateRuntime(
	token: string,
	providerId: string,
	projectId: string,
	signal?: AbortSignal,
	onExecution?: ExecutionObserver,
) {
	return requestGeneratedOperation<
		'validate_runtime_api_v1_model_gateway_providers__provider_id__validate_runtime_post',
		RuntimeValidationResponse
	>(
		'validate_runtime_api_v1_model_gateway_providers__provider_id__validate_runtime_post',
		{
			token,
			pathParams: { provider_id: providerId },
			body: { projectId },
			signal,
		},
		onExecution,
	);
}

/** Sets or clears the thread's AI team; requires the write token. */
export function updateThreadRunConfiguration(
	token: string,
	threadId: string,
	body: ThreadRunConfigurationRequest,
	signal?: AbortSignal,
) {
	return requestGeneratedOperation<
		'update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch',
		ThreadRunConfigurationResponse
	>('update_thread_run_configuration_api_v1_threads__thread_id__run_configuration_patch', {
		token,
		pathParams: { thread_id: threadId },
		body,
		signal,
	});
}
```

(Si el regen produjo otro operationId, buscar la ruta en `API_ENDPOINTS` de `generated/openapi.ts` y usar ese literal en los 3 lugares.)

- [ ] **Step 4: Ejecutar gates**

Run: `corepack pnpm@10.24.0 run typecheck:web` y `corepack pnpm@10.24.0 exec biome check local-control-center/web/src/api/execution-client.ts local-control-center/web/src/api/client.ts local-control-center/web/src/features/runtime-team/runtimeTeamModel.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add local-control-center/web/src/api/execution-client.ts local-control-center/web/src/api/client.ts local-control-center/web/src/features/runtime-team/runtimeTeamModel.ts
git commit -m "Feature (Web): cliente tipado y modelo del equipo de runtimes por hilo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Panel "Equipo IA" en el composer

**Files:**
- Create: `local-control-center/web/src/features/runtime-team/useRuntimeTeamCandidates.ts`
- Create: `local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx`
- Create: `local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx`
- Modify: `local-control-center/web/src/design-system/layout.css` (después del bloque `.composer-runtimes[data-tone="warn"]`, `:2639-2641`)
- Modify: `local-control-center/web/src/features/shell/ThreadConversation.tsx` (imports `:41-52` y `:83`; `sendMessage` `:214-220`; composer existente `:588-604`; props `:874-903`; destructuring `:912-931`; contexto `:1144`; `NewThreadComposer` `:1287-1346`, `:1438-1455`)
- Modify: `local_control_center/i18n/default_catalog.json` (después de `app.composer.executableRuntime`, `:1282-1285`)

**Interfaces:**
- Consumes: Task 10 completo; `Drawer`, `Checkbox`, `SelectField`, `StatusChip`, `Button` (`components/ui/index.ts`); `useI18n` (`i18n/I18nProvider.tsx:26`).
- Produces: `useRuntimeTeamCandidates(projectId, selected, enabled) -> { data, failed, reload }`; `RuntimeTeamPanel(props: RuntimeTeamPanelProps)` (solo PO y Developer marcan error si faltan; Arquitecto/Seguridad muestran ayuda de rol opcional; fila `policy_denied` sin checkbox ni "Probar", con la causa de la política); `RuntimeTeamChip(props: RuntimeTeamChipProps)`; prop `teamControl?: ReactNode` en `ThreadComposerBox`.

- [ ] **Step 1: "Test que falla"**

La verificación de comportamiento es el spec de Task 12 (se escribe después porque necesita esta UI). Aquí el gate previo es el catálogo i18n: tras crear los componentes (Step 3) y antes de agregar las claves, correr:
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','-q','-p','no:randomly']))"`
Expected: FAIL en `test_every_static_t_call_key_is_registered_in_default_catalog` listando las claves `app.runtimeTeam.*`.

- [ ] **Step 2: Agregar las claves y ver el gate verde**

Con Edit (old_string = entrada completa `app.composer.executableRuntime`), agregar a continuación:

```json
    "app.runtimeTeam.chipAuto": {
      "en": "AI team · automatic",
      "es": "Equipo IA · automático"
    },
    "app.runtimeTeam.chipCount": {
      "en": "AI team · {selected} of {total}",
      "es": "Equipo IA · {selected} de {total}"
    },
    "app.runtimeTeam.title": {
      "en": "AI team for this thread",
      "es": "Equipo IA de este hilo"
    },
    "app.runtimeTeam.intro": {
      "en": "Only runtimes that answered a real test in the last 30 minutes can join this thread. Expired ones are re-tested when this panel opens.",
      "es": "Solo pueden sumarse los runtimes que respondieron una prueba real en los últimos 30 minutos. Los vencidos se vuelven a probar al abrir este panel."
    },
    "app.runtimeTeam.runtimes": {
      "en": "Available runtimes",
      "es": "Runtimes disponibles"
    },
    "app.runtimeTeam.cliQuota": {
      "en": "Testing uses your subscription quota.",
      "es": "Probarlo consume la cuota de tu suscripción."
    },
    "app.runtimeTeam.noRole": {
      "en": "No team role can use this runtime.",
      "es": "Ningún rol del equipo puede usar este runtime."
    },
    "app.runtimeTeam.test": {
      "en": "Test",
      "es": "Probar"
    },
    "app.runtimeTeam.status.validated": {
      "en": "Validated",
      "es": "Validado"
    },
    "app.runtimeTeam.status.stale": {
      "en": "Expired",
      "es": "Vencido"
    },
    "app.runtimeTeam.status.failed": {
      "en": "Failed",
      "es": "Falló"
    },
    "app.runtimeTeam.status.never": {
      "en": "Not tested",
      "es": "Sin probar"
    },
    "app.runtimeTeam.status.running": {
      "en": "Testing…",
      "es": "Probando…"
    },
    "app.runtimeTeam.status.deferred": {
      "en": "Deferred",
      "es": "Diferida"
    },
    "app.runtimeTeam.status.policy_denied": {
      "en": "Blocked by project policy",
      "es": "Bloqueado por la política del proyecto"
    },
    "app.runtimeTeam.roles": {
      "en": "Role assignment",
      "es": "Asignación de roles"
    },
    "app.runtimeTeam.role.product_owner": {
      "en": "Product Owner",
      "es": "Dueño de producto"
    },
    "app.runtimeTeam.role.developer": {
      "en": "Developer",
      "es": "Desarrollo"
    },
    "app.runtimeTeam.role.architect": {
      "en": "Architect",
      "es": "Arquitectura"
    },
    "app.runtimeTeam.role.security": {
      "en": "Security",
      "es": "Seguridad"
    },
    "app.runtimeTeam.role.qa": {
      "en": "QA",
      "es": "Calidad (QA)"
    },
    "app.runtimeTeam.role.devops": {
      "en": "DevOps",
      "es": "Operaciones (DevOps)"
    },
    "app.runtimeTeam.role.techLead": {
      "en": "Tech Lead",
      "es": "Líder técnico"
    },
    "app.runtimeTeam.choose": {
      "en": "Choose a runtime",
      "es": "Elige un runtime"
    },
    "app.runtimeTeam.roleRequired": {
      "en": "This role needs a runtime before the thread can run.",
      "es": "Este rol necesita un runtime antes de que el hilo pueda ejecutarse."
    },
    "app.runtimeTeam.architectOptional": {
      "en": "Optional. Without a runtime, the architecture review does not run for this thread.",
      "es": "Opcional. Sin runtime, la revisión de arquitectura no se ejecuta en este hilo."
    },
    "app.runtimeTeam.securityOptional": {
      "en": "Optional. Without a runtime, security runs only its deterministic scanners.",
      "es": "Opcional. Sin runtime, seguridad ejecuta solo sus scanners deterministas."
    },
    "app.runtimeTeam.deterministic": {
      "en": "AIDO (deterministic)",
      "es": "AIDO (determinista)"
    },
    "app.runtimeTeam.autoAssign": {
      "en": "Assign automatically",
      "es": "Repartir automáticamente"
    },
    "app.runtimeTeam.missingRoles": {
      "en": "Product Owner and Developer need a runtime before the thread can run.",
      "es": "Product Owner y Developer necesitan un runtime antes de que el hilo pueda ejecutarse."
    },
    "app.runtimeTeam.clear": {
      "en": "Use automatic routing",
      "es": "Usar ruteo automático"
    },
    "app.runtimeTeam.save": {
      "en": "Save team",
      "es": "Guardar equipo"
    },
    "app.runtimeTeam.loadError": {
      "en": "Could not load the runtimes. Try again.",
      "es": "No se pudieron cargar los runtimes. Inténtalo de nuevo."
    },
```

- [ ] **Step 3: Implementación mínima**

`local-control-center/web/src/features/runtime-team/useRuntimeTeamCandidates.ts`:

```ts
/**
 * Loads the runtime-team candidates for a project plus the backend's automatic split for the
 * current selection; a changed selection aborts the stale request.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useState } from 'react';

import { getRuntimeTeamCandidates, type RuntimeTeamCandidatesResponse } from '../../api/client';

export function useRuntimeTeamCandidates(
	projectId: string,
	selected: readonly string[] | null,
	enabled: boolean,
) {
	const [data, setData] = useState<RuntimeTeamCandidatesResponse | null>(null);
	const [failed, setFailed] = useState(false);
	const selectedKey = selected === null ? null : selected.join(',');

	const load = useCallback(
		async (signal?: AbortSignal) => {
			const selection = selectedKey === null ? null : selectedKey.split(',').filter(Boolean);
			try {
				const response = await getRuntimeTeamCandidates(projectId, selection, signal);
				if (!signal?.aborted) {
					setData(response);
					setFailed(false);
				}
			} catch {
				if (!signal?.aborted) setFailed(true);
			}
		},
		[projectId, selectedKey],
	);

	useEffect(() => {
		if (!enabled) return undefined;
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	}, [enabled, load]);

	const reload = useCallback(() => {
		void load();
	}, [load]);

	return { data, failed, reload };
}
```

`local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx`:

```tsx
/**
 * Drawer body of the thread's AI team: runtimes with their 30-minute validation, the per-role grid
 * preloaded with the backend's automatic split, and the deterministic roles AIDO keeps for itself.
 * Expired runtimes are re-tested when the panel opens; CLI rows warn that a test spends quota. Each
 * row keeps its last test outcome (failed or deferred with the cause, including the resource
 * governor's reason while the queued test waits), and roles the operator edited stay pinned while
 * the automatic ones follow the backend split for the current selection.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { type RuntimeTeamCandidate, validateRuntime } from '../../api/client';
import { Button, Checkbox, SelectField, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import {
	isEligible,
	mergeRoleRuntimes,
	missingRequiredRoles,
	OPTIONAL_TEAM_ROLES,
	REQUIRED_TEAM_ROLES,
	type RoleRuntimes,
	type RuntimeTeamSelection,
	TEAM_ROLES,
	type TeamRole,
	toRoleRuntimes,
	type ValidationView,
	validationTone,
	withRole,
} from './runtimeTeamModel';
import { useRuntimeTeamCandidates } from './useRuntimeTeamCandidates';

type Translate = (key: string, fallback?: string) => string;

function roleLabel(t: Translate, role: TeamRole): string {
	switch (role) {
		case 'product_owner':
			return t('app.runtimeTeam.role.product_owner', 'Product Owner');
		case 'developer':
			return t('app.runtimeTeam.role.developer', 'Developer');
		case 'architect':
			return t('app.runtimeTeam.role.architect', 'Architect');
		default:
			return t('app.runtimeTeam.role.security', 'Security');
	}
}

function validationLabel(t: Translate, status: ValidationView): string {
	switch (status) {
		case 'validated':
			return t('app.runtimeTeam.status.validated', 'Validated');
		case 'stale':
			return t('app.runtimeTeam.status.stale', 'Expired');
		case 'failed':
			return t('app.runtimeTeam.status.failed', 'Failed');
		case 'running':
			return t('app.runtimeTeam.status.running', 'Testing…');
		case 'deferred':
			return t('app.runtimeTeam.status.deferred', 'Deferred');
		case 'policy_denied':
			return t('app.runtimeTeam.status.policy_denied', 'Blocked by project policy');
		default:
			return t('app.runtimeTeam.status.never', 'Not tested');
	}
}

function optionalRoleHelp(t: Translate, role: TeamRole): string | undefined {
	if (role === 'architect') {
		return t(
			'app.runtimeTeam.architectOptional',
			'Optional. Without a runtime, the architecture review does not run for this thread.',
		);
	}
	if (role === 'security') {
		return t(
			'app.runtimeTeam.securityOptional',
			'Optional. Without a runtime, security runs only its deterministic scanners.',
		);
	}
	return undefined;
}

function rowHelp(t: Translate, candidate: RuntimeTeamCandidate): string | undefined {
	if (candidate.eligibleRoles.length === 0) {
		return t('app.runtimeTeam.noRole', 'No team role can use this runtime.');
	}
	if (candidate.kind === 'cli') {
		return t('app.runtimeTeam.cliQuota', 'Testing uses your subscription quota.');
	}
	return undefined;
}

/** Outcome of the last test this panel ran for a row, kept until the next test of that row. */
type ProbeOutcome = { status: ValidationView; reason: string | null; inFlight: boolean };

function latencyText(latencyMs: number | null | undefined): string {
	return latencyMs === null || latencyMs === undefined ? '—' : `${latencyMs} ms`;
}

export type RuntimeTeamPanelProps = {
	projectId: string;
	token: string;
	initial: RuntimeTeamSelection | null;
	onSave: (selection: RuntimeTeamSelection | null) => Promise<void> | void;
	onClose: () => void;
};

export function RuntimeTeamPanel({ projectId, token, initial, onSave, onClose }: RuntimeTeamPanelProps) {
	const { t } = useI18n();
	const [allowed, setAllowed] = useState<string[]>(initial?.allowedRuntimes ?? []);
	const [roles, setRoles] = useState<RoleRuntimes>(initial?.roleRuntimes ?? {});
	// A saved team is the operator's choice: its roles start pinned until "Assign automatically".
	const [manualRoles, setManualRoles] = useState<ReadonlySet<TeamRole>>(
		() => new Set(TEAM_ROLES.filter((role) => Boolean(initial?.roleRuntimes[role]))),
	);
	const [probes, setProbes] = useState<Readonly<Record<string, ProbeOutcome>>>({});
	const [saving, setSaving] = useState(false);
	const [saveError, setSaveError] = useState<string | null>(null);
	const { data, failed, reload } = useRuntimeTeamCandidates(projectId, allowed, true);
	const candidates = data?.candidates ?? [];
	const autoProbed = useRef(false);

	const setProbe = useCallback((providerId: string, outcome: ProbeOutcome | null) => {
		setProbes((current) => {
			const next = { ...current };
			if (outcome) next[providerId] = outcome;
			else delete next[providerId];
			return next;
		});
	}, []);

	const probe = useCallback(
		async (providerId: string) => {
			setProbe(providerId, { status: 'running', reason: null, inFlight: true });
			try {
				const { validation } = await validateRuntime(token, providerId, projectId, undefined, (execution) => {
					if (execution.status === 'resource_wait') {
						setProbe(providerId, { status: 'deferred', reason: execution.reason || null, inFlight: true });
					}
				});
				setProbe(
					providerId,
					validation.status === 'validated'
						? null
						: { status: validation.status, reason: validation.reason ?? null, inFlight: false },
				);
			} catch (error) {
				setProbe(providerId, {
					status: 'failed',
					reason: error instanceof Error ? error.message : String(error),
					inFlight: false,
				});
			} finally {
				reload();
			}
		},
		[token, projectId, reload, setProbe],
	);

	// Operator policy: expired runtimes are re-tested on open; never-tested ones wait for a click.
	useEffect(() => {
		if (!data || autoProbed.current) return;
		autoProbed.current = true;
		for (const candidate of data.candidates) {
			if (candidate.validation.status === 'stale') void probe(candidate.providerId);
		}
	}, [data, probe]);

	useEffect(() => {
		if (!data) return;
		setRoles((current) =>
			mergeRoleRuntimes(
				current,
				toRoleRuntimes(data.suggestedRoleRuntimes),
				data.candidates,
				allowed,
				manualRoles,
			),
		);
	}, [data, allowed, manualRoles]);

	const toggle = (providerId: string, checked: boolean) => {
		setAllowed((current) =>
			checked ? [...current, providerId] : current.filter((id) => id !== providerId),
		);
	};

	const assignAutomatically = () => {
		if (!data) return;
		const none = new Set<TeamRole>();
		setManualRoles(none);
		setRoles(mergeRoleRuntimes({}, toRoleRuntimes(data.suggestedRoleRuntimes), data.candidates, allowed, none));
	};

	const missing = allowed.length > 0 ? missingRequiredRoles(roles) : [];

	const save = async () => {
		setSaving(true);
		setSaveError(null);
		try {
			await onSave(allowed.length > 0 ? { allowedRuntimes: allowed, roleRuntimes: roles } : null);
			onClose();
		} catch (reason) {
			setSaveError(reason instanceof Error ? reason.message : String(reason));
		} finally {
			setSaving(false);
		}
	};

	const deterministicRoles = [
		t('app.runtimeTeam.role.qa', 'QA'),
		t('app.runtimeTeam.role.devops', 'DevOps'),
		t('app.runtimeTeam.role.techLead', 'Tech Lead'),
	];

	return (
		<div className="drawer-body runtime-team-panel">
			<p className="field-help">
				{t(
					'app.runtimeTeam.intro',
					'Only runtimes that answered a real test in the last 30 minutes can join this thread. Expired ones are re-tested when this panel opens.',
				)}
			</p>
			{failed ? (
				<p className="runtime-team-error" role="alert">
					{t('app.runtimeTeam.loadError', 'Could not load the runtimes. Try again.')}
				</p>
			) : null}
			<ul className="runtime-team-list" aria-label={t('app.runtimeTeam.runtimes', 'Available runtimes')}>
				{candidates.map((candidate) => {
					const outcome = probes[candidate.providerId];
					const running = outcome?.inFlight ?? false;
					const denied = candidate.validation.status === 'policy_denied';
					const status: ValidationView = denied
						? 'policy_denied'
						: outcome
							? outcome.status
							: candidate.validation.status;
					const reason = denied
						? candidate.validation.reason
						: (outcome?.reason ?? (candidate.validation.status === 'failed' ? candidate.validation.reason : null));
					const checked = allowed.includes(candidate.providerId);
					const selectable =
						candidate.validation.status === 'validated' && candidate.eligibleRoles.length > 0;
					return (
						<li key={candidate.providerId} className="runtime-team-row">
							<Checkbox
								label={candidate.label}
								help={rowHelp(t, candidate)}
								checked={checked}
								disabled={!checked && !selectable}
								onChange={(event) => toggle(candidate.providerId, event.currentTarget.checked)}
							/>
							<StatusChip tone={validationTone(status)}>{validationLabel(t, status)}</StatusChip>
							<span className="runtime-team-latency tnum">
								{latencyText(candidate.validation.latencyMs)}
							</span>
							<Button loading={running} disabled={denied} onClick={() => void probe(candidate.providerId)}>
								{t('app.runtimeTeam.test', 'Test')}
							</Button>
							{reason && (status === 'failed' || status === 'deferred' || status === 'policy_denied') ? (
								<span className="field-help runtime-team-reason">{reason}</span>
							) : null}
						</li>
					);
				})}
			</ul>
			<fieldset className="runtime-team-roles" disabled={allowed.length === 0}>
				<legend>{t('app.runtimeTeam.roles', 'Role assignment')}</legend>
				{TEAM_ROLES.map((role) => {
					const options = candidates.filter(
						(candidate) => allowed.includes(candidate.providerId) && isEligible(candidate, role),
					);
					return (
						<SelectField
							key={role}
							label={roleLabel(t, role)}
							value={roles[role] ?? ''}
							onChange={(event) => {
								const value = event.currentTarget.value;
								setManualRoles((current) => new Set(current).add(role));
								setRoles((current) => withRole(current, role, value));
							}}
							help={OPTIONAL_TEAM_ROLES.includes(role) ? optionalRoleHelp(t, role) : undefined}
							error={
								allowed.length > 0 && REQUIRED_TEAM_ROLES.includes(role) && !roles[role]
									? t('app.runtimeTeam.roleRequired', 'This role needs a runtime before the thread can run.')
									: undefined
							}
						>
							<option value="">{t('app.runtimeTeam.choose', 'Choose a runtime')}</option>
							{options.map((candidate) => (
								<option key={candidate.providerId} value={candidate.providerId}>
									{candidate.label}
								</option>
							))}
						</SelectField>
					);
				})}
				{deterministicRoles.map((label) => (
					<p key={label} className="runtime-team-fixed">
						<span>{label}</span>
						<span>{t('app.runtimeTeam.deterministic', 'AIDO (deterministic)')}</span>
					</p>
				))}
				<Button onClick={assignAutomatically} disabled={!data}>
					{t('app.runtimeTeam.autoAssign', 'Assign automatically')}
				</Button>
			</fieldset>
			{missing.length > 0 ? (
				<p className="field-help" role="status">
					{t('app.runtimeTeam.missingRoles', 'Product Owner and Developer need a runtime before the thread can run.')}
				</p>
			) : null}
			{saveError ? (
				<p className="runtime-team-error" role="alert">
					{saveError}
				</p>
			) : null}
			<div className="runtime-team-actions">
				<Button
					disabled={allowed.length === 0}
					onClick={() => {
						setAllowed([]);
						setRoles({});
						setManualRoles(new Set());
					}}
				>
					{t('app.runtimeTeam.clear', 'Use automatic routing')}
				</Button>
				<Button variant="primary" loading={saving} disabled={missing.length > 0} onClick={() => void save()}>
					{t('app.runtimeTeam.save', 'Save team')}
				</Button>
			</div>
		</div>
	);
}
```

`local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx`:

```tsx
/**
 * Composer chip that states the thread's AI team ("AI team · 2 of 5") and opens the team drawer.
 * A new thread keeps the selection as a draft that the intake persists before the first message;
 * an existing thread saves it straight away through the run-configuration PATCH.
 * @author Rodrigo Mason
 */
import { Users } from 'lucide-react';
import { useState } from 'react';

import { Drawer } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { RuntimeTeamPanel } from './RuntimeTeamPanel';
import type { RuntimeTeamSelection } from './runtimeTeamModel';
import { useRuntimeTeamCandidates } from './useRuntimeTeamCandidates';

export type RuntimeTeamChipProps = {
	projectId: string;
	token: string;
	selection: RuntimeTeamSelection | null;
	onChange: (selection: RuntimeTeamSelection | null) => Promise<void> | void;
	disabled?: boolean;
};

export function RuntimeTeamChip({
	projectId,
	token,
	selection,
	onChange,
	disabled = false,
}: RuntimeTeamChipProps) {
	const { t } = useI18n();
	const [open, setOpen] = useState(false);
	const { data } = useRuntimeTeamCandidates(projectId, selection?.allowedRuntimes ?? null, !open);
	const total = data?.candidates.length ?? 0;
	const unvalidated =
		data && selection
			? selection.allowedRuntimes.filter(
					(id) =>
						data.candidates.find((candidate) => candidate.providerId === id)?.validation.status !==
						'validated',
				).length
			: 0;
	const label = selection
		? t('app.runtimeTeam.chipCount', 'AI team · {selected} of {total}')
				.replace('{selected}', String(selection.allowedRuntimes.length))
				.replace('{total}', String(total))
		: t('app.runtimeTeam.chipAuto', 'AI team · automatic');
	return (
		<>
			<button
				type="button"
				className="composer-env composer-runtimes runtime-team-chip"
				data-tone={unvalidated > 0 ? 'warn' : undefined}
				aria-haspopup="dialog"
				disabled={disabled}
				onClick={() => setOpen(true)}
			>
				<Users aria-hidden="true" size={13} />
				<span className="composer-runtimes-label">{label}</span>
			</button>
			<Drawer open={open} onClose={() => setOpen(false)} label={t('app.runtimeTeam.title', 'AI team for this thread')}>
				<RuntimeTeamPanel
					projectId={projectId}
					token={token}
					initial={selection}
					onSave={onChange}
					onClose={() => setOpen(false)}
				/>
			</Drawer>
		</>
	);
}
```

`local-control-center/web/src/design-system/layout.css`, a continuación del bloque `.composer-runtimes[data-tone="warn"] { … }`:

```css
.runtime-team-chip:disabled {
	cursor: not-allowed;
	opacity: 0.6;
}

/* ---- Thread AI team drawer: runtimes list, role grid and actions ---- */
.runtime-team-panel {
	display: grid;
	gap: var(--space-4);
}

.runtime-team-list {
	display: grid;
	gap: var(--space-2);
	margin: 0;
	padding: 0;
	list-style: none;
}

.runtime-team-row {
	display: grid;
	grid-template-columns: minmax(0, 1fr) auto auto auto;
	align-items: center;
	gap: var(--space-2);
	padding: var(--space-2) var(--space-3);
	border: 1px solid var(--color-border-subtle);
	border-radius: var(--radius-md);
	background: var(--color-surface-card);
}

.runtime-team-reason {
	grid-column: 1 / -1;
}

.runtime-team-latency {
	color: var(--color-text-muted);
	font-family: var(--font-data);
	font-size: var(--font-size-xs);
}

.runtime-team-roles {
	display: grid;
	gap: var(--space-3);
	margin: 0;
	padding: var(--space-3);
	border: 1px solid var(--color-border-subtle);
	border-radius: var(--radius-md);
}

.runtime-team-fixed {
	display: flex;
	justify-content: space-between;
	gap: var(--space-2);
	margin: 0;
	color: var(--color-text-muted);
}

.runtime-team-actions {
	display: flex;
	justify-content: flex-end;
	gap: var(--space-2);
}

.runtime-team-error {
	margin: 0;
	color: color-mix(in oklch, var(--color-status-danger) 78%, var(--color-text-primary));
}

/* The drawer is fixed to the viewport (portal), so a viewport query is correct here. */
@media (max-width: 480px) {
	.runtime-team-row {
		grid-template-columns: minmax(0, 1fr) auto;
	}
}
```

`local-control-center/web/src/features/shell/ThreadConversation.tsx`:

1. En el import de `'../../api/client'`, agregar `updateThreadRunConfiguration,` después de `runWorkerOnce,`.
2. Antes de `import { useSettings } from '../settings/useSettings';` agregar:

```ts
import { RuntimeTeamChip } from '../runtime-team/RuntimeTeamChip';
import {
	type RuntimeTeamSelection,
	readRuntimeTeam,
	runtimeTeamRequest,
} from '../runtime-team/runtimeTeamModel';
```

3. Después del `useCallback` de `sendMessage` (termina en `		[send],\n	);`) agregar:

```ts
	const saveRuntimeTeam = useCallback(
		async (selection: RuntimeTeamSelection | null) => {
			if (!activeThreadId) return;
			await updateThreadRunConfiguration(token, activeThreadId, runtimeTeamRequest(selection));
			reload();
		},
		[activeThreadId, token, reload],
	);
```

4. En el `<ThreadComposerBox>` del hilo existente (dentro de `.thread-composer-dock`), después de `onNewObjective={openNewObjective}` agregar:

```tsx
								teamControl={
									<RuntimeTeamChip
										projectId={selectedProject.id}
										token={token}
										selection={readRuntimeTeam(detail.thread.metadata)}
										disabled={isExecuting}
										onChange={saveRuntimeTeam}
									/>
								}
```

5. En `type ThreadComposerBoxProps`, después de la última prop (`onNewObjective?`), agregar:

```ts
	/** Context-row control for the thread's AI team (chip + drawer), rendered before the git bar. */
	teamControl?: ReactNode;
```

6. En la desestructuración de `ThreadComposerBox`, agregar `teamControl,` después de `onNewObjective,`.
7. Reemplazar `				<GitBranchBar selectedProject={project} token={token} onRefresh={onGitRefresh} />` por:

```tsx
				{teamControl}
				<GitBranchBar selectedProject={project} token={token} onRefresh={onGitRefresh} />
```

8. En `NewThreadComposer`, después de `	const [reuseFailed, setReuseFailed] = useState(false);` agregar `	const [runtimeTeam, setRuntimeTeam] = useState<RuntimeTeamSelection | null>(null);`; dentro de `createThreadFromMessage`, inmediatamente después de `			const pendingThreadId = pending.threadId;` agregar:

```ts
			if (runtimeTeam) {
				// Persist the team before the first message so the backend seals it into that run.
				await updateThreadRunConfiguration(token, pendingThreadId, runtimeTeamRequest(runtimeTeam));
			}
```

y en su `<ThreadComposerBox>`, después de `onValueChange={setDraft}` agregar:

```tsx
						teamControl={
							<RuntimeTeamChip
								projectId={project.id}
								token={token}
								selection={runtimeTeam}
								onChange={setRuntimeTeam}
							/>
						}
```

(Las cadenas ancladas por `tests_py/test_web_rework_architecture.py:359-371` — `const created = await createThread(token, {`, `await postThreadMessage(token, pendingThreadId, {`, etc. — quedan intactas.)

- [ ] **Step 4: Ejecutar gates**

Run: `corepack pnpm@10.24.0 exec biome check --write local-control-center/web/src/features/runtime-team local-control-center/web/src/features/shell/ThreadConversation.tsx` (aplica organizeImports/format), luego `corepack pnpm@10.24.0 exec biome check local-control-center/web/src/features/runtime-team local-control-center/web/src/features/shell/ThreadConversation.tsx local-control-center/web/src/design-system/layout.css`, `corepack pnpm@10.24.0 run typecheck:web`, `corepack pnpm@10.24.0 run build:web`.
Run: `H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','-q','-p','no:randomly']))"`
Expected: todo verde (i18n bilingüe completo, guardrails visuales y pins del composer). `git diff --stat local_control_center/i18n/default_catalog.json` solo agrega líneas.

- [ ] **Step 5: Commit**

```powershell
git add local-control-center/web/src/features/runtime-team/useRuntimeTeamCandidates.ts local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx local-control-center/web/src/design-system/layout.css local-control-center/web/src/features/shell/ThreadConversation.tsx local_control_center/i18n/default_catalog.json
git commit -m "Feature (Web): panel Equipo IA en el composer con prueba automática de vencidos y grilla de roles" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Spec Playwright del panel

**Files:**
- Create: `tests_web/thread-runtime-team.spec.js`

**Interfaces:**
- Consumes: UI de Task 11; patrón de navegación y mocks de `tests_web/thread-cost.spec.js:17-40,95-104`.
- Produces: cobertura E2E de: checkbox deshabilitado sin validación, auto-prueba de vencidos, un solo runtime que cubre PO y Developer habilita "Guardar equipo" con Seguridad (opcional) sin asignar, reparto que se redistribuye al sumar runtimes (el mock responde según `selected`), rol editado a mano que se conserva, PATCH antes del primer mensaje.

- [ ] **Step 1: Escribir el spec (falla hasta tener build con Task 11)**

`tests_web/thread-runtime-team.spec.js`:

```js
/**
 * Thread AI team: the composer chip opens a drawer where only freshly validated runtimes can be
 * selected, expired ones are re-tested on open, the backend split preloads the role grid, and a new
 * thread persists its team before the first message. Candidates, validate-runtime and
 * run-configuration are mocked so the scenarios never depend on real provider credentials; the
 * thread and its first message go to the real control plane.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

const VALIDATED = {
	status: 'validated',
	checkedAt: '2026-09-22T10:00:00+00:00',
	latencyMs: 420,
	model: 'm',
	reason: null,
};
const STALE = {
	status: 'stale',
	checkedAt: '2026-09-22T08:00:00+00:00',
	latencyMs: 900,
	model: 'm',
	reason: 'runtime_validation_expired',
};

const NO_SPLIT = { developer: null, product_owner: null, architect: null, security: null };
// Backend split per sorted selection (only validated runtimes), as `auto_assign_roles` computes it.
const SPLITS = {
	claude_code_cli: {
		developer: 'claude_code_cli',
		product_owner: 'claude_code_cli',
		architect: 'claude_code_cli',
		security: null,
	},
	'claude_code_cli,omniroute': {
		developer: 'claude_code_cli',
		product_owner: 'omniroute',
		architect: 'omniroute',
		security: 'omniroute',
	},
	'claude_code_cli,nvidia_nim,omniroute': {
		developer: 'claude_code_cli',
		product_owner: 'omniroute',
		architect: 'nvidia_nim',
		security: 'nvidia_nim',
	},
};

function splitFor(selected) {
	if (selected === null) return NO_SPLIT;
	const key = selected.split(',').filter(Boolean).sort().join(',');
	return SPLITS[key] ?? NO_SPLIT;
}

function candidatesBody(nimValidation, selected) {
	return {
		candidates: [
			{
				providerId: 'claude_code_cli',
				label: 'Claude Code CLI',
				kind: 'cli',
				validation: VALIDATED,
				eligibleRoles: ['product_owner', 'developer', 'architect'],
			},
			{
				providerId: 'omniroute',
				label: 'OmniRoute',
				kind: 'gateway',
				validation: VALIDATED,
				eligibleRoles: ['product_owner', 'developer', 'architect', 'security'],
			},
			{
				providerId: 'nvidia_nim',
				label: 'NVIDIA NIM',
				kind: 'api',
				validation: nimValidation,
				eligibleRoles: ['product_owner', 'architect', 'security'],
			},
		],
		freshnessSeconds: 1800,
		suggestedRoleRuntimes: splitFor(selected),
	};
}

async function mockRuntimeTeam(page, { probeSucceeds }) {
	const state = { nimValidation: STALE, probes: [], patches: [], order: [] };
	page.on('request', (request) => {
		const url = request.url();
		if (url.includes('/run-configuration')) state.order.push('patch');
		if (url.includes('/messages') && request.method() === 'POST') state.order.push('message');
	});
	await page.route('**/api/v1/runtime/team-candidates**', async (route) => {
		const selected = new URL(route.request().url()).searchParams.get('selected');
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(candidatesBody(state.nimValidation, selected)),
		});
	});
	await page.route('**/api/v1/model-gateway/providers/*/validate-runtime', async (route) => {
		const providerId = route.request().url().split('/providers/')[1].split('/')[0];
		state.probes.push(providerId);
		if (probeSucceeds) state.nimValidation = VALIDATED;
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				validation: {
					providerId,
					kind: 'api',
					status: probeSucceeds ? 'validated' : 'failed',
					model: 'm',
					latencyMs: 300,
					reason: probeSucceeds ? null : 'runtime_auth_missing',
					checkedAt: '2026-09-22T10:05:00+00:00',
				},
			}),
		});
	});
	await page.route('**/api/v1/threads/*/run-configuration', async (route) => {
		const body = route.request().postDataJSON();
		state.patches.push(body);
		const threadId = route.request().url().split('/threads/')[1].split('/')[0];
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				threadId,
				runtimeTeam: { allowedRuntimes: body.allowedRuntimes, roleRuntimes: body.roleRuntimes },
			}),
		});
	});
	return state;
}

async function openNewThreadTeamPanel(page) {
	await page.goto('/#threads');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	await page.locator('.thread-workspace-head').first().click();
	await page.locator('.shell-new-thread').click();
	await expect(page.getByRole('heading', { name: /What will we work on/ })).toBeVisible();
	await page.getByRole('button', { name: /AI team/ }).click();
	const panel = page.getByRole('dialog', { name: 'AI team for this thread' });
	await expect(panel).toBeVisible();
	return panel;
}

test('an expired runtime is re-tested on open and stays disabled while its test fails', async ({ page }) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false });
	const panel = await openNewThreadTeamPanel(page);
	await expect.poll(() => state.probes).toContain('nvidia_nim');
	expect(state.probes).not.toContain('claude_code_cli');
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeDisabled();
	await expect(panel.getByRole('checkbox', { name: /Claude Code CLI/ })).toBeEnabled();
});

test('a successful automatic re-test makes the runtime selectable', async ({ page }) => {
	await mockRuntimeTeam(page, { probeSucceeds: true });
	const panel = await openNewThreadTeamPanel(page);
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeEnabled();
});

test('adding a runtime redistributes the automatic roles and the team is saved before the first message', async ({
	page,
}) => {
	const state = await mockRuntimeTeam(page, { probeSucceeds: false });
	const panel = await openNewThreadTeamPanel(page);
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('claude_code_cli');
	// Security is optional: one runtime covering Product Owner and Developer is already a sendable team.
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('');
	await expect(panel.getByRole('button', { name: 'Save team' })).toBeEnabled();
	await panel.getByRole('checkbox', { name: /OmniRoute/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Developer' })).toHaveValue('claude_code_cli');
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('omniroute');
	await expect(panel.getByRole('combobox', { name: 'Product Owner' })).toHaveValue('omniroute');
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('omniroute');
	await panel.getByRole('button', { name: 'Save team' }).click();
	await expect(panel).toBeHidden();
	await expect(page.getByRole('button', { name: 'AI team · 2 of 3' })).toBeVisible();

	const objective = `Runtime team spec ${Date.now()}`;
	await page.getByLabel('Message AIDO').fill(objective);
	await page.getByRole('button', { name: 'Create thread' }).click();
	await expect(page.getByText(objective).first()).toBeVisible({ timeout: 20_000 });
	expect(state.patches[0]).toEqual({
		allowedRuntimes: ['claude_code_cli', 'omniroute'],
		roleRuntimes: {
			product_owner: 'omniroute',
			developer: 'claude_code_cli',
			architect: 'omniroute',
			security: 'omniroute',
		},
	});
	expect(state.order.indexOf('patch')).toBeLessThan(state.order.indexOf('message'));
});

test('a role edited by hand stays pinned while the automatic roles follow the new split', async ({ page }) => {
	await mockRuntimeTeam(page, { probeSucceeds: true });
	const panel = await openNewThreadTeamPanel(page);
	await expect(panel.getByRole('checkbox', { name: /NVIDIA NIM/ })).toBeEnabled();
	await panel.getByRole('checkbox', { name: /Claude Code CLI/ }).check();
	await panel.getByRole('checkbox', { name: /OmniRoute/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('omniroute');
	await panel.getByRole('combobox', { name: 'Architect' }).selectOption('claude_code_cli');
	await panel.getByRole('checkbox', { name: /NVIDIA NIM/ }).check();
	await expect(panel.getByRole('combobox', { name: 'Security' })).toHaveValue('nvidia_nim');
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('claude_code_cli');
	await panel.getByRole('button', { name: 'Assign automatically' }).click();
	await expect(panel.getByRole('combobox', { name: 'Architect' })).toHaveValue('nvidia_nim');
});
```

- [ ] **Step 2: Ejecutarlo contra un build previo a Task 11 (opcional) / verificar que falla sin la UI**

Si Task 11 no está en el build: Expected FAIL en `getByRole('button', { name: /AI team/ })` (no existe).

- [ ] **Step 3: Build fresco y ejecución directa con puerto fijo**

```powershell
corepack pnpm@10.24.0 run build:web
$env:PLAYWRIGHT_DASHBOARD_PORT = '4391'
corepack pnpm@10.24.0 exec playwright test tests_web/thread-runtime-team.spec.js --project=desktop
corepack pnpm@10.24.0 exec playwright test tests_web/thread-runtime-team.spec.js --project=mobile
Remove-Item Env:PLAYWRIGHT_DASHBOARD_PORT
```

- [ ] **Step 4: Resultado esperado**

Expected: 4 passed por proyecto. Si otro proceso (p. ej. un build de Codex) vacía `dist/web` durante la corrida, repetir con `PLAYWRIGHT_STATIC_DIR` apuntando a un snapshot congelado del build (lección registrada del repo) y verificar el dueño del puerto antes de confiar en el resultado.

- [ ] **Step 5: Commit**

```powershell
corepack pnpm@10.24.0 exec biome check tests_web/thread-runtime-team.spec.js
git add tests_web/thread-runtime-team.spec.js
git commit -m "Test (Web): spec del panel Equipo IA con auto-prueba, redistribución del reparto, rol manual fijado y PATCH antes del primer mensaje" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Verificación final integrada

**Files:** ninguno nuevo (solo lectura/verificación; si algo falla, corregir en el task dueño y re-commitear allí).

**Interfaces:** Consumes: Tasks 1–12. Produces: evidencia de cierre.

- [ ] **Step 1: Lint Python de todo lo tocado**

```powershell
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff format --check local_control_center/runtime_team local_control_center/agents/provider_catalog.py local_control_center/agents/provider_catalog_api.py local_control_center/agents/model_gateway_api.py local_control_center/agents/api.py local_control_center/executions/workloads.py local_control_center/threads local_control_center/product_loop/metadata.py local_control_center/product_loop/coordinator.py local_control_center/product_loop/phases local_control_center/remediations tests_py/test_runtime_team_*.py tests_py/test_llama_cpp_provider.py
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -m ruff check local_control_center tests_py
```
Expected: sin cambios pendientes ni errores.

- [ ] **Step 2: Tests Python nuevos + regresión**

```powershell
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_runtime_team_roles.py','tests_py/test_runtime_team_validation.py','tests_py/test_llama_cpp_provider.py','tests_py/test_runtime_team_validate_api.py','tests_py/test_runtime_team_configuration.py','tests_py/test_runtime_team_candidates_api.py','tests_py/test_runtime_team_thread_api.py','tests_py/test_runtime_team_loop_enforcement.py','tests_py/test_runtime_team_execution_gate.py','tests_py/test_model_execution_health.py','tests_py/test_provider_setup_catalog.py','tests_py/test_all_provider_defaults.py','tests_py/test_thread_run_configuration.py','tests_py/test_threads_api.py','tests_py/test_threads_coordinator.py','tests_py/test_threads_operator_notes.py','tests_py/test_decision_engine_remediation.py','tests_py/test_remediation_blocker_experience.py','tests_py/test_product_loop_fsm_matrix.py','tests_py/test_product_loop_state_order_frontend.py','tests_py/test_product_loop_result_taxonomy.py','tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','-q','-p','no:randomly']))"
```
Expected: PASS.

- [ ] **Step 3: Suite larga del coordinator (proceso desacoplado)**

```powershell
H:\Proyectos\Personales\AIDO\.venv\Scripts\python.exe -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['tests_py/test_product_loop_coordinator.py','-q','-p','no:randomly']))"
```
Correr en background (>10 min) y leer el exit real, no la notificación. Expected: PASS; cualquier rojo que no esté en la lista de fallas preexistentes conocidas es regresión de Task 8.

- [ ] **Step 4: Web y contrato**

```powershell
corepack pnpm@10.24.0 run openapi:generate
git diff --exit-code local-control-center/web/src/api/generated/openapi.ts
corepack pnpm@10.24.0 run typecheck:web
corepack pnpm@10.24.0 exec biome check local-control-center/web
corepack pnpm@10.24.0 run build:web
```
Expected: sin drift de OpenAPI (exit 0), typecheck y biome sin errores, build OK.

- [ ] **Step 5: E2E y cierre**

Re-ejecutar el spec de Task 12 en ambos proyectos con `PLAYWRIGHT_DASHBOARD_PORT` fijo sobre el build del Step 4 y además `tests_web/threads.spec.js` y `tests_web/thread-remediations.spec.js` (tocan el composer y las tarjetas de remediación). Verificar `git status` limpio salvo archivos ajenos y que ningún archivo quedó en CRLF (`git diff --check` sin avisos). Sin commit si todo está verde.

## Self-Review

| Requisito del spec | Tasks |
|---|---|
| §1.1 Panel lista runtimes configurados; checkbox solo si prueba real < 30 min con config actual | 2 (predicado), 6 (candidatos), 11 (checkbox), 12 (E2E) |
| §1.2 1 runtime → todos sus roles elegibles y, si cubre PO y Developer, el hilo puede enviarse; N → reparto que se redistribuye al sumar runtimes; operador edita (rol manual fijado) | 1 (reparto + `REQUIRED_TEAM_ROLES` = PO/Developer), 5 (readiness de un solo runtime), 6 (sugerencia backend por `selected`), 10 (merge automático vs manual), 11 (grilla editable + "Repartir automáticamente"), 12 (guardar con Seguridad sin asignar) |
| §1.3 El loop solo usa el conjunto y, por rol, el asignado; sin failover fuera; el developer asignado exige una decisión real del gestor | 5 (sellado/allowlist), 7 (sello en el run dentro de la transacción del mensaje, clave protegida), 8 (equipo, PO, failover, developer con bloqueo si no hay decisión real, seguridad, arquitecto) |
| §1.4 Runtime **asignado** sin validación al ejecutar → bloqueo con "Re-probar runtime" (un seleccionado sin rol no bloquea) | 5 (`only_assigned`), 9 |
| §3.1 `models.validate_runtime` por runtime, clase de carga por runtime, API vía test prompt, CLI vía preflight con aprobación auditada, evidencia sin migración, todo fallo invalida 24 h, predicado 30 min, consumidores de 24 h intactos | 2, 4 |
| §3.2 llama.cpp (local, openai_compatible, 8082, optional bearer, /v1/models, capacidades sembradas, lista UI bilingüe) | 3 |
| §3.3 `GET /api/v1/runtime/team-candidates` con validation/eligibleRoles; elegibilidad en backend con los predicados reales de cada runner (Seguridad = `chat` ∧ `code_review`/`review`); runtime denegado por `runtime_policy_decision` ⇒ `policy_denied` con causa, nunca seleccionable ni sugerido | 1, 5 (facts + veto en PATCH), 6, 10, 11 |
| §3.4 `runConfiguration.allowedRuntimes/roleRuntimes`; PATCH (bloqueado en queued/running, atómico); sellado re-verifica 30 min de todo el conjunto e intersecta con `project.runtime.allowedProviders`; rol **obligatorio** (PO/Developer) sin runtime → 422 sin encolar; Arquitecto sin asignar → no corre, Seguridad sin asignar → solo scanners deterministas; sin selección = comportamiento actual | 5, 7, 8, 9 |
| §3.5 `_team_resource_request`, `_product_owner_resource_selection`, `_failover_replacement`, `_security_execution_resource`, Arquitecto `preferredRuntime`; remediación que **encola** `models.validate_runtime` por runtime y, al pasar, reanuda el retry | 8, 9 |
| §3.6 Reparto: Developer CLI primero, runtime_order, alfabético; PO distinto; Arquitecto/Seguridad reciclan; solo un rol obligatorio (PO/Developer) sin elegible bloquea el envío; uno opcional queda sin asignar; `policy_denied` fuera del reparto | 1, 5, 6, 7, 9, 10, 11, 12 |
| §3.7 Chip "Equipo IA · N de M", Drawer, checkbox/StatusChip/latencia/"Probar", auto-prueba de vencidos con aviso de cuota CLI, grilla con precarga, filas fijas deterministas, i18n | 11 |
| §4 Fallo con causa clasificada (`classify_runtime_failure`) y evidencia redactada; auditoría CLI; evento `run_configuration_updated`; config efectiva en metadata del run | 2, 4 (`evidence` en el contrato), 7 |
| §5 Unit (frescura, elegibilidad, reparto), integración (API/CLI, PATCH, sellado, allowlist en PO/equipo/failover/seguridad, arquitecto), contrato (OpenAPI + EXECUTION_OPERATIONS, catálogo), web (Playwright) | 1–12, 13 |
| §7 Riesgos: cuota CLI (solo vencidos + aviso + auditoría), diferimiento por admisión (fila `deferred` con la razón del governor mientras espera y la causa final), cambio de config (fingerprint al sellar y al ejecutar), GPU compartida (cada prueba encolada reserva `local_gpu_model` para loopback) | 2, 4, 9, 10, 11 |

Placeholders: ninguno; cada símbolo usado existe en el repo (citado con `archivo:línea`) o se define en un task anterior del grafo.

## Execution Notes

- **Carriles y modelo por task:**
  - Modelo más fuerte (HIGH): Tasks 2, 5, 7, 8, 9, 11. Motivo: semántica de validación y aprobación CLI, evidencia de fallo, sellado atómico y superficie no confiable, el archivo de 5958 líneas del coordinator, la reanudación encolada y UX/a11y.
  - Modelo eficiente (PATTERN): Tasks 1, 4, 6, 12. Task 1 pasa de MECHANICAL a PATTERN: la elegibilidad compone los predicados reales de cuatro contratos y sus tests fijan casos límite.
  - Delegables a modelo local y verificables por tests/typecheck (MECHANICAL): Tasks 3, 10, 13.
- **Serialización obligatoria:**
  - `openapi.ts` en orden 4 → 6 → 7 → 9, un agente a la vez. `openapi:generate` hornea cualquier cambio de API sin commitear del árbol, así que hay que regenerar sobre un árbol limpio de trabajo ajeno.
  - `model_gateway_api.py`, `executions/workloads.py` y `tests_py/test_runtime_team_validate_api.py`: Task 4 y luego Task 9.
  - `default_catalog.json` en orden 3 → 9 → 11.
  - `product_loop/coordinator.py` solo en Task 8.
- **Escritor concurrente:** otro agente commitea a `dev` en este mismo checkout (memoria del repo). Antes de cada commit hay que hacer `git fetch` y revisar `git log origin/dev..dev` y el dev local; stagear solo las rutas del task.
- **Riesgos residuales:**
  - Un equipo de un solo CLI envía sin Arquitecto (salvo `claude_code_cli`) ni análisis de modelo de Seguridad: la revisión de arquitectura queda `skipped` y Seguridad corre solo scanners deterministas. Es la regla aprobada (Architect Decision 1); el panel lo explica con la ayuda de rol opcional.
  - Seguridad exige `chat` y `code_review`/`review`: una cuenta Ollama recién configurada (`chat, local, private, streaming`) no es elegible para Seguridad hasta anunciar `code_review`. Coherente con el gestor (`coordinator.py:2516-2524`) y con la tabla del spec §3.3 actualizada (Architect Decision 2).
  - `policy_denied` no se persiste como falla (es decisión del proyecto, no evidencia del runtime). El panel ya no puede mostrar `validated` en un proyecto que veta el runtime: `team-candidates` devuelve `policy_denied` y el PATCH lo rechaza (Architect Decision 3). Si la política cambia después de guardar el equipo, el sellado solo re-intersecta `project.runtime.allowedProviders`; los demás vetos (modo, `runtime.cli.enabled`, `runtime.remote.enabled`) los bloquea `AIResourceManager` en el loop con blocker visible.
  - La reanudación solo la dispara el handler de `models.validate_runtime`. Una validación hecha por otra vía (p. ej. `test-prompt` del gateway) no reanuda el loop; el operador vuelve a pulsar "Re-probar", que reanuda de inmediato si ya no hay vencidos.
  - Un rol del scheduler cuya política prohíba el runtime asignado (p. ej. `allowCli=False` con un CLI asignado) bloquea el loop con un blocker visible de `resource_manager`, no en silencio.
  - Los helpers privados de `model_gateway_api` se importan desde `probe.py`. Es deuda menor y conviene moverlos a un módulo compartido si crecen los consumidores.
  - `_persisted_runtime_order` queda duplicado en `candidates._runtime_order` (4 líneas).
- **No confundir** la remediación existente `validate_runtime` (solo lee readiness, `remediations/service.py:787,1107`) con la nueva `revalidate_runtime` ni con la operación `models.validate_runtime`.
- **Gate 1 (antes de codificar):** que un verificador independiente revise este plan contra el spec, en especial las decisiones 1, 2, 7, 9, 10, 11 y 12, y la sección Architect Decisions (2026-09-22).
- **Gate 2 (primera rebanada real):** tras Task 7, probar a mano con un runtime real (Ollama u OmniRoute): `validate-runtime`, luego PATCH y luego el mensaje sellado.
- **Gate 3:** Task 13, más la revisión de código de un agente distinto del que implementó.

## Cross-Model Review Log

Revisor: Codex `gpt-5.1-codex` (perfil `analysis`, read-only). Cada hallazgo se verificó contra el código real antes de tocar el plan.

| # | Severidad | Hallazgo | Veredicto | Motivo (una línea) |
|---|---|---|---|---|
| 1 | major | Solo PO y Developer obligatorios, contra spec §3.6 | SUPERSEDED | Se aceptó en su momento con los cuatro roles obligatorios; Architect Decision 1 (2026-09-22) cerró la pregunta abierta: spec §3.4/§3.6 y Tasks 1/5/9/10/11/12 usan ahora `REQUIRED_TEAM_ROLES = ("product_owner", "developer")`. |
| 2 | major | Elegibilidad no replica los contratos (Developer sin `chat`, Seguridad sin `chat`) | ACCEPTED | Verificado en `developer_agent_contract.py:94-111` y `security_agent_contract.py:97-111`; `eligible_team_roles` ahora llama a los cuatro predicados reales y suma la capacidad que pide el gestor al rol. |
| 3 | major | Retornos tempranos `failed` sin evidencia dejan vigente un éxito previo | ACCEPTED (salvo `policy_denied`) | `runtime_validation_state` lee solo el último registro; todo `failed` persiste ahora vía `_failed` (con el último modelo si no hubo modelo), con regresión por cada retorno temprano; `policy_denied` no se persiste porque es decisión del proyecto, no evidencia del runtime. |
| 4 | minor | No se usa `classify_runtime_failure` | ACCEPTED | `_failure_cause` usa el clasificador compartido (`runtime_failure_classifier.py:111`) con `reason` estable y `evidence` redactada, y el CLI reutiliza `failureCause`/`failureEvidence` del preflight. |
| 5 | major | PATCH/gate/sellado no atómicos | ACCEPTED | Hay conexiones por request (`control_plane/runtime.py:71-84`) y el gate corría fuera de la transacción de `threads/coordinator.py:152`; PATCH y gate quedan dentro de `immediate_transaction`, con tests que lo fijan. |
| 6 | major | `_assigned_developer_execution_resource` fabrica una selección | ACCEPTED | El fallback saltaba los blockers de `AIResourceManager`; ahora exige una decisión real del proveedor asignado y `prepare_developer_execution` bloquea en `resource_manager` si no la hay. |
| 7 | major | El gate de ejecución revisa todo `allowedRuntimes` | ACCEPTED | El spec §1.4/§3.5 habla del runtime asignado; `assess_runtime_team(..., only_assigned=True)` en el gate de ejecución y el de envío sigue revisando todo el conjunto (spec §3.4). |
| 8 | blocker | La remediación prueba en batch dentro de `remediations.execute` (`agent_cli`) | ACCEPTED | Verificado en `remediations/api.py:105` y `workloads.py:48`; `revalidate_runtime` encola una `models.validate_runtime` por runtime (`enqueue_registered_operation`), reserva `qa_light` y el handler reanuda el retry con `resume_after_runtime_validation`. |
| 9 | major | `deferred` no se persiste ni se ve; el panel descarta el resultado | ACCEPTED (parcial) | No persistir `deferred` se mantiene a propósito (no hubo prueba; persistirlo invalidaría evidencia vigente), pero el panel conserva por fila el último resultado con su causa y muestra `resource_wait` con la razón del governor vía un callback nuevo de `requestCompletedOperation`. |
| 10 | major | `mergeRoleRuntimes` conserva asignaciones automáticas viejas; el mock ignora `selected` | ACCEPTED | El merge distingue roles manuales de automáticos y redistribuye los automáticos; el mock responde según `selected` y hay un E2E nuevo para la redistribución y otro para el rol manual fijado. |

Resultado: 10 aceptados (2 de ellos con alcance acotado y justificado), 0 rechazados.

## Architect Decisions (2026-09-22)

| # | Decisión | Dónde se aplica |
|---|---|---|
| 1 | Solo `product_owner` y `developer` son obligatorios; `architect` y `security` son opcionales. El reparto deja sin asignar un rol opcional sin elegibles. Arquitecto sin asignar ⇒ `ArchitectAgent` no corre en el hilo (review `skipped`). Seguridad sin asignar ⇒ la fase de seguridad corre solo sus scanners deterministas (gitleaks, semgrep, chequeos locales), sin análisis de modelo. El envío (422) y el gate de ejecución bloquean solo si PO o Developer no tienen runtime. Un único runtime elegible para PO y Developer basta para enviar. | Spec §1.2, §3.4, §3.5, §3.6, §5; Decisión 1; Task 1 (`REQUIRED_TEAM_ROLES`, `OPTIONAL_TEAM_ROLES`, tests), Task 5 (readiness, test de un solo runtime), Task 8 (ramas `skipped`/sin análisis como camino normal), Task 9 (`missingRoles == ["product_owner"]`, test de roles opcionales), Task 10 (constantes TS), Task 11 (error solo en roles obligatorios, ayuda de rol opcional, copy), Task 12 (guardar con Seguridad sin asignar) |
| 2 | Elegibilidad de Seguridad = `chat` **y** `code_review`/`review`: predicado real del runner (`is_security_model_runtime`) más la capacidad `review` que `AIResourceManager` exige al rol. | Spec §3.3 (tabla); Decisión 2; Task 1 (docstring de `eligible_team_roles`, tests existentes ya lo fijan); Execution Notes |
| 3 | `team-candidates` marca como `policy_denied` (con la causa) todo runtime que `runtime_policy_decision` deniega para el proyecto; nunca es seleccionable en ese proyecto: fuera del reparto sugerido, checkbox y "Probar" deshabilitados, y el PATCH lo rechaza con 422. | Spec §3.3; Decisión 11; Task 1 (`RuntimeFacts.policy_denied_reason`), Task 5 (`load_runtime_facts` + veto en `write_thread_runtime_team` + test), Task 6 (Literal `policy_denied`, `_validation_record`, pool, test), Task 10 (`validationTone`), Task 11 (fila vetada + i18n) |
