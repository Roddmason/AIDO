"""Motor de politicas: decide allow/deny/requires_approval/requires_human por accion.

Combina el perfil de permisos del rol, la clasificacion de riesgo del comando y el confinamiento
al workspace para emitir la decision y su razon auditada. Invariantes que garantiza: ninguna
ruta fuera del workspace asignado se permite sin aprobacion; los deploys a prod, force-push y
acciones criticas escalan a revision humana; cada operacion de agente exige su propio agentId,
tool, perfil y contexto (workspace + agent run) o se deniega. Funcion pura: no ejecuta ni
persiste, solo devuelve la decision; no lanza.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .command_classifier import classify_command

PROFILE_DEFAULTS: dict[str, str] = {
    "product_owner": "plan",
    "project_manager": "plan",
    "scrum_master": "plan",
    "architect": "plan",
    "technical_lead": "plan",
    "technical_lead_shadow": "plan",
    "researcher": "plan",
    "implementer": "dev_safe",
    "backend_engineer": "dev_safe",
    "frontend_engineer": "dev_safe",
    "mobile_engineer": "dev_safe",
    "data_engineer": "dev_safe",
    "database_engineer": "dev_safe",
    "devops": "qa",
    "devops_engineer": "qa",
    "qa_reviewer": "qa",
    "qa_engineer": "qa",
    "security_reviewer": "qa",
    "security_engineer": "qa",
    "pentester": "qa",
    "release_manager": "release",
}


def is_path_inside(path: str | None, root: str | None) -> bool:
    """Confirma que ``path`` resuelve dentro de ``root`` tras normalizar ``..`` y symlinks.

    Considera dentro cuando falta path o root (no hay restriccion declarada). Resuelve ambas
    rutas para evitar escapes via traversal; ante rutas invalidas devuelve ``False`` (fuera),
    fallando hacia el lado seguro en vez de lanzar.
    """
    if not path or not root:
        return True
    try:
        candidate = Path(path).resolve(strict=False)
        workspace_root = Path(root).resolve(strict=False)
        candidate.relative_to(workspace_root)
        return True
    except (OSError, ValueError):
        return False


def permission_profile_for(input_payload: dict[str, Any]) -> str:
    """Resuelve el perfil de permisos efectivo: explicito del payload o derivado del rol.

    Un ``permissionProfile`` explicito tiene prioridad; si no, mapea el rol a su perfil por
    defecto. Invariante: un rol desconocido cae al perfil ``plan`` (el mas restrictivo, sin
    shell), nunca a uno mas permisivo.
    """
    explicit = input_payload.get("permissionProfile") or input_payload.get("permission_profile")
    if explicit:
        return str(explicit)
    role = str(input_payload.get("role") or "")
    return PROFILE_DEFAULTS.get(role, "plan")


def allowlisted_shell_categories(profile: str, categories: list[str]) -> list[str]:
    """Devuelve las categorias shell permitidas para el perfil, segun su allowlist.

    ``dev_safe`` permite test/build/lint/diagnostico/lectura; ``qa`` añade typecheck, quality y
    security_scan. Invariante: cualquier otro perfil (p. ej. ``plan``, ``release``) obtiene lista
    vacia, de modo que el motor no podra conceder ``allow`` por allowlist.
    """
    allowed: list[str] = []
    if profile == "dev_safe":
        if "test" in categories:
            allowed.append("allowlisted_test")
        if "build" in categories:
            allowed.append("allowlisted_build")
        if "lint" in categories:
            allowed.append("allowlisted_lint")
        if "interpreter_version" in categories:
            allowed.append("allowlisted_diagnostic")
        if "read_only" in categories:
            allowed.append("allowlisted_read")
    elif profile == "qa":
        if "test" in categories:
            allowed.append("allowlisted_test")
        if "build" in categories:
            allowed.append("allowlisted_build")
        if "lint" in categories:
            allowed.append("allowlisted_lint")
        if "typecheck" in categories:
            allowed.append("allowlisted_typecheck")
        if "quality" in categories:
            allowed.append("allowlisted_quality")
        if "security_scan" in categories:
            allowed.append("allowlisted_security_scan")
        if "interpreter_version" in categories:
            allowed.append("allowlisted_diagnostic")
        if "read_only" in categories:
            allowed.append("allowlisted_read")
    return allowed


def evaluate_action(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Evalua una accion y devuelve ``{decision, riskLevel, reason, categories}``.

    Aplica las puertas de seguridad en orden de severidad: confinamiento al workspace, deploy a
    prod y git/shell destructivo (revision humana), luego las reglas por operacion de agente
    (cada una exige agentId, tool, perfil y contexto correctos o deniega), y finalmente las reglas
    por perfil para shell y adaptadores de runtime. Invariante: el perfil ``plan`` nunca ejecuta
    shell ni adaptadores; lo no allowlisted cae a ``requires_approval``, no a ``allow``.
    """
    command = str(input_payload.get("command") or "")
    git_operation = str(input_payload.get("gitOperation") or input_payload.get("git_operation") or "")
    deployment_target = str(
        input_payload.get("deploymentTarget") or input_payload.get("deployment_target") or ""
    )
    tool = str(input_payload.get("tool") or "")
    operation = str(input_payload.get("operation") or "")
    permission_profile = permission_profile_for(input_payload)
    classification = classify_command(command)
    categories = list(classification["categories"])
    if not is_path_inside(input_payload.get("path"), input_payload.get("workspacePath")):
        categories.append("path_outside_workspace")
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Action path is outside the allocated workspace.",
            "categories": categories,
        }

    if deployment_target.lower() == "prod":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Production deployment requires explicit human approval.",
            "categories": categories,
        }
    if git_operation in {"force_push", "push_main"} or classification["riskLevel"] == "critical":
        return {
            "decision": "requires_human",
            "riskLevel": "critical",
            "reason": "Dangerous git or destructive shell action requires human review.",
            "categories": categories,
        }

    if operation == "qa_agent_command":
        if input_payload.get("agentId") != "qa_agent":
            categories.append("qa_agent_command_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "QAAgent command execution is restricted to the QAAgent profile.",
                "categories": categories,
            }
        if tool != "shell":
            categories.append("qa_agent_command_tool_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "QAAgent commands must execute through shell with structured argv.",
                "categories": categories,
            }
        if permission_profile != "qa":
            categories.append("qa_agent_command_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "QAAgent command execution requires the qa permission profile.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("qa_agent_command_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "QAAgent command execution requires workspace and agent run context.",
                "categories": categories,
            }
        allowed = allowlisted_shell_categories(permission_profile, categories)
        if allowed and classification["riskLevel"] == "low":
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "QAAgent command is allowlisted for real QA execution.",
                "categories": categories + allowed + ["qa_agent_command"],
            }
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "QAAgent command is not in the low-risk QA allowlist.",
            "categories": [*categories, "qa_agent_command_gated"],
        }

    if operation == "devops_agent_command":
        if input_payload.get("agentId") != "devops_agent":
            categories.append("devops_agent_command_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DevOpsAgent command execution is restricted to the DevOpsAgent profile.",
                "categories": categories,
            }
        if tool != "shell":
            categories.append("devops_agent_command_tool_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DevOpsAgent commands must execute through shell with structured argv.",
                "categories": categories,
            }
        if permission_profile != "qa":
            categories.append("devops_agent_command_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DevOpsAgent command execution requires the qa permission profile.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("devops_agent_command_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DevOpsAgent command execution requires workspace and agent run context.",
                "categories": categories,
            }
        allowed = allowlisted_shell_categories(permission_profile, categories)
        if allowed and classification["riskLevel"] == "low":
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "DevOpsAgent command is allowlisted for real local validation.",
                "categories": categories + allowed + ["devops_agent_command"],
            }
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "DevOpsAgent command is not in the low-risk local validation allowlist.",
            "categories": [*categories, "devops_agent_command_gated"],
        }

    if operation == "security_agent_scanner":
        if input_payload.get("agentId") != "security_agent":
            categories.append("security_agent_scanner_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanner execution is restricted to the SecurityAgent profile.",
                "categories": categories,
            }
        if tool != "shell":
            categories.append("security_agent_scanner_tool_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanners must execute through shell with structured argv.",
                "categories": categories,
            }
        if permission_profile != "qa":
            categories.append("security_agent_scanner_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanner execution requires the qa permission profile.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("security_agent_scanner_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanner execution requires workspace and agent run context.",
                "categories": categories,
            }
        if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
            categories.append("security_agent_scanner_remote_or_secret_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanners must run as local scans without network or injected secrets.",
                "categories": categories,
            }
        argv = input_payload.get("commandArgv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            categories.append("security_agent_scanner_argv_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanner execution requires structured argv.",
                "categories": categories,
            }
        scanner = str(input_payload.get("runtimeId") or "").strip().lower()
        executable = Path(str(argv[0])).name.lower()
        scanner_executables = {
            "gitleaks": {"gitleaks", "gitleaks.cmd", "gitleaks.exe"},
            "semgrep": {"semgrep", "semgrep.cmd", "semgrep.exe"},
        }
        if scanner not in scanner_executables or executable not in scanner_executables[scanner]:
            categories.append("security_agent_scanner_executable_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent scanner execution is limited to gitleaks or semgrep local CLIs.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": f"SecurityAgent {scanner} scanner execution is allowlisted for local security evidence.",
            "categories": [*categories, "security_agent_scanner", scanner],
        }

    if operation in {
        "developer_agent_runtime",
        "developer_agent_model_call",
        "developer_agent_patch_apply",
        "developer_agent_qa",
    }:
        if input_payload.get("agentId") != "developer_agent":
            categories.append("developer_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DeveloperAgent runtime operations are restricted to the DeveloperAgent profile.",
                "categories": categories,
            }
        if permission_profile != "dev_safe":
            categories.append("developer_agent_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DeveloperAgent execution requires a dev_safe profile.",
                "categories": categories,
            }
        if not input_payload.get("workspaceId") or not input_payload.get("workspacePath"):
            categories.append("developer_agent_workspace_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DeveloperAgent execution requires an allocated workspace.",
                "categories": categories,
            }
        if not input_payload.get("runtimeId") and operation not in {
            "developer_agent_patch_apply",
            "developer_agent_qa",
        }:
            categories.append("developer_agent_runtime_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DeveloperAgent execution requires runtime context.",
                "categories": categories,
            }
        if not input_payload.get("agentRunId"):
            categories.append("developer_agent_run_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "DeveloperAgent execution requires an agent run audit id.",
                "categories": categories,
            }
        if operation == "developer_agent_runtime":
            if tool != "shell" or input_payload.get("runtimeId") not in {"codex_cli", "claude_code_cli"}:
                categories.append("developer_agent_cli_runtime_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "DeveloperAgent CLI execution is limited to configured Codex or Claude CLI runtimes.",
                    "categories": categories,
                }
            if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
                categories.append("developer_agent_cli_approval_required")
                return {
                    "decision": "requires_approval",
                    "riskLevel": "medium",
                    "reason": "DeveloperAgent CLI execution with network or secrets requires approval.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "DeveloperAgent CLI runtime execution is allowed inside the allocated workspace.",
                "categories": [*categories, "developer_agent_runtime"],
            }
        if operation == "developer_agent_model_call":
            if tool not in {"ollama", "openai_compatible"} or input_payload.get("runtimeId") != tool:
                categories.append("developer_agent_model_runtime_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "DeveloperAgent model execution is limited to configured OpenAI-compatible or Ollama adapters.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "DeveloperAgent model execution is allowed for a configured runtime adapter.",
                "categories": [*categories, "developer_agent_model_call"],
            }
        if operation == "developer_agent_patch_apply":
            if tool != "workspace_patch":
                categories.append("developer_agent_patch_tool_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "DeveloperAgent patch application must use the workspace_patch adapter.",
                    "categories": categories,
                }
            if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
                categories.append("developer_agent_patch_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "DeveloperAgent patch application cannot request network or secrets.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "DeveloperAgent patch application is allowed inside the allocated workspace.",
                "categories": [*categories, "developer_agent_patch_apply"],
            }
        if operation == "developer_agent_qa":
            if tool != "shell":
                categories.append("developer_agent_qa_tool_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "DeveloperAgent QA must execute through shell with structured argv.",
                    "categories": categories,
                }
            allowed = allowlisted_shell_categories(permission_profile, categories)
            if allowed and classification["riskLevel"] == "low":
                return {
                    "decision": "allow",
                    "riskLevel": "low",
                    "reason": "DeveloperAgent QA command is allowlisted for dev_safe execution.",
                    "categories": categories + allowed + ["developer_agent_qa"],
                }
            return {
                "decision": "requires_approval",
                "riskLevel": "medium",
                "reason": "DeveloperAgent QA command is not in the low-risk allowlist.",
                "categories": [*categories, "developer_agent_qa_gated"],
            }

    if operation == "architect_agent_model_call":
        if input_payload.get("agentId") != "architect_agent":
            categories.append("architect_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ArchitectAgent model execution is restricted to the ArchitectAgent profile.",
                "categories": categories,
            }
        if permission_profile != "plan":
            categories.append("architect_agent_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ArchitectAgent model execution requires the plan permission profile.",
                "categories": categories,
            }
        if tool not in {"ollama", "openai_compatible"} or input_payload.get("runtimeId") != tool:
            categories.append("architect_agent_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ArchitectAgent model execution is limited to configured OpenAI-compatible or Ollama adapters.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("architect_agent_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ArchitectAgent model execution requires workspace and agent run context.",
                "categories": categories,
            }
        if input_payload.get("secretsRequired"):
            categories.append("architect_agent_secrets_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ArchitectAgent prompts cannot request secret-bearing execution.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "ArchitectAgent model execution is allowed for a configured runtime adapter.",
            "categories": [*categories, "architect_agent_model_call"],
        }

    if operation in {"product_owner_runtime", "product_owner_model_call"}:
        if input_payload.get("agentId") != "product_owner_agent":
            categories.append("product_owner_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent runtime operations are restricted to the ProductOwnerAgent profile.",
                "categories": categories,
            }
        if permission_profile != "plan":
            categories.append("product_owner_agent_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent execution requires the plan permission profile.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("product_owner_agent_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent execution requires workspace and agent run context.",
                "categories": categories,
            }
        if operation == "product_owner_runtime":
            if tool != "shell" or input_payload.get("runtimeId") not in {"codex_cli", "claude_code_cli"}:
                categories.append("product_owner_cli_runtime_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "ProductOwnerAgent CLI execution is limited to configured Codex or Claude CLI runtimes.",
                    "categories": categories,
                }
            if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
                categories.append("product_owner_cli_approval_required")
                return {
                    "decision": "requires_approval",
                    "riskLevel": "medium",
                    "reason": "ProductOwnerAgent CLI execution with network or secrets requires approval.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "ProductOwnerAgent CLI runtime execution is allowed inside the allocated workspace.",
                "categories": [*categories, "product_owner_runtime"],
            }
        if tool not in {"ollama", "openai_compatible"} or input_payload.get("runtimeId") != tool:
            categories.append("product_owner_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent model execution is limited to configured OpenAI-compatible or Ollama adapters.",
                "categories": categories,
            }
        if input_payload.get("secretsRequired"):
            categories.append("product_owner_secrets_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "ProductOwnerAgent prompts cannot request secret-bearing execution.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "ProductOwnerAgent model execution is allowed for a configured runtime adapter.",
            "categories": [*categories, "product_owner_model_call"],
        }

    if operation == "security_agent_model_call":
        if input_payload.get("agentId") != "security_agent":
            categories.append("security_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent model analysis is restricted to the SecurityAgent profile.",
                "categories": categories,
            }
        if permission_profile != "qa":
            categories.append("security_agent_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent optional model analysis requires the qa permission profile.",
                "categories": categories,
            }
        if tool not in {"ollama", "openai_compatible"} or input_payload.get("runtimeId") != tool:
            categories.append("security_agent_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent model analysis is limited to configured OpenAI-compatible or Ollama adapters.",
                "categories": categories,
            }
        if (
            not input_payload.get("workspaceId")
            or not input_payload.get("workspacePath")
            or not input_payload.get("agentRunId")
        ):
            categories.append("security_agent_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent model analysis requires workspace and agent run context.",
                "categories": categories,
            }
        if input_payload.get("secretsRequired"):
            categories.append("security_agent_secrets_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "SecurityAgent prompts cannot request secret-bearing execution.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "SecurityAgent optional model analysis is allowed for a configured runtime adapter.",
            "categories": [*categories, "security_agent_model_call"],
        }

    if operation == "project_assessment":
        if input_payload.get("agentId") != "project_assessment_agent":
            categories.append("project_assessment_agent_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Project assessment inspection is restricted to the ProjectAssessment agent.",
                "categories": categories,
            }
        if permission_profile != "plan":
            categories.append("project_assessment_profile_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Project assessment inspection requires the plan permission profile.",
                "categories": categories,
            }
        if not input_payload.get("path") or not input_payload.get("agentRunId"):
            categories.append("project_assessment_context_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Project assessment inspection requires the project path and agent run context.",
                "categories": categories,
            }
        if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
            categories.append("project_assessment_remote_or_secret_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Project assessment must run as a local read-only scan without network or secrets.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": "Project assessment static read-only inspection is allowed for local assessment evidence.",
            "categories": [*categories, "project_assessment"],
        }

    if tool == "shell" and command:
        if operation == "issue_to_patch_runtime":
            if input_payload.get("agentId") != "aido_issue_to_patch_runner":
                categories.append("issue_to_patch_runtime_agent_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "issue_to_patch runtime execution is restricted to the workflow runner agent.",
                    "categories": categories,
                }
            if input_payload.get("workflowKind") != "issue_to_patch" or not input_payload.get("runtimeId"):
                categories.append("issue_to_patch_runtime_context_required")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "issue_to_patch runtime execution requires workflow and runtime context.",
                    "categories": categories,
                }
            if permission_profile != "dev_safe":
                categories.append("issue_to_patch_runtime_profile_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "issue_to_patch runtime execution requires a dev_safe implementer profile.",
                    "categories": categories,
                }
            if not input_payload.get("workspaceId") or not input_payload.get("workspacePath"):
                categories.append("issue_to_patch_workspace_required")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "issue_to_patch runtime execution requires an allocated workspace.",
                    "categories": categories,
                }
            if input_payload.get("networkRequired") or input_payload.get("secretsRequired"):
                categories.append("issue_to_patch_runtime_approval_required")
                return {
                    "decision": "requires_approval",
                    "riskLevel": "medium",
                    "reason": "issue_to_patch runtime execution with network or secrets requires approval.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Configured issue_to_patch runtime execution is allowed inside the allocated workspace.",
                "categories": [*categories, "issue_to_patch_runtime"],
            }
        if permission_profile == "plan":
            categories.append("profile_shell_denied")
            return {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": "Plan profile cannot execute shell commands.",
                "categories": categories,
            }
        if permission_profile == "release":
            categories.append("profile_release_shell_gated")
            return {
                "decision": "requires_approval",
                "riskLevel": "medium",
                "reason": "Release profile shell actions require explicit approval.",
                "categories": categories,
            }
        allowed = allowlisted_shell_categories(permission_profile, categories)
        if allowed and classification["riskLevel"] == "low":
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": f"{permission_profile} profile allows this shell command category.",
                "categories": categories + allowed,
            }
        if permission_profile == "qa":
            categories.append("profile_test_only")
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "Shell command is not in the permission profile allowlist.",
            "categories": categories,
        }

    if (
        tool in {"mcp", "openhands", "swe_agent", "ollama", "openai_compatible", "workspace_patch"}
        and command
    ):
        if permission_profile == "plan":
            categories.append("profile_runtime_adapter_denied")
            return {
                "decision": "deny",
                "riskLevel": "medium",
                "reason": "Plan profile cannot execute runtime adapter commands.",
                "categories": categories,
            }
        if classification["riskLevel"] == "critical":
            return {
                "decision": "requires_human",
                "riskLevel": "critical",
                "reason": "Runtime adapter command is critical and requires explicit human review.",
                "categories": categories,
            }
        if classification["riskLevel"] != "low":
            return {
                "decision": "requires_approval",
                "riskLevel": "medium",
                "reason": "Runtime adapter command is not in the low-risk allowlist and requires approval.",
                "categories": categories,
            }
    if (
        tool == "mcp"
        and operation
        and not command
        and operation not in {"tools/list", "resources/list", "prompts/list"}
    ):
        return {
            "decision": "requires_approval",
            "riskLevel": "medium",
            "reason": "MCP tool execution beyond read-only discovery requires approval.",
            "categories": [*categories, "mcp_operation_gated"],
        }

    if classification["riskLevel"] == "low":
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": "Low-risk test/build/read-only action allowed by internal policy.",
            "categories": categories,
        }
    return {
        "decision": "allow",
        "riskLevel": "low",
        "reason": "Read-only or low-risk action allowed by internal policy.",
        "categories": categories,
    }
