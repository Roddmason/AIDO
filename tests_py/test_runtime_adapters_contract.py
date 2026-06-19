from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from local_control_center.agents.runtime_adapters import (
    CliVersionAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    RestrictedSubprocessAdapter,
    RuntimeAdapterRegistry,
    RuntimeExecutionRequest,
)
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import utc_now

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ADAPTER_FILES = [
    ROOT / "local_control_center" / "agents" / "runtime_adapters.py",
    ROOT / "local_control_center" / "agents" / "runtime_registry.py",
    *(ROOT / "local_control_center" / "agents" / "cli_runtimes").glob("*.py"),
    *(ROOT / "local_control_center" / "agents" / "providers").glob("*.py"),
]


def runtime_request(tmp_path: Path, **overrides: object) -> RuntimeExecutionRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    payload = {
        "projectId": "project-runtime",
        "workflowRunId": "workflow-run-runtime",
        "workflowStepId": "workflow-step-runtime",
        "jobId": "job-runtime",
        "agentRunId": "agent-run-runtime",
        "workspaceId": "workspace-runtime",
        "workspacePath": str(workspace),
        "capability": "version_check",
        "argv": [sys.executable, "--version"],
        "input": {},
        "timeoutSeconds": 5,
        "approvalGrantId": "grant-runtime",
        "metadata": {},
    }
    payload.update(overrides)
    return RuntimeExecutionRequest.model_validate(payload)


def register_runtime_workspace(connection, request: RuntimeExecutionRequest) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (
            id, project_id, task_id, owner_agent_id, path, status,
            isolation_type, metadata, created_at, updated_at, archived_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.workspace_id,
            request.project_id,
            request.capability,
            "implementer",
            request.workspace_path,
            "ready",
            "directory",
            "{}",
            now,
            now,
            None,
        ),
    )
    connection.commit()


def test_restricted_subprocess_rejects_command_string(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, argv="python --version")

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert result.exit_code is None
    assert "structured argv" in (result.reason or "").lower()


def test_restricted_subprocess_requires_argv(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, argv=[])

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert "argv" in (result.reason or "").lower()


def test_restricted_subprocess_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    request = runtime_request(tmp_path, metadata={"cwd": str(outside)})

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "blocked"
    assert "outside the allocated workspace" in (result.reason or "").lower()


def test_restricted_subprocess_records_real_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "sleep.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    request = runtime_request(
        tmp_path,
        workspacePath=str(workspace),
        argv=[sys.executable, str(script)],
        timeoutSeconds=1,
    )

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert result.started_at
    assert result.completed_at
    assert "timed out" in (result.reason or "").lower()


def test_restricted_subprocess_promotes_large_stdout_and_stderr_to_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "large_output.py"
    script.write_text(
        "import sys\nsys.stdout.write('stdout-line-' * 1400)\nsys.stderr.write('stderr-line-' * 1400)\n",
        encoding="utf-8",
    )
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        adapter = RestrictedSubprocessAdapter(connection=connection, artifact_root=tmp_path)
        request = runtime_request(
            tmp_path,
            workspacePath=str(workspace),
            argv=[sys.executable, str(script)],
        )
        register_runtime_workspace(connection, request)

        result = adapter.execute(request)

        assert result.status == "completed"
        assert result.stdout_artifact_id and result.stdout_artifact_id.startswith("artifact-")
        assert result.stderr_artifact_id and result.stderr_artifact_id.startswith("artifact-")
        assert result.evidence_package_id and result.evidence_package_id.startswith("evidence-")
        artifacts = EvidenceRepository(connection).list_artifacts(result.evidence_package_id)
        artifact_ids = {artifact["id"] for artifact in artifacts}
        assert {result.stdout_artifact_id, result.stderr_artifact_id} <= artifact_ids
        for artifact in artifacts:
            assert Path(artifact["path"]).exists()
            assert artifact["kind"] == "execution_log"


def test_restricted_subprocess_requires_registered_workspace_when_connected(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        adapter = RestrictedSubprocessAdapter(connection=connection, artifact_root=tmp_path)
        request = runtime_request(tmp_path)

        result = adapter.execute(request)

        assert result.status == "blocked"
        assert "registered workspace" in (result.reason or "").lower()


def test_restricted_subprocess_never_uses_shell_true(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def capture_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("local_control_center.security_policy.sandbox.subprocess.run", capture_run)
    request = runtime_request(tmp_path)

    result = RestrictedSubprocessAdapter().execute(request)

    assert result.status == "completed"
    assert calls
    assert calls[0]["shell"] is False


def test_cli_version_adapter_rejects_non_version_execution(tmp_path: Path) -> None:
    request = runtime_request(tmp_path, capability="code_edit", argv=["codex", "exec", "change files"])

    result = CliVersionAdapter(adapter_id="codex").execute(request)

    assert result.status == "blocked"
    assert "version_check" in (result.reason or "")


def test_cli_version_adapter_reports_unavailable_when_executable_is_missing(tmp_path: Path) -> None:
    missing_executable = tmp_path / "missing-codex.exe"
    request = runtime_request(tmp_path, argv=[str(missing_executable), "--version"])

    result = CliVersionAdapter(adapter_id="codex").execute(request)

    assert result.status == "unavailable"
    assert result.exit_code is None
    assert result.reason


def test_provider_adapters_require_real_configuration(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "AIDO_OLLAMA_BASE_URL",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "AIDO_OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_OPENAI_COMPATIBLE_API_KEY",
        "AIDO_OPENAI_COMPATIBLE_MODEL",
        "OPENAI_COMPATIBLE_BASE_URL",
        "AIDO_ENABLE_REAL_PROVIDER_CALLS",
    ):
        monkeypatch.delenv(name, raising=False)

    ollama = OllamaAdapter().execute(runtime_request(tmp_path, capability="chat", argv=[]))
    openai_compatible = OpenAICompatibleAdapter().execute(
        runtime_request(tmp_path, capability="chat", argv=[])
    )

    assert ollama.status == "configuration_required"
    assert openai_compatible.status == "configuration_required"
    assert ollama.reason
    assert openai_compatible.reason


def test_runtime_adapter_registry_reports_unavailable_for_unregistered_adapter(tmp_path: Path) -> None:
    result = RuntimeAdapterRegistry().execute("missing_adapter", runtime_request(tmp_path))

    assert result.status == "unavailable"
    assert "not registered" in (result.reason or "").lower()


def test_product_runtime_adapter_files_do_not_define_mock_or_fake_adapters() -> None:
    prohibited = ("mock", "fake", "dummy", "placeholder", "demo")
    violations: list[str] = []
    for path in PRODUCT_ADAPTER_FILES:
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip().lower()
            if not stripped.startswith("class ") or "adapter" not in stripped:
                continue
            if any(term in stripped for term in prohibited):
                violations.append(f"{path.relative_to(ROOT).as_posix()}:{line_number}: {line.strip()}")

    assert not violations
