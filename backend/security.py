"""
backend/security.py — Authentication & secret-handling primitives for Lifodial.

Provides:
  • Password hashing / verification (PBKDF2-HMAC-SHA256, stdlib — no extra deps).
      Backward-compatible: verify_password() also accepts a legacy *plaintext*
      value so existing tenants created before hashing are not locked out. Call
      needs_rehash() to detect legacy rows and upgrade them on next login.
  • JWT session tokens (python-jose, already a dependency).
  • Symmetric encryption for provider API keys at rest (Fernet, from the
      `cryptography` package pulled in by python-jose[cryptography]).

The signing/encryption material is derived from settings.secret_key. In
production settings.secret_key MUST be a strong, unique value (see config.py,
which now refuses to boot on the default secret when ENVIRONMENT=production).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from backend.config import settings

# ── Password hashing (PBKDF2-HMAC-SHA256) ────────────────────────────────────
_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 240_000
_PBKDF2_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Return an encoded hash: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = os.urandom(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "{}${}${}${}".format(
        _PBKDF2_ALGO,
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def _looks_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(_PBKDF2_ALGO + "$")


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verify. Accepts legacy plaintext values for migration."""
    if not stored or not password:
        return False
    if not _looks_hashed(stored):
        # Legacy plaintext row — compare directly (constant-time).
        return hmac.compare_digest(password, stored)
    try:
        _algo, iters_s, salt_b64, hash_b64 = stored.split("$", 3)
        iterations = int(iters_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str | None) -> bool:
    """True if the stored value is legacy plaintext or uses weaker params."""
    if not stored:
        return False
    if not _looks_hashed(stored):
        return True
    try:
        _algo, iters_s, _salt, _hash = stored.split("$", 3)
        return int(iters_s) < _PBKDF2_ITERATIONS
    except (ValueError, TypeError):
        return True


# ── JWT session tokens ───────────────────────────────────────────────────────
_JWT_ALG = "HS256"
ACCESS_TOKEN_TTL = timedelta(hours=12)


def create_access_token(
    subject: str,
    role: str,
    extra: dict[str, Any] | None = None,
    ttl: timedelta = ACCESS_TOKEN_TTL,
) -> str:
    """Mint a signed JWT. `subject` is the tenant_id (or 'superadmin')."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the claims dict, or None if invalid/expired."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALG])
    except JWTError:
        return None


# ── Provider-key encryption at rest (Fernet) ─────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet

    # Derive a stable 32-byte urlsafe key from the app secret.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


_ENC_PREFIX = "fernet:"


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a provider key for storage. Returns 'fernet:<token>'."""
    if plaintext is None:
        return ""
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def decrypt_secret(stored: str | None) -> str:
    """Decrypt a stored provider key. Tolerates legacy base64/plaintext values."""
    if not stored:
        return ""
    if stored.startswith(_ENC_PREFIX):
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(stored[len(_ENC_PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
    # Legacy value written by the old base64 "obfuscation" — best-effort decode.
    try:
        return base64.b64decode(stored.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return stored
