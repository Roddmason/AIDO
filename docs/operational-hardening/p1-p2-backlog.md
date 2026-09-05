# P1/P2 backlog — not authorized by P0

P1 does not start until P0 verification and the complete `quality:pr` gate pass and the operator
explicitly authorizes the next scope. These are follow-up requirements, not implemented features.

## P1: measured operational completion

- Validate native containment/recovery on Linux and macOS; keep Windows evidence separate.
- Run explicitly approved real Codex/provider smokes with a configured account, compatible binary,
  model catalog, project policy and durable receipt. Never turn unit-test fakes into release evidence.
- Define operator-approved retention/export for sealed inputs and complete artifacts, preserving
  references and audit requirements; measure long-running disk growth.
- Exercise an actual operator database copy with representative historical workflows before promotion.
- Measure API latency and cancellation under representative external Unreal/browser/build load;
  validate GPU headroom without taking ownership of the operator's existing processes.
- Review long Product Loop timeout budgets against the complete final quality gate. Preserve full
  delivery checks; iteration tiers are not delivery approval.

## P2: only after demand and measurements

- Multi-host/distributed coordination and a different persistence/queue architecture if a single host
  genuinely becomes the bottleneck.
- Adaptive budgets or extra heavy concurrency only with measured interactive responsiveness,
  cancellation reliability, resource accounting and a rollback policy.
- Stronger OS privilege isolation where the threat model needs more than Job Object containment.

No P1/P2 feature is implied by the existence of this backlog.
