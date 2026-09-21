"""Load an explicitly enabled Jev credential once, outside the inference deadline.

Only the Windows vault entry AIDO/Jev is read. No credential or provider permissions
are written; the dedicated guard never enables other runtime providers.
@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import resolve_config


def load_jev_startup_credential(db_path: Path) -> bool:
    """Load the existing vault entry only for authorized runtime selection, never QA."""
    if any(
        os.environ.get(key)
        for key in ("AIDO_QUALITY_INVOCATION_ID", "AIDO_QUALITY_DB_PATH", "PYTEST_CURRENT_TEST")
    ):
        return False
    guard = os.environ.get("AIDO_ENABLE_JEV_CALLS", "").lower()
    if guard == "false":
        return False
    if guard != "true":
        if not db_path.is_file():
            return False
        try:
            with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                config = resolve_config(connection, None)
                if not config.selects_runtime or not config.jev_enabled or config.provider != "jev":
                    return False
        except (sqlite3.Error, ValueError):
            return False
    if not os.environ.get("TYPESAFE_API_KEY"):
        if os.name != "nt":
            return False
        try:
            from keyring.backends.Windows import WinVaultKeyring

            credential = WinVaultKeyring().get_password("AIDO", "Jev")
        except Exception:
            return False
        if not isinstance(credential, str) or not 1 <= len(credential) <= 4096:
            return False
        if any(not 33 <= ord(character) <= 126 for character in credential):
            return False
        os.environ["TYPESAFE_API_KEY"] = credential
    os.environ["AIDO_ENABLE_JEV_CALLS"] = "true"
    return True
