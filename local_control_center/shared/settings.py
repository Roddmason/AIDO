from __future__ import annotations

import os
from pathlib import Path


def default_db_path() -> Path:
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_DB")
    if explicit:
        return Path(explicit)
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home()))
    return home / ".claude" / "local-control-center" / "platform.sqlite"


def default_cwd() -> Path:
    explicit = os.environ.get("LOCAL_CONTROL_CENTER_CWD")
    return Path(explicit) if explicit else Path.cwd()
