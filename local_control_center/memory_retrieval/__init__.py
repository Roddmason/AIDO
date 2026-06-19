"""Marca el paquete del slice de memoria y retrieval del control center.

Agrupa el CRUD de memory items, sus embeddings y el índice vectorial por
proyecto. No reexporta símbolos: cada módulo (api/commands/index/repository)
se importa por su ruta para mantener explícitas las dependencias del slice.
"""

__all__: list[str] = []
