"""Gestor de ramas gitflow: inventario, limpieza de ramas integradas, prune, rename y worktrees.

Todo Git corre por ``GitWorkspaceService._run_brokered_command`` (ToolBroker + policy
``git_workspace_command``), con un agent run auditable por operación y la traza de cada tool call en
la respuesta. El gestor nunca reescribe historia: no hace rebase, no hace force-push y sólo usa
``branch -D`` cuando la rama está verificada como integrada o el operador la marcó explícitamente
para forzar. Las ramas protegidas (``dev``/``develop``/``main``/``master``, HEAD, la rama de
integración configurada y cualquier rama con worktree) se rechazan antes de llegar a Git.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_control_center.settings.resolver import resolve_setting_value
from local_control_center.shared.event_bus import EventBus
from local_control_center.shared.serialization import json_loads

from .service import (
    GitWorkspaceContext,
    GitWorkspaceService,
    OperationContext,
    _is_safe_ref,
    _is_safe_remote_name,
    _parse_remotes,
    _parse_worktrees,
    _split_lines,
)

GITFLOW_MAINLINE_BRANCHES = frozenset({"dev", "develop", "main", "master"})
GITFLOW_PREFIXES: tuple[tuple[str, str], ...] = (
    ("feature/", "feature"),
    ("bugfix/", "bugfix"),
    ("release/", "release"),
    ("hotfix/", "hotfix"),
    ("support/", "support"),
)
GITFLOW_RENAME_PREFIXES = tuple(prefix for prefix, _ in GITFLOW_PREFIXES)
AIDO_BRANCH_PREFIXES = ("aido/", "codex/")
DEFAULT_INTEGRATION_BRANCH = "dev"
DEFAULT_STALE_DAYS = 30
DEFAULT_FAR_BEHIND_COMMITS = 50
FIELD_SEPARATOR = "\x1f"
REF_FORMAT = "%1f".join(
    [
        "%(refname)",
        "%(refname:short)",
        "%(objectname)",
        "%(committerdate:iso-strict)",
        "%(authorname)",
        "%(subject)",
        "%(upstream:short)",
        "%(upstream:track)",
        "%(symref)",
        "%(tree)",
    ]
)
ACTIVE_AIDO_WORKSPACE_STATUSES = ("allocated", "preparing", "ready", "locked", "running", "dirty")
NOT_FULLY_MERGED_MARKER = "not fully merged"


@dataclass
class _RunState:
    """Contexto de una operación del gestor: agent run, trazas acumuladas y resolución de refs."""

    ctx: GitWorkspaceContext
    op: OperationContext
    traces: list[dict[str, Any]] = field(default_factory=list)


def gitflow_type(short_name: str, *, work_prefixes: tuple[str, ...] = AIDO_BRANCH_PREFIXES) -> str:
    """Clasifica una rama por su prefijo gitflow (o como rama propia de AIDO / ``other``)."""
    if short_name in GITFLOW_MAINLINE_BRANCHES:
        return "integration"
    for prefix, kind in GITFLOW_PREFIXES:
        if short_name.startswith(prefix):
            return kind
    if short_name.startswith(work_prefixes):
        return "aido"
    return "other"


def suggest_gitflow_name(short_name: str) -> str:
    """Sugiere un nombre gitflow para una rama sin prefijo (``bugfix/`` si parece un fix)."""
    slug = re.sub(r"[^A-Za-z0-9._/-]+", "-", short_name).strip("-/.") or "branch"
    prefix = "bugfix/" if re.search(r"(^|[-_/])(fix|bug|bugfix)([-_/]|$)", slug, re.I) else "feature/"
    return f"{prefix}{slug.replace('/', '-')}"


def _parse_track(value: str) -> tuple[str, int, int]:
    text = value.strip()
    if not text:
        return "tracking", 0, 0
    if "gone" in text:
        return "gone", 0, 0
    ahead = re.search(r"ahead (\d+)", text)
    behind = re.search(r"behind (\d+)", text)
    return "tracking", int(ahead.group(1)) if ahead else 0, int(behind.group(1)) if behind else 0


def _age_days(date: str, now: datetime) -> int | None:
    try:
        parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0, int((now - parsed).total_seconds() // 86400))


class GitBranchManager:
    """Operaciones del gestor de ramas de un proyecto, todas brokered y auditadas."""

    def __init__(self, service: GitWorkspaceService):
        self.service = service
        self.connection = service.connection

    # ------------------------------------------------------------------ plumbing

    def _setting(self, key: str, project_id: str) -> str:
        value = resolve_setting_value(connection=self.connection, key=key, project_id=project_id)
        return str(value or "").strip()

    def integration_branch(self, project_id: str) -> str:
        """Rama de integración configurada (``project.git.baseBranch``), ``dev`` por defecto."""
        value = self._setting("project.git.baseBranch", project_id) or DEFAULT_INTEGRATION_BRANCH
        return value if _is_safe_ref(value) else DEFAULT_INTEGRATION_BRANCH

    def _work_prefixes(self, project_id: str) -> tuple[str, ...]:
        prefix = self._setting("project.git.workBranchPrefix", project_id).strip("/")
        extra = (f"{prefix}/",) if prefix and _is_safe_ref(prefix) else ()
        return tuple(dict.fromkeys([*AIDO_BRANCH_PREFIXES, *extra]))

    def _unavailable(self, ctx: GitWorkspaceContext) -> dict[str, Any] | None:
        if not shutil.which("git"):
            reason = "Git executable was not found on PATH."
        elif not ctx.workspace_id:
            reason = "Project path does not exist or is not a directory."
        elif not (ctx.root / ".git").exists():
            reason = "Project path is not a Git repository."
        else:
            return None
        return {
            "status": "configuration_required",
            "reason": reason,
            "projectId": ctx.project["id"],
            "workspaceId": ctx.workspace_id,
        }

    def _begin(
        self, project_id: str, operation: str, payload: dict[str, Any]
    ) -> tuple[_RunState | None, dict]:
        ctx = self.service._project_context(project_id)
        unavailable = self._unavailable(ctx)
        if unavailable is not None:
            return None, unavailable
        op = self.service._begin_operation(
            ctx=ctx, operation=operation, profile=self.service._git_profile(), payload=payload
        )
        return _RunState(ctx=ctx, op=op), {}

    def _git(
        self,
        state: _RunState,
        args: list[str],
        *,
        git_operation: str | None = None,
        network: bool = False,
    ):
        result = self.service._run_brokered_command(
            ctx=state.ctx,
            op=state.op,
            argv=["git", *args],
            operation="git_workspace_command",
            runtime_id="git",
            git_operation=git_operation,
            capability="git",
            network_required=network,
        )
        state.traces.append(result.trace)
        return result

    @staticmethod
    def _policy_ids(traces: list[dict[str, Any]]) -> list[str]:
        return [str(trace["permissionDecisionId"]) for trace in traces if trace.get("permissionDecisionId")]

    def _finish(self, state: _RunState, response: dict[str, Any]) -> dict[str, Any]:
        response["toolCalls"] = state.traces
        response["policyDecisionIds"] = self._policy_ids(state.traces)
        self.service._finish_operation(state.op, status=response["status"], output=response)
        return response

    # ------------------------------------------------------------------ inventory

    def _aido_workspaces(self, project_id: str) -> dict[str, list[dict[str, Any]]]:
        placeholders = ",".join("?" for _ in ACTIVE_AIDO_WORKSPACE_STATUSES)
        rows = self.connection.execute(
            f"""SELECT id, path, status, task_id, metadata FROM workspaces
            WHERE project_id=? AND isolation_type='git_worktree' AND status IN ({placeholders})
            ORDER BY created_at ASC""",
            (project_id, *ACTIVE_AIDO_WORKSPACE_STATUSES),
        ).fetchall()
        by_branch: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metadata = json_loads(row["metadata"], {}) or {}
            branch = str((metadata.get("gitWorktree") or {}).get("branchName") or "")
            if not branch:
                continue
            by_branch.setdefault(branch, []).append(
                {
                    "workspaceId": row["id"],
                    "path": row["path"],
                    "status": row["status"],
                    "taskId": row["task_id"] or "",
                }
            )
        return by_branch

    def _collect(
        self,
        state: _RunState,
        *,
        stale_days: int = DEFAULT_STALE_DAYS,
        far_behind_commits: int = DEFAULT_FAR_BEHIND_COMMITS,
    ) -> dict[str, Any]:
        project_id = str(state.ctx.project["id"])
        integration = self.integration_branch(project_id)
        work_prefixes = self._work_prefixes(project_id)
        now = datetime.now(UTC)

        current = self._git(state, ["branch", "--show-current"])
        current_branch = current.stdout.strip() if current.return_code == 0 else ""
        listing = self._git(state, ["for-each-ref", f"--format={REF_FORMAT}", "refs/heads", "refs/remotes"])
        if listing.return_code != 0:
            raise RuntimeError(listing.stderr.strip() or listing.reason)
        remotes_result = self._git(state, ["remote", "-v"])
        remotes = sorted(
            {item["name"] for item in _parse_remotes(remotes_result.stdout)}
            if remotes_result.return_code == 0
            else set()
        )
        worktrees_result = self._git(state, ["worktree", "list", "--porcelain"])
        worktrees = _parse_worktrees(worktrees_result.stdout) if worktrees_result.return_code == 0 else []
        checked_out = {item["branch"]: item["path"] for item in worktrees if item.get("branch")}
        aido_workspaces = self._aido_workspaces(project_id)

        refs: list[dict[str, Any]] = []
        for line in _split_lines(listing.stdout):
            parts = line.split(FIELD_SEPARATOR)
            if len(parts) != 10:
                continue
            refname, short, objectname, date, author, subject, upstream, track, symref, tree = parts
            if symref or refname.endswith("/HEAD"):
                continue
            refs.append(
                {
                    "refname": refname,
                    "short": short,
                    "object": objectname,
                    "date": date,
                    "author": author,
                    "subject": subject,
                    "upstream": upstream,
                    "track": track,
                    "tree": tree,
                }
            )
        local_refs = {item["short"]: item for item in refs if item["refname"].startswith("refs/heads/")}
        integration_ref = local_refs.get(integration)
        integration_target = f"refs/heads/{integration}" if integration_ref else None
        if integration_target is None:
            # Sin la rama local, la remote-tracking (origin/dev) sigue siendo una base honesta.
            for remote in remotes:
                candidate = next(
                    (item for item in refs if item["refname"] == f"refs/remotes/{remote}/{integration}"), None
                )
                if candidate:
                    integration_ref = candidate
                    integration_target = candidate["refname"]
                    break
        mainline = next(
            (name for name in ("main", "master") if name in local_refs and name != integration), None
        )

        merged: set[str] = set()
        ahead_behind: dict[str, tuple[int, int]] = {}
        ahead_vs_main: dict[str, int] = {}
        if integration_target:
            merged_result = self._git(
                state,
                [
                    "for-each-ref",
                    f"--merged={integration_target}",
                    "--format=%(refname)",
                    "refs/heads",
                    "refs/remotes",
                ],
            )
            if merged_result.return_code == 0:
                merged = set(_split_lines(merged_result.stdout))
            atoms = [f"%(ahead-behind:{integration_target})"]
            if mainline:
                atoms.append(f"%(ahead-behind:refs/heads/{mainline})")
            counts = self._git(
                state,
                ["for-each-ref", "--format=%(refname)%1f" + "%1f".join(atoms), "refs/heads", "refs/remotes"],
            )
            if counts.return_code == 0:
                for line in _split_lines(counts.stdout):
                    parts = line.split(FIELD_SEPARATOR)
                    try:
                        ahead, behind = (int(value) for value in parts[1].split())
                    except (IndexError, ValueError):
                        continue
                    ahead_behind[parts[0]] = (ahead, behind)
                    if mainline and len(parts) > 2 and parts[2].split()[:1] and parts[2].split()[0].isdigit():
                        ahead_vs_main[parts[0]] = int(parts[2].split()[0])
            else:
                # Git < 2.41 no conoce %(ahead-behind); se cae a rev-list por rama.
                for item in refs:
                    rev = self._git(
                        state,
                        ["rev-list", "--left-right", "--count", f"{integration_target}...{item['refname']}"],
                    )
                    try:
                        behind, ahead = (int(value) for value in rev.stdout.split())
                    except ValueError:
                        continue
                    ahead_behind[item["refname"]] = (ahead, behind)

        merge_tree_supported = True
        branches: list[dict[str, Any]] = []
        for item in refs:
            is_local = item["refname"].startswith("refs/heads/")
            short = item["short"]
            remote_name: str | None = None
            short_name = short
            if not is_local:
                remote_name = next((remote for remote in remotes if short.startswith(f"{remote}/")), None)
                if remote_name is None and "/" in short:
                    remote_name = short.split("/", 1)[0]
                short_name = short[len(remote_name) + 1 :] if remote_name else short
            kind = gitflow_type(short_name, work_prefixes=work_prefixes)

            protected_reason: str | None = None
            if short_name == integration:
                protected_reason = "integration_branch"
            elif short_name in GITFLOW_MAINLINE_BRANCHES:
                protected_reason = "gitflow_mainline"
            elif is_local and short == current_branch:
                protected_reason = "current_branch"
            elif is_local and short in checked_out:
                protected_reason = "checked_out_in_worktree"

            ahead, behind = ahead_behind.get(item["refname"], (None, None))
            if not integration_target:
                merge_state, evidence = "unknown", None
            elif item["refname"] in merged:
                merge_state, evidence = "merged", "merge-base --is-ancestor"
            else:
                merge_state, evidence = "not_merged", None
            if (
                merge_state == "not_merged"
                and protected_reason is None
                and integration_target
                and (is_local or not any(local == short_name for local in local_refs))
            ):
                cherry = self._git(state, ["cherry", integration_target, item["refname"]])
                cherry_lines = _split_lines(cherry.stdout) if cherry.return_code == 0 else []
                if cherry_lines and all(line.startswith("-") for line in cherry_lines):
                    merge_state, evidence = "squash_probable", "git cherry"
                elif merge_tree_supported and integration_ref:
                    tree = self._git(
                        state,
                        ["merge-tree", "--write-tree", integration_target, item["refname"]],
                        git_operation="branch_inventory",
                    )
                    first = tree.stdout.strip().splitlines()[0] if tree.stdout.strip() else ""
                    if tree.return_code == 0 and first and first == integration_ref["tree"]:
                        merge_state, evidence = "squash_probable", "merge-tree"
                    elif tree.return_code not in (0, 1):
                        merge_tree_supported = False

            status, up_ahead, up_behind = ("none", 0, 0)
            if item["upstream"]:
                status, up_ahead, up_behind = _parse_track(item["track"])
            age = _age_days(item["date"], now)
            stale = protected_reason is None and age is not None and age > stale_days
            far_behind = protected_reason is None and behind is not None and behind > far_behind_commits
            based_on_main = bool(
                mainline
                and kind in {"feature", "bugfix"}
                and merge_state == "not_merged"
                and item["refname"] in ahead_vs_main
                and ahead is not None
                and ahead > ahead_vs_main[item["refname"]]
            )
            workspaces = aido_workspaces.get(short, []) if is_local else []
            suggested = None
            if (
                is_local
                and kind == "other"
                and protected_reason is None
                and status == "none"
                and not workspaces
            ):
                candidate = suggest_gitflow_name(short)
                suggested = candidate if candidate not in local_refs else None
            branches.append(
                {
                    "name": short,
                    "ref": item["refname"],
                    "kind": "local" if is_local else "remote",
                    "remote": remote_name,
                    "shortName": short_name,
                    "type": kind,
                    "current": is_local and short == current_branch,
                    "protected": protected_reason is not None,
                    "protectedReason": protected_reason,
                    "lastCommit": {
                        "hash": item["object"],
                        "date": item["date"],
                        "author": item["author"],
                        "subject": item["subject"],
                    },
                    "ageDays": age,
                    "ahead": ahead,
                    "behind": behind,
                    "mergeState": merge_state,
                    "mergeEvidence": evidence,
                    "upstream": {
                        "name": item["upstream"],
                        "status": status,
                        "ahead": up_ahead,
                        "behind": up_behind,
                    },
                    "worktreePath": checked_out.get(short) if is_local else None,
                    "aidoWorkspaces": workspaces,
                    "stale": stale,
                    "farBehind": far_behind,
                    "basedOnMain": based_on_main,
                    "suggestedName": suggested,
                    "deletable": protected_reason is None and merge_state in {"merged", "squash_probable"},
                    "requiresForce": protected_reason is None and merge_state != "merged",
                }
            )

        order = {
            "integration": 0,
            "feature": 1,
            "bugfix": 2,
            "release": 3,
            "hotfix": 4,
            "support": 5,
            "aido": 6,
        }
        merge_order = {"merged": 0, "squash_probable": 1, "not_merged": 2, "unknown": 3}
        branches.sort(
            key=lambda branch: (
                order.get(branch["type"], 7),
                branch["kind"] != "local",
                merge_order[branch["mergeState"]],
                not branch["stale"],
                branch["name"],
            )
        )
        return {
            "currentBranch": current_branch,
            "integrationBranch": integration,
            "integrationExists": integration_target is not None,
            "mainlineBranch": mainline,
            "remotes": remotes,
            "staleDays": stale_days,
            "farBehindCommits": far_behind_commits,
            "squashDetection": "cherry+merge-tree" if merge_tree_supported else "cherry",
            "branches": branches,
            "health": self._health(
                branches, integration_exists=integration_target is not None, remotes=remotes
            ),
        }

    @staticmethod
    def _health(
        branches: list[dict[str, Any]], *, integration_exists: bool, remotes: list[str]
    ) -> dict[str, Any]:
        def names(predicate) -> list[str]:
            return [branch["name"] for branch in branches if predicate(branch)]

        unprotected = [branch for branch in branches if not branch["protected"]]
        merged = names(lambda b: not b["protected"] and b["mergeState"] == "merged")
        squash = names(lambda b: not b["protected"] and b["mergeState"] == "squash_probable")
        non_gitflow = names(lambda b: b["kind"] == "local" and not b["protected"] and b["type"] == "other")
        stale = names(lambda b: b["stale"] and b["mergeState"] == "not_merged")
        far_behind = names(lambda b: b["farBehind"] and b["mergeState"] == "not_merged")
        based_on_main = names(lambda b: b["basedOnMain"])
        gone = names(lambda b: b["kind"] == "local" and b["upstream"]["status"] == "gone")
        merged_worktrees = names(
            lambda b: bool(b["aidoWorkspaces"]) and b["mergeState"] in {"merged", "squash_probable"}
        )
        issues: list[dict[str, Any]] = []
        if not integration_exists:
            issues.append(
                {
                    "kind": "integration_missing",
                    "severity": "warning",
                    "branches": [],
                    "suggestedAction": "configure_integration",
                }
            )
        for kind, severity, items, action in (
            ("merged_not_deleted", "warning", merged, "delete_merged"),
            ("squash_probable", "info", squash, "review_force_delete"),
            ("merged_worktree", "warning", merged_worktrees, "remove_worktree"),
            ("upstream_gone", "info", gone, "prune" if remotes else "review"),
            ("non_gitflow", "info", non_gitflow, "rename"),
            ("stale", "info", stale, "review"),
            ("far_behind", "warning", far_behind, "update_from_integration"),
            ("based_on_main", "warning", based_on_main, "recreate_from_integration"),
        ):
            if items:
                issues.append(
                    {"kind": kind, "severity": severity, "branches": items, "suggestedAction": action}
                )
        return {
            "total": len(branches),
            "local": sum(1 for branch in branches if branch["kind"] == "local"),
            "remote": sum(1 for branch in branches if branch["kind"] == "remote"),
            "protected": len(branches) - len(unprotected),
            "merged": len(merged),
            "squashProbable": len(squash),
            "stale": len(stale),
            "nonGitflow": len(non_gitflow),
            "farBehind": len(far_behind),
            "basedOnMain": len(based_on_main),
            "upstreamGone": len(gone),
            "issues": issues,
        }

    def inventory(
        self,
        project_id: str,
        *,
        stale_days: int = DEFAULT_STALE_DAYS,
        far_behind_commits: int = DEFAULT_FAR_BEHIND_COMMITS,
    ) -> dict[str, Any]:
        """Inventario gitflow del repo del proyecto (sólo lecturas brokered)."""
        state, early = self._begin(project_id, "branch_inventory", {"staleDays": stale_days})
        if state is None:
            return {**early, "integrationBranch": self.integration_branch(project_id)}
        try:
            collected = self._collect(state, stale_days=stale_days, far_behind_commits=far_behind_commits)
        except RuntimeError as error:
            return self._finish(
                state,
                {
                    "status": "failed",
                    "reason": str(error),
                    "projectId": project_id,
                    "workspaceId": state.ctx.workspace_id,
                    "root": str(state.ctx.root),
                    "integrationBranch": self.integration_branch(project_id),
                },
            )
        return self._finish(
            state,
            {
                "status": "completed",
                "reason": "Branch inventory collected through ToolBroker.",
                "projectId": project_id,
                "workspaceId": state.ctx.workspace_id,
                "root": str(state.ctx.root),
                **collected,
            },
        )

    # ------------------------------------------------------------------ mutations

    def _action_response(
        self,
        state: _RunState,
        *,
        project_id: str,
        integration: str,
        results: list[dict[str, Any]],
        event: str,
    ) -> dict[str, Any]:
        done = sum(1 for item in results if item["status"] in {"deleted", "renamed", "pruned", "removed"})
        failed = sum(1 for item in results if item["status"] in {"failed", "blocked"})
        summary = {"done": done, "skipped": len(results) - done - failed, "failed": failed}
        response = {
            "status": "completed" if not failed else "failed",
            "reason": (
                f"{done} done, {summary['skipped']} skipped, {failed} failed."
                if results
                else "Nothing was selected."
            ),
            "projectId": project_id,
            "workspaceId": state.ctx.workspace_id,
            "integrationBranch": integration,
            "results": results,
            "summary": summary,
        }
        EventBus(self.connection).record_event(
            project_id=project_id,
            event_type=event,
            payload={
                "summary": summary,
                "results": [
                    {key: item[key] for key in ("branch", "kind", "action", "status", "forced")}
                    for item in results
                ],
            },
        )
        return self._finish(state, response)

    @staticmethod
    def _result(
        branch: str,
        kind: str,
        action: str,
        status: str,
        reason: str = "",
        *,
        forced: bool = False,
        detail=None,
    ) -> dict[str, Any]:
        return {
            "branch": branch,
            "kind": kind,
            "action": action,
            "status": status,
            "reason": reason,
            "forced": forced,
            "detail": list(detail or []),
        }

    def _command_failure(self, result) -> tuple[str, str]:
        status = "blocked" if result.status == "blocked" else "failed"
        return status, (result.stderr.strip() or result.reason)[:500]

    def _delete_remote(
        self, state: _RunState, *, remote: str, branch: str, label: str, action: str
    ) -> dict[str, Any]:
        if (
            not _is_safe_remote_name(remote)
            or not _is_safe_ref(branch)
            or branch in GITFLOW_MAINLINE_BRANCHES
        ):
            return self._result(label, "remote", action, "skipped", "protected_or_unsafe")
        result = self._git(
            state, ["push", remote, "--delete", branch], git_operation="delete_remote_branch", network=True
        )
        if result.return_code == 0:
            return self._result(label, "remote", action, "deleted", f"Deleted {branch} on {remote}.")
        status, reason = self._command_failure(result)
        return self._result(label, "remote", action, status, reason)

    def delete_branches(
        self,
        project_id: str,
        *,
        branches: list[str],
        force_branches: list[str],
        delete_remote: bool = False,
        remote_branches: list[str] | None = None,
    ) -> dict[str, Any]:
        """Borra ramas seleccionadas re-validando protección y merge contra un inventario fresco."""
        remote_branches = remote_branches or []
        integration = self.integration_branch(project_id)
        state, early = self._begin(
            project_id,
            "branch_delete",
            {
                "branches": branches,
                "forceBranches": force_branches,
                "deleteRemote": delete_remote,
                "remoteBranches": remote_branches,
            },
        )
        if state is None:
            return {**early, "integrationBranch": integration, "results": []}
        inventory = self._collect(state)
        by_name = {(item["kind"], item["name"]): item for item in inventory["branches"]}
        force = set(force_branches)
        results: list[dict[str, Any]] = []
        for name in dict.fromkeys(branches):
            branch = by_name.get(("local", name))
            if branch is None:
                results.append(self._result(name, "local", "delete", "skipped", "not_found"))
                continue
            if branch["protected"]:
                results.append(
                    self._result(name, "local", "delete", "skipped", f"protected:{branch['protectedReason']}")
                )
                continue
            if branch["aidoWorkspaces"]:
                results.append(self._result(name, "local", "delete", "skipped", "aido_workspace_active"))
                continue
            forced = False
            if branch["mergeState"] == "merged":
                result = self._git(state, ["branch", "--delete", name], git_operation="delete_branch")
                if result.return_code != 0 and NOT_FULLY_MERGED_MARKER in result.stderr:
                    # `-d` mide contra HEAD/upstream; el inventario ya verificó que está en la integración.
                    result = self._git(state, ["branch", "-D", name], git_operation="delete_branch")
            elif name in force:
                forced = True
                result = self._git(state, ["branch", "-D", name], git_operation="delete_branch")
            else:
                results.append(
                    self._result(
                        name,
                        "local",
                        "delete",
                        "skipped",
                        "squash_probable_requires_force"
                        if branch["mergeState"] == "squash_probable"
                        else "not_merged_requires_force",
                    )
                )
                continue
            if result.return_code != 0:
                status, reason = self._command_failure(result)
                results.append(self._result(name, "local", "delete", status, reason, forced=forced))
                continue
            results.append(
                self._result(
                    name,
                    "local",
                    "delete",
                    "deleted",
                    f"merged into {integration}" if not forced else f"force-deleted ({branch['mergeState']})",
                    forced=forced,
                )
            )
            upstream = branch["upstream"]
            if delete_remote and upstream["status"] == "tracking" and upstream["name"]:
                remote = next(
                    (item for item in inventory["remotes"] if upstream["name"].startswith(f"{item}/")), None
                )
                if remote:
                    results.append(
                        self._delete_remote(
                            state,
                            remote=remote,
                            branch=upstream["name"][len(remote) + 1 :],
                            label=upstream["name"],
                            action="delete_remote",
                        )
                    )
        for label in dict.fromkeys(remote_branches):
            branch = by_name.get(("remote", label))
            if not delete_remote:
                results.append(
                    self._result(label, "remote", "delete_remote", "skipped", "remote_delete_not_enabled")
                )
                continue
            if branch is None or not branch["remote"]:
                results.append(self._result(label, "remote", "delete_remote", "skipped", "not_found"))
                continue
            if branch["protected"]:
                results.append(
                    self._result(
                        label, "remote", "delete_remote", "skipped", f"protected:{branch['protectedReason']}"
                    )
                )
                continue
            if branch["mergeState"] != "merged" and label not in force:
                results.append(
                    self._result(label, "remote", "delete_remote", "skipped", "not_merged_requires_force")
                )
                continue
            results.append(
                self._delete_remote(
                    state,
                    remote=branch["remote"],
                    branch=branch["shortName"],
                    label=label,
                    action="delete_remote",
                )
            )
        return self._action_response(
            state,
            project_id=project_id,
            integration=integration,
            results=results,
            event="git.branches.deleted",
        )

    def rename_branch(self, project_id: str, *, branch: str, new_name: str) -> dict[str, Any]:
        """Renombra una rama local sin upstream a un prefijo gitflow (``git branch -m``)."""
        integration = self.integration_branch(project_id)
        state, early = self._begin(project_id, "branch_rename", {"branch": branch, "newName": new_name})
        if state is None:
            return {**early, "integrationBranch": integration, "results": []}
        inventory = self._collect(state)
        by_name = {item["name"]: item for item in inventory["branches"] if item["kind"] == "local"}
        target = by_name.get(branch)
        reason = ""
        if target is None:
            reason = "not_found"
        elif target["protected"]:
            reason = f"protected:{target['protectedReason']}"
        elif target["type"] == "aido" or target["aidoWorkspaces"]:
            reason = "aido_managed_branch"
        elif target["upstream"]["status"] != "none":
            reason = "has_upstream"
        elif not _is_safe_ref(new_name) or not new_name.startswith(GITFLOW_RENAME_PREFIXES):
            reason = "invalid_gitflow_name"
        elif new_name in by_name:
            reason = "target_exists"
        if reason:
            results = [self._result(branch, "local", "rename", "skipped", reason)]
        else:
            result = self._git(state, ["branch", "-m", branch, new_name], git_operation="rename_branch")
            if result.return_code == 0:
                results = [self._result(branch, "local", "rename", "renamed", f"Renamed to {new_name}.")]
            else:
                status, failure = self._command_failure(result)
                results = [self._result(branch, "local", "rename", status, failure)]
        return self._action_response(
            state,
            project_id=project_id,
            integration=integration,
            results=results,
            event="git.branches.renamed",
        )

    def prune_remotes(self, project_id: str, *, remote: str | None = None) -> dict[str, Any]:
        """Elimina refs remote-tracking obsoletas con ``git fetch --prune <remote>``."""
        integration = self.integration_branch(project_id)
        state, early = self._begin(project_id, "branch_prune", {"remote": remote})
        if state is None:
            return {**early, "integrationBranch": integration, "results": []}
        listed = self._git(state, ["remote", "-v"])
        remotes = (
            sorted({item["name"] for item in _parse_remotes(listed.stdout)})
            if listed.return_code == 0
            else []
        )
        targets = [remote] if remote else remotes
        results: list[dict[str, Any]] = []
        if not remotes:
            results.append(self._result(remote or "", "remote_refs", "prune", "skipped", "no_remote"))
        for name in targets:
            if name not in remotes:
                results.append(self._result(name, "remote_refs", "prune", "skipped", "unknown_remote"))
                continue
            result = self._git(
                state, ["fetch", "--prune", name], git_operation="prune_remote_refs", network=True
            )
            if result.return_code == 0:
                pruned = re.findall(r"\[deleted\].*->\s*(\S+)", f"{result.stdout}\n{result.stderr}")
                results.append(
                    self._result(
                        name,
                        "remote_refs",
                        "prune",
                        "pruned",
                        f"{len(pruned)} stale remote-tracking ref(s) pruned.",
                        detail=pruned,
                    )
                )
            else:
                status, reason = self._command_failure(result)
                results.append(self._result(name, "remote_refs", "prune", status, reason))
        return self._action_response(
            state,
            project_id=project_id,
            integration=integration,
            results=results,
            event="git.branches.pruned",
        )

    def remove_merged_worktrees(
        self, project_id: str, *, workspace_ids: list[str], root: Path
    ) -> dict[str, Any]:
        """Retira workspaces AIDO cuya rama ya está integrada (archiva y remueve el worktree).

        Reusa ``WorkspacesRepository.archive_workspace`` (el mismo primitivo de la limpieza de
        workspaces), que remueve el worktree por ToolBroker sin borrar la rama; borrarla después es
        una acción separada del gestor.
        """
        from local_control_center.workspaces_projects.repository import WorkspacesRepository

        integration = self.integration_branch(project_id)
        state, early = self._begin(project_id, "branch_worktree_remove", {"workspaceIds": workspace_ids})
        if state is None:
            return {**early, "integrationBranch": integration, "results": []}
        inventory = self._collect(state)
        owners: dict[str, dict[str, Any]] = {}
        for branch in inventory["branches"]:
            for workspace in branch["aidoWorkspaces"]:
                owners[workspace["workspaceId"]] = branch
        repository = WorkspacesRepository(self.connection, root=root)
        results: list[dict[str, Any]] = []
        for workspace_id in dict.fromkeys(workspace_ids):
            branch = owners.get(workspace_id)
            if branch is None:
                results.append(
                    self._result(workspace_id, "worktree", "remove_worktree", "skipped", "not_found")
                )
                continue
            if branch["mergeState"] not in {"merged", "squash_probable"}:
                results.append(
                    self._result(
                        branch["name"], "worktree", "remove_worktree", "skipped", "branch_not_merged"
                    )
                )
                continue
            archived = repository.archive_workspace(
                workspace_id,
                reason=f"Branch {branch['name']} is integrated into {integration}; removed from the branch manager.",
                delete_branch=False,
            )
            cleanup = (archived.get("metadata") or {}).get("gitWorktreeCleanup") or {}
            state.traces.extend(cleanup.get("toolCalls") or [])
            if cleanup.get("status") == "removed":
                results.append(
                    self._result(branch["name"], "worktree", "remove_worktree", "removed", workspace_id)
                )
            else:
                results.append(
                    self._result(
                        branch["name"],
                        "worktree",
                        "remove_worktree",
                        "failed",
                        str(cleanup.get("stderr") or cleanup.get("status") or "cleanup_failed"),
                    )
                )
        return self._action_response(
            state,
            project_id=project_id,
            integration=integration,
            results=results,
            event="git.branches.worktrees_removed",
        )
