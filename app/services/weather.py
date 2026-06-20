"""
app/services/weather.py — Weather data fetching and farming brief generation.

Fetches 7-day forecast from WeatherAPI.com and combines it with:
  - Gemini-generated one-paragraph farming insight
  - Rule-based farming status per day (safe / caution / avoid)
  - Planting window advice based on month and county
  - Spray window determination
  - UV advisory

WeatherAPI is called with the county name as the location query.
Kenya county names resolve accurately with WeatherAPI.

Caching
───────
Weather is cached in Redis per device_id for 30 minutes. Weather data
doesn't change minute-to-minute and Gemini calls are expensive.
Farmers with the same county will share the same underlying forecast.

Farming status rules
────────────────────
  avoid   → rain_mm > 5  OR wind_kph > 30
  caution → wind_kph > 20  OR uv_index > 9  OR humidity > 90
  safe    → everything else
"""

import logging
from datetime import datetime, timezone

import httpx
from google import genai
from google.genai import types

from app.core.config import settings
from app.schemas.weather import DayForecast, WeatherBrief

log = logging.getLogger("agricore.weather")

_WEATHER_API_BASE = "https://api.weatherapi.com/v1"
_FORECAST_DAYS = 7


# ── Kenyan planting calendar (simplified) ─────────────────────────────────────
# Based on Kenya's two main rainfall seasons:
#   Long rains: March – May
#   Short rains: October – December
# This is a broad guide; actual windows vary by altitude and county.

_PLANTING_GUIDANCE: dict[int, str] = {
    1:  "Off-season. Prepare land and source certified seed for long rains.",
    2:  "Pre-season. Apply basal fertiliser. Long rains planting starts in March.",
    3:  "Long rains season starting. Optimal planting window open now.",
    4:  "Long rains in progress. Good planting conditions. Monitor for FAW.",
    5:  "Late long rains. Last chance for planting; maize should be at V4-V6 stage.",
    6:  "Dry season begins. Focus on weeding, top-dressing, and pest scouting.",
    7:  "Dry season. Harvest preparations for early-planted crops.",
    8:  "Dry season. Prepare land for short rains. Source inputs.",
    9:  "Pre-short rains. Apply basal fertiliser. Short rains planting from October.",
    10: "Short rains season open. Optimal planting window for beans and vegetables.",
    11: "Short rains in progress. Monitor for late blight on potatoes and tomatoes.",
    12: "Late short rains. Harvest approaching for short-rains crops.",
}


# ── Farming status logic ───────────────────────────────────────────────────────

def _farming_status(rain_mm: float, wind_kph: float, uv_index: float, humidity: float) -> tuple[str, str]:
    """Return (status, note) based on weather parameters."""
    if rain_mm > 5:
        return "avoid", "Rain expected — stay off the field. Avoid spraying and field operations."
    if wind_kph > 30:
        return "avoid", "High winds — spraying will drift. Postpone field operations."
    if wind_kph > 20:
        return "caution", "Moderate wind — spray early morning only, before 9am."
    if uv_index > 9:
        return "caution", "Extreme UV — work before 10am or after 4pm. Protect yourself."
    if humidity > 90:
        return "caution", "Very high humidity — increased disease pressure. Scout for blight."
    return "safe", "Good conditions for field operations, spraying, and fertiliser application."


def _spray_window(forecast_today) -> tuple[bool, str]:
    """Determine if today is safe for pesticide/fertiliser application."""
    rain = forecast_today["day"]["totalprecip_mm"]
    wind = forecast_today["day"]["maxwind_kph"]
    if rain > 2:
        return False, "Rain expected today — do not spray. Chemicals will wash off."
    if wind > 25:
        return False, "Wind too high for spraying. Wait for calmer conditions."
    return True, "Safe to spray today. Apply in early morning for best absorption."


def _uv_advisory(uv_index: float) -> str:
    if uv_index >= 11:
        return "Extreme UV. Work only before 9am or after 5pm. Cover all skin."
    if uv_index >= 8:
        return "Very high UV. Work before 10am or after 4pm. Wear a hat and long sleeves."
    if uv_index >= 6:
        return "High UV. Take breaks in shade. Stay hydrated."
    return "Moderate UV. Normal precautions apply."


# ── WeatherAPI fetcher ────────────────────────────────────────────────────────

