# Governance

Governance turns architecture decisions, risks, and next steps into platform
state instead of informal project notes. The goal is traceability: a technical
decision can point to the risks it accepts, and a risk can point to the next
work required to reduce it.

## Entities

- `architecture_decisions`: ADR-style records with status, context, decision,
  consequences, linked risks, and linked next steps.
- `risk_register`: open, monitoring, mitigating, mitigated, accepted, and
  closed risks. High and critical risks require a mitigation at creation time.
- `next_steps`: prioritized execution items linked to risks or architecture
  decisions.

## API

- `GET /api/v1/governance`
- `GET /api/v1/architecture-decisions`
- `POST /api/v1/architecture-decisions`
- `GET /api/v1/risks`
- `POST /api/v1/risks`
- `PATCH /api/v1/risks/{risk_id}`
- `GET /api/v1/next-steps`
- `POST /api/v1/next-steps`
- `PATCH /api/v1/next-steps/{step_id}`

All mutating routes require the loopback write token from
`/api/v1/security/handshake`.

## Rules

- Accepted architecture decisions require both `context` and `decision`.
- Architecture decision status is limited to `proposed`, `accepted`,
  `rejected`, `superseded`, and `deprecated`.
- Risk severity is limited to `low`, `medium`, `high`, and `critical`.
- Next-step status and priority use explicit enums rather than free text.
- High and critical risks require a non-empty `mitigation`.
- Risk and next-step updates write audit events.
- Policy gated actions, failed/blocked QA verdicts, and cancelled workflows
  create risk-register entries automatically.
- The dashboard reads governance state from `/api/v1/overview` and does not
  invent client-only state.

## Current Limitations

- Governance forms are intentionally strict and still basic; most rich editing
  happens through API-backed records rendered in the dashboard.
- Remaining cleanup is ordinary UX depth: richer edit forms and filtering. The
  old store-based fixture harness has been removed; product overview
  aggregation lives in `control_plane`.
