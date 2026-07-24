"""
backend/services/storage.py â€” Supabase Storage helper (multi-tenant, no SDK).

Talks to the Supabase Storage REST API directly with httpx using the
service-role key (server-side only â€” this key is never exposed to clients).

Bucket strategy (deliberately SPLIT by sensitivity, not one bucket + policies):
  â€¢ settings.supabase_storage_bucket  (private) â€” clinical / tenant-private data
        {tenant_id}/knowledge-base/{file}
        {tenant_id}/recordings/{call_id}.opus     (compressed; recordings not
                                                    yet persisted â€” see report)
        {tenant_id}/consent/{voice_id}.wav
  â€¢ settings.supabase_public_bucket   (public)  â€” branding only
        {tenant_id}/branding/{agent_id}-avatar.{ext}

WHY SPLIT BUCKETS instead of one bucket with per-folder policies: a single
mis-scoped policy on a shared bucket could expose PHI (recordings, consent).
Physically separating public branding from private clinical data means the
public bucket can be blanket-public with zero PHI risk, and the private bucket
blanket-private â€” far safer to get right than folder-level policy juggling.
Branding must be blanket-public because the embed widget loads avatars on
external clinic sites with no auth context.
"""
from __future__ import annotations

import logging
import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# Allowed avatar types â†’ canonical extension. Validated before any upload.
AVATAR_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
AVATAR_MAX_BYTES = 8 * 1024 * 1024  # 8 MB (clinics may upload high-res logos)
# Rendered avatar size served to the widget â€” small, square, WebP.
AVATAR_RENDER_PX = 256

# Allowed MIME types for private bucket (KB docs, consent audio, recordings)
PRIVATE_ALLOWED_MIMES = [
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/json",
    "audio/wav",
    "audio/mpeg",
    "audio/ogg",
    "audio/webm",
    "audio/opus",
]
# Max private file: 50 MB (large PDF knowledge-base docs, consent audio)
PRIVATE_MAX_BYTES = 50 * 1024 * 1024

# Max public avatar: 8 MB
PUBLIC_MAX_BYTES = 8 * 1024 * 1024
PUBLIC_ALLOWED_MIMES = list(AVATAR_MIME_EXT.keys())


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
        logger.warning("Pillow not available â€” serving original avatar without optimization")
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
        logger.warning("Avatar optimization failed (%s) â€” serving original", e)
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
    """Create/configure the public branding bucket (idempotent).

    Enforces:
      - public=True (widget embed loads avatars without auth)
      - file_size_limit=8MB (protect free-tier quota)
      - allowed_mime_types=image types only (prevent XSS via .html uploads)
    """
    if not storage_configured():
        return
    bucket = settings.supabase_public_bucket
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{settings.supabase_url}/storage/v1/bucket/{bucket}",
            headers=_headers(),
        )
        if r.status_code == 200:
            # Bucket exists â€” patch to enforce limits (idempotent update)
            await client.patch(
                f"{settings.supabase_url}/storage/v1/bucket/{bucket}",
                headers=_headers("application/json"),
                json={
                    "public": True,
                    "file_size_limit": PUBLIC_MAX_BYTES,
                    "allowed_mime_types": PUBLIC_ALLOWED_MIMES,
                },
            )
            return
        # Create as public with limits
        create_resp = await client.post(
            f"{settings.supabase_url}/storage/v1/bucket",
            headers=_headers("application/json"),
            json={
                "id": bucket,
                "name": bucket,
                "public": True,
                "file_size_limit": PUBLIC_MAX_BYTES,
                "allowed_mime_types": PUBLIC_ALLOWED_MIMES,
            },
        )
        if create_resp.status_code in (200, 201):
            logger.info("Created public storage bucket %s (8MB limit, image MIME types)", bucket)
        else:
            logger.warning(
                "Could not create public bucket %s: HTTP %s %s",
                bucket, create_resp.status_code, create_resp.text[:100],
            )


