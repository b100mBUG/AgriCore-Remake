"""
app/core/security.py — Password hashing and JWT utilities.

Password hashing
────────────────
Uses bcrypt directly (no passlib wrapper). bcrypt is the industry
standard for password hashing — adaptive cost factor, built-in salt.

    from app.core.security import hash_password, verify_password

    hashed = hash_password("secret123")
    ok = verify_password("secret123", hashed)  # True

JWT tokens
──────────
Officers authenticate with a JWT Bearer token. Tokens carry:
  - sub  : officer id (str)
  - exp  : expiry timestamp

    from app.core.security import create_access_token, decode_token

    token = create_access_token({"sub": str(officer.id)})
    payload = decode_token(token)   # raises HTTPException on invalid
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.core.config import settings

log = logging.getLogger("agricore.security")

# ── bcrypt cost factor ────────────────────────────────────────────────────────
# 12 is a good default — adjust up on faster hardware.
# Each increment doubles hashing time.
_BCRYPT_ROUNDS = 12

_ALGORITHM = "HS256"


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt.

    Returns a utf-8 string suitable for storing in the DB.
    bcrypt generates a fresh salt on every call — two hashes of the
    same password will differ, which is correct behaviour.
    """
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(plain.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash.

    Always runs in constant time to prevent timing attacks.
    Returns False (never raises) on any verification error.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as exc:
        log.warning("Password verification error: %s", exc)
        return False


# ── JWT tokens ────────────────────────────────────────────────────────────────

def create_access_token(payload: dict) -> str:
    """Create a signed JWT with an expiry claim.

    Args:
        payload: Dict of claims. Should include "sub" (subject = officer id).
                 Do NOT include secrets or passwords in the payload.

    Returns:
        Signed JWT string.
    """
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    to_encode = {**payload, "exp": expires}
    return jwt.encode(to_encode, settings.secret_key, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTP 401 on any failure.

    Validates:
      - Signature (secret key match)
      - Expiry (exp claim)
      - Presence of "sub" claim

    Returns:
        The decoded payload dict.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
        if payload.get("sub") is None:
            raise credentials_exc
        return payload
    except JWTError as exc:
        log.debug("JWT decode failed: %s", exc)
        raise credentials_exc
