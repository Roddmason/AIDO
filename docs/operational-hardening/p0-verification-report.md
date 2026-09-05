# P0 verification report

Status: **P0 local Windows verification complete**. External provider certification remains
`configuration_required`; this is not a claim of native POSIX or representative Unreal validation.

Branch: `codex/aido-operational-hardening-p0`.
Baseline: `94eaf8c1756f17493c7f80af359d86cb9561816c` (`origin/dev` at task start).
No push or merge is authorized in this hardening task.

## Final acceptance (2026-09-05 UTC)

The source tree remained unchanged throughout the final unified run. Only this report was finalized
afterward; phase-8 commit identity is recorded in Git history. No P1/P2 work, push or merge was performed.

| Command / gate | Result | Receipt under `.tmp/operational-hardening-p0/` |
| --- | --- | --- |
| `powershell -NoProfile -File scripts/verify-operational-hardening.ps1` | exit 0 | `verify-db869fa39a224ccebb7f9e29a65fc92c/report.json` |
| `python -m local_control_center.quality --tier pr` (inside verifier) | exit 0, all 12 steps | `quality-pr-25153469e0d14808986260431e26c1ec.json` |
| Full `pytest tests_py -v --tb=short` | 1,879 passed; exit 0; 2,090.83 s | `phase8-python-complete-green.xml` |
| `node scripts/run-web-tests.mjs` | 337 passed, 7 skipped; exit 0; 1,475.63 s including setup/build | PR web step and its stdout artifact |
| Vite, TypeScript, Ruff, format, Biome | all exit 0 | PR step receipts |
| Architecture guardrails | 101 passed; exit 0 | PR architecture step |
| Gitleaks / Semgrep / `git diff --check` | all exit 0; no leaks; 4 rules / 333 targets / 0 findings | PR security and diff steps |
| `python -m local_control_center.quality.release` | exit 0; all 7 clean-install/upgrade steps | `release-9a28f64605f24fc78249d912828d7654.json`, `final-clean-install.json` |

The verifier ran from **05:39:50.068Z to 06:42:53.087Z**. Before PR, it passed baseline
upgrade/backup, focused P0 **68**, HTTP responsiveness **5**, leadership **6**, admission **16**,
native supervision/cancellation **29**, the managed-root audit and OpenAPI drift. No full gate was
resumed from cached test results and no failed command result was retried into a pass.

Clean installation completed from **06:43:33.628Z to 06:44:14.816Z** using locked dependencies
and a temporary checkout. Baseline schema **58 → 67** was applied twice; existing project data,
integrity, foreign keys and backup/restore roundtrip passed. The installed native launcher returned
HTTP **200** for `/healthz` and `/`. Its owned API tree was intentionally cancelled for smoke cleanup
(`native_smoke_cleanup`, process exit 3), with **0 descendants** remaining; the enclosing smoke
gate exited 0. This cancellation is not described as a natural API exit. The temporary installation
was removed by its scoped cleanup; the operator installation and external project workspaces remained.

### Explicit omissions and warnings

- Five existing viewport-specific omissions: three desktop resizable-pane tests on mobile, the
  narrow-layout fallback on desktop, and the native lifecycle on mobile. The native lifecycle passed
  on desktop with a separate API/worker, actual temporary Git, controlled localhost protocol, diff,
  QA/Gitleaks, approval, archive, rename and similarity.
- Two existing conditional `Workbench shows the Git branch detected by the policy-gated Git status
  endpoint` cases (desktop/mobile) skipped because their initial cache-only GET had no branch snapshot.
  They are **not verified** in the unified run. No skip condition was added to obtain a green gate;
  these do not replace the separately passing native Git and lifecycle evidence.
- Biome emitted **6 warnings**, pytest emitted **3 SWIG deprecation warnings**, and Vite reported the
  existing **702.74 kB** main chunk. Semgrep's one size-based skip is historical Python bytecode,
  not source. Clean install reported cross-volume copy fallback and an ignored esbuild install script;
  its actual build and HTTP smoke nevertheless passed. Warnings are retained, not suppressed.

### Measured resources and cleanup

- Verifier: host CPU peak **98.8%**, minimum available RAM **29,402,816,512 bytes** (27.38 GiB),
  **1,883 samples**. PR's independent monitor: **98.7%**, **29,398,196,224 bytes**, **1,809 samples**.
