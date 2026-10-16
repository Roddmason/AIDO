"""Safety checks for Git remote URLs proposed by remediation actions.

@author Rodrigo Mason
"""

from __future__ import annotations

from urllib.parse import urlparse


def remote_url_payload_is_safe(url: str) -> bool:
    """Return true when a proposed remote URL is safe to offer as an action."""
    text = url.strip()
    if not text or text != url:
        return False
    parsed = urlparse(text)
    if parsed.scheme:
        if parsed.scheme not in {"https", "ssh"}:
            return False
        if parsed.username or parsed.password:
            return False
        return bool(parsed.hostname and parsed.path.strip("/"))
    if " " in text or "\t" in text or "\n" in text:
        return False
    user_host, separator, path = text.partition(":")
    user, at, host = user_host.partition("@")
    return bool(separator and at and user and host and path.strip("/"))