async def ensure_private_bucket() -> None:
    """Create/configure the private clinical data bucket (idempotent).

    Enforces:
      - public=False (PHI data â€” KB, recordings, consent audio)
      - file_size_limit=50MB (large PDF knowledge-base docs)
      - allowed_mime_types=docs+audio (prevent script uploads)
    """
    if not storage_configured():
        return
    bucket = settings.supabase_storage_bucket
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{settings.supabase_url}/storage/v1/bucket/{bucket}",
            headers=_headers(),
        )
        if r.status_code == 200:
            # Bucket exists â€” patch to enforce limits (idempotent)
            await client.patch(
                f"{settings.supabase_url}/storage/v1/bucket/{bucket}",
                headers=_headers("application/json"),
                json={
                    "public": False,
                    "file_size_limit": PRIVATE_MAX_BYTES,
                    "allowed_mime_types": PRIVATE_ALLOWED_MIMES,
                },
            )
            return
        # Create as private with limits
        create_resp = await client.post(
            f"{settings.supabase_url}/storage/v1/bucket",
            headers=_headers("application/json"),
            json={
                "id": bucket,
                "name": bucket,
                "public": False,
                "file_size_limit": PRIVATE_MAX_BYTES,
                "allowed_mime_types": PRIVATE_ALLOWED_MIMES,
            },
        )
        if create_resp.status_code in (200, 201):
            logger.info("Created private storage bucket %s (50MB limit, doc+audio MIME types)", bucket)
        else:
            logger.warning(
                "Could not create private bucket %s: HTTP %s %s",
                bucket, create_resp.status_code, create_resp.text[:100],
            )


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


async def generate_signed_url(path: str, expires_in_seconds: int = 3600) -> str:
    """Generate a time-limited signed URL for a private bucket object.

    Used to serve private KB files, consent audio, or recordings to authenticated
    users without exposing the service-role key or making the bucket public.

    Args:
        path: The storage path as returned by upload_private() e.g.
              "lifodial-uploads/{tenant_id}/knowledge-base/file.pdf"
              OR just the object path within the bucket e.g.
              "{tenant_id}/knowledge-base/file.pdf"
        expires_in_seconds: Signed URL lifetime (default 1 hour).

    Returns:
        Signed URL string (publicly accessible for expires_in_seconds).

    Raises:
        RuntimeError: If storage is not configured or Supabase returns an error.
    """
    if not storage_configured():
        raise RuntimeError("Supabase Storage is not configured.")

    bucket = settings.supabase_storage_bucket
    # Strip bucket prefix if present (callers may pass the full upload_private path)
    object_path = path
    if object_path.startswith(f"{bucket}/"):
        object_path = object_path[len(f"{bucket}/"):]

    url = f"{settings.supabase_url}/storage/v1/object/sign/{bucket}/{object_path}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            url,
            headers=_headers("application/json"),
            json={"expiresIn": expires_in_seconds},
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to generate signed URL for {object_path}: HTTP {r.status_code} {r.text[:200]}"
        )
    data = r.json()
    signed_path = data.get("signedURL") or data.get("signedUrl") or ""
    if not signed_path:
        raise RuntimeError(f"Supabase returned no signedURL in response: {data}")
    # signedURL from Supabase is a path like /storage/v1/object/sign/... â€” prepend base URL
    if signed_path.startswith("/"):
        return f"{settings.supabase_url}{signed_path}"
    return signed_path


async def delete_object(bucket_type: str, path: str) -> bool:
    """Delete a storage object. Returns True on success.

    Args:
        bucket_type: "public" or "private"
        path: Object path within the bucket
    """
    if not storage_configured():
        return False
    bucket = (
        settings.supabase_public_bucket if bucket_type == "public"
        else settings.supabase_storage_bucket
    )
    object_path = path
    if object_path.startswith(f"{bucket}/"):
        object_path = object_path[len(f"{bucket}/"):]

    url = f"{settings.supabase_url}/storage/v1/object/{bucket}/{object_path}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(url, headers=_headers())
    if r.status_code in (200, 204):
        logger.info("Deleted storage object: %s/%s", bucket, object_path)
        return True
    logger.warning(
        "Delete storage object %s/%s failed: HTTP %s",
        bucket, object_path, r.status_code,
    )
    return False
