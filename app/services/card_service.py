"""
app/services/card_service.py — Solution card business logic.

All DB queries for cards live here. Routes are thin — they call these
functions and return the result. No SQLAlchemy in route files.

Officer matching algorithm
──────────────────────────
When a farmer opens a card, we suggest up to 2 extension officers:
  1. county == farmer.county (passed as query param from app)
  2. specialization matches card.category or "general"
  3. is_featured=True first (pro tier perk)
  4. Then by profile_views DESC (social proof)
  5. is_active=True and effective_tier != free (basic/pro have profiles)

Ad injection
────────────
Ads appear at card detail level. We match ads by:
  - target_categories_json contains card.category  OR
  - is_general=True
  - is_active=True and listing not expired
Up to 2 ads per card.

Search
──────
Simple LIKE-based full-text search over title + crop + the content JSON
blob (as raw text). Good enough for MVP. If the card volume grows
(10k+), migrate to PostgreSQL full-text search (tsvector) or a search
service.
"""

import json
import logging
from datetime import date

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.input_ad import InputAd
from app.models.officer import ExtensionOfficer, OfficerTier
from app.models.solution_card import CardCategory, CardStatus, SolutionCard
from app.models.card_view import CardView
from app.schemas.card import (
    AdSummary,
    BrowseResponse,
    CardDetailResponse,
    SearchResponse,
    SolutionCardSummary,
)

log = logging.getLogger("agricore.cards")


# ── Browse ────────────────────────────────────────────────────────────────────

