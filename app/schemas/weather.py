"""
app/schemas/weather.py — Pydantic v2 schemas for weather endpoints.

The weather response is structured as a full farming brief, not just
current conditions. Every field maps to something a farmer acts on.

DayForecast.farming_status uses a 3-value enum:
  safe     → all operations OK
  caution  → some restrictions (e.g. high wind, UV warning)
  avoid    → stay off the field (rain, flooding risk)
"""

from pydantic import BaseModel


class DayForecast(BaseModel):
    """Single day in the 7-day farming calendar."""

    date: str                    # ISO date string "2024-06-17"
    day_name: str                # "Monday"
    condition: str               # "Partly cloudy"
    icon_code: str               # WeatherAPI icon code for frontend
    temp_max_c: float
    temp_min_c: float
    rain_mm: float               # expected rainfall
    wind_kph: float
    humidity_percent: float
    farming_status: str          # "safe" | "caution" | "avoid"
    farming_note: str            # one-line human-readable guidance


class WeatherBrief(BaseModel):
    """Full farming weather brief returned to the app.

    Designed for the expanded weather screen — every field answers
    a real farmer question without them having to ask.
    """

    # Location
    location: str                # "Nakuru, Kenya"
    county: str

    # Current conditions
    current_temp_c: float
    current_condition: str
    current_icon_code: str
    humidity_percent: float
    wind_kph: float
    uv_index: float
    feels_like_c: float

    # Spray window — key decision for input application
    safe_to_spray: bool
    spray_note: str              # why safe or not

    # Rain outlook
    rain_next_24h_mm: float
    rain_next_7d_mm: float
    rain_alert: str | None       # non-null if significant rain coming

    # 7-day farming calendar
    forecast: list[DayForecast]

    # Planting window guidance — based on month + county
    planting_window_note: str

    # AI-generated one-paragraph farming insight for current conditions
    ai_insight: str

    # UV advisory
    uv_advisory: str             # "Work before 10am or after 4pm"

    # Data freshness
    fetched_at: str              # ISO datetime
