"""Reloj UTC canónico para timestamps persistidos en toda la plataforma.

Centraliza el formato de fecha-hora (ISO-8601 en UTC, milisegundos, sufijo ``Z``)
para que columnas y eventos sean comparables como texto y ordenables lexicográficamente.
Todo el backend debe obtener sus timestamps de aquí en vez de llamar a ``datetime`` directo.

@author Rodrigo Mason
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


def iso_after_seconds(base_iso: str, seconds: float) -> str:
    """Devuelve ``base_iso`` desplazado ``seconds`` segundos, en el mismo formato ISO-8601 (ms, ``Z``).

    Conserva el formato canónico (milisegundos, sufijo ``Z``) para que el resultado sea comparable
    lexicográficamente con ``utc_now()``; así un deadline calculado y un instante actual se ordenan
    como texto sin parsear. Acepta una base con sufijo ``Z`` o ``+00:00``.
    """
    base = datetime.fromisoformat(base_iso.replace("Z", "+00:00"))
    return (base + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
