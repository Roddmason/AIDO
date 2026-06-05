from __future__ import annotations

import re
from typing import Any


SECRET_KEY_PATTERN = re.compile(r"(api[_-]?key|authorization|credential|secret|token)", re.I)
SECRET_VALUE_PATTERN = re.compile(
    r"("
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._-]+|"
    r"ghp_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"password\s*=\s*[^&\s]+"
    r")",
    re.I,
)


def redact_secrets(value: Any, *, key: str = "") -> Any:
    if key.lower() in {"token_status", "tokenstatus", "cost_status", "coststatus"}:
        return value
    if key.lower().endswith(("tokens", "_tokens", "token_count")) and isinstance(value, int | float):
        return value
    if SECRET_KEY_PATTERN.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {item_key: redact_secrets(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_PATTERN.sub("[redacted]", value)
    return value
