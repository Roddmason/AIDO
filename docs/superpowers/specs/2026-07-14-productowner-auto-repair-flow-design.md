# Diseño — Sub-proyecto A: Flujo auto-reparable del ProductOwnerAgent

- **Fecha:** 2026-07-14
- **Autor:** Rodrigo Mason (con asistencia)
- **Estado:** Aprobado para plan de implementación
- **Alcance:** Solo Sub-proyecto A. El Sub-proyecto B (enrutamiento consciente de uso de CLI/API) tendrá su propio spec.

## 1. Contexto y problema

El `ProductOwnerAgent` cae a `blocked` de forma terminal cuando el runtime real (un CLI como `codex_cli`/`claude_code_cli`, que van **primero** en el orden de preferencia) emite un `category` cercano pero fuera del enum, o cualquier otro roce de esquema. El bloqueo observado en producción fue una cascada (`#299 → #300 → #301`) con el motivo:

```
ProductOwnerAgent output must be a JSON object: questions[0].category must be one of
['compliance', 'data', 'delivery', 'integration', 'nonfunctional', 'risk', 'scope', 'users', 'ux'].
```

### Causa raíz (evidencia)

1. **Validación estricta sin tolerancia.** `validate_impact_question` normaliza a `strip().lower()` y luego exige pertenencia estricta a `QUESTION_CATEGORIES`, sin mapa de sinónimos ni default — `local_control_center/agents/impact_question_engine.py:85-89`. Sinónimos habituales del modelo (`performance`, `technical`, `security`, `non-functional`, `usability`, `functional`, `cost`) fallan de inmediato.
2. **El prompt NO es la causa.** El sistema ya enumera las categorías permitidas, tanto en prosa como incrustando el `outputSchema` completo — `local_control_center/agents/product_owner_agent.py:369-386`. La falla es puramente de la capa de parseo/validación.
3. **Single-shot, sin reparación.** Un `ImpactQuestionValidationError`/`ProductOwnerOutputValidationError` marca `failed_validation`, descarta **toda** la salida (`output=None`) y retorna, sin reintento ni re-prompt — `local_control_center/agents/product_owner_agent.py:1300-1314`.
4. **Doble validación coherente.** El coordinador **re-valida** la salida del runner llamando de nuevo a `ProductOwnerAgent().validate_output(...)` — `local_control_center/product_loop/coordinator.py:1474`. Como el runner ya devolvió `output=None`, el coordinador arma el mensaje compuesto `"ProductOwnerAgent output must be a JSON object: <reason>"` (`coordinator.py:1452-1455`) y llama a `_block_run(stage="product_owner", ...)` (`coordinator.py:3892-3914`). Ambos puntos de validación pasan por el **mismo** `validate_impact_question`, por lo que corregir la fuente los corrige a los dos.
5. **El `retry_loop` re-encola el mismo mensaje sin el error.** La remediación re-lanza el job original sin inyectar el motivo de validación — `local_control_center/remediations/service.py:1129-1151` — por lo que un modelo que reincide en el sinónimo re-bloquea de forma determinista (la cascada `#299 → #301`).
6. **Inconsistencia de rigor.** `decisions.category` ya es libre (acepta `category` o `type`, sin enum) — `local_control_center/agents/product_owner_agent.py:504` — mientras `questions.category` es estricto. El mismo concepto se valida distinto.

El `category` **solo alimenta priorización y deduplicación** de preguntas (`impact_score`, `CATEGORY_IMPACT`, `question_dedup_key`, `BRIEF_FIELD_CATEGORY`); **no afecta la corrección** del brief ni del backlog. Botar toda la salida por un sinónimo cosmético es fragilidad desproporcionada.

## 2. Objetivo y criterio de éxito verificable

Una corrida cuyo runtime emite un `category` cercano (u otro roce de esquema recuperable) deja de morir en `blocked`: se auto-corrige y produce un brief/backlog válido. Si la salida es genuinamente irrecuperable, bloquea con motivo claro **tras un tope de intentos**, no al primer roce.

**Criterios verificables:**

