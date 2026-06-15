"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_millis(ms: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(milliseconds=ms)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
