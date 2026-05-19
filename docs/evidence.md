# Evidence And QA

Evidence packages are required to make QA decisions auditable.

## Implemented Foundation

- `evidence_packages`
- `test_results`
- `qa_verdicts`
- `artifacts` table reserved for future file/screenshot/log references

## QA Gate

A package cannot be marked `passed` unless it includes at least one evidence
signal:

- test results
- diff references
- screenshot references

This prevents a reviewer or agent from approving work without recorded proof.

## Next Steps

- Ingest JUnit/pytest output directly.
- Capture diff metadata from allocated workspaces.
- Link screenshots and logs as artifacts.
- Require evidence package IDs in technical review outputs.
