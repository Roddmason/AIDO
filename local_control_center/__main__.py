"""Punto de entrada ejecutable del paquete (`python -m local_control_center`).

Delega de inmediato en `cli.main`, que parsea argumentos y arranca el dashboard
y/o el worker. Mantiene la invocacion como modulo equivalente al script de consola.
"""

from .cli import main

main()
