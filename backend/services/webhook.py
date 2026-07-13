"""
backend/services/webhook.py — Send webhook events to configured URLs.
"""
import logging
from datetime import datetime, timezone
import httpx

from backend.services.net import is_safe_outbound_url

logger = logging.getLogger(__name__)


async def send_webhook(url: str, event_type: str, data: dict) -> bool:
    """
    Send a webhook POST to the configured URL.
    Returns True on success, False on failure.
    """
    if not url:
        return False
    if not is_safe_outbound_url(url):
        logger.warning("Refusing to send webhook to unsafe/internal URL: %s", url)
        return False

    payload = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, follow_redirects=False)
            if response.status_code < 400:
                logger.info(f"Webhook sent: {event_type} -> {url} ({response.status_code})")
                return True
            else:
                logger.warning(
                    f"Webhook failed: {event_type} -> {url} ({response.status_code})"
                )
                return False
    except Exception as e:
        logger.error(f"Webhook error: {event_type} -> {url}: {e}")
        return False
