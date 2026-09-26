# ADR-005: Desalojo graduado por presión de memoria

## Estado: Aceptada

## Contexto

ADR-004 dejó documentado como deuda pendiente que, bajo el piso duro
(`resources.hardFreeMemoryGiB`), el sistema cancelaba **todas** las cargas no esenciales a la vez en
vez de desalojar de a una, como hace el node-pressure eviction de Kubernetes. Existían dos caminos
independientes con ese mismo defecto:

1. `HostResourceGovernor.violations_for_snapshot` (`local_control_center/host_resources/governor.py`)
   registraba una violación `hard_memory_floor` para **cada** lease no esencial activa en una sola
   pasada. Lo invoca el worker (`workers/runtime.py::_sample_resources_if_due`), que corre también
   durante jobs largos vía `_leadership_watch_loop` mientras el worker es líder.
2. Cada vigilante de proceso (`process_supervision/service.py`) se detenía a sí mismo si
   `psutil.virtual_memory().available` caía bajo el piso y su clase no era esencial, sin distinguir
   gradación ni preguntar si algo más ya estaba resolviendo la presión.

Medido sobre la instalación real (7 días, 91.777 muestras de `resource_usage_samples`): memoria
disponible por debajo de 4 GiB en 49 muestras, por debajo de 2 GiB en 4, mínimo histórico 0,7 GiB;
`resource_violations` nunca tuvo una fila (el camino de desalojo total nunca llegó a dispararse en
producción, pero seguía siendo el único diseño disponible si la presión hubiera persistido).
`agent_cli` (`managed_processes`, 132 ejecuciones medidas): p50 170 MB, p90 622 MB, p99 2,56 GB,
máximo 2,99 GB.

## Decisión

- **Unidad de desalojo: la lease**, no el proceso individual, igual que Kubernetes desaloja un pod
  completo, no un contenedor suelto dentro de él
  (https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/). Una lease puede
  cubrir varios `managed_processes` cuando una rama pidió prestado el presupuesto de su padre
  (`host_resources/branch_admission.py`); desalojar la lease cancela el árbol completo que ocupa.
- **Uso real, no pico histórico**: `process_supervision/memory_usage.py::live_memory_by_lease` suma
  el RSS actual (`psutil.Process.memory_info().rss`) del proceso raíz y sus descendientes vivos de
  cada lease, verificando identidad por `create_time()` (tolerancia 0,01 s, el mismo criterio que ya
  usa `recovery.py`/`owned_tree.py`) para no contar un PID reciclado. Nunca se usa
  `managed_processes.peak_memory_bytes`: es un máximo que nunca baja y penalizaría para siempre a un
  proceso que ya liberó memoria.
- **Un desalojo por invocación**, priorizado así: primero la lease cuyo uso excede su reserva
  (la registrada en la lease, `memory_request_bytes`, o su tope si es NULL: la misma cuenta que la
  admisión), luego la que excede por más, luego la más reciente (`acquired_at` desc), luego por id.
  Sin `usage_source` (compatibilidad hacia atrás, por ejemplo una llamada directa sin el helper de
  proceso), o si la medición falla, todas las leases no esenciales son candidatas con uso asumido
  en 0 y el desempate cae en la más reciente: sin medición se sigue conteniendo, nunca se deja de
  desalojar. Con medición, sólo son candidatas las leases con procesos vivos: desalojar una sin
  procesos no libera memoria real.
- **Gracia de `EVICTION_GRACE_SECONDS = 6`** (3x el intervalo de muestreo por defecto, 2 s) desde la
  última violación `hard_memory_floor`: el sistema operativo tarda en reclamar físicamente la
  memoria de un árbol recién terminado, así que repetir el desalojo en la muestra siguiente
  (~2 s después) cancelaría una segunda víctima innecesaria antes de ver el efecto de la primera. Las
  leases ya con una violación sin resolver quedan excluidas de la siguiente ronda, así que pasada la
  gracia el gobernador avanza a la próxima candidata en vez de repetir la misma.
