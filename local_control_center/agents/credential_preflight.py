"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from .credentials import CredentialResolver

PASSING_STATUS_BY_MODE = {
    "status": {"configured", "unverified"},
    "fetch": {"configured"},
}


def refs_from_environment() -> list[str]:
    raw = os.environ.get("AIDO_CREDENTIAL_PREFLIGHT_REFS", "")
    refs: list[str] = []
    for chunk in raw.replace("\n", ",").replace(";", ",").split(","):
        ref = chunk.strip()
        if ref:
            refs.append(ref)
    return refs


def run_credential_preflight(
    refs: Sequence[str],
    *,
    resolver: CredentialResolver | None = None,
    fetch: bool = False,
) -> dict[str, object]:
    """Validate credential refs without exposing resolved credential values."""
    mode = "fetch" if fetch else "status"
    resolver = resolver or CredentialResolver()
    allowed_statuses = PASSING_STATUS_BY_MODE[mode]
    credentials: list[dict[str, str]] = []
    ok = True
    for raw_ref in refs:
        result = resolver.resolve(raw_ref, fetch=fetch)
        if result.status not in allowed_statuses:
            ok = False
        public = result.to_public_dict()
        credentials.append(
            {
                "ref": public["credentialRef"],
                "status": public["credentialStatus"],
                "source": public["credentialSource"],
                "message": public["message"],
                **public,
            }
        )
    if not credentials:
        ok = False
    return {
        "ok": ok,
        "mode": mode,
        "credentials": credentials,
        "summary": {
            "total": len(credentials),
            "passing": sum(1 for item in credentials if item["status"] in allowed_statuses),
        },
    }
