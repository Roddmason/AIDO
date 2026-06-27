"""Slice de product discovery: del problema a un brief de producto trazable y versionado.

Modela el descubrimiento de producto como entidades de primera clase y project-scoped:
iniciativas, sesiones de descubrimiento y sus mensajes, preguntas y respuestas de aclaración,
briefs de producto con su historial de versiones, supuestos y decisiones de producto. Cada
concepto vive en su propia tabla (nunca embebido en metadata) y se enlaza por referencias
explícitas para mantener la trazabilidad. No exporta símbolos: cada consumidor importa de los
submódulos `repository` o (a futuro) `models`/`api`.

@author Rodrigo Mason
"""

__all__: list[str] = []
