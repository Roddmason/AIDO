"""Revalida la salud de los runtimes de AIDO para devolverle ejecutables al product loop.

AIDO exige evidencia de salud EXPLICITA por runtime y esa evidencia no sobrevive al reinicio del
control plane: tras cada arranque el rollup vuelve a `0 executable runtimes` y los perfiles de
agente quedan en `blocked` aunque la configuracion siga intacta. Este script dispara el
health-check de cada proveedor habilitado y de cada CLI detectada, espera a que el worker drene
las ejecuciones encoladas y reporta cuantos perfiles de agente quedaron disponibles.

Requiere el control plane levantado y el worker en `running`: los health-checks son operaciones
encoladas, asi que con el worker pausado quedarian esperando para siempre.

Uso tipico:

    uv run python scripts/refresh_runtime_health.py
    uv run python scripts/refresh_runtime_health.py --api-base http://127.0.0.1:4951

Codigos de salida: 0 todos los perfiles quedaron disponibles, 1 quedan perfiles bloqueados,
2 el control plane no respondio o el worker no drena la cola.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API_BASE = "http://localhost:4310"
DEFAULT_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 3.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
USER_AGENT = "AIDO-RuntimeHealth/1.0"
CLI_SEGMENT_BY_KIND = {"provider": "providers", "cli": "cli-runtimes"}


class HealthRefreshError(RuntimeError):
    """Fallo irrecuperable al hablar con el control plane local."""


class ControlPlaneClient:
    """Cliente HTTP minimo del control plane local, con el token de escritura del handshake."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-Local-Control-Token"] = self.token()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise HealthRefreshError(f"{method} {path} -> HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise HealthRefreshError(
                f"{method} {path} -> sin respuesta de {self.base_url}: {error.reason}"
            ) from error
        return json.loads(body) if body.strip() else None

    def token(self) -> str:
        """Obtiene (y cachea) el token de escritura de loopback del handshake."""
        if self._token is None:
            handshake = self._request("GET", "/api/v1/security/handshake")
            self._token = str(handshake["token"])
        return self._token

    def get(self, path: str) -> Any:
        """Ejecuta una lectura contra el control plane."""
        return self._request("GET", path)

    def post(self, path: str, payload: Any = None) -> Any:
        """Ejecuta una escritura contra el control plane."""
        return self._request("POST", path, payload if payload is not None else {})


def discover_targets(client: ControlPlaneClient) -> list[tuple[str, str]]:
    """Lista los runtimes revalidables: cuentas habilitadas y CLIs efectivamente detectadas."""
    providers = client.get("/api/v1/model-gateway/providers") or {}
    runtimes = client.get("/api/v1/model-gateway/cli-runtimes") or {}
    targets: list[tuple[str, str]] = [
        ("provider", str(account["providerId"]))
        for account in providers.get("providers") or []
        if account.get("enabled")
    ]
    targets.extend(
        ("cli", str(runtime["id"]))
        for runtime in runtimes.get("cliRuntimes") or []
        if str(runtime.get("status")) != "offline"
    )
    return targets


def trigger_health_checks(
    client: ControlPlaneClient, targets: list[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """Encola un health-check por runtime y devuelve el executionId de cada uno que lo acepto.

    La clave incluye el tipo porque un mismo id (claude_code_cli, codex_cli) existe a la vez como
    cuenta del gateway y como CLI detectada, y cada ruta verifica algo distinto.
    """
    executions: dict[tuple[str, str], str] = {}
    for kind, runtime_id in targets:
        path = f"/api/v1/model-gateway/{CLI_SEGMENT_BY_KIND[kind]}/{runtime_id}/health-check"
        label = f"{runtime_id} ({kind})"
        try:
            response = client.post(path) or {}
        except HealthRefreshError as error:
            # Un runtime sin contrato de health-check (la ruta manual) no invalida a los demas.
            print(f"  - {label}: no acepta health-check ({str(error)[-90:]})")
            continue
        execution_id = str(response.get("executionId") or "")
        if execution_id:
            executions[kind, runtime_id] = execution_id
            print(f"  - {label}: encolado")
        else:
            print(f"  - {label}: respuesta sin executionId ({response.get('status')})")
    return executions


def wait_for_executions(
    client: ControlPlaneClient, executions: dict[tuple[str, str], str], timeout_seconds: int
) -> dict[tuple[str, str], str]:
    """Espera a que el worker cierre cada ejecucion; devuelve el estado final por runtime."""
    pending = dict(executions)
    results: dict[tuple[str, str], str] = {}
    deadline = time.monotonic() + timeout_seconds
    while pending and time.monotonic() < deadline:
        for target, execution_id in list(pending.items()):
            execution = client.get(f"/api/v1/executions/{execution_id}") or {}
            status = str(execution.get("status") or "unknown")
            if status in TERMINAL_STATUSES:
                results[target] = status
                pending.pop(target)
                print(f"  - {target[1]} ({target[0]}): {status}")
        if pending:
            time.sleep(POLL_INTERVAL_SECONDS)
    for target in pending:
        results[target] = "timeout"
        print(f"  - {target[1]} ({target[0]}): timeout tras {timeout_seconds}s; worker sin drenar")
    return results


def summarize_profiles(client: ControlPlaneClient) -> int:
    """Imprime el rollup de perfiles de agente y devuelve cuantos siguen bloqueados."""
    profiles = (client.get("/api/v1/agent-profiles") or {}).get("agentProfiles") or []
    blocked: list[dict[str, Any]] = []
    chosen: dict[str, int] = {}
    for profile in profiles:
        availability = profile.get("runtimeAvailability") or {}
        if availability.get("status") == "blocked":
            blocked.append(profile)
            continue
        provider_id = str(availability.get("selectedProviderId") or "sin-proveedor")
        chosen[provider_id] = chosen.get(provider_id, 0) + 1
    print(f"\nPerfiles disponibles: {len(profiles) - len(blocked)}/{len(profiles)}")
    for provider_id, count in sorted(chosen.items(), key=lambda item: -item[1]):
        print(f"  {provider_id}: {count}")
    for profile in blocked:
        availability = profile.get("runtimeAvailability") or {}
        reason = str(availability.get("blockedReason") or "").strip()
        print(f"  BLOQUEADO {profile.get('role')}: {reason[:160]}")
    return len(blocked)


def main() -> int:
    """Revalida todos los runtimes y reporta el rollup resultante."""
    parser = argparse.ArgumentParser(description="Revalida la salud de los runtimes de AIDO.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="URL del control plane local.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Segundos maximos de espera a que el worker drene las ejecuciones.",
    )
    args = parser.parse_args()

    client = ControlPlaneClient(args.api_base)
    try:
        worker = client.get("/api/v1/workers/status") or {}
        if not worker.get("running"):
            print(
                f"Worker en estado '{worker.get('status')}': los health-checks quedarian encolados. "
                "Reanudalo con POST /api/v1/workers/resume antes de reintentar.",
                file=sys.stderr,
            )
            return 2
        targets = discover_targets(client)
        if not targets:
            print("No hay runtimes habilitados que revalidar.", file=sys.stderr)
            return 2
        print("Runtimes a revalidar:")
        executions = trigger_health_checks(client, targets)
        print("\nEsperando al worker:")
        results = wait_for_executions(client, executions, args.timeout)
        blocked = summarize_profiles(client)
    except HealthRefreshError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    if "timeout" in results.values():
        return 2
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
