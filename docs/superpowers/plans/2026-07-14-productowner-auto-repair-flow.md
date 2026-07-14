# ProductOwnerAgent Auto-Repair Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el ProductOwnerAgent deje de morir en `blocked` por un `category` sinónimo o un roce de esquema recuperable, y que ningún hilo bloqueado quede sin acción de reparación.

**Architecture:** Tres cambios de backend, independientes y probados por TDD. A1 tolera/coerce `category` en el motor compartido de preguntas. A2 envuelve la ejecución+validación del runtime en un bucle de reparación acotado in-process. A4 garantiza que un hilo bloqueado siempre reciba una acción `retry_loop` aunque ningún loop esté en estado exactamente `"blocked"`.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, SQLite. Sin dependencias nuevas. Backend puro (sin cambios de web/i18n/OpenAPI/migraciones).

**Spec de referencia:** `docs/superpowers/specs/2026-07-14-productowner-auto-repair-flow-design.md`.

## Global Constraints

- Line endings **LF** en todo archivo tocado; verificar que `git` no emita "CRLF will be replaced".
- `ruff format` + `ruff check` limpios en cada archivo productivo modificado (`impact_question_engine.py`, `product_owner_agent.py`, `remediations/service.py`).
- **No** crear módulos productivos nuevos → no se dispara el gate `@author`. Las funciones/métodos públicos nuevos llevan docstring (D103/D102).
- **No** cambiar el `outputSchema` del contrato: `questions.category` sigue enumerando los 9 valores canónicos como fuente de verdad; la coerción ocurre **antes** del check estricto.
- Formato de commit: `Tipo (Ámbito): mensaje detallado` (ej. `Fix (ProductOwner): ...`). Terminar el cuerpo con la línea `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Ejecutar tests con `uv run pytest`.
- Categorías canónicas (fuente de verdad, no tocar): `scope, users, data, integration, compliance, nonfunctional, ux, risk, delivery`. Default de desconocidos: `scope`.
- **A3 (feedback en `retry_loop`) queda DIFERIDO** a un fast-follow: A1 neutraliza la recurrencia de `category` y A2 entrega reparación automática in-process, por lo que el reintento manual rara vez necesita el feedback; su cableado (`runMetadata` → payload del PO) requiere una traza aparte. No se implementa aquí.

---

## File Structure

- `local_control_center/agents/impact_question_engine.py` — A1: `CATEGORY_ALIASES`, `DEFAULT_QUESTION_CATEGORY`, `normalize_category`, logger de auditoría, y `validate_impact_question` tolerante.
- `local_control_center/agents/product_owner_agent.py` — A2: `PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS`, `_repair_instruction`, parámetro `repair` en `cli_prompt`/`model_messages`, helper `_execute_once`, y bucle de reparación en `_execute_and_persist`.
- `local_control_center/remediations/service.py` — A4: `_fallback_loop_for_thread` y ruta de respaldo en `ensure_blocked_thread_remediation`.
- `tests_py/test_impact_question_engine.py` — tests A1.
- `tests_py/test_product_owner_agent_real_runtime.py` — cola de respuestas en el mock provider + tests A2.
- `tests_py/test_remediation_blocker_experience.py` — test A4.

---

## Task 1: A1 — Tolerancia y coerción de `category`

**Files:**
- Modify: `local_control_center/agents/impact_question_engine.py:14-27` (imports/constantes) y `:75-123` (`validate_impact_question`)
- Test: `tests_py/test_impact_question_engine.py`

**Interfaces:**
- Produces: `normalize_category(raw: Any) -> tuple[str, str | None]` (canónica, valor_original_si_coercido); constantes `CATEGORY_ALIASES: dict[str, str]`, `DEFAULT_QUESTION_CATEGORY: str`. `validate_impact_question` mantiene su firma y su dict de retorno de 8 campos.

- [ ] **Step 1: Escribir los tests A1 (fallan)**

En `tests_py/test_impact_question_engine.py`, (a) actualizar la parametrización de rechazos quitando la fila de `category`, (b) repuntar el test de propagación a `confidence`, y (c) agregar dos tests nuevos. Reemplazar el bloque `test_validate_impact_question_rejects_invalid_contracts` (líneas 49-63) por:

```python
@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"confidence": "certain"}, "confidence"),
        ({"blocking": "yes"}, "blocking"),
        ({"options": ["only-one"]}, "options"),
        ({"whyItMatters": ""}, "whyItMatters"),
        ({"recommendation": "Desktop"}, "recommendation"),
        ({"defaultDecision": "Desktop"}, "defaultDecision"),
    ],
)
def test_validate_impact_question_rejects_invalid_contracts(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ImpactQuestionValidationError, match=fragment):
        validate_impact_question(question(**overrides))


