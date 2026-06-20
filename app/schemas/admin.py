"""
app/schemas/admin.py — Pydantic v2 schemas for admin auth.
"""

from pydantic import BaseModel, EmailStr, Field


class AdminLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class AdminTokenOut(BaseModel):
    """JWT token response for the admin."""

    access_token: str
    token_type: str = "bearer"
