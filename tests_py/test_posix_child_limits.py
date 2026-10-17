"""Contrato del bootstrap POSIX de memoria: acota memoria escrita, no espacio de direcciones reservado.

@author Rodrigo Mason
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="RLIMIT_DATA counts private writable mappings on Linux >= 4.7"
)

POSIX_CHILD = (
    Path(__file__).resolve().parents[1] / "local_control_center" / "process_supervision" / "posix_child.py"
)
GIB = 1024**3


def _run_bounded(limit_bytes: int, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(POSIX_CHILD), str(limit_bytes), "0", sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_bootstrap_bounds_private_data_and_leaves_address_space_to_the_parent():
    import resource

    parent_as = resource.getrlimit(resource.RLIMIT_AS)[0]
    result = _run_bounded(
        4 * GIB,
        "import resource; print(resource.getrlimit(resource.RLIMIT_DATA)[0], "
        "resource.getrlimit(resource.RLIMIT_AS)[0])",
    )
    assert result.returncode == 0, result.stderr
    data_limit, as_limit = (int(value) for value in result.stdout.split())
    assert data_limit == 4 * GIB
    assert as_limit == parent_as


def test_large_virtual_reservations_survive_the_memory_bound():
    # Go (gitleaks), Rust y la JVM reservan espacio virtual que no escriben: no debe contar.
    # prot=0 es PROT_NONE (el módulo mmap no exporta la constante).
    result = _run_bounded(
        GIB,
        "import mmap; reserved = mmap.mmap(-1, 8 * 1024**3, prot=0); print(len(reserved))",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(8 * GIB)


def test_writing_beyond_the_memory_bound_still_fails():
    result = _run_bounded(512 * 1024**2, "data = bytearray(2 * 1024**3); print(len(data))")
    assert result.returncode != 0
    assert "MemoryError" in result.stderr
