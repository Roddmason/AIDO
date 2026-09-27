"""Uso de cuota por proveedor: porcentaje observado, umbral del operador y suspensión automática.

El operador fija un umbral (``runtime.quota.suspendThresholdPercent``, 80 % por defecto, y un override
por proveedor en ``provider_usage_policies``). Cuando una ventana de uso de un proveedor lo alcanza,
el proveedor queda **suspendido por cuota** hasta el reinicio de esa ventana: no se apaga su switch
(``provider_accounts.enabled`` es la decisión del operador y ninguna automatización la reescribe),
pero ``QuotaManager.providers_in_cooldown`` lo incluye, así que el estado de runtime lo marca no
ejecutable, la selección lo salta, el failover a mitad de loop lo excluye y la reanudación automática
retoma los hilos bloqueados cuando vuelve a estar disponible.

Fuentes del porcentaje (best effort; sin dato, el proveedor no se suspende por umbral):

- Claude Code CLI: ``GET https://api.anthropic.com/api/oauth/usage`` con el token OAuth local del CLI
  (``~/.claude/.credentials.json`` o ``CLAUDE_CODE_OAUTH_TOKEN``); ventanas ``five_hour``/``seven_day``
  con ``utilization`` 0-100 y ``resets_at`` ISO. No consume cuota; se consulta como mucho cada 3 min.
- Codex CLI: primero el app-server oficial (``codex app-server``, JSON-RPC por stdio,
  ``account/rateLimits/read``; ver ``codex_app_server.py``); ante cualquier falla, ``GET
  https://chatgpt.com/backend-api/wham/usage`` (no documentado) con el token de ``~/.codex/auth.json``;
  ventanas ``primary_window``/``secondary_window`` con ``used_percent`` y ``reset_at`` (epoch).
- Proveedores con límites configurados en AIDO (``provider_limits``): la contabilidad propia de AIDO
  (``QuotaManager.status``) como uso sobre el límite diario/mensual.

Ninguna consulta corre dentro de una transacción SQLite: el refresco lo dispara el worker líder o la
API, y la suspensión se lee solo de la base.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_control_center.agents.codex_app_server import CodexAppServerError, read_codex_rate_limits
from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.time import utc_now

THRESHOLD_SETTING_KEY = "runtime.quota.suspendThresholdPercent"
DEFAULT_THRESHOLD_PERCENT = 80.0
MIN_POLL_INTERVAL_SECONDS = 180
"""Intervalo mínimo entre consultas remotas por proveedor (el endpoint de Claude limita por debajo)."""
UNKNOWN_RESET_SUSPENSION = timedelta(hours=1)
"""Suspensión cuando la fuente no informa el reinicio: se reevalúa en el próximo refresco."""
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
HTTP_TIMEOUT_SECONDS = 10.0

WINDOW_LABELS = {
    "five_hour": "5h",
    "seven_day": "7d",
    "seven_day_opus": "7d Opus",
    "seven_day_sonnet": "7d Sonnet",
    "primary": "primary",
    "secondary": "secondary",
    "daily": "day",
    "monthly": "month",
}

Fetch = Callable[[urllib.request.Request], Any]
AppServerRead = Callable[..., dict[str, Any]]
"""Lector de ``account/rateLimits/read``: recibe ``env=`` y devuelve el ``result`` del app-server."""


@dataclass(frozen=True)
class UsageWindow:
    """Una ventana de uso observada de un proveedor."""

    provider_id: str
    window_kind: str
    used_percent: float | None
    resets_at: str | None
    source: str
    status: str = "ok"


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        seconds = float(value)
        if seconds > 10_000_000_000:  # epoch en milisegundos
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=UTC)
    text = str(value).strip()
    if text.isdigit():
        return _parse_time(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, min(100.0, number))


class ProviderUsageStore:
    """Persistencia de ventanas de uso y de la política de umbral por proveedor."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record(self, windows: Iterable[UsageWindow]) -> None:
        """Guarda la última observación de cada ventana (upsert por proveedor y ventana)."""
        now = utc_now()
        for window in windows:
            self.connection.execute(
                """
                INSERT INTO provider_usage_windows
                    (provider_id, window_kind, used_percent, resets_at, source, status, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, window_kind) DO UPDATE SET
                    used_percent = excluded.used_percent,
                    resets_at = excluded.resets_at,
                    source = excluded.source,
                    status = excluded.status,
                    observed_at = excluded.observed_at
                """,
                (
                    window.provider_id,
                    window.window_kind,
                    window.used_percent,
                    window.resets_at,
                    window.source,
                    window.status,
                    now,
                ),
            )

    def record_poll_error(self, provider_id: str, message: str) -> None:
        """Deja constancia de un refresco fallido sin borrar la última observación válida."""
        self._upsert_policy(provider_id, last_poll_at=utc_now(), last_poll_error=message[:300])

    def mark_polled(self, provider_id: str) -> None:
        """Registra un refresco exitoso (limpia el último error)."""
        self._upsert_policy(provider_id, last_poll_at=utc_now(), last_poll_error="")

    def windows(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """Ventanas observadas (todas o de un proveedor), en forma pública."""
        query = "SELECT * FROM provider_usage_windows"
        params: tuple[Any, ...] = ()
        if provider_id:
            query += " WHERE provider_id = ?"
            params = (provider_id,)
        rows = self.connection.execute(query + " ORDER BY provider_id, window_kind", params).fetchall()
        return [
            {
                "providerId": str(row["provider_id"]),
                "window": str(row["window_kind"]),
                "label": WINDOW_LABELS.get(str(row["window_kind"]), str(row["window_kind"])),
                "usedPercent": row["used_percent"],
                "resetsAt": row["resets_at"],
                "source": str(row["source"]),
                "status": str(row["status"]),
                "observedAt": str(row["observed_at"]),
            }
            for row in rows
        ]

    def policy(self, provider_id: str) -> dict[str, Any]:
        """Override de umbral y estado de refresco de un proveedor (vacío si no tiene)."""
        row = self.connection.execute(
            "SELECT * FROM provider_usage_policies WHERE provider_id = ?", (provider_id,)
        ).fetchone()
        if row is None:
            return {"thresholdPercent": None, "ignoreUntil": None, "lastPollAt": None, "lastPollError": ""}
        return {
            "thresholdPercent": row["threshold_percent"],
            "ignoreUntil": row["ignore_until"],
            "lastPollAt": row["last_poll_at"],
            "lastPollError": str(row["last_poll_error"] or ""),
        }

    def set_policy(
        self,
        provider_id: str,
        *,
        threshold_percent: float | None,
        ignore_until: str | None = None,
    ) -> dict[str, Any]:
        """Fija (o limpia con ``None``) el umbral propio del proveedor y la reanudación manual."""
        if threshold_percent is not None and not 1 <= float(threshold_percent) <= 100:
            raise ValueError("thresholdPercent must be between 1 and 100.")
        self._upsert_policy(
            provider_id,
            threshold_percent=None if threshold_percent is None else float(threshold_percent),
            ignore_until=ignore_until,
            set_threshold=True,
        )
        return self.policy(provider_id)

    def _upsert_policy(
        self,
        provider_id: str,
        *,
        threshold_percent: float | None = None,
        ignore_until: str | None = None,
        last_poll_at: str | None = None,
        last_poll_error: str | None = None,
        set_threshold: bool = False,
    ) -> None:
        current = self.policy(provider_id)
        self.connection.execute(
            """
            INSERT INTO provider_usage_policies
                (provider_id, threshold_percent, ignore_until, last_poll_at, last_poll_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                threshold_percent = excluded.threshold_percent,
                ignore_until = excluded.ignore_until,
                last_poll_at = excluded.last_poll_at,
                last_poll_error = excluded.last_poll_error,
                updated_at = excluded.updated_at
            """,
            (
                provider_id,
                threshold_percent if set_threshold else current["thresholdPercent"],
                ignore_until if set_threshold else current["ignoreUntil"],
                last_poll_at if last_poll_at is not None else current["lastPollAt"],
                last_poll_error if last_poll_error is not None else current["lastPollError"],
                utc_now(),
            ),
        )

    def threshold_for(self, provider_id: str) -> tuple[float, str]:
        """Umbral efectivo y su origen: ``provider`` (override) o ``general`` (setting, 80 % por defecto)."""
        own = self.policy(provider_id)["thresholdPercent"]
        if own is not None:
            return float(own), "provider"
        return general_threshold(self.connection), "general"

    def suspensions(self, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Proveedores suspendidos por cuota ahora mismo, con hasta cuándo y qué ventana los suspendió.

        Una ventana suspende si su uso alcanza el umbral efectivo del proveedor o si la fuente la
        declara agotada (``status = rejected``), y su reinicio aún no pasó. Sin reinicio informado,
        la suspensión dura ``UNKNOWN_RESET_SUSPENSION`` desde la observación. ``ignoreUntil`` (reanudar
        ahora) la anula hasta esa fecha.
        """
        moment = now or datetime.now(UTC)
        result: dict[str, dict[str, Any]] = {}
        try:
            rows = self.connection.execute("SELECT * FROM provider_usage_windows").fetchall()
        except sqlite3.Error:
            return {}
        for row in rows:
            provider_id = str(row["provider_id"])
            threshold, _ = self.threshold_for(provider_id)
            used = row["used_percent"]
            exhausted = str(row["status"]) == "rejected"
            if not exhausted and (used is None or float(used) < threshold):
                continue
            until = _parse_time(row["resets_at"])
            if until is None:
                observed = _parse_time(row["observed_at"]) or moment
                until = observed + UNKNOWN_RESET_SUSPENSION
            if until <= moment:
                continue
            ignore_until = _parse_time(self.policy(provider_id)["ignoreUntil"])
            if ignore_until is not None and ignore_until > moment:
                continue
            current = result.get(provider_id)
            if current is None or until.isoformat() > current["until"]:
                result[provider_id] = {
                    "until": until.isoformat(),
                    "window": str(row["window_kind"]),
                    "usedPercent": used,
                    "thresholdPercent": threshold,
                }
        return result

    def due_for_poll(self, provider_id: str, *, now: datetime | None = None) -> bool:
        """Verdadero si pasó el intervalo mínimo desde el último refresco remoto del proveedor."""
        last = _parse_time(self.policy(provider_id)["lastPollAt"])
        moment = now or datetime.now(UTC)
        return last is None or (moment - last).total_seconds() >= MIN_POLL_INTERVAL_SECONDS


def general_threshold(connection: sqlite3.Connection) -> float:
    """Umbral general del operador (``runtime.quota.suspendThresholdPercent``), 80 % por defecto."""
    value = resolve_setting_value(connection=connection, key=THRESHOLD_SETTING_KEY, project_id=None)
    percent = _percent(value)
    return percent if percent is not None and percent >= 1 else DEFAULT_THRESHOLD_PERCENT


# ---------------------------------------------------------------------------------------------------
# Colectores: devuelven ventanas o levantan ``UsageUnavailable`` con una causa legible.
# ---------------------------------------------------------------------------------------------------


class UsageUnavailableError(RuntimeError):
    """La fuente de uso no está disponible (sin credencial local, respuesta inesperada, red)."""


def _default_fetch(request: urllib.request.Request) -> Any:
    from local_control_center.agents.providers.http_transport import urlopen_fail_closed

    with urlopen_fail_closed(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read(1_048_576).decode("utf-8"))


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UsageUnavailableError(f"credentials file not readable: {path.name}") from error
    if not isinstance(payload, dict):
        raise UsageUnavailableError(f"credentials file has an unexpected shape: {path.name}")
    return payload


def _claude_token(env: Mapping[str, str]) -> str:
    token = str(env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return token
    base = str(env.get("CLAUDE_CONFIG_DIR") or "").strip()
    root = Path(base) if base else Path.home() / ".claude"
    oauth = _read_json_file(root / ".credentials.json").get("claudeAiOauth")
    token = str((oauth or {}).get("accessToken") or "").strip() if isinstance(oauth, dict) else ""
    if not token:
        raise UsageUnavailableError("Claude Code CLI is not logged in (no OAuth access token)")
    return token


def collect_claude_usage(
    *, provider_id: str = "claude_code_cli", env: Mapping[str, str] | None = None, fetch: Fetch | None = None
) -> list[UsageWindow]:
    """Ventanas de uso de la suscripción de Claude (5 h, semanal, semanal por modelo)."""
    token = _claude_token(env if env is not None else os.environ)
    request = urllib.request.Request(
        CLAUDE_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/aido-usage",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        payload = (fetch or _default_fetch)(request)
    except UsageUnavailableError:
        raise
    except Exception as error:
        raise UsageUnavailableError(
            f"Claude usage endpoint unavailable: {error.__class__.__name__}"
        ) from error
    return parse_claude_usage(payload, provider_id=provider_id)


def parse_claude_usage(payload: Any, *, provider_id: str = "claude_code_cli") -> list[UsageWindow]:
    """Interpreta la respuesta de ``/api/oauth/usage`` (``utilization`` 0-100, ``resets_at`` ISO)."""
    if not isinstance(payload, dict):
        raise UsageUnavailableError("Claude usage response is not an object")
    windows: list[UsageWindow] = []
    for kind, item in payload.items():
        if not isinstance(item, dict) or "utilization" not in item:
            continue
        windows.append(
            UsageWindow(
                provider_id=provider_id,
                window_kind=str(kind),
                used_percent=_percent(item.get("utilization")),
                resets_at=_iso(_parse_time(item.get("resets_at"))),
                source="claude_oauth_usage",
            )
        )
    if not windows:
        raise UsageUnavailableError("Claude usage response has no usage windows")
    return windows


def _codex_tokens(env: Mapping[str, str]) -> tuple[str, str]:
    base = str(env.get("CODEX_HOME") or "").strip()
    root = Path(base) if base else Path.home() / ".codex"
    tokens = _read_json_file(root / "auth.json").get("tokens")
    if not isinstance(tokens, dict) or not str(tokens.get("access_token") or "").strip():
        raise UsageUnavailableError("Codex CLI is not logged in with ChatGPT (no access token)")
    return str(tokens["access_token"]).strip(), str(tokens.get("account_id") or "").strip()


def collect_codex_usage(
    *,
    provider_id: str = "codex_cli",
    env: Mapping[str, str] | None = None,
    fetch: Fetch | None = None,
    app_server: AppServerRead | None = None,
) -> list[UsageWindow]:
    """Ventanas de uso de la cuenta de ChatGPT que usa Codex (5 h y semanal).

    Fuente primaria: el app-server oficial (``app_server``, por defecto ``read_codex_rate_limits``).
    Si falla por cualquier causa se usa ``wham/usage``; si ambas fallan, la causa lleva las dos. Dentro
    de una transacción SQLite el app-server levanta ``RuntimeError`` y no se intenta la otra fuente.
    """
    environment = env if env is not None else os.environ
    try:
        payload = (app_server or read_codex_rate_limits)(env=environment)
        return parse_codex_usage(payload, provider_id=provider_id, source="codex_app_server")
    except (CodexAppServerError, UsageUnavailableError) as error:
        app_server_cause = str(error)
    try:
        return _collect_codex_wham_usage(provider_id=provider_id, env=environment, fetch=fetch)
    except UsageUnavailableError as error:
        raise UsageUnavailableError(f"{error} (app-server: {app_server_cause})") from error


def _collect_codex_wham_usage(
    *, provider_id: str, env: Mapping[str, str], fetch: Fetch | None
) -> list[UsageWindow]:
    """Fuente HTTP de respaldo: ``wham/usage`` con el login local de Codex."""
    token, account_id = _codex_tokens(env)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "codex-cli/aido",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    request = urllib.request.Request(CODEX_USAGE_URL, headers=headers, method="GET")
    try:
        payload = (fetch or _default_fetch)(request)
    except UsageUnavailableError:
        raise
    except Exception as error:
        raise UsageUnavailableError(
            f"Codex usage endpoint unavailable: {error.__class__.__name__}"
        ) from error
    return parse_codex_usage(payload, provider_id=provider_id)


def _codex_window_kind(name: str, window: Mapping[str, Any]) -> str:
    seconds = window.get("limit_window_seconds")
    minutes = window.get("window_minutes") or window.get("windowDurationMins")
    try:
        total = int(seconds) if seconds is not None else int(minutes) * 60 if minutes is not None else None
    except (TypeError, ValueError):
        total = None
    if total is not None and total <= 6 * 3600:
        return "five_hour"
    if total is not None and total >= 6 * 86400:
        return "seven_day"
    return "primary" if "primary" in name else "secondary"


def parse_codex_usage(
    payload: Any, *, provider_id: str = "codex_cli", source: str = "codex_usage"
) -> list[UsageWindow]:
    """Interpreta ``wham/usage`` (``rate_limit.primary_window``…) o la forma del app-server (``rateLimits``).

    En el app-server un ``rateLimitReachedType`` presente marca la ventana como rechazada, igual que
    ``limit_reached`` en ``wham/usage``.
    """
    if not isinstance(payload, dict):
        raise UsageUnavailableError("Codex usage response is not an object")
    limits = payload.get("rate_limit") or payload.get("rateLimits") or payload.get("rate_limits") or {}
    if not isinstance(limits, dict):
        raise UsageUnavailableError("Codex usage response has no rate limits")
    reached = bool(limits.get("rateLimitReachedType"))
    windows: list[UsageWindow] = []
    for name in ("primary_window", "secondary_window", "primary", "secondary"):
        window = limits.get(name)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent", window.get("usedPercent"))
        reset = window.get("reset_at", window.get("resetsAt"))
        if reset is None and window.get("reset_after_seconds") is not None:
            try:
                reset = (
                    datetime.now(UTC) + timedelta(seconds=int(window["reset_after_seconds"]))
                ).isoformat()
            except (TypeError, ValueError):
                reset = None
        windows.append(
            UsageWindow(
                provider_id=provider_id,
                window_kind=_codex_window_kind(name, window),
                used_percent=_percent(used),
                resets_at=_iso(_parse_time(reset)),
                source=source,
                status="rejected" if limits.get("limit_reached") is True or reached else "ok",
            )
        )
    if not windows:
        raise UsageUnavailableError("Codex usage response has no usage windows")
    return windows


def collect_accounting_usage(connection: sqlite3.Connection) -> list[UsageWindow]:
    """Uso contabilizado por AIDO contra los límites diarios/mensuales configurados por el operador.

    Por cada límite habilitado de ``provider_limits`` con tope diario o mensual (requests, tokens o
    presupuesto USD), el porcentaje es el mayor cociente consumido/tope de esa ventana.
    """
    from local_control_center.agents.quota_manager import QuotaManager

    try:
        limits = connection.execute(
            """SELECT provider_id, model, daily_requests, daily_tokens, monthly_requests,
                      monthly_tokens, monthly_budget_usd
               FROM provider_limits WHERE enabled = 1"""
        ).fetchall()
    except sqlite3.Error:
        return []
    manager = QuotaManager(connection)
    best: dict[tuple[str, str], UsageWindow] = {}
    for limit in limits:
        caps = {
            "day": (limit["daily_requests"], limit["daily_tokens"], None),
            "month": (limit["monthly_requests"], limit["monthly_tokens"], limit["monthly_budget_usd"]),
        }
        if not any(value for window in caps.values() for value in window):
            continue
        provider_id = str(limit["provider_id"])
        try:
            status = manager.status(provider_id=provider_id, model=str(limit["model"] or "*"))
        except Exception:
            continue
        for window in status.get("windows") or []:
            kind = window.get("kind")
            if kind not in caps:
                continue
            request_cap, token_cap, cost_cap = caps[kind]
            requests = int(window.get("committedRequests") or 0) + int(window.get("reservedRequests") or 0)
            tokens = sum(
                int(window.get(key) or 0) for key in ("committedTokens", "unverifiedTokens", "reservedTokens")
            )
            cost = sum(
                float(window.get(key) or 0.0)
                for key in ("knownCostUsd", "unverifiedCostUsd", "reservedCostUsd")
            )
            ratios = [
                used / float(cap) * 100
                for used, cap in ((requests, request_cap), (tokens, token_cap), (cost, cost_cap))
                if cap
            ]
            if not ratios:
                continue
            window_kind = "daily" if kind == "day" else "monthly"
            candidate = UsageWindow(
                provider_id=provider_id,
                window_kind=window_kind,
                used_percent=_percent(max(ratios)),
                resets_at=_iso(_parse_time(window.get("resetsAt"))),
                source="aido_accounting",
            )
            current = best.get((provider_id, window_kind))
            if current is None or (candidate.used_percent or 0) > (current.used_percent or 0):
                best[(provider_id, window_kind)] = candidate
    return list(best.values())


COLLECTORS: dict[str, Callable[..., list[UsageWindow]]] = {
    "claude_code_cli": collect_claude_usage,
    "codex_cli": collect_codex_usage,
}
"""Proveedores con una fuente de uso remota propia (el resto usa la contabilidad de AIDO)."""


def refresh_provider_usage(
    connection: sqlite3.Connection,
    *,
    provider_ids: Iterable[str] | None = None,
    force: bool = False,
    env: Mapping[str, str] | None = None,
    fetch: Fetch | None = None,
) -> dict[str, Any]:
    """Refresca el uso de los proveedores habilitados con fuente propia y la contabilidad de AIDO.

    Respeta ``MIN_POLL_INTERVAL_SECONDS`` salvo ``force``. Un proveedor sin fuente (no logueado,
    respuesta inesperada) registra la causa y conserva su última observación. Debe llamarse fuera de
    una transacción: las fuentes remotas hacen I/O.
    """
    from local_control_center.agents.provider_accounts import ProviderAccountStore

    store = ProviderUsageStore(connection)
    enabled = {
        str(account["providerId"])
        for account in ProviderAccountStore(connection).list_provider_accounts()
        if account.get("enabled")
    }
    wanted = set(provider_ids) if provider_ids is not None else set(COLLECTORS)
    report: dict[str, Any] = {"refreshed": [], "skipped": [], "errors": {}}
    for provider_id, collector in COLLECTORS.items():
        if provider_id not in wanted or provider_id not in enabled:
            continue
        if not force and not store.due_for_poll(provider_id):
            report["skipped"].append(provider_id)
            continue
        try:
            windows = collector(provider_id=provider_id, env=env, fetch=fetch)
        except UsageUnavailableError as error:
            store.record_poll_error(provider_id, str(error))
            report["errors"][provider_id] = str(error)
            continue
        store.record(windows)
        store.mark_polled(provider_id)
        report["refreshed"].append(provider_id)
    accounting = [window for window in collect_accounting_usage(connection) if window.provider_id in enabled]
    if accounting:
        store.record(accounting)
    return report


def describe_provider_usage(connection: sqlite3.Connection) -> dict[str, Any]:
    """Cuerpo de ``GET /api/v1/runtime/provider-usage``: umbral general y estado por proveedor."""
    store = ProviderUsageStore(connection)
    suspensions = store.suspensions()
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for window in store.windows():
        by_provider.setdefault(window["providerId"], []).append(window)
    provider_ids = sorted(set(by_provider) | set(COLLECTORS) | set(suspensions))
    providers = []
    for provider_id in provider_ids:
        threshold, threshold_source = store.threshold_for(provider_id)
        policy = store.policy(provider_id)
        suspension = suspensions.get(provider_id)
        windows = by_provider.get(provider_id, [])
        used = [float(item["usedPercent"]) for item in windows if item["usedPercent"] is not None]
        providers.append(
            {
                "providerId": provider_id,
                "thresholdPercent": threshold,
                "thresholdSource": threshold_source,
                "ownThresholdPercent": policy["thresholdPercent"],
                "maxUsedPercent": max(used) if used else None,
                "windows": windows,
                "suspended": suspension is not None,
                "suspendedUntil": suspension["until"] if suspension else None,
                "suspendedWindow": suspension["window"] if suspension else None,
                "ignoreUntil": policy["ignoreUntil"],
                "lastPollAt": policy["lastPollAt"],
                "lastPollError": policy["lastPollError"] or None,
                "hasRemoteSource": provider_id in COLLECTORS,
            }
        )
    return {"generalThresholdPercent": general_threshold(connection), "providers": providers}
