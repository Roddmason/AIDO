from __future__ import annotations

import pytest


@pytest.mark.parametrize("version", ["codex-cli 0.142.2", "codex-cli 0.149.0", "codex-cli 9.0.0"])
def test_capabilities_without_smoke_never_accept_a_version(version):
    from local_control_center.agents.codex_compatibility import REQUIRED_FLAGS, assess_capabilities

    result = assess_capabilities(version, " ".join(REQUIRED_FLAGS), "binary-sha", None)
    assert result["status"] == "validation_required"
    assert "validated_smoke_missing" in result["blockingReasons"]


@pytest.mark.parametrize("version", ["codex-cli 0.142.2", "codex-cli 0.149.0"])
def test_matching_smoke_and_flags_accept_legacy_and_current(version):
    from local_control_center.agents.codex_compatibility import (
        REQUIRED_FLAGS,
        assess_capabilities,
        contract_fingerprint,
    )

    receipt = {
        "version": version,
        "binaryFingerprint": "sha",
        "contractFingerprint": contract_fingerprint(),
        "status": "validated",
    }
    assert assess_capabilities(version, " ".join(REQUIRED_FLAGS), "sha", receipt)["status"] == "compatible"
    assert (
        assess_capabilities(version, " ".join(REQUIRED_FLAGS), "changed", receipt)["status"]
        == "validation_required"
    )
    assert assess_capabilities(version, "--sandbox", "sha", receipt)["status"] == "incompatible"


@pytest.mark.parametrize(
    "version,expected",
    [
        ("codex-cli 0.149.0", (0, 149, 0)),
        ("codex-cli 0.149.0-alpha.1", None),
        ("garbage 0.149.0", None),
        ("codex-cli 0.149.0 extra", None),
    ],
)
def test_semver_parser_is_strict(version, expected):
    from local_control_center.agents.codex_compatibility import parse_codex_version

    assert parse_codex_version(version) == expected


def test_model_alias_requires_a_real_enabled_catalog_model(tmp_path):
    from local_control_center.agents.model_aliases import resolve_model_alias
    from local_control_center.agents.provider_accounts import ProviderAccountStore
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        with pytest.raises(ValueError, match="model_configuration_required"):
            resolve_model_alias(runtime.connection, provider_id="codex_cli", alias="deep_coding")
        store = ProviderAccountStore(runtime.connection)
        store.upsert_model(
            {
                "providerId": "codex_cli",
                "model": "test-current-coding",
                "displayName": "Test current",
                "enabled": True,
                "source": "manual",
                "supportsReasoning": True,
            }
        )
        model = resolve_model_alias(runtime.connection, provider_id="codex_cli", alias="deep_coding")
        assert model == "test-current-coding"
        runtime.connection.execute("UPDATE model_catalog SET enabled=0 WHERE provider_id='codex_cli'")
        with pytest.raises(ValueError, match="model_configuration_required"):
            resolve_model_alias(runtime.connection, provider_id="codex_cli", alias="review")
    finally:
        runtime.close()


def test_smoke_output_rejects_tool_activity_unknown_events_and_incomplete_turns():
    import json

    from local_control_center.agents.codex_smoke import SMOKE_MARKER, smoke_output_is_safe

    events = [
        {"type": "thread.started"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": SMOKE_MARKER}},
        {"type": "turn.completed"},
    ]

    def encode(items):
        return "\n".join(json.dumps(item) for item in items)

    assert smoke_output_is_safe(encode(events))
    assert not smoke_output_is_safe(encode(events[:-1]))
    assert not smoke_output_is_safe(encode([*events, {"type": "future.event"}]))
    assert not smoke_output_is_safe(
        encode([*events, {"type": "item.completed", "item": {"type": "command_execution"}}])
    )


def test_probe_fingerprint_change_invalidates_previous_capabilities(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from local_control_center.agents import codex_compatibility as module
    from local_control_center.control_plane.runtime import ControlCenterRuntime

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"test binary not executed")
    monkeypatch.setattr(module.shutil, "which", lambda executable: str(binary))
    monkeypatch.setattr(
        module,
        "run_probe_command",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="codex-cli 0.149.0" if argv[-1] == "--version" else " ".join(module.REQUIRED_FLAGS),
        ),
    )
    try:
        service = module.CodexCompatibilityService(runtime.connection)
        assert service.probe(str(binary))["status"] == "validation_required"
        binary.write_bytes(b"updated test binary")
        assert service.status(str(binary))["blockingReasons"] == ["executable_changed"]
    finally:
        runtime.close()


