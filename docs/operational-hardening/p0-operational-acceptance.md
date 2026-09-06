# Aceptación operacional P0 — 2026-09-05

**Estado vigente: §14.9 — diagnóstico estructurado, 2026-09-06.** Instrumentación y captura
sintética verificadas; smoke real y captura real FAIL. Causa del stack overflow histórico UNKNOWN.
Las tablas anteriores se conservan como historia, no como aceptación del candidato actual.

**Resultado global: BLOCKED.** Las regresiones locales indicadas pasan; no se certificó el ciclo
con Codex y Claude reales. La nueva copia reparada pasa integridad relacional, pero el smoke
operacional falló y la política histórica sigue sin resolver. La base original no fue reparada.
Este informe no declara «AIDO completamente funcional» ni autoriza adopción, merge o publicación.

Actualización del incidente del watchdog, **2026-09-06**: véase §14.7. Se conservan los resultados
anteriores; corregir el watchdog no convierte el nuevo smoke nativo fallido en PASS.

## Estado vigente del cierre autorizado — 2026-09-05, 17:20Z

Continuación desde `d464c571b51fbecb175706923707b2efa12d0a44`, encontrado exactamente como HEAD,
con árbol limpio. Código final: `df49ea6fc743bb363860d17f2cb0a2d02a473589`; cuatro commits locales
propios. La autorización ampliada comprende como máximo un CLI Codex por smoke, planificación e
implementación, y un CLI Claude para revisión; **no limita las peticiones internas del CLI**.
Las secciones 1–13 se conservan como historia. Evidencia nueva y límites: sección 14.

| Pista | Estado | Evidencia y motivo vigente |
| --- | --- | --- |
| Candidato preservado | PASS | Cuatro commits locales, manifiestos de fuentes/configuración/lockfiles y regresión del contenido final. No equivale a aceptación integral. |
| Smoke Codex | FAIL | La aprobación normal fue registrada y reclamada; el watchdog detuvo la API con `control_watch_failed`. Registro nativo incompleto, sin recibo validado. No se repite el CLI. |
| Planificación | BLOCKED | Codex conserva `validated_smoke_missing`. Ningún CLI de planificación iniciado. |
| Implementación | NOT_RUN | No existe plan completado ni su aprobación correspondiente. |
| QA del ciclo real | NOT_RUN | No existe implementación real que probar. Las regresiones de AIDO no sustituyen esta etapa. |
| Revisión Claude | NOT_RUN | No existe diff real del ciclo. La sesión nativa ahora está autenticada, pero no se ejecutó una revisión. |
| Integridad de la copia | PASS | Una asociación cambiada a NULL y un audit adicional; integridad/FK completas, idempotencia y rollback verificados. |
| Procedencia histórica | BLOCKED | ID y fila originales preservados; `historicalPolicyStatus=unresolved`. No se reconstruyó la política perdida. |
| PR completo | BLOCKED | 30,609 GiB disponibles frente a 32 GiB requeridos; déficit 1,391 GiB, cero reservas activas. Comando NOT_RUN. |
| Release | BLOCKED | PR no completado y misma condición de admisión. Comando NOT_RUN. |
| Coexistencia Unreal | NOT_RUN | Unreal no estaba abierto manualmente. No es prerrequisito de las otras pistas. |
| Adopción original | NOT_RUN | No autorizada; original sin cambios, sin cutover, push, merge ni publicación. |

Pendiente de limpieza: una copia **temporal** de autenticación creada por el aislamiento de AIDO
quedó después de la caída; la política de herramientas rechazó retirarla. Ruta exacta y acción
manual limitada en §14.5. No se leyó ni publicó su contenido.

## Cierre anterior conservado — 2026-09-05, 15:32Z

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

## 14. Continuación desde el candidato de entrega y autorización ampliada

Este apartado sustituye los estados vigentes anteriores, no sus recibos históricos. No se
repitió la auditoría general, el soak de 30 ciclos ni los escenarios históricos Windows/Git.
No se añadieron agentes, integraciones, P1/P2 ni capas operacionales.

Rutas de esta continuación:

- `N/` = `.tmp/operational-hardening-p0/authorized-close/` (evidencia privada, fuera de Git).
- `T/` = `C:\Users\Rodd\AppData\Local\Temp\aido-p0-authorized-c12ffc376b2d4e379c045821afa0a9fa/`.
- `F/` = `.tmp/operational-hardening-p0/` (recibos del runner público).
- DB de calidad: `.tmp/operational-hardening-p0/closure/quality.sqlite`.
- DB original: `C:\Users\Rodd\.claude\local-control-center\platform.sqlite`.

### 14.1. Candidato y trazabilidad del contenido final

El HEAD inicial fue exactamente `d464c571b51fbecb175706923707b2efa12d0a44`; no fue necesario
retroceder ni aislar cambios ajenos. Rama conservada: `codex/aido-operational-hardening-p0`.

| Commit local | Cambio propio y justificación |
| --- | --- |
| `5ba0cb41daab327863b2253b0aa7eaa34cad0ad2` | Reconciliación copy-only de la observación autorizada, auditoría atómica y cinco pruebas. |
| `27fea3ee1794f74c0d6d637807d1ce8f3254ac1a` | Conecta el smoke aprobado a la política interna acotada; no habilita shell genérico de `plan`. Prueba real del seam con transporte sustituido sólo en tests. |
| `ef0a817b91a7380f5068ace18208354b18e40b18` | Impide autoasignar la aprobación desde un payload HTTP; sólo el contexto interno puede presentar la aprobación verificada. |
| `df49ea6fc743bb363860d17f2cb0a2d02a473589` | Conserva el error original de un BEGIN fallido y no revierte una transacción del llamador; dos reproducciones deterministas. |

No se incorporaron bases, backups, `.tmp`, recibos privados ni credenciales. Los commits usaron
rutas explícitas, `git diff --cached --check` y hooks de Ruff/formato/gitleaks, todos exit 0.
El commit documental de cierre no cambia código productivo. El HEAD de entrega completo queda
en `N/final-traceability.json`; el HEAD de código probado es el indicado arriba.

`N/source-start.json`, `N/source-final-code.json`, `N/validation-source-before.json` y el manifiesto
final contienen hashes de fuentes, configuración y lockfiles, Python **3.13.15**, SQLite **3.53.1**
y versiones de dependencias. Hash productivo final:
`bcb2b2434c8004c80cff54568267b8a33f845565d0df889907c1da631cb38ce8`.
La regresión final se inició con el candidato ya en commits; se comparan sus fuentes antes/después
y los hashes de stdout/stderr contra los recibos. Los recibos históricos no certifican este HEAD.

### 14.2. Reparación auditada de una referencia, exclusivamente en copia

Se generó otro backup consistente con SQLite backup y se restauró en `N/repair-copy/platform.sqlite`.
La reparación rechaza la ruta original, la ruta operativa predeterminada y sus alias hardlink.
Se revalidaron esquema 67, `table_info`, `foreign_key_list` y `foreign_key_check` completo:

- Observación estable: `provider-limit-observation-abbee835-9c68-4645-a4ec-6adbfe37c21a`.
- `limit_id=codex_cli:*`, padre ausente; columna nullable y `ON DELETE SET NULL`.
- Provider/model: `codex_cli` / `*`. Metadata histórica `demo_verification`, 429, 600 segundos,
  conservada exactamente, incluido el texto original; no se usó rowid como identificación única.
- Una sola violación previa: tabla `provider_limit_observations`, rowid 1, parent `provider_limits`,
  fkid 0. fkid 0 identifica la restricción, **no una fila padre**.

