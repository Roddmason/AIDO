"""Bootstrap POSIX de límites en el hijo, sin preexec_fn en el worker multihilo.

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
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    if priority:
        os.nice(10)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
