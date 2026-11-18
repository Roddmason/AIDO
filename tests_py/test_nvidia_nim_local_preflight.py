from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.nvidia_nim import api as nvidia_nim_api
from local_control_center.nvidia_nim.system_probe import (
    ProbeCommandResult,
    ReadOnlySystemProbe,
    SubprocessProbeCommandRunner,
)
from tests_py.control_plane_fixture import ControlPlaneFixture


def _create_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "platform.sqlite"
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(db_path))
    runtime = ControlPlaneFixture(cwd=tmp_path, db_path=db_path)
    runtime.init()
    return TestClient(create_app(runtime=runtime, static_dir=None))


def test_read_only_nvidia_nim_preflight_is_exposed_without_write_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutationAttempted"] is False
    assert body["probeScope"] == "aido_host_process"
    assert body["profiles"]


def _facts(
    *,
    platform_target: str = "wsl2",
    gpu_name: str = "NVIDIA GeForce RTX 3090",
    gpu_memory_gib: float = 24.0,
    driver_version: str = "595.79",
    compute_capability: str = "8.6",
    runtime_ready: bool = True,
    toolkit_installed: bool = False,
    cdi_available: bool = False,
    system_memory_gib: float = 24.0,
) -> dict[str, object]:
    return {
        "platform": {
            "os": "windows" if platform_target == "wsl2" else "linux",
            "target": platform_target,
            "architecture": "AMD64" if platform_target == "wsl2" else "x86_64",
            "wslAvailable": platform_target == "wsl2",
            "wslVersion": 2 if platform_target == "wsl2" else None,
            "distributions": ["Ubuntu-24.04-bot"] if platform_target == "wsl2" else [],
        },
        "gpus": [
            {
                "index": 0,
                "name": gpu_name,
                "memoryTotalGiB": gpu_memory_gib,
                "driverVersion": driver_version,
                "computeCapability": compute_capability,
            }
        ],
        "systemMemoryGiB": system_memory_gib,
        "swapGiB": 8.0,
        "diskFreeGiB": 200.0,
        "resourceScope": "runtime_target",
        "containerRuntimes": [
            {
                "id": "podman",
                "installed": True,
                "serverReachable": runtime_ready,
                "version": "4.9.3",
            }
        ],
        "containerToolkit": {
            "installed": toolkit_installed,
            "version": "1.18.0" if toolkit_installed else None,
            "cdiAvailable": cdi_available,
            "detectionScope": "aido_host_process",
        },
    }


def test_qwen_image_edit_reports_all_current_host_blockers_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _facts()
    monkeypatch.setattr(nvidia_nim_api, "collect_system_facts", lambda: facts, raising=False)
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "qwen-image-edit"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "blocked"
    assert body["mutationAttempted"] is False
    assert body["facts"] == facts
    assert len(body["profiles"]) == 1
    result = body["profiles"][0]
    assert result["profileId"] == "qwen-image-edit"
    assert result["modelId"] == "qwen/qwen-image-edit"
    assert result["status"] == "blocked_incompatible_hardware"
    assert result["hardwareCompatible"] is False
    assert result["runtimeReady"] is False
    assert result["observedGpuMemoryGiB"] == 24.0
    assert result["requiredGpuMemoryGiB"] == 80.0
    assert result["mutationAttempted"] is False
    blocker_codes = {item["code"] for item in result["blockers"]}
    assert {
        "wsl_gpu_generation_unsupported",
        "insufficient_gpu_memory",
        "insufficient_system_memory",
        "container_toolkit_not_detected",
        "cdi_not_detected",
    } <= blocker_codes
    assert result["requirement"]["sourceUrl"] == (
        "https://docs.nvidia.com/nim/visual-genai/latest/support-matrix.html"
    )
    assert result["requirement"]["verifiedAt"] == "2026-07-13"