La función `quality.maintenance.reconcile_p0_observation_copy` exige la fila íntegra esperada,
ausencia del padre, esquema compatible, copia quiescente y evidencia de autorización. En una
transacción sólo cambia `limit_id` a NULL y registra `data.p0.observation_reconciled` mediante
EventBus. Audit `audit-d0b89194-e09e-4d32-aaac-186ae9b5fcc8`, actor `codex:delegated-by-owner`,
fecha UTC, motivo, evidencia, fila completa, `originalLimitId=codex_cli:*`,
`historicalPolicyStatus=unresolved`, `currentQuotaPolicyChanged=false` y hash de la fila:
`4802621edde5967634cc1b6562bb9f1bf19758ab9da05c5db4f04dd043049b72`.
La metadata histórica no fue sobrescrita.

| Comprobación | Estado | Evidencia |
| --- | --- | --- |
| Integridad física y relacional | PASS | `integrity_check=['ok']`; `foreign_key_check=[]` completo, también bajo el código final. |
| Historia y conteos | PASS | Consulta por provider/model conserva la observación con NULL. Todas las tablas conservan hashes salvo observaciones y audit; sólo aumenta audit en una fila. |
| Idempotencia | PASS | Segunda aplicación devuelve `applied=false`, mismo audit, ningún cambio adicional; repetida sobre el código final. NULL sin audit coincidente es rechazo, no PASS. |
| Atomicidad | PASS | Trigger de prueba que impide insertar auditoría revierte también el cambio de asociación. |
| Rollback de la copia | PASS | Nueva restauración del backup, paridad lógica de todas las tablas y de la FK histórica; no sobrescribe la copia reparada. |
| Original sin cambios | PASS | SHA antes/después `aadfc254b34e9f13309cd9d4f48a1530ce6e36e9cc28524a0cd4493f634f1dc2`. |

Backup intacto y restauración: SHA
`0569dc8358a21851c90af317c72f258c8936f367798ba9e9f4bc8a07dcaded00`.
Evidencia: `N/repair-row-before.json`, `repair-before.json`, `repair-result.json`,
`repair-final-code-verification.json`, `repair-backup/`, `repair-copy/`, `repair-rollback/`.

**Cuotas actuales: sin cambio, no certificadas como política suficiente.** `QuotaManager.status`
da lo mismo antes/después: `no_limit`, `guarded=false`. Eso significa ausencia de límite local,
no cuota nativa ilimitada. No se habilitó ejecución en la copia reparada; sigue faltando una
política actual explícita para cualquier futura adopción. La procedencia de la política histórica
continúa BLOCKED/unresolved. Las conexiones productivas conservan `foreign_keys=ON`; no se atribuye
la corrupción histórica a un PRAGMA pasado que no está demostrado.

### 14.3. Runtimes, aprobación real del smoke y fallo operacional

Se confirmó que los ciclos anteriores constaban NOT_RUN: cero receipts smoke operativos y cero
ejecuciones de inferencia en el cierre anterior. No se trasladó esa cuenta cero al segundo intento
de esta continuación cuando apareció incertidumbre sobre el arranque nativo.

Codex **0.149.0**, binario
`C:\Users\Rodd\AppData\Local\Programs\OpenAI\Codex\bin\codex.EXE`:
`login status` exit 0, sesión ChatGPT; probes de capacidades sin flags faltantes. Se comprobó además
login en el entorno mínimo generado por el aislador productivo, con CODEX_HOME distinto y sin
API key. No se extrajeron ni copiaron tokens manualmente: el manager existente aisló la sesión.
No se modificó el entorno global del usuario. Binario y contrato conservan respectivamente los
SHA `14b7e6b2356e82d1d9275579eaa588757b4e0a501b65dcc19fccdf77bd83dc00` y
`fefa6ddf5a9b7750db0a1592bd2309670763f6e503ad849b81bad5a98cf8b53d`.

Claude **2.1.218**, binario `C:\Users\Rodd\.local\bin\claude.EXE`: ahora `auth status --json`
termina exit **0**, `loggedIn=true`, `authMethod=claude.ai`, `apiProvider=firstParty`,
`subscriptionType=max`. Help exit 0, flags de contrato read-only presentes. **No requiere login
humano ahora.** No se ejecutó revisión ni se comprobó su entorno efectivo de inferencia. Cuota
incluida restante: desconocida. Autenticación no demuestra saldo suficiente ni revisión aprobada.

Se utilizó `N/cycle.sqlite`, nueva e independiente de la FK histórica, y un repositorio desechable
con commit inicial real `b25145a20f076b57efa0f1953d0588dced3c90ac`, sin remotos, secretos ni juego.
Proyecto `project-ddc93024-abbf-46df-b3ec-79ebee8d5678`; worktree real
`workspace-959a2130-2779-4cd5-ba38-f6d1620153c5`. Registro delegado de la autorización del propietario:
`audit-2b2e430c-c339-4180-a718-aaca5b1019a9`; no se afirmó una identidad humana inventada.

Las solicitudes de smoke pasaron por HTTP autenticado de AIDO y su worker separado:
`POST /api/v1/model-gateway/cli-runtimes/codex_cli/compatibility/smoke`, workspace explícito,
`approved=true`, motivo exacto **«Validación operacional P0 autorizada por el propietario»**.
El worker permaneció pausado y recibió un único `POST /api/v1/workers/run-once` por intento.
No se lanzaron CLI de IA directamente desde el controlador de aceptación.

1. Job/ejecución `job-d367e005-2328-4c96-9005-380f9de96ae6`: HTTP 202, resultado interno failed,
   CLI session blocked con `Plan profile cannot execute shell commands.`. `managedProcessId=null`.
   Se verificó que el único proceso `agent_cli` era el wrapper Python del dispatcher por el hash
   de su argv exacto: no una ejecución Codex. Este bloqueo previo al lanzamiento no consumió un CLI.
2. Tras reproducir y corregir ese seam, job/ejecución
   `job-30cdf7fd-f5e7-49b2-8ce4-cd35cdca46b1`: HTTP 202 y run-once 202. Aprobación durable
   `audit-5cae9df5-d9da-4ce6-b28f-f76a156083ee`, reclamada por
   `audit-fafa80e0-4014-4301-85d2-dfbd9f9c84db`, ligada a workspace, binario y comando.
   A `16:56:24Z` el supervisor de la API registró `OperationalError`, SQLite code 1,
   `control_watch_failed`, y terminó su árbol. El cliente obtuvo conexión rechazada; script exit 1.
   El registro reservado para el CLI `managed-process-c1187c20-5ef7-41da-a719-0c8ef7a73b9c`
   quedó con root PID/create time cero. **Eso no prueba que ningún CLI alcanzara a arrancar.**

Resultado conservador: **smoke FAIL**, cero receipts validados, hasta **un arranque posible**,
ningún reintento CLI permitido por el controlador. Modelo solicitado `gpt-5.6-terra`, obtenido del
catálogo nativo; **modelo efectivamente atendido, thread ID nativo, inferencia y consumo desconocidos**.
No hay marker completado ni salida nativa verificable. El workspace conserva sus hashes antes/después.
Codex sigue `validation_required / validated_smoke_missing`, sin edición manual de healthy.

La corrección del seam conserva flags read-only/tool isolation, entorno mínimo, política de runtime,
admisión y supervisor. La aprobación se reclama una sola vez en SQLite. Un payload no puede
autoasignarla; el ToolBroker genérico rechaza la operación de smoke, y `plan` genérico continúa
sin shell. Los tests reemplazan sólo el transporte nativo y **no se presentan como un smoke real**.

Solicitud funcional desde el panel, planificación, aprobación del plan, implementación, QA de ese
diff y revisión Claude: no completados/no ejecutados. El worktree preparatorio y el HTTP smoke no
certifican ese recorrido. No hubo nuevas API keys, compras, extra usage, gateways, fallback ni
rework automático. No se convierte la sesión nativa de suscripción en prueba de cuota ilimitada.
Evidencia: `N/preflight.json`, `cycle-setup.json`, `smoke-http.json`, `smoke-diagnosis.json`,
`smoke-fixed-http.json`, `smoke-failure-final.json`, `claude-final-readiness.json`,
`disposable-no-remotes.json` y artefactos del dispatcher.

