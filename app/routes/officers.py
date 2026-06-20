"""
app/routes/officers.py — Extension officer endpoints.

Two access levels only:
  Public (no auth) → directory, profile view, contact click tracking
  Admin  (JWT)      → create, update, verify, set tier, upload photo

There is no officer login. Officers never hold a credential in this
system — the admin creates and maintains every profile on their
behalf, typically after vetting them by phone or in person. This is
intentional for the MVP: one fewer account type, one fewer auth flow,
one fewer thing that can be phished or leaked.

The officer directory is the public face of the platform — farmers
browse it to find specialists. Officer profiles are SEO-optimised
(detailed descriptions, county, specialization) to drive organic traffic.
"""

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import Pagination, require_admin
from app.core.limiter import limiter
from app.core.redis import cache_delete, cache_get, cache_set
from app.models.officer import ExtensionOfficer, OfficerTier
from app.schemas.officer import (
    OfficerAdminOut,
    OfficerCreate,
    OfficerProfileUpdate,
    OfficerPublicOut,
)
from app.services.cloudinary_service import upload_officer_photo

log = logging.getLogger("agricore.routes.officers")
router = APIRouter(prefix="/officers", tags=["Officers"])

_PROFILE_CACHE_TTL = 600      # 10 minutes
_DIRECTORY_CACHE_TTL = 300    # 5 minutes
_MAX_PHOTO_SIZE_MB = 5


# ── Admin: profile management ───────────────────────────────────────────────────

