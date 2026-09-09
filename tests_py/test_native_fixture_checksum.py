"""The native diagnostic fixtures must carry a verifiable PE image checksum."""

import struct
import sys

import pytest

from tests_py.test_launcher_capture_http import offline_native_cli as offline_native_cli
from tests_py.test_native_diagnostics import native_csharp_target as native_csharp_target
from tests_py.test_native_diagnostics import native_failfast_targets as native_failfast_targets

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Native Windows fixture toolchain")


@pytest.mark.parametrize("target_kind", ["http", "fastfail", "pressure", "managed"])
def test_native_fixture_has_valid_image_checksum(
    offline_native_cli, native_failfast_targets, native_csharp_target, target_kind
):
    import ctypes
    from ctypes import wintypes

    target = (
        offline_native_cli if target_kind == "http" else native_failfast_targets[target_kind == "pressure"]
    )
    if target_kind == "managed":
        target = native_csharp_target
    image = target.read_bytes()
    pe = struct.unpack_from("<I", image, 60)[0]
    assert image[pe : pe + 4] == b"PE\0\0"
    stored = struct.unpack_from("<I", image, pe + 24 + 64)[0]
    assert stored != 0, "Synthetic fixture lacks the PE checksum required for CDB verification"
    mapped = ctypes.create_string_buffer(image)
    original, computed = wintypes.DWORD(), wintypes.DWORD()
    checksum = ctypes.WinDLL("imagehlp", use_last_error=True).CheckSumMappedFile
    checksum.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    checksum.restype = ctypes.c_void_p
    assert checksum(mapped, len(image), ctypes.byref(original), ctypes.byref(computed))
    assert original.value == computed.value == stored