### 14.4. Regresiones y diagnóstico SQLite sin ocultar FAIL

Dos tests deterministas reprodujeron un defecto del helper transaccional: si `BEGIN IMMEDIATE`
falla por SQLITE_BUSY, el ROLLBACK incondicional sustituye el error por
`cannot rollback - no transaction is active` (code 1). Un BEGIN anidado también revertía trabajo
del llamador. Se corrigió el helper, no el watchdog ni sus controles. **El logger del incidente
no conservó el error causal completo: esta reproducción es compatible con el síntoma, no prueba
que SQLITE_BUSY haya sido la causa única del incidente real.** No se afirma que el ciclo quedó
reparado ni se relanzó para probarlo fuera del permiso.

| Runner público / recibo en F/ | Exit exterior | Resultado |
| --- | ---: | --- |
| `quality-fast-a39fc2e51d164a7eb820042ca58754d9.json` | 1 | Rojo inicial de reparación: cinco tests sin implementación. |
| `quality-fast-e08ff9e37c6048ec95a5b67a9c66ab12.json` | 0 | Cinco pruebas de reparación aprobadas. |
| `quality-fast-932f0ef47d374f5293164710174940a3.json` | 1 | Reproducción del seam: un fallo, un PASS; CLI autorizado no alcanzaba transporte. |
| `quality-fast-a815aab085c0410e8fbc8f0016869309.json` | 0 | 159 pruebas focalizadas, incluidos broker, ProductOwner y gateway. |
| `quality-fast-4845eec33a4b4efca0c160f9229d24dd.json` | 1 | Un fallo adicional: aprobación autoasignada al evaluador. |
| `quality-fast-f705db85a7df4b8285bec656c21e8139.json` | 1 | Dos dobles de prueba rechazaron el keyword nuevo; se preservó el contrato de llamada genérico. |
| `quality-fast-250f2a8304fb4ad4859aa97ee0911461.json` | 0 | 131 pruebas tras la corrección de la frontera interna. |
| `quality-fast-941185b390804aff9237692dec45a9d8.json` | 1 | Dos reproducciones del manejo incorrecto de BEGIN. |
| `quality-fast-650599812dfc402cb86f4d5f73f9e5b8.json` | 0 | 81 pruebas afectadas tras corregir el helper transaccional. |
| `quality-fast-8abe93fec60f4888a1188a95bcb3b15b.json` | 0 | Revalidación del candidato ya en commits: 81 PASS en 42,96 s, sin skips; todos los pasos del plan exit 0 y sin descendientes pendientes. |

Comando de esa última revalidación:

```powershell
uv run python -m local_control_center.quality --tier fast --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_transaction_begin_failure.py --python-test tests_py/test_p0_copy_reconciliation.py --python-test tests_py/test_host_resource_governor.py --python-test tests_py/test_process_supervision.py --python-test tests_py/test_codex_smoke_policy_seam.py --python-test tests_py/test_codex_capability_contract.py
```

Se conservan tres warnings SWIG de deprecación: `SwigPyPacked`, `SwigPyObject`, `swigvarlink`
sin `__module__`. No se silenciaron; no equivalen a fallo funcional ni desaparecieron. Los cinco
skips históricos de viewport no se reetiquetaron y no se volvió a ejecutar el soak opt-in.

Los scripts `uv run python T/repair_copy.py`, `smoke_failure.py`, `final_snapshot.py` terminaron
exit 0 al emitir/verificar sus evidencias; **ese exit no convierte el smoke fallido, la política
histórica pendiente ni el gate denegado en PASS**. Los scripts de smoke terminaron exit 1: el
primero tuvo además un error de serialización de cleanup, corregido en el controlador siguiente;
el segundo perdió la API. Los errores de preparación (ruta/import, comparación textual de JSON,
serialización y selección `archivo::test` no admitida por el runner) se corrigieron sin relanzar
inferencia y no se cuentan como tests exitosos ni se borran de la sesión.

### 14.5. Recuperación del intento y limpieza pendiente

Se verificaron identidades OS de owners y raíces conocidas como inexistentes antes de recuperar
sus registros mediante `recover_managed_processes`, sólo en `N/cycle.sqlite`. Se solicitó
cancelación normal del job para impedir cualquier recuperación con reintento: job `cancelled`,
operación `cancel_requested`, sin fabricar un resultado completado. Cero procesos administrados
pendientes, cero reservas activas y ningún proceso Codex/Claude creado desde el inicio del intento
seguía presente. Los procesos del usuario, anteriores al intento, no se tocaron.

La recuperación conserva la incertidumbre del registro root PID 0; no demuestra una finalización
normal del CLI ni un consumo cero. Evidencia: `N/owned-harness-recovery.json`,
`smoke-fixed-service-cleanup.json`, `smoke-failure-final.json`.

El aislador de AIDO dejó una carpeta privada creada a `16:56:19Z`, dentro del intervalo de este
intento, con sólo su copia de `auth.json`. Se inspeccionaron nombre/tamaño/fecha, **no el contenido**:

```text
C:\Users\Rodd\AppData\Local\AIDO\product-owner-codex-homes\89d4c072-1f4d-451b-97f7-062ce80412a5
```

La herramienta rechazó la limpieza de esta ruta exacta por política; no hubo exit code de shell
ni se intentó evadir el rechazo mediante otra herramienta. **Limpieza BLOCKED**. Intervención
manual exacta: retirar únicamente esa carpeta temporal de este intento. No eliminar ni alterar
`C:\Users\Rodd\.codex\auth.json`, que es la credencial original. No se requiere nuevo login para
Claude ni se solicita de nuevo el consentimiento del smoke.

### 14.6. PR, release y Unreal independientes

Snapshot `2026-09-05T17:07:58Z`, `N/gate-final-snapshot.json`:

- RAM disponible **32.884.985.856 bytes / 30,626530 GiB**.
- Reserva `build_heavy` **16 GiB** más mínimo libre **16 GiB**: **32 GiB** requeridos.
- Déficit **1.474.752.512 bytes / 1,373470 GiB**; cero reservas activas.
- Preview `resource_wait`, `aggregate_memory_budget`; es una condición de admisión, no RAM
  medida del build. No se buscaron indefinidamente leases ni se cerraron aplicaciones ajenas.
- Unreal ausente: **NOT_RUN**. No se abrió, controló ni modificó el editor o el juego.

PR y release completos conservan todos sus pasos, argv y deadlines en el recibo del snapshot.
No se invocaron por falta de capacidad: **exit exterior null, pasos NOT_RUN**, no un falso exit 0.
Secuencia autorizada pendiente, sólo si se admite y PR termina correctamente:

```powershell
uv run python -m local_control_center.quality --tier pr --db-path .tmp/operational-hardening-p0/closure/quality.sqlite
uv run python -m local_control_center.quality --tier release --db-path .tmp/operational-hardening-p0/closure/quality.sqlite
```

No se bajaron reservas, CPU, memoria, seguridad ni cuotas; no se ejecutaron gates fuera del
supervisor. No se usaron gates históricos como aprobación del código nuevo. La aceptación
integral continúa **BLOCKED**, con smoke **FAIL**, aun cuando la reparación de la copia y las
regresiones focalizadas son **PASS**. No hubo adopción original, push, merge ni publicación.

Comprobación de entrega posterior a las pruebas, `2026-09-05T17:20:16.271Z`, conservada aparte
en `N/gate-delivery-snapshot.json`: disponibles **32.866.168.832 bytes / 30,609 GiB**,
requeridos **34.359.738.368 bytes / 32 GiB**, déficit **1.493.569.536 bytes / 1,391 GiB**.
Cero reservas activas; misma denegación `aggregate_memory_budget`; Unreal ausente. No se dejó
ningún bucle de espera ni se ejecutó PR/release contra esa denegación.

