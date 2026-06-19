"""Slice de jobs y aprobaciones granulares: cola de trabajo, ejecución y gating de riesgo.

Agrupa el modelo de datos, repositorio transaccional, comandos HTTP y worker concurrente
que orquestan el ciclo de vida de un job (queued -> running -> completed/failed) y las
action requests que exigen aprobación humana antes de ejecutar acciones sensibles.
No re-exporta símbolos: cada consumidor importa desde el submódulo concreto.
"""

__all__ = []
