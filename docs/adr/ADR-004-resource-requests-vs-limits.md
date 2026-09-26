# ADR-004: Admitir por la memoria medida (request) y topear por el límite (limit)

## Estado: Aceptada

## Contexto

El gobernador de recursos usaba un solo número por clase de carga para dos cosas distintas:
decidir la admisión y fijar el tope duro del proceso (`JobMemoryLimit` del Job Object en Windows,
`process_supervision/windows_job.py`). La admisión rechazaba si
`Σ tope(leases vivas no control) + tope(nueva) > memoria disponible − piso (16 GiB)`.

Medido sobre 20.747 procesos de la instalación real (`managed_processes`, 2026-09-25):

| clase | n | tope | p50 | p90 | p99 | máx |
|---|---|---|---|---|---|---|
| `agent_cli` | 132 | 8 GiB | 170 MB | 622 MB | 2.560 MB | 2.986 MB |
| `build_heavy` | 78 | 16 GiB | 1.409 MB | 6.358 MB | 6.398 MB | 7.164 MB |
| `remote_llm_light` | 186 | 2 GiB | 47 MB | 67 MB | 83 MB | 87 MB |
| `qa_light` | 1.031 | 4 GiB | 42 MB | 724 MB | 4.127 MB | 4.139 MB |

Cada hilo que corre (`thread.product_loop.run`) se admite como `agent_cli`: con el piso de 16 GiB
exigía 24 GiB libres, aunque sus procesos usaran cientos de MB. En un equipo de desarrollo con un
modelo local cargado eso deja los hilos en `resource_wait` la mayor parte del tiempo (~90.000
rechazos por memoria registrados).

## Decisión

Separar, como Kubernetes separa `requests` de `limits`
(https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/):

- **`memory_request_bytes`** (nuevo en `WorkloadProfile` y en `resource_leases`, fase 81): lo que la
  admisión da por ocupado. `admission_memory_bytes(profile)` devuelve el request o, si la clase no lo
  tiene, su tope.
- **`memory_limit_bytes`** (sin cambio): el tope duro del Job Object. Un proceso desbocado se corta
  igual que antes.
- La admisión suma requests: `Σ request(leases vivas no control) + request(nueva) > disponible − piso`.
  Una lease escrita antes de la fase 81 (request NULL) cuenta por su tope.
- El rechazo `aggregate_memory_budget` incluye los números comparados (requested, reservado y
  margen), para auditar un sobrecompromiso.

Valores iniciales, con un margen de 34-40% sobre el **máximo** observado (no sobre un percentil,
porque las muestras son chicas y en `agent_cli` cada invocación es única):

- `agent_cli` 4 GiB (máximo 2,99 GB), `build_heavy` 10 GiB (7,16 GB), `remote_llm_light` 512 MiB (87 MB).
- Reservan su tope, sin cambio: `control_plane` (esencial: la admisión no revisa su memoria),
  `qa_light` (bimodal: git de ~10 MB y suites de ~4 GB comparten clase), y las clases sin muestra
  suficiente (`local_model_call`, `browser_test`, `local_gpu_model`, `capture_session`, `unreal_*`).

El recomendador del Vertical Pod Autoscaler usa el percentil 90 más un 15% de margen
(`target-memory-percentile=0.9`, `recommendation-margin-fraction=0.15`,
https://github.com/kubernetes/autoscaler/blob/master/vertical-pod-autoscaler/docs/flags.md). Aquí se
eligió un criterio más conservador (máximo + 34-40%) mientras la muestra crece.

## Consecuencias positivas

- Un hilo pasa a exigir piso + 4 GiB en vez de piso + 8 GiB libres; un build, piso + 10 en vez de
  piso + 16.
- El tope por proceso no cambia: la protección contra un proceso desbocado es la misma.
- Compatible hacia atrás: las leases vivas al desplegar cuentan por su tope.

## Consecuencias negativas

- **Sobrecompromiso acotado**: un árbol de `agent_cli` puede crecer hasta su tope (8 GiB) habiendo
  reservado 4. La red es el vigilante del piso duro (`process_supervision/service.py`), que cancela
  cargas no esenciales si la memoria **real** baja de `hardFreeMemoryGiB` (8 GiB), más un solo slot
  pesado y un worker de un job a la vez.
- Ese vigilante cancela **todas** las cargas no esenciales a la vez; Kubernetes desaloja primero las
  que exceden su request (https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/).
  Un desalojo graduado queda como mejora pendiente.
- Los hijos cubiertos por la lease del padre (`BranchAdmission._parent_covers`) heredan el tope del
  padre: el ahorro de margen aplica a la raíz del árbol.

## Alternativas consideradas

- **Una clase `git_command`**: git mide 8-17 MB en el repo de AIDO, pero dentro de un job los `git`
  heredan la lease del job (531 leases propias de `qa_light` para 1.031 procesos), así que no mueve la
  reserva que sí pesa: la del job del hilo.
- **Bajar el tope de `agent_cli` a 4 GiB**: reduce la reserva sin concepto nuevo, pero corta con el
  Job Object procesos que hoy terminan bien.
- **Solo bajar el piso**: es configuración del operador (`resources.minFreeMemoryGiB`) y es
  complementario; no corrige que la admisión cuente topes en vez de uso.

## Impacto

`host_resources/{models,profiles,repository,governor}.py`, `shared/migrations.py` (fase 81),
`ResourceLease.memoryRequestBytes` en la API y el cliente OpenAPI generado.