- C1. `validate_impact_question(question(category="Performance"))["category"] == "nonfunctional"` (y equivalentes del mapa de sinónimos).
- C2. `validate_impact_question(question(category="<desconocido>"))["category"] == "scope"` (default seguro) y se registra una nota de auditoría; **no** lanza.
- C3. Un runtime que devuelve JSON inválido en el intento 1 y válido en el intento 2 hace que `ProductOwnerAgentRunner.run` **complete** (no `failed_validation`), con exactamente 2 invocaciones del runtime.
- C4. Un runtime que devuelve JSON inválido en ambos intentos termina en `failed_validation` con el **último** error como motivo (bloqueo honesto, no bucle infinito).
- C5. Las suites `test_impact_question_engine.py` y `test_product_owner_*` quedan verdes tras actualizar los casos de comportamiento intencionalmente cambiado; `test:py` y `ruff` pasan.

## 3. Reproducción (antes / después)

- **Antes:** un stub de runtime que devuelve un PO JSON con `questions[0].category="performance"` → `validate_output` lanza → `run` retorna `failed_validation` → el coordinador bloquea. (Test que reproduce el bloqueo.)
- **Después (A1):** mismo input → `category` se coerce a `nonfunctional`, `validate_output` retorna válido → estado de éxito; no se bloquea.
- **Después (A2):** stub que devuelve inválido (p. ej. HU sin `acceptanceCriteria`) y luego válido → `run` completa en el 2º intento.

## 4. Diseño

### A1 · Tolerancia de `category` — `local_control_center/agents/impact_question_engine.py`

- Nuevo `CATEGORY_ALIASES: dict[str, str]` (claves en minúscula → categoría canónica). Mapa inicial curado y extensible:
  - `nonfunctional` ← `performance`, `scalability`, `reliability`, `availability`, `latency`, `throughput`, `maintainability`, `observability`, `quality`, `technical`, `architecture`, `infrastructure`, `operational`, `ops`, `non-functional`, `non functional`, `nfr`, `resilience`, `capacity`
  - `ux` ← `usability`, `design`, `ui`, `user experience`, `user-experience`, `accessibility`, `a11y`, `interaction`
  - `scope` ← `functional`, `feature`, `features`, `functionality`, `mvp`, `requirement`, `requirements`, `boundaries`
  - `delivery` ← `timeline`, `schedule`, `cost`, `budget`, `resourcing`, `rollout`, `release`, `deployment`, `milestone`, `milestones`, `planning`, `roadmap`, `effort`, `estimate`, `estimation`
  - `compliance` ← `legal`, `regulatory`, `regulation`, `regulations`, `privacy`, `gdpr`, `hipaa`, `data privacy`, `data protection`, `governance`, `policy`, `licensing`
  - `risk` ← `security`, `threat`, `vulnerability`, `safety`, `business risk`, `dependency`, `dependencies`, `uncertainty`
  - `data` ← `database`, `storage`, `schema`, `data model`, `data modeling`, `analytics`, `data retention`, `persistence`, `migration`
  - `integration` ← `api`, `apis`, `third party`, `third-party`, `interoperability`, `integrations`, `external`, `webhook`, `webhooks`, `connectivity`, `interface`
  - `users` ← `user`, `persona`, `personas`, `audience`, `stakeholder`, `stakeholders`, `target users`, `customer`, `customers`, `roles`
- Nuevo `DEFAULT_QUESTION_CATEGORY = "scope"`.
- Nueva función pura `normalize_category(raw: str) -> tuple[str, str | None]` que devuelve `(canonical, coerced_from)`: si el valor ya es canónico → `(valor, None)`; si está en el mapa → `(canónica, raw)`; si es desconocido → `(DEFAULT_QUESTION_CATEGORY, raw)`.
- En `validate_impact_question`, reemplazar el check estricto (`:85-89`) por la normalización: `category` **nunca** lanza; si hubo coerción, registrar auditoría con logging estructurado (`logging.getLogger(__name__)`), incluyendo `index`, valor original y valor mapeado. **No** se agrega un 9º campo al dict retornado (preserva el contrato de 8 campos que verifica `test_validate_impact_question_normalizes_the_eight_fields`).
- Al ser estrictamente **más leniente**, nunca rompe entradas ya válidas: seguro para todos los consumidores del engine compartido (`product_owner_agent.py` es el único hoy — verificado por grep).

