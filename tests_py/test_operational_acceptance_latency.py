"""Latencia HTTP real bajo un árbol pequeño y acotado; no mide paint del navegador.

@author Rodrigo Mason
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from local_control_center.host_resources.probes import HostResourceProbe
from local_control_center.process_supervision.service import ProcessSupervisorService
from tests_py.operational_acceptance_support import evidence, identities_gone, native_readback, wait_until


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native load acceptance")
def test_panel_http_and_cancellation_under_bounded_native_load(tmp_path, low_impact_host_policy):
    db = tmp_path / "platform.sqlite"
    policy = low_impact_host_policy(db)
    host = HostResourceProbe(relevant_paths=[tmp_path, Path.cwd()]).sample()
    assert not host.unreal_editor_running and not host.ollama_running
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    service = ProcessSupervisorService(db_path=db)
    api = service.start(
        argv=[
            sys.executable,
            "-m",
            "local_control_center",
            "--dashboard-only",
            "--dashboard-host",
            "127.0.0.1",
            "--dashboard-port",
            str(port),
            "--db-path",
            str(db),
            "--workspace",
            str(tmp_path),
            "--static-dir",
            str(Path("local-control-center/dist/web").resolve()),
        ],
        cwd=Path.cwd(),
        workload_class="qa_light",
        cpu_limit_percent=10,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    load = None
    report = {
        "status": "FAIL",
        "fixturePolicy": policy,
        "preflight": host.model_dump(by_alias=True),
        "samples": [],
        "scope": "real loopback HTTP + Windows test writer, not browser paint or provider inference",
    }
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False) as client:

            def healthy():
                try:
                    return client.get("/healthz").status_code == 200
                except httpx.HTTPError:
                    return False

            wait_until(healthy, 30)
            token = client.get("/api/v1/security/handshake").json()["token"]
            load = service.start(
                argv=[
                    sys.executable,
                    "-m",
                    "tests_py.operational_acceptance_worker",
                    "writer",
                    str(tmp_path),
                    "--depth",
                    "1",
                    "--duration",
                    "30",
                ],
                cwd=Path.cwd(),
                workload_class="qa_light",
                cpu_limit_percent=5,
                memory_limit_bytes=256 * 1024**2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            wait_until(lambda: len(list(tmp_path.glob("writer-*.log"))) >= 2)
            report["apiNative"] = native_readback(api)
            report["loadNative"] = native_readback(load)
            previous_cpu = (
                service.backend.stats(api).cpu_time_seconds + service.backend.stats(load).cpu_time_seconds
            )
            previous_time = time.monotonic()
            psutil.cpu_percent()
            for _ in range(20):
                time.sleep(0.25)
                row = {"timestamp": time.time(), "hostCpuPercent": psutil.cpu_percent(), "latencyMs": {}}
                for endpoint in ["/", "/healthz", "/api/v1/overview", "/api/v1/workers/status"]:
                    start = time.monotonic()
                    response = client.get(endpoint)
                    row["latencyMs"][endpoint] = (time.monotonic() - start) * 1000
                    assert response.status_code == 200
                    assert row["latencyMs"][endpoint] < 1000
                current_cpu = (
                    service.backend.stats(api).cpu_time_seconds + service.backend.stats(load).cpu_time_seconds
                )
                current_time = time.monotonic()
                row["aidoHostNormalizedCpuPercent"] = (
                    (current_cpu - previous_cpu)
                    / (current_time - previous_time)
                    / host.logical_processors
                    * 100
                )
                row["activeTestTrees"] = 2
                row["availableMemoryBytes"] = psutil.virtual_memory().available
                previous_cpu, previous_time = current_cpu, current_time
                report["samples"].append(row)
            start = time.monotonic()
            response = client.post(
                f"/api/v1/operations/processes/{load.managed_process_id}/cancel",
                headers={"X-Local-Control-Token": token},
                json={"reason": "bounded load acceptance"},
            )
            report["cancelHttpMs"] = (time.monotonic() - start) * 1000
            assert response.status_code == 202
            wait_until(lambda: identities_gone(report["loadNative"]["members"]), 5)
            report["cancelTreeGoneMs"] = (time.monotonic() - start) * 1000
            report["cancelHttpStatus"] = response.status_code
            report["status"] = "PASS"
    finally:
        if load is not None and not load.released:
            service.cancel(load.managed_process_id, reason="acceptance cleanup")
        if not api.released:
            service.cancel(api.managed_process_id, reason="acceptance cleanup")
        report["remainingApiIdentities"] = (
            0 if identities_gone(report.get("apiNative", {}).get("members", [])) else None
        )
        evidence("latency", report)
