"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from pathlib import Path


def select_directory_with_native_dialog(*, title: str, initial_path: str | None = None) -> dict[str, str | None]:
    """Open a native directory picker when the local desktop runtime supports it.

    This intentionally uses only Python stdlib. Browser directory pickers cannot
    expose absolute OS paths, so the native process must broker this interaction.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as error:  # pragma: no cover - depends on host Python build.
        return {"status": "unavailable", "selectedPath": None, "reason": f"tkinter unavailable: {error}"}

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title=title,
            initialdir=str(Path(initial_path).expanduser()) if initial_path else None,
        )
        root.destroy()
    except Exception as error:  # pragma: no cover - depends on desktop session.
        return {"status": "unavailable", "selectedPath": None, "reason": f"directory picker unavailable: {error}"}

    if not selected:
        return {"status": "cancelled", "selectedPath": None, "reason": None}
    return {"status": "selected", "selectedPath": str(Path(selected)), "reason": None}

