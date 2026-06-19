"""Raiz del paquete del Local Control Center backend; publica la version del producto.

Marca el directorio como paquete Python y expone `__version__` como unica fuente
de verdad de la version, consumida por el ensamblado de la app FastAPI y el empaquetado.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