async def _fetch_forecast(county: str) -> dict:
    """Call WeatherAPI forecast endpoint. Returns raw JSON dict."""
    url = f"{_WEATHER_API_BASE}/forecast.json"
    params = {
        "key": settings.weather_api_key,
        "q": f"{county}, Kenya",
        "days": _FORECAST_DAYS,
        "aqi": "no",
        "alerts": "no",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ── Gemini farming insight ─────────────────────────────────────────────────────

async def _generate_ai_insight(county: str, forecast_summary: str, month: int) -> str:
    """Generate a one-paragraph farming insight from forecast data."""
    if not settings.gemini_api_key:
        return "Weather insight unavailable — API key not configured."

    planting_context = _PLANTING_GUIDANCE.get(month, "")
    prompt = f"""
You are an agricultural advisor for Kenyan smallholder farmers.

Location: {county} County, Kenya
Current month context: {planting_context}
7-day weather summary:
{forecast_summary}

Write ONE short paragraph (3-4 sentences max) of practical farming advice
based on this weather. Tell the farmer what to do THIS WEEK given the forecast.
Be specific and actionable. Write in simple, clear English.
Do not start with "Based on" or "According to". Be direct.
""".strip()

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=200,
            ),
        )
        return response.text.strip()
    except Exception as exc:
        log.warning("Gemini weather insight failed: %s", exc)
        return "Unable to generate farming insight at this time."


# ── Main weather brief builder ────────────────────────────────────────────────

async def get_weather_brief(county: str) -> WeatherBrief:
    """Build the full farming weather brief for a given county.

    Called by the weather route. Results are cached by the route layer.
    """
    data = await _fetch_forecast(county)

    current = data["current"]
    location_data = data["location"]
    forecast_days = data["forecast"]["forecastday"]

    today_data = forecast_days[0]
    today_day = today_data["day"]

    # ── Current conditions ─────────────────────────────────────────────────
    current_temp = current["temp_c"]
    current_condition = current["condition"]["text"]
    current_icon = str(current["condition"]["code"])
    humidity = current["humidity"]
    wind_kph = current["wind_kph"]
    uv_index = current.get("uv", 0.0)
    feels_like = current["feelslike_c"]

    # ── Rain totals ────────────────────────────────────────────────────────
    rain_24h = today_day["totalprecip_mm"]
    rain_7d = sum(d["day"]["totalprecip_mm"] for d in forecast_days)

    rain_alert: str | None = None
    if rain_7d > 30:
        rain_alert = f"{rain_7d:.0f}mm of rain expected this week. Plan field operations around dry days."
    elif rain_24h > 10:
        rain_alert = f"{rain_24h:.0f}mm of rain expected today. Stay off the field."

    # ── Spray window ───────────────────────────────────────────────────────
    safe_to_spray, spray_note = _spray_window(today_data)

    # ── 7-day forecast ─────────────────────────────────────────────────────
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    forecast_list: list[DayForecast] = []
    forecast_summary_lines: list[str] = []

    for fd in forecast_days:
        day = fd["day"]
        dt = datetime.fromisoformat(fd["date"])
        day_name = day_names[dt.weekday()]

        rain = day["totalprecip_mm"]
        wind = day["maxwind_kph"]
        uv = day.get("uv", 0.0)
        hum = day["avghumidity"]

        f_status, f_note = _farming_status(rain, wind, uv, hum)

        forecast_list.append(DayForecast(
            date=fd["date"],
            day_name=day_name,
            condition=day["condition"]["text"],
            icon_code=str(day["condition"]["code"]),
            temp_max_c=day["maxtemp_c"],
            temp_min_c=day["mintemp_c"],
            rain_mm=rain,
            wind_kph=wind,
            humidity_percent=hum,
            farming_status=f_status,
            farming_note=f_note,
        ))

        forecast_summary_lines.append(
            f"{day_name}: {day['condition']['text']}, "
            f"max {day['maxtemp_c']}°C, rain {rain:.0f}mm, "
            f"wind {wind:.0f}kph → {f_status}"
        )

    # ── AI insight ─────────────────────────────────────────────────────────
    month = datetime.now(timezone.utc).month
    forecast_summary_text = "\n".join(forecast_summary_lines)
    ai_insight = await _generate_ai_insight(county, forecast_summary_text, month)

    # ── Planting window ────────────────────────────────────────────────────
    planting_note = _PLANTING_GUIDANCE.get(month, "Check local extension officer for planting advice.")

    return WeatherBrief(
        location=location_data["name"],
        county=county,
        current_temp_c=current_temp,
        current_condition=current_condition,
        current_icon_code=current_icon,
        humidity_percent=humidity,
        wind_kph=wind_kph,
        uv_index=uv_index,
        feels_like_c=feels_like,
        safe_to_spray=safe_to_spray,
        spray_note=spray_note,
        rain_next_24h_mm=rain_24h,
        rain_next_7d_mm=rain_7d,
        rain_alert=rain_alert,
        forecast=forecast_list,
        planting_window_note=planting_note,
        ai_insight=ai_insight,
        uv_advisory=_uv_advisory(uv_index),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