### A2 · Reintento de reparación acotado in-process — `local_control_center/agents/product_owner_agent.py`

- Nuevo `PRODUCT_OWNER_MAX_REPAIR_ATTEMPTS = 2` (1 original + 1 reparación; techo de costo/latencia, cada intento respeta `PRODUCT_OWNER_RUNTIME_TIMEOUT_SECONDS`).
- Extraer de `_execute_and_persist` el tramo *ejecutar runtime → leer texto → parsear → `validate_output` → `ImpactQuestionEngine().select`* a un helper `_run_and_validate_once(...)` que retorna, en éxito, `(output, selection, runtime_result, output_artifact_id)` y, en fallo de **validación**, señaliza el error + texto crudo previo.
- Bucle en `_execute_and_persist`:
  - Fallo de **infraestructura** (`runtime_result["status"] != "completed"`: timeout, exit≠0, blocked) → comportamiento actual (`status=failed`, retorna). La reparación **no** aplica a fallos de infraestructura (un re-prompt no los arregla).
  - Fallo de **validación** con intentos restantes → construir contexto de reparación (último error + texto previo acotado) y reintentar con el **mismo** runtime.
  - Fallo de **validación** sin intentos restantes → `failed_validation` con el **último** error (honesto).
- Prompt de reparación: `cli_prompt`/`model_messages` aceptan un parámetro opcional `repair` que antepone/adjunta: *"Your previous response failed validation with: &lt;error&gt;. Previous output was: &lt;texto acotado&gt;. Return corrected JSON only, fixing exactly that error and keeping everything else valid."* El error ya nombra el campo y los valores permitidos (diseñado para auto-corrección).
- Idempotencia: `_execute_cli_runtime` aloja/archiva su propio workspace efímero **por llamada**, así que reinvocar es seguro; `job`/`agent_run` se reutilizan y cada invocación genera sus propios `tool_call`/artefactos vía `broker.evaluate_tool_call`. La persistencia de discovery/backlog (`product_owner_agent.py:1320-1399`) queda **fuera** del bucle: corre una sola vez, con el `output` exitoso.

### A3 · `retry_loop` con feedback (opcional, mismo spec — menor prioridad) — `local_control_center/remediations/service.py`

- Inyectar el `blockedReason` del bloqueo PO en `retry_metadata` (`service.py:1104-1118`) para que el reintento **manual** arranque ya como reparación (belt-and-suspenders para cuando A2 agota intentos y el usuario clickea "reintentar").
- Requiere: (1) `service.py` agrega `productOwnerRepairContext` a `runMetadata` del job de reintento; (2) el run del product loop propaga ese `runMetadata` al `payload.metadata.repairContext` del `ProductOwnerAgent`; (3) `_execute_and_persist` siembra el contexto de reparación del intento 0 desde `payload` si viene.
- Se implementa **al final**; si el presupuesto del slice se acota, A1+A2 ya entregan auto-reparación automática sin click y A3 puede diferirse a un fast-follow.

## 5. Cambios de comportamiento y compatibilidad

- **Intencional (documentado):** `questions.category` deja de rechazar valores desconocidos; los coerce (sinónimo conocido → canónica; desconocido → `scope`) con nota de auditoría. Esto invalida dos aserciones actuales que deben actualizarse:
  - `tests_py/test_impact_question_engine.py:52` — quitar la fila `({"category": "unknown"}, "category")` de `test_validate_impact_question_rejects_invalid_contracts` (las demás filas siguen rechazando).
  - `tests_py/test_impact_question_engine.py:107-109` — `test_engine_validation_propagates_for_malformed_candidates`: repuntar el candidato malformado de `category="bogus"` a un campo que **siga** siendo inválido (p. ej. `confidence="bogus"`, `match=r"questions\[1\].confidence"`), preservando la intención "la validación propaga".
