"""Slice del product loop: máquina de estados durable que coordina idea → discovery → backlog → entrega.

Reúne el repositorio SQLite que persiste cada loop y su bitácora de transiciones, y el
``ProductLoopCoordinator`` que valida los cambios de estado. El estado vive en la base (no solo en
memoria), de modo que el loop puede reanudarse tras reiniciar AIDO. No exporta símbolos: cada
consumidor importa de los submódulos `coordinator` o `repository`.
"""

__all__: list[str] = []
