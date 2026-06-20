"""
app/models/farmer.py — Farmer profile.

Farmers need no account. Identity is a device-generated UUID stored
locally on the phone. This means:
  - No password, no email, no phone number required
  - Farmer can't "log in" from another device (acceptable for this use case)
  - Profile is tied to the device — reset wipes it

The county + primary_crop fields drive:
  - Which solution cards are shown first (matching)
  - Which extension officers are suggested (county match)
  - Weather location (geocoded once from county text)
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Farmer(Base):
    __tablename__ = "farmers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Device identity — UUID generated on first app launch
    device_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # Profile — all optional, farmer fills in what they want
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Location — text-based, geocoded server-side
    county: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    sub_county: Mapped[str | None] = mapped_column(String(80), nullable=True)
    village: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Resolved coordinates from geocoding — cached here to avoid repeat API calls
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Farm context — drives card matching and officer suggestions
    primary_crop: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    farm_size_acres: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Farmer id={self.id} county={self.county!r} crop={self.primary_crop!r}>"
