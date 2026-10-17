"""Bootstrap POSIX de límites en el hijo, sin preexec_fn en el worker multihilo.

La memoria se acota con ``RLIMIT_DATA`` (memoria privada escribible) y no con ``RLIMIT_AS``: los
runtimes Go, Rust o JVM reservan espacio de direcciones que nunca escriben, y bajo ``RLIMIT_AS`` de
4 GiB gitleaks (Go + wazero) aborta con "failed to reserve memory". Es además el equivalente del
``JobMemoryLimit`` de Windows, que limita memoria comprometida y no reservada.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Aplica memoria y prioridad antes de reemplazar el hijo por el ejecutable final."""
    import resource

    memory = int(sys.argv[1])
    priority = int(sys.argv[2])
    command = sys.argv[3:]
    resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))
    if priority:
        os.nice(10)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
