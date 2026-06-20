"""
app/models/officer.py — Extension Officer model.

Officers do NOT have accounts. There is no login, no password, no JWT
issued to an officer. The admin creates and maintains every officer
profile directly (e.g. after vetting them by phone or in person) —
officers are a curated directory entry, not a self-service tenant.
This keeps the MVP surface small: the only credential in this system
belongs to the admin, not to officers or farmers.

Subscription tiers
──────────────────
  free    → Listed in directory only, no profile photo, no social links
  basic   → Full profile (photo, bio, social links), appears in card
            recommendations next to relevant solution cards
  pro     → Everything in basic + featured placement (top of county
            list), analytics dashboard (profile views, contact clicks),
            verified badge

Tier and verification are both admin-set (PATCH /officers/{id}/tier,
PATCH /officers/{id}/verify) — there's no officer-facing billing flow
in this MVP; subscriptions are handled offline and reflected by the
admin.

Officer matching
────────────────
When a farmer reads a solution card, the backend queries officers by:
  1. county == farmer.county
  2. specialization overlaps with card.category or card.crop
  3. is_featured=True officers appear first (pro tier perk)
  4. Then ordered by profile_views DESC (social proof)

Social links
────────────
All stored as full URLs (https://wa.me/254..., https://facebook.com/...).
The frontend renders them as icon buttons — no parsing needed.
"""

import enum
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OfficerTier(str, enum.Enum):
    free  = "free"
    basic = "basic"
    pro   = "pro"


class ExtensionOfficer(Base):
    __tablename__ = "extension_officers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ── Contact (informational only — never used for login) ────────────────────
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )

    # ── Public profile ────────────────────────────────────────────────────────
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    title: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )  # e.g. "Crops Specialist", "Livestock Officer"
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cloudinary URL of their profile photo
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Location / expertise ──────────────────────────────────────────────────
    county: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sub_county: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # JSON-encoded list of crops they specialise in, e.g. '["maize","beans"]'
    # Stored as text for SQLite compatibility; use JSON column in Postgres if preferred.
    crops_json: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")

    # Broad category focus — drives officer ↔ card matching
    specialization: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )  # "crops" | "livestock" | "soil" | "horticulture" | "general"

    # Years of experience — shown on profile card
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Contact / social links ────────────────────────────────────────────────
    # All stored as full URLs — frontend renders as icon buttons.
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp_link: Mapped[str | None] = mapped_column(String(200), nullable=True)
    facebook_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tiktok_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    twitter_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # ── Subscription ──────────────────────────────────────────────────────────
    tier: Mapped[OfficerTier] = mapped_column(
        Enum(OfficerTier), nullable=False, default=OfficerTier.free
    )
    subscription_expires: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_featured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # pro perk — appears top of county list

    # ── Analytics ─────────────────────────────────────────────────────────────
    # Incremented by the card detail endpoint when officer is shown.
    profile_views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Incremented by frontend (via POST /officers/{id}/contact-click).
    contact_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )  # admin verifies credentials

    # ── Timestamps ────────────────────────────────────────────────────────────
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_subscription_active(self) -> bool:
        """True if the officer has a valid paid subscription today."""
        if self.tier == OfficerTier.free:
            return True
        if self.subscription_expires is None:
            return False
        return self.subscription_expires >= date.today()

    @property
    def effective_tier(self) -> OfficerTier:
        """Return actual tier — downgrades to free if subscription expired."""
        if self.is_subscription_active:
            return self.tier
        return OfficerTier.free

    def __repr__(self) -> str:
        return (
            f"<ExtensionOfficer id={self.id} name={self.full_name!r} "
            f"county={self.county!r} tier={self.tier}>"
        )
