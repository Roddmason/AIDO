from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .jobs_approvals.repository import JobsRepository
from .memory_retrieval.repository import MemoryRepository


SENSITIVE_JOB_KINDS = {
    "pipeline.start",
    "pipeline.retry",
    "pipeline.stage.retry",
}

PROJECT_TEMPLATES = [
    {"id": "react-vite", "name": "React + Vite", "kind": "frontend"},
    {"id": "node-cli", "name": "Node CLI", "kind": "tooling"},
    {"id": "python-fastapi", "name": "Python FastAPI", "kind": "backend"},
    {"id": "other", "name": "Other", "kind": "generic"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_millis(ms: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def default_db_path() -> Path:
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_DB")
    if explicit:
        return Path(explicit)
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    return home / ".claude" / "local-control-center" / "platform.sqlite"


def default_cwd() -> Path:
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_CWD")
    return Path(explicit) if explicit else Path.cwd()


def row_to_project(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["path"],
        "templateId": row["template_id"],
        "source": row["source"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_provider(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "capabilities": json_loads(row["capabilities"], []),
        "models": json_loads(row["models"], []),
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "updatedAt": row["updated_at"],
    }


def row_to_team(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "version": row["version"],
        "capabilities": json_loads(row["capabilities"], []),
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_agent(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "teamId": row["team_id"],
        "name": row["name"],
        "role": row["role"],
        "kind": row["kind"],
        "providerId": row["provider_id"],
        "model": row["model"],
        "capabilities": json_loads(row["capabilities"], []),
        "permissions": json_loads(row["permissions"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "teamId": row["team_id"],
        "name": row["name"],
        "status": row["status"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "scope": row["scope"],
        "scopeId": row["scope_id"],
        "kind": row["kind"],
        "content": row["content"],
        "sourceRef": row["source_ref"],
        "version": row["version"],
        "hash": row["hash"],
        "supersedesId": row["supersedes_id"],
        "createdByRunId": row["created_by_run_id"],
        "validFrom": row["valid_from"],
        "metadata": json_loads(row["metadata"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_prompt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "name": row["name"],
        "mode": row["mode"],
        "body": row["body"],
        "optimizer": row["optimizer"],
        "appliesTo": json_loads(row["applies_to"]),
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "kind": row["kind"],
        "status": row["status"],
        "payload": json_loads(row["payload"]),
        "leaseOwner": row["lease_owner"],
        "leaseExpiresAt": row["lease_expires_at"],
        "idempotencyKey": row["idempotency_key"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_job_run(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "providerId": row["provider_id"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "summary": row["summary"],
        "metadata": json_loads(row["metadata"]),
    }


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "projectId": row["project_id"],
        "type": row["type"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
    }


def row_to_audit(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "projectId": row["project_id"],
        "action": row["action"],
        "actor": row["actor"],
        "target": row["target"],
        "payload": json_loads(row["payload"]),
        "createdAt": row["created_at"],
    }


def row_to_action_request(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "projectId": row["project_id"],
        "actionType": row["action_type"],
        "status": row["status"],
        "riskLevel": row["risk_level"],
        "command": row["command"],
        "payload": json_loads(row["payload"]),
        "reason": row["reason"],
        "requestedAt": row["requested_at"],
        "decidedAt": row["decided_at"],
        "decidedBy": row["decided_by"],
    }


class PlatformStore:
    def __init__(self, cwd: str | Path | None = None, db_path: str | Path | None = None):
        self.cwd = Path(cwd) if cwd is not None else default_cwd()
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self._connection: sqlite3.Connection | None = None
        self._token = secrets.token_urlsafe(32)

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                self.db_path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA busy_timeout = 30000")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection.executescript(
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
            CREATE TABLE IF NOT EXISTS workspace_states (
                project_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                metadata TEXT NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_jobs_status_lease ON jobs(status, lease_expires_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_project_created ON events(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_memory_project_scope_hash ON memory_items(project_id, scope, hash);
            CREATE INDEX IF NOT EXISTS idx_action_requests_job_status ON action_requests(job_id, status);
            """
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (1, utc_now()),
        )
        self._init_phase2_schema()
        self._init_phase3_schema()
        self._init_phase4_schema()
        self._seed_providers()

    def _init_phase2_schema(self) -> None:
        self.connection.executescript(
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
            CREATE INDEX IF NOT EXISTS idx_workflows_project_status ON workflows(project_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_status ON workflow_runs(workflow_id, status);
            CREATE INDEX IF NOT EXISTS idx_permission_decisions_project_created ON permission_decisions(project_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_evidence_project_created ON evidence_packages(project_id, created_at);
            """
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (2, utc_now()),
        )
        timestamp = utc_now()
        default_policies = [
            ("plan", "Plan", "plan", [{"tool": "filesystem", "effect": "read"}, {"tool": "shell", "effect": "deny"}]),
            ("dev_safe", "Dev Safe", "dev_safe", [{"tool": "shell", "effect": "approval_required"}]),
            ("release", "Release", "release", [{"tool": "deploy_prod", "effect": "human_required"}]),
        ]
        for policy_id, name, profile, rules in default_policies:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO permission_policies
                    (id, name, profile, rules, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (policy_id, name, profile, json_dumps(rules), timestamp, timestamp),
            )

    def _init_phase3_schema(self) -> None:
        self.connection.executescript(
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
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (3, utc_now()),
        )
        timestamp = utc_now()
        self.connection.execute(
            """
            INSERT OR IGNORE INTO model_providers
                (id, provider, label, status, allow_remote, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "internal_mock",
                "internal_mock",
                "Internal Mock",
                "available",
                0,
                json_dumps({"runtime": "test"}),
                timestamp,
                timestamp,
            ),
        )

    def _init_phase4_schema(self) -> None:
        workspace_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        if "workflow_run_id" not in workspace_columns:
            self.connection.execute("ALTER TABLE workspaces ADD COLUMN workflow_run_id TEXT")
        if "workflow_step_id" not in workspace_columns:
            self.connection.execute("ALTER TABLE workspaces ADD COLUMN workflow_step_id TEXT")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_workspaces_workflow_run ON workspaces(workflow_run_id)"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (4, utc_now()),
        )

    def _seed_providers(self) -> None:
        providers = [
            ("codex", "agent", "Codex", ["code", "review"], ["gpt-5", "gpt-5.4"], "available"),
            ("claudecode", "agent", "Claude Code", ["code", "analysis"], ["sonnet"], "available"),
            ("gemini", "research", "Gemini", ["research"], ["gemini-pro"], "available"),
            ("openai-agents", "agent", "OpenAI Agents SDK", ["planning", "tools"], ["gpt-5"], "disabled"),
        ]
        timestamp = utc_now()
        for provider in providers:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO providers
                    (id, kind, label, capabilities, models, status, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider[0],
                    provider[1],
                    provider[2],
                    json_dumps(provider[3]),
                    json_dumps(provider[4]),
                    provider[5],
                    json_dumps({}),
                    timestamp,
                ),
            )

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, tuple(params)).fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, tuple(params)).fetchone()

    def _get_project_by_path(self, path: str | Path) -> dict[str, Any] | None:
        row = self._query_one("SELECT * FROM projects WHERE path = ?", (str(Path(path)),))
        return row_to_project(row) if row else None

    def find_project(self, ref: str | Path) -> dict[str, Any] | None:
        ref_text = str(ref)
        row = self._query_one("SELECT * FROM projects WHERE id = ?", (ref_text,))
        if row:
            return row_to_project(row)
        return self._get_project_by_path(ref_text)

    def get_handshake(self) -> dict[str, Any]:
        return {"token": self._token, "loopbackOnly": True}

    def create_project(
        self,
        *,
        name: str,
        path: str | Path,
        template_id: str = "other",
        create_directory: bool = True,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_path = Path(path)
        if create_directory:
            project_path.mkdir(parents=True, exist_ok=True)
        existing = self._get_project_by_path(project_path)
        if existing:
            return existing
        timestamp = utc_now()
        project_id = f"project-{uuid.uuid4()}"
        self.connection.execute(
            """
            INSERT INTO projects
                (id, name, path, template_id, source, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name,
                str(project_path),
                template_id or "other",
                source,
                "active",
                json_dumps(metadata or {}),
                timestamp,
                timestamp,
            ),
        )
        self.record_audit(project_id=project_id, action="project.create", target=project_id, payload={"path": str(project_path)})
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        row = self._query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise KeyError(f"Project not found: {project_id}")
        return row_to_project(row)

    def list_projects(self) -> list[dict[str, Any]]:
        return [row_to_project(row) for row in self._query("SELECT * FROM projects ORDER BY created_at ASC")]

    def list_project_templates(self) -> list[dict[str, Any]]:
        return [template.copy() for template in PROJECT_TEMPLATES]

    def import_legacy_workspace(self, cwd: str | Path) -> dict[str, Any] | None:
        workspace = Path(cwd)
        source_path = workspace / ".claude" / "team-workspace.json"
        if not source_path.exists():
            return None
        raw = source_path.read_text(encoding="utf-8")
        state = json.loads(raw)
        source_hash = stable_hash(raw)
        project = self._get_project_by_path(workspace) or self.create_project(
            name=workspace.name or "Workspace",
            path=workspace,
            template_id="other",
            create_directory=True,
            source="legacy-import",
            metadata={"legacyVersion": state.get("version")},
        )
        self.save_workspace_state(
            workspace,
            state,
            source="legacy-import",
            source_path=str(source_path),
            source_hash=source_hash,
            project_id=project["id"],
        )
        timestamp = utc_now()
        for team in state.get("teams", []):
            self.connection.execute(
                """
                INSERT OR REPLACE INTO teams
                    (id, project_id, name, version, capabilities, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team.get("id") or f"team-{uuid.uuid4()}",
                    project["id"],
                    team.get("name") or "Team",
                    str(state.get("version") or 1),
                    json_dumps(team.get("capabilities") or []),
                    json_dumps({"workspaceScope": team.get("workspaceScope") or {}}),
                    timestamp,
                    timestamp,
                ),
            )
            for member in team.get("members", []):
                runtime = (member.get("runtimePreferences") or [{}])[0]
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO agents
                        (id, team_id, name, role, kind, provider_id, model, capabilities, permissions, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        member.get("id") or f"agent-{uuid.uuid4()}",
                        team.get("id"),
                        member.get("name") or "Agent",
                        member.get("role") or "",
                        member.get("kind") or "agent",
                        runtime.get("provider") or "codex",
                        runtime.get("model") or "",
                        json_dumps(member.get("capabilities") or []),
                        json_dumps(member.get("permissions") or {}),
                        timestamp,
                        timestamp,
                    ),
                )
        for session in state.get("sessions", []):
            self.connection.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (id, project_id, team_id, name, status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.get("id") or f"session-{uuid.uuid4()}",
                    project["id"],
                    session.get("teamId"),
                    session.get("name") or "Main",
                    "active",
                    json_dumps(
                        {
                            "activeChatId": session.get("activeChatId"),
                            "activePipelineId": session.get("activePipelineId"),
                        }
                    ),
                    timestamp,
                    timestamp,
                ),
            )
        for team_id, memory in (state.get("memoryByTeamId") or {}).items():
            summary = memory.get("summary")
            if summary:
                self.create_memory_item(
                    project_id=project["id"],
                    scope="team",
                    scope_id=team_id,
                    kind="summary",
                    content=summary,
                    source_ref=str(source_path),
                    metadata={"notes": memory.get("notes") or []},
                )
        return self.load_workspace_state(workspace)

    def save_workspace_state(
        self,
        cwd: str | Path,
        state: dict[str, Any],
        *,
        source: str = "runtime",
        source_path: str | None = None,
        source_hash: str | None = None,
        project_id: str | None = None,
    ) -> None:
        workspace = Path(cwd)
        project = self.get_project(project_id) if project_id else (
            self._get_project_by_path(workspace)
            or self.create_project(name=workspace.name or "Workspace", path=workspace, template_id="other")
        )
        timestamp = utc_now()
        resolved_source_path = source_path or str(workspace / ".claude" / "team-workspace.json")
        resolved_hash = source_hash or stable_hash(state)
        self.connection.execute(
            """
            INSERT INTO workspace_states
                (project_id, state_json, source_path, source_hash, imported_at, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                state_json = excluded.state_json,
                source_path = excluded.source_path,
                source_hash = excluded.source_hash,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                project["id"],
                json_dumps(state),
                resolved_source_path,
                resolved_hash,
                timestamp,
                json_dumps({"source": source}),
                timestamp,
            ),
        )

    def load_workspace_state(self, cwd: str | Path) -> dict[str, Any]:
        project = self._get_project_by_path(cwd)
        if not project:
            raise KeyError(f"Workspace not imported: {cwd}")
        row = self._query_one("SELECT * FROM workspace_states WHERE project_id = ?", (project["id"],))
        if not row:
            raise KeyError(f"Workspace state not found: {cwd}")
        return {
            "project": project,
            "state": json_loads(row["state_json"]),
            "metadata": {
                "sourcePath": row["source_path"],
                "sourceHash": row["source_hash"],
                "importedAt": row["imported_at"],
                **json_loads(row["metadata"]),
            },
        }

    def list_providers(self) -> list[dict[str, Any]]:
        return [row_to_provider(row) for row in self._query("SELECT * FROM providers ORDER BY id ASC")]

    def list_teams(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query("SELECT * FROM teams WHERE project_id = ? ORDER BY created_at ASC", (project_id,))
        else:
            rows = self._query("SELECT * FROM teams ORDER BY created_at ASC")
        return [row_to_team(row) for row in rows]

    def list_agents(self, team_id: str | None = None) -> list[dict[str, Any]]:
        if team_id:
            rows = self._query("SELECT * FROM agents WHERE team_id = ? ORDER BY created_at ASC", (team_id,))
        else:
            rows = self._query("SELECT * FROM agents ORDER BY created_at ASC")
        return [row_to_agent(row) for row in rows]

    def list_sessions(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query("SELECT * FROM sessions WHERE project_id = ? ORDER BY created_at ASC", (project_id,))
        else:
            rows = self._query("SELECT * FROM sessions ORDER BY created_at ASC")
        return [row_to_session(row) for row in rows]

    @property
    def memory(self) -> MemoryRepository:
        return MemoryRepository(self.connection)

    def create_memory_item(
        self,
        *,
        project_id: str,
        scope: str,
        scope_id: str,
        kind: str,
        content: str,
        source_ref: str = "",
        version: int = 1,
        metadata: dict[str, Any] | None = None,
        supersedes_id: str | None = None,
        created_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        memory_item = self.memory.create_memory_item(
            project_id=project_id,
            scope=scope,
            scope_id=scope_id,
            kind=kind,
            content=content,
            source_ref=source_ref,
            version=version,
            metadata=metadata,
            supersedes_id=supersedes_id,
            created_by_run_id=created_by_run_id,
        )
        self.record_event(project_id=project_id, event_type="memory.created", payload={"memoryItemId": memory_item["id"]})
        return memory_item

    def get_memory_item(self, memory_id: str) -> dict[str, Any]:
        return self.memory.get_memory_item(memory_id)

    def list_memory_items(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self.memory.list_memory_items(project_id=project_id)

    def upsert_memory_embedding(
        self,
        *,
        memory_item_id: str,
        provider: str,
        model: str,
        embedding: list[float],
    ) -> None:
        self.memory.upsert_memory_embedding(
            memory_item_id=memory_item_id,
            provider=provider,
            model=model,
            embedding=embedding,
        )

    def upsert_prompt_template(
        self,
        *,
        project_id: str,
        name: str,
        body: str,
        mode: str = "manual",
        optimizer: str = "",
        applies_to: dict[str, Any] | None = None,
        prompt_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        existing = self._query_one("SELECT * FROM prompt_templates WHERE id = ?", (prompt_id,)) if prompt_id else None
        next_id = prompt_id or f"prompt-{uuid.uuid4()}"
        version = (existing["version"] + 1) if existing else 1
        if existing:
            self.connection.execute(
                """
                UPDATE prompt_templates
                SET name = ?, mode = ?, body = ?, optimizer = ?, applies_to = ?, version = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, mode, body, optimizer, json_dumps(applies_to or {}), version, timestamp, next_id),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO prompt_templates
                    (id, project_id, name, mode, body, optimizer, applies_to, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (next_id, project_id, name, mode, body, optimizer, json_dumps(applies_to or {}), version, timestamp, timestamp),
            )
        self.connection.execute(
            """
            INSERT INTO prompt_versions
                (id, prompt_id, version, body, mode, optimizer, applies_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"prompt-version-{uuid.uuid4()}", next_id, version, body, mode, optimizer, json_dumps(applies_to or {}), timestamp),
        )
        return row_to_prompt(self._query_one("SELECT * FROM prompt_templates WHERE id = ?", (next_id,)))

    def list_prompt_templates(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query("SELECT * FROM prompt_templates WHERE project_id = ? ORDER BY updated_at DESC", (project_id,))
        else:
            rows = self._query("SELECT * FROM prompt_templates ORDER BY updated_at DESC")
        return [row_to_prompt(row) for row in rows]

    @property
    def jobs(self) -> JobsRepository:
        return JobsRepository(self.connection)

    def create_job(
        self,
        *,
        project_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.jobs.create_job(
            project_id=project_id,
            kind=kind,
            payload=payload,
            status=status,
            idempotency_key=idempotency_key,
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.get_job(job_id)

    def list_jobs(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.list_jobs(project_id=project_id)

    def create_action_request(
        self,
        *,
        job_id: str,
        project_id: str,
        action_type: str,
        risk_level: str,
        command: str = "",
        payload: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        return self.jobs.create_action_request(
            job_id=job_id,
            project_id=project_id,
            action_type=action_type,
            risk_level=risk_level,
            command=command,
            payload=payload,
            reason=reason,
        )

    def get_action_request(self, action_id: str) -> dict[str, Any]:
        return self.jobs.get_action_request(action_id)

    def list_action_requests(self, job_id: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.list_action_requests(job_id=job_id)

    def _pending_actions(self, job_id: str) -> list[dict[str, Any]]:
        return self.jobs._pending_actions(job_id)

    def approve_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        return self.jobs.approve_job(job_id, reason=reason, actor=actor)

    def approve_action(
        self,
        job_id: str,
        action_id: str,
        *,
        reason: str = "",
        actor: str = "operator",
    ) -> dict[str, Any]:
        return self.jobs.approve_action(job_id, action_id, reason=reason, actor=actor)

    def deny_action(
        self,
        job_id: str,
        action_id: str,
        *,
        reason: str = "",
        actor: str = "operator",
    ) -> dict[str, Any]:
        return self.jobs.deny_action(job_id, action_id, reason=reason, actor=actor)

    def cancel_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        return self.jobs.cancel_job(job_id, reason=reason, actor=actor)

    def retry_job(self, job_id: str, reason: str = "", actor: str = "operator") -> dict[str, Any]:
        return self.jobs.retry_job(job_id, reason=reason, actor=actor)

    def claim_next_job(self, *, worker_id: str, lease_ms: int = 300000) -> dict[str, Any] | None:
        return self.jobs.claim_next_job(worker_id=worker_id, lease_ms=lease_ms)

    def requeue_expired_jobs(self, *, now_iso: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.requeue_expired_jobs(now_iso=now_iso)

    def complete_job_run(
        self,
        *,
        job_id: str,
        run_id: str,
        status: str,
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.jobs.complete_job_run(
            job_id=job_id,
            run_id=run_id,
            status=status,
            summary=summary,
            metadata=metadata,
        )

    def list_job_runs(self, job_id: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.list_job_runs(job_id=job_id)

    def record_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        return self.jobs.record_event(
            event_type=event_type,
            payload=payload,
            project_id=project_id,
            job_id=job_id,
        )

    def list_events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.list_events(project_id=project_id)

    def record_audit(
        self,
        *,
        action: str,
        target: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        return self.jobs.record_audit(
            action=action,
            target=target,
            payload=payload,
            project_id=project_id,
            actor=actor,
        )

    def list_audit_events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return self.jobs.list_audit_events(project_id=project_id)

    def upsert_ide_connection(
        self,
        *,
        project_id: str,
        editor: str,
        workspace_root: str,
        status: str = "connected",
        open_files: list[Any] | None = None,
        diagnostics: list[Any] | None = None,
        selection: dict[str, Any] | None = None,
        terminal_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        row = self._query_one(
            "SELECT * FROM ide_connections WHERE project_id = ? AND editor = ? AND workspace_root = ?",
            (project_id, editor, workspace_root),
        )
        connection_id = row["id"] if row else f"ide-{uuid.uuid4()}"
        if row:
            self.connection.execute(
                """
                UPDATE ide_connections
                SET status = ?, open_files = ?, diagnostics = ?, selection = ?, terminal_context = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json_dumps(open_files or []),
                    json_dumps(diagnostics or []),
                    json_dumps(selection or {}),
                    json_dumps(terminal_context or {}),
                    timestamp,
                    connection_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO ide_connections
                    (id, project_id, editor, workspace_root, status, open_files, diagnostics,
                     selection, terminal_context, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    project_id,
                    editor,
                    workspace_root,
                    status,
                    json_dumps(open_files or []),
                    json_dumps(diagnostics or []),
                    json_dumps(selection or {}),
                    json_dumps(terminal_context or {}),
                    timestamp,
                    timestamp,
                ),
            )
        self.record_event(project_id=project_id, event_type="ide.connection.upserted", payload={"ideConnectionId": connection_id})
        return self.get_ide_connection(connection_id)

    def get_ide_connection(self, connection_id: str) -> dict[str, Any]:
        row = self._query_one("SELECT * FROM ide_connections WHERE id = ?", (connection_id,))
        if not row:
            raise KeyError(f"IDE connection not found: {connection_id}")
        return {
            "id": row["id"],
            "projectId": row["project_id"],
            "editor": row["editor"],
            "workspaceRoot": row["workspace_root"],
            "status": row["status"],
            "openFiles": json_loads(row["open_files"], []),
            "diagnostics": json_loads(row["diagnostics"], []),
            "selection": json_loads(row["selection"]),
            "terminalContext": json_loads(row["terminal_context"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_ide_connections(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._query(
                "SELECT * FROM ide_connections WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,),
            )
        else:
            rows = self._query("SELECT * FROM ide_connections ORDER BY updated_at DESC")
        return [
            {
                "id": row["id"],
                "projectId": row["project_id"],
                "editor": row["editor"],
                "workspaceRoot": row["workspace_root"],
                "status": row["status"],
                "openFiles": json_loads(row["open_files"], []),
                "diagnostics": json_loads(row["diagnostics"], []),
                "selection": json_loads(row["selection"]),
                "terminalContext": json_loads(row["terminal_context"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def list_workflows(self) -> list[dict[str, Any]]:
        from .workflows.repository import WorkflowsRepository

        return WorkflowsRepository(self.connection).list_workflows()

    def list_workflow_runs(self) -> list[dict[str, Any]]:
        from .workflows.repository import WorkflowsRepository

        return WorkflowsRepository(self.connection).list_workflow_runs()

    def list_workflow_steps(self) -> list[dict[str, Any]]:
        from .workflows.repository import WorkflowsRepository

        return WorkflowsRepository(self.connection).list_workflow_steps()

    def list_permission_decisions(self) -> list[dict[str, Any]]:
        from .security_policy.repository import SecurityPolicyRepository

        return SecurityPolicyRepository(self.connection).list_decisions()

    def list_evidence_packages(self) -> list[dict[str, Any]]:
        from .evidence.repository import EvidenceRepository

        return EvidenceRepository(self.connection).list_evidence_packages()

    def list_agent_profiles(self) -> list[dict[str, Any]]:
        from .agents.repository import AgentsRepository

        return AgentsRepository(self.connection).list_agent_profiles()

    def list_model_policies(self) -> list[dict[str, Any]]:
        from .agents.repository import AgentsRepository

        return AgentsRepository(self.connection).list_model_policies()

    def list_agent_tool_calls(self) -> list[dict[str, Any]]:
        from .agents.repository import AgentsRepository

        return AgentsRepository(self.connection).list_agent_tool_calls()

    def list_model_calls(self) -> list[dict[str, Any]]:
        from .agents.repository import AgentsRepository

        return AgentsRepository(self.connection).list_model_calls()

    def list_cost_usage(self) -> list[dict[str, Any]]:
        from .agents.repository import AgentsRepository

        return AgentsRepository(self.connection).list_cost_usage()

    def list_runtime_workspaces(self) -> list[dict[str, Any]]:
        from .workspaces_projects.repository import WorkspacesRepository

        return WorkspacesRepository(self.connection, root=self.cwd).list_workspaces()

    def list_skills(self) -> list[dict[str, Any]]:
        from .agents.skills import SkillRegistry

        return SkillRegistry(self.connection).list_skills()

    def ensure_runtime_project(self) -> dict[str, Any]:
        existing = self._get_project_by_path(self.cwd)
        if existing:
            return existing
        imported = self.import_legacy_workspace(self.cwd)
        if imported:
            return imported["project"]
        return self.create_project(
            name=self.cwd.name or "Local Control Center",
            path=self.cwd,
            template_id="other",
            create_directory=True,
            source="runtime",
        )

    def get_overview(self) -> dict[str, Any]:
        self.ensure_runtime_project()
        return {
            "projectTemplates": self.list_project_templates(),
            "projects": self.list_projects(),
            "providers": self.list_providers(),
            "teams": self.list_teams(),
            "agents": self.list_agents(),
            "sessions": self.list_sessions(),
            "jobs": self.list_jobs(),
            "jobRuns": self.list_job_runs(),
            "events": self.list_events(),
            "auditEvents": self.list_audit_events(),
            "memoryItems": self.list_memory_items(),
            "promptTemplates": self.list_prompt_templates(),
            "actionRequests": self.list_action_requests(),
            "ideConnections": self.list_ide_connections(),
            "workflows": self.list_workflows(),
            "workflowRuns": self.list_workflow_runs(),
            "workflowSteps": self.list_workflow_steps(),
            "permissionDecisions": self.list_permission_decisions(),
            "evidencePackages": self.list_evidence_packages(),
            "agentProfiles": self.list_agent_profiles(),
            "modelPolicies": self.list_model_policies(),
            "modelProviders": self.list_providers(),
            "agentToolCalls": self.list_agent_tool_calls(),
            "modelCalls": self.list_model_calls(),
            "costUsage": self.list_cost_usage(),
            "runtimeWorkspaces": self.list_runtime_workspaces(),
            "skills": self.list_skills(),
            "openDesign": {"status": "python-backend"},
            "security": {"loopbackOnly": True, "writeTokenRequired": True},
        }
