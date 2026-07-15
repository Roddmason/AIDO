# HU como spec ejecutable: la historia y sus criterios llegan al agente que ejecuta

- **Fecha**: 2026-07-14
- **Autor**: Rodrigo Mason
- **Estado**: aprobado
- **Alcance**: backend (`backlog`, `product_loop`, `agents`) + API read-only

## 1. Contexto y problema

El pipeline PO -> TechnicalLead persiste una jerarquia completa epica -> historia ->
criterios de aceptacion -> tareas por rol (`shared/migrations.py` fases de backlog;
`backlog/technical_lead_planner.py` desglosa responsabilidades por agente con goal,
gates, reviewer y dependencias). Sin embargo, **nada de eso llega al agente que
ejecuta**:

- `developer_agent_prompt` (`agents/runtime_registry.py:258`) construye el prompt del
  DeveloperAgent solo con la `instruction` cruda del usuario + comandos QA.
- El `developer_payload` del coordinador (`product_loop/coordinator.py:4831`) incluye
  `agentTasks`, pero el runner no los usa en el prompt; las tareas ademas llevan solo
  `acceptanceRefs` (ids), nunca el texto de los criterios.
- Resultado: el agente implementa sin ver los criterios de aceptacion ni el desglose
  de su responsabilidad; la HU no funciona como spec.

## 2. Objetivo y criterio de exito verificable

Que cada HU opere como spec ejecutable: el agente que implementa recibe, en su prompt,
la epica, la historia (asA/iWant/soThat), los criterios de aceptacion en texto y el
desglose de responsabilidades por rol con sus dependencias.

- **C1**: existe `build_story_spec(backlog, story_id)` que ensambla epica + historia +
  criterios + responsabilidades por rol (goal, reviewer, gates, dependsOn) desde las
  tablas existentes, sin escribir nada.
- **C2**: existe `render_story_spec_prompt(specs)` que produce texto acotado (limite
  de caracteres) apto para prompt, determinista.
- **C3**: `developer_agent_prompt` acepta `story_specs` opcional y lo incluye; ambas
  rutas de ejecucion (CLI argv y model messages) lo propagan desde
  `payload["storySpecs"]`. Sin `storySpecs` el prompt es byte-identico al actual.
- **C4**: el coordinador arma `storySpecs` desde las historias del backlog del loop y
  lo pone en `developer_payload`.
- **C5**: `GET /api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec`
  devuelve el spec ensamblado (read-only, sin token de escritura).
- **C6**: tests unitarios + de endpoint cubren C1-C5; los suites existentes del
  developer agent y product loop no se rompen.

## 3. Diseño

### 3.1 Ensamblador (`local_control_center/backlog/story_spec.py`, nuevo)

- `build_story_spec(backlog: BacklogRepository, story_id: str) -> dict`:
  `{storyId, epic{id,title,description}, story{title,asA,iWant,soThat,businessValue,
  status,priority}, acceptanceCriteria[{id,sequence,criterion,status}],
  roleResponsibilities[{taskId,role,title,goal,reviewerRole,qualityGates,
  dependsOn[{taskId,role}]}]}`.
  Usa `get_user_story`, `get_epic`, `list_acceptance_criteria(story_id=...)`,
  `list_agent_tasks(story_id=...)` y `list_task_dependencies(...)`. `KeyError` si la
  historia no existe (mismo contrato del repositorio).
- `render_story_spec_prompt(specs: list[dict], *, char_limit) -> str`: markdown
  compacto y estable (orden por sequence/rol); trunca con marcador explicito si excede
  el limite.

### 3.2 Propagacion al DeveloperAgent

- `developer_agent_prompt(*, instruction, qa_commands, story_specs: str | None = None)`:
  agrega un bloque "User story spec (source of truth for acceptance)" cuando hay spec.
- `build_developer_agent_argv(..., story_specs=None)` y
  `_developer_model_messages(..., story_specs=None)` lo propagan.
- `DeveloperAgentRunner` lee `payload.get("storySpecs")` en ambas rutas.
- Coordinador: en `_run_user_message`, tras generar `agent_tasks`, ensambla los specs
  de las historias involucradas y agrega `storySpecs` al `developer_payload`.

### 3.3 API read-only

- `GET /api/v1/projects/{project_id}/product-loop/stories/{story_id}/spec` en
  `product_loop/api.py`, con response model `StorySpecResponse` en
  `product_loop/models.py`. 404 si la historia no existe o no pertenece al proyecto.
- No se regenera `openapi.ts` en este slice (el gate de cliente generado pinnea
  substrings existentes, no cobertura total); el consumo web queda como siguiente paso.

## 4. Cambios de comportamiento y compatibilidad

- Prompts del DeveloperAgent ganan el bloque de spec solo cuando el coordinador provee
  `storySpecs`; invocaciones existentes sin ese campo no cambian ni un byte.
- Ningun cambio de esquema SQLite ni de contratos existentes: el spec es una vista
  ensamblada de datos ya persistidos.

## 5. Fuera de alcance

- Derivar HUs desde una epica existente (flujo epic-first): extension mayor del
  contrato del PO; queda como siguiente slice.
- Inyectar el spec en QAAgent/SecurityAgent (siguiente paso natural).
- UI web del spec y regeneracion del cliente OpenAPI.

## 6. Plan de pruebas y gates

- `tests_py/test_story_spec.py`: ensamblado completo (C1), render acotado y
  determinista (C2), prompt con/sin spec (C3), endpoint 200/404 (C5).
- Regresion: `tests_py/test_developer_agent_real_runtime.py`,
  `tests_py/test_product_loop_coordinator.py` (subset ejecutable), ruff format+lint.

## 7. Riesgos

- Prompt inflado -> mitigado con `char_limit` y truncado explicito.
- Divergencia spec/tareas si el planner cambia -> el ensamblador lee las mismas tablas
  que la UI, no duplica reglas de negocio.
