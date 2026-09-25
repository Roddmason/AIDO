"""Verifica que la función capture_workspace_snapshot contabiliza correctamente el tamaño total de los archivos.

Se comprueba que el total incluye todos los archivos no ignorados, que se truncan los detalles a 200 y que el estado
es "missing" cuando el directorio no existe.

@author Rodrigo Mason
"""

from __future__ import annotations

# import os
from pathlib import Path

from local_control_center.workspaces_projects.cleanup import capture_workspace_snapshot


def test_total_size_bytes_with_known_files(tmp_path: Path) -> None:
    """Un workspace con 3 archivos de tamaños 10, 20 y 30 bytes debe reportar totalSizeBytes == 60."""
    (tmp_path / "a.txt").write_bytes(b"x" * 10)
    (tmp_path / "b.txt").write_bytes(b"x" * 20)
    (tmp_path / "c.txt").write_bytes(b"x" * 30)
    result = capture_workspace_snapshot(tmp_path)
    assert result["totalSizeBytes"] == 60


def test_total_size_bytes_with_more_than_max_files(tmp_path: Path) -> None:
    """Con 250 archivos de 1 byte, totalSizeBytes debe ser 250, pero solo 200 se listan y truncated es True."""
    for i in range(250):
        (tmp_path / f"file{i}.txt").write_bytes(b"x")
    result = capture_workspace_snapshot(tmp_path)
    assert result["totalSizeBytes"] == 250
    assert len(result["files"]) == 200
    assert result["truncated"] is True


def test_missing_directory(tmp_path: Path) -> None:
    """Si el directorio no existe, status debe ser "missing" y totalSizeBytes 0."""
    missing = tmp_path / "nonexistent"
    result = capture_workspace_snapshot(missing)
    assert result["status"] == "missing"
    assert result["totalSizeBytes"] == 0


def test_ignored_directories(tmp_path: Path) -> None:
    """Archivos dentro de node_modules deben ser ignorados en totalSizeBytes."""
    (tmp_path / "node_modules").mkdir(parents=True, exist_ok=True)
    (tmp_path / "node_modules" / "basura.bin").write_bytes(b"x" * 5000)
    (tmp_path / "real.txt").write_bytes(b"x" * 10)
    result = capture_workspace_snapshot(tmp_path)
    assert result["totalSizeBytes"] == 10
