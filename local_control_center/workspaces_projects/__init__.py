"""Slice de workspaces aislados por tarea/proyecto y su archivado con evidencia.

Agrupa el router HTTP, el repositorio transaccional, los modelos de contrato y los
helpers de aislamiento (copia de fuente o git worktree) y de snapshot/diff. No expone
símbolos a nivel de paquete: cada consumidor importa desde el submódulo concreto.
"""

__all__ = []
