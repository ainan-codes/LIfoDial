"""
backend/services/net.py — SSRF protection for outbound (tenant-controlled) URLs.

Tenants can set webhook / Google-Sheets URLs. Without validation an attacker
could point these at cloud metadata (169.254.169.254), localhost, or internal
services and have the server POST booking data there (SSRF). is_safe_outbound_url()
resolves the host and rejects any private / loopback / link-local / reserved
address. Callers must ALSO pass follow_redirects=False so a 302 can't bounce to
an internal target after the check.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _addr_is_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_outbound_url(url: str | None) -> bool:
    """True only if `url` is http(s) with a public, resolvable host."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        logger.warning("Blocked outbound URL with scheme %r", parsed.scheme)
        return False
    host = parsed.hostname
    if not host:
        return False

    # Resolve ALL addresses the host maps to and ensure none are internal.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        logger.warning("Blocked outbound URL — host does not resolve: %s", host)
        return False

    for info in infos:
        ip_str = info[4][0]
        if _addr_is_blocked(ip_str):
            logger.warning("Blocked SSRF attempt to %s -> %s", host, ip_str)
            return False
    return True
