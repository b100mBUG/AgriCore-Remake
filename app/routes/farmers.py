"""
app/routes/farmers.py — Farmer profile endpoints.

Farmers are anonymous users identified by device_id. These endpoints
are called by the mobile app on first launch (register) and whenever
the farmer updates their profile.

Rate limiting: 30/minute (lower than default — profile ops are infrequent)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import Pagination
from app.core.limiter import limiter
from app.models.farmer import Farmer
from app.schemas.farmer import FarmerOut, FarmerRegister, FarmerUpdate

log = logging.getLogger("agricore.routes.farmers")
router = APIRouter(prefix="/farmers", tags=["Farmers"])


@router.post("/register", response_model=FarmerOut, status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")
async def register_or_update_farmer(
    request: Request,
    body: FarmerRegister,
    db: AsyncSession = Depends(get_db),
) -> FarmerOut:
    """Register a new farmer or update existing profile by device_id.

    Acts as an upsert — if the device_id already exists, the profile
    is updated with any provided fields. Safe to call on every app launch.

    The mobile app should call this:
      - On first launch (creates profile)
      - After the farmer completes onboarding (updates county + crop)
      - Whenever the farmer changes their profile in settings
    """
    result = await db.execute(
        select(Farmer).where(Farmer.device_id == body.device_id)
    )
    farmer = result.scalar_one_or_none()

    if farmer is None:
        # New farmer
        farmer = Farmer(**body.model_dump())
        db.add(farmer)
        log.info("New farmer registered: device_id=%s county=%s", body.device_id, body.county)
    else:
        # Update existing
        update_data = body.model_dump(exclude={"device_id"}, exclude_none=True)
        for field, value in update_data.items():
            setattr(farmer, field, value)
        log.debug("Farmer profile updated: device_id=%s", body.device_id)

    await db.commit()
    await db.refresh(farmer)
    return FarmerOut.model_validate(farmer)


@router.get("/{device_id}", response_model=FarmerOut)
@limiter.limit("60/minute")
async def get_farmer_profile(
    request: Request,
    device_id: str,
    db: AsyncSession = Depends(get_db),
) -> FarmerOut:
    """Retrieve a farmer profile by device_id.

    Used by the app on launch to restore saved profile.
    Returns 404 if the device has never registered.
    """
    result = await db.execute(
        select(Farmer).where(Farmer.device_id == device_id)
    )
    farmer = result.scalar_one_or_none()

    if not farmer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Farmer profile not found. Please complete onboarding.",
        )
    return FarmerOut.model_validate(farmer)
