"""
backend/services/net.py — SSRF protection for outbound (tenant-controlled) URLs.

Tenants can set webhook / Google-Sheets URLs. Without validation an attacker
could point these at cloud metadata (169.254.169.254), localhost, or internal
services and have the server POST booking data there (SSRF). is_safe_outbound_url()
resolves the host and rejects any private / loopback / link-local / reserved
address.

A redirect can defeat that check by bouncing to an internal target AFTER the
URL was validated, so httpx's own follow_redirects=True must never be used on
a tenant-controlled URL. Callers either pass follow_redirects=False, or — when
the endpoint legitimately redirects — use post_json_with_safe_redirects()
below, which re-validates every hop before following it.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

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


# Redirects that turn a POST into a GET of the target (301/302/303) vs. those
# that must replay the original method and body (307/308).
_REDIRECT_TO_GET = frozenset({301, 302, 303})
_REDIRECT_PRESERVING_METHOD = frozenset({307, 308})
_REDIRECT_STATUSES = _REDIRECT_TO_GET | _REDIRECT_PRESERVING_METHOD


class UnsafeRedirectError(Exception):
    """A redirect pointed at a host that failed the SSRF check."""


async def post_json_with_safe_redirects(
    url: str,
    payload: dict,
    *,
    timeout: float = 10.0,
    max_redirects: int = 3,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.Response:
    """POST `payload` as JSON, following redirects ONLY to hosts that still
    pass is_safe_outbound_url().

    Why this exists: refusing every redirect (the previous behaviour) silently
    broke Google Sheets sync for every clinic. A Google Apps Script /exec URL
    ALWAYS answers with a 302 to script.googleusercontent.com, so
    follow_redirects=False meant each booking failed with
    "Redirect response '302 Found'" — clinics believed their bookings were
    syncing to their sheet and none of them were (found in production
    2026-08-10).

    The naive fix (follow_redirects=True) would reintroduce the SSRF this
    module exists to prevent: a tenant-controlled webhook could 302 the server
    into 169.254.169.254 or localhost after passing the initial check. So each
    hop is re-validated here before it is followed, and the chain is bounded.

    Raises UnsafeRedirectError if a hop is rejected, or httpx.HTTPError on
    transport failures. The response is returned unchecked — callers decide
    what to do with its status.
    """
    if not is_safe_outbound_url(url):
        raise UnsafeRedirectError(f"Refusing to POST to unsafe URL: {url}")

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        current_url = url
        method = "POST"
        send_body = True

        for _ in range(max_redirects + 1):
            if method == "POST" and send_body:
                response = await client.post(current_url, json=payload, follow_redirects=False)
            else:
                response = await client.get(current_url, follow_redirects=False)

            if response.status_code not in _REDIRECT_STATUSES:
                return response

            location = response.headers.get("location")
            if not location:
                return response  # a redirect with nowhere to go — let the caller see it

            # Location may be relative; resolve it against the URL we just hit.
            next_url = urljoin(str(current_url), location)
            if not is_safe_outbound_url(next_url):
                raise UnsafeRedirectError(
                    f"Blocked redirect from {current_url} to unsafe target {next_url}"
                )

            if response.status_code in _REDIRECT_TO_GET:
                method, send_body = "GET", False
            current_url = next_url

        raise UnsafeRedirectError(f"Too many redirects (>{max_redirects}) starting at {url}")
