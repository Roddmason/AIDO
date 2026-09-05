# Aceptación operacional P0 — 2026-09-05

**Resultado global: BLOCKED.** Las regresiones locales indicadas pasan; no se certificó el ciclo
con Codex y Claude reales. La integridad relacional de la copia operativa tiene un FAIL preexistente.
Este informe no declara «AIDO completamente funcional» ni autoriza adopción, merge o publicación.

## Estado vigente del cierre — 2026-09-05, 15:32Z

Esta continuación tiene autorización acotada para commits locales, copias y hasta dos llamadas
Codex (plan e implementación) más una revisión Claude. Sustituye el bloqueo histórico genérico
de autorización, pero no sustituye las aprobaciones humanas de AIDO ni permite inferencia adicional.
Las secciones 1–12 y la tabla histórica se conservan como evidencia de la fase anterior.
El detalle nuevo, los comandos y las limitaciones están en la sección 13.

| Pista solicitada | Estado | Evidencia y bloqueo vigente |
| --- | --- | --- |
| Candidato preservado en commits | PASS | Cuatro commits locales; candidato de código `e61247932a9d08852c3de75fc57c80208f7c9aa2`. Fuentes idénticas a las de la regresión focalizada nueva. No implica aceptación integral. |
| Integridad de la copia | FAIL | `integrity_check=ok`, pero persiste una FK huérfana. Segunda inspección y restauración conservan el defecto y los datos. Sin padre recuperable demostrado. |
| Readiness Codex | BLOCKED | 0.149.0, autenticación ChatGPT y capacidades requeridas: PASS. Falta `validated_smoke`; no se falsificó salud ni se ejecutó una tercera llamada. |
| Readiness Claude | BLOCKED | 2.1.218, `auth status --json` exit 1, `loggedIn=false`; requiere login humano con la suscripción existente. |
| Ciclo real desde AIDO | BLOCKED | Etapas NOT_RUN por readiness y smoke adicional. Cero llamadas de inferencia; no se reemplazó ninguna etapa por un mock. |
| PR completo del candidato final | BLOCKED | Preview `aggregate_memory_budget`: 27,60 GiB disponibles, mínimo 32 GiB, déficit 4,40 GiB. Comando completo NOT_RUN, exit exterior no disponible. |
| Release del candidato final | BLOCKED | Misma admisión; sus dos gates se conservan en el plan sin ejecutarse. No se usó un release histórico como sustituto. |
| Coexistencia Unreal | NOT_RUN | La sonda vigente no detectó Unreal abierto manualmente. Escenario independiente, sin control del editor. |
| Adopción de datos originales | NOT_RUN | No autorizada. SHA-256 del original sin cambios; ningún cutover, push ni merge. |

## Estados separados — evidencia histórica de la primera fase

| Área | Estado | Alcance de la conclusión |
| --- | --- | --- |
| Implementación P0 | PASS | Implementación existente revisada; tres defectos reproducidos corregidos y regresiones focalizadas aprobadas. No equivale a aceptación integral. |
| Pruebas locales | PASS | 134 pruebas Python, 101 controles de arquitectura, 6 casos adicionales del worker y 2 casos web; checks estáticos y build web aprobados. PR completo final: BLOCKED por admisión de RAM. |
| Runtimes reales | BLOCKED | Binarios identificados; falta autorización de consumo verificable y readiness vigente. Ciclo real: NOT_RUN. |
| Coexistencia Unreal | NOT_RUN | Unreal no estaba abierto; escenario preparado abajo. No se inició ni controló el motor. |
| Recuperación | PASS | Windows real, cancelación, caída del owner, pérdida de fencing y recuperación explícita del mismo job, sin escritores antiguos vivos. |
| Prueba prolongada acotada | PASS | 30 ciclos de procesos de prueba en 135,38 s. No es una prueba de varias horas de un worker residente ni de inferencia. |
| Adopción de datos | FAIL | Una referencia huérfana preexistente. Copia consistente y rollback: PASS; migración de datos originales: NOT_RUN. |

PASS significa únicamente que se ejecutó y cumplió el criterio descrito. FAIL indica un criterio
incumplido; BLOCKED identifica una precondición faltante; NOT_RUN significa que no se ejecutó.
Los tiempos de los recibos están en UTC. Fecha local: 5 de septiembre de 2026, America/Santiago.

## 1. Procedencia, alcance y trazabilidad

- HEAD comprobado: `94240a05d839329dd93cc4d4d339c2d51ba815d5`.
  Rama: `codex/aido-operational-hardening-p0`; árbol inicialmente limpio.
- Se leyó el pedido original completo en
  `C:\Users\Rodd\.codex\attachments\c81e980c-e5b4-4cc3-97aa-d882b700e46b\pasted-text.txt`,
  el informe `docs/operational-hardening/p0-verification-report.md`, los documentos P0 de
  arquitectura, recursos y recuperación, y los recibos originales con sus salidas.
- Se verificaron por SHA-256 los **56 artefactos de stdout/stderr referenciados** por los cinco
  recibos principales. El inventario conserva ruta, tamaño, hash esperado/obtenido y resultado.
  No se usó un resumen conversacional como evidencia.
- Commits P0 existentes: `35016f0b`, `a80a80b7`, `b479ec5a`, `8a6a15d3`, `ae91765d`,
  `85d21825`, `2c4dafb1`, `94240a05`. Baseline original: `94eaf8c1`.
  No se creó ningún commit, rama nueva, merge ni push en esta aceptación.
- Los cambios de esta aceptación permanecen sin commit y se identifican por hashes de contenido.
  No se reemplazó la arquitectura, no se inició P1/P2, ni se añadieron integraciones externas,
  credenciales, APIs pagadas o control de Unreal. Trabajo serial, sin subagentes.

