# P0 resource profiles

Canonical definitions: `local_control_center/host_resources/profiles.py` and
`local_control_center/settings/registry.py`. Values below are defaults, not measured host capacity.

| Class | Admission | CPU cap % | RAM cap GiB | Process cap |
| --- | --- | ---: | ---: | ---: |
| control_plane | essential | 20 | 2 | 8 |
| remote_llm_light | light | 15 | 2 | 4 |
| qa_light | light | 25 | 4 | 8 |
| agent_cli | heavy | 40 | 8 | 16 |
| browser_test | heavy | 40 | 8 | 24 |
| build_heavy | heavy | 65 | 16 | 32 |
| unreal_editor | heavy, GPU | 70 | 24 | 64 |
| unreal_cook | heavy, exclusive | 80 | 32 | 64 |
| local_gpu_model | heavy, GPU | 50 | 16 | 16 |

These native caps apply to registered trees launched with the profile. They do not throttle the
operator's entire computer or retroactively contain an already open Unreal Editor. Host sampling
observes external contention; admission refuses new work when there is insufficient headroom.

On Windows, the Job memory cap constrains committed virtual memory, not resident working set (RSS).
An allocation can fail at the Job cap while the host still has free physical RAM; inspect both metrics.
See Microsoft's [Job Object limit contract](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information).

## Global policy

- `resources.profile=auto`: `interactive_unreal` when Unreal Editor is observed; otherwise `development`.
- `resources.maxHeavyWorkloads=1`; `resources.maxLightWorkloads=2`.
- `resources.minFreeMemoryGiB=16`; hard safety floor `resources.hardFreeMemoryGiB=8`.
- `resources.minFreeDiskGiB=50`; `resources.maxCpuPercent=65` admission ceiling.
- `resources.unrealReserveMemoryGiB=20`; `resources.blockLocalGpuWhenUnreal=true`.
- `resources.sampleIntervalSeconds=2`; resource samples retained for seven days.

Operational acceptance correction: non-control leases now also sum their CPU caps against
`resources.maxCpuPercent`, and their memory caps must fit available RAM minus the configured
free-memory reserve. This is conservative: live lease usage is not subtracted without trustworthy
per-lease attribution. A 16 GiB build therefore needs at least 32 GiB available at default settings;
do not reduce the reserve to force a gate to run. The admission threshold is not a whole-host throttle.
One lease cannot fund independent sibling process roots. A durable pre-spawn reservation serializes
them; only verified native descendants may inherit the already contained parent's budget.

`interactive_unreal` reserves at least 20 GiB of available RAM and blocks local GPU inference by
default. `idle_validation` is an explicit profile label, **not** permission to raise concurrency or
disable safeguards. P0 retains the validated limits. A stale/missing resource sample is not evidence
of available capacity; readiness requires a sample no older than 30 seconds.

Admitted remote branches receive their own leases. A workflow cannot bypass global limits by
spawning a private thread pool. Nested quality processes may inherit a lease only from a live native
ancestor whose identity and durable lease match; an environment-supplied ID is insufficient.

## Interpreting a wait

`resource_wait` means the job remains durable but has not acquired permission to launch. Read its
reason and inspect `/api/v1/operations/resources`. Wait for load to subside or stop **your own** workload
through its normal controls. Do not raise parallelism, kill unrelated apps or delete a lease to make
the indicator green. Expired capacity linked to an unresolved native tree stays reserved until safe
recovery. The hard RAM floor can cancel an active managed execution; this is recorded as a safety
termination, not a successful result.

CPU peak and minimum free host RAM describe the observed host during a gate. Per-process CPU time
and peak RAM describe the managed tree. They answer different questions and must not be interchanged.

Admission uses a positive CPU measurement window. A requested zero interval is measured for one
second instead: [psutil documents](https://psutil.io/api/#psutil.cpu_percent) that an unprimed
nonblocking sample can return a meaningless zero. That value must never authorize heavy work.

The full Python gate uses `build_heavy` to contain native Git/security-hook descendants. Semgrep
uses one scanner worker (`--jobs 1`), fails on findings (`--error`), and disables metrics and remote
version checks. These options follow the [official CLI contract](https://docs.semgrep.dev/cli-reference);
serial execution does not remove rules or target directories.

On Windows, the pinned Semgrep 1.163.0 command selects `--legacy`: its
[official entrypoint](https://github.com/semgrep/semgrep/blob/v1.163.0/cli/src/semgrep/console_scripts/entrypoint.py)
otherwise starts the newer CLI and falls back to Python through additional native launchers.
The measured chain exhausted the eight-process `qa_light` quota before scanner launch. The
documented alternate entry mode runs the same Python driver and scanner with unchanged rules,
error policy, CPU/RAM/process limits and one scanner worker; it is not a security-gate bypass.
