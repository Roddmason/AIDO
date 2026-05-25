# Workspace And Project Foundation

## What It Does

AIDO treats a workspace base as an operator-owned folder that can contain many projects. A project can either attach to an existing folder or be created as a new child directory under that workspace base. Each project can carry detected runtime metadata for monorepo and multi-runtime scenarios such as frontend, backend, JVM, Python, Node, Rust and Terraform/infra.

This is intentionally separate from runtime workspaces in `workspaces`, which are task-owned isolated working trees allocated to agents.

## Configuration And UX

The Settings project wizard supports two modes:

- New project under workspace base: the user selects or types a base path and provides a project directory name.
- Attach existing project path: the user selects or types an existing project folder.

The native directory picker is brokered by the local Python process because browser directory pickers do not expose absolute OS paths reliably. If the host cannot open a desktop dialog, the UI keeps a manual path fallback.

## Endpoints

- `POST /api/v1/projects/discover`
  - Protected by the local write token.
  - Reads manifests from the provided path.
  - Does not execute commands.

- `POST /api/v1/local-paths/select-directory`
  - Protected by the local write token.
  - Opens a native directory picker when `tkinter` and a desktop session are available.
  - Returns `unavailable` or `cancelled` instead of failing open.

- `POST /api/v1/projects`
  - Existing contract remains compatible.
  - New optional fields: `workspaceBasePath` and `projectDirectoryName`.

## Tables

No new table was added in this increment. Project-level workspace metadata is stored in `projects.metadata`:

- `workspaceBasePath`
- `projectDirectoryName`
- `creationMode`
- `detectedRuntimes`
- `manifestSources`

## Security

Discovery and native path selection require the local write token because both expose filesystem-derived information. Discovery reads bounded manifest content and never invokes CLIs, package managers or build tools. Metadata and audit payloads pass through redaction before persistence.

## Tests

- `tests_py/test_project_workspace_foundation.py`
- `tests_web/control-center.spec.js`

Covered behavior:

- Manifest-based name discovery.
- Runtime detection for Node, Python, Maven and Terraform.
- Token protection for discovery and native directory picker.
- Project creation under a workspace base.
- Wizard validation and mobile/desktop rendering.

## Limitations

The current model records workspace base metadata on projects. A dedicated `workspace_roots` table is still the cleaner long-term model if teams must be assigned at the workspace level independently from projects. Native folder selection depends on desktop availability; headless Linux should use manual path entry.