### 14.7. Incidente del watchdog — 2026-09-06

Alcance limitado al incidente, desde `d7c92592104a7bcbb07a8d66b9db32743adeb60a`, rama
`codex/aido-cleanup-post-p0`, inicialmente limpia. Sin reset, cambio de rama, limpieza adicional,
P1/P2, nuevas integraciones, adopción ni modificaciones de la base operativa original.

#### Candidato y estados separados

| Pista | Estado | Evidencia / limitación |
| --- | --- | --- |
| Candidato en commits locales | PASS | `d5434b87da78de70ea9d171ffce28f724194dc33`: watchdog/identidad/entornos; `7160d579355b6228292495d88016f55b9ec1694a`: integración HTTP y perfil QA. |
| Contención, fencing, identidad y recuperación sintética | PASS | 184 pruebas: 124 + 3 HTTP nativas + 57 regresiones adicionales, sin skips. |
| Residuo sensible anterior, §14.5 | PASS | La ruta exacta ya no existe. Sólo se consultó existencia; no se leyó auth ni se intentó borrar/eludir el rechazo anterior. No se atribuye quién la retiró. |
| Auth/capacidades Codex | PASS | 0.149.0; probes nativos y entorno aislado exit 0, sesión ChatGPT, sin API key ni flags faltantes. No prueba cuota ni ejecución. |
| Único smoke adicional desde AIDO | FAIL | CLI nativo exit `3221225725 / 0xC00000FD`, desbordamiento de pila. Sin reintento. Sigue `validation_required / validated_smoke_missing`. |
| Instalación frontend aislada, typecheck y build | BLOCKED | `build_heavy`: déficit 540.205.056 bytes. No se sustituyeron por herramientas heredadas. |
| PR completo | BLOCKED | `aggregate_memory_budget`; comando/gates NOT_RUN, exit exterior null. Las pruebas focalizadas no sustituyen PR. |
| Release | NOT_RUN | PR no ejecutado/aprobado; misma falta de capacidad. |
| Coexistencia Unreal | NOT_RUN | Snapshot sin editor abierto; no se abrió/controló Unreal. |
| Adopción original | NOT_RUN | No autorizada ni ejecutada; no se repitió reparación de datos. |

SHA-256 productivo probado: `e3c90fda98c446a351b742912a1f6e12bd0ff51276ffa2743019aa75d9e57c8a`.
`N/watchdog-final-traceability.json` conserva fuentes, configuración, lockfiles, versiones, commits,
receipts y hashes. Esta documentación es posterior al código `7160d579`; se verifica que sólo
cambia documentación y no el contenido productivo probado.

#### Causa reproducida y corrección

Una conexión independiente retiene `BEGIN IMMEDIATE`. El watchdog abre su propia conexión en
autocommit e intenta `HostResourceGovernor.heartbeat` → `BEGIN IMMEDIATE` para renovar la reserva.
Ese BEGIN devuelve **SQLITE_BUSY (5)**; la conexión del watchdog **no adquirió una transacción**.
Antes, cualquier excepción causaba `control_watch_failed` y terminaba su Job Object: si el objeto
contenía la API, desaparecía el servidor HTTP, no sólo el CLI.

Esta reproducción prueba un defecto actual; **no demuestra la causa original de §14.3**, cuyo
logger sólo conservó SQLite code 1. Se mantienen las correcciones del helper: un BEGIN fallido no
revierte al llamador ni se sustituye por un rollback sin transacción propia.

- Sólo SQLITE_BUSY del heartbeat idempotente admite una ventana de **2 s**. Cada iteración relee
  cancelación, fencing y vigencia de la reserva. No se repite el comando externo. El timeout de
  250 ms también rige al abrir la conexión del watchdog.
- Agotar la ventana termina únicamente su árbol con `control_watch_deadline_exceeded`. Perder
  liderazgo termina ese árbol con `leadership_fence_lost` **antes** de persistir cancelación:
  un escritor SQLite no puede demorar la contención ni sustituir ese motivo.
- Logging existente, fuera de la transacción fallida: excepción causal redactada, código/nombre
  SQLite, fase, acción, managed process, ejecución, lease, worker y fence. Sin SQL, argumentos
  privados ni entorno completo. Otros fallos de supervisión siguen cerrando de forma segura.
- Windows crea/asigna/reanuda el proceso antes de persistir PID/create-time. El ensayo mata al
  owner en ese intervalo y prueba un arranque real con `root_pid=0` durable. Ahora se conserva
  UNKNOWN, registro y reserva pendientes, impidiendo replay automático. Si el handle sigue
  disponible y falla la persistencia, se termina el árbol y se registra la causa en fase
  `identity_persist` (`SQLITE_CONSTRAINT_TRIGGER` en la inyección determinista).

`test_watchdog_http_pipeline.py` usa HTTP loopback real, API en proceso separado, worker,
dispatcher, sandbox y watchdog nativos. Sólo el ejecutable IA se sustituye por el binario
compilado de `tests_py/fixtures/watchdog/codex.cs`, sin red ni lectura de credenciales. Sus receipts
están sólo en DBs de tests: **no certifican Codex real**. Ambos escritores desaparecen tras
cancelación/fencing. El estado HTTP durante contención respondió en **513,867 ms**, bajo el límite
de 1 s; API disponible después de liberar el escritor. IDs OS, Job Object de la API y resultados
durables: `N/watchdog-http-*.json`.

El primer recorrido multiproceso agotó `qa_light` de 4 GiB: `MemoryError`, exit interno 3,
peak nativo **4.429.447.168 bytes**. Una prueba reprodujo la clasificación incorrecta. Sólo ese
archivo se separó al perfil existente **agent_cli, 8 GiB / CPU 40% / 16 procesos**, sin inferencia.
Las regresiones pequeñas siguen **qa_light, 4 GiB / CPU 25% / 8 procesos**; el mínimo del host sigue
16 GiB. PR conserva todos sus casos y perfiles.

Con `auth.json` sintético se probaron salida normal, cancelación nativa, muerte abrupta del owner
y limpieza rechazada. El aislador existente registra propiedad/identidad en `audit_events` antes
de copiar auth. La recuperación existente sólo retira hogares propios de owners muertos, sin
árboles pendientes de reconciliar; rechaza junctions/symlinks y conserva hogares de owners vivos.
Un rechazo queda auditado. No hay nuevo servicio de limpieza ni gestor de credenciales.

#### Pruebas, comandos y errores conservados

Receipts en `F/ = .tmp/operational-hardening-p0/`, prefijo `quality-fast-`, sufijo `.json`:

| Receipt | Exit exterior | Resultado |
| --- | ---: | --- |
| `0fd3d8a1bac04a3dba5805e82a065c33` | 1 | Imports; pruebas aún no ejecutadas. |
| `37e8c385b57147319e0f174b5090a80c` | null | Sesión interrumpida, sin resultado terminal. Owner/raíz propios muertos; recuperación normal liberó esa reserva. No PASS. |
| `74b484b13af2451787746f575eba9f3e` | 1 | Tres fallos nativos reproducidos. |
| `ca165da92cd745e396d09f734ff33bdf` | 0 | 35 pruebas después del fix del watchdog. |
| `d015deac122b40129776ef8a21351d87` | 1 | Dos fallos de identidad/diagnóstico reproducidos. |
| `7f65c265088d414eba74639e0d180717` | 1 | Identidad 2 PASS; fixture C# falló por separadores de ruta, corregidos. |
| `36a6d15ef0ab4923ab8ac9e6abe147b4` | 1 | Python interno exit 3/MemoryError; no PASS del recorrido. |
| `ce7969fff1e04e2592fdc4d6e97ee62c` | 1 | Clasificación y dos fallos de auth temporal; 13 PASS. |
| `65de4dff21664086a16ccd3a37a0eee0` | 1 | Docstring D401, corregido; pruebas no ejecutadas. |
| `0e43b4ce7ef64292b79967ef2717777d` | 0 | 16 pruebas + 3 HTTP nativas. |
| `7787fc286a2644e0930205e4f6f2d7b4` | 0 | **124 + 3**, contenido final; lint/secretos exit 0. |
| `bfd2530e6d2c494da120657dc285eb7d` | 0 | **57** regresiones adicionales, candidato en commits. |