async def browse_cards(
    db: AsyncSession,
    *,
    category: CardCategory | None = None,
    crop: str | None = None,
    county: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> BrowseResponse:
    """Return paginated published cards, filtered and ranked.

    Ranking order:
      1. Region matches farmer's county (boost)
      2. Confidence DESC
      3. view_count DESC (popular cards first)
      4. created_at DESC (freshest)
    """
    base_query = (
        select(SolutionCard)
        .where(SolutionCard.status == CardStatus.published)
    )

    if category:
        base_query = base_query.where(SolutionCard.category == category)

    if crop:
        base_query = base_query.where(
            or_(
                SolutionCard.crop == crop.lower(),
                SolutionCard.crop == "general",
            )
        )

    # Region boost: cards matching county come before national cards.
    # We use a CASE expression equivalent — order by region match first.
    if county:
        from sqlalchemy import case
        region_rank = case(
            (SolutionCard.region == county, 0),
            (SolutionCard.region.is_(None), 1),
            else_=2,
        )
        base_query = base_query.order_by(
            region_rank,
            SolutionCard.confidence.desc(),
            SolutionCard.view_count.desc(),
            SolutionCard.created_at.desc(),
        )
    else:
        base_query = base_query.order_by(
            SolutionCard.confidence.desc(),
            SolutionCard.view_count.desc(),
            SolutionCard.created_at.desc(),
        )

    # Count total for pagination
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Fetch page
    result = await db.execute(base_query.offset(offset).limit(limit))
    cards = result.scalars().all()

    return BrowseResponse(
        items=[SolutionCardSummary.model_validate(c) for c in cards],
        total=total,
        offset=offset,
        limit=limit,
    )


# ── Card detail ───────────────────────────────────────────────────────────────

async def get_card_detail(
    db: AsyncSession,
    card_id: int,
    *,
    device_id: str | None = None,
    county: str | None = None,
) -> CardDetailResponse | None:
    """Return full card + matched officers + contextual ads.

    Also increments view_count and logs a CardView row for analytics.
    """
    result = await db.execute(
        select(SolutionCard).where(
            SolutionCard.id == card_id,
            SolutionCard.status == CardStatus.published,
        )
    )
    card = result.scalar_one_or_none()
    if not card:
        return None

    # ── Increment view count ───────────────────────────────────────────────
    await db.execute(
        update(SolutionCard)
        .where(SolutionCard.id == card_id)
        .values(view_count=SolutionCard.view_count + 1)
    )

    # ── Log card view for analytics ────────────────────────────────────────
    if device_id:
        db.add(CardView(card_id=card_id, device_id=device_id, county=county))

    await db.commit()

    # ── Match extension officers ───────────────────────────────────────────
    officers = await _match_officers(db, card=card, county=county)

    # ── Inject ads ─────────────────────────────────────────────────────────
    ads = await _get_contextual_ads(db, card=card)

    from app.schemas.card import SolutionCardDetail
    from app.schemas.officer import OfficerPublicOut

    return CardDetailResponse(
        card=SolutionCardDetail.model_validate(card),
        specialists=[OfficerPublicOut.model_validate(o) for o in officers],
        ads=[AdSummary.model_validate(a) for a in ads],
    )


# ── Officer matching ───────────────────────────────────────────────────────────

async def _match_officers(
    db: AsyncSession,
    card: SolutionCard,
    county: str | None,
) -> list[ExtensionOfficer]:
    """Return up to 2 matched extension officers for this card.

    Matching criteria (in priority order):
      1. Same county as farmer (if county provided)
      2. Specialization matches card category, or "general"
      3. Paid tier (basic or pro) — free officers don't show profiles
      4. Featured first, then by profile_views
    """
    query = (
        select(ExtensionOfficer)
        .where(
            ExtensionOfficer.is_active == True,  # noqa: E712
            ExtensionOfficer.tier != OfficerTier.free,
            or_(
                ExtensionOfficer.specialization == card.category.value,
                ExtensionOfficer.specialization == "general",
                ExtensionOfficer.specialization.is_(None),
            ),
        )
        .order_by(
            ExtensionOfficer.is_featured.desc(),
            ExtensionOfficer.profile_views.desc(),
        )
        .limit(2)
    )

    if county:
        # Prefer county match — run county-scoped query first
        county_result = await db.execute(
            query.where(ExtensionOfficer.county == county)
        )
        county_officers = county_result.scalars().all()
        if county_officers:
            # Increment profile_views for shown officers
            for officer in county_officers:
                officer.profile_views += 1
            await db.commit()
            return county_officers

    # Fallback: any county
    result = await db.execute(query)
    officers = result.scalars().all()
    for officer in officers:
        officer.profile_views += 1
    await db.commit()
    return officers


# ── Contextual ads ─────────────────────────────────────────────────────────────

async def _get_contextual_ads(db: AsyncSession, card: SolutionCard) -> list[InputAd]:
    """Return up to 2 ads relevant to this card's category."""
    today = date.today()

    result = await db.execute(
        select(InputAd)
        .where(
            InputAd.is_active == True,  # noqa: E712
            or_(
                InputAd.listing_expires.is_(None),
                InputAd.listing_expires >= today,
            ),
            or_(
                InputAd.is_general == True,  # noqa: E712
                InputAd.target_categories_json.contains(card.category.value),
            ),
        )
        .order_by(InputAd.impression_count.asc())  # rotate least-shown ads first
        .limit(2)
    )
    ads = result.scalars().all()

    # Increment impression count
    for ad in ads:
        ad.impression_count += 1
    if ads:
        await db.commit()

    return ads


# ── Search ────────────────────────────────────────────────────────────────────

async def search_cards(
    db: AsyncSession,
    query: str,
    *,
    county: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> SearchResponse:
    """Keyword search over card title, crop, and content.

    Case-insensitive LIKE search, including a raw text match against the
    JSON `content` column — crude (it'll match key names too, e.g. a
    search for "steps" matches every practice card) but functional on
    both SQLite and PostgreSQL without extra plumbing. Works on SQLite
    and PostgreSQL. For PostgreSQL at scale, consider tsvector full-text
    search over title + a flattened version of content instead.
    """
    if not query or not query.strip():
        return SearchResponse(items=[], query=query, total=0)

    term = f"%{query.strip().lower()}%"

    search_filter = or_(
        func.lower(SolutionCard.title).like(term),
        func.lower(cast(SolutionCard.content, String)).like(term),
        func.lower(SolutionCard.crop).like(term),
    )

    base_q = (
        select(SolutionCard)
        .where(
            SolutionCard.status == CardStatus.published,
            search_filter,
        )
        .order_by(
            SolutionCard.confidence.desc(),
            SolutionCard.view_count.desc(),
        )
    )

    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(base_q.offset(offset).limit(limit))
    cards = result.scalars().all()

    return SearchResponse(
        items=[SolutionCardSummary.model_validate(c) for c in cards],
        query=query,
        total=total,
    )
