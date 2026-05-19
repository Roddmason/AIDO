# Frontend Architecture

The current React dashboard remains active while the backend foundation is
stabilized. The next frontend migration should be TypeScript-first and should
not start until workflows, policy decisions, evidence, agents, and model gateway
endpoints expose real data.

Current JavaScript dashboard coverage now includes:

- Overview
- Jobs/Approvals
- Memory/Retrieval
- Agents Runtime
- Sandbox/Security
- Workflows
- Governance
- Pipelines
- Workspaces/Sessions
- Evidence & QA
- Integrations
- Design Lab
- Settings

## Target Structure

```text
local-control-center/web/
├── index.html
├── src/
│   ├── main.tsx
│   ├── app/
│   │   ├── App.tsx
│   │   ├── router.tsx
│   │   ├── providers.tsx
│   │   └── error-boundary.tsx
│   ├── api/
│   │   ├── client.ts
│   │   ├── generated/
│   │   ├── events.ts
│   │   └── types.ts
│   ├── design-system/
│   │   ├── tokens.css
│   │   ├── base.css
│   │   ├── layout.css
│   │   ├── components.css
│   │   └── motion.css
│   ├── components/
│   │   ├── shell/
│   │   ├── data/
│   │   ├── forms/
│   │   ├── feedback/
│   │   ├── charts/
│   │   └── primitives/
│   ├── features/
│   │   ├── overview/
│   │   ├── command-center/
│   │   ├── workflows/
│   │   ├── jobs-approvals/
│   │   ├── agents/
│   │   ├── model-gateway/
│   │   ├── policy-security/
│   │   ├── workspaces/
│   │   ├── memory/
│   │   ├── evidence/
│   │   ├── governance/
│   │   ├── integrations/
│   │   ├── settings/
│   │   └── audit/
│   ├── stores/
│   ├── hooks/
│   ├── lib/
│   └── test/
├── vite.config.ts
├── tsconfig.json
└── package.json
```

## Migration Rules

- Keep the existing dashboard operational until the TypeScript shell passes
  Playwright smoke tests.
- Migrate by feature slice, not by visual page rewrite.
- Do not move business rules into React. The UI renders backend state and sends
  explicit commands.
- Use local fonts only; no CDN dependencies.
- Preserve keyboard navigation, visible focus, reduced motion, and status text
  that does not rely on color alone.

## Visual Direction

Editorial operational control room: graphite, parchment, off-white, muted moss,
amber, and oxblood. Avoid neon terminal styling, liquid glass, heavy gradients,
and generic SaaS dashboards.
