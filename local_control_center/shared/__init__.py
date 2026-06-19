"""Utilidades transversales del backend compartidas por todos los slices.

Agrupa helpers sin estado de dominio (DB, serialización, redacción, tiempo,
event bus, telemetría, esquema y settings) que el resto de paquetes importa.
No expone API propia: cada utilidad se importa desde su módulo concreto.
"""

__all__: list[str] = []