@pytest.mark.parametrize("authenticated", [False, True])
def test_smoke_blocks_missing_auth_or_catalog_without_executing_a_cli(tmp_path, monkeypatch, authenticated):
    from types import SimpleNamespace

    from local_control_center.agents import codex_smoke as module
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
    from tests_py.test_model_runtime_gateway import register_workspace

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    try:
        connection = runtime.connection
        register_workspace(connection, "smoke-workspace", tmp_path)
        repository = RuntimeConfigRepository(connection)
        repository.upsert_installation({"runtimeId": "codex_cli", "enabled": True, "executablePath": "codex"})
        connection.execute("UPDATE provider_accounts SET enabled=1 WHERE provider_id='codex_cli'")
        connection.execute("UPDATE runtime_accounts SET enabled=1 WHERE runtime_id='codex_cli'")
        monkeypatch.setattr(
            module.CodexCompatibilityService,
            "probe",
            lambda *args: {"status": "validation_required", "blockingReasons": ["validated_smoke_missing"]},
        )
        monkeypatch.setattr(
            module.CodexCliRuntime,
            "validate_native_auth",
            lambda self: SimpleNamespace(status="authenticated" if authenticated else "unknown"),
        )

        def unexpected_run(*args, **kwargs):
            raise AssertionError("No CLI execution is authorized without auth and a catalog model")

        monkeypatch.setattr(module.CodexCliRuntime, "run", unexpected_run)
        result = module.run_codex_smoke(
            connection,
            module.CodexSmokeRequest(
                workspaceId="smoke-workspace", reason="Test preconditions", approved=True
            ),
        )
        assert result["status"] == "configuration_required"
        assert ("model_configuration_required" if authenticated else "authentication") in result["reason"]
        assert connection.execute("SELECT COUNT(*) FROM codex_smoke_receipts").fetchone()[0] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("wrong_command", [False, True])
def test_smoke_receipt_requires_its_own_command_and_updates_compatibility(
    tmp_path, monkeypatch, wrong_command
):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace

    from local_control_center.agents import codex_compatibility, codex_smoke
    from local_control_center.agents.cli_runtimes.base import RuntimeResult
    from local_control_center.agents.provider_accounts import ProviderAccountStore
    from local_control_center.control_plane.runtime import ControlCenterRuntime
    from local_control_center.process_supervision.service import command_fingerprint
    from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
    from local_control_center.shared.time import utc_now
    from tests_py.test_model_runtime_gateway import register_workspace

    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "runtime.sqlite")
    runtime.init()
    binary = tmp_path / "codex.exe"
    binary.write_bytes(b"test-only binary, never executed")
    try:
        connection = runtime.connection
        register_workspace(connection, "smoke-workspace", tmp_path)
        RuntimeConfigRepository(connection).upsert_installation(
            {"runtimeId": "codex_cli", "enabled": True, "executablePath": str(binary)}
        )
        connection.execute("UPDATE provider_accounts SET enabled=1 WHERE provider_id='codex_cli'")
        connection.execute("UPDATE runtime_accounts SET enabled=1 WHERE runtime_id='codex_cli'")
        ProviderAccountStore(connection).upsert_model(
            {
                "providerId": "codex_cli",
                "model": "test-current",
                "displayName": "Test model",
                "source": "manual",
                "enabled": True,
            }
        )
        monkeypatch.setattr(
            codex_compatibility,
            "run_probe_command",
            lambda argv, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="codex-cli 0.149.0"
                if argv[-1] == "--version"
                else " ".join(codex_compatibility.REQUIRED_FLAGS),
            ),
        )
        monkeypatch.setattr(
            codex_smoke.CodexCliRuntime,
            "validate_native_auth",
            lambda self: SimpleNamespace(status="authenticated"),
        )
        monkeypatch.setattr(codex_smoke, "isolated_product_owner_codex_environment", lambda: nullcontext({}))

        def fake_run(self, request, **kwargs):
            command = self.build_command(request)
            assert "read-only" in command and "--strict-config" in command
            connection.execute(
                """INSERT INTO managed_processes
                (managed_process_id, execution_id, root_pid, workload_class, command_fingerprint,
                 started_at, finished_at, exit_code, released_at)
                VALUES ('test-smoke-process', 'test-smoke', 0, 'agent_cli', ?, ?, ?, 0, ?)""",
                (
                    command_fingerprint(["unrelated", "command"] if wrong_command else command),
                    utc_now(),
                    utc_now(),
                    utc_now(),
                ),
            )
            return RuntimeResult(
                runtime="codex_cli",
                status="completed",
                command=["codex"],
                returnCode=0,
                stdout="\n".join(
                    json.dumps(event)
                    for event in [
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": codex_smoke.SMOKE_MARKER},
                        },
                        {"type": "turn.completed"},
                    ]
                ),
                processEvidence={
                    "managedProcessId": "test-smoke-process",
                    "remainingDescendantCount": 0,
                    "stdoutCaptureTruncated": False,
                },
            )

        monkeypatch.setattr(codex_smoke.CodexCliRuntime, "run", fake_run)
        result = codex_smoke.run_codex_smoke(
            connection,
            codex_smoke.CodexSmokeRequest(
                workspaceId="smoke-workspace",
                approved=True,
                reason="Test-only injected runtime",
            ),
        )
        assert result["status"] == ("failed" if wrong_command else "validated")
        status = codex_compatibility.CodexCompatibilityService(connection).status(str(binary))
        assert status["status"] == ("validation_required" if wrong_command else "compatible")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        runtime.close()
