"""Punto de entrada del paquete de proveedores: reexporta el contrato comun.

Expone los DTO y la clase base de `base.py` para que el resto del sistema importe el
contrato de proveedor desde un solo lugar, sin acoplarse al modulo concreto. Las
implementaciones por proveedor se importan directamente desde sus propios modulos.
"""

from .base import (
    CostEstimate,
    ModelInfo,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderHealth,
    UsageRecord,
)

__all__ = [
    "CostEstimate",
    "ModelInfo",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ProviderHealth",
    "UsageRecord",
]