- **El vigilante local por proceso sigue existiendo como red de respaldo**, pero deja de actuar por
  su cuenta salvo que: (a) `available < piso / 2` (emergencia real), o (b) el gobernador global no
  parece estar corriendo, definido como que la muestra más reciente
  (`ResourceRepository.latest_sample()`) tiene más de `GOVERNOR_STALE_SECONDS = 10` o no existe. Esa
  lectura sólo ocurre bajo presión (`available < piso`), nunca en el camino feliz. Las clases
  esenciales siguen exentas en ambos caminos.
- **Reserva de `agent_cli` de 4 a 1 GiB** (el tope del Job Object sigue en 8 GiB, sin cambio). ADR-004
  la fijó en 4 GiB con un margen de 34-40% sobre el máximo medido (2,99 GB) porque la única red de
  seguridad era el desalojo total. Con el desalojo graduado protegiendo por uso real, la reserva
  contable puede acercarse más al p90 medido (622 MB) sin perder esa protección: 1 GiB deja margen
  cómodo sobre el p90 y delega el caso p99/máximo al desalojo graduado, no a una reserva conservadora
  que sólo servía para inflar el cálculo de admisión.
- **La exclusión y la resolución de violaciones son por lease, no por `execution_id`**
  (`ResourceRepository.leases_with_unresolved_violation`/`resolve_violations_for_lease`). Una
  violación es un evento de esa reserva puntual: indexarla por ejecución tenía dos fallas. Primero,
  una víctima que no muere (su lease sigue activa) excluía la ejecución completa para siempre, y si
  era la única candidata el gobernador dejaba de actuar en cualquier lease futura de esa misma
  ejecución. Segundo —un bug latente, nunca observado en producción porque `resource_violations`
  nunca tuvo filas—, un reintento con el mismo `execution_id` habría heredado la cancelación de una
  lease vieja para siempre, porque nada marcaba `resolved_at`. `release` ahora resuelve las
  violaciones de la lease que libera (y por lo tanto también al recuperar expiradas, que liberan
  cada lease vencida); `ManagedProcessRepository.cancellation_reason` además exige con un JOIN que la
  lease de la violación siga activa, como blindaje si algún otro camino futuro dejara de resolver.
- **Una carga respaldada por un contenedor Docker también es candidata.** `live_memory_by_lease`
  sumaba sólo `managed_processes`; una lease sostenida por un contenedor (`process_supervision/
  docker.py`, por defecto `build_heavy`) nunca aparecía en el uso medido y por lo tanto nunca era
  candidata (con `usage_source` sólo se desaloja lo que aparece en el dict). Ahora también recorre
  `managed_containers` con `released_at IS NULL` y las agrega con uso 0: **el RSS de un contenedor no
  se mide por este camino** (requeriría la API de stats de Docker, fuera de alcance); la lease sigue
  siendo candidata y se desempata por recencia como cualquier otra sin medición.
- **El vigilante local escala tras presión sostenida.** Que el gobernador global esté "activo" (una
  muestra reciente) no prueba que esté logrando liberar memoria: si el desalojo graduado no alcanza a
  bajar la presión, el vigilante quedaba mudo en `[piso/2, piso)` indefinidamente. Cada vigilante
  recuerda (`time.monotonic()`, se reinicia al subir sobre el piso) desde cuándo ve
  `available < piso`; si la presión dura más de `PRESSURE_ESCALATION_SECONDS = 30` (5 ventanas de
  gracia de desalojo), actúa aunque el gobernador siga activo.

## Consecuencias positivas

- Bajo presión, se cancela sólo la carga que más contribuye al problema (mayor uso sobre su
  reserva), no todo el trabajo no esencial en curso.
- El criterio de selección usa consumo real medido en el momento, no la reserva declarada ni un
  máximo histórico que nunca refleja el estado actual.
