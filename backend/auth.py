"""
backend/auth.py — FastAPI authentication dependencies.

Usage:
    from backend.auth import CurrentUser, require_superadmin, require_tenant

    @router.get("/tenants/{id}/something")
    async def handler(id: str, user: CurrentUser = Depends(require_tenant)):
        # user.tenant_id is derived from the verified token — NEVER from the path.
        ...

Tokens are Bearer JWTs issued by the login endpoints (see backend/security.py).
The token's `sub` claim is the tenant_id (or "superadmin"); `role` is
"clinic" or "superadmin". Tenant-scoped handlers must compare the path/body
tenant_id against user.tenant_id and 404 on mismatch (helper: user.owns()).

Impersonation tokens (superadmin "view as this clinic" — see
backend/services/impersonation.py) are ordinary CLINIC tokens carrying `imp`,
`jti` and `act` claims. They are deliberately indistinguishable from a clinic
login as far as authorisation goes, which is what scopes them to one clinic; the
extra claims only add a revocation check and let handlers see who is really
behind the session (user.impersonator).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.security import IMPERSONATION_CLAIM, decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    subject: str
    role: str
    tenant_id: str | None
    #: Set only for impersonation tokens: the session row's id (the token's `jti`).
    impersonation_id: str | None = None
    #: Set only for impersonation tokens: the superadmin behind the session.
    impersonator: str | None = None

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_impersonating(self) -> bool:
        return self.impersonation_id is not None

    def owns(self, tenant_id: str) -> bool:
        """Superadmin owns everything; a clinic owns only its own tenant."""
        return self.is_superadmin or (self.tenant_id is not None and self.tenant_id == tenant_id)

    def require_owns(self, tenant_id: str) -> None:
        if not self.owns(tenant_id):
            # 404 (not 403) so we don't confirm existence of other tenants' data.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_access_token(creds.credentials)
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = claims.get("role", "clinic")
    subject = claims.get("sub", "")
    tenant_id = None if role == "superadmin" else subject

    if claims.get(IMPERSONATION_CLAIM):
        return await _authorize_impersonation(claims, subject, role, tenant_id)

    return AuthUser(subject=subject, role=role, tenant_id=tenant_id)


# ── Impersonation tokens ──────────────────────────────────────────────────────
# Built per raise rather than shared: a single exception instance re-raised from
# concurrent requests accumulates tracebacks from all of them.
def _impersonation_ended() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="This impersonation session has ended. Start a new one from the superadmin panel.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _authorize_impersonation(
    claims: dict, subject: str, role: str, tenant_id: str | None
) -> AuthUser:
    """Validate a superadmin "view as this clinic" token against its session row.

    A valid signature is NOT enough here. The signed token cannot be recalled, so
    the live session row is what makes "Exit" and the TTL real: it is checked on
    every single request, and the token stops working the moment the row is ended
    or past expires_at.

    The role check below is defence in depth. An impersonation token is minted
    with role="clinic" precisely so it cannot reach superadmin endpoints; forging
    one that claims `imp` AND role="superadmin" would need the signing key, but if
    that ever happened this path must not be the thing that honours it.
    """
    if role != "clinic" or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed impersonation token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_id = claims.get("jti")
    if not session_id:
        raise _impersonation_ended()

    # Imported here (not at module scope) so backend.auth stays importable without
    # the DB layer, and so only impersonated requests pay for the lookup.
    from backend.db import AsyncSessionLocal
    from backend.services.impersonation import load_active

    async with AsyncSessionLocal() as db:
        row = await load_active(db, session_id, tenant_id)

    if row is None:
        raise _impersonation_ended()

    return AuthUser(
        subject=subject,
        role="clinic",
        tenant_id=tenant_id,
        impersonation_id=session_id,
        impersonator=claims.get("act") or row.actor,
    )


async def require_superadmin(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    if not user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# A clinic OR superadmin token (tenant-scoped handlers enforce ownership via user.require_owns()).
require_tenant = get_current_user

CurrentUser = Annotated[AuthUser, Depends(get_current_user)]
SuperAdmin = Annotated[AuthUser, Depends(require_superadmin)]