Comandos del contenido final, sin retirar casos:

```powershell
uv run python -m local_control_center.quality --tier fast --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_watchdog_contention.py --python-test tests_py/test_watchdog_identity.py --python-test tests_py/test_watchdog_temporary_auth.py --python-test tests_py/test_watchdog_http_pipeline.py --python-test tests_py/test_process_supervision.py --python-test tests_py/test_transaction_begin_failure.py --python-test tests_py/test_host_resource_governor.py --python-test tests_py/test_codex_smoke_policy_seam.py --python-test tests_py/test_codex_capability_contract.py --python-test tests_py/test_worker_leadership.py --python-test tests_py/test_quality_tiers.py --python-test tests_py/test_product_owner_agent_real_runtime.py
uv run python -m local_control_center.quality --tier fast --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_operational_recovery.py --python-test tests_py/test_durable_executions.py --python-test tests_py/test_local_worker_runtime.py --python-test tests_py/test_operational_http_boundaries.py --python-test tests_py/test_operational_hardening_p0.py
```

Persisten tres deprecaciones SWIG (`SwigPyPacked`, `SwigPyObject`, `swigvarlink`), no silenciadas.
Warnings de contención inyectada y telemetry omitida son observaciones esperadas de esos ensayos,
no se ocultan ni se presentan como errores de inferencia.

#### Único smoke real: watchdog operativo, CLI nativo fallido

El controlador tuvo primero un `UnicodeDecodeError` leyendo el catálogo con codificación Windows:
exit 1 **antes de job/aprobación/intent o CLI**. Se conserva `N/watchdog-smoke-preparation.json`.
Antes de continuar se cotejaron cero jobs/aprobaciones y cuatro procesos Git terminados por hash
de argv. Se corrigió la lectura a UTF-8; esa preparación no cuenta como inferencia ni PASS.

DB nueva `N/watchdog-smoke.sqlite`, repositorio desechable sin remotos, mismo usuario OS efectivo
de API/worker. Autorización delegada real `audit-108bc50d-60af-43f0-b736-66e0e3cc7016`, sin identidad
humana fabricada. Un POST smoke autenticado y un run-once, ambos HTTP 202; ninguna otra fase IA.

- Job/ejecución: `job-81bdebda-a4c7-46c8-82f2-12bc3244c9b1`.
- Aprobación smoke: `audit-244629fc-c162-4eb4-9c48-74091bb35f04`.
- CLI: `managed-process-96adad03-f3a9-42dc-917b-e19d31570999`, PID **65828**, identidad y cierre
  persistidos. Inicio `06:53:09.947Z`, fin `06:53:18.706Z`; sin timeout/cancelación del watchdog,
  cero descendientes restantes. Dispatcher exit 0.
- Codex **0.149.0**, mismos hashes binario/contrato de §14.3. Solicitado `gpt-5.6-terra`;
  **modelo servido, thread nativo, inferencia, consumo y cuota restante UNKNOWN**. `no_limit` local
  no significa cuota ilimitada. Sin nuevas keys, API pagada, fallback ni extra usage.
- Exit nativo **3221225725 (`0xC00000FD`)**. Stderr: `thread 'tokio-rt-worker' ... has overflowed its stack`.
  SHA stderr `ef89952dc0184d9060c161f1add350766588efb7efaec9221edd96ca4950264b`;
  stdout vacío. La causa interna del desbordamiento **no está establecida**. Pista detenida:
  no se relanzó Codex ni se modificó el adaptador para sortearlo.
- La API atravesó dos SQLITE_BUSY de heartbeat con `action=retry_heartbeat` y respondió hasta
  el cierre deliberado. No hubo `control_watch_failed` que la terminara.
- La operación HTTP quedó `completed` porque devolvió el resultado; **el smoke es `failed`**.
  `reason=null` del envelope no borra el exit ni el stderr causal. No se marcó healthy manualmente.
- README conserva su hash. El manifiesto completo sí cambió: AIDO escribió un
  `.tmp/evidence-artifacts/*.runtime.json` mediante `CliSessionStore`. Se conserva
  `workspaceUnchanged=false`: no se oculta el archivo ni se atribuye a una escritura de Codex.

`uv run --no-sync python T/watchdog_smoke.py`: exit **1**. Los scripts `watchdog_preflight.py`,
`watchdog_resources.py` y `watchdog_trace.py` terminaron exit 0 como comprobaciones, **no como
aprobación del smoke ni de gates pendientes**. `T/` es el directorio temporal autorizado ya usado.
Sus hashes y los receipts están en `N/watchdog-final-traceability.json`. Se verificaron 27 artefactos,
cero registros/procesos propios activos, cero reservas y ausencia de hogares temporales de este
preflight/CLI. El historial y consumo UNKNOWN del intento anterior siguen intactos.

#### Dependencias y capacidad de entrega

JSON válido y ambas dependencias retiradas ausentes de todas las secciones: Node exit 0. Diff
autorizado: **1 línea menos en package.json, 20 menos en pnpm-lock.yaml**. Biome incluye sólo el
frontend; su gate PR apunta a `local-control-center/web`. Se conserva la exclusión del manifiesto;
no se usó `--no-errors-on-unmatched` ni se registró PASS por un archivo no procesado. La validación
JSON es separada; véase también `docs/cleanup/post-p0-cleanup.md`.

Snapshot **2026-09-06T06:58:11.531Z**: disponibles **33.819.533.312 bytes / 31,497 GiB**;
`build_heavy` **16 GiB** + mínimo del host **16 GiB**, requeridos **34.359.738.368 bytes / 32 GiB**;
déficit **540.205.056 bytes / 0,503 GiB**, **cero reservas activas**, `aggregate_memory_budget`.
Instalación congelada aislada/typecheck/build, PR completo y release no se lanzaron fuera de
admisión ni se sustituyeron por herramientas activas. No se mantuvo espera indefinida ni se cerraron
procesos ajenos. No se reconstruyeron node_modules/venv activos. Sin push, merge, publicación ni cutover.

### 14.8 Comparación del crash nativo — 2026-09-06

Base verificada: `439d0d624b54fe6cf8016b927ff35c0b4b33b7fb`, rama
`codex/aido-cleanup-post-p0`, limpia. Corrección propia preservada en
`f0a54a4a4cae08944324138c7e302aba25a740fc`; código probado y usado en el smoke,
SHA agregado productivo `e3777445f2cecaed6d2174da5db6f061eab225a9caaf2c6e30013a90babc39a1`.
El commit documental de esta sección no cambia ese contenido. Manifiestos, lockfiles,
versiones y HEAD de entrega: `N/native-candidate-final-traceability.json`.