def test_wsl_gpu_generation_is_blocked_even_when_reported_vram_is_sufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nvidia_nim_api,
        "collect_system_facts",
        lambda: _facts(gpu_memory_gib=96.0, system_memory_gib=128.0),
        raising=False,
    )
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "qwen-image-edit"})

    assert response.status_code == 200, response.text
    result = response.json()["profiles"][0]
    blocker_codes = {item["code"] for item in result["blockers"]}
    assert result["status"] == "blocked_incompatible_hardware"
    assert "wsl_gpu_generation_unsupported" in blocker_codes
    assert "insufficient_gpu_memory" not in blocker_codes


def test_wsl_requirements_must_be_satisfied_by_one_gpu_not_combined_across_devices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _facts(
        gpu_name="NVIDIA H100 80GB HBM3",
        gpu_memory_gib=80.0,
        driver_version="595.79",
        compute_capability="9.0",
        toolkit_installed=True,
        cdi_available=True,
        system_memory_gib=128.0,
    )
    facts["gpus"] = [
        facts["gpus"][0],
        {
            "index": 1,
            "name": "NVIDIA GeForce RTX 5090",
            "memoryTotalGiB": 32.0,
            "driverVersion": "595.79",
            "computeCapability": "10.0",
        },
    ]
    monkeypatch.setattr(nvidia_nim_api, "collect_system_facts", lambda: facts)
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "qwen-image-edit"})

    assert response.status_code == 200, response.text
    result = response.json()["profiles"][0]
    blockers = {item["code"]: item for item in result["blockers"]}
    assert result["status"] == "blocked_incompatible_hardware"
    assert blockers["insufficient_gpu_memory"]["observed"] == 32.0
    assert blockers["insufficient_gpu_memory"]["required"] == 80.0


def test_compatible_native_linux_fixture_is_ready_when_runtime_prerequisites_are_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nvidia_nim_api,
        "collect_system_facts",
        lambda: _facts(
            platform_target="linux",
            gpu_name="NVIDIA H100 80GB HBM3",
            gpu_memory_gib=80.0,
            driver_version="570.86.15",
            compute_capability="9.0",
            toolkit_installed=True,
            cdi_available=True,
            system_memory_gib=128.0,
        ),
        raising=False,
    )
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "qwen-image-edit"})

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["profiles"][0]
    assert body["status"] == "ready"
    assert result["status"] == "ready"
    assert result["hardwareCompatible"] is True
    assert result["runtimeReady"] is True
    assert result["blockers"] == []
    assert result["mutationAttempted"] is False


def test_runtime_blockers_are_actionable_and_independent_from_hardware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        nvidia_nim_api,
        "collect_system_facts",
        lambda: _facts(
            platform_target="linux",
            gpu_name="NVIDIA H100 80GB HBM3",
            gpu_memory_gib=80.0,
            driver_version="570.86.15",
            compute_capability="9.0",
            runtime_ready=False,
            toolkit_installed=False,
            cdi_available=False,
            system_memory_gib=128.0,
        ),
        raising=False,
    )
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "qwen-image-edit"})

    assert response.status_code == 200, response.text
    result = response.json()["profiles"][0]
    blockers = {item["code"]: item for item in result["blockers"]}
    assert result["status"] == "blocked_runtime_not_ready"
    assert result["hardwareCompatible"] is True
    assert result["runtimeReady"] is False
    assert {"container_runtime_unavailable", "container_toolkit_not_detected", "cdi_not_detected"} <= set(
        blockers
    )
    for code in ("container_runtime_unavailable", "container_toolkit_not_detected", "cdi_not_detected"):
        assert blockers[code]["actions"]
        assert all(action["kind"] != "execute_command" for action in blockers[code]["actions"])


def test_unknown_profile_is_rejected_without_running_a_system_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_probe() -> dict[str, object]:
        raise AssertionError("Unknown profile must be rejected before probing the host.")

    monkeypatch.setattr(nvidia_nim_api, "collect_system_facts", unexpected_probe, raising=False)
    client = _create_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/nvidia-nim/preflight", params={"profileId": "not-catalogued"})

    assert response.status_code == 404
    assert response.json()["detail"] == "NVIDIA NIM profile not found: not-catalogued"


