"""
app/models/solution_card.py — The core knowledge unit.

SolutionCards are what farmers see. The OUTER shell is fixed for every
card (title, category, crop, region, confidence, status, provenance) —
that's what makes cards predictable to browse, filter, and rank. The
INNER content shape varies by `card_kind`, because "identify → treat →
prevent" fits a pest outbreak but is a poor fit for "how to raise dairy
goats" or "this week's rainfall outlook." Fluid content, specific shell.

Card kinds
──────────
  problem   → identify / treat / prevent
              For: pest, disease, soil deficiencies/erosion.
              Something is wrong; farmer needs to recognise it, act,
              and stop it recurring.

  practice  → overview / steps / tips
              For: livestock husbandry, harvest & post-harvest handling,
              general crop practice. Not a problem to fix — a skill or
              routine to follow (e.g. "feeding dairy cattle in dry season").

  advisory  → summary / recommended_actions / risk_level
              For: weather-driven alerts and seasonal guidance.
              Time-bound, action-oriented, not "identify a disease."

  input     → product_overview / usage / cautions
              For: fertiliser, pesticide, seed variety guidance tied to
              a specific input product or category, not a symptom.

Each kind's shape is enforced in app/schemas/card_content.py (Pydantic),
stored as a single JSON blob in `content`. The classifier picks the
right card_kind from the category it assigns; the kind→shape mapping
lives in CARD_KIND_BY_CATEGORY below so it's defined once, not duplicated
between the model, the classifier prompt, and the schemas.

Card matching algorithm
────────────────────────
When a farmer opens a category (e.g. "Pests"), the browse endpoint:
  1. Filters by category + crop (exact crop or "general")
  2. Boosts cards matching the farmer's region
  3. Orders by: region match DESC, confidence DESC, created_at DESC
  4. Appends matched extension officers at the end of each card response

Categories
──────────
  pest       → insects, worms, rodents                    [problem]
  disease    → fungal, bacterial, viral, deficiency        [problem]
  soil       → pH, nutrients, erosion, compaction          [problem]
  livestock  → cattle, goats, poultry, pigs husbandry       [practice]
  weather    → drought, frost, flood, wind advisories       [advisory]
  input      → fertiliser, pesticide, seed variety advice   [input]
  harvest    → post-harvest, storage, grading               [practice]

Status lifecycle
────────────────
  draft      → just created by classifier, not yet visible
  published  → live, visible to farmers
  review     → flagged for admin review (low confidence or reported)
  archived   → removed from browsing, kept for audit
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CardCategory(str, enum.Enum):
    pest      = "pest"
    disease   = "disease"
    soil      = "soil"
    livestock = "livestock"
    weather   = "weather"
    input     = "input"
    harvest   = "harvest"


class CardKind(str, enum.Enum):
    """Which content shape this card uses. See module docstring."""
    problem  = "problem"
    practice = "practice"
    advisory = "advisory"
    input    = "input"


# Single source of truth for category → default content shape.
# The classifier uses this to pick a kind from the category it assigns,
# so the mapping is never hand-duplicated in the prompt.
CARD_KIND_BY_CATEGORY: dict[CardCategory, CardKind] = {
    CardCategory.pest:      CardKind.problem,
    CardCategory.disease:   CardKind.problem,
    CardCategory.soil:      CardKind.problem,
    CardCategory.livestock: CardKind.practice,
    CardCategory.harvest:   CardKind.practice,
    CardCategory.weather:   CardKind.advisory,
    CardCategory.input:     CardKind.input,
}


class CardStatus(str, enum.Enum):
    draft     = "draft"
    published = "published"
    review    = "review"
    archived  = "archived"


# Bumped only if the JSON shape of `content` changes in a way that old
# rows can't be read by the new frontend without a migration/backfill.
CURRENT_CONTENT_SCHEMA_VERSION = 1


class SolutionCard(Base):
    __tablename__ = "solution_cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ── Content shell (fixed for every card) ────────────────────────────────────
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)

    # Which shape `content` follows — see CardKind / module docstring.
    card_kind: Mapped[CardKind] = mapped_column(
        Enum(CardKind), nullable=False, index=True
    )

    # The fluid part. Keys depend on card_kind; validated against the
    # matching Pydantic model in app/schemas/card_content.py before save.
    content: Mapped[dict] = mapped_column(JSON, nullable=False)

    content_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=CURRENT_CONTENT_SCHEMA_VERSION
    )

    # Optional supplementary detail — AI may include dosage tables, etc.
    # Free text, applies to any card_kind.
    extra_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # ── Classification ────────────────────────────────────────────────────────
    category: Mapped[CardCategory] = mapped_column(
        Enum(CardCategory), nullable=False, index=True
    )
    crop: Mapped[str] = mapped_column(
        String(120), nullable=False, index=True, default="general"
    )  # "maize" | "tomatoes" | "dairy cattle" | "general" (applies broadly)

    # Region allows county-specific advice (e.g. "Coast" vs "Rift Valley")
    region: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )  # None = applies nationally

    # ── AI metadata ───────────────────────────────────────────────────────────
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )  # 0.0–1.0, set by the classifier
    ai_model_version: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )  # e.g. "llama-3.3-70b-versatile" — track which model generated this

    # ── Provenance ────────────────────────────────────────────────────────────
    source_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )  # original article URL — shown in card footer for credibility
    raw_content_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("raw_content.id", ondelete="SET NULL"), nullable=True
    )
    raw_content: Mapped["RawContent"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "RawContent", lazy="select"
    )

    # ── Status & visibility ───────────────────────────────────────────────────
    status: Mapped[CardStatus] = mapped_column(
        Enum(CardStatus),
        nullable=False,
        default=CardStatus.draft,
        index=True,
    )

    # ── Engagement ────────────────────────────────────────────────────────────
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # how many farmers have opened this card

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

    def __repr__(self) -> str:
        return (
            f"<SolutionCard id={self.id} category={self.category} "
            f"kind={self.card_kind} crop={self.crop!r} confidence={self.confidence:.2f}>"
        )
