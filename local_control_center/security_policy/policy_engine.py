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
from urllib.parse import urlparse

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

GIT_WORKSPACE_AGENT_ID = "git_workspace_agent"
GIT_WORKSPACE_READ_COMMANDS = {"status", "diff", "log", "rev-parse"}
GIT_WORKSPACE_WRITE_COMMANDS = {"branch", "checkout"}
GIT_PROTECTED_BRANCHES = {"main", "master", "dev", "HEAD"}
GIT_BRANCH_READ_FLAGS = {"--show-current", "--remotes", "-r", "--list"}
GIT_BRANCH_MUTATION_FLAGS = {
    "-d",
    "-D",
    "--delete",
    "-f",
    "--force",
    "-m",
    "-M",
    "--move",
    "-c",
    "-C",
    "--copy",
}
GIT_CHECKOUT_BLOCKED_FLAGS = {"-f", "--force", "--orphan", "--detach", "-B", "-b"}
GIT_WORKTREE_LIST_FLAGS = {"--porcelain"}
MODEL_RUNTIME_TOOLS = {"ollama", "openai_compatible", "openrouter", "nvidia_nim", "anthropic_api"}
MODEL_RUNTIME_REASON = "configured Ollama, OpenAI-compatible, OpenRouter, NVIDIA NIM or Anthropic adapters"
PLAN_EXECUTION_ADAPTER_TOOLS = {
    *MODEL_RUNTIME_TOOLS,
    "openhands",
    "swe_agent",
    "workspace_patch",
}


def _model_runtime_binding_is_valid(input_payload: dict[str, Any], tool: str) -> bool:
    runtime_id = str(input_payload.get("runtimeId") or "")
    provider_id = str(input_payload.get("providerId") or "")
    if tool not in MODEL_RUNTIME_TOOLS or not runtime_id or provider_id != runtime_id:
        return False
    return input_payload.get("providerFamily") == tool


def _is_safe_git_arg(value: str | None) -> bool:
    text = (value or "").strip()
    if not text or text != value or text.startswith("-") or len(text) > 200:
        return False
    if ".." in text or "@{" in text or "\n" in text or "\r" in text:
        return False
    return not any(char in set(" ~^:?*[\\") or ord(char) < 32 for char in text)


def _is_safe_remote_name(value: str | None) -> bool:
    text = (value or "").strip()
    if not text or text != value or text.startswith("-") or len(text) > 80:
        return False
    return all(char.isalnum() or char in {".", "_", "-"} for char in text)