- Native peak Job committed memory: Python **6,679,146,496 bytes** (6.22 GiB); web
  **7,482,040,320 bytes** (6.97 GiB). These are Job commit measurements, **not RSS**.
- Host CPU includes unrelated interactive work and does not attribute the peak to AIDO or establish
  a saturation-free whole computer. Admission thresholds, memory/process limits and one-heavy-workload
  concurrency were not increased. Pre-spawn contention waits remained bounded and fail-closed.
- Final audit at **06:44:50.375Z**: **0 orphan roots, 0 unresolved identities, 0 abandoned records,
  0 active registered roots, 0 foreign processes terminated** (`final-process-audit.json`). Scope:
  selected supervision database plus native descendant assertions; not every process on the machine.

## Implementation and diagnostic history (2026-09-04–05)

- Phases 1–7 are local commits. Phase 7 story gate passed Python/architecture checks, TypeScript,
  production build, desktop/mobile Operations tests and secret scans.
- Current Python 3.13.15 links SQLite 3.53.1. The previous environment was retained, not deleted.
- Recovery/capture/native process suite: **39 passed**, exit 0; JUnit
  `.tmp/operational-hardening-p0/phase8-capture-sqlite-green.xml`.
- Launcher and Windows DNS compatibility: **6 passed**, exit 0; JUnit
  `.tmp/operational-hardening-p0/phase8-launcher-green.xml`.
- Subsequent functional runtime/launcher regression: **73 passed**, exit 0; JUnit
  `.tmp/operational-hardening-p0/phase8-runtime-focused-green.xml`. Includes actual owned-descendant
  shutdown, Git hooks, native test runtimes and asynchronous domain completion.
- Recovery/resource regression after fixing unprimed CPU admission: **25 passed**, exit 0; JUnit
  `.tmp/operational-hardening-p0/phase8-cpu-sampling-green.xml`.
- Actual baseline bootstrap/upgrade/backup: schema **58 → 67**, initialization twice,
  existing project retained and backup/restore roundtrip passed on isolated databases.
- Full Python diagnostic finished: **1,599 passed / 263 failed**, exit 1, 2,786.75 seconds.
  Preserved JUnit: `.tmp/operational-hardening-p0/phase8-full-python-diagnostic.xml`;
  PR report: `quality-pr-b80040cb361540e99d5a9243053fe17e.json`. No PR approval followed.
- Initial repair regression: **34 passed**, exit 0; JUnit
  `.tmp/operational-hardening-p0/phase8-contract-regression-a2.xml`. Covers i18n, DevOps, cutover,
  event-bus ownership, selected Product Loop cases, Semgrep command policy and clean-interpreter
  worker exports. The latter caught and fixed an import-order cycle missed by full-suite ordering.
- Native root audit after the failed full run, `2026-09-05T00:52:54.694Z`: **0 orphan roots,
  0 unresolved identities, 0 abandoned records** in the selected supervision database.
- Subsequent regressions: **228 passed / 20 failed**, then **13 passed / 7 failed**, then
  **7 passed**, exit 0 (`phase8-final-seven-r2.xml`). These are repair passes, not a substitute for
  the final full suite. They also reproduced and fixed Git delivery attempted inside an approval
  transaction; the integration test now proves committed intent, actual merge and branch cleanup.
- Clean-install staging regression: missing launchers reproduced, then **1 passed**, exit 0
  (`clean-install-launcher-green.xml`). Actual clean installation subsequently passed all seven steps,
  exit 0 (`release-5ae64d0161b64063ad28fe88f5a750b5.json`): locked Python/Node dependencies,
  production build, native installed launcher and actual `/healthz` and dashboard HTTP 200.
  The owned smoke tree was intentionally terminated during cleanup, with zero remaining descendants;
  its cancellation receipt is not misrepresented as a natural process exit.
- Earlier full Python run: **1,867 passed / 1 failed**, exit 1, 2,408.73 seconds. Preserved JUnit:
  `phase8-python-one-stale-assertion.xml`. The remaining obsolete assertion required a direct root-PID
  kill, now replaced by owned-tree cleanup. Its focused retest passed; full PR approval was then pending.
- Native Playwright server cleanup regression: Windows venv PID differs from actual server PID.
  Parent/creation-time verified descendant cleanup passed in the 38-test process/CI regression and
  the desktop/mobile operational smoke. No port-based or broad process cleanup was used.