Raíz de evidencia local: `H:\Proyectos\Personales\AIDO\.tmp\operational-hardening-p0\`.
En este informe, **A/** significa `acceptance/` y **F/** significa `acceptance/final/` bajo esa raíz.
Son archivos locales ignorados por Git: conservarlos junto al informe si se transporta la evidencia.
`F/evidence-index.json` contiene hashes de los recibos finales y de sus artefactos; no es una firma externa.

### Evidencia histórica frente a código final

| Evidencia original | Resultado leído | Límite de trazabilidad |
| --- | --- | --- |
| `verify-db869fa39a224ccebb7f9e29a65fc92c/report.json` | exit 0; 05:39:50.068Z–06:42:53.087Z | SHA-256 `cb37f7624ecdbdcd5e636030bb62e1eb224a172c6643d80b8795155f9ee6a6fd` |
| `quality-pr-25153469e0d14808986260431e26c1ec.json` | 12 gates exit 0; 1.879 Python, 337 web, 7 skips | SHA-256 `59ae0c088fd4d6908ca193005cd6bf1c55246a8d3859a47cd8a36dbf45d57879` |
| `release-9a28f64605f24fc78249d912828d7654.json` y `final-clean-install.json` | 7 pasos exit 0; fixture 58→67, instalación aislada y HTTP 200 | No es adopción de la base operativa real ni certificación de proveedores |
| `post-commit-process-audit.json` | 0 raíces huérfanas/no resueltas/abandonadas | Fotografía histórica, no estado actual |

El último gate original terminó a las 06:44:14.816Z; el HEAD se creó a las 06:48:13Z.
Los recibos originales **no contienen manifiesto del contenido fuente probado**. El orden temporal
y la afirmación del informe anterior no prueban criptográficamente igualdad de código: esta parte
de la trazabilidad histórica queda FAIL por evidencia insuficiente, sin afirmar que los tests fueran falsos.

Después de corregir código productivo se ejecutaron de nuevo las regresiones pertinentes.
`F/source-before.json`, `F/source-after-python.json`, `F/source-before-web.json` y
`F/source-final.json` registran HEAD, archivos, configuración/lockfiles y versiones. El hash agregado
de los árboles productivos (`local_control_center/`, `local-control-center/`, `scripts/`) es:

`baea87a38bbb4287db1f8b19be1feb1929578253c50954a4ca6ae2d32f860049`.

No hubo cambios productivos entre esos gates. El test web de latencia se añadió después del gate
Python y se probó posteriormente; los hashes individuales permiten distinguir esa secuencia.
La redacción final del informe es posterior a las pruebas y no altera código productivo.
El primer manifiesto pasó por el redactor de recibos: algunas entradas cuyo nombre contiene
`credentials`/`tokens` quedaron como `[redacted]`. No se tratan como hashes válidos. Se compararon
el hash agregado productivo preservado y los hashes disponibles de todas las pruebas afectadas;
el índice declara esas omisiones en vez de afirmar paridad individual de las entradas redactadas.

Versiones verificadas: Windows, Python **3.13.15**, SQLite **3.53.1**, Node **24.16.0**,
pnpm **10.24.0**, Vite **6.4.2**, FastAPI **0.136.1**, Uvicorn **0.47.0**, psutil **7.2.2**,
pywin32 **311**, pytest **9.0.3**, Pydantic **2.13.4**. Se preservaron lockfiles y políticas de seguridad.

## 2. Matriz requisito → implementación → prueba → evidencia → limitación

Los módulos Python de implementación de esta tabla están bajo `local_control_center/`;
las rutas `tests_py/` y `tests_web/` son relativas a la raíz del repositorio.

| Requisito | Implementación | Prueba | Evidencia / estado | Limitación |
| --- | --- | --- | --- | --- |
| Separar HTTP de ejecución durable | `workers/`, `jobs_approvals/`, contexto de `process_supervision/` | `test_operational_http_boundaries.py`, `test_worker_leadership.py`, `test_durable_executions.py` | F/affected-python.json, PASS | No prueba un proveedor remoto |
| Gobierno y presupuesto agregado | `host_resources/governor.py`, `profiles.py` | tests de recursos/admisión; contraejemplo de lease prestado | A/borrowed-lease-budget-red.json → A/borrowed-lease-budget.json, PASS corregido | Control plane esencial queda fuera del presupuesto no esencial; no es un tope de toda la CPU del host |
| No multiplicar un lease con raíces independientes | `process_supervision/service.py` | reserva previa al spawn, carrera concurrente, lectura nativa | Test de reserva y 134 regresiones, PASS | Anidamiento permitido sólo para un ancestro nativo con PID y tiempo de creación coincidentes |
| Pertenencia y límites de padres/descendientes | `process_supervision/windows_job.py` | `native_readback`, `IsProcessInJob`, consulta de límites | F/native-*.json, PASS | Windows de este host; POSIX no certificado |
| Cancelación y caída del worker | supervisor, `recovery.py`, worker real | cancelación de job, `os._exit(17)`, nuevo worker | F/native-cancel.json, F/native-crash.json, PASS | Cuerpo del job es un escritor de prueba, no Codex |
| Pérdida de liderazgo sin escritor viejo | `workers/leadership.py`, guardia en `requeue_expired_jobs` | expiración aislada + intento de requeue mientras está registrado + retry del mismo job | F/native-loss.json, PASS | Expiración inyectada en DB desechable; reintento explícito tras revisión, no replay automático de efectos inciertos |
| Explicar pico CPU histórico | monitor `quality/__main__.py` y recibos originales | lectura de máximos, CPU acumulada, duración y configuración | A/inventory.json, FAIL de atribución histórica | No existen muestras por PID/instante ni duración del pico |
| Latencia bajo carga pequeña | API real + Job de escritor; panel Playwright real | 20 muestras HTTP, cancelación HTTP y 2 viewports | F/latency.json, F/git-panel-latency.json, PASS | Sin saturación; no es percentil de producción ni full-data benchmark |
| Ciclo desde AIDO con Codex y Claude | `product_loop/`, `workspaces_projects/`, `agents/cli_runtimes/`, `evidence/` | Guion preparado; revisión de instalación/readiness sin inferencia | A/operational-state.json, BLOCKED; ciclo NOT_RUN | Falta autorización verificable de consumo y configuración vigente |
| Coexistencia con Unreal manual | sonda de host y política existente | Guion de la sección 7 | F/final-audit.json: Unreal ausente, NOT_RUN | No se abrió, cerró ni automatizó Unreal |
| 30 ciclos sin inferencia repetitiva | worker/cola/SQLite/supervisor productivos; cuerpo de prueba | `test_thirty_sequential_cycles_with_cancellation_and_recovery` | F/soak-30.json, PASS | 135,38 s, muestra entre ciclos; no horas de proceso residente |
| Resolver 2 skips Git y conservar 5 de viewport | `tests_web/control-center.spec.js` | Git real desechable, refresh durable, panel desktop/mobile | F/git-panel-latency.json, PASS | Los otros cinco se clasificaron; no se volvió a ejecutar toda la suite web |
| Adoptar y revertir sólo copia consistente | `quality/maintenance.py`, migraciones existentes | backup, restore a rutas nuevas, init doble de copia y rollback | A/inventory.json + A/adoption-assessment.json | Copia/rollback PASS; integridad relacional FAIL; original sin migrar |

## 3. Defectos demostrados y correcciones mínimas

1. **Admisión agregada insuficiente.** El conteo de jobs y un tope por job no impedían que la suma
   de reservas superara CPU/RAM disponibles. Tres tests fallaron primero. Se agregó la suma de
   límites no esenciales y se conservó la reserva de RAM del host. Resultado inicial: 19 PASS;
   incluidos posteriormente en el gate de 134 pruebas.
2. **Recuperación prematura.** Un job con lease vencido podía volver a cola mientras su CLI seguía
   registrado. El test de pérdida de liderazgo falló primero. `requeue_expired_jobs` ahora espera
   que todos los procesos del mismo execution estén finalizados y liberados. La limpieza/fencing
   ocurre antes del reintento; el job no se declara seguro sólo porque expiró un timestamp.
3. **Multiplicación de raíces con lease prestado.** Un probe con tres procesos dormidos obtuvo tres
   Job Objects de 25% bajo una sola reserva de 25%: suma nominal 75%, exit 1. Esto demuestra el
   defecto de presupuesto, no un consumo observado de 75%. Se agregó reserva durable de raíz
   antes del spawn, con transacción corta y consultas de identidad OS fuera de ella. El probe
   corregido admitió sólo una raíz, exit 0; la prueba concurrente y 30 tests de supervisión pasaron.

Se preservaron los recibos rojos: `acceptance-aggregate-red.xml`,
`acceptance-recovery-race-red.xml`, `acceptance-root-reservation-red.xml` y
`A/borrowed-lease-budget-red.json`. No se ocultaron mediante retries ni cambios de umbrales.
Sólo se modificaron tres módulos productivos, sus pruebas y documentación relacionada.

La política de RAM es conservadora: reserva los topes completos sin descontar el consumo ya
atribuible a cada lease, porque no existe esa atribución fiable en la muestra. Reduce concurrencia
y puede diferir trabajo que consumiría menos que su máximo. No se aumentaron techos para forzar PASS.

## 4. CPU, presupuesto agregado y latencia

### Pico original de 98,8%

El verifier registró **CPU del host**, no «CPU de AIDO»: máximo 98,8%, 1.883 muestras,
intervalo nominal de 2 s; RAM libre mínima 29.402.816.512 bytes. El PR anidado registró 98,7%
y 1.809 muestras. El monitor sólo persiste máximos/mínimos/conteos, no la serie temporal.

| Dato solicitado | Hecho disponible |
| --- | --- |
| CPU del host | 98,8% máximo del verifier; no timestamp del máximo |
| CPU de AIDO en ese instante | No registrada: no atribuible retrospectivamente |
| Duración del pico | No registrada; 2 s de polling no prueba duración sostenida |
| Jobs activos en el pico | No se conservaron IDs por muestra. El PR contiene pasos seriales, pero no hay correlación con ese instante |
| CPU acumulada del árbol PR | 3.228,65625 s CPU en 3.635,496 s de ejecución |
| Media normalizada ilustrativa | 4,44% de capacidad del host de 20 procesadores lógicos; no es el pico. No se suma otra vez la CPU de hijos incluida en el padre |
| Límites efectivos históricos | Perfiles/leases solicitados conservados; no readback nativo histórico de CPU/RAM. El readback actual no prueba el pasado |

Por tanto, no hay datos suficientes para explicar causalmente ese pico. Culpar a AIDO o a otra
aplicación sería especulación. Las tres correcciones cierran defectos demostrados de admisión y
recuperación; **no se afirma que hayan sido la causa del 98,8%**.

### Medición nueva y acotada

`F/latency.json`: 20 muestras durante **5,57 s**, dos árboles activos identificados: API y escritor
con un hijo. CPU del host: máximo **32,9%**, p95 **32,0%**. CPU acumulada diferencial de esos dos
Job Objects, dividida por tiempo y 20 CPUs: máximo **2,41%**, p95 **1,64%** del host.
No se atribuye la CPU restante a procesos particulares.

Readback de Windows: API `cpuFlags=5`, `cpuRate=1000`, 4 GiB/8 procesos; escritor
`cpuFlags=5`, `cpuRate=500`, 256 MiB/8 procesos. Ambos con kill-on-close y miembros confirmados.
Suma de rates declarados: **15%**, suma de topes RAM: **4,25 GiB**; reservas conservadoras de
dos `qa_light`: **50% y 8 GiB**, dentro de 65% y del headroom observado. Durante este gate hay
además un padre `agent_cli` de 40%/8 GiB que contiene los tests: no sumar padres e hijos como
consumidores independientes. Los límites anidados pueden restringir más la CPU efectiva.

La política agregada limita trabajo no esencial. `control_plane` es esencial y conserva su perfil
independiente (20%/2 GiB); las aplicaciones externas tampoco pertenecen a un Job Object global
de AIDO. No existe aquí una garantía de que **todo el host** permanezca bajo 65%.

| Medición | p50 ms | p95 ms | máximo ms |
| --- | ---: | ---: | ---: |
| GET `/` | 3,39 | 3,98 | 136,16 |
| GET `/healthz` | 1,61 | 2,03 | 2,73 |
| GET `/api/v1/overview` | 15,44 | 17,20 | 17,75 |
| GET `/api/v1/workers/status` | 13,89 | 15,57 | 15,60 |

Cancelación real por HTTP: **202**, respuesta **13,99 ms**; todas las identidades del escritor
desaparecieron a **244,67 ms** desde el inicio de la cancelación. Los GET devolvieron 200.
Criterio del fixture: cada petición <1 s; no hubo timeout.

Panel Playwright real, contenido Git visible más dos frames de navegador: **700,88 ms desktop**
y **540,53 ms mobile**. Carga adicional: un hilo del host con 2 ms activos cada 50 ms, terminado
en `finally`; 12 y 9 ticks durante la medición. Criterio acotado: <5 s. Una navegación por viewport
no estima p95 visual ni garantiza fluidez sostenida. Las skills UI orientaron la medición visible;
no se rediseñó la interfaz ni se rebajaron sus exigencias.

## 5. Windows: pertenencia, cancelación y recuperación

La prueba consulta al kernel, no sólo la lista de PIDs de psutil:
`QueryInformationJobObject` (miembros/límites/CPU) e `IsProcessInJob` para cada PID.
Los escritores raíz e hijo aparecen como cuatro PIDs por los launchers de la venv, todos miembros
del Job con `cpuRate=500`, 256 MiB, límite de 8 procesos y `KILL_ON_JOB_CLOSE`.
Se registra también tiempo de creación para evitar aceptar un PID reutilizado.

Contrato contrastado con documentación primaria de Microsoft:
[consulta del Job](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject),
[pertenencia](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob),
[CPU rate](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information)
y [jobs anidados](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs).

| Escenario final | Exit del owner | Tiempo observado hasta retorno del owner | Resultado |
| --- | ---: | ---: | --- |
| Terminación normal | 0 | 1.117,72 ms | PASS, sin identidades restantes |
| Cancelación del job | 0 | 204,99 ms | PASS, árbol cancelado y mismo job recuperado |
| Caída abrupta del owner | 17 intencional | 36,44 ms | PASS, cierre del Job y evidencia parcial recuperada |
| Pérdida de liderazgo | 0 | 355,59 ms | PASS, `leadership_fence_lost` y fence mayor en reemplazo |

Estos tiempos miden hasta el retorno del owner; después se verifica explícitamente desaparición
de todos los PIDs originales. No se confunden con el tiempo HTTP hasta árbol ausente de la sección 4.
El CLI de prueba puede terminar con código de cancelación Windows `3221225786`; no se lo registra
como terminación natural exitosa. El test espera ese desenlace, no que todo subprocess devuelva 0.

En cancelación, caída y pérdida de liderazgo, otro proceso worker recupera **el mismo job ID**,
con fencing mayor. Se verifica job terminal `completed`, PIDs viejos ausentes y tamaños de los
logs viejos sin cambios durante toda la ejecución de reemplazo. Es revisión/retry explícito sobre
datos desechables; no es exactamente-una-vez para efectos externos arbitrarios.

Auditoría final a las **08:30:53Z**: 226 identidades nativas comprobadas como ausentes; cero registros
activos, huérfanos, no resueltos o abandonados en las tres bases supervisadas inspeccionadas.
`activeAidoProcessCount` de la sonda es una heurística de texto de comandos y puede contar al propio
auditor; no se usa como prueba de que haya cuatro workers operativos. No se terminó ningún proceso ajeno.

## 6. Ciclo real desde AIDO: BLOCKED

Instalación identificada: checkout de este repositorio, `.venv` local y `node_modules`; el instalador
existente instala en esa ubicación. Base predeterminada real:
`C:\Users\Rodd\.claude\local-control-center\platform.sqlite`. No había variable de entorno de DB
alternativa ni API/worker operativo verificado al inicio. No se supone que esta sea la única copia
de AIDO que pueda existir en otros discos.

| Runtime | Binario / versión instalada | Estado registrado en DB | Aceptación |
| --- | --- | --- | --- |
| Codex | `C:\Users\Rodd\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe`, 0.149.0 | Versión cacheada 0.142.2/offline; salud de cuenta antigua; 0 capability probes y 0 smoke receipts | BLOCKED, no readiness vigente para el binario actual |
| Claude | `C:\Users\Rodd\.local\bin\claude.exe`, 2.1.218 | Instalación healthy de julio; cuenta `unauthenticated`, sin validación | BLOCKED, no sesión autorizada verificable |

Se usó únicamente `--version` y lectura de metadatos. Un CLI instalado, un estado healthy antiguo
o esta sesión de Codex Desktop **no constituyen autorización para consumir ambos runtimes desde AIDO**.
No se hizo login, se copiaron tokens, se crearon credenciales ni se habilitaron APIs.

Requisito exacto pendiente: autorización de un ciclo acotado con las cuentas y runtimes elegidos,
alcance de consumo permitido, autenticación nativa de Claude y preflight/capability/smoke vigente
de Codex; además, selección válida de runtime/modelo y política del proyecto desechable en AIDO.
Si algún contrato no se cumple, mantener BLOCKED; no sustituir por un mock y llamarlo certificación.

Guion preparado para esa autorización, sin ejecutar inferencia en esta tarea:

1. Crear un repositorio desechable local sin remotos ni secretos y una DB aislada de AIDO.
2. **Desde el panel de AIDO**, registrar el proyecto y enviar una solicitud pequeña, por ejemplo
   implementar una función pura y dos tests. Conservar thread ID, request ID, job ID y timestamps.
3. Obtener el plan desde AIDO, revisarlo y aprobar sólo ese alcance. Estado actual: NOT_RUN.
4. Verificar worktree creado por AIDO y su commit base; Codex sólo puede escribir allí.
5. Ejecutar una única implementación Codex, QA focalizado con comando/exit/output hash y una
   revisión Claude de solo lectura. Verificar hash y Git status antes/después de la revisión.
6. Exportar evidencia vinculada al diff final, versión/configuración y resultados; sin merge/push.

Todos los pasos del ciclo real quedan **NOT_RUN**, subordinados al BLOCKED de autorización/readiness.
El fixture web sí crea un proyecto y refresca Git desde la API real, pero no cubre plan, Codex y Claude.
El antiguo lifecycle con protocolo localhost controlado tampoco constituye esta certificación.

## 7. Coexistencia con Unreal: NOT_RUN, escenario preparado

Las sondas previas y final indican `unrealEditorRunning=false`; no se abrió Unreal para fabricar
el escenario. Preparación para una sesión posterior:

- El usuario abre manualmente Unreal y deja el juego sin cambios. Registrar versión, PID/creation
  time y hash/estado inicial del juego con lecturas; no guardar assets, ejecutar cook ni controlar el editor.
- Tomar 30 s de baseline de CPU/RAM/disco y latencia del panel de AIDO aislado; usar la sonda real,
  no `ResourceSnapshot.test_snapshot()`. Registrar políticas y reservas efectivas con Unreal presente.
- Solicitar sólo un job de prueba pequeño, con 5% de CPU/256 MiB, sin proveedor ni modelo GPU.
  Si el gobernador no admite la reserva canónica, registrar BLOCKED y no eludir la admisión.
- Si se admite, medir 20 peticiones del panel, navegación visible y una cancelación; comprobar
  readback Windows, árbol ausente y que el proceso Unreal sigue con su identidad original.
- Interrumpir únicamente la carga propia ante RAM bajo la reserva, CPU sostenida sobre la política
  o degradación observable. No elevar carga hasta saturación ni cerrar procesos del usuario.
- Criterios: peticiones <1 s, navegación visible <5 s, cancelación <1 s para este escenario pequeño,
  cero procesos propios residuales y cero modificaciones al juego. Guardar tiempos medidos,
  incluidas fallas; no inferir ausencia de impacto sólo por un gate técnico.

Este protocolo no reutiliza sin cambios el fixture HTTP que exige Unreal ausente. Tampoco usa la
sonda determinista de las pruebas de fallas para afirmar coexistencia. No hay resultado de este escenario.

## 8. Treinta ciclos y tendencias

`F/soak-30.json`: **30/30 PASS**, 12 normales, 6 cancelaciones, 6 caídas y 6 pérdidas de liderazgo;
18 reemplazos explícitos del mismo job incluidos. Duración **135,38 s**, 31 muestras entre ciclos.
Cola, claim, heartbeat, fencing, SQLite y supervisión son código productivo; sólo el cuerpo de
ejecución del job se sustituye por escritores deterministas no facturables. Admisión de esos tests
usa una fotografía determinista; el gate exterior sí pasó admisión real y contención nativa.

| Métrica | Inicial | Final | Cambio |
| --- | ---: | ---: | ---: |
| RSS del controlador de tests, bytes | 162.729.984 | 162.918.400 | +188.416 |
| Memoria privada del controlador, bytes | 3.327.700.992 | 3.327.709.184 | +8.192 |
| Handles del controlador | 277 | 275 | -2; máximo 278 |
| Hilos del controlador | 43 | 43 | 0; máximo 44 |
| Conexiones / descendientes entre ciclos | 0 / 0 | 0 / 0 | 0 |
| SQLite, bytes | 2.420.736 | 2.797.568 | +376.832 |
| Carpeta de evidencia incluida DB, bytes | 2.420.736 | 2.897.015 | +476.279; no sumar otra vez la DB |
| WAL entre ciclos, bytes | 0 | 0 | 0; checkpoint pasivo final `(0,0,0)` |
| Lecturas / escrituras acumuladas, bytes | — | — | +30.347.346 / +8.561.267 |
| Espacio libre del volumen, bytes | 91.994.607.616 | 91.991.162.880 | -3.444.736, incluye actividad ajena |
| Procesos de todo el host | 544 | 539 | -5; no atribuible a AIDO |

No se observó acumulación de handles, hilos, conexiones ni procesos entre ciclos. El crecimiento
de DB/evidencia corresponde a historial preservado, no a cero escritura. La memoria privada
incluye dependencias numéricas ya cargadas en pytest; no es RSS del worker liviano.
El muestreo entre conexiones cerradas **no mide el máximo de WAL durante transacciones**.
No se certifica ausencia de fugas a largo plazo en un API/worker residente ni comportamiento
con bases grandes, jobs heterogéneos o inferencia real.

## 9. Siete skips y warnings

Los dos skips Git (desktop/mobile del mismo test) se reemplazaron por un repo `mkdtemp` real,
branch `aido-acceptance`, commit vacío y refresh explícito. El wire devuelve 202; el fixture
`operations.js` espera la operación y entrega su respuesta terminal 200. No se falsea que el
endpoint productivo sea síncrono. Git status y UI leen el snapshot durable. Ambos casos PASS.
El repo desechable propio se eliminó al finalizar; no se tocó el repo del usuario.

Se conservan los cinco casos no aplicables por viewport:

- `tests_web/ide-resizable-shell.spec.js:20`, `:39`, `:64`: resize/shortcuts/persistencia desktop,
  omitidos en mobile.
- Mismo archivo `:106`: fallback estrecho, omitido en desktop.
- `tests_web/thread-lifecycle-e2e.spec.js:339`: lifecycle nativo diseñado para desktop, omitido en mobile.

No se declara «339 web PASS» como si se hubiese repetido la suite completa: 337/7 es histórico,
y los dos casos reparados son el gate focalizado actual. El nuevo soak Python requiere opt-in
`AIDO_ACCEPTANCE_SOAK=1`; su skip sin opt-in no sustituye los 30 ciclos ejecutados en esta aceptación.

| Warning | Clasificación / tratamiento |
| --- | --- |
| Biome: 6 `useExhaustiveDependencies`, `reloadToken` en AgentsPage 208/231/254 y EvidencePage 80/107/139 | Deuda de claridad de refresh/performance. Persisten; no aplicar autofix inseguro que podría quitar el disparador de recarga |
| SWIG: `SwigPyPacked`, `SwigPyObject`, `swigvarlink` sin `__module__` | Deprecación de dependencia nativa importada por la suite; 3 warnings visibles. No prueba fallo funcional ni se suprimió |
| Vite: chunk principal 702,74 kB, umbral 500 kB | Riesgo de carga/performance. No se aumentó el umbral; la medición local no elimina el warning |
| Semgrep: 1 archivo >1 MB omitido | Único archivo de ese tamaño encontrado en el scope: `tests_py/__pycache__/test_product_loop_coordinator.cpython-311-pytest-9.0.3.pyc`, 1.060.679 bytes, bytecode antiguo. Sin borrarlo ni ocultar la exclusión. 4 reglas/333 targets, no auditoría exhaustiva de seguridad |
| `NO_COLOR` ignorado por `FORCE_COLOR` | Presentación del runner; conservado en stderr |
| uv: entorno diferente / hardlink→copy en instalación original | Entorno objetivo explícito y fallback de copia entre volúmenes; advertencias históricas, no ahorro probado |
| pnpm: build script de esbuild no autorizado | Política existente de supply chain; build verificado sin habilitar scripts adicionalmente |

Gitleaks: 0 hallazgos dentro del scope configurado; `.tmp`, documentación y dependencias tienen
exclusiones preexistentes. No equivale a revisar secretos en todo el disco ni a escanear credenciales.

## 10. Instalación, copia operativa y rollback

Base real: `C:\Users\Rodd\.claude\local-control-center\platform.sqlite`, **404.254.720 bytes**,
schema **67**. No hay overrides `resources.*`/`worker.*` almacenados; se resolvieron defaults.
SHA-256 antes del backup y al cierre:
`aadfc254b34e9f13309cd9d4f48a1530ce6e36e9cc28524a0cd4493f634f1dc2`.

Ensayo aislado en
`C:\Users\Rodd\AppData\Local\Temp\aido-acceptance-adoption-20260905-0748\`:
`consistent-backup/`, `adoption/`, `rollback/`. Se usó backup SQLite consistente y el bundle
de inputs del producto, sin descifrar ni copiar credenciales externas. El backup toma brevemente
el lock de coordinación existente; no migra ni modifica filas originales.

Se restauró a rutas nuevas, se inicializó dos veces **sólo la copia** y se verificaron schema,
conteos por tabla, `PRAGMA integrity_check` y `PRAGMA foreign_key_check`; luego se restauró el
backup a otra ruta de rollback. Conteos y schema coinciden en las tres copias. El original sigue
con hash idéntico; no hubo cutover de la instalación operativa.

**FAIL de integridad relacional:** las tres copias preservan
`provider_limit_observations`, rowid `1`, referencia a `provider_limits`, constraint `0`.
`integrity_check='ok'` no cubre esa validez referencial. No se eliminó ni reparó esa fila: requiere
reconciliar el dato en un trabajo explícito antes de adoptar.

El primer `A/inventory.json` llamó PASS a la paridad de copia/rollback. Se preservó ese recibo
original y se corrigió la clasificación en `A/adoption-assessment.json`: **adopción FAIL,
copia/rollback PASS**. El script de inventario también exige ahora cero violaciones para emitir
PASS. Un exit 0 del comando de inventario significa que pudo producir el diagnóstico, no que
todos sus criterios de datos sean PASS.

El ensayo usa una base operativa ya en schema 67; no prueba upgrade de datos reales históricos
58→67. Ese caso sólo tiene el fixture histórico de release. Las copias se conservaron como
evidencia local y contienen datos del usuario: no publicarlas ni incorporarlas a Git.
También se preservó `.tmp/p0-python/`, de la que depende la venv actual; no es basura eliminable.

## 11. Comandos, exit codes y evidencia final

Los comandos de calidad se ejecutaron serialmente mediante
`local_control_center.quality.__main__._run(QualityStep(...), root=Path.cwd(),
db_path=Path('.tmp/operational-hardening-p0/acceptance/final/quality.sqlite'))`.
Ese wrapper aplica admisión, Job Object, timeout y captura con SHA-256; `_write_report` persiste
el resultado. No ejecutar los comandos pesados sin ese wrapper o su CLI equivalente.
Los recibos de checks estáticos incluyen el argv exacto; los tests siguientes especifican el suyo.

| Comando / selección | Exit | Resultado / recibo |
| --- | ---: | --- |
| `git rev-parse HEAD`, `git branch --show-current`, `git status --short` | 0 | HEAD/rama y conservación de cambios; F/source-*.json |
| `uv run python -m tests_py.operational_acceptance_inventory --source C:\Users\Rodd\.claude\local-control-center\platform.sqlite --output .tmp/operational-hardening-p0/acceptance/inventory.json --adoption-root C:\Users\Rodd\AppData\Local\Temp\aido-acceptance-adoption-20260905-0748` | 0 | 56 hashes verificados; diagnóstico de adopción corregido por assessment |
| `python -m pytest` con selección detallada abajo, perfil `agent_cli`, timeout 1.800 s | 0 | 134 PASS / 278,21 s; F/affected-python.json y .xml |
| `node scripts/run-web-tests.mjs tests_web/control-center.spec.js --grep "Workbench shows the Git branch detected"`, perfil `browser_test`, timeout 600 s | 0 | 2 PASS; build Vite incluido; F/git-panel-latency.json |
| `python scripts/productive-truth-scan.py` | 0 | F/productive-truth.json |
| `node node_modules/typescript/bin/tsc --noEmit -p local-control-center/web/tsconfig.json` | 0 | F/typecheck.json |
| `uv run --extra dev ruff check .` / `ruff format --check .` | 0 / 0 | F/ruff.json, F/format.json; 506 archivos formateados |
| `node node_modules/@biomejs/biome/bin/biome check local-control-center/web` | 0 | F/biome.json; 6 warnings, no fixes |
| `python -m pytest` sobre los cinco `ARCHITECTURE_TESTS` de `quality/plans.py`, `-q` | 0 | 101 PASS; F/architecture.json |
| `gitleaks detect --no-git --source . --config .gitleaks.toml --redact` | 0 | Sin leaks; F/secrets.json |
| `uv run --extra dev semgrep --legacy scan --jobs 1 --error --metrics off --disable-version-check --config .semgrep.yml --no-git-ignore local_control_center tests_py local-control-center/web/src tests_web` | 0 | 4 reglas, 333 targets, 0 findings; F/semgrep.json |
| `python -m pytest tests_py/test_python_control_center.py -k "worker or lease or recover" -q` | 0 | 6 PASS / 28 deselected; F/legacy-worker.json |
| `python local-control-center/scripts/generate_openapi_client.py --check` | 0 | Sin drift; F/openapi.json |
| `git diff --check` | 0 | F/diff-check.json; repetido al cierre |
| Consulta final de identidad, DB hash y auditoría de raíces, sin cleanup ajeno | 0 | F/final-audit.json |
| PR/release completos sobre el código corregido | — | BLOCKED: preview `aggregate_memory_budget`, sin comando pesado iniciado; F/full-pr-admission.json |
| Ciclo Codex/Claude, coexistencia Unreal, cutover de datos originales | — | NOT_RUN; no hay exit code inventado |

Selección Python exacta, con `AIDO_ACCEPTANCE_SOAK=1` y `AIDO_ACCEPTANCE_EVIDENCE` apuntando a F/:

```text
python -m pytest
  tests_py/test_host_resource_governor.py
  tests_py/test_branch_resource_admission.py
  tests_py/test_process_supervision.py
  tests_py/test_operational_hardening_p0.py
  tests_py/test_operational_http_boundaries.py
  tests_py/test_worker_leadership.py
  tests_py/test_operational_recovery.py
  tests_py/test_operational_launchers.py
  tests_py/test_durable_executions.py
  tests_py/test_codex_capability_contract.py
  tests_py/test_quality_tiers.py
  tests_py/test_operational_acceptance_native.py
  tests_py/test_operational_acceptance_latency.py
  -q --junitxml=.tmp/operational-hardening-p0/acceptance/final/affected-python.xml
