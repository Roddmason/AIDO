"""Dependency-neutral PNG/JPEG header validation shared by artifact write and download paths."""

from __future__ import annotations

from pathlib import Path

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_192
MAX_IMAGE_PIXELS = 32_000_000
JPEG_START_OF_FRAME_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


class ImageValidationError(ValueError):
    """Stable invalid-media result without provider or repository dependencies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    offset = 2
    while offset < len(content):
        if content[offset] != 0xFF:
            raise ImageValidationError("image_media_invalid")
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            break
        marker = content[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            break
        segment_length = int.from_bytes(content[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            raise ImageValidationError("image_media_invalid")
        if marker in JPEG_START_OF_FRAME_MARKERS:
            if segment_length < 7:
                raise ImageValidationError("image_media_invalid")
            height = int.from_bytes(content[offset + 3 : offset + 5], "big")
            width = int.from_bytes(content[offset + 5 : offset + 7], "big")
            return width, height
        if marker == 0xDA:
            break
        offset += segment_length
    raise ImageValidationError("image_media_invalid")


def inspect_image(content: bytes) -> tuple[str, str, int, int]:
    """Validate byte size, signature, header dimensions and pixel count."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ImageValidationError("image_size_invalid")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(content) < 24 or content[12:16] != b"IHDR":
            raise ImageValidationError("image_media_invalid")
        width = int.from_bytes(content[16:20], "big")
        height = int.from_bytes(content[20:24], "big")
        mime_type, suffix = "image/png", ".png"
    elif content.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(content)
        mime_type, suffix = "image/jpeg", ".jpg"
    else:
        raise ImageValidationError("image_media_invalid")
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ImageValidationError("image_dimensions_invalid")
    return mime_type, suffix, width, height


def read_validated_image(path: Path) -> tuple[bytes, str, str, int, int]:
    """Read at most the configured cap plus one byte, then validate the complete image header."""
    with path.open("rb") as image_file:
        content = image_file.read(MAX_IMAGE_BYTES + 1)
    mime_type, suffix, width, height = inspect_image(content)
    return content, mime_type, suffix, width, height
