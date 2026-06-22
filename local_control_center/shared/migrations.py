"""Esquema DDL idempotente de la plataforma, aplicado por fases incrementales.

Crea tablas/índices y siembra catálogos por defecto (proveedores, modelos, políticas
de ruteo, presupuestos) de forma reentrante: usa ``CREATE TABLE IF NOT EXISTS``,
``ALTER TABLE`` condicional e ``INSERT OR IGNORE``, registrando cada fase en
``schema_migrations``. Cada ``init_phaseN_schema`` se ejecuta como su propia unidad DDL;
re-ejecutar todo el conjunto sobre una base ya migrada no produce cambios.
"""

from __future__ import annotations

import sqlite3

from .serialization import json_dumps
from .time import utc_now


def initialize_platform_schema(connection: sqlite3.Connection) -> None:
    """Aplica todas las fases del esquema en orden y siembra los catálogos de la plataforma."""
    init_base_schema(connection)
    init_phase2_schema(connection)
    init_phase3_schema(connection)
    init_phase4_schema(connection)
    init_phase5_schema(connection)
    init_phase6_schema(connection)
    init_phase7_schema(connection)
    init_phase8_schema(connection)
    init_phase9_schema(connection)
    init_phase10_schema(connection)
    init_phase11_schema(connection)
    init_phase12_schema(connection)
    init_phase13_schema(connection)
    init_phase14_schema(connection)
    init_phase15_schema(connection)
    init_phase16_schema(connection)
    init_phase17_schema(connection)
    init_phase18_schema(connection)
    init_phase19_schema(connection)
    init_phase20_schema(connection)
    seed_platform_catalogs(connection)


