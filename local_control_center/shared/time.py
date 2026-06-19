"""Reloj UTC canónico para timestamps persistidos en toda la plataforma.

Centraliza el formato de fecha-hora (ISO-8601 en UTC, milisegundos, sufijo ``Z``)
para que columnas y eventos sean comparables como texto y ordenables lexicográficamente.
Todo el backend debe obtener sus timestamps de aquí en vez de llamar a ``datetime`` directo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> str:
    """Devuelve el instante actual en UTC con formato ISO-8601 y sufijo ``Z``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_millis(ms: int) -> str:
    """Devuelve el timestamp UTC resultante de sumar ``ms`` milisegundos a ahora."""
    return (
        (datetime.now(UTC) + timedelta(milliseconds=ms))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
