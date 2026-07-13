"""
backend/services/storage.py — Supabase Storage helper (multi-tenant, no SDK).

Talks to the Supabase Storage REST API directly with httpx using the
service-role key (server-side only — this key is never exposed to clients).

Bucket strategy (deliberately SPLIT by sensitivity, not one bucket + policies):
  • settings.supabase_storage_bucket  (private) — clinical / tenant-private data
        {tenant_id}/knowledge-base/{file}
        {tenant_id}/recordings/{call_id}.opus     (compressed; recordings not
                                                    yet persisted — see report)
        {tenant_id}/consent/{voice_id}.wav
  • settings.supabase_public_bucket   (public)  — branding only
        {tenant_id}/branding/{agent_id}-avatar.{ext}

WHY SPLIT BUCKETS instead of one bucket with per-folder policies: a single
mis-scoped policy on a shared bucket could expose PHI (recordings, consent).
Physically separating public branding from private clinical data means the
public bucket can be blanket-public with zero PHI risk, and the private bucket
blanket-private — far safer to get right than folder-level policy juggling.
Branding must be blanket-public because the embed widget loads avatars on
external clinic sites with no auth context.
"""
from __future__ import annotations

import logging
import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# Allowed avatar types → canonical extension. Validated before any upload.
AVATAR_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
AVATAR_MAX_BYTES = 8 * 1024 * 1024  # 8 MB (clinics may upload high-res logos)
# Rendered avatar size served to the widget — small, square, WebP.
AVATAR_RENDER_PX = 256


def optimize_avatar_to_webp(content: bytes) -> bytes | None:
    """Resize/crop an uploaded avatar to a small square WebP for widget rendering.

    Returns optimized WebP bytes, or None if Pillow is unavailable or the image
    can't be processed (caller then falls back to serving the original). This
    keeps a large source upload from ever slowing the embed script.
    """
    try:
        import io
        from PIL import Image
    except Exception:
        logger.warning("Pillow not available — serving original avatar without optimization")
        return None
    try:
        img = Image.open(io.BytesIO(content))
        img = img.convert("RGBA")
        # Center square-crop, then downscale to AVATAR_RENDER_PX.
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((AVATAR_RENDER_PX, AVATAR_RENDER_PX), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=82, method=6)
        return out.getvalue()
    except Exception as e:
        logger.warning("Avatar optimization failed (%s) — serving original", e)
        return None


def storage_configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def _headers(content_type: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {settings.supabase_service_role_key}"}
    if content_type:
        h["Content-Type"] = content_type
    return h


def public_url(bucket: str, path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{bucket}/{path}"


async def ensure_public_bucket() -> None:
    """Create the public branding bucket if it doesn't exist (idempotent)."""
    if not storage_configured():
        return
    bucket = settings.supabase_public_bucket
    async with httpx.AsyncClient(timeout=15) as client:
        # Check existence
        r = await client.get(f"{settings.supabase_url}/storage/v1/bucket/{bucket}", headers=_headers())
        if r.status_code == 200:
            return
        # Create as public
        await client.post(
            f"{settings.supabase_url}/storage/v1/bucket",
            headers=_headers("application/json"),
            json={"id": bucket, "name": bucket, "public": True},
        )
        logger.info("Created public storage bucket %s", bucket)


async def upload_public(path: str, content: bytes, content_type: str) -> str:
    """
    Upload to the PUBLIC bucket (branding/avatars) and return the public URL.
    Overwrites any existing object at the same path (x-upsert).
    Raises RuntimeError on failure so callers can surface a real error.
    """
    if not storage_configured():
        raise RuntimeError("Supabase Storage is not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).")
    bucket = settings.supabase_public_bucket
    headers = _headers(content_type)
    headers["x-upsert"] = "true"
    url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, content=content)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload failed: HTTP {r.status_code} {r.text[:200]}")
    return public_url(bucket, path)


async def upload_private(path: str, content: bytes, content_type: str) -> str:
    """Upload to the PRIVATE bucket (KB/recordings/consent). Returns the storage
    path (serve later via a signed URL, never a public link)."""
    if not storage_configured():
        raise RuntimeError("Supabase Storage is not configured.")
    bucket = settings.supabase_storage_bucket
    headers = _headers(content_type)
    headers["x-upsert"] = "true"
    url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{path}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, content=content)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload failed: HTTP {r.status_code} {r.text[:200]}")
    return f"{bucket}/{path}"
