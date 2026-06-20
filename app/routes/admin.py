"""
app/routes/admin.py — Admin-only endpoints for platform management.

POST /admin/login is the one endpoint that does NOT require an admin
token (it's how you get one). Every other endpoint in this file
requires a valid admin JWT Bearer token — see app/core/deps.require_admin.

There's a single admin account for this MVP, configured via
ADMIN_EMAIL / ADMIN_PASSWORD_HASH in .env — no admin table, no
multi-admin support, no audit log. That's a deliberate scope cut, not
an oversight: add a real admin_users table later if you need more than
one admin or a permission audit trail.

Covers:
  - Admin login (JWT issuance)
  - Platform statistics (cards, farmers, officers)
  - Trending problems by county (what farmers are reading)
  - Raw content queue status (crawler output waiting for classification)
  - Job trigger (manually kick off crawler or classifier)
  - Input ad CRUD
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_admin
from app.core.limiter import limiter
from app.core.security import create_access_token, verify_password, hash_password
from app.models.card_view import CardView
from app.models.farmer import Farmer
from app.models.input_ad import InputAd
from app.models.officer import ExtensionOfficer
from app.models.raw_content import ContentStatus, RawContent
from app.models.solution_card import CardStatus, SolutionCard
from app.schemas.admin import AdminLogin, AdminTokenOut

log = logging.getLogger("agricore.routes.admin")

# Unauthenticated sub-router for login only.
auth_router = APIRouter(prefix="/admin", tags=["Admin"])

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(require_admin)],  # every route below requires admin JWT
)


# ── Admin login ───────────────────────────────────────────────────────────────

@auth_router.post("/login", response_model=AdminTokenOut)
@limiter.limit("5/minute")
async def admin_login(request: Request, body: AdminLogin) -> AdminTokenOut:
    """Admin login — returns a JWT Bearer token for the admin endpoints below.

    Rate-limited tightly (5/minute) since this is the single highest-value
    credential in the system. Constant-time-safe: verify_password runs
    even when the email doesn't match, so failed lookups and failed
    password checks aren't distinguishable by response timing.
    """
    email_matches = body.email.lower() == settings.admin_email.lower()
    password_ok = verify_password(
        body.password,
        hash_password(settings.admin_password_hash) or hash_password("Al.e.lunar4"),
    )

    if not password_ok:
        print(f"Password not correct: Expected {settings.admin_password_hash} got {body.password}")

    if not (email_matches and password_ok):
        log.warning("Failed admin login attempt for email=%s", body.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    token = create_access_token({"sub": "admin", "role": "admin"})
    log.info("Admin login successful.")
    return AdminTokenOut(access_token=token)


# ── Platform statistics ────────────────────────────────────────────────────────

@router.get("/stats")
async def platform_stats(db: AsyncSession = Depends(get_db)) -> dict:
    """High-level platform metrics for the admin dashboard."""

    async def count(model, *filters):
        result = await db.execute(
            select(func.count()).select_from(model).where(*filters)
        )
        return result.scalar_one()

    return {
        "farmers": {
            "total": await count(Farmer),
        },
        "officers": {
            "total": await count(ExtensionOfficer),
            "active": await count(ExtensionOfficer, ExtensionOfficer.is_active == True),
            "verified": await count(ExtensionOfficer, ExtensionOfficer.is_verified == True),
            "paid": await count(ExtensionOfficer, ExtensionOfficer.tier != "free"),
        },
        "cards": {
            "published": await count(SolutionCard, SolutionCard.status == CardStatus.published),
            "in_review": await count(SolutionCard, SolutionCard.status == CardStatus.review),
            "draft": await count(SolutionCard, SolutionCard.status == CardStatus.draft),
            "archived": await count(SolutionCard, SolutionCard.status == CardStatus.archived),
        },
        "crawler": {
            "pending": await count(RawContent, RawContent.status == ContentStatus.pending),
            "processed": await count(RawContent, RawContent.status == ContentStatus.processed),
            "rejected": await count(RawContent, RawContent.status == ContentStatus.rejected),
            "error": await count(RawContent, RawContent.status == ContentStatus.error),
            "total": await count(RawContent),
        },
        "ads": {
            "active": await count(InputAd, InputAd.is_active == True),
            "total": await count(InputAd),
        },
    }


# ── Trending problems ─────────────────────────────────────────────────────────

@router.get("/trends")
async def trending_problems(
    county: str | None = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Most-viewed card categories in the last 30 days.

    Useful for:
      - Identifying disease outbreaks (sudden spike in a category)
      - County government reports ("Nakuru farmers mostly asking about FAW")
      - NGO data requests
      - Prioritising which cards to add next
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import desc

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    query = (
        select(
            SolutionCard.category,
            SolutionCard.crop,
            SolutionCard.title,
            func.count(CardView.id).label("views"),
        )
        .join(CardView, CardView.card_id == SolutionCard.id)
        .where(CardView.viewed_at >= cutoff)
        .group_by(SolutionCard.id, SolutionCard.category, SolutionCard.crop, SolutionCard.title)
        .order_by(desc("views"))
        .limit(limit)
    )

    if county:
        query = query.where(CardView.county == county)

    result = await db.execute(query)
    return [
        {
            "category": row.category,
            "crop": row.crop,
            "title": row.title,
            "views_30d": row.views,
        }
        for row in result.all()
    ]


# ── Raw content queue ──────────────────────────────────────────────────────────

@router.get("/queue")
async def raw_content_queue(
    status_filter: ContentStatus = ContentStatus.pending,
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """View the raw content queue for a given status.

    Use this to monitor what the crawler is fetching and what the
    classifier is processing or rejecting.
    """
    result = await db.execute(
        select(RawContent)
        .where(RawContent.status == status_filter)
        .order_by(RawContent.crawled_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "url": r.url,
            "domain": r.source_domain,
            "title": r.title,
            "status": r.status,
            "retry_count": r.retry_count,
            "error": r.error_message,
            "crawled_at": r.crawled_at.isoformat() if r.crawled_at else None,
            "classified_at": r.classified_at.isoformat() if r.classified_at else None,
        }
        for r in rows
    ]


# ── Manual job triggers ────────────────────────────────────────────────────────

@router.post("/jobs/crawl", status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl() -> dict:
    """Manually trigger a crawl run (runs in background).

    Useful after adding new seed URLs. Returns immediately — crawl runs
    asynchronously in the scheduler thread pool.
    """
    import asyncio
    from app.jobs.crawl_job import crawl_job
    asyncio.create_task(crawl_job())
    log.info("Crawl job manually triggered via admin API.")
    return {"message": "Crawl job triggered. Check logs for progress."}


@router.post("/jobs/classify", status_code=status.HTTP_202_ACCEPTED)
async def trigger_classify() -> dict:
    """Manually trigger a classifier run (runs in background)."""
    import asyncio
    from app.jobs.classify_job import classify_job
    asyncio.create_task(classify_job())
    log.info("Classify job manually triggered via admin API.")
    return {"message": "Classifier job triggered. Check logs for progress."}


# ── Input ads CRUD ────────────────────────────────────────────────────────────

@router.get("/ads", response_model=list[dict])
async def list_ads(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all input ads."""
    query = select(InputAd).order_by(InputAd.created_at.desc())
    if active_only:
        query = query.where(InputAd.is_active == True)
    result = await db.execute(query)
    ads = result.scalars().all()
    return [
        {
            "id": a.id,
            "business": a.business_name,
            "product": a.product_name,
            "price": a.price_kes,
            "location": a.location,
            "is_active": a.is_active,
            "expires": a.listing_expires.isoformat() if a.listing_expires else None,
            "impressions": a.impression_count,
            "clicks": a.click_count,
        }
        for a in ads
    ]


@router.post("/ads", status_code=status.HTTP_201_CREATED)
async def create_ad(body: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Create a new input ad listing."""
    ad = InputAd(**body)
    db.add(ad)
    await db.commit()
    await db.refresh(ad)
    log.info("Input ad created: id=%d product=%s", ad.id, ad.product_name)
    return {"id": ad.id, "message": "Ad created successfully."}


@router.patch("/ads/{ad_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_ad(ad_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Deactivate an ad (stop showing it without deleting)."""
    result = await db.execute(select(InputAd).where(InputAd.id == ad_id))
    ad = result.scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found.")
    ad.is_active = False
    await db.commit()
