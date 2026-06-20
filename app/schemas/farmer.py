"""
app/schemas/farmer.py — Pydantic v2 schemas for farmer endpoints.

Schemas are thin DTOs — validation in, serialization out.
They intentionally do not mirror the ORM model 1:1; fields that are
internal (hashed_password, created_at, etc.) are excluded from
request schemas and only appear in response schemas where relevant.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class FarmerRegister(BaseModel):
    """Payload to create or upsert a farmer profile from the app.

    The device_id is generated on first launch by the mobile app and
    stored locally. If it already exists in the DB, this acts as an
    update — the farmer is effectively "syncing" their profile.
    """

    device_id: str = Field(..., min_length=8, max_length=64)
    name: str | None = Field(None, max_length=120)
    county: str | None = Field(None, max_length=80)
    sub_county: str | None = Field(None, max_length=80)
    village: str | None = Field(None, max_length=80)
    primary_crop: str | None = Field(None, max_length=120)
    farm_size_acres: float | None = Field(None, gt=0)

    @field_validator("county", "sub_county", "village", "primary_crop", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v


class FarmerUpdate(BaseModel):
    """Partial update — all fields optional, only provided fields change."""

    name: str | None = Field(None, max_length=120)
    county: str | None = Field(None, max_length=80)
    sub_county: str | None = Field(None, max_length=80)
    village: str | None = Field(None, max_length=80)
    primary_crop: str | None = Field(None, max_length=120)
    farm_size_acres: float | None = Field(None, gt=0)


class FarmerOut(BaseModel):
    """Response schema — safe subset of farmer fields."""

    model_config = {"from_attributes": True}

    id: int
    device_id: str
    name: str | None
    county: str | None
    sub_county: str | None
    village: str | None
    primary_crop: str | None
    farm_size_acres: float | None
    latitude: float | None
    longitude: float | None
    created_at: datetime
    updated_at: datetime
