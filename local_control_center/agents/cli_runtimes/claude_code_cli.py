"""Adaptador del runtime Claude Code CLI: traduce una request al argv de `claude`.

Resuelve el binario por env vars (AIDO_CLAUDE_COMMAND / CLAUDE_CODE_CLI_PATH), mapea perfiles
de agente a modelo y arma el comando en modo no interactivo (--print) con edición aceptada y el
workspace acotado vía --add-dir. La ejecución segura y el registro los hereda de CliRuntime.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .base import CliRuntime, RuntimeAuthStatus, RuntimeRequest

CLAUDE_PROFILES = {
    "claude_sonnet_developer": {"model": "sonnet", "effort": "medium"},
    "claude_sonnet_qa": {"model": "sonnet", "effort": "medium"},
    "claude_opus_planner": {"model": "opus", "effort": "high"},
    "claude_opus_xhigh_architect": {"model": "opus", "effort": "xhigh"},
    "claude_opus_max_requires_approval": {"model": "opus", "effort": "max"},
    "claude_opusplan_if_supported": {"model": "opus", "effort": "high"},
}


class ClaudeCodeCliRuntime(CliRuntime):
    """Runtime CLI para Claude Code, con perfiles que fijan modelo y esfuerzo por rol."""

    runtime_id = "claude_code_cli"
    display_name = "Claude Code CLI"
    auth_status_argv = ("auth", "status", "--json")
    login_hint = "Claude Code CLI is not logged in; run `claude auth login` and retry."

    def __init__(
        self,
        *,
        executable: str | None = None,
        connection: sqlite3.Connection | None = None,
    ):
        super().__init__(
            executable=executable
            or os.environ.get("AIDO_CLAUDE_COMMAND")
            or os.environ.get("CLAUDE_CODE_CLI_PATH", "claude"),
            connection=connection,
        )

    def _parse_auth_probe(self, result: dict[str, Any]) -> RuntimeAuthStatus:
        """Lee el JSON de `claude auth status --json`: solo `loggedIn` decide, sin exponer identidad.

        El mensaje persiste método de auth como contexto operativo; email/organización jamás
        salen de aquí porque el payload completo se descarta tras leer los campos booleanos.
        """
        if result.get("returnCode") != 0:
            return RuntimeAuthStatus(
                runtime=self.runtime_id, status="unauthenticated", message=self.login_hint
            )
        try:
            payload = json.loads(str(result.get("stdout") or ""))
        except json.JSONDecodeError:
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="unknown",
                message="Claude Code CLI auth status did not return parseable JSON.",
            )
        if not isinstance(payload, dict) or payload.get("loggedIn") is not True:
            return RuntimeAuthStatus(
                runtime=self.runtime_id, status="unauthenticated", message=self.login_hint
            )
        auth_method = str(payload.get("authMethod") or "native session")
        return RuntimeAuthStatus(
            runtime=self.runtime_id,
            status="authenticated",
            message=f"Claude Code CLI session is logged in ({auth_method}).",
        )

    def _credentials_path(self) -> Path:
        """Ruta del archivo de credenciales OAuth que lee el binario standalone de `claude`.

        Honra ``CLAUDE_CONFIG_DIR`` (donde el CLI persiste su config) y degrada a ``~/.claude``.
        """
        base = str(os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
        root = Path(base) if base else (Path.home() / ".claude")
        return (root / ".credentials.json").expanduser()

    def _local_auth_invalidation(self, parsed: RuntimeAuthStatus) -> RuntimeAuthStatus | None:
        """Degrada a unauthenticated si el token OAuth local ya venció, aunque el probe diga logged-in.

        ``claude auth status`` reporta ``loggedIn: true`` sobre un ``accessToken`` expirado sin
        renovarlo; la ejecución real recién falla con 401. Leemos ``claudeAiOauth.expiresAt`` (epoch
        ms) del archivo de credenciales y degradamos solo cuando ya pasó. Archivo ausente, ilegible o
        sin ``expiresAt`` => ``None`` (sin evidencia, se respeta el probe).
        """
        expires_at_ms = _read_oauth_expiry_ms(self._credentials_path())
        if expires_at_ms is None:
            return None
        if expires_at_ms <= int(time.time() * 1000):
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="unauthenticated",
                message=(
                    "Claude Code CLI OAuth token expired; run `claude auth login` and retry "
                    "(`claude auth status` still reports logged-in on an expired token)."
                ),
            )
        return None

    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Arma el argv de `claude --print` para el workspace validado; el prompt va al final.

        El modelo explícito de la request gana sobre el del perfil; los extra_args se interponen
        antes del prompt para no romper su posición posicional.
        """
        workspace = self._validate_workspace(request)
        self._validate_safe_args(request)
        profile = CLAUDE_PROFILES.get(request.profile or "", {})
        model = request.model or profile.get("model")
        plan_only = request.role == "product_owner" or request.env_policy.get("permissionProfile") == "plan"
        permission_mode = "plan" if plan_only else "acceptEdits"
        command = [
            self.executable,
            "--print",
            "--permission-mode",
            permission_mode,
            "--add-dir",
            str(workspace),
        ]
        if model:
            command.extend(["--model", str(model)])
        command.extend(request.extra_args)
        command.extend(["--", request.prompt])
        return command


def _read_oauth_expiry_ms(path: Path) -> int | None:
    """Lee ``claudeAiOauth.expiresAt`` (epoch ms) del archivo de credenciales, o None si no es legible.

    Tolera archivo ausente, JSON inválido, forma inesperada o ``expiresAt`` no numérico: en todos
    esos casos no hay evidencia de vencimiento y se devuelve None para no degradar el veredicto.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        return None
    try:
        return int(oauth.get("expiresAt"))
    except (TypeError, ValueError):
        return None
