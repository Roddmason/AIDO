"""Facade canonica del factory de la aplicacion: reexporta `create_app` desde `api`.

Da a los consumidores (cli, tests, despliegue) un punto de import estable
(`local_control_center.app:create_app`) desacoplado del modulo de ensamblado real.
"""

from .api import create_app

__all__ = ["create_app"]