def test_validate_impact_question_coerces_known_category_synonyms() -> None:
    assert validate_impact_question(question(category="Performance"))["category"] == "nonfunctional"
    assert validate_impact_question(question(category="usability"))["category"] == "ux"
    assert validate_impact_question(question(category="security"))["category"] == "risk"
    assert validate_impact_question(question(category="timeline"))["category"] == "delivery"


def test_validate_impact_question_defaults_unknown_category_and_audits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        normalized = validate_impact_question(question(category="totally-made-up"))
    assert normalized["category"] == "scope"
    assert any("totally-made-up" in record.getMessage() for record in caplog.records)
```

Y reemplazar `test_engine_validation_propagates_for_malformed_candidates` (líneas 107-109) por:

```python
def test_engine_validation_propagates_for_malformed_candidates() -> None:
    with pytest.raises(ImpactQuestionValidationError, match=r"questions\[1\].confidence"):
        ImpactQuestionEngine().select([question(), question(confidence="bogus")])
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `uv run pytest tests_py/test_impact_question_engine.py -q`
Expected: FAIL — `test_validate_impact_question_coerces_known_category_synonyms` y `..._defaults_unknown_category_and_audits` fallan con `ImpactQuestionValidationError: questions[0].category must be one of [...]` (aún no existe la coerción).

- [ ] **Step 3: Implementar A1**

En `impact_question_engine.py`, agregar `import logging` al inicio (junto a `import re`, línea 14) y un logger de módulo bajo las constantes. Tras la definición de `QUESTION_CATEGORIES` (línea 27) agregar:

```python
logger = logging.getLogger(__name__)

DEFAULT_QUESTION_CATEGORY = "scope"
CATEGORY_ALIASES = {
    # nonfunctional
    "performance": "nonfunctional", "scalability": "nonfunctional", "reliability": "nonfunctional",
    "availability": "nonfunctional", "latency": "nonfunctional", "throughput": "nonfunctional",
    "maintainability": "nonfunctional", "observability": "nonfunctional", "quality": "nonfunctional",
    "technical": "nonfunctional", "architecture": "nonfunctional", "infrastructure": "nonfunctional",
    "operational": "nonfunctional", "ops": "nonfunctional", "non-functional": "nonfunctional",
    "non functional": "nonfunctional", "nfr": "nonfunctional", "resilience": "nonfunctional",
    "capacity": "nonfunctional",
    # ux
    "usability": "ux", "design": "ux", "ui": "ux", "user experience": "ux",
    "user-experience": "ux", "accessibility": "ux", "a11y": "ux", "interaction": "ux",
    # scope
    "functional": "scope", "feature": "scope", "features": "scope", "functionality": "scope",
    "mvp": "scope", "requirement": "scope", "requirements": "scope", "boundaries": "scope",
    # delivery
    "timeline": "delivery", "schedule": "delivery", "cost": "delivery", "budget": "delivery",
    "resourcing": "delivery", "rollout": "delivery", "release": "delivery", "deployment": "delivery",
    "milestone": "delivery", "milestones": "delivery", "planning": "delivery", "roadmap": "delivery",
    "effort": "delivery", "estimate": "delivery", "estimation": "delivery",
    # compliance
    "legal": "compliance", "regulatory": "compliance", "regulation": "compliance",
    "regulations": "compliance", "privacy": "compliance", "gdpr": "compliance", "hipaa": "compliance",
    "data privacy": "compliance", "data protection": "compliance", "governance": "compliance",
    "policy": "compliance", "licensing": "compliance",
    # risk
    "security": "risk", "threat": "risk", "vulnerability": "risk", "safety": "risk",
    "business risk": "risk", "dependency": "risk", "dependencies": "risk", "uncertainty": "risk",
    # data
    "database": "data", "storage": "data", "schema": "data", "data model": "data",
    "data modeling": "data", "analytics": "data", "data retention": "data", "persistence": "data",
    "migration": "data",
    # integration
    "api": "integration", "apis": "integration", "third party": "integration",
    "third-party": "integration", "interoperability": "integration", "integrations": "integration",
    "external": "integration", "webhook": "integration", "webhooks": "integration",
    "connectivity": "integration", "interface": "integration",
    # users
    "user": "users", "persona": "users", "personas": "users", "audience": "users",
    "stakeholder": "users", "stakeholders": "users", "target users": "users",
    "customer": "users", "customers": "users", "roles": "users",
}


def normalize_category(raw: Any) -> tuple[str, str | None]:
    """Coerce a question category to a canonical value.

    Returns ``(canonical, coerced_from)`` where ``coerced_from`` is ``None`` when the value was
    already canonical, the original (lowercased) value when mapped via an alias, and the original
    value when it was unknown and fell back to ``DEFAULT_QUESTION_CATEGORY``.
    """
    value = str(raw or "").strip().lower()
    if value in QUESTION_CATEGORIES:
        return value, None
    mapped = CATEGORY_ALIASES.get(value)
    if mapped is not None:
        return mapped, value
    return DEFAULT_QUESTION_CATEGORY, value
```