- **Sin cambios** en el `outputSchema` del contrato (el enum de `questions.category` sigue siendo la fuente de verdad de los valores canónicos; la coerción ocurre antes del check). Sin migraciones. Sin cambios de API HTTP ni de OpenAPI.

## 6. Fuera de alcance

- Sub-proyecto B completo: `QuotaManager`, detección de límite de uso de CLI, cooldown por runtime, enrutamiento consciente de uso, failover entre candidatos.
- Cualquier cambio de UI (`local-control-center/web/**`) e i18n. A es **backend puro**: la tarjeta de remediación `product_owner_output_invalid` y su copy ya existen.
- Cambiar el rigor de `decisions.category` (se deja como está; solo se documenta la inconsistencia).

## 7. Plan de pruebas y gates

- **Unit (`tests_py/test_impact_question_engine.py`):**
  - Nuevo: coerción de sinónimos conocidos (`Performance→nonfunctional`, `Usability→ux`, `Security→risk`, …).
  - Nuevo: desconocido → `DEFAULT_QUESTION_CATEGORY` y emisión de log de auditoría (via `caplog`).
  - Actualizar los dos casos de comportamiento cambiado (§5).
- **Unit/integración (`tests_py/test_product_owner_*`):**
  - Reparación: stub de runtime que devuelve inválido→válido → `run` completa con 2 invocaciones (C3). Reusar el arnés de inyección de runtime de `tests_py/test_product_owner_agent_real_runtime.py` (los runtimes de modelo/provider controlado devuelven JSON; los CLI reales no garantizan JSON estricto).
  - Tope: inválido×2 → `failed_validation` con el último error (C4).
- **Regresión:** `test_product_owner_assessment_grounding.py`, `test_workflow_product_owner_intake.py`, `test_aido_product_loop_real_e2e.py`, `test_remediation_blocker_experience.py` verdes.
- **Gates:** `uv run pytest tests_py/test_impact_question_engine.py tests_py/test_product_owner_*.py -q` → luego `ruff format` + `ruff check` (asegurar **LF**, sin warning "CRLF will be replaced") → luego `test:py` completo (incluye gates de arquitectura). Sin `test:web` (no hay cambios de web).
- **Autor/docstrings:** no se crean módulos productivos nuevos (se editan existentes que ya declaran `@author Rodrigo Mason`); las funciones públicas nuevas (`normalize_category`) llevan docstring D103.

## 8. Riesgos y mitigaciones

- **Coerción demasiado agresiva enmascara una pregunta mal categorizada.** Mitigación: el `category` no afecta corrección (solo orden/dedup); toda coerción queda en log de auditoría; el default `scope` tiene peso de impacto medio (30), sin distorsionar el ranking hacia extremos.
- **La reparación duplica costo/latencia.** Mitigación: tope duro de 2 intentos; solo se dispara en fallo de validación (no de infraestructura); cada intento respeta el timeout del runtime.
- **El re-prompt reincide en el mismo error.** Mitigación: para `category` ya no ocurre (A1 lo neutraliza antes); para otros campos, el error inyectado nombra el campo exacto; si aun así falla, se bloquea honestamente tras el tope (C4).
- **CRLF en Windows rompe `ruff format`.** Mitigación: escribir con LF y verificar `git diff --stat`/sin warning de CRLF antes de cerrar.

## 9. Verificación de cierre

Comandos a ejecutar y evidenciar:

1. `uv run pytest tests_py/test_impact_question_engine.py tests_py/test_product_owner_agent_real_runtime.py -q`
2. `uv run pytest tests_py/test_product_owner_assessment_grounding.py tests_py/test_workflow_product_owner_intake.py tests_py/test_remediation_blocker_experience.py -q`
3. `ruff format local_control_center/agents/impact_question_engine.py local_control_center/agents/product_owner_agent.py && ruff check local_control_center/agents/impact_question_engine.py local_control_center/agents/product_owner_agent.py`
4. `test:py` completo (gate de arquitectura + i18n).

El slice se declara listo solo con la salida real de estos comandos vista y verde, o con el bloqueo exacto reportado.
