"""
app/core/deps.py — Reusable FastAPI dependencies.

Import these in route files:

    from app.core.deps import require_admin, Pagination

These are the only place where auth logic and header checks live.
Routes should import deps, not re-implement auth themselves.

Dependencies
────────────
  get_db         → DB session (defined in database.py)
  require_admin  → Validates the admin's JWT Bearer token
  Pagination     → Query params: offset + limit

Note: there is no officer auth dependency. Officers have no accounts
in this system — see app/models/officer.py.
"""

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_token

log = logging.getLogger("agricore.deps")

_bearer = HTTPBearer(auto_error=False)


# ── Admin authentication ──────────────────────────────────────────────────────

async def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> None:
    """Validate the admin's JWT Bearer token for admin-only endpoints.

    Usage:
        @router.delete("/cards/{id}", dependencies=[Depends(require_admin)])
        async def delete_card(...):
            ...

    The token is obtained via POST /admin/login and must carry
    role="admin" — see app/core/security.py and app/routes/admin.py.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Pagination ────────────────────────────────────────────────────────────────

@dataclass
class Pagination:
    """Standard pagination query parameters.

    Usage:
        @router.get("/cards")
        async def list_cards(page: Pagination = Depends(Pagination)):
            offset = page.offset
            limit  = page.limit
    """
    offset: int = 0
    limit: int = 20

    def __post_init__(self):
        if self.offset < 0:
            self.offset = 0
        if self.limit < 1:
            self.limit = 1
        if self.limit > 100:
            self.limit = 100
