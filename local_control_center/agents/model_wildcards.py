"""Comodines de modelo compartidos por el ranking preferred de ambos motores de routing.

Un valor comodín en la posición ``model`` de una preferencia de rol significa "cualquier
modelo de ese provider": el wizard y ``scripts/setup_omniroute.py`` escriben ``*`` para
gateways auto-ruteados, y las migraciones siembran ``auto_best_available`` para varios
roles. Los dos motores (``ModelRouter`` legacy y ``AIResourceManager``) deben reconocer
el mismo conjunto o el rank preferred se degrada en silencio según el camino que tome
la selección.

@author Rodrigo Mason
"""

from __future__ import annotations

MODEL_WILDCARDS: frozenset[str] = frozenset({"", "*", "auto", "auto_best_available"})


def is_nvidia_nim_auto_selection_sentinel(provider_family: str, model: str) -> bool:
    """Return whether an NVIDIA NIM candidate still needs an explicit model selection."""
    return provider_family == "nvidia_nim" and model == "auto_best_available"
