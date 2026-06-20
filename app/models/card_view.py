"""
app/models/card_view.py — Farmer card engagement tracking.

Records when a farmer opens a solution card. This data drives:
  1. Officer analytics: which card topics generate contact clicks
  2. Trending problems: what farmers in a county are dealing with now
  3. Card ranking: popular cards bubble up in browse results
  4. NGO/government data reports: county-level problem heatmaps

Schema is intentionally lean — no personal farmer data beyond
device_id and county. Farmers are anonymous users.

We do NOT track officer contact clicks here (those go on the officer
model's contact_clicks field, incremented via a separate endpoint).
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CardView(Base):
    __tablename__ = "card_views"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Which card was viewed
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("solution_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Anonymous farmer identity — device UUID only, no personal data
    device_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # County at time of view — allows regional analytics without
    # joining to the farmers table on every analytics query
    county: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)

    # Timestamp — date-level aggregation is sufficient for analytics
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<CardView card_id={self.card_id} county={self.county!r} "
            f"at={self.viewed_at.date()}>"
        )
