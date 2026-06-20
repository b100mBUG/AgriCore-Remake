"""
app/schemas/card.py — Pydantic v2 schemas for solution card endpoints.

The card is the core product unit. Every schema here is read-only
from the farmer's perspective — farmers never create or edit cards.

BrowseResponse bundles cards + matched officers + relevant ads in a
single payload so the frontend makes one API call per browse action.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.solution_card import CardCategory, CardKind
from app.schemas.card_content import CardContent
from app.schemas.officer import OfficerPublicOut


class SolutionCardSummary(BaseModel):
    """Compact card for list views — no full text, just enough to render
    a card tile in the browse grid."""

    model_config = {"from_attributes": True}

    id: int
    title: str
    category: CardCategory
    card_kind: CardKind
    crop: str
    region: str | None
    view_count: int
    created_at: datetime


class SolutionCardDetail(BaseModel):
    """Full card content — returned when farmer taps a card tile."""

    model_config = {"from_attributes": True}

    id: int
    title: str
    category: CardCategory
    card_kind: CardKind
    crop: str
    region: str | None

    # The fluid part — shape depends on card_kind, validated at read time
    # so a malformed row in the DB raises loudly instead of confusing
    # the frontend with unexpected fields.
    content: CardContent
    extra_notes: str | None

    # Provenance — shown in card footer for credibility
    source_url: str | None

    confidence: float
    view_count: int
    created_at: datetime


class AdSummary(BaseModel):
    """Slim ad payload — enough to render a sponsored card tile."""

    model_config = {"from_attributes": True}

    id: int
    business_name: str
    product_name: str
    description: str | None
    price_kes: str | None
    location: str | None
    photo_url: str | None
    whatsapp_link: str | None
    phone_number: str | None


class CardDetailResponse(BaseModel):
    """Full response when a farmer opens a card.

    Bundles the card content, matched specialists, and contextual ads
    in one payload — no extra round trips from the app.
    """

    card: SolutionCardDetail
    specialists: list[OfficerPublicOut] = Field(default_factory=list)
    ads: list[AdSummary] = Field(default_factory=list)


class BrowseResponse(BaseModel):
    """Paginated list of card summaries for the browse screen."""

    items: list[SolutionCardSummary]
    total: int
    offset: int
    limit: int


class SearchResponse(BaseModel):
    """Keyword search results."""

    items: list[SolutionCardSummary]
    query: str
    total: int
