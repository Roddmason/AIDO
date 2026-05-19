# Agents

Agents are modeled as contracts, not personalities.

## Implemented Foundation

- `agent_profiles`: role, runtime type, tools, skills, permission profile, and
  quality gates.
- `agent_runs`: structured run input/output.
- `agent_tool_calls`: tool-call trace records.
- `model_calls`: model-call trace records.
- `cost_usage`: local cost ledger.
- `skills` and `skill_versions`: versionable skill registry loaded from local
  `SKILL.md` files.

## Runtime Types

- `internal_mock`: implemented and used by tests.
- `internal_llm`: planned.
- `openhands`: optional adapter planned.
- `swe_agent`: optional adapter planned.
- `manual`: planned.

Optional runtimes must not break local installation when unavailable.

## Structured Output

Agent runs should return structured JSON with verdict, summary, evidence
references, risks, and next actions. Free-form text is insufficient for the
control plane.
