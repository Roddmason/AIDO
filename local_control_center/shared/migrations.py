from __future__ import annotations

import sqlite3

from .serialization import json_dumps
from .time import utc_now


def initialize_platform_schema(connection: sqlite3.Connection) -> None:
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
    seed_platform_catalogs(connection)


def init_base_schema(connection: sqlite3.Connection) -> None:
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
            agent_id TEXT,
            task_id TEXT NOT NULL,
            test_plan TEXT NOT NULL,
            acceptance_checklist TEXT NOT NULL,
            test_results TEXT NOT NULL,
            logs TEXT NOT NULL,
            diff_refs TEXT NOT NULL,
            screenshot_refs TEXT NOT NULL,
            risk_notes TEXT NOT NULL,
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
        ("internal_mock", "internal_mock", "Internal Mock", "available", 0, {"runtime": "test"}),
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
        ("manual", "manual", "Manual Operator", "available", 0, {"runtime": "manual"}),
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
    workspace_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
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
    job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    if "workflow_run_id" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN workflow_run_id TEXT")
    if "workflow_step_id" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN workflow_step_id TEXT")

    agent_run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_runs)").fetchall()}
    if "workflow_run_id" not in agent_run_columns:
        connection.execute("ALTER TABLE agent_runs ADD COLUMN workflow_run_id TEXT")
    if "workflow_step_id" not in agent_run_columns:
        connection.execute("ALTER TABLE agent_runs ADD COLUMN workflow_step_id TEXT")

    connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_workflow_run ON jobs(workflow_run_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow_run ON agent_runs(workflow_run_id)")
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (6, utc_now()),
    )


def init_phase7_schema(connection: sqlite3.Connection) -> None:
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
            json_dumps(["python:3.13-slim", "python:3.12-slim", "node:22-alpine", "debian:bookworm-slim", "ubuntu:24.04"]),
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
    grant_columns = {row["name"] for row in connection.execute("PRAGMA table_info(permission_grants)").fetchall()}
    if "revoked_at" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoked_at TEXT")
    if "revoked_by" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoked_by TEXT")
    if "revoke_reason" not in grant_columns:
        connection.execute("ALTER TABLE permission_grants ADD COLUMN revoke_reason TEXT")

    sandbox_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sandbox_profiles)").fetchall()}
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


def seed_platform_catalogs(connection: sqlite3.Connection) -> None:
    from local_control_center.projects.repository import ProjectsRepository

    ProjectsRepository(connection).seed_providers()
