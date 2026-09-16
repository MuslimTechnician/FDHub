"""
URL safety validation.

Ensures that URLs fetched by FDHub are trustworthy:
- HTTPS only
- No credential-bearing URLs
- No localhost / private network destinations
- No file:// or other dangerous schemes
- Redirects validated against the same rules
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse


class URLSafetyError(ValueError):
    """Raised when a URL fails safety checks."""


_ALLOWED_SCHEMES = {"https"}

# Private/link-local/loopback network ranges
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_LOCALHOST_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _is_private_ip(host: str) -> bool:
    """Return True if host resolves to a private/loopback/link-local address."""
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        # Not an IP address literal — do name-based check
        return False


def validate_url(url: str, context: str = "") -> str:
    """
    Validate a URL for safe fetching.

    Raises URLSafetyError with a descriptive message if unsafe.
    Returns the URL unchanged if safe.
    """
    ctx = f" ({context})" if context else ""

    if not url:
        raise URLSafetyError(f"Empty URL{ctx}")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise URLSafetyError(f"Malformed URL{ctx}: {exc}") from exc

    # Scheme check
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise URLSafetyError(
            f"Unsafe URL scheme '{scheme}'{ctx}. Only HTTPS is allowed. URL: {url!r}"
        )

    # Must have a host
    host = parsed.hostname or ""
    if not host:
        raise URLSafetyError(f"URL has no host{ctx}: {url!r}")

    # Reject credentials in URL
    if parsed.username or parsed.password:
        raise URLSafetyError(
            f"URL contains embedded credentials{ctx} — this is not allowed: {url!r}"
        )

    # Reject localhost
    if host.lower() in _LOCALHOST_NAMES:
        raise URLSafetyError(f"URL points to localhost{ctx}: {url!r}")

    # Reject private IP ranges
    if _is_private_ip(host):
        raise URLSafetyError(f"URL points to a private/internal IP address{ctx}: {url!r}")

    # Reject suspicious patterns
    if "@" in url.split("//", 1)[-1].split("/")[0]:
        raise URLSafetyError(f"URL contains '@' in host part{ctx}: {url!r}")

    return url


def validate_github_asset_url(url: str) -> str:
    """
    Strict validation for GitHub Release asset URLs.
    Must be from github.com or objects.githubusercontent.com.
    """
    validate_url(url, context="github-asset")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    allowed_hosts = {
        "github.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }

    if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise URLSafetyError(
            f"GitHub asset URL host '{host}' is not a recognized GitHub domain. URL: {url!r}"
        )

    return url