```

Errores del harness también preservados: el primer test HTTP falló por una ruta de static-dir
incorrecta del fixture (404), corregida antes del gate final. Tres intentos iniciales de preparar
el wrapper web fallaron con TypeError por firmas de helpers, antes de iniciar carga. El gate web
final sí terminó exit 0 y escribió su recibo; el comando exterior devolvió 1 al imprimir Unicode
en cp1252 después de guardar el éxito. Se releyó el recibo con salida JSON segura, exit 0; no se
convirtió ese error de presentación en fallo de producto ni se ocultó el exit exterior.
La primera comparación de manifiestos también falló al comparar entradas `[redacted]` con hashes;
se corrigió el criterio de auditoría para declarar esa limitación, sin modificar los recibos previos.

A las 08:30:53Z había **30.705.717.248 bytes (28,60 GiB)** libres. `build_heavy` reserva 16 GiB
y la política conserva 16 GiB libres: necesita al menos 32 GiB bajo el criterio conservador actual.
El preview real rechazó el PR completo con `aggregate_memory_budget`. No se cerraron aplicaciones
ajenas, se redujeron reservas ni se llamó PASS a un gate no ejecutado. Los gates finales focalizados
usan sus perfiles existentes y no sustituyen ese PR completo para una futura promoción.

## 12. Condiciones pendientes para aceptación integral

1. Autoridad y readiness verificables para ejecutar una vez el ciclo completo desde AIDO con
   Codex y revisión Claude de solo lectura, sobre un repo desechable.
2. Reconciliar la referencia huérfana en una copia, volver a validar integridad y acordar por separado
   cualquier adopción de datos operativos. No autoriza una migración implícita.
3. Ejecutar la coexistencia cuando el usuario tenga Unreal abierto manualmente, sin tocar el juego.
4. Disponer de capacidad admitida para repetir PR/release completo sobre el candidato final antes
   de promoverlo. Preservar manifiesto y recibos; no reutilizar como actuales los gates históricos.

Las limitaciones de atribución del pico histórico y de duración/muestreo del soak permanecen
explícitas. No hay push, merge, cambio de instalación operativa ni promesa de funcionamiento integral.

## 13. Continuación de cierre, sin repetir el hardening completo

### 13.1. Candidato, alcance y trazabilidad

Se releyó este informe completo y se revisó el diff actual antes de escribir en Git. El HEAD inicial
seguía siendo `94240a05d839329dd93cc4d4d339c2d51ba815d5`, sobre la misma rama. Se preservaron las
correcciones de aceptación existentes; no se agregó código productivo en esta continuación.
No se ejecutaron reset, restore destructivo, add indiscriminado, push ni merge.

| Commit local | Responsabilidad y pruebas conservadas |
| --- | --- |
| `ab1ac065dad76ea655f61335df9080d7ed655852` | Presupuesto agregado: `host_resources/governor.py`, tres regresiones en `test_host_resource_governor.py`. |
| `6c5147bcd90561d1936094403368537b5b9062cd` | Reserva de una raíz por lease: `process_supervision/service.py`, prueba concurrente, soporte nativo y contraejemplo de presupuesto prestado. |
| `40eda68a6a6751f40ace2c4d3c4f305ce6fa4605` | Recuperación no prematura: `jobs_approvals/repository.py`, worker de prueba y cancelación/crash/pérdida de liderazgo contra Windows. |
| `e61247932a9d08852c3de75fc57c80208f7c9aa2` | Fixtures Git deterministas, latencia HTTP, inventario de copias y documentación de perfiles. |

Los cuatro commits devolvieron exit 0 con hooks activos: gitleaks sin leaks, Ruff format/check PASS.
Este informe se guarda en un commit documental posterior. El HEAD de entrega, el hash del informe
y el vínculo al candidato de código quedan en `C/final-traceability.json`; no se introduce un hash
autorreferente dentro del propio commit.

**C/** significa `.tmp/operational-hardening-p0/closure/`. Los recibos, SQLite, copias y scripts
temporales no se incorporan a Git. Los scripts de diagnóstico están en
`C:\Users\Rodd\AppData\Local\Temp\aido-p0-closure-a9cb34c76f2a4bc3a75c45bc37298d6f\` (**T/**).
Se conservaron para auditoría local y no se deben publicar con las bases.

`C/source-start.json` y `C/source-candidate.json` registran todos los hashes de fuentes, pruebas,
configuración y lockfiles (`pyproject.toml`, `uv.lock`, `package.json`, `pnpm-lock.yaml`, entre otros).
La comparación antes/después de los commits tuvo **cero entradas de contenido distintas**.
Hash productivo agregado:
`baea87a38bbb4287db1f8b19be1feb1929578253c50954a4ca6ae2d32f860049`.
Python 3.13.15; SQLite 3.53.1; FastAPI 0.136.1; uvicorn 0.47.0; psutil 7.2.2; pywin32 311;
pytest 9.0.3; Pydantic 2.13.4. Los ajustes efectivos del gobernador están en
`C/gate-final-admission.json`, no se infieren del nombre del perfil.

### 13.2. Copia consistente: diagnóstico, cuotas y tratamiento propuesto

La instalación operativa sigue usando
`C:\Users\Rodd\.claude\local-control-center\platform.sqlite`, esquema **67**. No se ejecutó 58→67
como reparación. `backup_bundle` verificó quiescencia; `restore_bundle` creó destinos nuevos bajo
T/diagnosis y T/rollback. No se restauró encima de la base del usuario.

El esquema y `foreign_key_list(provider_limit_observations)` confirman:

- FK id **0** significa la restricción `limit_id → provider_limits.id`, no una fila padre número 0.
- `limit_id` admite NULL; `ON DELETE SET NULL`, `ON UPDATE NO ACTION`.
- Observación rowid 1: `provider-limit-observation-abbee835-9c68-4645-a4ec-6adbfe37c21a`;
  valor referido **`codex_cli:*`**, provider `codex_cli`, modelo `*`, tipo `rate_limited`.
- Timestamp `2026-07-23T20:41:50.351103+00:00`; retry a las `20:51:50.351103+00:00`.
  Metadata: `errorClass=demo_verification`, `retryAfterSeconds=600`,
  `retryAfterSource=legacy_seconds`, `statusCode=429`.

Sólo sobreviven tres padres (`nvidia_nim:*`, `openai_compatible:*`, `gemini:*`); ninguno justifica
reasociar Codex. No hay ventanas ni leases de cuota. La búsqueda acotada de procedencia en los
2.012 eventos del 23 de julio y en auditoría de ese día no encontró el ID ni `demo_verification`;
no había filas de auditoría en esa ventana. La búsqueda de ese marcador y de borrados del padre
en código/historial consultado tampoco recuperó una política. Es búsqueda limitada, no prueba
de que jamás existió un respaldo fuera de las rutas inspeccionadas.

La metadata sugiere origen diagnóstico; **no demuestra los límites que tenía el padre ni quién
lo retiró**. El código actual `QuotaManager.record_rate_limit` crea/actualiza el padre antes de
registrar una observación. No se atribuye el defecto a `foreign_keys=OFF` sin evidencia histórica.

Impacto observado: la FK afecta la asociación histórica. `QuotaManager.check/status` sobre la
copia devuelve para Codex `allowed=true`, `reason=no_limit`, `guarded=false`, `effectiveLimitId=null`.
La admisión consulta `provider_limits`, no utiliza la observación huérfana para reconstruir cuotas.
El cooldown anotado ya expiró, pero **se desconoce si se perdió además otra política vigente**.
`no_limit` es ausencia de guardia local, no cuota ilimitada del proveedor ni permiso de gasto.

| Verificación sobre copias | Estado | Resultado |
| --- | --- | --- |
| `integrity_check` | PASS | `ok`, en diagnóstico, segunda lectura y restauración. |
| `foreign_key_check` completo | FAIL | Una violación idéntica: `provider_limit_observations`, 1, `provider_limits`, 0. |
| Comportamiento actual de cuota | PASS | Se observó `no_limit` sin reservar consumo ni llamar al proveedor. No certifica una política correcta. |
| Segunda ejecución del diagnóstico | PASS | Igual contenido, conteos, cuota y violación; copia sin modificación. |
| Reconciliación e idempotencia de reparación | NOT_RUN | No hay respaldo probatorio para recuperar el padre o cambiar la asociación. |
| Restauración del respaldo | PASS | Nuevo destino, paridad lógica de todas las tablas y del defecto; no sólo igualdad del esquema. |
| Original sin cambios | PASS | SHA-256 `aadfc254b34e9f13309cd9d4f48a1530ce6e36e9cc28524a0cd4493f634f1dc2` antes/después. |

Propuesta auditable, **sin aplicar**: mantener la fila y ambas copias intactas, bloquear adopción y
conservar el ID original, la metadata y hashes en el expediente. Buscar un backup/export de la
política de esa fecha. Sólo esa evidencia puede justificar reconstruir el padre o corregir la
asociación sobre otra copia. Si no existe, acordar por separado un registro de procedencia/
cuarentena histórica y una política actual explícita del propietario. No poner NULL ni borrar la
fila sólo para obtener PASS; ese tratamiento perdería la relación original sin resolver qué cuota
correspondía. El diagnóstico termina exit 0, pero **la aceptación relacional continúa FAIL**.

`shared/db.py:32` habilita `PRAGMA foreign_keys=ON`. API y conexiones del worker utilizan ese
factory; las tres aperturas de copia verificaron valor 1. Esto prueba el contrato productivo actual
y su ejecución en copias, no el PRAGMA de una conexión histórica ya cerrada. Las conexiones de
backup/restauración y el almacén separado de credenciales tienen otro propósito; no se editó la
base original para probar una restricción. Evidencia: `C/copy-diagnosis.json`, `C/copy-verification.json`.

### 13.3. Readiness nativo y ciclo real bloqueado

Se usó el usuario nativo Rodd y el entorno heredado del launcher local del worker; no había un
worker operativo residente. `sameEnvironmentAsWorkerLauncher` en el recibo **no prueba aún**
el entorno aislado por ejecución que ProductOwner crea después. Esa verificación end-to-end y
los modelos efectivamente ejecutados siguen NOT_RUN. No se copiaron tokens manualmente ni se
modificaron ajustes globales.

Codex: `C:\Users\Rodd\AppData\Local\Programs\OpenAI\Codex\bin\codex.EXE`, **0.149.0**.
`login status` exit 0, autenticación ChatGPT. Help/version/exec-help: exit 0, ninguna capacidad
requerida ausente. SHA del binario
`14b7e6b2356e82d1d9275579eaa588757b4e0a501b65dcc19fccdf77bd83dc00`;
contrato `fefa6ddf5a9b7750db0a1592bd2309670763f6e503ad849b81bad5a98cf8b53d`.
El probe oficial de AIDO actualizó únicamente C/readiness.sqlite; estado `validation_required`,
motivo `validated_smoke_missing`. La base operativa conserva cero capability probes y cero smoke
receipts; no se marcó healthy mediante SQLite ni se cambió un adaptador por diferencia de versión.

La configuración de usuario selecciona `openai`, sin endpoint alternativo ni env-key en ese
provider. Las variables de API key, bearer token, gateway y cloud-provider inspeccionadas estaban
ausentes; se registró sólo presencia, nunca valores. El modelo configurado globalmente no es
evidencia del modelo que usaría AIDO. ProductOwner usa configuración mínima aislada y omite la
configuración global según su contrato existente.

La consulta de uso de **Codex Desktop** mostró 92% consumido/8% restante del período semanal y
balance adicional 0. No se verificó identidad compartida de esa cuenta con el CLI: no trasladar
ese porcentaje como cuota garantizada del CLI ni como capacidad suficiente para dos ejecuciones.
No se compraron ni canjearon créditos, no se activó extra usage ni fallback.
La distinción acceso por suscripción/API está documentada por
[OpenAI](https://learn.chatgpt.com/docs/auth).

Claude: `C:\Users\Rodd\.local\bin\claude.EXE`, **2.1.218**; help exit 0. Estado nativo
`auth status --json`: exit 1, `loggedIn=false`, `authMethod=none`. Suscripción y consumo efectivo:
desconocidos. Intervención humana exacta: ejecutar
`& 'C:\Users\Rodd\.local\bin\claude.exe' auth login` como Rodd y elegir la cuenta de suscripción
ya existente, **no Console/API key ni gateway**. Después se debe revalidar el estado nativo y el
consumo incluido, sin habilitar cargos. La precedencia de credenciales y el login están descritos
en la [documentación de Claude Code](https://code.claude.com/docs/en/iam).

Bloqueo específico adicional: `codex_smoke.py` exige workspace, motivo y `approved=true` humano;
ejecuta una llamada real con marcador `AIDO_READ_ONLY_SMOKE_OK`. No es el plan ni la implementación.
La ruta de AIDO es `POST /api/v1/model-gateway/cli-runtimes/codex_cli/compatibility/smoke`.
La autorización de este encargo reserva **dos** llamadas a plan e implementación; este smoke
previo sería una tercera. Se necesita consentimiento específico para ese smoke adicional y su
aprobación humana en AIDO antes de continuar; no se autoasignó aprobación ni se eliminó el gate.

Por estas precondiciones no se inició el panel para generar una solicitud que disparase inferencia.
Solicitud, plan, aprobación, worktree, implementación, QA, revisión y evidencia del ciclo real:
**NOT_RUN**. IDs de thread/ejecución/job/workspace y runtime/modelo efectivos: no existen para
este ciclo. Diff real y hashes antes/después de Claude: NOT_RUN. Cero llamadas Codex y cero
Claude, sin reintentos, sustituciones ni llamadas directas fuera de AIDO. El límite autorizado
queda sin consumir. Evidencia: `C/native-readiness.json`, `C/access-assessment.json`,
`C/receipt-details.json` y help nativo conservado sin salida privada de autenticación.

### 13.4. Verificación nueva, gate completo y coexistencia independiente

Regresión nueva mediante **CLI público**, sin wrapper privado:

```powershell
uv run python -m local_control_center.quality --tier fast --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_host_resource_governor.py --python-test tests_py/test_process_supervision.py --python-test tests_py/test_operational_acceptance_native.py
```

Exit exterior **0**; 51 PASS, 1 skip del soak opt-in, tres warnings SWIG de deprecación
(`SwigPyPacked`, `SwigPyObject`, `swigvarlink` sin `__module__`). Python 58,74 s; runner 124,522 s.
Diff, Ruff y todos los scans de secretos del plan finalizaron exit 0; todos los pasos registraron
cero descendientes restantes. Recibo: `quality-fast-2927c8ef98d74c8c9d81f9951efde683.json` en la raíz
de evidencia. No se volvieron a correr 30 ciclos ni la suite completa para repetir el hardening.
Las pruebas Git y los cinco skips por viewport conservan su evidencia histórica de la sección 9;
el manifiesto confirma que sus fuentes no cambiaron desde el inicio de esta continuación.

Advertencia no ocultada: el monitor de este runner registró pico **CPU del host 95,9%**, 61 muestras,
mínimo RAM 28.713.848.832 bytes. La admisión esperó temporalmente CPU antes de uno de los scans de
secretos; sólo lo inició cuando se admitió, dentro de su espera acotada. Suma de CPU de árboles
administrados: 44,609375 s; media normalizada sobre 20 procesadores/124,522 s: **1,79%**.
Esa media no atribuye el pico: faltan muestras por PID simultáneas y su duración exacta. No se
concluye que AIDO causó o no causó el pico. Sigue vigente la limitación equivalente del 98,8%
histórico. No se redujeron controles ni se realizó una carga de saturación deliberada.

Snapshot de admisión del candidato a `2026-09-05T15:32:11.404Z`:

- RAM disponible: **29.630.984.192 bytes / 27,596 GiB**; CPU del host 12,1%.
- Reserva `build_heavy`: **17.179.869.184 bytes / 16 GiB**; mínimo libre adicional: **16 GiB**.
- Total mínimo: **34.359.738.368 bytes / 32 GiB**; déficit: **4.728.754.176 bytes / 4,404 GiB**.
- Cero reservas activas y cero managed processes sin finalizar/liberar en la DB operativa,
  C/quality.sqlite, C/readiness.sqlite y F/quality.sqlite. No se recuperó ningún lease.
- Sonda: cero procesos AIDO activos, Unreal ausente. No se cerró ninguna aplicación ajena.

`C/gate-final-admission.json` conserva los argv completos, controles, deadlines y cada caso del
plan PR y release existente, sin omitir pasos. Ambos gates completos: **BLOCKED**; cada paso
interno: NOT_RUN; exit exterior y por paso: **null**, porque no se invocó un comando denegado.
Comandos pendientes, en orden y sólo si se admite capacidad:

```powershell
uv run python -m local_control_center.quality --tier pr --db-path .tmp/operational-hardening-p0/closure/quality.sqlite
uv run python -m local_control_center.quality --tier release --db-path .tmp/operational-hardening-p0/closure/quality.sqlite
```

No se bajaron umbrales, no se ejecutó fuera del supervisor ni se dejó un bucle de espera. El
commit documental final no cambia código/configuración/pruebas del candidato; tampoco convierte
los gates pendientes en PASS. Unreal conserva NOT_RUN con el protocolo de la sección 7 preparado.

### 13.5. Comandos de diagnóstico y cierre

| Comando | Exit | Evidencia / interpretación |
| --- | ---: | --- |
| `git status --short`, `git diff`, `git diff --cached --check`, cuatro `git commit -m ...` con rutas explícitas | 0 | Historial y `C/source-candidate.json`; hooks activos. |
| `uv run python T/inspect_copy.py` | 0 | C/source-start, host-start, gate-preview-start, copy-diagnosis; backup consistente nuevo. No aprobación de datos. |
| `uv run python T/verify_copy.py` | 0 | C/copy-verification: paridad PASS; FK FAIL; reparación NOT_RUN. |
| `uv run python T/native_readiness.py` | 0 | Diagnóstico emitido; probes Codex exit 0 y auth Claude exit 1, no se oculta dentro del exit del script. |
| `uv run python T/final_audit.py` | 0 | C/source-candidate, candidate-audit, gate-final-admission, access-assessment. Sin cambio productivo posterior a pruebas. |
| `uv run python T/receipt_details.py` | 0 | C/receipt-details: conteos de probes operativos, CPU y etapas reales NOT_RUN. |
| PR / release completos / ciclo real / Unreal / adopción original | — | BLOCKED o NOT_RUN según tabla vigente; sin exit ficticio. |

Errores de exploración conservados en la sesión: algunas consultas a rutas inexistentes emitieron
exit 1 antes de corregir la ruta; no fueron fallos de tests ni se usaron como evidencia de producto.
No se añadió código para ocultar warnings, reparar datos sin procedencia o eludir readiness.
El proceso de ejecución por pistas y verificación antes del cierre mantuvo separados resultados
diagnósticos, gates ejecutados y precondiciones pendientes. **Aceptación integral: BLOCKED**.
