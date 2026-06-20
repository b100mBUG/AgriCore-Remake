"""
app/schemas/officer.py — Pydantic v2 schemas for officer endpoints.

Officers do NOT have accounts. There is no officer login, no officer
JWT, no officer-facing "my profile" endpoint. The admin creates and
maintains every officer profile; officers are a directory entry the
admin curates on their behalf (e.g. after a phone call or in-person
verification), not a self-service tenant.

Covers:
  - Admin create / update (the only way an officer profile changes)
  - Public profile (what farmers see)
  - Admin view (includes tier, analytics, active status)
"""

import json
from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class OfficerCreate(BaseModel):
    """Admin creates a new officer profile.

    No password — officers never log in. Contact email is optional and
    purely informational (e.g. for the admin's own records), not a
    login credential.
    """

    full_name: str = Field(..., min_length=2, max_length=150)
    email: EmailStr | None = None
    county: str = Field(..., min_length=2, max_length=80)
    title: str | None = Field(None, max_length=120)
    specialization: str | None = Field(None, max_length=100)
    phone_number: str | None = Field(None, max_length=20)

    @field_validator("full_name", "county", mode="before")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class OfficerProfileUpdate(BaseModel):
    """Admin updates an officer's public profile.

    All fields optional — only provided fields are updated.
    photo_url is set by the photo upload endpoint, not here directly.
    """

    full_name: str | None = Field(None, min_length=2, max_length=150)
    email: EmailStr | None = None
    title: str | None = Field(None, max_length=120)
    bio: str | None = Field(None, max_length=1000)
    county: str | None = Field(None, max_length=80)
    sub_county: str | None = Field(None, max_length=80)
    specialization: str | None = Field(None, max_length=100)
    years_experience: int | None = Field(None, ge=0, le=60)

    # Social links
    phone_number: str | None = Field(None, max_length=20)
    whatsapp_link: str | None = Field(None, max_length=200)
    facebook_url: str | None = Field(None, max_length=300)
    instagram_url: str | None = Field(None, max_length=300)
    tiktok_url: str | None = Field(None, max_length=300)
    twitter_url: str | None = Field(None, max_length=300)
    website_url: str | None = Field(None, max_length=300)

    # crops_json stored in DB as JSON string; accept list from client
    crops: list[str] | None = Field(None)

    @field_validator("crops", mode="before")
    @classmethod
    def parse_crops(cls, v):
        """Accept JSON string or list from client."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> "OfficerProfileUpdate":
        if all(
            getattr(self, f) is None
            for f in self.model_fields
        ):
            raise ValueError("At least one field must be provided for update.")
        return self


class OfficerPublicOut(BaseModel):
    """What farmers see — no email, no analytics, no tier details."""

    model_config = {"from_attributes": True}

    id: int
    full_name: str
    title: str | None
    bio: str | None
    photo_url: str | None
    county: str
    sub_county: str | None
    specialization: str | None
    years_experience: int | None
    crops_json: str | None   # raw JSON string; frontend parses
    is_verified: bool
    is_featured: bool

    # Contact — only whatsapp + social (no email exposed publicly)
    phone_number: str | None
    whatsapp_link: str | None
    facebook_url: str | None
    instagram_url: str | None
    tiktok_url: str | None
    twitter_url: str | None
    website_url: str | None


class OfficerAdminOut(OfficerPublicOut):
    """Admin view — everything, since the admin owns this profile end-to-end."""

    email: str | None
    tier: str
    subscription_expires: date | None
    profile_views: int
    contact_clicks: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