Reemplazar el check estricto en `validate_impact_question` (líneas 85-89):

```python
    category = str(item.get("category") or "").strip().lower()
    if category not in QUESTION_CATEGORIES:
        raise ImpactQuestionValidationError(
            f"questions[{index}].category must be one of {sorted(QUESTION_CATEGORIES)}."
        )
```

por:

```python
    category, coerced_from = normalize_category(item.get("category"))
    if coerced_from is not None:
        logger.warning(
            "questions[%s].category %r coerced to %r (category only affects ranking/dedup).",
            index,
            coerced_from,
            category,
        )
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `uv run pytest tests_py/test_impact_question_engine.py -q`
Expected: PASS (todos).

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff format local_control_center/agents/impact_question_engine.py tests_py/test_impact_question_engine.py
uv run ruff check local_control_center/agents/impact_question_engine.py tests_py/test_impact_question_engine.py
git add local_control_center/agents/impact_question_engine.py tests_py/test_impact_question_engine.py
git commit -m "Fix (ProductOwner): tolera y coerce category de preguntas al enum canonico en vez de bloquear

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: A2 — Reintento de reparación acotado in-process

**Files:**
- Modify: `local_control_center/agents/product_owner_agent.py` — constante nueva (~línea 84), prompt builders (`cli_prompt` :364-367, `model_messages` :352-362) + `_repair_instruction` nuevo, helper `_execute_once` nuevo, y bucle en `_execute_and_persist` (:1262-1318).
- Test: `tests_py/test_product_owner_agent_real_runtime.py`

**Interfaces:**
- Consumes: `normalize_category` (Task 1, indirecto vía `validate_output`).
- Produces: `PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS: int = 2`; `ProductOwnerAgent.cli_prompt(*, idea, assessment, repair=None)`, `ProductOwnerAgent.model_messages(*, idea, assessment, repair=None)`, `ProductOwnerAgent._repair_instruction(repair: dict) -> str`; `ProductOwnerAgentRunner._execute_once(...) -> dict` con `kind ∈ {"ok","invalid","infra_failed"}`.

- [ ] **Step 1: Extender el mock provider con una cola de respuestas (soporte de test)**

En `tests_py/test_product_owner_agent_real_runtime.py`, en `ControlledProductOwnerProviderHandler` (línea 162) agregar dos atributos de clase y un helper, y usarlos en ambos `do_POST` (rutas `/messages` y `/chat/completions`). Cambiar la cabecera de la clase:

```python
class ControlledProductOwnerProviderHandler(BaseHTTPRequestHandler):
    response_content = "{}"
    response_queue: list[str] = []
    chat_post_count = 0

    def _next_content(self) -> str:
        cls = type(self)
        cls.chat_post_count += 1
        if cls.response_queue:
            return cls.response_queue.pop(0)
        return cls.response_content
