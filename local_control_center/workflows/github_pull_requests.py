"""GitHub pull-request creation: resolve remote config and POST a PR via the REST API.

Parses ``AIDO_GITHUB_REMOTE`` (owner/repo, HTTPS or SSH form) and the API token from the
environment, then opens a pull request over urllib with structured, secret-redacted audit
output. Failures never raise to the caller: every error path returns a ``status: failed``
dict so the workflow can record the attempt instead of crashing.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from local_control_center.shared.redaction import redact_secrets

GITHUB_API_VERSION = "2026-03-10"
GITHUB_USER_AGENT = "AIDO-Local-Control-Center"


@dataclass(frozen=True)
class GitHubPullRequestConfig:
    """Resolved GitHub target and credentials for PR creation (owner, repo, token, API base)."""

    token: str
    remote: str
    owner: str
    repo: str
    api_base_url: str

    @property
    def repository(self) -> str:
        """The ``owner/repo`` slug used for logging and the PR endpoint path."""
        return f"{self.owner}/{self.repo}"


class GitHubPullRequestConfigError(ValueError):
    """Raised when the GitHub remote/token configuration is missing or unparseable.

    ``missing`` lists the environment variable names that were absent, so the caller can
    surface a precise "not configured" reason instead of a generic error.
    """

    def __init__(self, reason: str, *, missing: list[str] | None = None):
        self.reason = reason
        self.missing = missing or []
        super().__init__(reason)


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _owner_repo_from_path(path: str) -> tuple[str, str]:
    clean = path.strip("/")
    if clean.startswith("repos/"):
        clean = clean.removeprefix("repos/")
    parts = [part for part in clean.split("/") if part]
    if len(parts) < 2:
        raise GitHubPullRequestConfigError(
            "AIDO_GITHUB_REMOTE must identify a GitHub repository as owner/repo or a repository URL."
        )
    owner = parts[0].strip()
    repo = _strip_git_suffix(parts[1].strip())
    if not owner or not repo:
        raise GitHubPullRequestConfigError(
            "AIDO_GITHUB_REMOTE must identify a GitHub repository as owner/repo or a repository URL."
        )
    return owner, repo


def parse_github_remote(remote: str) -> tuple[str, str, str]:
    """Resolve a remote string to ``(owner, repo, api_base_url)``.

    Accepts ``owner/repo``, an HTTPS URL, or an SSH git remote; the API base defaults to
    ``api.github.com`` for github.com and is derived from the host for GitHub Enterprise.

    Raises:
        GitHubPullRequestConfigError: if the remote is empty or not a recognizable repository.
    """
    clean = remote.strip()
    if not clean:
        raise GitHubPullRequestConfigError("AIDO_GITHUB_REMOTE is required.", missing=["AIDO_GITHUB_REMOTE"])

    owner_repo_match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", clean)
    if owner_repo_match:
        return (
            owner_repo_match.group(1),
            _strip_git_suffix(owner_repo_match.group(2)),
            "https://api.github.com",
        )

    ssh_match = re.fullmatch(r"(?:[^@]+@)?([^:]+):/?(.+)", clean)
    if ssh_match and "://" not in clean:
        host = ssh_match.group(1).lower()
        owner, repo = _owner_repo_from_path(ssh_match.group(2))
        api_base = "https://api.github.com" if host == "github.com" else f"https://{host}"
        return owner, repo, api_base.rstrip("/")

    parsed = urllib.parse.urlparse(clean)
    if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.netloc:
        raise GitHubPullRequestConfigError(
            "AIDO_GITHUB_REMOTE must be owner/repo, a GitHub HTTPS URL, or an SSH git remote."
        )
    owner, repo = _owner_repo_from_path(parsed.path)
    host = (parsed.hostname or "").lower()
    if host == "github.com":
        api_base = "https://api.github.com"
    elif host == "api.github.com":
        api_base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    else:
        api_base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return owner, repo, api_base


def github_pull_request_config_from_env(
    env: Mapping[str, str] | None = None,
) -> GitHubPullRequestConfig:
    """Build a config from ``AIDO_GITHUB_TOKEN``/``AIDO_GITHUB_REMOTE`` (defaults to ``os.environ``).

    Raises:
        GitHubPullRequestConfigError: with the missing variable names if either is absent,
            or if the remote cannot be parsed.
    """
    values = env or os.environ
    token = str(values.get("AIDO_GITHUB_TOKEN") or "").strip()
    remote = str(values.get("AIDO_GITHUB_REMOTE") or "").strip()
    missing = [
        name for name, value in (("AIDO_GITHUB_TOKEN", token), ("AIDO_GITHUB_REMOTE", remote)) if not value
    ]
    if missing:
        raise GitHubPullRequestConfigError(
            "GitHub PR creation is not configured; missing " + ", ".join(missing) + ".",
            missing=missing,
        )
    owner, repo, api_base_url = parse_github_remote(remote)
    return GitHubPullRequestConfig(
        token=token,
        remote=remote,
        owner=owner,
        repo=repo,
        api_base_url=api_base_url,
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": GITHUB_USER_AGENT,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized = dict(headers)
    if "Authorization" in sanitized:
        sanitized["Authorization"] = "Bearer [REDACTED]"
    return sanitized


def _decode_json_response(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": raw.decode("utf-8", errors="replace")}


def create_github_pull_request(
    config: GitHubPullRequestConfig,
    *,
    title: str,
    head: str,
    base: str,
    body: str,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """POST a pull request to GitHub and return a structured result with redacted audit data.

    Never raises for HTTP, network or timeout failures: those return a ``status: failed`` dict
    (with ``httpStatus`` and the sanitized request/response) so the workflow records the attempt.
    On success returns ``status: created`` with the new PR number, id and URLs.
    """
    path = (
        "/repos/"
        + urllib.parse.quote(config.owner, safe="")
        + "/"
        + urllib.parse.quote(config.repo, safe="")
        + "/pulls"
    )
    url = config.api_base_url.rstrip("/") + path
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    headers = _headers(config.token)
    request_audit = {
        "method": "POST",
        "url": url,
        "headers": _sanitize_headers(headers),
        "body": redact_secrets(payload),
        "repository": config.repository,
        "remote": config.remote,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = _decode_json_response(response.read())
            http_status = int(response.status)
    except urllib.error.HTTPError as error:
        response_body = _decode_json_response(error.read())
        return {
            "status": "failed",
            "reason": f"GitHub create pull request returned HTTP {error.code}.",
            "httpStatus": int(error.code),
            "request": request_audit,
            "response": redact_secrets(response_body),
        }
    except urllib.error.URLError as error:
        return {
            "status": "failed",
            "reason": f"GitHub create pull request request failed: {error.reason}",
            "httpStatus": None,
            "request": request_audit,
            "response": {},
        }
    except TimeoutError as error:
        return {
            "status": "failed",
            "reason": f"GitHub create pull request timed out: {error}",
            "httpStatus": None,
            "request": request_audit,
            "response": {},
        }

    if http_status != 201:
        return {
            "status": "failed",
            "reason": f"GitHub create pull request returned HTTP {http_status}.",
            "httpStatus": http_status,
            "request": request_audit,
            "response": redact_secrets(response_body),
        }
    if not isinstance(response_body, dict):
        return {
            "status": "failed",
            "reason": "GitHub create pull request response was not a JSON object.",
            "httpStatus": http_status,
            "request": request_audit,
            "response": redact_secrets(response_body),
        }
    return {
        "status": "created",
        "reason": "GitHub pull request created.",
        "httpStatus": http_status,
        "number": response_body.get("number"),
        "id": response_body.get("id"),
        "url": response_body.get("url"),
        "htmlUrl": response_body.get("html_url"),
        "repository": config.repository,
        "head": head,
        "base": base,
        "request": request_audit,
        "response": redact_secrets(response_body),
    }
