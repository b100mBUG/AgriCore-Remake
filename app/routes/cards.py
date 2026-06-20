"""
app/routes/cards.py — Solution card endpoints (farmer-facing).

All endpoints are read-only from the farmer's perspective. Cards are
produced by the AI classifier — farmers only browse and read them.

Caching strategy
────────────────
  Browse lists   → cached 10min per category+crop+county+page combo
  Card detail    → NOT cached (view_count must increment on every real view)
  Search results → NOT cached (too many query permutations)

The browse cache is the most impactful — it's the most frequent call
and the result set changes only when new cards are published (hourly).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import Pagination, require_admin
from app.core.limiter import limiter
from app.core.redis import cache_get, cache_set
from app.models.solution_card import CardCategory
from app.schemas.card import BrowseResponse, CardDetailResponse, SearchResponse
from app.services.card_service import browse_cards, get_card_detail, search_cards

log = logging.getLogger("agricore.routes.cards")
router = APIRouter(prefix="/cards", tags=["Cards"])

_BROWSE_CACHE_TTL = 600      # 10 minutes


# ── Browse ─────────────────────────────────────────────────────────────────────

@router.get("/", response_model=BrowseResponse)
@limiter.limit("120/minute")
async def browse(
    request: Request,
    category: CardCategory | None = Query(None, description="Filter by problem category"),
    crop: str | None = Query(None, description="Filter by crop name (e.g. 'maize')"),
    county: str | None = Query(None, description="Farmer's county — used for region ranking"),
    page: Pagination = Depends(Pagination),
    db: AsyncSession = Depends(get_db),
) -> BrowseResponse:
    """Browse solution cards by category and crop.

    This is the main farmer entry point — called when they tap a category
    tile on the home screen.

    Query params:
      category → pest | disease | soil | livestock | weather | input | harvest
      crop     → maize | tomatoes | beans | kale | ... | (omit for all crops)
      county   → farmer's county for region-boosted ranking (e.g. "Nakuru")
      offset   → pagination offset (default 0)
      limit    → page size (default 20, max 100)

    Cards are returned ordered by: region match, confidence, popularity.
    """
    cat_str = category.value if category else "all"
    cache_key = (
        f"cards:{cat_str}:{crop or 'all'}:"
        f"{county or 'all'}:{page.offset}:{page.limit}"
    )
    cached = await cache_get(cache_key)
    if cached:
        return BrowseResponse(**cached)

    result = await browse_cards(
        db,
        category=category,
        crop=crop,
        county=county,
        offset=page.offset,
        limit=page.limit,
    )

    await cache_set(cache_key, result.model_dump(), ttl=_BROWSE_CACHE_TTL)
    return result


# ── Search ─────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=SearchResponse)
@limiter.limit("60/minute")
async def search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100, description="Search query"),
    county: str | None = Query(None),
    page: Pagination = Depends(Pagination),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Keyword search over solution cards.

    Searches across: title, crop name, and card content (text match).
    Case-insensitive. Returns cards ranked by confidence and popularity.

    The farmer types "FAW maize" or "yellowing leaves" — they get
    matching cards, never a chat prompt.
    """
    return await search_cards(
        db,
        q,
        county=county,
        offset=page.offset,
        limit=page.limit,
    )


# ── Card detail ────────────────────────────────────────────────────────────────

@router.get("/{card_id}", response_model=CardDetailResponse)
@limiter.limit("120/minute")
async def get_card(
    request: Request,
    card_id: int,
    device_id: str | None = Query(None, description="Farmer device ID for analytics"),
    county: str | None = Query(None, description="Farmer county for officer matching"),
    db: AsyncSession = Depends(get_db),
) -> CardDetailResponse:
    """Get full card detail with matched officers and contextual ads.

    This single endpoint returns everything the farmer needs when they
    tap a card tile:
      - Full card content (shape depends on card_kind — see
        app/schemas/card_content.py)
      - Up to 2 matched extension officers (by county + specialization)
      - Up to 2 contextual input ads (relevant to card category)

    Also increments view_count and logs a CardView analytics row.
    Not cached — view tracking must fire on every real view.
    """
    result = await get_card_detail(
        db,
        card_id,
        device_id=device_id,
        county=county,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Card not found or not published.",
        )
    return result


# ── Admin: publish / archive cards ────────────────────────────────────────────

@router.patch(
    "/{card_id}/publish",
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def publish_card(
    card_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Admin: move a card from 'review' or 'draft' to 'published'.

    Cards in review status have confidence below the threshold and need
    admin approval before going live. Admins can approve them here.
    """
    from sqlalchemy import select, update
    from app.models.solution_card import SolutionCard, CardStatus

    result = await db.execute(
        select(SolutionCard).where(SolutionCard.id == card_id)
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")

    card.status = CardStatus.published
    await db.commit()
    log.info("Card published by admin: id=%d", card_id)


@router.patch(
    "/{card_id}/archive",
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_card(
    card_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Admin: archive a card (remove from browsing without deleting)."""
    from sqlalchemy import select
    from app.models.solution_card import SolutionCard, CardStatus

    result = await db.execute(
        select(SolutionCard).where(SolutionCard.id == card_id)
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found.")

    card.status = CardStatus.archived
    await db.commit()
    log.info("Card archived by admin: id=%d", card_id)