- Web project completion regression: **10 passed**, exit 0, desktop/mobile. The real server still
  responds 202; an explicit test-only OS fixture completes only that operation with lease/fence and
  controlled domain resource data. Production scheduling and offline-boundary tests remain separate.
- Full control-center spec: **178 passed**, exit 0 (89 desktop + 89 mobile), 854.27 seconds;
  `web-control-center-202.json`. Other specs and the final full gate are not implied by this result.
- External-worker regressions reproduced missing conversation preflight, repair operations blocked
  behind an unconfigured conversation, and a heartbeat race that could pass a null fencing token.
  After fixes, **38 passed**, exit 0 (`phase8-worker-contracts-green.xml`). Lightweight durable
  operations have priority over queued background conversations, without bypassing resource admission.
- Native lifecycle browser diagnostic failed on startup, exit 1: the registered tree reached
  **8,725,929,984 bytes** peak Job memory against its 8 GiB profile. Cleanup reported zero remaining
  descendants. This is a failed run, not evidence of successful lifecycle execution.
- An isolated import probe measured worker-module private committed memory at **43,278,336 bytes**,
  rising to **3,274,285,056 bytes** after loading HTTP routers and numerical dependencies. The CLI now
  imports HTTP routers only for API mode; its measured private committed memory is **47,964,160 bytes**
  (RSS **64,061,440 bytes**). These are cold-import measurements, not whole-run RAM or GPU claims.
  **46 launcher/architecture checks passed**, exit 0 (`worker-cli-import-green.xml`). The lifecycle
  fixture also no longer starts an unused second API. No resource ceiling was increased.
- Read-only Ollama readiness regression: **18 passed**, exit 0
  (`ollama-cached-models-green-r2.xml`). The healthy account now uses enabled, synchronized catalog
  models instead of an empty internal model list. Disabling the model makes Developer readiness
  unavailable again; neither GET starts a provider network probe. Lifecycle diagnostics after the
  import correction stayed below 7.14 GB peak Job memory but exposed this readiness defect; they
  remain failed runs and do not establish an end-to-end pass.
- Runtime remediation scheduling regression: **36 passed**, exit 0 (`repair-workload-green.xml`).
  The persisted `validate_runtime` action is classified as `qa_light` and precedes the conversation
  it repairs. A client-supplied action/resource-class claim cannot downgrade a Git action. Other
  remediation actions retain their conservative resource profile and durable execution boundary.
- Native lifecycle **passed**, exit 0 (`web-native-worker-lifecycle-r5.json`): separate API/worker,
  controlled localhost protocol, actual temporary Git project/worktree, runtime repair, queued job,
  generated diff, QA/Gitleaks, human approval, archive/rename and similarity. Desktop: **1 passed**;
  mobile: **1 existing skip**, not claimed as verified. Duration **154.37 seconds**, peak Job memory
  **7,457,980,416 bytes**, CPU time **129.39 seconds**, remaining descendants **0**. Test-owned
  temporary files were cleaned after owned-process shutdown; no real provider inference was enabled.
- Quality admission regression: **10 passed**, exit 0 (`quality-admission-green.xml`). A transient
  pre-spawn refusal waits/rechecks with unchanged limits for at most 120 seconds. An executed failing
  command is not retried; persistent refusal still fails closed. This addresses an observed CPU
  refusal between otherwise successful build and TypeScript gates, not a bypass of host policy.
- Sequential static/security diagnostic: build and TypeScript exit 0; Ruff/format exit 0 (500 Python
  files); Biome exit 0 with six dependency-array warnings; architecture **101 passed**; Gitleaks
  exit 0, no leaks. Reports: `pr-static-1bacf05ced8e4003990a9d0ec0d2e430.json` and
  `pr-static-cd43432cafa64a85b05fe6c01e50968c.json`. These partial diagnostic reports retain their
  original failed terminal status where the next gate failed.
- Native Semgrep initially failed before scanning: a measured eight-process CLI fallback chain
  exhausted the unchanged `qa_light` process quota (`semgrep-quota-probe.json`). The pinned version's
  supported Windows `--legacy` entry mode removed the redundant launch chain. Actual scan then
  passed, exit 0 (`semgrep-legacy.json`): **4 rules, 333 targets, 0 findings**, 12.72 seconds,
  peak Job memory **394,932,224 bytes**, remaining descendants **0**. The size-based skipped file is
  an old Python 3.11 pytest bytecode cache, not source. Rules, exclusions and limits were not changed.
