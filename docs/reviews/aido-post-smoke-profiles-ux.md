# AIDO después del smoke: perfiles y dos recorridos UX

Revisión: **2026-09-07, Windows x64**. Baseline ejecutable `cc3897c5b2486d6a597830e5f80b19cc999e4f38`, HEAD inicial documental `f1d529d21bde857aa795c823e7fcbdc4ff051617`. Rama única: `codex/aido-post-smoke-profiles-ux`. Cambio ejecutable de esta fase: **`c8113b0f7f5becbb5242fecf6445678f5bd93768`**. Inferencia AIDO consumida: **0**. Sin promoción del runtime, push, merge, adopción, login ni reconstrucción de entornos activos.

Estado vigente del cierre: apartado «Cierre documental y validación completa» al final. Las mediciones y resultados anteriores conservan su fecha y candidato; no son una validación nueva.

## Estado y evidencia

No hay un porcentaje global de avance. Los PASS históricos mantienen su candidato; no certifican automáticamente el nuevo.

| Capacidad/hito | Estado | Evidencia y última revisión | Siguiente prueba / limitación |
|---|---|---|---|
| PR baseline | PASS | §14.13.2: 13/13, Python 2013 + 1 skip y 3 warnings; web 339 + 5 skips | Sólo `cc3897c5`; PR nuevo separado abajo |
| HTTP/captura Windows baseline | PASS | 8/8 en §14.13.2 | Sintético, no certifica proveedores ni otros OS |
| Release local baseline sin inferencia | PASS | 7/7, instalación, upgrade/restore sobre fixtures y HTTP | Tier release completo NOT_RUN |
| Smoke Codex real baseline | PASS | [smoke-verification.json](../../.tmp/operational-hardening-p0/authorized-close/codex-smoke-5217a125432a4741b7c0748901a7f651/smoke-verification.json), 2026-09-07 | Un marcador read-only, no HU/plan/implementación/Claude |
| Dos recorridos UI nuevos | PASS | 38 casos de selección afectada, desktop/mobile, exit 0; `af8fa9af…/web-affected.json` | No equivale al PR completo |
| Perfiles/PO: contrato y procedencia | PASS | Inspección de código + exportación determinista de 17 roles, 4 modos y 7 rutas; `73642a65…/profiles-audit.json` | Defaults de esquema nuevo, no configuración efectiva de cada proyecto operativo |
| PO cotidiano con runtime real | NOT_RUN | Ningún proveedor ejecutado en esta fase | Readiness nativa vigente y ciclo de dominio requieren autorización futura |
| Linux / macOS / convivencia Unreal | NOT_RUN | No ejecutados aquí | No certificados por Windows ni mocks |
| Integridad relacional en copia P0 | PASS | Reparación previa sólo en copia, informe P0 | Procedencia de política histórica UNKNOWN; original no adoptado |
| Nueve recibos históricos perdidos | FAIL | Incidente §14.11 conservado | No reconstruidos, sobrescritos ni buscados nuevamente |
| Aceptación integral | NOT_RUN | No afirmada | No confundir smoke, validación local y autonomía completa |

Los IDs abreviados de esta fase son directorios bajo `../../.tmp/post-smoke-profiles-ux/`; allí están los recibos completos, hashes, argv de herramientas, stdout/stderr y temporales separados. La evidencia sensible anterior no se incorpora a Git.

## Diagramas reales Archify