```

En `do_POST`, reemplazar los dos usos de `self.response_content` (líneas 183 y 199) por `self._next_content()`.

Reemplazar `start_controlled_provider` (líneas 213-219) para resetear el estado de clase y aceptar una secuencia opcional:

```python
def start_controlled_provider(
    content: str, *, contents: list[str] | None = None
) -> tuple[ThreadingHTTPServer, str]:
    ControlledProductOwnerProviderHandler.response_content = content
    ControlledProductOwnerProviderHandler.response_queue = list(contents or [])
    ControlledProductOwnerProviderHandler.chat_post_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledProductOwnerProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"
```

Y en `run_with_controlled_provider` (línea 496) agregar el parámetro `contents` y pasarlo. Cambiar la firma y la llamada interna:

```python
def run_with_controlled_provider(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str,
    body: dict,
    runtime_id: str = "openai_compatible",
    contents: list[str] | None = None,
):
    server, base_url = start_controlled_provider(content, contents=contents)
```

- [ ] **Step 2: Escribir los tests A2 (fallan)**

Agregar al final de `tests_py/test_product_owner_agent_real_runtime.py`:

```python
def test_product_owner_repairs_invalid_output_on_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-repair-ok")

    invalid = json.dumps({"brief": {"title": "Partial"}})  # faltan campos requeridos
    valid = json.dumps(product_owner_output(blocking=False))  # brief_ready

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=valid,
        contents=[invalid, valid],
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "brief_ready"
    assert ControlledProductOwnerProviderHandler.chat_post_count == 2


