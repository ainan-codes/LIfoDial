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
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    subject: str
    role: str
    tenant_id: str | None

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

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
    return AuthUser(subject=subject, role=role, tenant_id=tenant_id)


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
