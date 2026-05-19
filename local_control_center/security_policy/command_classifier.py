from __future__ import annotations

import re
from typing import Any


CRITICAL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("destructive_delete", re.compile(r"\b(rm\s+-rf|Remove-Item\b.*(?<!\S)-Recurse\b.*(?<!\S)-Force\b)", re.I)),
    ("force_push", re.compile(r"\bgit\s+push\b.*\b--force\b", re.I)),
    ("prod_deploy", re.compile(r"\b(deploy|release)\b.*\b(prod|production)\b", re.I)),
    ("privilege_escalation", re.compile(r"\b(sudo|Start-Process\b.*\b-Verb\s+RunAs|runas)\b", re.I)),
    ("secrets_write", re.compile(r"\b(\.env|secret|credential|token)\b.*\b(write|set|update|remove|delete)\b", re.I)),
]

MEDIUM_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("install", re.compile(r"\b(pnpm|npm|uv|pip|winget|choco)\s+(install|add)\b", re.I)),
    ("git_write", re.compile(r"\bgit\s+(commit|merge|rebase|push|checkout|branch)\b", re.I)),
    ("network", re.compile(r"\b(curl|Invoke-WebRequest|wget|ssh|scp)\b", re.I)),
]

LOW_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("test", re.compile(r"\b(pytest|playwright\s+test|vitest|npm\s+test|pnpm\s+test)\b", re.I)),
    ("build", re.compile(r"\b(pnpm\s+run\s+build|npm\s+run\s+build|uv\s+run)\b", re.I)),
    ("read_only", re.compile(r"\b(rg|Get-Content|git\s+status|git\s+diff|ls|dir)\b", re.I)),
]


def classify_command(command: str | None) -> dict[str, Any]:
    text = command or ""
    categories: list[str] = []
    for name, pattern in CRITICAL_RULES:
        if pattern.search(text):
            categories.append(name)
    if categories:
        return {"riskLevel": "critical", "categories": categories}
    for name, pattern in MEDIUM_RULES:
        if pattern.search(text):
            categories.append(name)
    if categories:
        return {"riskLevel": "medium", "categories": categories}
    for name, pattern in LOW_RULES:
        if pattern.search(text):
            categories.append(name)
    if categories:
        return {"riskLevel": "low", "categories": categories}
    return {"riskLevel": "medium" if text.strip() else "low", "categories": ["unknown"] if text.strip() else ["none"]}
