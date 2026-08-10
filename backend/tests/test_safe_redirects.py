"""
Tests for net.post_json_with_safe_redirects — following redirects WITHOUT
reopening the SSRF hole the module exists to close.

Background (production, 2026-08-10): every Google Sheets sync was failing with
"Redirect response '302 Found'", because a Google Apps Script /exec URL always
302s to script.googleusercontent.com and the client passed
follow_redirects=False. Clinics believed bookings were syncing to their sheet
and none of them were. The naive fix (follow_redirects=True) would let a
tenant-controlled webhook 302 the server into cloud metadata / localhost, so
each hop is re-validated instead — that is what these tests pin down.

Run: python -m pytest backend/tests/test_safe_redirects.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-redirect-tests")
os.environ.setdefault("ENVIRONMENT", "development")

from unittest.mock import patch

import httpx
import pytest

from backend.services import net
from backend.services.net import UnsafeRedirectError, post_json_with_safe_redirects


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_follows_a_redirect_to_a_safe_host():
    """The Google Apps Script shape: POST /exec -> 302 -> GET the target."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        if request.url.path == "/macros/s/KEY/exec":
            return httpx.Response(302, headers={"location": "https://script.googleusercontent.com/macros/echo?k=1"})
        return httpx.Response(200, text="OK")

    with patch.object(net, "is_safe_outbound_url", return_value=True):
        r = await post_json_with_safe_redirects(
            "https://script.google.com/macros/s/KEY/exec", {"a": 1}, transport=_transport(handler),
        )

    assert r.status_code == 200
    # 302 must convert POST -> GET, exactly as a browser/httpx would.
    assert seen[0][0] == "POST"
    assert seen[1][0] == "GET"
    assert "googleusercontent" in seen[1][1]


@pytest.mark.asyncio
async def test_blocks_a_redirect_into_an_internal_host():
    """THE security property: a 302 must not be able to bounce the server into
    cloud metadata after the initial URL passed validation."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "evil" in str(request.url):
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        raise AssertionError(f"must never have been requested: {request.url}")

    # Only the FIRST url is safe; the redirect target is not — mirroring a
    # tenant webhook that passes the check then bounces somewhere internal.
    def fake_safe(url):
        return "169.254.169.254" not in (url or "")

    with patch.object(net, "is_safe_outbound_url", side_effect=fake_safe):
        with pytest.raises(UnsafeRedirectError, match="unsafe target"):
            await post_json_with_safe_redirects(
                "https://evil.example.com/hook", {"a": 1}, transport=_transport(handler),
            )


@pytest.mark.asyncio
async def test_rejects_an_unsafe_starting_url_outright():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never have been requested")

    with patch.object(net, "is_safe_outbound_url", return_value=False):
        with pytest.raises(UnsafeRedirectError, match="unsafe URL"):
            await post_json_with_safe_redirects(
                "http://localhost/hook", {"a": 1}, transport=_transport(handler),
            )


@pytest.mark.asyncio
async def test_redirect_chain_is_bounded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    with patch.object(net, "is_safe_outbound_url", return_value=True):
        with pytest.raises(UnsafeRedirectError, match="Too many redirects"):
            await post_json_with_safe_redirects(
                "https://example.com/start", {"a": 1}, max_redirects=2, transport=_transport(handler),
            )


@pytest.mark.asyncio
async def test_307_preserves_method_and_body():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.content))
        if request.url.path == "/start":
            return httpx.Response(307, headers={"location": "https://example.com/moved"})
        return httpx.Response(200, text="OK")

    with patch.object(net, "is_safe_outbound_url", return_value=True):
        r = await post_json_with_safe_redirects(
            "https://example.com/start", {"a": 1}, transport=_transport(handler),
        )

    assert r.status_code == 200
    assert [m for m, _ in seen] == ["POST", "POST"]
    assert b'"a"' in seen[1][1], "307 must replay the original body"


@pytest.mark.asyncio
async def test_non_redirect_response_is_returned_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with patch.object(net, "is_safe_outbound_url", return_value=True):
        r = await post_json_with_safe_redirects(
            "https://example.com/hook", {"a": 1}, transport=_transport(handler),
        )
    # Status is the caller's to interpret — this helper does not raise on 5xx.
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_sheets_logger_now_succeeds_through_a_redirect():
    """End-to-end through the real log_booking_to_sheets(), which previously
    returned False for every booking because of the 302."""
    from backend.services import sheets

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/exec"):
            return httpx.Response(302, headers={"location": "https://script.googleusercontent.com/macros/echo"})
        return httpx.Response(200, text="OK")

    async def fake_post(url, payload, *, timeout=10.0, max_redirects=3, transport=None):
        return await post_json_with_safe_redirects(
            url, payload, timeout=timeout, max_redirects=max_redirects,
            transport=_transport(handler),
        )

    with patch.object(net, "is_safe_outbound_url", return_value=True), \
         patch.object(sheets, "is_safe_outbound_url", return_value=True), \
         patch.object(sheets, "post_json_with_safe_redirects", side_effect=fake_post):
        ok = await sheets.log_booking_to_sheets(
            action="BOOK", name="John Doe", phone="+919812345678", date="23/07/2026",
            time="3 PM", doctor="Dr Sharma", appointment_id="appt-1",
            webhook_url="https://script.google.com/macros/s/KEY/exec",
        )
    assert ok is True
