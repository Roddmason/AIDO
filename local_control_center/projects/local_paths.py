"""Selector nativo de directorios del workspace, brokeado por el proceso de escritorio local.

Existe porque los pickers del navegador no exponen rutas absolutas del SO; este módulo
abre un diálogo Tk del runtime local para obtenerlas. Usa solo la stdlib y degrada con
gracia (estado ``unavailable``) cuando Tk o la sesión de escritorio no están disponibles.
"""

from __future__ import annotations

from pathlib import Path


def select_directory_with_native_dialog(
    *, title: str, initial_path: str | None = None
) -> dict[str, str | None]:
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
        return {
            "status": "unavailable",
            "selectedPath": None,
            "reason": f"directory picker unavailable: {error}",
        }

    if not selected:
        return {"status": "cancelled", "selectedPath": None, "reason": None}
    return {"status": "selected", "selectedPath": str(Path(selected)), "reason": None}
