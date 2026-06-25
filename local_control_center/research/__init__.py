"""Slice de research: política de fuentes para conclusiones técnicas basadas en investigación web.

Modela la jerarquía de confianza de fuentes (documentación oficial > repos/releases oficiales >
estándares/RFCs > investigación primaria > fuentes secundarias reputables), construye el registro de
procedencia de cada fuente (URL, publisher, timestamp de fetch, hash del contenido, nivel de confianza y
artefacto relacionado), exige que toda conclusión técnica basada en web cite fuentes, y detecta fuentes
en conflicto produciendo un hallazgo explícito y una recomendación (resuelta por la fuente de mayor
confianza, o marcada para revisión manual si las de máxima confianza discrepan). ``source_policy`` es
lógica pura y determinista; ``source_log`` persiste cada fuente como artefacto de evidencia (sin tabla
nueva). No exporta símbolos: los consumidores importan de los submódulos.
"""

__all__: list[str] = []
