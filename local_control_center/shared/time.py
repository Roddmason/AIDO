"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_millis(ms: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(milliseconds=ms))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
