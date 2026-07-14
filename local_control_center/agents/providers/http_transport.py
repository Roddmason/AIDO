"""Credential-safe urllib transport primitives shared by model providers."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from typing import Any

UrlopenCallable = Callable[..., Any]
_STANDARD_URLOPEN = urllib.request.urlopen


class FailClosedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every HTTP redirect before urllib can replay headers or rewrite a POST."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Return no follow-up request for every redirect status and destination."""


_FAIL_CLOSED_OPENER = urllib.request.build_opener(FailClosedRedirectHandler())


def urlopen_fail_closed(
    request: urllib.request.Request,
    *,
    timeout: float,
    urlopen_override: UrlopenCallable | None = None,
):
    """Open one request without redirects, retaining explicit test injection seams."""
    if urlopen_override is not None and urlopen_override is not _STANDARD_URLOPEN:
        return urlopen_override(request, timeout=timeout)
    return _FAIL_CLOSED_OPENER.open(request, timeout=timeout)