- Con el worker corriendo un job a la vez (tope duro vigente en `ConcurrentWorker`), la reserva más
  baja de `agent_cli` no se acumula entre ejecuciones concurrentes: en la práctica convive una sola
  lease pesada no esencial a la vez, más los hijos que cubre.

## Consecuencias negativas

- La gracia de 6 s es una ventana en la que la memoria puede seguir cayendo si el primer desalojo no
  alcanza a liberar lo suficiente; el vigilante local de emergencia (`piso / 2`) sigue siendo la
  malla de seguridad final para ese caso.
- El vigilante local de respaldo, cuando actúa, sigue sin ser graduado (cancela ese proceso puntual,
  no coordina con otras leases); sólo se activa cuando el gobernador global no está disponible, hay
  emergencia real, o la presión ya duró `PRESSURE_ESCALATION_SECONDS`, así que su alcance quedó
  acotado pero no eliminado. Con presión sostenida y varios vigilantes en `[piso/2, piso)` a la vez,
  cada uno decide por su cuenta sin coordinarse entre sí, igual que ya ocurría en la emergencia.
- El uso de un contenedor Docker nunca se mide (aporta 0): dos leases respaldadas por contenedor con
  huellas reales distintas se desempatan igual, por recencia, hasta que se instrumente `docker stats`
  u otra fuente de RSS para contenedores.
- Bajar `agent_cli` a 1 GiB amplía el margen de sobrecompromiso contable por árbol individual (de 4 a
  7 GiB bajo el mismo tope de 8 GiB) hasta que el desalojo graduado actúe; es un cambio consciente
  que traslada la protección de "reserva conservadora" a "uso real vigilado".
- **Dependencia explícita**: el argumento de que la sobreasignación no se acumula asume el worker
  admitiendo un job a la vez (tope duro actual). Si el worker pasa a más de un job concurrente, hay
  que revisar si 1 GiB de reserva para `agent_cli` sigue siendo suficiente para la contabilidad
  agregada de admisión (`aggregate_memory_budget`), porque el ahorro de margen se multiplicaría por
  cada job simultáneo.

## Alternativas consideradas

- **Mantener el desalojo total**: ya señalado como deuda en ADR-004; cancela trabajo sano sólo por
  coexistir con el causante real de la presión.
- **Priorizar por `peak_memory_bytes`**: rechazado porque es un máximo histórico que nunca baja; un
  proceso que ya liberó memoria seguiría pareciendo indefinidamente el peor candidato.
- **Sin período de gracia**: rechazado porque el sistema operativo no libera memoria de forma
  instantánea al terminar un proceso; sin gracia, la siguiente muestra (~2 s después) desalojaría una
  segunda víctima antes de que la primera cancelación hiciera efecto.
- **Extender el desalojo graduado a CPU**: fuera de alcance de este cambio; el piso duro que dispara
  esta ruta es sólo de memoria, y `resources.maxCpuPercent` ya tiene su propio control en la
  admisión (`aggregate_cpu_budget`).
- **Medir el consumo de un contenedor Docker con `docker stats`**: se descartó para esta iteración;
  añade una llamada de red/CLI al camino de muestreo y el propio motor de contención de Docker
  (`--memory`) ya acota su tope, a diferencia de un proceso nativo sin ese límite adicional.
- **Mantener la exclusión por `execution_id`**: se descartó porque no distingue una lease muerta de
  una viva, y expone el bug latente de cancelar un reintento con el mismo id para siempre.

## Impacto

`host_resources/{governor,repository,profiles}.py`, `process_supervision/{memory_usage.py (nuevo),
repository.py,service.py}`, `workers/runtime.py`. Tests: `tests_py/test_host_resource_governor.py`,
`tests_py/test_process_supervision.py`, `tests_py/test_process_memory_usage.py` (nuevo), y
recalibración de fronteras de admisión en
`tests_py/{test_branch_resource_admission,test_effective_runtime_readiness,test_worker_local_gpu_admission}.py`.
