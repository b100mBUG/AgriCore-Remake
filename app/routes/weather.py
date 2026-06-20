"""
app/routes/weather.py — Farming weather brief endpoint.

Single endpoint: GET /weather?county=Nakuru

Returns a full farming brief including:
  - Current conditions
  - 7-day forecast with farming status per day
  - Spray window advice
  - AI-generated farming insight
  - Planting window guidance

Caching: 30 minutes per county. Weather doesn't change minute-to-minute
and Gemini AI insight generation is expensive. Farmers in the same county
share the same cached brief.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.redis import cache_get, cache_set
from app.schemas.weather import WeatherBrief
from app.services.weather import get_weather_brief

log = logging.getLogger("agricore.routes.weather")
router = APIRouter(prefix="/weather", tags=["Weather"])

_WEATHER_CACHE_TTL = 1800    # 30 minutes per county


@router.get("/", response_model=WeatherBrief)
@limiter.limit("30/minute")
async def farming_weather_brief(
    request: Request,
    county: str = Query(..., min_length=2, max_length=80, description="Kenya county name"),
    db: AsyncSession = Depends(get_db),
) -> WeatherBrief:
    """Return a full farming weather brief for a Kenya county.

    This is more than a weather forecast — every data point maps to a
    farmer decision:
      - safe_to_spray → can I apply chemicals today?
      - farming_status per day → which days to work the field?
      - rain_alert → do I need to rush my top-dressing?
      - planting_window_note → is it the right time to plant?
      - ai_insight → one paragraph of expert contextual advice

    The county name is passed by the farmer app after onboarding.
    It resolves accurately with WeatherAPI (e.g. "Nakuru", "Meru", "Kisumu").
    """
    cache_key = f"weather:{county.lower().strip()}"
    cached = await cache_get(cache_key)
    if cached:
        log.debug("Weather cache hit: county=%s", county)
        return WeatherBrief(**cached)

    if not (county_clean := county.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="County name cannot be empty.",
        )

    try:
        brief = await get_weather_brief(county_clean)
    except Exception as exc:
        log.error("Weather fetch failed for county=%s: %s", county, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Weather data temporarily unavailable. Please try again.",
        )

    await cache_set(cache_key, brief.model_dump(), ttl=_WEATHER_CACHE_TTL)
    return brief
