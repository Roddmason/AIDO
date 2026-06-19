"""Punto de entrada del paquete de adaptadores de runtimes CLI de agentes.

Reexporta el contrato base (CliRuntime) y los DTOs de request/resultado/detección/salud
para que el resto del backend importe el slice sin acoplarse a la ruta de cada adaptador.
"""

from .base import CliRuntime, RuntimeDetection, RuntimeHealth, RuntimeRequest, RuntimeResult

__all__ = ["CliRuntime", "RuntimeDetection", "RuntimeHealth", "RuntimeRequest", "RuntimeResult"]