@router.post(
    "/",
    dependencies=[Depends(require_admin)],
    response_model=OfficerAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_officer(
    body: OfficerCreate,
    db: AsyncSession = Depends(get_db),
) -> OfficerAdminOut:
    """Admin: create a new extension officer profile.

    New officers start on the free tier and unverified. Admin upgrades
    tier and verification status separately once vetted, and once any
    off-platform subscription payment is confirmed.
    """
    if body.email:
        existing = await db.execute(
            select(ExtensionOfficer).where(ExtensionOfficer.email == body.email)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An officer profile with this email already exists.",
            )

    officer = ExtensionOfficer(
        full_name=body.full_name,
        email=body.email,
        county=body.county,
        title=body.title,
        specialization=body.specialization,
        phone_number=body.phone_number,
        tier=OfficerTier.free,
    )
    db.add(officer)
    await db.commit()
    await db.refresh(officer)
    log.info("Officer created by admin: id=%d county=%s", officer.id, officer.county)
    return OfficerAdminOut.model_validate(officer)


@router.patch(
    "/{officer_id}",
    dependencies=[Depends(require_admin)],
    response_model=OfficerAdminOut,
)
async def update_officer(
    officer_id: int,
    body: OfficerProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> OfficerAdminOut:
    """Admin: update an officer's profile fields.

    Only provided fields are updated (partial PATCH semantics).
    Invalidates the officer's public profile cache on change.
    """
    result = await db.execute(
        select(ExtensionOfficer).where(ExtensionOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found.")

    update_data = body.model_dump(exclude_none=True)

    if "crops" in update_data:
        import json
        update_data["crops_json"] = json.dumps(update_data.pop("crops"))

    for field, value in update_data.items():
        if hasattr(officer, field):
            setattr(officer, field, value)

    await db.commit()
    await db.refresh(officer)

    await cache_delete(f"officer_profile:{officer.id}")
    log.info("Officer profile updated by admin: id=%d", officer.id)
    return OfficerAdminOut.model_validate(officer)


@router.post(
    "/{officer_id}/photo",
    dependencies=[Depends(require_admin)],
    response_model=OfficerAdminOut,
)
async def upload_officer_photo_admin(
    officer_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> OfficerAdminOut:
    """Admin: upload/replace an officer's profile photo.

    Accepts JPEG, PNG, or WebP, max 5MB. Uploaded to Cloudinary; the
    URL is saved on the profile.
    """
    result = await db.execute(
        select(ExtensionOfficer).where(ExtensionOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found.")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only JPEG, PNG, or WebP images are accepted.",
        )

    file_bytes = await file.read()

    max_bytes = _MAX_PHOTO_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Photo must be under {_MAX_PHOTO_SIZE_MB}MB.",
        )

    photo_url = await upload_officer_photo(file_bytes, officer.id)
    officer.photo_url = photo_url
    await db.commit()
    await db.refresh(officer)

    await cache_delete(f"officer_profile:{officer.id}")
    log.info("Officer photo updated by admin: id=%d url=%s", officer.id, photo_url)
    return OfficerAdminOut.model_validate(officer)


@router.delete(
    "/{officer_id}",
    dependencies=[Depends(require_admin)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_officer(
    officer_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Admin: deactivate an officer profile (soft delete — keeps history).

    Deactivated officers stop appearing in the public directory and
    card-detail recommendations immediately (cache is invalidated).
    """
    result = await db.execute(
        select(ExtensionOfficer).where(ExtensionOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found.")

    officer.is_active = False
    await db.commit()
    await cache_delete(f"officer_profile:{officer_id}")
    log.info("Officer deactivated by admin: id=%d", officer_id)


# ── Public directory ───────────────────────────────────────────────────────────

@router.get("/", response_model=list[OfficerPublicOut])
@limiter.limit("60/minute")
async def list_officers(
    request: Request,
    county: str | None = None,
    specialization: str | None = None,
    page: Pagination = Depends(Pagination),
    db: AsyncSession = Depends(get_db),
) -> list[OfficerPublicOut]:
    """Browse the extension officer directory.

    Filterable by county and specialization (case-insensitive). 
    Featured (pro) officers appear first. 
    """
    # Clean input strings to prevent casing discrepancies in cache keys
    clean_county = county.strip().lower() if county else "all"
    clean_spec = specialization.strip().lower() if specialization else "all"
    
    # Base cache key on normalized lowercase strings
    cache_key = f"officers:{clean_county}:{clean_spec}:{page.offset}:{page.limit}"
    
    cached = await cache_get(cache_key)
    if cached:
        return cached

    query = (
        select(ExtensionOfficer)
        .where(
            ExtensionOfficer.is_active == True,  # noqa: E712
            ExtensionOfficer.tier != OfficerTier.free, # Keep this if free tier is hidden
        )
        .order_by(
            ExtensionOfficer.is_featured.desc(),
            ExtensionOfficer.profile_views.desc(),
        )
        .offset(page.offset)
        .limit(page.limit)
    )

    # Use .ilike() for case-insensitive database matching
    if county:
        query = query.where(ExtensionOfficer.county.ilike(county.strip()))
    if specialization:
        query = query.where(ExtensionOfficer.specialization.ilike(specialization.strip()))

    result = await db.execute(query)
    officers = result.scalars().all()
    out = [OfficerPublicOut.model_validate(o) for o in officers]

    await cache_set(cache_key, [o.model_dump() for o in out], ttl=_DIRECTORY_CACHE_TTL)
    return out

@router.get("/{officer_id}", response_model=OfficerPublicOut)
@limiter.limit("120/minute")
async def get_officer_profile(
    request: Request,
    officer_id: int,
    db: AsyncSession = Depends(get_db),
) -> OfficerPublicOut:
    """Get a single officer's public profile.

    Called when farmer taps an officer card. Cached per officer for 10min.
    """
    cache_key = f"officer_profile:{officer_id}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    result = await db.execute(
        select(ExtensionOfficer).where(
            ExtensionOfficer.id == officer_id,
            ExtensionOfficer.is_active == True,  # noqa: E712
        )
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Officer not found.",
        )

    out = OfficerPublicOut.model_validate(officer)
    await cache_set(cache_key, out.model_dump(), ttl=_PROFILE_CACHE_TTL)
    return out


@router.post("/{officer_id}/contact-click", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def record_contact_click(
    request: Request,
    officer_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Record that a farmer clicked a contact link (WhatsApp / call / social).

    Called by the app when any contact button is tapped. Increments the
    officer's contact_clicks counter — this feeds their analytics dashboard.
    Fire-and-forget from the app's perspective (no response body needed).
    """
    await db.execute(
        update(ExtensionOfficer)
        .where(ExtensionOfficer.id == officer_id)
        .values(contact_clicks=ExtensionOfficer.contact_clicks + 1)
    )
    await db.commit()


# ── Admin: verification & subscription tier ─────────────────────────────────────

@router.patch("/{officer_id}/verify", dependencies=[Depends(require_admin)], response_model=OfficerAdminOut)
async def verify_officer(
    officer_id: int,
    db: AsyncSession = Depends(get_db),
) -> OfficerAdminOut:
    """Admin: mark an officer as verified (credentials checked)."""
    result = await db.execute(
        select(ExtensionOfficer).where(ExtensionOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found.")

    officer.is_verified = True
    await db.commit()
    await db.refresh(officer)
    await cache_delete(f"officer_profile:{officer_id}")
    log.info("Officer verified by admin: id=%d", officer_id)
    return OfficerAdminOut.model_validate(officer)


@router.patch("/{officer_id}/tier", dependencies=[Depends(require_admin)], response_model=OfficerAdminOut)
async def set_officer_tier(
    officer_id: int,
    tier: OfficerTier,
    db: AsyncSession = Depends(get_db),
) -> OfficerAdminOut:
    """Admin: upgrade or downgrade an officer's subscription tier.

    Subscription payment is handled offline for this MVP (no officer
    billing flow); the admin reflects payment status here once confirmed.
    """
    result = await db.execute(
        select(ExtensionOfficer).where(ExtensionOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found.")

    officer.tier = tier
    await db.commit()
    await db.refresh(officer)
    await cache_delete(f"officer_profile:{officer_id}")
    log.info("Officer tier updated: id=%d tier=%s", officer_id, tier)
    return OfficerAdminOut.model_validate(officer)
