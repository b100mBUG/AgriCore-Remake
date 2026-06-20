"""
app/models/input_ad.py — Sponsored input listings (agrodealers).

InputAds appear at the bottom of relevant solution cards. For example,
a card about Fall Armyworm shows a sponsored Duduthrin listing.

Matching logic
──────────────
An ad appears on a card if:
  - ad.category matches card.category  OR
  - ad.crop_tags contains card.crop    OR
  - ad.is_general=True (appears on all cards)

Agrodealers pay per listing (flat monthly fee) or per WhatsApp click.
The click-through is tracked via the POST /ads/{id}/click endpoint.

All contact is via WhatsApp or phone — no in-app transaction.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InputAd(Base):
    __tablename__ = "input_ads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ── Advertiser info ───────────────────────────────────────────────────────
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # ── Ad content ────────────────────────────────────────────────────────────
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_kes: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )  # stored as string to allow "KES 850/L" or "Call for price"
    location: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # "Available in Nakuru, Eldoret"
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Targeting ─────────────────────────────────────────────────────────────
    # JSON-encoded list, e.g. '["pest", "disease"]' — card categories
    # where this ad should appear
    target_categories_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default="[]"
    )
    # JSON-encoded list of crop names, e.g. '["maize", "beans"]'
    target_crops_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default="[]"
    )
    # If True, ad appears on all cards regardless of category/crop
    is_general: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Contact ───────────────────────────────────────────────────────────────
    whatsapp_link: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Subscription ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    listing_expires: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ── Analytics ─────────────────────────────────────────────────────────────
    impression_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # times ad was shown
    click_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # times WhatsApp/phone tapped

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<InputAd id={self.id} product={self.product_name!r} "
            f"business={self.business_name!r}>"
        )
