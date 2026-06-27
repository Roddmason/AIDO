"""Slice de sesiones y chats: agrupa proyectos en sesiones de trabajo y sus conversaciones.

Reúne el router HTTP, los esquemas de request/response y el repositorio SQLite que persisten
las sesiones (contenedores por proyecto/equipo) y los chats (prompts con su estado). No exporta
símbolos: cada consumidor importa explícitamente de los submódulos `api`, `models` o `repository`.

@author Rodrigo Mason
"""

__all__: list[str] = []
