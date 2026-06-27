"""Slice de backlog: del valor de usuario al trabajo de los agentes, trazable y versionado.

Modela el backlog como entidades de primera clase y project-scoped: epics que agrupan user
stories, criterios de aceptación por historia, y dos grafos de dependencias (entre historias y
entre tareas). La user story representa **valor para el usuario** y es agnóstica al rol: no se
duplica una HU por cada disciplina. El trabajo técnico se modela aparte como agent_tasks (cada
una con su rol) y se reparte con agent_assignments (qué agente ejecuta cada tarea). Cada concepto
vive en su propia tabla (nunca embebido en metadata) y se enlaza por referencias explícitas. No
exporta símbolos: cada consumidor importa de los submódulos `repository` o (a futuro) `models`/`api`.

@author Rodrigo Mason
"""

__all__: list[str] = []
