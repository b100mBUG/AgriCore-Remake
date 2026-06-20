"""
app/models/raw_content.py — Crawler output table.

Every page the crawler fetches lands here first. The AI classifier
job reads rows where status="pending", processes them, and either:
  - Creates a SolutionCard and sets status="processed"
  - Sets status="rejected" if the content isn't relevant to East
    African smallholder farming

This two-table design lets us:
  1. Re-run the classifier on raw content if the prompt improves
  2. Audit what was crawled vs what made it into the knowledge base
  3. Avoid re-crawling pages we've already seen (url uniqueness)

Status lifecycle
────────────────
  pending   → crawled, waiting for AI classification
  processed → classifier created a SolutionCard from this content
  rejected  → classifier determined content is not relevant
  error     → classifier failed (will retry on next run)
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ContentStatus(str, enum.Enum):
    pending   = "pending"
    processed = "processed"
    rejected  = "rejected"
    error     = "error"


class RawContent(Base):
    __tablename__ = "raw_content"

    __table_args__ = (
        # Prevent storing the same URL twice — crawler checks this
        UniqueConstraint("url", name="uq_raw_content_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Source page
    url: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    source_domain: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )  # e.g. "kalro.org" — used to filter/prioritise sources

    # Extracted content
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # full visible text, HTML stripped

    # HTTP metadata — used to detect changes on re-crawl
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # SHA-256 of body — skip classifier if unchanged

    # Processing state
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus),
        nullable=False,
        default=ContentStatus.pending,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # classifier error retries
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    classified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<RawContent id={self.id} status={self.status} "
            f"domain={self.source_domain!r}>"
        )
