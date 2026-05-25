# AIDO Architecture Diagrams

## Diagrama 1 - arquitectura actual

```mermaid
flowchart LR
    UI["React/Vite Control Center"] --> API["FastAPI API v1"]
    API --> Runtime["ControlCenterRuntime"]
    Runtime --> DB["SQLite local store"]
    Runtime --> Bus["EventBus and audit"]
    Runtime --> Telemetry["Local telemetry"]
    API --> Jobs["Jobs and approvals"]
    API --> Workflows["Workflow runner"]
    API --> Agents["Agent profiles and runs"]
    API --> Policy["Security policy engine"]
    API --> Workspaces["Workspaces and git worktrees"]
    API --> Evidence["Evidence, QA, artifacts"]
    API --> Gateway["Unified Model & Runtime Gateway"]
    Gateway --> Providers["API/local/gateway providers"]
    Gateway --> CLIs["CLI runtimes"]
    Gateway --> Ledger["Usage ledger and cost read-model"]
```

The current architecture is already a local-first control plane with API,
policy, workflow, evidence, workspace and gateway slices.

## Diagrama 2 - arquitectura objetivo

```mermaid
flowchart TD
    Idea["Idea or requirement"] --> Discovery["Project discovery"]
    Discovery --> Charter["Project charter"]
    Charter --> Backlog["Backlog, epics and stories"]
    Backlog --> ADR["Architecture and ADR"]
    ADR --> Plan["Execution plan"]
    Plan --> Workspace["Isolated workspace"]
    Workspace --> Contract["Executable agent contract"]
    Contract --> Route["ModelRouter decision"]
    Route --> Approval{"Approval needed?"}
    Approval -- "yes" --> Human["Human approval"]
    Approval -- "no" --> Execute["API provider or CLI runtime"]
    Human --> Execute
    Execute --> Diff["Diff and artifacts"]
    Diff --> Tests["Tests and QA evidence"]
    Tests --> Review["Technical and security review"]
    Review --> PR["PR or integration"]
    PR --> Release["Controlled release"]
    Release --> Retro["Retrospective"]
    Retro --> Memory["Memory and benchmarks"]
    Memory --> Route
```

The target is not an agent chat room. It is an SDLC control plane that routes
work through contracts, policies, budgets, quotas, evidence and audit.

## Diagrama 3 - decision de modelo/runtime

```mermaid
flowchart LR
    Request["Routing request"] --> HardFilters["Hard filters"]
    HardFilters --> Policy["Role model policy"]
    HardFilters --> Privacy["Privacy and modality"]
    HardFilters --> Budget["BudgetRuleEvaluator"]
    HardFilters --> Quota["QuotaManager"]
    Policy --> Score["Weighted score"]
    Privacy --> Score
    Budget --> Score
    Quota --> Score
    Catalog["Model catalog"] --> Score
    Health["Provider health"] --> Score
    Score --> Decision["routing_decisions"]
    Decision --> Preview["route preview response"]
    Preview --> Selected["selected provider/model/runtime"]
    Preview --> Rejected["rejected candidates"]
    Preview --> Results["policyResult, budgetResult, quotaResult"]
```

The router first applies hard filters and only then scores candidates. The
default mode remains `balanced_best_value`, not `max_performance`.

## Diagrama 4 - ejecucion segura CLI

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI
    participant Router as ModelRouter
    participant Runtime as CliRuntime
    participant Policy as SecurityPolicy
    participant DB as SQLite
    participant Sandbox as RestrictedSubprocessSandbox

    User->>API: route execute or runtime request
    API->>Router: preview and record decision
    Router->>DB: budget, quota, provider, model, role policy
    API->>Runtime: RuntimeRequest
    Runtime->>Runtime: validate workspace and dangerous flags
    Runtime->>Policy: evaluate command and env policy
    alt disabled or policy blocked
        Runtime->>DB: cli_sessions status blocked
        Runtime->>DB: usage_ledger estimated
    else allowed and enabled
        Runtime->>Sandbox: execute inside workspace
        Sandbox-->>Runtime: stdout, stderr, return code
        Runtime->>DB: cli_sessions completed/failed
        Runtime->>DB: usage_ledger actual or estimated
    end
```

Real CLI execution is fail-closed by default. Mock execution can persist session
and usage records without invoking external CLIs.