- Remaining 13 web specs: **160 passed / 4 existing skips**, exit 0
  (`remaining-web-contracts.json`), desktop/mobile, 804.36 seconds, peak Job memory
  **7,402,311,680 bytes**, CPU time **971.52 seconds**, remaining descendants **0**. These skips and
  the lifecycle mobile skip are explicit, not green test results. Final unified PR/verifier was then
  pending; separated diagnostic passes did not replace it.
- Subsequent complete verifier `verify-98b16808cfc04f039f6c564a9cfdb666` passed upgrade,
  focused P0, HTTP responsiveness, leadership, admission, cancellation and OpenAPI, then failed
  full Python: **1,871 passed / 6 failed**, exit 1, 2,738.95 seconds. Preserved JUnit:
  `phase8-python-six-diagnostic.xml`; PR `quality-pr-542af326e7e34c339e7926ac4fbf763a.json`.
  Four functional tests depended on transient host CPU availability (confirmed by admission
  decisions and job events); two source-contract tests mishandled generated bytecode and the
  verified Windows Semgrep entry mode. No product resource limit or scanner rule was relaxed.
- All six affected modules then passed **76 tests**, exit 0 (`full-run-regressions.json`).
  Controlled domain snapshots are opt-in test fixtures; subprocesses remain native and the outer
  quality governor uses real host measurements. A regression also proves that ignoring bytecode
  does not omit the Python web-fixture source from the removed-contract inventory.
- Quality entrypoint policy regression: **26 passed**, exit 0 (`quality-entrypoints-green.json`).
  New quality tiers and the release verifier invocation respect the host PowerShell policy without
  an execution-policy override. Ruff, format (500 files) and `git diff --check` subsequently exited 0
  (`final-regression-static.json`). Final unified verifier approval was then pending.
- Verifier `verify-a0534c0cdefe4f13b9957b04137770a1` again passed all pre-PR gates,
  then full Python finished **1,878 passed / 1 failed**, exit 1, 2,727.01 seconds
  (`phase8-python-worker-admission-diagnostic.xml`; PR
  `quality-pr-8bed0912b44b4b30bc9ac82765fb8809.json`). All six previously failing cases passed.
  The remaining domain test requested an unsupported worker job but received `resource_wait`
  before any run existed; its persisted admission reason was `host_cpu_saturated`.
  A direct-call inventory identified four worker-domain cases and one native output-capture case
  still lacking deterministic capacity. Only those tests now opt into the existing host fixture;
  dedicated resource scenarios and the external native governor retain their own measurements.
- Worker-domain/adaptor/resource regression then passed **73 tests**, exit 0
  (`worker-domain-admission-green.json`), followed by Ruff, format and whitespace exit 0
  (`worker-domain-static.json`). No product code changed in this final determinism correction.

## Failed attempts preserved

The first complete verifier attempt passed focused operational checks, then its full Python gate was
cancelled deliberately after discovering missing incremental capture and an affected SQLite runtime.
It remains **failed**, not reclassified as passed. The later complete Python diagnostic also failed;
it exposed synchronous-client assumptions, unprepared host samples, missing UI catalog entries and
stale source assertions. Repairs were verified in focused regressions before the final full run.
Some regression launches were correctly refused by CPU admission; no test result is inferred from
those refused launches.

The final acceptance section supersedes the earlier pending status, not the failed receipts themselves.
Closure rests on the actual full PR/verifier, clean installation and native cleanup results, not the
focused diagnostic passes. Full logs and resource measurements remain in the ignored receipt directory.

## Scope and residual evidence

Real provider inference was not executed: explicit billable-call approval is absent. Authentication,
provider/project configuration and a fresh compatibility receipt remain unverified for a real smoke.
Providers were not enabled by verification. Native POSIX validation, representative live Unreal load,
sealed-input/artifact retention and full external-store backup are separate limitations.

CPU/RAM samples are host observations, not per-process measurements. Orphan checks cover registered
managed roots in the selected database plus descendant assertions in native tests; no foreign process
was intentionally terminated. The final whole-run audit is recorded above.

The failed full verifier observed host CPU peak **99.8%** and minimum available RAM **27,897,380,864
bytes** (about 25.98 GiB), over 1,472 samples. This does not attribute that load to AIDO; it demonstrates
real host contention and is not evidence of a saturation-free final gate.
