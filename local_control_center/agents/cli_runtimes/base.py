"""Contrato base y barrera de seguridad para ejecutar agentes de codificación vía CLI.

Define el ABC CliRuntime que detecta el binario, valida workspace/flags, somete cada
ejecución a la policy y al sandbox de subprocesos, y persiste el resultado. Aquí viven
los DTOs request/resultado y el parseo tolerante de uso de tokens desde stdout/stderr.
Invariante: ninguna ejecución real ocurre sin política SQLite que habilite el runtime y
decisión 'allow' de la policy; los flags peligrosos se rechazan antes de construir el comando.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from local_control_center.agents.cli_sessions import CliSessionStore
from local_control_center.agents.providers.base import UsageRecord
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.security_policy import sandbox as subprocess_sandbox
from local_control_center.security_policy.policy_engine import evaluate_action
from local_control_center.shared.redaction import redact_secrets

DANGEROUS_CLI_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--danger-full-access",
    "--no-sandbox",
    "--privileged",
    "--mount",
    "--volume",
    "--network=host",
    "danger-full-access",
}


def _which_executable(executable: str) -> str | None:
    from shutil import which

    return which(executable)


class RuntimeDetection(BaseModel):
    """Resultado de sondear si un runtime CLI está instalado, con su ruta y versión."""

    runtime: str
    status: str
    executable: str | None = None
    version: str | None = None
    message: str = ""


class RuntimeAuthStatus(BaseModel):
    """Veredicto del sondeo de autenticación nativa: authenticated, unauthenticated o unknown.

    ``unknown`` significa que el probe no pudo ejecutarse (binario ausente, timeout, bloqueado);
    nunca debe degradar una validación previa. El mensaje jamás incluye identidad de la cuenta.
    """

    runtime: str
    status: str
    message: str = ""


class RuntimeHealth(BaseModel):
    """Estado de salud derivado de la detección, listo para exponer en health checks."""

    runtime: str
    status: str
    message: str = ""


class RuntimeRequest(BaseModel):
    """Solicitud de ejecución de un runtime CLI: prompt, workspace y contexto de policy/workflow.

    Acepta nombres camelCase (alias) además del snake_case interno para integrarse con el API web.
    """

    model_config = ConfigDict(populate_by_name=True)

    runtime: str
    workspace_id: str = Field(alias="workspaceId")
    workspace_path: str = Field(alias="workspacePath")
    prompt: str
    profile: str | None = None
    model: str | None = None
    effort: str | None = None
    env_policy: dict[str, Any] = Field(default_factory=dict, alias="envPolicy")
    extra_args: list[str] = Field(default_factory=list, alias="extraArgs")
    role: str | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    workflow_run_id: str | None = Field(default=None, alias="workflowRunId")
    workflow_step_id: str | None = Field(default=None, alias="workflowStepId")


class RuntimeResult(BaseModel):
    """Desenlace de una ejecución: estado, comando lanzado, salidas, código y uso de tokens.

    El estado distingue blocked (rechazado por validación/gate/policy) de completed/failed
    (ejecutado por el sandbox) y created (sesión manual sin ejecución real).
    """

    runtime: str
    status: str
    command: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    return_code: int | None = Field(default=None, alias="returnCode")
    error: str | None = None
    usage: UsageRecord | None = None


class CliRuntime(ABC):
    """Plantilla de adaptador para un agente CLI: detección, validación, ejecución y registro.

    Las subclases solo aportan build_command (la línea de comando específica del CLI); la
    clase base centraliza el flujo seguro común y la persistencia opcional de cada resultado.
    """

    runtime_id: str
    display_name: str
    # Subcomando de solo lectura que reporta el estado de login nativo (None = sin probe).
    auth_status_argv: tuple[str, ...] | None = None
    # Instrucción accionable que se muestra cuando el CLI no está autenticado.
    login_hint: str = ""

    def __init__(self, *, executable: str, connection: sqlite3.Connection | None = None):
        self.executable = executable
        self.connection = connection

    def _which(self) -> str | None:
        return _which_executable(self.executable)

    def _version(self, executable: str) -> str | None:
        result = subprocess_sandbox.run_version_check(argv=[executable, "--version"], timeout_seconds=5)
        if result.get("returnCode") != 0:
            return None
        output = str(result.get("stdout") or result.get("stderr") or "").strip()
        if not output:
            return None
        return output.splitlines()[0][:200]

    def _validate_workspace(self, request: RuntimeRequest) -> Path:
        if not request.workspace_id:
            raise ValueError("workspace_id is required for CLI runtimes")
        workspace = Path(request.workspace_path).resolve(strict=False)
        if not workspace.exists():
            raise ValueError("workspace_path does not exist")
        if self.connection is not None:
            row = self.connection.execute(
                "SELECT path FROM workspaces WHERE id = ?", (request.workspace_id,)
            ).fetchone()
            if not row:
                raise ValueError("workspace must be registered before real CLI execution")
            registered = Path(row["path"]).resolve(strict=False)
            if registered != workspace:
                raise ValueError("workspace_path does not match registered workspace")
        return workspace

    def _validate_git_workspace(self, workspace: Path) -> None:
        if not (workspace / ".git").exists():
            raise ValueError("workspace_path must be a Git worktree for this CLI runtime contract")

    def _validate_safe_args(self, request: RuntimeRequest) -> None:
        lowered = {str(arg).lower() for arg in request.extra_args}
        if lowered & DANGEROUS_CLI_FLAGS:
            raise ValueError("dangerous CLI flags are blocked by runtime policy")

    def detect(self) -> RuntimeDetection:
        """Resuelve el binario en el PATH y reporta si está instalado junto con su versión."""
        executable = self._which()
        if not executable:
            return RuntimeDetection(
                runtime=self.runtime_id, status="not_installed", message=f"{self.display_name} not detected"
            )
        return RuntimeDetection(
            runtime=self.runtime_id,
            status="installed",
            executable=executable,
            version=self._version(executable),
            message=f"{self.display_name} detected",
        )

    def health_check(self) -> RuntimeHealth:
        """Traduce la detección a un veredicto de salud (healthy si el binario está instalado)."""
        detection = self.detect()
        status = "healthy" if detection.status == "installed" else detection.status
        return RuntimeHealth(runtime=self.runtime_id, status=status, message=detection.message)

    def validate_native_auth(self) -> RuntimeAuthStatus:
        """Sondea el estado de login nativo del CLI con su subcomando de solo lectura.

        Invariante: solo distingue unauthenticated cuando el CLI respondió de forma concluyente;
        un probe que no pudo ejecutarse (binario ausente, timeout, bloqueado por el sandbox)
        devuelve ``unknown`` para no degradar validaciones previas por un fallo transitorio.
        """
        if not self.auth_status_argv:
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="unknown",
                message=f"{self.display_name} does not expose a native auth status probe.",
            )
        executable = self._which()
        if not executable:
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="unknown",
                message=f"{self.display_name} executable was not found for the auth probe.",
            )
        result = subprocess_sandbox.run_auth_status_check(
            argv=[executable, *self.auth_status_argv], timeout_seconds=10
        )
        if result.get("blocked") or result.get("timedOut") or result.get("returnCode") is None:
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="unknown",
                message=str(result.get("reason") or "Auth status probe did not complete."),
            )
        parsed = self._parse_auth_probe(result)
        # El probe puede reportar 'authenticated' con una credencial ya vencida (p.ej.
        # `claude auth status` confía en un token expirado en disco): cruzamos el veredicto
        # positivo con la verdad local. El hook solo degrada ante evidencia concluyente de
        # vencimiento; sin evidencia devuelve None y el veredicto del probe se respeta, de modo
        # que un archivo ausente/ilegible nunca convierte authenticated en unknown.
        if parsed.status == "authenticated":
            invalidation = self._local_auth_invalidation(parsed)
            if invalidation is not None:
                return invalidation
        return parsed

    def _local_auth_invalidation(self, parsed: RuntimeAuthStatus) -> RuntimeAuthStatus | None:
        """Degrada un veredicto 'authenticated' del probe ante evidencia LOCAL de credencial inválida.

        Hook para subclases cuyo CLI puede reportar login válido sobre una credencial ya vencida
        (el probe lee un token expirado en disco sin renovarlo). Devuelve un ``RuntimeAuthStatus``
        ``unauthenticated`` solo ante evidencia concluyente (p.ej. ``expiresAt`` pasado en el
        archivo de credenciales), o ``None`` para respetar el veredicto del probe cuando no hay
        evidencia. Nunca produce ``unknown``: preserva el invariante "unknown nunca degrada".
        La base es no-op; el cruce con la verdad local es responsabilidad de cada subclase.
        """
        return None

    def _parse_auth_probe(self, result: dict[str, Any]) -> RuntimeAuthStatus:
        """Interpreta el resultado del probe: exit 0 = autenticado; distinto = no autenticado."""
        if result.get("returnCode") == 0:
            return RuntimeAuthStatus(
                runtime=self.runtime_id,
                status="authenticated",
                message=f"{self.display_name} native session is logged in.",
            )
        return RuntimeAuthStatus(
            runtime=self.runtime_id,
            status="unauthenticated",
            message=self.login_hint or f"{self.display_name} native session is not logged in.",
        )

    @abstractmethod
    def build_command(self, request: RuntimeRequest) -> list[str]:
        """Construye el argv del CLI tras validar workspace y flags.

        Raises:
            ValueError: si el workspace no es válido o la request trae flags peligrosos.
        """
        raise NotImplementedError

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        """Ejecuta el CLI bajo el sandbox tras pasar validación, política runtime y security policy.

        Invariante: solo se invoca el sandbox si la configuración SQLite habilita este runtime
        y la policy decide 'allow'; en cualquier otro caso devuelve un resultado 'blocked' con el
        motivo. No propaga ValueError de build_command: lo captura y lo materializa como bloqueo.
        Cada decisión (deny o ejecución) queda registrada vía _record_result.
        """
        try:
            command = self.build_command(request)
        except ValueError as error:
            blocked = RuntimeResult(
                runtime=self.runtime_id,
                status="blocked",
                command=self._blocked_command(request),
                error=str(error),
            )
            validation_request = request.model_copy(
                update={
                    "env_policy": {
                        **request.env_policy,
                        "runtimeValidation": {
                            "decision": "deny",
                            "reason": str(error),
                        },
                    }
                }
            )
            self._record_result(validation_request, blocked)
            return blocked
        workspace = Path(request.workspace_path).resolve(strict=False)
        runtime_policy = self._runtime_policy_decision(request)
        if not runtime_policy.get("allowed"):
            blocked = RuntimeResult(
                runtime=self.runtime_id,
                status="blocked",
                command=command,
                error=str(runtime_policy.get("reason") or "CLI runtime is disabled by runtime policy"),
            )
            gated_request = request.model_copy(
                update={
                    "env_policy": {
                        **request.env_policy,
                        "runtimePolicy": {
                            "decision": "deny",
                            "reason": blocked.error,
                        },
                    }
                }
            )
            self._record_result(gated_request, blocked)
            return blocked
        policy = self._evaluate_policy(request, command, workspace)
        policy_request = request.model_copy(
            update={"env_policy": {**request.env_policy, "policyResult": policy}}
        )
        if policy.get("decision") != "allow":
            blocked = RuntimeResult(
                runtime=self.runtime_id,
                status="blocked",
                command=command,
                error=str(policy.get("reason") or "CLI runtime blocked by security policy"),
            )
            self._record_result(policy_request, blocked)
            return blocked
        result = subprocess_sandbox.RestrictedSubprocessSandbox().execute(
            argv=command,
            cwd=str(workspace),
            workspace_path=str(workspace),
            timeout_seconds=900,
        )
        status = "completed" if result.get("returnCode") == 0 else "failed"
        runtime_result = RuntimeResult(
            runtime=self.runtime_id,
            status=status,
            command=command,
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            returnCode=result.get("returnCode"),
            error=result.get("reason"),
        )
        completed = runtime_result.model_copy(update={"usage": self.parse_usage(runtime_result)})
        self._record_result(policy_request, completed)
        return completed

    def _blocked_command(self, request: RuntimeRequest) -> list[str]:
        command = [self.executable, "<blocked>"]
        command.extend(str(arg) for arg in request.extra_args)
        if request.prompt:
            command.append("<prompt>")
        return command

    def _evaluate_policy(
        self,
        request: RuntimeRequest,
        command: list[str],
        workspace: Path,
    ) -> dict[str, Any]:
        return evaluate_action(
            {
                "tool": "shell",
                "command": " ".join(command),
                "path": str(workspace),
                "workspacePath": str(workspace),
                "role": request.role,
                "permissionProfile": request.env_policy.get("permissionProfile", "dev_safe"),
                "networkRequired": bool(request.env_policy.get("network", False)),
                "secretsRequired": bool(
                    request.env_policy.get("secrets") or request.env_policy.get("secretRefs")
                ),
            }
        )

    def _runtime_policy_decision(self, request: RuntimeRequest) -> dict[str, Any]:
        if self.connection is None:
            return {
                "allowed": False,
                "reason": "SQLite runtime policy is required for CLI execution.",
            }
        project_id = self._project_id_for_workspace(request)
        return RuntimeConfigRepository(self.connection).runtime_policy_decision(
            provider_id=self.runtime_id,
            kind="cli",
            project_id=project_id,
        )

    def _project_id_for_workspace(self, request: RuntimeRequest) -> str | None:
        if self.connection is None:
            return None
        row = self.connection.execute(
            "SELECT project_id FROM workspaces WHERE id = ?", (request.workspace_id,)
        ).fetchone()
        return str(row["project_id"]) if row else None

    def _record_result(self, request: RuntimeRequest, result: RuntimeResult) -> None:
        if self.connection is None:
            return
        CliSessionStore(self.connection).record_result(
            runtime=self.runtime_id,
            executable=self.executable,
            workspace_id=request.workspace_id,
            workflow_run_id=request.workflow_run_id,
            workflow_step_id=request.workflow_step_id,
            agent_id=request.agent_id,
            command=result.command,
            env_policy=request.env_policy,
            status=result.status,
            model=request.model,
            role=request.role,
            stdout=result.stdout,
            stderr=result.stderr,
            error=result.error,
            usage=result.usage,
        )

    def parse_usage(self, result: RuntimeResult) -> UsageRecord | None:
        """Extrae el conteo de tokens del JSON emitido por el CLI en stdout o stderr, si existe."""
        if result.usage is not None:
            return result.usage
        for payload in _json_payloads_from_text(result.stdout):
            usage = _usage_from_payload(payload)
            if usage is not None:
                return usage
        for payload in _json_payloads_from_text(result.stderr):
            usage = _usage_from_payload(payload)
            if usage is not None:
                return usage
        return None


def _json_payloads_from_text(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    stripped = text.strip()
    if not stripped:
        return payloads
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            payloads.append(parsed)
            return payloads
        if isinstance(parsed, list):
            payloads.extend(item for item in parsed if isinstance(item, dict))
            return payloads
    except json.JSONDecodeError:
        pass
    for line in stripped.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _usage_from_payload(payload: dict[str, Any]) -> UsageRecord | None:
    for usage in _usage_candidates(payload):
        parsed = _usage_record_from_mapping(usage)
        if parsed is not None:
            return parsed
    return None


def _usage_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ("usage", "token_usage", "tokens", "llm_metrics"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        candidates.append(message["usage"])
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        candidates.append(result["usage"])
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        for key in ("usage", "token_usage", "tokens", "llm_metrics"):
            value = metrics.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    candidates.append(payload)
    return candidates


def _usage_record_from_mapping(usage: dict[str, Any]) -> UsageRecord | None:
    token_keys = {
        "prompt_tokens",
        "input_tokens",
        "input",
        "completion_tokens",
        "output_tokens",
        "output",
        "cached_input_tokens",
        "cached_input",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_tokens",
        "reasoning",
        "tool_tokens",
        "tool",
        "total_tokens",
        "total",
    }
    if not (set(usage) & token_keys):
        return None
    input_tokens = _int_token(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("input"))
    output_tokens = _int_token(
        usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("output")
    )
    cached_input_tokens = _int_token(
        usage.get("cached_input_tokens")
        or usage.get("cached_input")
        or usage.get("cache_read_input_tokens")
        or usage.get("cache_creation_input_tokens")
        or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    )
    reasoning_tokens = _int_token(
        usage.get("reasoning_tokens")
        or usage.get("reasoning")
        or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    )
    tool_tokens = _int_token(usage.get("tool_tokens") or usage.get("tool"))
    total_tokens = _int_token(usage.get("total_tokens") or usage.get("total")) or (
        input_tokens + cached_input_tokens + output_tokens + reasoning_tokens + tool_tokens
    )
    if total_tokens == 0:
        return None
    return UsageRecord(
        inputTokens=input_tokens,
        cachedInputTokens=cached_input_tokens,
        outputTokens=output_tokens,
        reasoningTokens=reasoning_tokens,
        toolTokens=tool_tokens,
        totalTokens=total_tokens,
        rawUsage=redact_secrets({"usage_source": "cli_output", **usage}),
    )


def _int_token(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