def test_openapi_requires_read_only_guarantee_and_probe_scope_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _create_client(tmp_path, monkeypatch)
    schemas = client.app.openapi()["components"]["schemas"]

    response_required = set(schemas["NvidiaNimPreflightResponse"]["required"])
    profile_required = set(schemas["NvidiaNimProfilePreflightResult"]["required"])
    toolkit_required = set(schemas["NvidiaNimContainerToolkitFacts"]["required"])
    assert {"mutationAttempted", "probeScope"} <= response_required
    assert "mutationAttempted" in profile_required
    assert "detectionScope" in toolkit_required


class _FakeProbeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.executables = {
            "wsl.exe": "C:/probe/wsl.exe",
            "nvidia-smi": "C:/probe/nvidia-smi.exe",
            "docker": "C:/probe/docker.exe",
            "podman": "C:/probe/podman.exe",
            "nvidia-ctk": "C:/probe/nvidia-ctk.exe",
        }

    def which(self, command: str) -> str | None:
        return self.executables.get(command)

    def run(self, argv: list[str], *, timeout_seconds: float) -> ProbeCommandResult:
        assert timeout_seconds <= 2.0
        command = tuple(argv)
        self.commands.append(command)
        executable = Path(argv[0]).stem.lower()
        args = tuple(argv[1:])
        responses = {
            ("wsl", ("--list", "--verbose")): ProbeCommandResult(
                0,
                "NAME STATE VERSION\n* Ubuntu-24.04 Running 2\n",
            ),
            (
                "nvidia-smi",
                (
                    "--query-gpu=index,name,memory.total,driver_version,compute_cap",
                    "--format=csv,noheader,nounits",
                ),
            ): ProbeCommandResult(0, "0, NVIDIA GeForce RTX 3090, 24576, 595.79, 8.6\n"),
            ("docker", ("--version",)): ProbeCommandResult(
                0,
                "Docker version 28.0.1, build 055a478\n",
            ),
            ("docker", ("version", "--format", "{{.Server.Version}}")): ProbeCommandResult(-1, ""),
            ("podman", ("--version",)): ProbeCommandResult(0, "podman version 4.9.3\n"),
            ("podman", ("info", "--format", "json")): ProbeCommandResult(0, "{}\n"),
            ("nvidia-ctk", ("--version",)): ProbeCommandResult(
                0, "NVIDIA Container Toolkit CLI version 1.18.0\n"
            ),
            ("nvidia-ctk", ("cdi", "list")): ProbeCommandResult(0, "nvidia.com/gpu=all\n"),
        }
        return responses[(executable, args)]


def test_system_probe_uses_only_bounded_read_only_commands_and_returns_normalized_facts() -> None:
    runner = _FakeProbeRunner()

    facts = ReadOnlySystemProbe(
        runner=runner,
        system_name="Windows",
        machine="AMD64",
        release="11",
        environment={},
    ).collect()

    assert facts.platform.target == "wsl2"
    assert facts.platform.wsl_version == 2
    assert facts.platform.distributions == ["Ubuntu-24.04"]
    assert facts.resource_scope == "host"
    assert facts.gpus[0].name == "NVIDIA GeForce RTX 3090"
    assert facts.gpus[0].memory_total_gib == 24.0
    runtimes = {item.id: item for item in facts.container_runtimes}
    assert runtimes["docker"].installed is True
    assert runtimes["docker"].server_reachable is False
    assert runtimes["docker"].version == "28.0.1"
    assert runtimes["podman"].server_reachable is True
    assert facts.container_toolkit.installed is True
    assert facts.container_toolkit.cdi_available is True
    forbidden_tokens = {"build", "create", "install", "pull", "run", "start", "stop"}
    assert not any(forbidden_tokens.intersection(command[1:]) for command in runner.commands)


def test_subprocess_probe_rejects_mutating_subcommands_before_process_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "local_control_center.nvidia_nim.system_probe.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("A rejected command must never reach subprocess.run.")
        ),
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        SubprocessProbeCommandRunner().run(
            ["docker", "run", "--rm", "untrusted-image"],
            timeout_seconds=2.0,
        )
