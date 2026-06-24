"""Slice del TeamScheduler: compone el equipo mínimo para una tarea y resuelve su ejecución por rol.

Dado el alcance (scope) y el riesgo de una tarea más un modo de operación
(economy/balanced/critical/maximum), selecciona solo los roles que la tarea realmente necesita y
resuelve, por rol, su provider-kind, model tier, runtime, skills, tools, presupuesto, reviewer y
quality gates. Es lógica pura y determinista (sin base de datos ni I/O): la misma entrada produce el
mismo plan, de modo que el plan se puede cachear, registrar y reproducir. Emite *tier tokens* (no ids
de modelo concretos) para que el model gateway resuelva el modelo vigente sin que este módulo se
quede obsoleto. No exporta símbolos: los consumidores importan de ``scheduler``.
"""

__all__: list[str] = []
