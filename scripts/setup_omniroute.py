"""Configura OmniRoute como runtime de modelo de AIDO para todos los perfiles de agente.

Deja la cuenta `omniroute` lista y elegible por el product loop: crea o actualiza la cuenta desde
el catálogo, habilita la política de runtime remoto, siembra las capabilities que los roles de
build y review exigen, corre el health-check, sincroniza y cura el catálogo de modelos, declara
precio cero verificable y pinea el gateway en las políticas de rol.

Todos los pasos son idempotentes: reejecutarlo converge al mismo estado y no duplica nada. La
curación va siempre pegada al sync porque `sync-models` reactiva todo lo que el gateway anuncie.

Uso típico (con el control plane y OmniRoute levantados):

    uv run python scripts/setup_omniroute.py
    uv run python scripts/setup_omniroute.py --dry-run
    uv run python scripts/setup_omniroute.py --rollback

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from local_control_center.shared.settings import default_db_path  # noqa: E402

PROVIDER_ID = "omniroute"
CATALOG_PROVIDER_ID = "omniroute"
DEFAULT_API_BASE = "http://localhost:4310"
DEFAULT_GATEWAY_URL = "http://localhost:20128/v1"
DEFAULT_MODELS_FILE = REPO_ROOT / "scripts" / "omniroute_models.json"
# `chat` es obligatorio para ejecutar y las filas por provider_id ocultan por completo las de la
# familia, así que se siembra junto a las que habilitan los roles de build (code) y review.
RUNTIME_CAPABILITIES = ("chat", "code_edit", "code_review")
BLOCKING_PROJECT_RUNTIME_MODES = {"cli", "ollama", "manual"}
PREFERRED_MODEL_WILDCARD = "*"


class SetupError(RuntimeError):
    """Falla accionable de configuración: se reporta al operador sin traceback."""


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class ControlPlaneClient:
    """Cliente HTTP mínimo del control plane local, con el token de escritura del handshake."""

    def __init__(self, base_url: str, *, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._token: str | None = None

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-Local-Control-Token"] = self.token()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:400]
            raise SetupError(f"{method} {path} -> HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise SetupError(
                f"{method} {path} -> no hay respuesta de {self.base_url}: {error.reason}"
            ) from error
        return json.loads(body) if body.strip() else None

    def token(self) -> str:
        """Obtiene (y cachea) el token de escritura de loopback del handshake."""
        if self._token is None:
            handshake = self._request("GET", "/api/v1/security/handshake")
            self._token = str(handshake["token"])
        return self._token

    def get(self, path: str) -> Any:
        """Ejecuta un GET; las lecturas corren igual en dry-run."""
        return self._request("GET", path)

    def write(self, method: str, path: str, payload: Any = None) -> Any:
        """Ejecuta una escritura, o solo la anuncia cuando el modo es dry-run."""
        if self.dry_run:
            print(f"  [dry-run] {method} {path}")
            return None
        return self._request(method, path, payload)


def check_gateway(gateway_url: str) -> list[str]:
    """Comprueba que OmniRoute responde y devuelve los ids de modelo que anuncia."""
    request = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/models", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        hint = " (¿REQUIRE_API_KEY=true sin credencial configurada?)" if error.code == 401 else ""
        raise SetupError(f"OmniRoute respondió HTTP {error.code} en /models{hint}") from error
    except urllib.error.URLError as error:
        raise SetupError(
            f"OmniRoute no responde en {gateway_url}: {error.reason}. Levántalo con `omniroute serve --port 20128`."
        ) from error
    return [str(item.get("id") or "") for item in (payload.get("data") or [])]


def load_allowlist(models_file: Path, override: str | None) -> list[dict[str, Any]]:
    """Carga la allowlist de modelos, con override por línea de comandos."""
    if override:
        return [{"model": name.strip()} for name in override.split(",") if name.strip()]
    try:
        document = json.loads(models_file.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SetupError(f"No existe la allowlist {models_file}") from error
    except json.JSONDecodeError as error:
        raise SetupError(f"Allowlist inválida en {models_file}: {error}") from error
    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise SetupError(f"{models_file} no declara ningún modelo en 'models'.")
    return models


def upsert_account(client: ControlPlaneClient, *, gateway_url: str, credential_ref: str | None) -> None:
    """Crea o actualiza la cuenta del gateway con precio declarado libre y localidad honesta."""
    payload: dict[str, Any] = {
        "providerId": CATALOG_PROVIDER_ID,
        "baseUrl": gateway_url,
        "enabled": True,
        "pricingMode": "free",
        # El gateway corre en localhost pero reenvía a proveedores externos: sin este marcador AIDO
        # lo clasifica como local y le atribuye privacidad local_private, que sería falso.
        "metadata": {"endpointKind": "remote", "gateway": "omniroute"},
    }
    if credential_ref:
        payload["credentialRef"] = credential_ref
    client.write("POST", "/api/v1/provider-accounts/from-catalog", payload)


def enable_remote_policy(client: ControlPlaneClient, *, project_id: str | None) -> list[str]:
    """Habilita la política de runtime remoto y avisa si el modo del proyecto bloquea el gateway."""
    warnings: list[str] = []
    client.write("PUT", "/api/v1/settings/runtime.remote.enabled", {"scope": "general", "value": True})
    if project_id:
        client.write(
            "PUT",
            "/api/v1/settings/project.runtime.remote.enabled",
            {"scope": "project", "scopeId": project_id, "value": True},
        )
        settings = client.get(f"/api/v1/settings?projectId={project_id}") or {}
        for item in settings.get("settings") or []:
            if str(item.get("key")) == "project.runtime.defaultMode":
                mode = str(item.get("value") or "")
                if mode in BLOCKING_PROJECT_RUNTIME_MODES:
                    warnings.append(
                        f"project.runtime.defaultMode={mode} bloquea los runtimes de tipo gateway; "
                        "cámbialo a 'api' o 'hybrid' para que el loop pueda elegir OmniRoute."
                    )
    return warnings


def seed_capabilities(db_path: Path, *, dry_run: bool) -> None:
    """Siembra las capabilities del runtime que los roles de build y review exigen al seleccionar."""
    if dry_run:
        print(f"  [dry-run] UPSERT runtime_capabilities {PROVIDER_ID}: {', '.join(RUNTIME_CAPABILITIES)}")
        return
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    metadata = json.dumps({"source": "setup_omniroute"})
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        for capability in RUNTIME_CAPABILITIES:
            connection.execute(
                """
                INSERT INTO runtime_capabilities
                    (id, runtime, capability, enabled, metadata, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(runtime, capability) DO UPDATE SET
                    enabled = 1,
                    metadata = excluded.metadata,
                    updated_at = excluded.updated_at
                """,
                (f"{PROVIDER_ID}:{capability}", PROVIDER_ID, capability, metadata, now, now),
            )
        connection.commit()
    finally:
        connection.close()


def curate_models(client: ControlPlaneClient, allowlist: list[dict[str, Any]]) -> tuple[int, int]:
    """Deja habilitados solo los modelos de la allowlist y les fija capacidades y precio cero."""
    wanted = {str(entry["model"]): entry for entry in allowlist}
    catalog = client.get("/api/v1/model-gateway/models") or {}
    disabled = 0
    for row in catalog.get("models") or []:
        if str(row.get("providerId")) != PROVIDER_ID:
            continue
        model = str(row.get("model"))
        if model not in wanted and row.get("enabled"):
            client.write("PATCH", f"/api/v1/model-gateway/models/{PROVIDER_ID}:{model}", {"enabled": False})
            disabled += 1
    for model, entry in wanted.items():
        patch = {
            "enabled": True,
            "supportsTools": bool(entry.get("supportsTools", False)),
            "supportsJson": bool(entry.get("supportsJson", True)),
            "supportsReasoning": bool(entry.get("supportsReasoning", False)),
            "freeTier": True,
        }
        if entry.get("contextWindow"):
            patch["contextWindow"] = int(entry["contextWindow"])
        if entry.get("maxOutputTokens"):
            patch["maxOutputTokens"] = int(entry["maxOutputTokens"])
        client.write("PATCH", f"/api/v1/model-gateway/models/{PROVIDER_ID}:{model}", patch)
        # Precio cero explícito y no "desconocido": con costo conocido el gate de costo desconocido
        # no aplica y el failover considera al candidato asequible en vez de descartarlo.
        client.write(
            "POST",
            "/api/v1/model-gateway/pricing-snapshots",
            {
                "providerId": PROVIDER_ID,
                "model": model,
                "inputPricePerMtok": 0,
                "cachedInputPricePerMtok": 0,
                "outputPricePerMtok": 0,
                "reasoningPricePerMtok": 0,
                "freeTier": True,
                "sourceRef": "omniroute-free-tier",
                "applyToCatalog": True,
            },
        )
    return len(wanted), disabled


def pin_role_policies(
    client: ControlPlaneClient, *, backup_dir: Path, priority_first: bool, dry_run: bool
) -> tuple[int, list[str], Path | None]:
    """Pinea el gateway en todas las políticas de rol, respaldando primero el estado anterior.

    Una política cuyo estado persistido ya viola la validación del endpoint (el PATCH revalida el
    resultado fusionado) no puede modificarse por API. Eso es deuda ajena a este script, así que se
    reporta por rol y el resto sigue: abortar dejaría la configuración a medio aplicar.
    """
    policies = (client.get("/api/v1/model-gateway/role-policies") or {}).get("rolePolicies") or []
    backup_path: Path | None = None
    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"role_policies_{_utc_stamp()}.json"
        backup_path.write_text(json.dumps(policies, indent=2), encoding="utf-8")
    candidate = {"provider": PROVIDER_ID, "model": PREFERRED_MODEL_WILDCARD}
    patched = 0
    failures: list[str] = []
    for policy in policies:
        preferred = [
            item
            for item in (policy.get("preferred") or [])
            if str((item or {}).get("provider")) != PROVIDER_ID
        ]
        preferred = [candidate, *preferred] if priority_first else [*preferred, candidate]
        try:
            client.write(
                "PATCH",
                f"/api/v1/model-gateway/role-policies/{policy['id']}",
                {"preferred": preferred, "allowApi": True, "allowRemote": True},
            )
        except SetupError as error:
            failures.append(f"{policy.get('role') or policy['id']}: {error}")
            continue
        patched += 1
    return patched, failures, backup_path


def verify_routing(client: ControlPlaneClient) -> list[str]:
    """Previsualiza el ruteo de los roles que ejecutan modelo y reporta a quién resolvió."""
    lines: list[str] = []
    for role in ("developer", "product_owner"):
        preview = client.write("POST", "/api/v1/model-gateway/route/preview", {"role": role}) or {}
        selection = preview.get("selected") or {}
        lines.append(
            f"  {role}: provider={selection.get('provider') or '-'} "
            f"model={selection.get('model') or '-'} costUsd={preview.get('estimatedCostUsd')}"
        )
    return lines


def rollback(client: ControlPlaneClient, *, backup_dir: Path) -> None:
    """Deshabilita la cuenta y restaura las políticas de rol del respaldo más reciente."""
    client.write("PATCH", f"/api/v1/model-gateway/providers/{PROVIDER_ID}", {"enabled": False})
    print(f"Cuenta {PROVIDER_ID} deshabilitada.")
    backups = sorted(backup_dir.glob("role_policies_*.json")) if backup_dir.exists() else []
    if not backups:
        print("No hay respaldo de políticas de rol; no se restauró nada.")
        return
    policies = json.loads(backups[-1].read_text(encoding="utf-8"))
    for policy in policies:
        client.write(
            "PATCH",
            f"/api/v1/model-gateway/role-policies/{policy['id']}",
            {"preferred": policy.get("preferred") or []},
        )
    print(f"Políticas de rol restauradas desde {backups[-1].name} ({len(policies)} filas).")


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos del script."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="URL del control plane de AIDO.")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL, help="Base URL de OmniRoute.")
    parser.add_argument(
        "--credential-ref",
        default=None,
        help="Referencia de credencial existente (env:/keyring:/vault:) si OmniRoute exige API key.",
    )
    parser.add_argument("--models-file", type=Path, default=DEFAULT_MODELS_FILE, help="Allowlist de modelos.")
    parser.add_argument("--models", default=None, help="Allowlist inline separada por comas (override).")
    parser.add_argument("--project-id", default=None, help="Proyecto donde habilitar el runtime remoto.")
    parser.add_argument("--db-path", type=Path, default=None, help="SQLite de la plataforma.")
    parser.add_argument(
        "--priority",
        choices=("first", "after-clis"),
        default="first",
        help="Posición del gateway en el 'preferred' de cada política de rol.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Muestra las mutaciones sin ejecutarlas.")
    parser.add_argument("--rollback", action="store_true", help="Deshabilita la cuenta y restaura políticas.")
    return parser


def main() -> int:
    """Ejecuta la configuración completa y devuelve el código de salida del proceso."""
    args = build_parser().parse_args()
    db_path = args.db_path or default_db_path()
    backup_dir = db_path.parent / "backups"
    client = ControlPlaneClient(args.api_base, dry_run=args.dry_run)

    try:
        if args.rollback:
            rollback(client, backup_dir=backup_dir)
            return 0

        print("1/8 Verificando OmniRoute y el control plane…")
        announced = check_gateway(args.gateway_url)
        client.get("/api/v1/model-gateway/overview")
        print(f"  OmniRoute anuncia {len(announced)} modelos; control plane responde en {args.api_base}.")

        allowlist = load_allowlist(args.models_file, args.models)
        missing = [entry["model"] for entry in allowlist if entry["model"] not in announced]
        if missing:
            print(f"  Aviso: la allowlist pide modelos que el gateway no anuncia: {', '.join(missing)}")

        print("2/8 Creando o actualizando la cuenta del gateway…")
        upsert_account(client, gateway_url=args.gateway_url, credential_ref=args.credential_ref)

        print("3/8 Habilitando la política de runtime remoto…")
        warnings = enable_remote_policy(client, project_id=args.project_id)

        print("4/8 Sembrando capabilities del runtime…")
        seed_capabilities(db_path, dry_run=args.dry_run)

        print("5/8 Ejecutando health-check…")
        health = client.write("POST", f"/api/v1/model-gateway/providers/{PROVIDER_ID}/health-check") or {}
        status = str((health.get("health") or {}).get("healthStatus") or "desconocido")
        print(f"  healthStatus={status}")

        print("6/8 Sincronizando y curando el catálogo de modelos…")
        client.write("POST", f"/api/v1/provider-accounts/{PROVIDER_ID}/sync-models")
        enabled, disabled = curate_models(client, allowlist)
        print(
            f"  {enabled} modelos habilitados con precio cero; {disabled} deshabilitados fuera de allowlist."
        )

        print("7/8 Pineando el gateway en las políticas de rol…")
        patched, pin_failures, backup_path = pin_role_policies(
            client, backup_dir=backup_dir, priority_first=args.priority == "first", dry_run=args.dry_run
        )
        print(f"  {patched} políticas actualizadas" + (f"; respaldo en {backup_path}" if backup_path else ""))
        for failure in pin_failures:
            warnings.append(f"política de rol no modificable por API -> {failure}")

        print("8/8 Verificando el ruteo por rol…")
        for line in verify_routing(client):
            print(line)

        for warning in warnings:
            print(f"AVISO: {warning}")
        print("Listo. Reejecuta este script tras cualquier sync manual de modelos.")
    except SetupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