| Pista | Estado | Evidencia / limitación |
| --- | --- | --- |
| 0.149.0 histórico | **FAIL** conservado | Mismo binario, argv reconstruido cuyo hash coincide con el registro nativo; WER confirma PID y creation time. Consumo UNKNOWN. |
| Distribución 0.153.4 y probes | **PASS** | Paquete oficial completo x64; checksums coincidentes; versión, help y auth nativa/aislada exit 0. Esto no prueba compatibilidad operacional. |
| Primer smoke 0.153.4 | **FAIL** | Exit nativo `3221225725 / 0xC00000FD`, mismo desbordamiento de pila. Recibo failed, no healthy manual. |
| API y workspace | **PASS** acotado | HTTP 200 después del fallo; 53 consultas, máximo 203,52 ms. Manifiesto completo antes/después idéntico, sin ignorar `.tmp` ni `.git`. |
| Causa interna / segunda ejecución | **BLOCKED / NOT_RUN** | Sin pila nativa ni depurador disponible en las rutas comprobadas; no se gastó otra ejecución sin captura discriminante. |
| Regresión de evidencia y relacionadas | **PASS** | 80 pruebas, exit 0; tres deprecaciones SWIG conservadas. No equivalen a validar dependencias frontend. |
| Instalación frontend nueva / typecheck / build / PR | **BLOCKED / NOT_RUN** | Preview vigente sin capacidad `build_heavy`; no se usó node_modules activo ni se lanzó por fuera del supervisor. |
| Release | **NOT_RUN** | PR y validación de instalación pendientes. |
| Limpieza propia de ejecución | **PASS** | Cero raíces propias vivas, registros activos, reservas y hogares auth temporales tras reconciliación normal. |
| Selección operacional / datos originales | **PASS** preservación | 0.149.0 sin reemplazo; selección candidata sólo en DB nueva. Sin adopción, push, merge, cambios globales ni Unreal. |

**Hechos e hipótesis.** `N/native-crash-history.json` vincula el stderr completo histórico,
PID 65828/create-time `1788677589.9799292`, WER 1000 de `06:53:14.6859230Z`, excepción
`c00000fd` y offset `0xd5bf3e7`. Ambos binarios son PE `0x8664`; Windows build 26200.
El último observable es el mensaje de lectura de stdin seguido del stack overflow: stdout vacío
no demuestra consumo cero. Stdin era DEVNULL/EOF; stdout/stderr, pipes binarios drenados
concurrentemente; `shell=False`. Cwd, CODEX_HOME, argv y lease originales están en ese recibo.