def test_product_owner_stops_after_repair_attempt_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, client, headers = create_client(tmp_path, monkeypatch)
    project, workspace = create_project_and_workspace(store, tmp_path, task_id="po-repair-cap")

    invalid = json.dumps({"brief": {"title": "Partial"}})

    response = run_with_controlled_provider(
        client,
        headers,
        monkeypatch,
        content=invalid,
        contents=[invalid, invalid],
        body=product_owner_request(project, workspace),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "failed_validation"
    assert body["output"] is None
    assert ControlledProductOwnerProviderHandler.chat_post_count == 2
    assert ProductDiscoveryRepository(store.connection).list_initiatives(project["id"]) == []
```

- [ ] **Step 3: Correr los tests para verificar que fallan**

Run: `uv run pytest tests_py/test_product_owner_agent_real_runtime.py -k "repair" -q`
Expected: FAIL — `test_product_owner_repairs_invalid_output_on_second_attempt` falla con `body["status"] == "failed_validation"` (hoy no hay reintento; se ejecuta 1 vez y falla) y `chat_post_count == 1`.

- [ ] **Step 4: Implementar A2 — constante + prompts de reparación**

En `product_owner_agent.py`, agregar la constante junto a las demás (tras `ASSESSMENT_SIGNAL_LIMIT`, ~línea 89):

```python
PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS = 2
```

Reemplazar `model_messages` (líneas 352-362) y `cli_prompt` (líneas 364-367) por versiones con `repair`, y agregar `_repair_instruction`:

```python
    def model_messages(
        self, *, idea: str, assessment: dict[str, Any], repair: dict[str, Any] | None = None
    ) -> list[dict[str, str]]:
        """Arma los mensajes system/user para el runtime de modelo, exigiendo solo JSON del esquema."""
        messages = [
            {"role": "system", "content": self._system_instruction()},
            {
                "role": "user",
                "content": json_dumps(
                    redact_secrets(self._assessment_context(idea=idea, assessment=assessment))
                ),
            },
        ]
        if repair:
            messages.append({"role": "user", "content": self._repair_instruction(repair)})
        return messages

    def cli_prompt(
        self, *, idea: str, assessment: dict[str, Any], repair: dict[str, Any] | None = None
    ) -> str:
        """Arma el prompt de una sola pieza para un runtime CLI real, exigiendo solo JSON del esquema."""
        context = json_dumps(redact_secrets(self._assessment_context(idea=idea, assessment=assessment)))
        prompt = f"{self._system_instruction()}\n\nInput context (JSON):\n{context}\n"
        if repair:
            prompt += self._repair_instruction(repair)
        return prompt

    def _repair_instruction(self, repair: dict[str, Any]) -> str:
        """Instrucción de reparación: adjunta el error de validación previo y la salida inválida acotada."""
        error = _bounded_text(repair.get("error"), limit=2_000)
        previous = _bounded_text(repair.get("previousOutput"), limit=4_000)
        return (
            "\n\nYour previous response FAILED strict validation with this error: "
            + error
            + "\nPrevious output was:\n"
            + previous
            + "\nReturn corrected JSON ONLY (no markdown fences), fixing exactly that error "
            "and keeping every other field valid."
        )
```

- [ ] **Step 5: Implementar A2 — helper `_execute_once` + bucle**

En `ProductOwnerAgentRunner`, agregar el helper `_execute_once` (justo antes de `_execute_and_persist`, ~línea 1232):

```python
    def _execute_once(
        self,
        *,
        payload: dict[str, Any],
        runtime: dict[str, Any],
        workspace: dict[str, Any],
        agent_run: dict[str, Any],
        job: dict[str, Any],
        profile: dict[str, Any],
        broker: ToolBroker,
        assessment: dict[str, Any],
        detected_facts: set[str],
        repair: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Ejecuta el runtime una vez y valida su salida.

        Returns:
            ``{"kind": "ok", output, selection, runtimeResult, outputArtifactId}`` en éxito;
            ``{"kind": "invalid", reason, runtimeResult, outputText, outputArtifactId}`` si la salida
            no valida; ``{"kind": "infra_failed", reason, runtimeResult}`` si el runtime no completó.
        """
        runtime_id = str(runtime["id"])
        if runtime_id in PRODUCT_OWNER_AGENT_CLI_RUNTIMES:
            try:
                runtime_result = self._execute_cli_runtime(
                    payload=payload,
                    runtime=runtime,
                    workspace=workspace,
                    agent_run=agent_run,
                    job=job,
                    profile=profile,
                    broker=broker,
                    prompt=self.agent.cli_prompt(
                        idea=assessment["idea"], assessment=assessment, repair=repair
                    ),
                )
            except RuntimeCommandUnavailableError as error:
                return {
                    "kind": "infra_failed",
                    "reason": str(error),
                    "runtimeResult": {"status": "failed", "reason": str(error)},
                }
        else:
            runtime_result = self._execute_model_runtime(
                payload=payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                messages=self.agent.model_messages(
                    idea=assessment["idea"], assessment=assessment, repair=repair
                ),
            )
        if runtime_result["status"] != "completed":
            return {
                "kind": "infra_failed",
                "reason": str(
                    runtime_result.get("reason") or "ProductOwnerAgent runtime execution failed."
                ),
                "runtimeResult": runtime_result,
            }
        try:
            runtime_output = self._runtime_output_text(runtime_result)
        except ProductOwnerOutputValidationError as error:
            return {
                "kind": "invalid",
                "reason": str(error),
                "runtimeResult": runtime_result,
                "outputText": "",
                "outputArtifactId": None,
            }
        try:
            output = self.agent.validate_output(self._json_object_from_text(runtime_output["text"]))
            selection = ImpactQuestionEngine().select(output["questions"], detected_facts=detected_facts)
        except (ProductOwnerOutputValidationError, ImpactQuestionValidationError) as error:
            return {
                "kind": "invalid",
                "reason": str(error),
                "runtimeResult": runtime_result,
                "outputText": runtime_output["text"],
                "outputArtifactId": runtime_output["artifactId"],
            }
        return {
            "kind": "ok",
            "output": output,
            "selection": selection,
            "runtimeResult": runtime_result,
            "outputArtifactId": runtime_output["artifactId"],
        }
```

Reemplazar el bloque de ejecución+validación de `_execute_and_persist` (desde la línea 1262 `broker = ToolBroker(...)` hasta la línea 1318 `output["questionSelection"] = selection["counts"]`) por:

```python
        broker = ToolBroker(self.connection, artifact_root=self.root)
        detected_facts = detected_facts_from_assessment(
            brief=assessment.get("brief"),
            existing_questions=assessment.get("openQuestions"),
        )
        repair: dict[str, Any] | None = None
        attempt_result: dict[str, Any] = {}
        for _attempt in range(PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS):
            attempt_result = self._execute_once(
                payload=payload,
                runtime=runtime,
                workspace=workspace,
                agent_run=agent_run,
                job=job,
                profile=profile,
                broker=broker,
                assessment=assessment,
                detected_facts=detected_facts,
                repair=repair,
            )
            result["runtimeResult"] = attempt_result["runtimeResult"]
            if attempt_result.get("outputArtifactId"):
                result["outputArtifactId"] = attempt_result["outputArtifactId"]
            if attempt_result["kind"] == "ok":
                break
            if attempt_result["kind"] == "infra_failed":
                result["status"] = "failed"
                result["reason"] = attempt_result["reason"]
                return result
            repair = {
                "error": attempt_result["reason"],
                "previousOutput": attempt_result.get("outputText", ""),
            }
        if attempt_result["kind"] != "ok":
            result["status"] = FAILED_VALIDATION_STATUS
            result["reason"] = attempt_result["reason"]
            return result
        output = attempt_result["output"]
        selection = attempt_result["selection"]
        output["questions"] = selection["turn"]
        output["deferredQuestions"] = selection["deferred"]
        output["suppressedQuestions"] = selection["suppressed"]
        output["questionSelection"] = selection["counts"]
```

Nota: `detected_facts_from_assessment` e `ImpactQuestionEngine` ya están importados (líneas 39-44). La persistencia posterior (línea 1320 en adelante) queda intacta.

- [ ] **Step 6: Correr los tests A2 para verificar que pasan**

Run: `uv run pytest tests_py/test_product_owner_agent_real_runtime.py -k "repair" -q`
Expected: PASS (2 tests).

- [ ] **Step 7: Correr la suite del PO completa (regresión, incluye el failed_validation existente)**

Run: `uv run pytest tests_py/test_product_owner_agent_real_runtime.py -q`
Expected: PASS. `test_product_owner_agent_invalid_output_fails_validation_without_persistence` sigue verde: el mock devuelve el mismo contenido inválido en el reintento → `failed_validation` tras el tope (sin cola, `response_content` se repite).

- [ ] **Step 8: Ruff + commit**

```bash
uv run ruff format local_control_center/agents/product_owner_agent.py tests_py/test_product_owner_agent_real_runtime.py
uv run ruff check local_control_center/agents/product_owner_agent.py tests_py/test_product_owner_agent_real_runtime.py
git add local_control_center/agents/product_owner_agent.py tests_py/test_product_owner_agent_real_runtime.py
git commit -m "Fix (ProductOwner): reintento de reparacion acotado in-process ante salida invalida del runtime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: A4 — Garantía "ningún bloqueo sin salida"

**Files:**
- Modify: `local_control_center/remediations/service.py` — `ensure_blocked_thread_remediation` (:248-305) y nuevo `_fallback_loop_for_thread` (junto a `_blocked_loop_for_thread`, :307-318).
- Test: `tests_py/test_remediation_blocker_experience.py`

**Interfaces:**
- Produces: `BlockerRemediationService._fallback_loop_for_thread(*, thread_id: str, project_id: str) -> dict[str, Any] | None`.

- [ ] **Step 1: Escribir el test A4 (falla)**

Agregar al final de `tests_py/test_remediation_blocker_experience.py` (usa `json`, `open_sqlite_connection`, `initialize_platform_schema`, `_project_and_thread`, `ProductLoopCoordinator`, `BlockerRemediationService`, `ThreadsRepository` ya importados; agregar `import json` al inicio si falta):

```python
def test_blocked_thread_without_a_blocked_loop_still_gets_a_retry_action(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project, thread = _project_and_thread(connection, tmp_path, "blocked-thread-no-blocked-loop")
        coordinator = ProductLoopCoordinator(connection, root=tmp_path)
        loop = coordinator.start(project_id=project["id"], title="Diverged state loop")

        # Divergencia: el loop dejó de estar "blocked" (cascada de reintentos + worker detenido),
        # pero conserva el durable del bloqueo y el hilo sigue marcado como blocked.
        durable = {
            "durableRun": {
                "thread": {"projectThreadId": thread["id"]},
                "status": "blocked",
                "blockedStage": "product_owner",
                "blockedReason": (
                    "ProductOwnerAgent output must be a JSON object: questions[0].category "
                    "must be one of ['compliance', 'data', 'delivery']."
                ),
            }
        }
        connection.execute(
            "UPDATE product_loops SET state = 'cancelled', context = ? WHERE id = ?",
            (json.dumps(durable), loop["id"]),
        )
        ThreadsRepository(connection).set_status(thread["id"], "blocked")

        service = BlockerRemediationService(connection, root=tmp_path)
        actions = service.list_for_thread(thread_id=thread["id"])

        assert any(action["actionType"] == "retry_loop" for action in actions)
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `uv run pytest tests_py/test_remediation_blocker_experience.py::test_blocked_thread_without_a_blocked_loop_still_gets_a_retry_action -q`
Expected: FAIL — `actions` queda vacío (ningún loop en estado `"blocked"` → `_blocked_loop_for_thread` devuelve `None` → sin backfill), así que el `assert any(...)` falla.

- [ ] **Step 3: Implementar A4**

En `service.py`, agregar `_fallback_loop_for_thread` justo después de `_blocked_loop_for_thread` (tras la línea 318):

```python
    def _fallback_loop_for_thread(
        self, *, thread_id: str, project_id: str
    ) -> dict[str, Any] | None:
        """Loop más reciente del hilo cuando ninguno está en estado exactamente ``"blocked"``.

        Prioriza el loop más reciente cuyo durable ya registró un ``blockedReason`` (fue bloqueado
        alguna vez); si ninguno lo tiene, cae al loop más reciente del hilo. Orden determinista por
        ``created_at``/``id`` para no depender de un orden inestable.
        """
        from local_control_center.product_loop.coordinator import ProductLoopCoordinator

        coordinator = ProductLoopCoordinator(self.connection, root=self.root)
        candidates: list[dict[str, Any]] = []
        for loop in coordinator.list_loops(project_id):
            durable = dict((loop.get("context") or {}).get("durableRun") or {})
            thread_ref = durable.get("thread") if isinstance(durable.get("thread"), dict) else {}
            if str(thread_ref.get("projectThreadId") or "") == thread_id:
                candidates.append(loop)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")))
        blocked_once = [
            loop
            for loop in candidates
            if str(
                ((loop.get("context") or {}).get("durableRun") or {}).get("blockedReason") or ""
            ).strip()
        ]
        return (blocked_once or candidates)[-1]
```

En `ensure_blocked_thread_remediation`, reemplazar la línea 265-267:

```python
        loop = self._blocked_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
        if loop is None:
            return actions
```

por:

```python
        loop = self._blocked_loop_for_thread(
            thread_id=thread_id, project_id=thread["projectId"]
        ) or self._fallback_loop_for_thread(thread_id=thread_id, project_id=thread["projectId"])
        if loop is None:
            return actions
```

- [ ] **Step 4: Correr el test para verificar que pasa**

Run: `uv run pytest tests_py/test_remediation_blocker_experience.py::test_blocked_thread_without_a_blocked_loop_still_gets_a_retry_action -q`
Expected: PASS.

- [ ] **Step 5: Correr la suite de remediación completa (regresión)**

Run: `uv run pytest tests_py/test_remediation_blocker_experience.py -q`
Expected: PASS (todos).

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff format local_control_center/remediations/service.py tests_py/test_remediation_blocker_experience.py
uv run ruff check local_control_center/remediations/service.py tests_py/test_remediation_blocker_experience.py
git add local_control_center/remediations/service.py tests_py/test_remediation_blocker_experience.py
git commit -m "Fix (Remediation): garantiza retry_loop en hilo bloqueado sin loop en estado blocked

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Verificación integral y publicación

**Files:** ninguno (solo verificación).

- [ ] **Step 1: Regresión de las suites impactadas**

Run: `uv run pytest tests_py/test_impact_question_engine.py tests_py/test_product_owner_agent_real_runtime.py tests_py/test_product_owner_assessment_grounding.py tests_py/test_workflow_product_owner_intake.py tests_py/test_remediation_blocker_experience.py tests_py/test_aido_product_loop_real_e2e.py -q`
Expected: PASS. Si algo falla, es señal real (no baseline roto) — diagnosticar y corregir antes de seguir.

- [ ] **Step 2: Gate completo `test:py` (arquitectura + i18n)**

Run: `uv run pytest tests_py -q` (o el alias `test:py` del repo).
Expected: PASS. Este gate incluye las validaciones de arquitectura/documentación; leer el exit code real, no la notificación.

- [ ] **Step 3: Ruff global de los archivos productivos tocados**

Run:
```bash
uv run ruff format --check local_control_center/agents/impact_question_engine.py local_control_center/agents/product_owner_agent.py local_control_center/remediations/service.py
uv run ruff check local_control_center/agents/impact_question_engine.py local_control_center/agents/product_owner_agent.py local_control_center/remediations/service.py
```
Expected: sin cambios pendientes, sin errores.

- [ ] **Step 4: Verificar LF (sin CRLF introducido)**

Run: `git diff --stat HEAD~3` y confirmar que ningún archivo aparece con conversión CRLF; `git ls-files --eol local_control_center/agents/impact_question_engine.py local_control_center/agents/product_owner_agent.py local_control_center/remediations/service.py` debe mostrar `lf`.
Expected: todos `lf`.

- [ ] **Step 5: Publicar a `dev` (norma AIDO: commit+push por funcionalidad)**

Re-sincronizar antes de publicar (otro agente puede haber empujado a `dev`):
```bash
git fetch origin dev
git rebase origin/dev   # resolver conflictos solo en los archivos de esta funcionalidad si aparecen
git push origin dev
```
Expected: push aceptado. Si el rebase toca archivos ajenos con conflicto, detenerse y reportar (no resolver a ciegas trabajo de otro agente).

---

## Self-Review

**Spec coverage:**
- A1 (tolerancia `category`) → Task 1. C1/C2 cubiertos por `test_validate_impact_question_coerces_known_category_synonyms` y `..._defaults_unknown_category_and_audits`.
- A2 (reintento acotado) → Task 2. C3 → `test_product_owner_repairs_invalid_output_on_second_attempt`; C4 → `test_product_owner_stops_after_repair_attempt_cap`.
- A4 (ningún bloqueo sin salida) → Task 3. C6 → `test_blocked_thread_without_a_blocked_loop_still_gets_a_retry_action`.
- C5 (suites verdes + `test:py` + ruff) → Task 4.
- Cambios de comportamiento del §5 del spec (2 tests que cambian) → Task 1, Step 1.
- A3 → **diferido explícitamente** (documentado en Global Constraints), dentro de lo aprobado ("opcional/menor prioridad").

**Placeholder scan:** sin "TBD"/"TODO"/"handle edge cases"; todo paso con código muestra el código.

**Type consistency:** `normalize_category` retorna `tuple[str, str | None]` y se consume así en `validate_impact_question`. `_execute_once` retorna dict con `kind ∈ {"ok","invalid","infra_failed"}`, consumido consistentemente en el bucle. `_fallback_loop_for_thread` retorna `dict | None`, encadenado con `or` a `_blocked_loop_for_thread` (misma forma).