def init_base_schema(connection: sqlite3.Connection) -> None:
    """Fase 1: crea el núcleo (proyectos, agentes, jobs, eventos, auditoría, memoria, MCP)."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            template_id TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teams (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            kind TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            permissions TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            team_id TEXT,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pipelines (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            chat_id TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            stages TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            models TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mode TEXT NOT NULL,
            body TEXT NOT NULL,
            optimizer TEXT NOT NULL,
            applies_to TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id TEXT PRIMARY KEY,
            prompt_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            body TEXT NOT NULL,
            mode TEXT NOT NULL,
            optimizer TEXT NOT NULL,
            applies_to TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_items (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            version INTEGER NOT NULL,
            hash TEXT NOT NULL,
            supersedes_id TEXT,
            created_by_run_id TEXT,
            valid_from TEXT NOT NULL,
            expires_at TEXT,
            deleted_at TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_item_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            embedding_json TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            provider_id TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            summary TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            project_id TEXT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            target TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ide_connections (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            editor TEXT NOT NULL,
            workspace_root TEXT NOT NULL,
            status TEXT NOT NULL,
            open_files TEXT NOT NULL,
            diagnostics TEXT NOT NULL,
            selection TEXT NOT NULL,
            terminal_context TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS integrations (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            transport TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mcp_tool_calls (
            id TEXT PRIMARY KEY,
            mcp_server_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_requests (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            status TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            command TEXT NOT NULL,
            payload TEXT NOT NULL,
            reason TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            expires_at TEXT NOT NULL DEFAULT '',
            decided_at TEXT,
            decided_by TEXT
        );
        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            job_id TEXT,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            status TEXT NOT NULL,
            input TEXT NOT NULL,
            output TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_tool_calls (
            id TEXT PRIMARY KEY,
            agent_run_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status_lease
            ON jobs(status, lease_expires_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_events_project_created
            ON events(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_memory_project_scope_hash
            ON memory_items(project_id, scope, hash);
        CREATE INDEX IF NOT EXISTS idx_action_requests_job_status
            ON action_requests(job_id, status);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (1, utc_now()),
    )


def init_phase2_schema(connection: sqlite3.Connection) -> None:
    """Fase 2: añade workflows, permisos, evidencia y costos; siembra políticas de permiso base."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            metadata TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id TEXT PRIMARY KEY,
            workflow_run_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            agent_profile_id TEXT,
            input TEXT NOT NULL,
            output TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_edges (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            from_step_id TEXT NOT NULL,
            to_step_id TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_events (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            workflow_run_id TEXT,
            step_id TEXT,
            project_id TEXT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL,
            correlation_id TEXT,
            causation_id TEXT
        );
        CREATE TABLE IF NOT EXISTS permission_policies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            profile TEXT NOT NULL,
            rules TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS permission_decisions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            workspace_id TEXT,
            agent_id TEXT,
            role TEXT,
            tool TEXT,
            command TEXT,
            path TEXT,
            decision TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_packages (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            agent_id TEXT,
            agent_run_id TEXT,
            job_id TEXT,
            workspace_id TEXT,
            runtime_id TEXT,
            task_id TEXT NOT NULL,
            test_plan TEXT NOT NULL,
            acceptance_checklist TEXT NOT NULL,
            test_results TEXT NOT NULL,
            logs TEXT NOT NULL,
            diff_refs TEXT NOT NULL,
            screenshot_refs TEXT NOT NULL,
            risk_notes TEXT NOT NULL,
            artifact_ids TEXT NOT NULL DEFAULT '[]',
            diff_summary TEXT NOT NULL DEFAULT '{}',
            runtime_health TEXT NOT NULL DEFAULT '{}',
            model_calls TEXT NOT NULL DEFAULT '[]',
            tool_calls TEXT NOT NULL DEFAULT '[]',
            policy_decisions TEXT NOT NULL DEFAULT '[]',
            approvals TEXT NOT NULL DEFAULT '[]',
            artifact_refs TEXT NOT NULL DEFAULT '[]',
            hashes TEXT NOT NULL DEFAULT '{}',
            evidence_source TEXT NOT NULL DEFAULT 'operator_attested',
            qa_verdict TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            runtime_type TEXT NOT NULL,
            model_policy_id TEXT,
            allowed_skills TEXT NOT NULL,
            allowed_tools TEXT NOT NULL,
            permission_profile TEXT NOT NULL,
            memory_scope TEXT NOT NULL,
            max_cost_per_run REAL NOT NULL,
            max_runtime_seconds INTEGER NOT NULL,
            output_schema TEXT NOT NULL,
            quality_gates TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_policies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            preferred TEXT NOT NULL,
            fallback TEXT NOT NULL,
            max_cost_usd REAL NOT NULL,
            max_tokens INTEGER NOT NULL,
            temperature REAL NOT NULL,
            allow_remote INTEGER NOT NULL,
            allow_local INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_run_id TEXT,
            model_policy_id TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cost_usage (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            scope TEXT NOT NULL,
            amount_usd REAL NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workflows_project_status
            ON workflows(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_status
            ON workflow_runs(workflow_id, status);
        CREATE INDEX IF NOT EXISTS idx_permission_decisions_project_created
            ON permission_decisions(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_evidence_project_created
            ON evidence_packages(project_id, created_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (2, utc_now()),
    )
    timestamp = utc_now()
    default_policies = [
        (
            "plan",
            "Plan",
            "plan",
            [{"tool": "filesystem", "effect": "read"}, {"tool": "shell", "effect": "deny"}],
        ),
        ("dev_safe", "Dev Safe", "dev_safe", [{"tool": "shell", "effect": "approval_required"}]),
        ("release", "Release", "release", [{"tool": "deploy_prod", "effect": "human_required"}]),
    ]
    for policy_id, name, profile, rules in default_policies:
        connection.execute(
            """
            INSERT OR IGNORE INTO permission_policies
                (id, name, profile, rules, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (policy_id, name, profile, json_dumps(rules), timestamp, timestamp),
        )


def init_phase3_schema(connection: sqlite3.Connection) -> None:
    """Fase 3: añade workspaces, git/PR, skills, artefactos y QA; siembra proveedores de modelo."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            owner_agent_id TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            isolation_type TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS workspace_allocations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT
        );
        CREATE TABLE IF NOT EXISTS workspace_files (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            path TEXT NOT NULL,
            role TEXT NOT NULL,
            hash TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspace_sessions (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            session_id TEXT,
            agent_id TEXT,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS git_branches (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            project_id TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            base_branch TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pull_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            project_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            license TEXT NOT NULL,
            compatibility TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skill_versions (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            version TEXT NOT NULL,
            instructions_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skill_bindings (
            id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            agent_profile_id TEXT,
            workflow_id TEXT,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            evidence_package_id TEXT,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            hash TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_results (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            evidence_package_id TEXT,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            output_ref TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qa_verdicts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            evidence_package_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_providers (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            allow_remote INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _add_column_if_missing(connection, "workspaces", "task_id", "task_id TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(connection, "workspaces", "status", "status TEXT NOT NULL DEFAULT 'active'")
    _add_column_if_missing(connection, "workspace_allocations", "task_id", "task_id TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(
        connection, "workspace_allocations", "status", "status TEXT NOT NULL DEFAULT 'allocated'"
    )
    _add_column_if_missing(connection, "evidence_packages", "workflow_step_id", "workflow_step_id TEXT")
    _add_column_if_missing(connection, "evidence_packages", "agent_run_id", "agent_run_id TEXT")
    _add_column_if_missing(connection, "evidence_packages", "job_id", "job_id TEXT")
    _add_column_if_missing(connection, "evidence_packages", "workspace_id", "workspace_id TEXT")
    _add_column_if_missing(connection, "evidence_packages", "runtime_id", "runtime_id TEXT")
    _add_column_if_missing(
        connection, "evidence_packages", "artifact_ids", "artifact_ids TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection, "evidence_packages", "diff_summary", "diff_summary TEXT NOT NULL DEFAULT '{}'"
    )
    _add_column_if_missing(
        connection, "evidence_packages", "runtime_health", "runtime_health TEXT NOT NULL DEFAULT '{}'"
    )
    _add_column_if_missing(
        connection, "evidence_packages", "model_calls", "model_calls TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection, "evidence_packages", "tool_calls", "tool_calls TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection,
        "evidence_packages",
        "policy_decisions",
        "policy_decisions TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        connection, "evidence_packages", "approvals", "approvals TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection, "evidence_packages", "artifact_refs", "artifact_refs TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(connection, "evidence_packages", "hashes", "hashes TEXT NOT NULL DEFAULT '{}'")
    _add_column_if_missing(
        connection,
        "evidence_packages",
        "evidence_source",
        "evidence_source TEXT NOT NULL DEFAULT 'operator_attested'",
    )
    _add_column_if_missing(connection, "action_requests", "expires_at", "expires_at TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(connection, "test_results", "status", "status TEXT NOT NULL DEFAULT 'unknown'")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_workspaces_task_active
            ON workspaces(project_id, task_id, status);
        CREATE INDEX IF NOT EXISTS idx_workspace_allocations_workspace
            ON workspace_allocations(workspace_id, status);
        CREATE INDEX IF NOT EXISTS idx_test_results_evidence
            ON test_results(evidence_package_id, status);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (3, utc_now()),
    )
    timestamp = utc_now()
    model_providers = [
        ("ollama", "ollama", "Ollama", "optional", 0, {"runtime": "local"}),
        (
            "openai_compatible",
            "openai_compatible",
            "OpenAI-compatible API",
            "optional",
            1,
            {"runtime": "api"},
        ),
        ("openrouter", "openrouter", "OpenRouter", "optional", 1, {"runtime": "api"}),
        ("openai_agents", "openai_agents", "OpenAI Agents SDK", "optional", 1, {"runtime": "api"}),
        ("cli_codex", "cli_codex", "Codex CLI", "optional", 0, {"runtime": "cli"}),
        ("cli_claude", "cli_claude", "Claude Code CLI", "optional", 0, {"runtime": "cli"}),
        ("manual", "manual", "Manual Operator", "optional", 0, {"runtime": "manual"}),
    ]
    for provider_id, provider, label, status, allow_remote, metadata in model_providers:
        connection.execute(
            """
            INSERT OR IGNORE INTO model_providers
                (id, provider, label, status, allow_remote, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                provider,
                label,
                status,
                allow_remote,
                json_dumps(metadata),
                timestamp,
                timestamp,
            ),
        )


def init_phase4_schema(connection: sqlite3.Connection) -> None:
    """Fase 4: vincula workspaces a workflow run/step y añade su índice por run."""
    workspace_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
    }
    if "workflow_run_id" not in workspace_columns:
        connection.execute("ALTER TABLE workspaces ADD COLUMN workflow_run_id TEXT")
    if "workflow_step_id" not in workspace_columns:
        connection.execute("ALTER TABLE workspaces ADD COLUMN workflow_step_id TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_workspaces_workflow_run ON workspaces(workflow_run_id)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (4, utc_now()),
    )


def init_phase5_schema(connection: sqlite3.Connection) -> None:
    """Fase 5: añade gobierno de proyecto (decisiones de arquitectura, riesgos, próximos pasos)."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS architecture_decisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            context TEXT NOT NULL,
            decision TEXT NOT NULL,
            consequences TEXT NOT NULL,
            linked_risk_ids TEXT NOT NULL,
            next_step_ids TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS risk_register (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            description TEXT NOT NULL,
            mitigation TEXT NOT NULL,
            owner TEXT NOT NULL,
            evidence_refs TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS next_steps (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            source_risk_id TEXT,
            source_decision_id TEXT,
            owner TEXT NOT NULL,
            due_at TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_architecture_decisions_project_status
            ON architecture_decisions(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_risk_register_project_status
            ON risk_register(project_id, status, severity);
        CREATE INDEX IF NOT EXISTS idx_next_steps_project_status
            ON next_steps(project_id, status, priority);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (5, utc_now()),
    )


def init_phase6_schema(connection: sqlite3.Connection) -> None:
    """Fase 6: vincula jobs y agent runs a workflow run/step y añade sus índices por run."""
    job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    if "workflow_run_id" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN workflow_run_id TEXT")
    if "workflow_step_id" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN workflow_step_id TEXT")

    agent_run_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()
    }
    if "workflow_run_id" not in agent_run_columns:
        connection.execute("ALTER TABLE agent_runs ADD COLUMN workflow_run_id TEXT")
    if "workflow_step_id" not in agent_run_columns:
        connection.execute("ALTER TABLE agent_runs ADD COLUMN workflow_step_id TEXT")

    connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_workflow_run ON jobs(workflow_run_id)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow_run ON agent_runs(workflow_run_id)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (6, utc_now()),
    )


def init_phase7_schema(connection: sqlite3.Connection) -> None:
    """Fase 7: asegura las tablas de integraciones y de servidores/llamadas MCP con sus índices."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS integrations (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            transport TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mcp_tool_calls (
            id TEXT PRIMARY KEY,
            mcp_server_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mcp_servers_status ON mcp_servers(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_mcp_tool_calls_server_created ON mcp_tool_calls(mcp_server_id, created_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (7, utc_now()),
    )


def init_phase8_schema(connection: sqlite3.Connection) -> None:
    """Fase 8: añade los grants de permiso emitidos contra action requests aprobados."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS permission_grants (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            job_id TEXT,
            action_request_id TEXT NOT NULL,
            permission_decision_id TEXT,
            agent_id TEXT,
            tool TEXT NOT NULL,
            command TEXT NOT NULL,
            path TEXT,
            command_argv TEXT NOT NULL DEFAULT '[]',
            workspace_id TEXT,
            runtime_id TEXT,
            expires_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            granted_by TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            consumed_at TEXT,
            consumed_by_agent_run_id TEXT,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_permission_grants_action
            ON permission_grants(action_request_id, status);
        CREATE INDEX IF NOT EXISTS idx_permission_grants_project_status
            ON permission_grants(project_id, status, granted_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (8, utc_now()),
    )


def init_phase9_schema(connection: sqlite3.Connection) -> None:
    """Fase 9: añade los perfiles de sandbox y siembra el perfil Docker por defecto."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sandbox_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            allowed_images TEXT NOT NULL,
            allowed_networks TEXT NOT NULL,
            default_network TEXT NOT NULL,
            memory TEXT NOT NULL,
            cpus TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sandbox_profiles_status
            ON sandbox_profiles(status, updated_at);
        """
    )
    timestamp = utc_now()
    connection.execute(
        """
        INSERT OR IGNORE INTO sandbox_profiles
            (id, name, allowed_images, allowed_networks, default_network, memory,
             cpus, timeout_seconds, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "default_docker",
            "Default Docker Sandbox",
            json_dumps(
                [
                    "python:3.13-slim",
                    "python:3.12-slim",
                    "node:22-alpine",
                    "debian:bookworm-slim",
                    "ubuntu:24.04",
                ]
            ),
            json_dumps(["none"]),
            "none",
            "2g",
            "2",
            120,
            "active",
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (9, utc_now()),
    )


def init_phase10_schema(connection: sqlite3.Connection) -> None:
    """Fase 10: backfill de columnas de expiración/revocación en grants y perfiles de sandbox."""
    _add_column_if_missing(connection, "action_requests", "expires_at", "expires_at TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(
        connection, "permission_grants", "command_argv", "command_argv TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(connection, "permission_grants", "workspace_id", "workspace_id TEXT")
    _add_column_if_missing(connection, "permission_grants", "runtime_id", "runtime_id TEXT")
    _add_column_if_missing(
        connection, "permission_grants", "expires_at", "expires_at TEXT NOT NULL DEFAULT ''"
    )
    grant_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(permission_grants)").fetchall()
    }
    if "revoked_at" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoked_at TEXT")
    if "revoked_by" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoked_by TEXT")
    if "revoke_reason" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoke_reason TEXT")

    sandbox_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(sandbox_profiles)").fetchall()
    }
    if "revoked_at" not in sandbox_columns:
        connection.execute("ALTER TABLE sandbox_profiles ADD COLUMN revoked_at TEXT")
    if "revoked_by" not in sandbox_columns:
        connection.execute("ALTER TABLE sandbox_profiles ADD COLUMN revoked_by TEXT")
    if "revoke_reason" not in sandbox_columns:
        connection.execute("ALTER TABLE sandbox_profiles ADD COLUMN revoke_reason TEXT")

    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (10, utc_now()),
    )


def init_phase11_schema(connection: sqlite3.Connection) -> None:
    """Fase 11: añade el historial versionado de revisiones de política (auditoría de cambios)."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS policy_revisions (
            id TEXT PRIMARY KEY,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            previous_json TEXT NOT NULL,
            updated_json TEXT NOT NULL,
            changed_fields TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(subject_type, subject_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_policy_revisions_subject
            ON policy_revisions(subject_type, subject_id, version);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (11, utc_now()),
    )


def _add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _remove_legacy_simulation_runtime_records(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE model_providers
        SET status = 'optional', updated_at = ?
        WHERE id = 'manual' AND status = 'available'
        """,
        (utc_now(),),
    )
    connection.execute(
        """
        DELETE FROM provider_accounts
        WHERE provider_type = 'local'
          AND api_format = 'custom'
          AND base_url = ''
          AND credential_ref = ''
          AND enabled = 1
          AND quota_mode = 'none'
          AND health_status = 'healthy'
        """
    )
    connection.execute(
        """
        DELETE FROM model_catalog
        WHERE source = 'manual_seed'
          AND provider_id NOT IN (SELECT provider_id FROM provider_accounts)
        """
    )
    connection.execute(
        """
        DELETE FROM model_providers
        WHERE status = 'available'
          AND allow_remote = 0
          AND metadata LIKE ?
        """,
        ('%"runtime": "test"%',),
    )
    connection.execute(
        """
        DELETE FROM runtime_capabilities
        WHERE runtime NOT IN (SELECT provider_id FROM provider_accounts)
        """
    )


def init_phase12_schema(connection: sqlite3.Connection) -> None:
    """Fase 12: instala el subsistema de ruteo de modelos (cuentas, catálogo, políticas por rol,
    ledger de uso, límites, decisiones) y siembra sus catálogos por defecto."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS provider_accounts (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            api_format TEXT NOT NULL,
            base_url TEXT,
            credential_ref TEXT,
            enabled INTEGER NOT NULL,
            quota_mode TEXT NOT NULL,
            health_status TEXT NOT NULL,
            last_health_check_at TEXT,
            last_error TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_catalog (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            display_name TEXT NOT NULL,
            model_family TEXT NOT NULL,
            context_window INTEGER NOT NULL,
            max_output_tokens INTEGER NOT NULL,
            supports_tools INTEGER NOT NULL,
            supports_json INTEGER NOT NULL,
            supports_streaming INTEGER NOT NULL,
            supports_vision INTEGER NOT NULL,
            supports_embeddings INTEGER NOT NULL,
            supports_rerank INTEGER NOT NULL,
            supports_reasoning INTEGER NOT NULL,
            supports_thinking INTEGER NOT NULL,
            effort_levels_json TEXT NOT NULL,
            input_price_per_mtok REAL,
            cached_input_price_per_mtok REAL,
            output_price_per_mtok REAL,
            reasoning_price_per_mtok REAL,
            free_tier INTEGER NOT NULL,
            free_tier_notes TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(provider_id, model)
        );
        CREATE TABLE IF NOT EXISTS routing_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL,
            objective TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS role_model_policies (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL UNIQUE,
            routing_profile_id TEXT NOT NULL,
            preferred_json TEXT NOT NULL,
            fallback_json TEXT NOT NULL,
            escalation_json TEXT NOT NULL,
            blocked_json TEXT NOT NULL,
            max_cost_per_task_usd REAL NOT NULL,
            max_tokens_per_run INTEGER NOT NULL,
            requires_approval_over_usd REAL,
            requires_approval_for_reasoning_max INTEGER NOT NULL,
            allow_remote INTEGER NOT NULL,
            allow_local INTEGER NOT NULL,
            allow_cli INTEGER NOT NULL,
            allow_api INTEGER NOT NULL,
            allow_unknown_cost INTEGER NOT NULL DEFAULT 1,
            require_approval_for_unknown_cost INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_ledger (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            runtime_type TEXT NOT NULL,
            agent_id TEXT,
            role TEXT,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            job_id TEXT,
            task_id TEXT,
            request_id TEXT,
            session_id TEXT,
            input_tokens INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            reasoning_tokens INTEGER NOT NULL,
            tool_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            currency TEXT NOT NULL,
            latency_ms INTEGER,
            raw_usage_json TEXT NOT NULL,
            usage_source TEXT NOT NULL DEFAULT 'estimated',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_limits (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            rpm INTEGER,
            tpm INTEGER,
            daily_requests INTEGER,
            daily_tokens INTEGER,
            monthly_requests INTEGER,
            monthly_tokens INTEGER,
            monthly_budget_usd REAL,
            current_window_json TEXT NOT NULL,
            cooldown_until TEXT,
            last_429_at TEXT,
            last_limit_error_at TEXT,
            unknown_limit_strategy TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(provider_id, model)
        );
        CREATE TABLE IF NOT EXISTS routing_decisions (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            task_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            selected_provider TEXT,
            selected_model TEXT,
            selected_runtime TEXT,
            selected_effort TEXT,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            agent_id TEXT,
            job_id TEXT,
            task_id TEXT,
            estimated_cost_usd REAL,
            estimated_tokens INTEGER,
            candidates_json TEXT NOT NULL,
            rejected_json TEXT NOT NULL,
            decision_reason TEXT NOT NULL,
            score_breakdown_json TEXT NOT NULL,
            policy_result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cli_sessions (
            id TEXT PRIMARY KEY,
            runtime TEXT NOT NULL,
            executable TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            agent_id TEXT,
            command_json TEXT NOT NULL,
            env_policy_json TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            usage_ledger_id TEXT,
            stdout_artifact_id TEXT,
            stderr_artifact_id TEXT,
            logs_artifact_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runtime_capabilities (
            id TEXT PRIMARY KEY,
            runtime TEXT NOT NULL,
            capability TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(runtime, capability)
        );
        CREATE TABLE IF NOT EXISTS provider_health_checks (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS budget_rules (
            id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL,
            scope_id TEXT,
            max_cost_usd REAL,
            max_tokens INTEGER,
            period TEXT NOT NULL,
            action_on_exceed TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_benchmarks (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            role TEXT,
            tasks_attempted INTEGER NOT NULL,
            success_rate REAL,
            qa_pass_rate REAL,
            avg_cost REAL,
            avg_latency_ms INTEGER,
            rework_rate REAL,
            last_used_at TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_ledger_provider_created
            ON usage_ledger(provider_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_routing_decisions_role_created
            ON routing_decisions(role, created_at);
        CREATE INDEX IF NOT EXISTS idx_cli_sessions_runtime_status
            ON cli_sessions(runtime, status, created_at);
        """
    )
    _add_column_if_missing(connection, "agent_profiles", "routing_profile_id", "routing_profile_id TEXT")
    _add_column_if_missing(connection, "agent_profiles", "role_model_policy_id", "role_model_policy_id TEXT")
    _add_column_if_missing(
        connection, "agent_profiles", "allowed_providers", "allowed_providers TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection, "agent_profiles", "allowed_runtimes", "allowed_runtimes TEXT NOT NULL DEFAULT '[]'"
    )
    _add_column_if_missing(
        connection, "agent_profiles", "max_tokens_per_run", "max_tokens_per_run INTEGER NOT NULL DEFAULT 0"
    )
    _add_column_if_missing(
        connection, "agent_profiles", "allow_remote", "allow_remote INTEGER NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(connection, "agent_profiles", "allow_cli", "allow_cli INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(connection, "agent_profiles", "allow_api", "allow_api INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(
        connection, "agent_profiles", "requires_approval_over_usd", "requires_approval_over_usd REAL"
    )
    _add_column_if_missing(
        connection,
        "role_model_policies",
        "allow_unknown_cost",
        "allow_unknown_cost INTEGER NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(
        connection,
        "role_model_policies",
        "require_approval_for_unknown_cost",
        "require_approval_for_unknown_cost INTEGER NOT NULL DEFAULT 1",
    )
    _add_column_if_missing(connection, "routing_decisions", "workflow_run_id", "workflow_run_id TEXT")
    _add_column_if_missing(connection, "routing_decisions", "workflow_step_id", "workflow_step_id TEXT")
    _add_column_if_missing(connection, "routing_decisions", "agent_id", "agent_id TEXT")
    _add_column_if_missing(connection, "routing_decisions", "job_id", "job_id TEXT")
    _add_column_if_missing(connection, "routing_decisions", "task_id", "task_id TEXT")
    _add_column_if_missing(
        connection,
        "provider_accounts",
        "metadata_json",
        "metadata_json TEXT NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing(
        connection,
        "usage_ledger",
        "usage_source",
        "usage_source TEXT NOT NULL DEFAULT 'estimated'",
    )
    _add_column_if_missing(connection, "workflow_steps", "role", "role TEXT")
    _add_column_if_missing(connection, "workflow_steps", "task_type", "task_type TEXT")
    _add_column_if_missing(connection, "workflow_steps", "risk_level", "risk_level TEXT")
    _add_column_if_missing(connection, "workflow_steps", "model_mode", "model_mode TEXT")
    _add_column_if_missing(
        connection, "workflow_steps", "manual_model_override", "manual_model_override TEXT"
    )

    timestamp = utc_now()
    provider_accounts = [
        (
            "nvidia_nim",
            "nvidia_nim",
            "NVIDIA NIM / Build",
            "api",
            "openai_compatible",
            "https://integrate.api.nvidia.com/v1",
            "NVIDIA_NIM_API_KEY",
            0,
            "trial_rate_limited",
            "unknown",
        ),
        (
            "ollama",
            "ollama",
            "Ollama Local",
            "local",
            "custom",
            "http://localhost:11434",
            "",
            0,
            "none",
            "unknown",
        ),
        (
            "openai_api",
            "openai_api",
            "OpenAI API",
            "api",
            "responses",
            "https://api.openai.com/v1",
            "OPENAI_API_KEY",
            0,
            "provider_reported",
            "unknown",
        ),
        (
            "anthropic_api",
            "anthropic_api",
            "Anthropic API",
            "api",
            "anthropic",
            "",
            "AIDO_ANTHROPIC_API_KEY",
            0,
            "provider_reported",
            "unknown",
        ),
        (
            "openai_compatible",
            "openai_compatible",
            "OpenAI-compatible API",
            "api",
            "openai_compatible",
            "",
            "OPENAI_API_KEY",
            0,
            "manual",
            "unknown",
        ),
        (
            "openrouter",
            "openrouter",
            "OpenRouter",
            "gateway",
            "openai_compatible",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
            0,
            "provider_reported",
            "unknown",
        ),
        (
            "litellm",
            "litellm",
            "LiteLLM Proxy",
            "gateway",
            "openai_compatible",
            "",
            "LITELLM_API_KEY",
            0,
            "manual",
            "unknown",
        ),
        ("codex_cli", "codex_cli", "Codex CLI", "cli", "cli", "", "", 0, "manual", "unknown"),
        (
            "claude_code_cli",
            "claude_code_cli",
            "Claude Code CLI",
            "cli",
            "cli",
            "",
            "",
            0,
            "manual",
            "unknown",
        ),
        ("openhands", "openhands", "OpenHands", "cli", "cli", "", "", 0, "manual", "unknown"),
        ("swe_agent", "swe_agent", "SWE-agent", "cli", "cli", "", "", 0, "manual", "unknown"),
        ("manual", "manual", "Manual Operator", "manual", "cli", "", "", 1, "none", "healthy"),
    ]
    for row in provider_accounts:
        connection.execute(
            """
            INSERT OR IGNORE INTO provider_accounts
                (id, provider_id, display_name, provider_type, api_format, base_url, credential_ref,
                 enabled, quota_mode, health_status, last_health_check_at, last_error, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', '{}', ?, ?)
            """,
            (*row, timestamp, timestamp),
        )

    model_catalog = [
        (
            "nvidia_nim:auto_best_available",
            "nvidia_nim",
            "auto_best_available",
            "NVIDIA NIM auto best available",
            "nim",
            128000,
            4096,
            0,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            ["low", "medium"],
            None,
            None,
            None,
            None,
            0,
            "unknown_price; provider pricing not configured",
            1,
        ),
        (
            "ollama:local_default",
            "ollama",
            "local_default",
            "Ollama local default",
            "local",
            32000,
            4096,
            0,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            ["low", "medium"],
            0.0,
            0.0,
            0.0,
            0.0,
            1,
            "local runtime cost only",
            1,
        ),
        (
            "openai_compatible:configured_model",
            "openai_compatible",
            "configured_model",
            "Configured OpenAI-compatible model",
            "configured",
            128000,
            4096,
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            ["low", "medium", "high"],
            0.25,
            0.05,
            1.0,
            1.0,
            0,
            "manual seed, staleness unknown",
            0,
        ),
        (
            "openrouter:configured_model",
            "openrouter",
            "configured_model",
            "Configured OpenRouter model",
            "configured",
            128000,
            4096,
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            ["low", "medium", "high"],
            0.25,
            0.05,
            1.0,
            1.0,
            0,
            "manual seed, staleness unknown",
            0,
        ),
        (
            "litellm:configured_model",
            "litellm",
            "configured_model",
            "Configured LiteLLM model",
            "configured",
            128000,
            4096,
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            ["low", "medium", "high"],
            None,
            None,
            None,
            None,
            0,
            "manual seed, price unknown",
            0,
        ),
        (
            "codex_cli:gpt-5.5",
            "codex_cli",
            "gpt-5.5",
            "Codex CLI GPT-5.5",
            "gpt",
            400000,
            8192,
            1,
            1,
            1,
            1,
            0,
            0,
            1,
            1,
            ["medium", "high", "xhigh"],
            1.0,
            0.25,
            5.0,
            5.0,
            0,
            "manual_seed; staleness unknown",
            1,
        ),
        (
            "claude_code_cli:sonnet",
            "claude_code_cli",
            "sonnet",
            "Claude Code Sonnet",
            "claude",
            200000,
            8192,
            1,
            1,
            1,
            1,
            0,
            0,
            1,
            1,
            ["medium", "high"],
            1.0,
            0.25,
            5.0,
            5.0,
            0,
            "manual_seed; staleness unknown",
            1,
        ),
        (
            "claude_code_cli:opus",
            "claude_code_cli",
            "opus",
            "Claude Code Opus",
            "claude",
            200000,
            8192,
            1,
            1,
            1,
            1,
            0,
            0,
            1,
            1,
            ["high", "xhigh", "max"],
            3.0,
            0.5,
            15.0,
            15.0,
            0,
            "manual_seed; staleness unknown",
            1,
        ),
        (
            "openhands:auto",
            "openhands",
            "auto",
            "OpenHands auto",
            "runtime",
            200000,
            8192,
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            ["medium", "high"],
            None,
            None,
            None,
            None,
            0,
            "manual_seed; runtime cost unknown",
            1,
        ),
        (
            "swe_agent:auto",
            "swe_agent",
            "auto",
            "SWE-agent auto",
            "runtime",
            200000,
            8192,
            1,
            1,
            1,
            0,
            0,
            0,
            1,
            1,
            ["medium", "high"],
            None,
            None,
            None,
            None,
            0,
            "manual_seed; runtime cost unknown",
            1,
        ),
        (
            "manual:manual_selection",
            "manual",
            "manual_selection",
            "Manual selection",
            "manual",
            0,
            0,
            1,
            1,
            0,
            1,
            0,
            0,
            1,
            1,
            ["manual"],
            0.0,
            0.0,
            0.0,
            0.0,
            1,
            "manual operator",
            1,
        ),
    ]
    for row in model_catalog:
        connection.execute(
            """
            INSERT OR IGNORE INTO model_catalog
                (id, provider_id, model, display_name, model_family, context_window, max_output_tokens,
                 supports_tools, supports_json, supports_streaming, supports_vision, supports_embeddings,
                 supports_rerank, supports_reasoning, supports_thinking, effort_levels_json,
                 input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                 reasoning_price_per_mtok, free_tier, free_tier_notes, enabled, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual_seed', ?, ?)
            """,
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                json_dumps(row[15]),
                row[16],
                row[17],
                row[18],
                row[19],
                row[20],
                row[21],
                row[22],
                timestamp,
                timestamp,
            ),
        )

    routing_profiles = [
        (
            "free_first",
            "free_first",
            "free_first",
            "minimize paid usage",
            {"paidEscalationRequiresApproval": True, "providerOrder": ["nvidia_nim", "ollama", "openrouter"]},
        ),
        (
            "cost_controlled",
            "cost_controlled",
            "cost_controlled",
            "acceptable quality under low cost",
            {"maxThinkingEffort": "medium", "allowPremiumModels": False},
        ),
        (
            "balanced_best_value",
            "balanced_best_value",
            "balanced_best_value",
            "best performance/cost",
            {"default": True, "maxThinkingEffort": "high"},
        ),
        (
            "max_performance",
            "max_performance",
            "max_performance",
            "maximum quality within explicit budget",
            {"allowThinkingMax": True, "requiresApprovalOverUsd": 3.0},
        ),
        (
            "manual_by_profile",
            "manual_by_profile",
            "manual_by_profile",
            "user selected provider/model/runtime",
            {"automaticFallback": False},
        ),
        (
            "local_private",
            "local_private",
            "local_private",
            "no remote data",
            {"allowRemoteProviders": False},
        ),
    ]
    for row in routing_profiles:
        connection.execute(
            """
            INSERT OR IGNORE INTO routing_profiles (id, name, mode, objective, rules_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (row[0], row[1], row[2], row[3], json_dumps(row[4]), timestamp, timestamp),
        )

    role_policies = [
        (
            "analyst",
            "analyst",
            "free_first",
            [
                {"provider": "nvidia_nim", "model": "auto_best_available"},
                {"provider": "ollama", "model": "local_default"},
            ],
            [{"provider": "openai_compatible", "model": "configured_model", "requiresApproval": True}],
            [{"provider": "codex_cli", "model": "gpt-5.5", "effort": "high", "requiresApproval": True}],
            [],
            0.10,
            64000,
            0.10,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "product_owner",
            "product_owner",
            "balanced_best_value",
            [
                {"provider": "nvidia_nim", "model": "auto_best_available"},
                {"provider": "openai_compatible", "model": "configured_model"},
            ],
            [],
            [],
            [],
            0.75,
            128000,
            0.75,
            0,
            1,
            1,
            0,
            1,
        ),
        (
            "technical_lead",
            "technical_lead",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "xhigh"},
                {"provider": "claude_code_cli", "model": "opus", "effort": "xhigh"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "high"},
            ],
            [],
            [{"provider": "codex_cli", "model": "gpt-5.5", "effort": "xhigh", "requiresApproval": True}],
            [],
            3.00,
            240000,
            3.00,
            1,
            1,
            1,
            1,
            1,
        ),
        (
            "technical_lead_shadow",
            "technical_lead_shadow",
            "balanced_best_value",
            [
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "high"},
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
            ],
            [],
            [{"provider": "claude_code_cli", "model": "opus", "effort": "xhigh", "requiresApproval": True}],
            [],
            1.50,
            200000,
            1.50,
            1,
            1,
            1,
            1,
            1,
        ),
        (
            "developer",
            "developer",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "medium"},
                {"provider": "openhands", "model": "auto"},
            ],
            [{"provider": "swe_agent", "model": "auto"}],
            [
                {
                    "provider": "codex_cli",
                    "model": "gpt-5.5",
                    "effort": "xhigh",
                    "condition": "repeated_failure",
                }
            ],
            [],
            2.00,
            200000,
            2.00,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "backend_engineer",
            "backend_engineer",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "medium"},
                {"provider": "openhands", "model": "auto"},
            ],
            [{"provider": "swe_agent", "model": "auto"}],
            [
                {
                    "provider": "codex_cli",
                    "model": "gpt-5.5",
                    "effort": "xhigh",
                    "condition": "repeated_failure",
                }
            ],
            [],
            2.00,
            200000,
            2.00,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "frontend_engineer",
            "frontend_engineer",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "medium"},
                {"provider": "openhands", "model": "auto"},
            ],
            [{"provider": "swe_agent", "model": "auto"}],
            [
                {
                    "provider": "codex_cli",
                    "model": "gpt-5.5",
                    "effort": "xhigh",
                    "condition": "repeated_failure",
                }
            ],
            [],
            2.00,
            200000,
            2.00,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "implementer",
            "implementer",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "medium"},
            ],
            [{"provider": "openhands", "model": "auto"}],
            [
                {
                    "provider": "codex_cli",
                    "model": "gpt-5.5",
                    "effort": "xhigh",
                    "condition": "repeated_failure",
                }
            ],
            [],
            1.50,
            160000,
            1.50,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "qa",
            "qa",
            "cost_controlled",
            [
                {"provider": "nvidia_nim", "model": "auto_best_available"},
                {"provider": "ollama", "model": "local_default"},
                {"provider": "claude_code_cli", "model": "sonnet"},
            ],
            [],
            [],
            [],
            0.50,
            128000,
            0.50,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "qa_reviewer",
            "qa_reviewer",
            "cost_controlled",
            [
                {"provider": "nvidia_nim", "model": "auto_best_available"},
                {"provider": "ollama", "model": "local_default"},
                {"provider": "claude_code_cli", "model": "sonnet"},
            ],
            [],
            [{"provider": "codex_cli", "model": "gpt-5.5", "effort": "high", "condition": "high_risk"}],
            [],
            0.75,
            128000,
            0.75,
            0,
            1,
            1,
            1,
            1,
        ),
        (
            "security_reviewer",
            "security_reviewer",
            "balanced_best_value",
            [
                {"provider": "codex_cli", "model": "gpt-5.5", "effort": "high"},
                {"provider": "claude_code_cli", "model": "sonnet", "effort": "high"},
            ],
            [],
            [{"provider": "codex_cli", "model": "gpt-5.5", "effort": "xhigh", "condition": "high_risk"}],
            [],
            2.50,
            200000,
            2.50,
            1,
            1,
            1,
            1,
            1,
        ),
        (
            "release_manager",
            "release_manager",
            "cost_controlled",
            [
                {"provider": "claude_code_cli", "model": "sonnet"},
                {"provider": "openai_compatible", "model": "configured_model"},
            ],
            [{"provider": "manual", "model": "manual_selection"}],
            [],
            [],
            1.00,
            128000,
            1.00,
            0,
            1,
            1,
            1,
            1,
        ),
    ]
    for row in role_policies:
        connection.execute(
            """
            INSERT OR IGNORE INTO role_model_policies
                (id, role, routing_profile_id, preferred_json, fallback_json, escalation_json, blocked_json,
                 max_cost_per_task_usd, max_tokens_per_run, requires_approval_over_usd,
                 requires_approval_for_reasoning_max, allow_remote, allow_local, allow_cli, allow_api,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[1],
                row[2],
                json_dumps(row[3]),
                json_dumps(row[4]),
                json_dumps(row[5]),
                json_dumps(row[6]),
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                timestamp,
                timestamp,
            ),
        )

    provider_limits = [
        ("nvidia_nim:*", "nvidia_nim", "*", None, None, None, None, None, None, None, "conservative"),
        (
            "openai_compatible:*",
            "openai_compatible",
            "*",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "conservative",
        ),
    ]
    for row in provider_limits:
        connection.execute(
            """
            INSERT OR IGNORE INTO provider_limits
                (id, provider_id, model, rpm, tpm, daily_requests, daily_tokens, monthly_requests,
                 monthly_tokens, monthly_budget_usd, current_window_json, cooldown_until, last_429_at,
                 last_limit_error_at, unknown_limit_strategy, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', NULL, NULL, NULL, ?, ?, ?)
            """,
            (*row, timestamp, timestamp),
        )

    budget_rules = [
        ("global-monthly-default", "global", None, 50.0, None, "month", "require_approval", 1),
        ("analyst-task-default", "role", "analyst", 0.10, 64000, "task", "require_approval", 1),
        ("developer-task-default", "role", "developer", 2.00, 200000, "task", "require_approval", 1),
    ]
    for row in budget_rules:
        connection.execute(
            """
            INSERT OR IGNORE INTO budget_rules
                (id, scope_type, scope_id, max_cost_usd, max_tokens, period, action_on_exceed, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row, timestamp, timestamp),
        )
    connection.execute(
        """
        UPDATE budget_rules
        SET action_on_exceed = 'require_approval', updated_at = ?
        WHERE id = 'analyst-task-default' AND action_on_exceed = 'fallback'
        """,
        (timestamp,),
    )

    runtime_capabilities = [
        ("codex_cli:code_edit", "codex_cli", "code_edit", 1, {"workspaceBound": True}),
        ("claude_code_cli:code_edit", "claude_code_cli", "code_edit", 1, {"workspaceBound": True}),
        ("manual:approval", "manual", "approval", 1, {"operator": True}),
    ]
    for row in runtime_capabilities:
        connection.execute(
            """
            INSERT OR IGNORE INTO runtime_capabilities
                (id, runtime, capability, enabled, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (row[0], row[1], row[2], row[3], json_dumps(row[4]), timestamp, timestamp),
        )
    connection.execute(
        """
        DELETE FROM runtime_capabilities
        WHERE runtime IN ('openhands', 'swe_agent')
          AND capability = 'issue_to_patch'
          AND metadata = ?
        """,
        (json_dumps({"optional": True}),),
    )

    for provider_id, provider, label, status, allow_remote, metadata in [
        (
            "nvidia_nim",
            "nvidia_nim",
            "NVIDIA NIM / Build",
            "optional",
            1,
            {"runtime": "api", "quotaMode": "trial_rate_limited"},
        ),
        ("codex_cli", "codex_cli", "Codex CLI", "optional", 0, {"runtime": "cli"}),
        ("claude_code_cli", "claude_code_cli", "Claude Code CLI", "optional", 0, {"runtime": "cli"}),
        ("openhands", "openhands", "OpenHands", "optional", 0, {"runtime": "cli"}),
        ("swe_agent", "swe_agent", "SWE-agent", "optional", 0, {"runtime": "cli"}),
        ("litellm", "litellm", "LiteLLM Proxy", "optional", 1, {"runtime": "gateway"}),
    ]:
        connection.execute(
            """
            INSERT OR IGNORE INTO model_providers
                (id, provider, label, status, allow_remote, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider_id, provider, label, status, allow_remote, json_dumps(metadata), timestamp, timestamp),
        )

    _remove_legacy_simulation_runtime_records(connection)

    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (12, utc_now()),
    )


def init_phase13_schema(connection: sqlite3.Connection) -> None:
    """Fase 13: añade los resultados por intento de benchmark de modelos (éxito, QA, costo, latencia)."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_benchmark_outcomes (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            runtime_type TEXT NOT NULL,
            role TEXT,
            workflow_run_id TEXT,
            workflow_step_id TEXT,
            agent_id TEXT,
            job_id TEXT,
            task_id TEXT,
            usage_ledger_id TEXT,
            success INTEGER,
            qa_pass INTEGER,
            rework INTEGER,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            latency_ms INTEGER,
            provenance TEXT NOT NULL DEFAULT 'operator_reported',
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_model_benchmark_outcomes_model
            ON model_benchmark_outcomes(provider_id, model, role, created_at);
        CREATE INDEX IF NOT EXISTS idx_model_benchmark_outcomes_workflow
            ON model_benchmark_outcomes(workflow_run_id, workflow_step_id);
        """
    )
    _add_column_if_missing(
        connection,
        "model_benchmark_outcomes",
        "provenance",
        "provenance TEXT NOT NULL DEFAULT 'operator_reported'",
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (13, utc_now()),
    )


def init_phase14_schema(connection: sqlite3.Connection) -> None:
    """Fase 14: añade los snapshots de precios de modelos con su procedencia y fecha de vigencia."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS pricing_snapshots (
            id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL,
            model TEXT NOT NULL,
            input_price_per_mtok REAL,
            cached_input_price_per_mtok REAL,
            output_price_per_mtok REAL,
            reasoning_price_per_mtok REAL,
            free_tier INTEGER NOT NULL DEFAULT 0,
            source_ref TEXT NOT NULL,
            effective_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            apply_to_catalog INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pricing_snapshots_model
            ON pricing_snapshots(provider_id, model, created_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (14, utc_now()),
    )


def init_phase15_schema(connection: sqlite3.Connection) -> None:
    """Fase 15: añade ciclo de vida de memoria (expiración/borrado) y las tablas de i18n."""
    _add_column_if_missing(connection, "memory_items", "expires_at", "expires_at TEXT")
    _add_column_if_missing(connection, "memory_items", "deleted_at", "deleted_at TEXT")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS i18n_languages (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            native_name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS i18n_translations (
            key TEXT NOT NULL,
            language_code TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (key, language_code),
            FOREIGN KEY (language_code) REFERENCES i18n_languages(code) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS i18n_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_i18n_translations_language
            ON i18n_translations(language_code, key);
        CREATE INDEX IF NOT EXISTS idx_memory_items_project_active
            ON memory_items(project_id, deleted_at, expires_at, created_at);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (15, utc_now()),
    )


def init_phase16_schema(connection: sqlite3.Connection) -> None:
    """Fase 16: instala el slice de product discovery (iniciativas, sesiones de descubrimiento,
    mensajes de conversación, preguntas/respuestas de aclaración, briefs versionados, supuestos y
    decisiones de producto). Cada entidad es su propia tabla (sin metadata-como-entidad),
    project-scoped, trazable por referencias e historial, y versionable donde el dominio lo exige."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS initiatives (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            owner TEXT NOT NULL,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            facilitator TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS clarification_questions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            session_id TEXT,
            sequence INTEGER NOT NULL,
            question TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            asked_by TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clarification_answers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            answer TEXT NOT NULL,
            status TEXT NOT NULL,
            answered_by TEXT NOT NULL,
            supersedes_id TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_briefs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            problem_statement TEXT NOT NULL,
            goals TEXT NOT NULL,
            target_users TEXT NOT NULL,
            success_metrics TEXT NOT NULL,
            scope TEXT NOT NULL,
            out_of_scope TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_brief_versions (
            id TEXT PRIMARY KEY,
            brief_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            problem_statement TEXT NOT NULL,
            goals TEXT NOT NULL,
            target_users TEXT NOT NULL,
            success_metrics TEXT NOT NULL,
            scope TEXT NOT NULL,
            out_of_scope TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            authored_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(brief_id, version)
        );
        CREATE TABLE IF NOT EXISTS assumptions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            brief_id TEXT,
            source_question_id TEXT,
            statement TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence TEXT NOT NULL,
            validation TEXT NOT NULL,
            owner TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_decisions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT NOT NULL,
            brief_id TEXT,
            supersedes_id TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            context TEXT NOT NULL,
            decision TEXT NOT NULL,
            rationale TEXT NOT NULL,
            consequences TEXT NOT NULL,
            linked_assumption_ids TEXT NOT NULL,
            linked_question_ids TEXT NOT NULL,
            decided_by TEXT NOT NULL,
            decided_at TEXT,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_initiatives_project_status
            ON initiatives(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_discovery_sessions_initiative
            ON discovery_sessions(initiative_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_discovery_sessions_project
            ON discovery_sessions(project_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_conversation_messages_session_seq
            ON conversation_messages(session_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_conversation_messages_project_created
            ON conversation_messages(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_clarification_questions_initiative
            ON clarification_questions(initiative_id, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_clarification_questions_project
            ON clarification_questions(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_clarification_answers_question
            ON clarification_answers(question_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_product_briefs_initiative
            ON product_briefs(initiative_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_product_briefs_project
            ON product_briefs(project_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_product_brief_versions_brief
            ON product_brief_versions(brief_id, version);
        CREATE INDEX IF NOT EXISTS idx_assumptions_initiative
            ON assumptions(initiative_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_assumptions_project
            ON assumptions(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_product_decisions_initiative
            ON product_decisions(initiative_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_product_decisions_project
            ON product_decisions(project_id, status);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (16, utc_now()),
    )


def init_phase17_schema(connection: sqlite3.Connection) -> None:
    """Fase 17: instala el slice de backlog (epics, user stories, criterios de aceptación,
    grafos de dependencias de historias y tareas, tareas de agente y asignaciones). Modela la HU
    como valor de usuario (sin rol: no se duplica por rol) y el trabajo técnico como agent_tasks
    con rol propio; cada entidad es su propia tabla, project-scoped, trazable y versionable donde
    el dominio lo exige."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS epics (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            owner TEXT NOT NULL,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_stories (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            epic_id TEXT NOT NULL,
            title TEXT NOT NULL,
            as_a TEXT NOT NULL,
            i_want TEXT NOT NULL,
            so_that TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            business_value TEXT NOT NULL,
            story_points INTEGER,
            owner TEXT NOT NULL,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS acceptance_criteria (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            story_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            criterion TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(story_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS story_dependencies (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            story_id TEXT NOT NULL,
            depends_on_story_id TEXT NOT NULL,
            type TEXT NOT NULL,
            reason TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(story_id, depends_on_story_id, type)
        );
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            story_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            role TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT NOT NULL,
            estimate_hours REAL,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS task_dependencies (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            depends_on_task_id TEXT NOT NULL,
            type TEXT NOT NULL,
            reason TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, depends_on_task_id, type)
        );
        CREATE TABLE IF NOT EXISTS agent_assignments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            released_at TEXT,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_epics_project_status
            ON epics(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_user_stories_epic
            ON user_stories(epic_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_user_stories_project
            ON user_stories(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_story
            ON acceptance_criteria(story_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_story_dependencies_story
            ON story_dependencies(story_id, type);
        CREATE INDEX IF NOT EXISTS idx_story_dependencies_depends_on
            ON story_dependencies(depends_on_story_id);
        CREATE INDEX IF NOT EXISTS idx_story_dependencies_project
            ON story_dependencies(project_id);
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_story
            ON agent_tasks(story_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_project_role
            ON agent_tasks(project_id, role, status);
        CREATE INDEX IF NOT EXISTS idx_task_dependencies_task
            ON task_dependencies(task_id, type);
        CREATE INDEX IF NOT EXISTS idx_task_dependencies_depends_on
            ON task_dependencies(depends_on_task_id);
        CREATE INDEX IF NOT EXISTS idx_task_dependencies_project
            ON task_dependencies(project_id);
        CREATE INDEX IF NOT EXISTS idx_agent_assignments_task
            ON agent_assignments(task_id, status);
        CREATE INDEX IF NOT EXISTS idx_agent_assignments_agent
            ON agent_assignments(agent_id, status);
        CREATE INDEX IF NOT EXISTS idx_agent_assignments_project
            ON agent_assignments(project_id, status);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (17, utc_now()),
    )


def init_phase18_schema(connection: sqlite3.Connection) -> None:
    """Fase 18: instala el assessment estático de proyectos (project_assessments) y sus hallazgos
    por dimensión (project_findings: stack, módulos, arquitectura, endpoints, datos, tests, cobertura,
    quality commands, deuda, seguridad, documentación, git history, funcionalidades, riesgos y gaps).
    Cada hallazgo es su propia fila, project-scoped y trazable a su assessment; no usa metadata JSON
    como sustituto de entidades."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            root_path TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            summary TEXT NOT NULL,
            findings_count INTEGER NOT NULL,
            risk_count INTEGER NOT NULL,
            gap_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_findings (
            id TEXT PRIMARY KEY,
            assessment_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT NOT NULL,
            severity TEXT NOT NULL,
            evidence TEXT NOT NULL,
            confidence TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_project_assessments_project
            ON project_assessments(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_project_findings_assessment
            ON project_findings(assessment_id, category);
        CREATE INDEX IF NOT EXISTS idx_project_findings_project_category
            ON project_findings(project_id, category, severity);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (18, utc_now()),
    )


def init_phase19_schema(connection: sqlite3.Connection) -> None:
    """Fase 19: instala la máquina de estados durable del ProductLoopCoordinator (product_loops) y su
    bitácora append-only de transiciones (product_loop_transitions). El estado vive en la base —no solo
    en memoria— para reanudar el loop tras reiniciar AIDO; cada transición queda registrada y trazable."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS product_loops (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            initiative_id TEXT,
            title TEXT NOT NULL,
            state TEXT NOT NULL,
            previous_state TEXT,
            status TEXT NOT NULL,
            context TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS product_loop_transitions (
            id TEXT PRIMARY KEY,
            loop_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            trigger TEXT NOT NULL,
            version INTEGER NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(loop_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_product_loops_project_state
            ON product_loops(project_id, state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_product_loop_transitions_loop
            ON product_loop_transitions(loop_id, version);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (19, utc_now()),
    )


def init_phase20_schema(connection: sqlite3.Connection) -> None:
    """Fase 20: añade la tabla ``iterations`` que el IterationPlanner produce a partir del brief
    aprobado y las historias ready. La iteración es el contenedor del plan (estrategia de workspace,
    quality gates, security gates risk-based y costo estimado marcado como estimado); el DAG de tareas,
    las asignaciones y las dependencias se materializan en las tablas del slice backlog y referencian la
    iteración por ``metadata.iterationId``."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS iterations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            brief_id TEXT NOT NULL,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL,
            story_ids TEXT NOT NULL,
            workspace_strategy TEXT NOT NULL,
            quality_gates TEXT NOT NULL,
            security_gates TEXT NOT NULL,
            estimated_cost TEXT NOT NULL,
            runtimes TEXT NOT NULL,
            task_count INTEGER NOT NULL,
            assignment_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_iterations_project_status
            ON iterations(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_iterations_brief
            ON iterations(brief_id);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (20, utc_now()),
    )


def seed_platform_catalogs(connection: sqlite3.Connection) -> None:
    """Siembra los catálogos de proveedores delegando en ``ProjectsRepository.seed_providers``."""
    from local_control_center.projects.repository import ProjectsRepository

    ProjectsRepository(connection).seed_providers()