Se contrastaron las fuentes oficiales de [0.152.0](https://github.com/openai/codex/releases/tag/rust-v0.152.0),
[#41840](https://github.com/openai/codex/pull/41840) y [#41853](https://github.com/openai/codex/pull/41853).
La primera comparte la constante de 16 MiB y la aplica al hilo de revisión: **no aumenta** el presupuesto
Tokio de 16 MiB que ya aparece en su diff. La segunda encapsula en BoxFuture el arranque diferido.
Eran pistas, no una causa demostrada; el fallo de 0.153.4 refuta que esta actualización baste aquí.
No se añadieron ajustes de pila/heap, variables Rust, bypass ni modificaciones del watchdog.

**Distribución y contrato.** [Paquete oficial 0.153.4](https://github.com/openai/codex/releases/tag/rust-v0.153.4),
`codex-package-x86_64-pc-windows-msvc.tar.gz`, SHA-256
`a6ef3442cb12766a88b39311d79244289e4f9763e2c53ff4fbebc2cb653cc5f3`; extraído completo en
`%LOCALAPPDATA%/AIDO/test-runtimes/codex-0.153.4-x86_64`, con code-mode host, rg y auxiliares
de sandbox. CLI SHA `444a3f0008050605cae73cd9b7a2dcac61294062dfaab56dd20430fd6498518b`;
contrato inalterado `fefa6ddf5a9b7750db0a1592bd2309670763f6e503ad849b81bad5a98cf8b53d`.
Se verificaron help y esquema oficial: read-only, ignore-user-config/rules, strict-config,
ephemeral, never, desactivaciones y entorno aislado permanecen. Modelo explícito `gpt-5.6-terra`;
**effort no especificado** en el argv histórico ni en la comparación. El catálogo local anuncia
medium por defecto, pero no prueba el esfuerzo realmente servido: permanece UNKNOWN.

**Una ejecución de dos autorizadas.** DB `N/native-candidate-smoke.sqlite`, repositorio desechable
sin remotos, workspace `workspace-f3d77c98-d529-48c5-a034-3c4cf2a68394`. Autorización delegada
`audit-6a3f14a3-db13-49d0-95e9-0debdd506977`; aprobación normal ligada al comando/binario
`audit-8a401d54-1f70-4fa7-bb2f-968237d00889`. HTTP smoke y worker run-once: 202, una vez cada uno.
Ruta productiva HTTP/worker/dispatcher/política de smoke aprobada/sandbox restringido/supervisor;
el seam de aprobación interno existente no se sustituyó por un CLI directo ni por mocks.

- Job `job-89a56a86-1584-482e-a3c3-d6f0ac24f6de`; CLI
  `managed-process-1e7d2e55-4002-491c-9e79-691309c5a3ed`, PID **46292**,
  create-time `1788682787.5445359`. Se leyó argv de ese PID propio y coincidió su hash durable.
- Inicio `08:19:47.518Z`, cierre `08:19:55.307Z`; exit **3221225725**. WER confirma identidad,
  excepción y offset `0xd3eecc7`. Stderr completo: lectura de stdin y
  `thread 'tokio-rt-worker' (22940) has overflowed its stack`; SHA
  `485ea25b6ab5d09532dca774d3cbf90e08093a1ca362099d45eeae5b501dcc24`.
- Lease `agent_cli`: **8 GiB / CPU 40% / 16 procesos**, compartida con dispatcher; supervisor
  asigna raíz suspendida al Job Object antes de reanudar. Peak nativo del CLI **15.110.144 bytes**,
  CPU **0,234375 s**, sin timeout/cancelación, cero descendientes. Límite CPU configurado;
  su readback no fue persistido separadamente y el Job Object anónimo cerrado no es consultable.
- La API siguió disponible; el único heartbeat BUSY observado se recuperó. `operation=completed`
  significa respuesta del handler, **no PASS del smoke**. Al cerrar el controlador quedó pendiente
  el registro del dispatcher: no se infiere exit 0. Owner y raíz ya muertos, con identidades conocidas,
  permitieron recuperar sólo `managed-process-c4210e26-64ff-4559-bf11-4384ff0f48e3` mediante
  `recover_managed_processes`; evidencia parcial conservada, sin replay ni procesos ajenos terminados.
- Modelo servido, thread nativo, inferencia, consumo y cuota restante: **UNKNOWN**.
  No API keys, extra usage, fallback ni reintentos. Sólo hubo este lanzamiento capaz de inferencia.

WER retuvo `Report.wer`, no el dump temporal citado. No se encontraron cdb/WinDbg/ProcDump en
PATH/rutas estándar comprobadas ni Microsoft.WinDbg AppX. No hubo adjuntos a procesos,
cambios del registro ni dumps copiados/publicados. Único reporte local sanitizado para proveedor:
`N/native-crash-provider-report.md`, **no publicado**. Causa interna aún desconocida, sin atribuirla
a SQLite, al modelo, RAM o una función upstream sin pila nativa.

**Corrección demostrada, no del crash.** `CliSessionStore` ahora dirige evidencia de
`permissionProfile=plan` al almacén existente de la DB externa al workspace. Se preservan
proyecto, stdout/stderr y runtime.json; no se borra ni se excluye evidencia para igualar hashes.
Regresión previa: dos FAIL esperados, exit 1, `F/quality-fast-04680cc5066842129f3c8fb72b141b90.json`.
Después: `F/quality-fast-f115c18acbf748bebd18b61bdce5ca4d.json`, **80 PASS**, exit 0:

```powershell
uv run python -m local_control_center.quality --tier fast --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_cli_read_only_artifacts.py --python-test tests_py/test_codex_capability_contract.py --python-test tests_py/test_codex_smoke_policy_seam.py --python-test tests_py/test_process_supervision.py --python-test tests_py/test_product_owner_agent_real_runtime.py
```

Controladores temporales `Tn/ = %TEMP%/aido-native-01534/`, hashes en la trazabilidad:
`.venv/Scripts/python.exe Tn/preflight.py` y `Tn/history.py`: exit 0, diagnóstico sin inferencia;
`Tn/smoke.py`: exit **1**; `Tn/closeout.py`: exit 0, reconciliación/diagnóstico, **no aprobación del smoke**.
La evidencia histórica `watchdog-*` sigue intacta. No se reejecutó el hardening completo.

Snapshot de cierre `08:22:02.131Z`: **33.752.612.864 bytes / 31,435 GiB** disponibles,
requeridos **34.359.738.368 / 32 GiB** (job 16 + reserva host 16), déficit
**607.125.504 bytes / 0,565 GiB**, cero reservas de calidad; `aggregate_memory_budget`.
Es admisión, no memoria del crash. Preview exit 0 **no** es un gate ejecutado/exit 75 nuevo.
Instalación frontend nueva, typecheck/build aislados y PR/release siguen sin validarse; no se
bajaron umbrales, cerraron aplicaciones ajenas ni reconstruyeron entornos activos.

### 14.9 Diagnóstico estructurado y captura acotada — 2026-09-06

Entrada exacta `464bca8508d1a9e6e7daa9158933b6bec05867c2`, misma rama
`codex/aido-cleanup-post-p0`, sin cambios ajenos encontrados. Commits propios:
`a294c901da0590126c7cb15f9f9545fd915d0c5c` (instrumentación, integración y pruebas) y
`01a5f207a083bd9db2b019428f5a3a48a1b305c8` (clasificación del colector sin dump).
`N/diagnostics-final-traceability.json` vincula HEAD final, contenido productivo, fuentes,
configuración, lockfiles, versiones y hashes de evidencia. El smoke se ejecutó sobre `a294c901`;
la corrección posterior tiene regresión sin inferencia, no otro smoke real.

| Área | Estado | Evidencia / límite |
| --- | --- | --- |
| Instrumentación común | PASS | JSONL v1, correlación HTTP/worker/dispatcher/supervisor, errores causales y exportación local probados en Windows. Alcance concreto debajo; no diagnóstico automático universal. |
| Captura sintética Windows | PASS | ProcDump antes de resume, PID/creation time, un minidump válido, CDB exit 0 e identificación de `RaiseFailFastException`; sólo proceso sintético sin red/auth. |
| Smoke Codex 0.153.4 | FAIL | `job-4815897c-2a27-42e4-b16b-eaf507681349`; exit nativo `3221226505 / 0xC0000409`; fallo de asignación de 4 MiB. No reproduce de forma demostrada el `0xC00000FD` histórico. |
| Captura real | FAIL | Colector preparado y asociado; intentó el dump, falló con `0x800707D1`, cero dumps. Pila y coincidencia de símbolos reales NOT_RUN. |
| Cierre de procesos/recursos/auth | PASS | API 200; workspace íntegro; cero identidades propias activas, cero reservas y ningún home temporal presente al cierre. |
| PR completo | BLOCKED | Preview vigente: 30,688 GiB disponibles, 32 GiB requeridos, déficit 1,312 GiB / 1.409.163.264 bytes, cero reservas activas; `aggregate_memory_budget`. |
| Release / instalación frontend nueva | NOT_RUN | PR no admitido. Typecheck activo no certifica instalación nueva ni retiro de dependencias. |
| Linux / macOS | NOT_RUN | Imports nativos condicionales; tests ejecutados en Windows, no certificación POSIX por mocks. No backend cgroups/macOS nuevo. |
| Unreal / adopción original | NOT_RUN | Escenarios separados, sin editor abierto/controlado ni datos operativos adoptados. |

**Instrumentación realmente añadida.** Extensión del logging estándar y de telemetría/EventBus,
no un logger por ejecución ni otra DB/daemon. Identificadores se transmiten por payload de job y se
restauran explícitamente en worker, dispatcher OS y watchdog. Se registran fronteras de aprobación,
admisión, liderazgo, eventos de etapas ya emitidos por Product Loop, proceso, salida, recuperación y
smoke; no se ejecutaron planificación, implementación ni Claude. UI envía sólo señal fija y correlation
ID, sin error/formulario. Las excepciones conservan cadena, tipo, mensaje redactado, frames sin
locals y códigos SQLite/Win32; el canal de error no consulta la conexión fallida. Se conserva la
auditoría durable con sus bloqueos; el JSONL no concede aprobación ni reemplaza esa auditoría.

Límites: INFO normal, DEBUG por intento con expiración máxima 900 s, evento 8 KiB, cola
1.024 eventos **y** 4 MiB, segmento 4 MiB, presupuesto agregado 256 MiB por directorio configurado
(por defecto `%LOCALAPPDATA%/AIDO/diagnostics`, compartido por los procesos). Escritor por proceso,
lock de presupuesto interproceso no bloqueante, cierre acotado; `diagnosticsDegraded`, `droppedEvents`
y motivo consultables en `/api/v1/telemetry/status` para ese proceso y en el siguiente evento grabado.
La rotación sólo retira segmentos diagnósticos cerrados sin protección `.keep`; nunca dumps,
backups o evidencia P0. Segmentos activos/huérfanos no se eliminan automáticamente: pueden agotar
el presupuesto y degradar el canal. Si disco y proceso fallan juntos, los contadores sólo en RAM
pueden perderse; no se promete durabilidad de diagnósticos ni visibilidad global de otros procesos.
Raíces de tests aisladas no comparten el presupuesto del directorio operacional.

Redacción anterior a cola/escritura/exportación, incluida salida UTF-16 del colector. Regresión
demostró y corrigió cabeceras Basic, prompts etiquetados y tokens JSON en stderr/excepciones.
La exportación no sigue `evidenceRefs` ni copia stdout, stderr, auth, dumps o artefactos binarios.
La redacción por patrones no demuestra que un minidump esté sanitizado. No se habilitó exportación
remota. El pequeño test de coste (300 eventos, sin saturación) midió mediana **105,05 µs**, P95
**317,6 µs**, máximo **441,1 µs**, 83.892 bytes y cero descartes; delta RSS -24.576 bytes,
que no demuestra ahorro. `N/diagnostics-logging-cost.json`; no es un SLA ni coste total de AIDO.

**Herramientas y contrato.** ProcDump 12.01 x64 oficial, firma Microsoft válida, SHA
`d1fc99ae304bd1d2bf28abeb62531da959e2431916194981b88c958fd713a8e6`;
licencia aceptada explícitamente por el propietario. CDB oficial portátil 10.0.29617.1000,
firma Microsoft válida, SHA `5f54abafca3ae5638bbf807d402fabb350a64575c1dfa9fbfc7f5732df5bee67`.
Se extrajo la distribución del [instalador oficial WinDbg](https://aka.ms/windbg/download),
sin instalar el MSIX globalmente.
Herramientas bajo `%LOCALAPPDATA%/AIDO/diagnostic-tools`, sin PATH, servicio, WER/AeDebug global
ni cambios de pila/heap/sandbox. Sí se registró la aceptación normal de licencia de ProcDump.
PDB oficial 0.153.4 preparado desde archivo SHA
`4845abdc77a5486d7377e98b4e93c37fb1e1d98bb721de43cd05ff114c701929`; no hay dump real sobre el
cual demostrar coincidencia de símbolos. [ProcDump oficial](https://learn.microsoft.com/en-us/sysinternals/downloads/procdump).

Se verificó `RUST_LOG` en [el código oficial de exec 0.153.4](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/exec/src/lib.rs):
`exec_stderr_env_filter` usa el filtro del entorno y el formatter escribe a stderr. Sólo el entorno
aislado del intento recibe `error,codex_exec=info`; la variable no se pierde en el aislador.
Esto no promete pila nativa. Probes de versión/help/auth exit 0, sesión ChatGPT nativa; modelo
solicitado `gpt-5.6-terra`, sin override de esfuerzo como en la comparación anterior. Modelo servido,
esfuerzo efectivo y consumo UNKNOWN. No API key, extra usage, fallback ni reintento.

**Prueba real y hechos diferenciados.** Autorización delegada registrada por AIDO y aprobación
normal `audit-0b476811-c9fd-4f87-9e25-4f3a76a5bcf0`. Se usó el segundo y último lanzamiento del
permiso acumulado; **restantes: 0**. DB y repositorio nuevos, sin remotos/secretos, ruta HTTP →
worker → dispatcher → ToolBroker/supervisor. CLI `managed-process-1dc9d157-ba45-4158-9721-b53f1ed5efd6`,
PID **52916**, creation time **1788720256.789972**; binario SHA
`444a3f0008050605cae73cd9b7a2dcac61294062dfaab56dd20430fd6498518b`, contrato SHA
`841ca30b70c58365b8b9a78523422255577b5abbad1fa67f345e3e1b3ca0af7e`.
Request ID `p0-native-capture-final`; IDs restantes, argv fingerprint, muestras y artefactos en
`N/diagnostics-native-smoke.json` y `N/diagnostics-native-observation.json`.

Hecho: stdout vacío; stderr **120 bytes**, SHA
`63a3eb5ba6ead96e0c1486dc04d8ec858a75d87adb2deac699187b9b073bcd1d`, informa
`memory allocation of 4194304 bytes failed`. No es consumo cero ni la misma evidencia de stack
overflow de §14.8. Dispatcher exit 0 y operación completed/200, pero contrato smoke failed.
La API sobrevivió; 72 consultas con mediana 14,195 ms y máximo 180,54 ms **con debugger**,
sin certificación de rendimiento. Los BUSY transitorios registrados se recuperaron; no prueban
la causa del fallo nativo. `N/diagnostics-native-smoke-intent.json` impide replay del intento incierto.

Límites solicitados/aplicados/readback se conservaron antes de cerrar handles, con membresía y
CPU flags=5, setters exitosos/Win32=0. Jerarquía AIDO observada:

```text
worker control_plane 2 GiB / CPU 20%
└─ dispatcher agent_cli 8 GiB / CPU 40%
   ├─ Codex agent_cli 8 GiB / CPU 40%
   └─ ProcDump qa_light 4 GiB / CPU 25%
```

No se presenta 8 GiB/40% como límite efectivo independiente del CLI: los límites de commit se
heredan y los porcentajes CPU anidados son relativos al padre. Para esta cadena AIDO, el producto
de tasas del CLI es 3,2% respecto del nivel exterior; no es CPU medida ni descarta otros ancestros.
[Semántica de Jobs anidados](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs),
[CPU rate anidado](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information).
Peak reportado del worker **2.281.967.616 bytes**, dispatcher **2.226.565.120**, CLI **10.706.944**;
no se suman padres y descendientes. El peak del worker supera su readback 2 GiB: esa discrepancia
de contabilidad y el rechazo de asignación quedan sin reconciliar. Hipótesis de presión por límites
anidados, **no causa demostrada**, tampoco demostración de falta de RAM física del host.

ProcDump estaba asociado antes de resume y detectó `0xC0000409`, pero reportó target ya terminado
y error de escritura **0x800707D1** (Win32 2001); cero dumps, ninguna pila/frames reales inventados.
El nombre [ERROR_BAD_DRIVER](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--1700-3999-)
no identifica por sí solo un driver culpable. Directorio privado `SENSITIVE_NATIVE`, ACL usuario
efectivo/SYSTEM, espacio libre previo **83.773.829.120 bytes**; guard de 1 GiB, no reserva de disco
exclusiva. El colector tiene lease propia qa_light; no se truncó un dump ni se publicó evidencia.
El helper de análisis salió 1 por falta de dump antes de lanzar CDB: análisis real NOT_RUN.

Defecto demostrado y corregido después: monitor intentado sin dump no debe registrarse NOT_RUN.
`01a5f207` conserva exit/HRESULT y registra FAIL; receipt histórico con etiqueta incorrecta sigue
intacto y se explica en `diagnostics-native-observation.json`. También se observó ProcDump exit 1
con dump sintético íntegro: ni exit 0 solo ni exit 1 solo sustituyen validación del artefacto.
La captura sintética/CDB PASS está separada de la real FAIL. Copia retenida del fixture fuera de
la limpieza de pytest, SHA `a29656c88ca1d5590735df5e2d1a5d5b7afb140f71ad8783013b420907440bde`.

**Regresiones y comandos.** `F/quality-fast-437d143038364ef983e33ea0966f341c.json`: 254+3 PASS,
exit 0; conservó warning de teardown `StaleWorkerFenceError`. Se corrigió sólo sincronización
del fixture, sin revocar liderazgo mientras el run cerraba; `F/quality-fast-93ee1f8bcf5d4c4cae7a151d2c2eb9a9.json`:
16+3 PASS, exit 0, sin ese warning. Última regresión del código productivo final:
`F/quality-fast-21cde9bcc0bf4d4e8f8c9ed0ab484ead.json`, **64+3 PASS**, exit 0, typecheck y
gitleaks de cambios/rama aprobados. Quedan los tres DeprecationWarning SWIG preexistentes,
sin ocultarlos. Formato/Biome procesaron los archivos seleccionados; OpenAPI check exit 0,
`N/diagnostics-contract-checks.json`. UI navegador real NOT_RUN; endpoint probado con TestClient.

```powershell
uv run python -m local_control_center.quality --tier fast --base 464bca8508d1a9e6e7daa9158933b6bec05867c2 --db-path .tmp/operational-hardening-p0/closure/quality.sqlite --python-test tests_py/test_native_diagnostics.py --python-test tests_py/test_structured_diagnostics.py --python-test tests_py/test_codex_smoke_policy_seam.py --python-test tests_py/test_process_supervision.py --python-test tests_py/test_watchdog_contention.py --python-test tests_py/test_watchdog_http_pipeline.py
```

Las pruebas nativas opt-in usaron `AIDO_TEST_PROCDUMP` y `AIDO_TEST_CDB` apuntando a las rutas
portátiles indicadas; sin esas variables se conserva SKIP, nunca certificación del OS.
CLI **implementado y ejecutado**, recibos en `N/diagnostics-cli-commands.json`, consulta/exportación
exit 0. La activación siguiente es el comando histórico usado, no una invitación a repetir el smoke:

```powershell
uv run python -m local_control_center.diagnostics enable --execution-id job-4815897c-2a27-42e4-b16b-eaf507681349 --ttl-seconds 900 --native-collector C:/Users/Rodd/AppData/Local/AIDO/diagnostic-tools/procdump/procdump64.exe --executable-sha256 444a3f0008050605cae73cd9b7a2dcac61294062dfaab56dd20430fd6498518b
uv run python -m local_control_center.diagnostics incident --execution-id job-4815897c-2a27-42e4-b16b-eaf507681349
uv run python -m local_control_center.diagnostics export --execution-id job-4815897c-2a27-42e4-b16b-eaf507681349 --output .tmp/operational-hardening-p0/authorized-close/diagnostics-sanitized.zip
uv run python -m local_control_center.diagnostics capabilities
```

Activar sólo configura diagnóstico; no aprueba ni ejecuta trabajo. Activación y ZIP son exclusivos,
por lo que repetir rutas existentes se rechaza. El paquete local contiene sólo eventos redactados
y manifiesto, no dumps. No se envió al proveedor; se amplió el único reporte local existente.

**Bloqueos precisos:** causa interna del stack overflow histórico UNKNOWN; captura real FAIL,
sin contexto de excepción/pila para análisis; presupuesto de inferencia agotado. Un nuevo intento
real requiere nueva autorización explícita, después de discriminar sin inferencia el rechazo de
memoria y el fallo de escritura del colector. No hay login/licencia pendiente ni motivo para otra
actualización global. PR sigue BLOCKED por admisión (preview exit 0, no un PR ejecutado/exit 75).
Selección original consultada en modo read-only sigue apuntando a Codex 0.149.0; hash intacto.
No se modificó/adoptó la DB original ni se hizo push, merge, publicación o control de Unreal.