def _remote_url_embeds_credentials(value: str | None) -> bool:
    text = (value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme == "https":
        return bool(parsed.username or parsed.password)
    if parsed.scheme == "ssh":
        return bool(parsed.password)
    return False


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


def evaluate_git_workspace_command(
    input_payload: dict[str, Any], *, permission_profile: str, categories: list[str]
) -> dict[str, Any]:
    """Evalua comandos Git locales del slice Git Workspace.

    Esta rama no concede Git general. Solo permite argv estructurado, ejecutable ``git`` y los
    subcomandos que el slice usa para status/branches/diff/worktree y branch/checkout seguros.
    """
    if input_payload.get("agentId") != GIT_WORKSPACE_AGENT_ID:
        categories.append("git_workspace_agent_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace commands are restricted to the GitWorkspace agent profile.",
            "categories": categories,
        }
    if input_payload.get("tool") != "shell":
        categories.append("git_workspace_tool_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace commands must execute through shell with structured argv.",
            "categories": categories,
        }
    if permission_profile != "dev_safe":
        categories.append("git_workspace_profile_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace execution requires the dev_safe permission profile.",
            "categories": categories,
        }
    if (
        not input_payload.get("workspaceId")
        or not input_payload.get("workspacePath")
        or not input_payload.get("agentRunId")
    ):
        categories.append("git_workspace_context_required")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace execution requires workspace and agent run context.",
            "categories": categories,
        }
    if input_payload.get("secretsRequired"):
        categories.append("git_workspace_secret_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace commands must not receive injected secrets.",
            "categories": categories,
        }
    argv = input_payload.get("commandArgv")
    if (
        not isinstance(argv, list)
        or len(argv) < 2
        or not all(isinstance(item, str) and item for item in argv)
    ):
        categories.append("git_workspace_argv_required")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace execution requires argv shaped as [git, subcommand, ...].",
            "categories": categories,
        }
    executable = Path(str(argv[0])).name.lower()
    if executable not in {"git", "git.exe", "git.cmd"}:
        categories.append("git_workspace_executable_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace execution is limited to the local git executable.",
            "categories": categories,
        }
    subcommand = str(argv[1]).lower()
    args = [str(item) for item in argv[2:]]
    git_operation = str(input_payload.get("gitOperation") or "")
    network_required = bool(input_payload.get("networkRequired"))
    if network_required and not (
        (subcommand == "ls-remote" and git_operation == "remote_test")
        or (subcommand == "push" and git_operation == "push_branch")
    ):
        categories.append("git_workspace_unapproved_network_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git workspace network access is limited to explicit remote tests and branch pushes.",
            "categories": categories,
        }
    if subcommand in GIT_WORKSPACE_READ_COMMANDS:
        return {
            "decision": "allow",
            "riskLevel": "low",
            "reason": f"Git {subcommand} is allowlisted for local workspace evidence.",
            "categories": [*categories, "git_workspace_command", f"git_{subcommand}"],
        }
    if subcommand == "init":
        if git_operation != "init_repository":
            categories.append("git_workspace_init_context_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git init requires the gitOperation=init_repository context.",
                "categories": categories,
            }
        if args and not (
            len(args) == 2 and args[0] in {"--initial-branch", "-b"} and args[1] in {"main", "dev"}
        ):
            categories.append("git_workspace_init_shape_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git init is limited to --initial-branch main/dev.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "Git init is allowlisted for project repository setup.",
            "categories": [*categories, "git_workspace_command", "git_init"],
        }
    if subcommand == "push":
        if (
            git_operation != "push_branch"
            or not network_required
            or len(args) != 2
            or not _is_safe_remote_name(args[0])
            or not _is_safe_git_arg(args[1])
        ):
            categories.append("git_workspace_push_shape_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git push is limited to `push <remote> <branch>` (no force/refspec) with push_branch.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "high",
            "reason": "Git branch push is allowlisted for the configured landing flow.",
            "categories": [*categories, "git_workspace_command", "git_push_branch"],
        }
    if subcommand == "merge":
        merge_ok = (
            len(args) == 4
            and args[0] == "--no-ff"
            and args[1] == "-m"
            and bool(str(args[2]).strip())
            and not str(args[2]).startswith("-")
            and _is_safe_git_arg(args[3])
            and args[3] not in GIT_PROTECTED_BRANCHES
        )
        abort_ok = args == ["--abort"]
        if git_operation != "merge_work_branch" or not (merge_ok or abort_ok):
            categories.append("git_workspace_merge_shape_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git merge is limited to `merge --no-ff -m <msg> <work-branch>` or `merge --abort`.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "Git work-branch merge into the checked-out base is allowlisted for landing.",
            "categories": [*categories, "git_workspace_command", "git_merge_work_branch"],
        }
    if subcommand == "add":
        if git_operation == "diff_capture_intent_to_add" and args == ["--intent-to-add", "--", "."]:
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git intent-to-add is allowlisted only to capture untracked files in diff evidence.",
                "categories": [*categories, "git_workspace_command", "git_intent_to_add"],
            }
        if git_operation == "stage_changes" and args == ["-A"]:
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "Git add -A is allowlisted to stage the isolated worktree before commit.",
                "categories": [*categories, "git_workspace_command", "git_add"],
            }
        categories.append("git_workspace_add_shape_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git add is limited to `add -A` (stage_changes) or `add --intent-to-add` (diff capture).",
            "categories": categories,
        }
    if subcommand == "commit":
        message_ok = (
            len(args) >= 2 and args[-2] == "-m" and bool(args[-1].strip()) and not args[-1].startswith("-")
        )
        prefix_ok = args[:-2] in ([], ["--allow-empty"])
        if git_operation != "commit_changes" or not message_ok or not prefix_ok:
            categories.append("git_workspace_commit_shape_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git commit is limited to `commit [--allow-empty] -m <message>` with commit_changes.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "Git commit is allowlisted to persist agent work on the isolated worktree branch.",
            "categories": [*categories, "git_workspace_command", "git_commit"],
        }
    if subcommand == "remote":
        if args == ["-v"]:
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "Git remote listing is allowlisted for local workspace evidence.",
                "categories": [*categories, "git_workspace_command", "git_remote_read"],
            }
        if git_operation == "remote_add":
            if len(args) != 3 or args[0] != "add" or not _is_safe_remote_name(args[1]):
                categories.append("git_workspace_remote_add_shape_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git remote add must use argv [git, remote, add, name, url].",
                    "categories": categories,
                }
            if _remote_url_embeds_credentials(args[2]):
                categories.append("git_workspace_remote_add_secret_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git remote URLs must not embed tokens or credentials.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git remote add is allowlisted after service-level URL validation.",
                "categories": [*categories, "git_workspace_command", "git_remote_add"],
            }
        categories.append("git_workspace_remote_mode_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git remote command is limited to listing or remote add with explicit context.",
            "categories": categories,
        }
    if subcommand == "ls-remote":
        if git_operation != "remote_test":
            categories.append("git_workspace_ls_remote_context_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git ls-remote requires the gitOperation=remote_test context.",
                "categories": categories,
            }
        if not network_required:
            categories.append("git_workspace_ls_remote_network_required")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git ls-remote must explicitly declare networkRequired=true.",
                "categories": categories,
            }
        if len(args) != 2 or args[0] != "--heads" or not _is_safe_remote_name(args[1]):
            categories.append("git_workspace_ls_remote_shape_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git ls-remote is limited to argv [git, ls-remote, --heads, remote].",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "Git remote test is allowlisted with explicit network gating.",
            "categories": [*categories, "git_workspace_command", "git_remote_test"],
        }
    if subcommand == "branch":
        git_operation = str(input_payload.get("gitOperation") or "")
        if git_operation == "init_default_branch":
            if len(args) != 2 or args[0] != "-M" or args[1] not in {"main", "dev"}:
                categories.append("git_workspace_init_default_branch_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git default branch rename is limited to -M main/dev after init.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git default branch rename is allowlisted for project repository setup.",
                "categories": [*categories, "git_workspace_command", "git_init_default_branch"],
            }
        if git_operation == "advance_base_after_merge":
            # El primitivo verifica el fast-forward (merge-base) ANTES de pedir este avance; la
            # policy fija la forma exacta para que no pueda mover refs arbitrarios ni forzar hist.
            if len(args) != 3 or args[0] != "-f" or not all(_is_safe_git_arg(arg) for arg in args[1:]):
                categories.append("git_workspace_branch_advance_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git base advance is limited to `branch -f <base> <landing-ref>`.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Fast-forward base advance after a verified landing merge is allowlisted.",
                "categories": [*categories, "git_workspace_command", "git_branch_advance_base"],
            }
        if git_operation == "delete_branch":
            if (
                len(args) != 2
                or args[0] not in {"-D", "--delete"}
                or not _is_safe_git_arg(args[1])
                or args[1] in GIT_PROTECTED_BRANCHES
            ):
                categories.append("git_workspace_branch_delete_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git branch delete is limited to -D of a safe non-protected work branch.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git work-branch delete is allowlisted for workspace GC.",
                "categories": [*categories, "git_workspace_command", "git_branch_delete"],
            }
        if git_operation == "create_branch":
            if any(arg in GIT_BRANCH_MUTATION_FLAGS or arg.startswith("-") for arg in args):
                categories.append("git_workspace_branch_flag_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git branch creation cannot include mutation flags.",
                    "categories": categories,
                }
            if not args or not all(_is_safe_git_arg(arg) for arg in args):
                categories.append("git_workspace_branch_name_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git branch creation requires safe branch/base refs.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git branch creation is allowlisted inside the allocated workspace.",
                "categories": [*categories, "git_workspace_command", "git_branch_create"],
            }
        if all(arg in GIT_BRANCH_READ_FLAGS or arg.startswith("--format=") for arg in args):
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "Git branch listing is allowlisted for local workspace evidence.",
                "categories": [*categories, "git_workspace_command", "git_branch_read"],
            }
        categories.append("git_workspace_branch_mode_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git branch command is limited to branch creation or read-only branch listing.",
            "categories": categories,
        }
    if subcommand == "checkout":
        if input_payload.get("gitOperation") != "checkout":
            categories.append("git_workspace_checkout_context_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git checkout requires the gitOperation=checkout context.",
                "categories": categories,
            }
        if any(arg in GIT_CHECKOUT_BLOCKED_FLAGS or arg.startswith("-") for arg in args):
            categories.append("git_workspace_checkout_flag_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": "Git checkout cannot include branch creation, detach or force flags.",
                "categories": categories,
            }
        return {
            "decision": "allow",
            "riskLevel": "medium",
            "reason": "Git checkout is allowlisted after service-level dirty-tree preflight.",
            "categories": [*categories, "git_workspace_command", "git_checkout"],
        }
    if subcommand == "worktree":
        git_operation = str(input_payload.get("gitOperation") or "")
        if args[:1] == ["list"] and all(arg in GIT_WORKTREE_LIST_FLAGS for arg in args[1:]):
            return {
                "decision": "allow",
                "riskLevel": "low",
                "reason": "Git worktree listing is allowlisted for local workspace evidence.",
                "categories": [*categories, "git_workspace_command", "git_worktree_list"],
            }
        if git_operation == "worktree_add":
            if len(args) != 5 or args[0] != "add" or args[1] != "-b":
                categories.append("git_workspace_worktree_add_shape_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git worktree add must use argv [git, worktree, add, -b, branch, path, base].",
                    "categories": categories,
                }
            branch, target_path, base_ref = args[2], args[3], args[4]
            if any(
                not item or item.startswith("-") or "\n" in item or "\r" in item
                for item in (branch, target_path, base_ref)
            ):
                categories.append("git_workspace_worktree_add_arg_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git worktree add arguments must be non-empty refs/paths, not flags.",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git worktree creation is allowlisted for isolated workspace setup.",
                "categories": [*categories, "git_workspace_command", "git_worktree_add"],
            }
        if git_operation == "worktree_remove":
            if (
                len(args) != 3
                or args[0] != "remove"
                or args[1] != "--force"
                or not args[2]
                or args[2].startswith("-")
            ):
                categories.append("git_workspace_worktree_remove_shape_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git worktree removal must use argv [git, worktree, remove, --force, path].",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git worktree cleanup is allowlisted for archived isolated workspaces.",
                "categories": [*categories, "git_workspace_command", "git_worktree_remove"],
            }
        categories.append("git_workspace_worktree_mode_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git worktree command is limited to list, add, or remove with explicit context.",
            "categories": categories,
        }
    if subcommand == "apply":
        git_operation = str(input_payload.get("gitOperation") or "")
        if git_operation == "apply_check":
            if len(args) != 2 or args[0] != "--check" or not args[1] or args[1].startswith("-"):
                categories.append("git_workspace_apply_check_shape_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git apply check must use argv [git, apply, --check, patch_path].",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git apply --check is allowlisted for verified patch promotion.",
                "categories": [*categories, "git_workspace_command", "git_apply_check"],
            }
        if git_operation == "apply_patch":
            if len(args) != 1 or not args[0] or args[0].startswith("-"):
                categories.append("git_workspace_apply_shape_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": "Git apply must use argv [git, apply, patch_path].",
                    "categories": categories,
                }
            return {
                "decision": "allow",
                "riskLevel": "medium",
                "reason": "Git apply is allowlisted only for approved patch promotion.",
                "categories": [*categories, "git_workspace_command", "git_apply_patch"],
            }
        categories.append("git_workspace_apply_denied")
        return {
            "decision": "deny",
            "riskLevel": "high",
            "reason": "Git apply requires explicit apply_check or apply_patch context.",
            "categories": categories,
        }
    return {
        "decision": "deny",
        "riskLevel": "high",
        "reason": "Git workspace command is not in the allowed local Git subcommand set.",
        "categories": [*categories, "git_workspace_command_denied"],
    }


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

    if operation == "git_workspace_command":
        return evaluate_git_workspace_command(
            input_payload, permission_profile=permission_profile, categories=categories
        )

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
            if tool != "shell" or input_payload.get("runtimeId") not in {
                "codex_cli",
                "claude_code_cli",
                "openhands",
                "swe_agent",
            }:
                categories.append("developer_agent_cli_runtime_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": (
                        "DeveloperAgent CLI execution is limited to configured Codex, Claude, "
                        "OpenHands, or SWE-agent CLI runtimes."
                    ),
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
            if not _model_runtime_binding_is_valid(input_payload, tool):
                categories.append("developer_agent_model_runtime_denied")
                return {
                    "decision": "deny",
                    "riskLevel": "high",
                    "reason": f"DeveloperAgent model execution is limited to {MODEL_RUNTIME_REASON}.",
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
        if not _model_runtime_binding_is_valid(input_payload, tool):
            categories.append("architect_agent_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": f"ArchitectAgent model execution is limited to {MODEL_RUNTIME_REASON}.",
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
        if not _model_runtime_binding_is_valid(input_payload, tool):
            categories.append("product_owner_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": f"ProductOwnerAgent model execution is limited to {MODEL_RUNTIME_REASON}.",
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
        if not _model_runtime_binding_is_valid(input_payload, tool):
            categories.append("security_agent_model_runtime_denied")
            return {
                "decision": "deny",
                "riskLevel": "high",
                "reason": f"SecurityAgent model analysis is limited to {MODEL_RUNTIME_REASON}.",
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

    if permission_profile == "plan" and tool in PLAN_EXECUTION_ADAPTER_TOOLS:
        categories.append("profile_runtime_adapter_denied")
        return {
            "decision": "deny",
            "riskLevel": "medium",
            "reason": (
                "Plan profile cannot execute an unscoped runtime adapter; a recognized internal "
                "operation is required."
            ),
            "categories": categories,
        }
    if tool in {"mcp", *PLAN_EXECUTION_ADAPTER_TOOLS} and command:
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