Instalación local de **skill**, no dependencia runtime ni plugin de AIDO: `C:/Users/Rodd/.agents/skills/archify`, [tt-a1i/archify v2.16.0](https://github.com/tt-a1i/archify/releases/tag/v2.16.0), licencia MIT (tt-a1i/Cocoon AI). ZIP oficial SHA-256 `4c59fa6557a2385beaaef8c7219cc414573acc9f0c30a932d5053b0b20689a46`, verificado. No actualización global, PATH ni otro paquete instalado. `doctor`: exit 0, recibo `99816856…/archify-doctor-command.json`. No había `.mmd` del paquete en el checkout: ausencia registrada, no se usaron como evidencia.

| Vista | HTML / SVG / fuente JSON | Validación |
|---|---|---|
| Responsabilidades | [HTML](../diagrams/aido-post-smoke/architecture.html), [SVG normalizado](../diagrams/aido-post-smoke/architecture.normalized.svg), [JSON](../diagrams/aido-post-smoke/architecture.json) | 9/9 showcase, 0 errores/warnings; `4ea2ee81…` |
| Procesos, Jobs y reservas | [HTML v4](../diagrams/aido-post-smoke/process-scopes-v4.html), [SVG normalizado v4](../diagrams/aido-post-smoke/process-scopes-v4.normalized.svg), [JSON v4](../diagrams/aido-post-smoke/process-scopes-v4.json) | 9/9 showcase; `a50a8c75…`; corrección semántica, composición conservada |
| Secuencia PO y gates | [HTML](../diagrams/aido-post-smoke/po-gates-v2.html), [SVG normalizado](../diagrams/aido-post-smoke/po-gates-v2.normalized.svg), [JSON](../diagrams/aido-post-smoke/po-gates.json) | 9/9 showcase; `ee830822…`; dos rondas de composición |
| Trabajo y sistemas externos | [HTML](../diagrams/aido-post-smoke/work-process.html), [SVG normalizado](../diagrams/aido-post-smoke/work-process.normalized.svg), [JSON v2](../diagrams/aido-post-smoke/work-process.json) | 9/9 showcase; `ee830822…` |

`visual-check` final: exit 0 en las cuatro vistas; PNG inspeccionados en claro/oscuro, 1440×900 y tamaño grande. Sin ocultar overflow ni reducir tipografía para aprobar. La primera composición de procesos/secuencia desbordaba y queda como FAIL en recibos independientes. Las correcciones compactaron espacio y texto redundante. El workflow conserva corredores largos del compilador v2, legibles y sin cruces; no es un Mermaid renderizado por otro motor.

HTML autocontenido con controles en **inglés** (v2.16 admite en/zh-CN, no se inventó es); contenido español. SVG exportados mediante `Archify.exportMenu.run('svg')`, no extracción de otro motor; hashes en `99816856…/archify-svg-command.json`. Los `*.visual-check.json` conservan `visualReview: pending` del automatismo: esta revisión humana/modelo de imágenes se registra aquí, sin alterar sus bytes. `visual_review: passed` para legibilidad/contención; no prueba funcionamiento del software dibujado. Un visor provisional/conceptual no aportaba esta validación tipada ni vínculos fuente; no se recibió otro visor para una comparación píxel a píxel.

Aristas y mecanismo: `projects/api.py` registra; `executions/runner.py` encola; `workers/runtime.py` reclama con fencing; `agents/product_owner_agent.py` valida scope y dominio; `agents/tool_broker.py` aplica autorización; `process_supervision/service.py` contiene y publica evidencia. `process_supervision/launcher_session.py` coordina captura opt-in, con contratos internos, no un ejecutor HTTP genérico. Jira/Confluence/Jenkins/Unreal están PROPUESTOS/no certificados, nunca operativos por su mera aparición.

## Perfiles reales: cinco ejes distintos

Exportación `73642a65…/profiles-audit.json`: `team_member_defaults()` y migraciones del candidato sobre SQLite en memoria, autocommit y FK ON. No agentes lanzados. Las definiciones locales de los roles solicitados fueron leídas y asumidas secuencialmente por la sesión principal; no se crearon once procesos ni subagentes. Skills aplicadas: depuración, TDD, arquitectura de software, UI/UX sobre el design system existente y verificación. No capability-evolver, overdrive ni coordinación recursiva.

| Rol de dominio (ID exacto) | PermissionProfile | Herramientas declaradas | Modo preferido; primeros candidatos |
|---|---|---|---|
| aido_lead | plan | policy.evaluate, evidence.create, mcp | CLI; Claude, Codex |
| product_owner | plan | evidence.create, mcp | CLI; Gemini, Ollama, Codex |
| project_manager | plan | evidence.create | API; openai_compatible, Ollama |
| scrum_master | plan | evidence.create | API; openai_compatible, Ollama |
| architect | plan | policy.evaluate, evidence.create, mcp | CLI; Claude, openai_compatible |
| technical_lead | plan | policy.evaluate, evidence.create | CLI; Codex, Claude |
| backend_engineer | dev_safe | shell, workspace_patch | CLI; Codex, Claude |
| frontend_engineer | dev_safe | shell, workspace_patch | CLI; Codex, Claude |
| mobile_engineer | dev_safe | shell, workspace_patch | CLI; Codex, Claude |
| database_engineer | dev_safe | shell, workspace_patch | CLI; Codex, Claude |
| data_engineer | dev_safe | shell, workspace_patch | CLI; Codex, openai_compatible |
| qa_engineer | qa | shell, evidence.create | CLI; Codex, Claude |
| security_engineer | qa | shell, policy.evaluate, evidence.create | CLI; Claude, openai_compatible |
| pentester | qa | shell, workspace_patch, policy.evaluate, evidence.create | CLI; Claude, openai_compatible |
| devops_engineer | qa | shell, workspace_patch, evidence.create | CLI; Codex, Claude |
| researcher | plan | mcp, evidence.create | API; openai_compatible, OpenRouter |
| release_manager | release | policy.evaluate, evidence.create | CLI; Claude, Codex |

No son asignaciones exclusivas de modelo: el catálogo admite candidatos compatibles; auth, disponibilidad, capacidades, permisos, privacidad, presupuesto y aprobación filtran antes de ordenar por costo. `allowedProviders=['*']` no otorga autoridad para red, shell libre, credenciales o gasto. Nombres abreviados de proveedores en la tabla; IDs completos y orden finito en la exportación. No se reasignó ningún modelo.

| Eje | Valores y origen efectivo |
|---|---|
| Autonomía | `guided` (default), `recommended`, `autonomous`; `agents/autonomy_profiles.py`. Irreversibilidad/baja confianza conservan ask/recommend |
| Equipo | `economy`, `balanced` (default), `critical`, `maximum`; `team_scheduler/scheduler.py`, `project.loop.teamMode`. Tiers economy/standard/high/frontier; presupuestos estimados 1/4/10/25 no son autorización de gasto |
| Routing | `free_first`, `free_tier`, `cost_controlled`, `balanced_best_value`, `max_performance`, `manual_by_profile`, `local_private`; 7 filas reales sembradas. `model_router.py` filtra elegibilidad antes de ranking |
| Esfuerzo | Preferencia coincidente; max_performance puede resolver xhigh si está disponible; en otro caso primer nivel disponible o None. Requested/resolved no equivalen a observed |
| Settings | `settings/registry.py` + `resolver.py`: proyecto > general > default; DTO expone origin/source/inherited. No hay override workspace en ese resolver; workspace de ejecución es otra entidad |
| Runtime | `runtime_integrations/config.py`: override explícito de entorno > instalación persistida; preferencias por scope exacto en repository. No confundir con precedencia de Settings ni perfil local de Codex Desktop |
| Workload | Se resuelve por operación/transporte en jobs/ToolBroker/supervisor, no por el nombre del rol. CLI → agent_cli; llamada remota ligera → remote_llm_light; gates → perfil propio |

Perfiles host finitos (`host_resources/profiles.py`), CPU solicitada como porcentaje de host; traducción nativa relativa al padre, no multiplicación repetida: control_plane 2 GiB/20%/8 procesos; remote_llm_light 2/15/4; qa_light 4/25/8; agent_cli 8/40/16; browser_test 8/40/24; build_heavy 16/65/32; capture_session 18/65/64; unreal_editor 24/70/64; unreal_cook 32/80/64 exclusivo; local_gpu_model 16/50/16. Son perfiles configurados, **no garantía de admisión**: combinaciones/CPU/reserva física/política efectiva pueden rechazarlos; Unreal/GPU no se ejercitaron.

Captura opt-in comparte **una** reserva. Memoria: creador 2 GiB, API y worker 2 GiB cada uno, ejecución 8 GiB, colector 4 GiB; host conserva 16 GiB. Readback del PASS: raíz AIDO con cuota solicitada 65%; dispatcher **40% de la cuota de esa raíz**; objetivo 100% del dispatcher; colector **20% de la cuota de esa raíz**. Los equivalentes nominales 26% y 13% del host sólo aplican sin otro ancestro limitante. Creador/API/worker usan respectivamente 20%/10%/10% de la raíz (nominales 13%/6,5%/6,5% del host bajo la misma condición). Objetivo y colector no se crean accidentalmente uno dentro del Job del otro. Reserva exterior/local, procesos y Jobs concretos no son sinónimos. Ante ancestros externos, `effectiveHostCpuPercent=null`, jerarquía externa UNKNOWN. Consumo propio UNKNOWN: no restar caída global de memoria, RSS, compromiso o picos como equivalentes. **No se modificó admisión.**

### PO cotidiano frente al runtime del smoke

Lectura acotada `mode=ro` de la base operativa: preferencia global **claude_code_cli**, orden `[claude_code_cli, ollama]`; Codex persistido en `.../Programs/OpenAI/Codex/bin/codex.exe`, versión **cacheada** 0.142.2, offline, validación 2026-06-29; cero probes/recibos Codex. Claude conserva health cacheado del 2026-07-28, que no demuestra autenticación vigente. Sin override Codex en esta shell; eso no certifica el entorno de otro worker. No se hizo login, probe ni actualización de esa base.

Además, **PO tiene selección propia**: `ProductOwnerAgentRunner.status(preferred_runtime=payload.preferredRuntime)` llama `product_owner_agent_readiness`. Un preferredRuntime no elegible devuelve configuration_required/runtime_unavailable, sin fallback silencioso; si se omite, ordena **sólo elegibles**, primero free-tier declarado/local. La preferencia global anterior no prueba que todo PO elija Claude. La selección cotidiana final necesita su request y readiness vigentes; no es el binario aislado por defecto.

Codex `build_product_owner_agent_argv` verifica hash y capacidades; busca recibo por **binary_fingerprint + contract_fingerprint**, no un bool global. Una aprobación read-only del transporte no valida las HU. `plan` no necesita shell genérico: ToolBroker distingue `product_owner_runtime`/`product_owner_model_call`, autoridad especializada, workspace y approvalGrantId. Se conserva esa frontera, sin cambiar permisos para arreglar nombres de perfil.

La salida exige JSON de dominio: historias asA/iWant/soThat y criterios, epics referenciadas, ambigüedades/decisiones. exit 0 no sustituye `brief_ready`, `needs_input` o `failed_validation`; errores de infraestructura impiden reparación de dominio. Existen hasta dos reparaciones de salida configuradas en el producto, **no ejecutadas ni autorizadas aquí**. `no_limit` local no representa cuota ilimitada. No hubo conflicto vigente de PO reproducido que justificara cambiar adaptadores o asignaciones.

Contexto: idea limitada a 8000 caracteres; señales de assessment, iniciativa/brief, objetivo/constitución y decisiones explícitas. Cuatro colecciones de decisiones/preguntas limitadas a 20 y presupuesto agregado 40000 caracteres para esas colecciones; **no equivale a 40000 tokens ni a un máximo universal de todo el prompt**. No se registró su contenido. El builder PO pasa model pero no expone parámetro effort: resolución de routing y esfuerzo observado deben permanecer separados; el smoke tiene esfuerzo UNKNOWN. No se aportaron logs de fallos PO pre-P0: no se les atribuye retrospectivamente la causa de un incidente posterior.

## Incidentes separados y comparación verificable

| Incidente | Hecho/evidencia | Conclusión limitada |
|---|---|---|
| A · pre-spawn | Configuración, auth y contrato faltantes producen bloqueo antes del proceso | No llamarlo fallo del modelo; el smoke aislado no promueve readiness original |
| B · SQLite/watchdog | Helper y watchdog corregidos previamente, regresiones P0; error causal histórico no quedó conservado | Contención reproducida ≠ demostrar SQLITE_BUSY como causa histórica |
| C · stack overflow | 0.149.0 y 0.153.4 dieron 0xC00000FD, tokio-rt-worker; WER del segundo PID 46292/offset 0x0d3eecc7 | Causa interna UNKNOWN; misma 0.153.4 falló y pasó |
| D · asignación | Otro intento: 4.194.304 bytes fallidos, 0xC0000409 | Diferente mecanismo observable; no explicación automática del stack overflow |
| E · ámbito 2 GiB | Harness histórico agregó Job control_plane al worker; launcher normal no lo agregaba | Contraejemplo/mitigación de ámbitos validados; pico 2.281.967.616 versus readback 2 GiB sigue discrepancia histórica, no RSS propio de Codex |
| F · captura/CPU | Conversión CPU relativa, colector con ámbito propio, fixtures fail-fast y rutas CDB tratados por pruebas previas | RaiseFailFastException no prueba el mecanismo del crash; 0x800707D1 no identifica un driver |
| G · runner/evidencia | Nueve recibos sobrescritos; posteriores fixtures Git/hook, esperas, Ollama/OpenAPI corregidos y gates baseline verdes | La nueva evidencia no restaura bytes históricos perdidos |

Fuente comparativa local: [reporte del crash](../../.tmp/operational-hardening-p0/authorized-close/native-crash-provider-report.md) y `smoke-final-v2.json`/`smoke-verification.json`. Ambos 0.153.4: binarySha256 **444a3f0008050605cae73cd9b7a2dcac61294062dfaab56dd20430fd6498518b**.

Las huellas de contrato difieren: FAIL `fefa6ddf…f8b53d`, PASS `841ca30b…0af7e`. Verificación pura, sin CLI de modelo, reprodujo exactamente ambas: mismo algoritmo `SHA256(UTF8(json_dumps(material)))`, sort_keys=true, ensure_ascii=false, separadores por defecto; mismos marcadores v1. Diferencia del material: **RUST_LOG agregado a la allowlist**. No cambio de canonicalización ni prueba automática de diferencia semántica de argv. Este hash **no incluye modelo, prompt, presupuesto ni topología**; no confundirlo con `command_fingerprint`, cuyo JSON usa separadores compactos y ensure_ascii=true.

| Elemento | FAIL 0.153.4 | PASS 0.153.4 |
|---|---|---|
| Modelo/esfuerzo | gpt-5.6-terra; sin override effort | mismo modelo; esfuerzo efectivo UNKNOWN |
| Argv de aislamiento | read-only; ask never; ephemeral; ignore-user-config/rules; strict-config; MCP/skills/plugins/tools deshabilitados | mismos flags del contrato; workspace/identidades distintos; no bypass |
| Controlador/launcher | harness previo, identidad/contención menos instrumentada | launcher `--capture-session --no-build`, HTTP → worker → dispatcher; creación compatible; registration sin create_app |
| Entorno permitido | home aislado, no copia config/skills/plugins; sin RUST_LOG | home aislado, mismos límites de entorno; diagnóstico acotado `error,codex_exec=info` con expiración |
| Recursos | lease agent_cli 8 GiB/40% solicitados; herencia y setters no todos conservados | agregado 18 GiB/65% solicitado; ejecución 8 GiB/40% de cuota raíz, colector 4 GiB/20% de cuota raíz; nominales host 26%/13% sólo sin otro ancestro limitante; readback conservado |
| Entrada/salida | stdin DEVNULL/EOF; pipes binarios; stderr 98 bytes, stdout vacío | mismo modo de E/S; stdout 365 y stderr 1285 bytes, sin truncar |
| Nativo / dispatcher | stack overflow; FAIL preservado | **0/0**, contrato validated; launcher **15 por cierre explícito posterior**, no crash Codex |
| Captura/consumo | no stack nativo discriminante; consumo UNKNOWN | ProcDump listo antes de Resume; exit 0, no excepción, cero dumps; 9586 entrada (7936 cacheados), 11 salida; costo/cuota UNKNOWN |

El contexto de 9586 tokens no se atribuye a todos los plugins instalados. Configuración instalada, instrucciones cargadas y procesos activos son tres hechos diferentes. Los homes aislados y flags contradicen esa atribución. Tampoco un upstream fix prueba causalidad cuando el mismo binario falló y pasó. Conclusión: **mitigaciones operacionales validadas y una ejecución real exitosa; causa interna histórica UNKNOWN**.

## UI: cambios y prueba rojo/verde

Shell, navegación, tipografía, tokens y frameworks conservados. Antes/después: Dark, EN, desktop 1280×720 y móvil 375×812 (DPR 2,75), API local con DB de prueba. Capturas completas sin recortar viewport. La navegación móvil de Settings sigue usando scroll; no se declara rediseñada.

- **Conexiones/API:** se reutilizan Form/TextField/SelectField/Button/StatusChip y el wizard existente. Alta API/endpoint separada de sesión CLI; guardar, comprobar config/auth y smoke no son equivalentes. Se retiró la llamada implícita a test-prompt de “Validate connection” y el health-check automático al guardar; quedan acciones explícitas y controles backend. `invalid` tiene tono danger; fallo de transporte no se presenta como credencial inválida. Errores de escritura no reflejan texto del vault. Secretos fuera del estado serializable/atributos HTML, vaciados antes de await y al cerrar. Rotación/auditoría avanzadas colapsables; endpoint requerido, backend escribible, advertencias de gasto y permisos no se ocultan. No se inventó un timeout ni un valor de cuota desde React.
- **Proyecto/workspace:** wizard source/review/open y discovery reutilizados. La primera fase no exige un nombre aún oculto; revisión valida y confirma. Registro de proyecto ≠ workspace de ejecución. Modo abrir consulta discovery antes de registrar y rechaza ruta inexistente/no directorio. El backend de registro no fue reescrito. Guard de submit evita duplicar POST con doble clic; cancelación mientras se escribe no finge haber cancelado un efecto ya enviado. No inicia Git ni agentes. Desde Settings se suspende el modal subyacente y vuelve al cancelar; al crear se abre workbench.
- **Accesibilidad:** un modal activo, confirmación de borrado inline en lugar de window.confirm; foco inicial/retorno, Escape y Tab sobre controles visibles (no catálogo oculto ni advanced colapsado). Mensajes nuevos en en/es; se conserva prueba existente de chrome español. Reduced motion se ejercita en entrada desde Settings. Sin reemplazo global por regex ni librería visual duplicada.

Rojo/verde con recibos nuevos: nombre oculto (`c42b2ee0…`); secreto reflejado en HTML (`77db4891…`); validación hacía un prompt (`338942f6…`, proveedor sustituido sólo en test); carpeta inexistente se registraba (`bcf4022d…`); dos diálogos desde Settings (`b7ee053c…`, expected 1/received 2). Selección final **38/38 PASS**, `af8fa9af…/web-affected.json`: 20 casos nuevos desktop/mobile + regresiones providers y localización. Pruebas de componentes a través de Playwright, no una certificación OS mediante mocks.

API real: guardar cuenta Ollama sin credencial y registrar carpeta nueva con espacios/ñ, confirmar GET y ausencia de `.git`; exactamente un POST y ninguna solicitud de ejecución. Health/test-prompt y vault fallido son dobles **exclusivos de tests**, identificados en el archivo. El fixture existente completa operaciones HTTP 202 con el dispatcher de pruebas; no equivale al recorrido real de un worker de inferencia. UNC real, shares con permisos denegados y rutas largas no se certificaron aquí; se mantienen errores de plataforma, no se fabrican PASS. No se probaron ni modificaron credenciales reales.

Capturas: baseline `9173d265…/playwright/{desktop,mobile}/chunk-1/`; después `64e49b47…` y candidato final `af8fa9af…`, mismos estados connections/provider/credentials/workspace-open/workspace-create, más review. Se inspeccionaron las imágenes, no se regeneraron snapshots de referencia a ciegas. Primitives legacy reutilizadas; retirados sólo window.confirm del borrado, modal anidado de proveedor y confirmación modal de transporte; no se eliminaron pantallas ni módulos backend.

## Comandos, candidato y cierre de validación

| Comparación visual | Baseline | Candidato nuevo |
|---|---|---|
| Credenciales · Desktop | [Antes](../../.tmp/post-smoke-profiles-ux/9173d265f9454c97afd9cf39e20896ec/playwright/desktop/chunk-1/post-smoke-ux-Post-smoke-b-9f0ce-connections-and-credentials-desktop/before-credentials.png) | [Después](../../.tmp/post-smoke-profiles-ux/af8fa9af129a472c859e3e1d2663654b/playwright/desktop/chunk-1/post-smoke-ux-Post-smoke-b-9f0ce-connections-and-credentials-desktop/after-credentials.png) |
| Proyecto · Desktop | [Antes](../../.tmp/post-smoke-profiles-ux/9173d265f9454c97afd9cf39e20896ec/playwright/desktop/chunk-1/post-smoke-ux-Post-smoke-baseline-workspace-journey-desktop/before-workspace-create.png) | [Después](../../.tmp/post-smoke-profiles-ux/af8fa9af129a472c859e3e1d2663654b/playwright/desktop/chunk-2/post-smoke-ux-Post-smoke-baseline-workspace-journey-desktop/after-workspace-create.png) |
| Credenciales · Estrecho | [Antes](../../.tmp/post-smoke-profiles-ux/9173d265f9454c97afd9cf39e20896ec/playwright/mobile/chunk-1/post-smoke-ux-Post-smoke-b-9f0ce-connections-and-credentials-mobile/before-credentials.png) | [Después](../../.tmp/post-smoke-profiles-ux/af8fa9af129a472c859e3e1d2663654b/playwright/mobile/chunk-1/post-smoke-ux-Post-smoke-b-9f0ce-connections-and-credentials-mobile/after-credentials.png) |
| Proyecto · Estrecho | [Antes](../../.tmp/post-smoke-profiles-ux/9173d265f9454c97afd9cf39e20896ec/playwright/mobile/chunk-1/post-smoke-ux-Post-smoke-baseline-workspace-journey-mobile/before-workspace-create.png) | [Después](../../.tmp/post-smoke-profiles-ux/af8fa9af129a472c859e3e1d2663654b/playwright/mobile/chunk-2/post-smoke-ux-Post-smoke-baseline-workspace-journey-mobile/after-workspace-create.png) |

Revisión práctica de foco, contraste sobre tokens existentes y lectura de capturas; no se atribuye una certificación WCAG completa ni una medición formal de todas las combinaciones de contraste.

Instalación aislada reutilizada: `H:/aido-isolated-validation/candidate-ad0e3b000e7e4bab86f8b8fca13f80ed`. Su HEAD Git **cc0e841e…** sigue distinto: el contenido sincronizado por manifiesto representa `c8113b0f`, sin imports desde otro checkout. Python 3.13.15, Node 24.16.0; lockfiles/dependencias intactos. No se reconstruyeron `.venv`/node_modules activos.

Comandos reales (pasos secuenciales mediante `_run(QualityStep(...))`, supervisor, admisión y `QualityPaths`; recibos `af8fa9af…`, salvo donde se indica):

```text
node node_modules/typescript/bin/tsc --noEmit -p local-control-center/web/tsconfig.json
node node_modules/@biomejs/biome/bin/biome check local-control-center/web
node scripts/run-web-tests.mjs tests_web/post-smoke-ux.spec.js tests_web/settings-providers.spec.js tests_web/control-center.spec.js --grep "Post-smoke|Add provider:|Configure provider:|language control localizes Settings"
node C:/Users/Rodd/.agents/skills/archify/bin/archify.mjs doctor
node C:/Users/Rodd/.agents/skills/archify/bin/archify.mjs validate architecture docs/diagrams/aido-post-smoke/architecture.json --quality showcase --json --repo-root H:/Proyectos/Personales/AIDO
node C:/Users/Rodd/.agents/skills/archify/bin/archify.mjs deliver architecture docs/diagrams/aido-post-smoke/architecture.json docs/diagrams/aido-post-smoke/architecture.html --quality showcase --json --repo-root H:/Proyectos/Personales/AIDO
node C:/Users/Rodd/.agents/skills/archify/bin/archify.mjs visual-check docs/diagrams/aido-post-smoke/architecture.html --json
```

Typecheck/Biome/browser: **exit 0/0/0**. Biome conserva seis warnings preexistentes en AgentsPage/EvidencePage; dos dependencias de step intencionales llevan justificación local de foco, no se eliminó el comportamiento por un autofix. Vite advierte chunk >500 kB; Node advierte NO_COLOR/FORCE_COLOR. No ocultados. El build aislado fue ejecutado por run-web-tests; no valida por sí solo el PR entero. No cambió DTO/OpenAPI; drift completo queda en los gates correspondientes, no se regeneró el cliente.

Commit UI con hook real: `e181321d…/commit.json`, exit 0, Gitleaks `analysis_clean`. Antes de admitirse registró resource_wait por memoria/CPU; no se bajaron topes ni se omitió el hook. Los controladores auxiliares de esta revisión tuvieron errores de preparación (import de script externo, autocommit de fixture en memoria y stdout CP1252); recibos conservados y corregidos sólo en herramientas temporales, no atribuidos a producto.

**PR completo del nuevo candidato: BLOCKED antes del spawn.** Directorio `782da4083a004c549ec8fdd1c77ee94f`: `candidate.json`, `admission.json` y decisiones durables del gobernador. Comando solicitado, mediante `_run` y `capture_session`:

```text
H:/aido-isolated-validation/candidate-ad0e3b000e7e4bab86f8b8fca13f80ed/.venv/Scripts/python.exe -m local_control_center.quality --tier pr --temporary-root H:/aido-quality-scratch --db-path H:/Proyectos/Personales/AIDO/.tmp/post-smoke-profiles-ux/782da4083a004c549ec8fdd1c77ee94f/quality.sqlite
```

Presupuesto 18 GiB/65%/64 + reserva host 16 GiB = **36.507.222.016 bytes requeridos**. Preview 23:14:07 UTC: disponibles 35.815.096.320, déficit 692.125.696. Última admisión real 23:16:08 UTC (`resource-admission-6f5a5fff-86ba-4e3c-9f0e-650d8f6ec2d5`): disponibles **34.877.149.184**, déficit **1.630.072.832**, cero leases; `aggregate_memory_budget`. No se bajaron controles ni cerraron aplicaciones. La espera propia del runner fue acotada a 120 segundos; no se repitió la suite.

Exit del envoltorio **1** por `ResourceWaitError`; **CLI y gates NOT_RUN, exit nativo no disponible**, no exit 75 inventado. El helper propaga esa excepción al llamador: al no capturarla el controlador de revisión, no se produjo su `pr-session.json`; el cierre se registra como evidencia nueva, no como un recibo de suite reconstruido. Cero registros managed_processes y cero reservas en esa DB confirman que este comando no arrancó. No es una regresión funcional observada del candidato.

Release del nuevo candidato **NOT_RUN** (PR pendiente); smoke real de esta fase **NOT_RUN**, permiso 0. HTTP/captura nuevo no se repitió por separado ni se presentó el PASS del baseline como actual. Permanecen vigentes los 38 casos UI focalizados, typecheck, lint, build aislado y revisión del diff del contenido `c8113b0f`; aceptación integral no afirmada.

Cierre independiente: [review-closure.json](../../.tmp/post-smoke-profiles-ux/782da4083a004c549ec8fdd1c77ee94f/review-closure.json), exit 0 del comprobador (no del PR): fuentes/configuración sin cambios, copia aislada idéntica, **0 procesos propios vivos por PID + creation time y 0 leases activos** en las DB de esta revisión. Preview ajeno de puerto 4310 preservado. Doce valores hash del manifiesto inicial fueron redactados automáticamente por nombres de archivo credential/token; no se reescribieron. La nueva lista de hashes y comparación contra el commit inmutable verifica esos archivos sin leer ni exportar credenciales.

También se revalidaron los bytes stdout/stderr del smoke histórico y se reconstruyó el argv con el builder puro, **sin ejecutar Codex ni auth**: command fingerprint `c11a1a285e3a17ebe7541ab727c6c2123fea013aee083aa674024910c3dce63a` coincide con el registro de lanzamiento. La versión redactada queda en el cierre; el prompt se sustituye por un marcador de tipo. Esa coincidencia respalda la comparación de flags, no reconstruye una pila nativa ni prueba causalidad.

El directorio de diagramas conserva además las primeras revisiones `process-scopes.html` y `po-gates.html` con sus recibos/PNG FAIL por composición, para no destruir evidencia. **No son entregas showcase finales**; las cuatro rutas de la tabla Archify son las salidas vigentes. JSON fuente, HTML, SVG, recibos y capturas nuevas no contienen dumps, bases, auth ni evidencia nativa sensible.

Registro histórico de estilo del paquete `8f30c3a`: `git diff --cached --check` del commit documental devolvió **exit 2** por espacios finales en CSS incluido por el exportador Archify en los cuatro SVG. Se conservaron los bytes exportados y sus hashes, sin modificar artefactos para aparentar un check limpio. El diff de código/Markdown no tenía esos hallazgos; typecheck/lint del producto se mantienen separados. Derivados del cierre actual: apartado siguiente.

## Cierre documental y validación completa

Inicio del cierre: `8f30c3a1494f25f63b4f0ab2374ec9f684df640b`, limpio, misma rama. Código funcional **c8113b0f** conservado. Sin repetición de inventarios, UI focalizada, instalación Archify ni inferencia.

**CPU: PASS documental.** `CAPTURE_SESSION_PARTS` en `host_resources/profiles.py` y `_cpu_scope`/`inspect_creator_scope` en `process_supervision/windows_job.py` distinguen requested host_percent, native immediate_parent_percent y capacidad externa UNKNOWN. Ejecución **40% de la cuota raíz AIDO**, colector **20%**; 26%/13% host sólo equivalentes nominales sin otro ancestro limitante. La [v2 original](../diagrams/aido-post-smoke/process-scopes-v2.html) y sus recibos quedan intactos. La [v3 intermedia](../diagrams/aido-post-smoke/process-scopes-v3.html), recibos [8b4a4207](../../.tmp/post-smoke-profiles-ux/8b4a420790f940f2861fe2dcbad40b69), pasó controles automáticos; la revisión final detectó una frase de captura bajo la leyenda Normal. La [v4 vigente](../diagrams/aido-post-smoke/process-scopes-v4.html) distingue explícitamente ambas leyendas, sin cambiar fórmulas, perfiles ni geometría. `validate`, `deliver`, `visual-check`: **0/0/0**, 9/9 showcase, cero errores/warnings; recibos [a50a8c75](../../.tmp/post-smoke-profiles-ux/a50a8c7551734c70a5e035c4aad64063). PNG claro 1440 y oscuro 2048 inspeccionados, controles en cuatro tamaños: revisión visual de esta sesión PASS. El campo automático `visualReview: pending` se conserva en el recibo, no se modifica para simular esa revisión. HTML validado sin edición posterior. Visor inglés; otras vistas no regeneradas.

**SVG: derivados documentales normalizados.** Reproducción sobre el rango completo `f1d529d..8f30c3a`: exit **2**, 84 hallazgos en cuatro SVG. No se usó un árbol limpio como sustituto. [Manifiesto original → derivado](../../.tmp/post-smoke-profiles-ux/86ac2396e0c54062bee5885ceec2a58f/svg-normalization.json): cinco originales preservados mediante `write_binary_artifact` con UUID nuevo y hash comprobado, incluidos los cuatro de 8f30c3a y el nuevo export v3. Sus recibos anteriores no se modificaron. Los cuatro SVG antiguos se sustituyen en el árbol de entrega por nombres `.normalized.svg`; bytes originales recuperables en ese almacén y en Git `8f30c3a:<ruta original>`. HTML v2 y sus PNG/recibos no se retiran.

Cada transformación elimina **21 bytes**, exclusivamente espacios ASCII finales en las 21 cabeceras `@keyframes archify-* {` del bloque style. Fuera de esos offsets los bytes son iguales; XML válido y árbol equivalente salvo ese whitespace CSS. No trim general, cambios de texto, atributos, geometría ni scripts. Normalizados de v2 conservados como revisión obsoleta, no como descripción CPU vigente.

[Equivalencia visual](../../.tmp/post-smoke-profiles-ux/b84a3d90ffdb428badb0d4229192e3e1/svg-visual-equivalence.json): **5/5 PASS**, CSSOM equivalente y PNG idénticos byte a byte, Chromium existente, red bloqueada, SVG nativo como imagen a 1440 px. Imágenes inspeccionadas, no blancos coincidentes. Primer intento de captura fullPage sobre documento SVG XML: timeout 30 s, exit 1 conservado en `86ac2396…/compare-svg-command.json`; el modo imagen pasó sin ampliar el timeout. No se atribuye ese fallo del capturador al producto.

El [original y derivado v4](../../.tmp/post-smoke-profiles-ux/abd31d3d8b834d12aee122fc5f38c6f8/svg-normalization.json) tienen identidades nuevas y la misma transformación acotada de 21 bytes; [XML/CSS/PNG: PASS](../../.tmp/post-smoke-profiles-ux/abd31d3d8b834d12aee122fc5f38c6f8/svg-visual-equivalence.json), exit **0/0/0** de exportar, normalizar y comparar. La v3 intermedia no sustituye a la v4 ni se sobrescribe.

**Validación vigente, 2026-09-08 UTC: PR BLOCKED antes de iniciar procesos.** [Identidad](../../.tmp/post-smoke-profiles-ux/4a2f11400ce94c9698628dfb73c92606/candidate-identity.json): 804 hashes de fuentes/configuración, incluidos lockfiles, iguales al manifiesto de `c8113b0f` en checkout y copia aislada. HEAD de copia **cc0e841e…**, distinto de su contenido sincronizado. Python **3.13.15**, Node **24.16.0**, uv **0.11.7**; import de AIDO desde la copia aislada. El intérprete base preservado en `.tmp/p0-python/` no es un import de fuentes desde otro checkout. Sin cambios de entornos.

[Muestra real](../../.tmp/post-smoke-profiles-ux/4a2f11400ce94c9698628dfb73c92606/availability-sample.json): siete muestras en **31,15 s**, 01:08:48–01:09:17 UTC, mínimo **32.296.476.672**, máximo **32.534.544.384 bytes** disponibles; no se alcanzó margen preferente de 38 GiB. [Admisión/cierre nuevo](../../.tmp/post-smoke-profiles-ux/4a2f11400ce94c9698628dfb73c92606/admission-close.json), gobernador transaccional a 01:09:18 UTC: **32.287.158.272 disponibles**, sesión **19.327.352.832** + reserva host **17.179.869.184** = **36.507.222.016 requeridos**; déficit **4.220.063.744**. `aggregate_memory_budget`, **0 reservas**, perfil `capture_session` **18 GiB/65%/64** intacto. Memoria comprometida y RSS no se descuentan del requisito; consumo propio **UNKNOWN**. No se modificó admisión ni se añadió un Job exterior.

Los **13 gates NOT_RUN**, incluidos HTTP/captura y web; CLI y exit nativo **no disponibles**, no se generó un `pr-session.json` ficticio. El comprobador de preflight terminó **exit 0**, que no es PR PASS. No se inició una suite parcial larga ni se repitió PR; **release local NOT_RUN** por PR pendiente, **tier release NOT_RUN**.

**Commit documental BLOCKED:** el comando `git commit -m "Docs (AIDO): precisa cuotas CPU y normaliza derivados SVG"`, con hook intacto bajo el corredor `build_heavy` usado en la fase, no llegó a iniciar Git. Admisión final a 01:02:53 UTC: **33.333.981.184 disponibles**, **34.359.738.368 requeridos**, déficit **1.025.757.184**, `aggregate_memory_budget`; espera acotada 120 s. Envoltorio **exit 1** por `ResourceWaitError`, Git y Gitleaks **NOT_RUN**, no exit nativo inventado. Evidencia durable `2cab7b83…/quality.sqlite` y [cierre nuevo del rechazo](../../.tmp/post-smoke-profiles-ux/4a2f11400ce94c9698628dfb73c92606/commit-admission-close.json). No se sustituyó ese perfil por otro más ligero ni se eludió el hook. Ajustes agrupados en el índice para un solo commit, **HEAD sigue 8f30c3a**, código **c8113b0f** sin cambios.

`git diff --cached --check f1d529d21bde857aa795c823e7fcbdc4ff051617`: **exit 0 sobre todo el rango preparado**, no sólo un árbol limpio. El rango histórico `f1d529d..8f30c3a` conserva exit 2; no hay un nuevo HEAD comprometido al cual atribuir el check. [Cierre e integridad](../../.tmp/post-smoke-profiles-ux/4a2f11400ce94c9698628dfb73c92606/documentary-closure.json): hashes de originales/derivados y fuentes antes/después, comandos y controles de recursos propios. Preview ajeno 4310 preservado. Dos errores del controlador de preflight (aserción de ruta demasiado amplia y columna `id` inexistente) se conservan identificados; no se atribuyen a un gate ni al producto, no iniciaron suites y sólo se corrigió el controlador temporal.

PO real **NOT_RUN**, inferencia **0**; los PASS baseline conservan `cc3897c5`, los 38 UI focalizados conservan `c8113b0f`. Sin readiness manual, promoción, push, merge, adopción ni resolución declarada del crash histórico. Fase **no cerrada integralmente**: commit documental, PR completo y release local pendientes por admisión de recursos, no por un defecto funcional demostrado.
