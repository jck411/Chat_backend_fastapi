"""Weather API router for kiosk display.

Provides endpoints to fetch weather data from AccuWeather.
Data is cached server-side to respect API rate limits.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.weather_service import (
    WeatherData,
    clear_weather_cache,
    get_weather,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weather", tags=["Weather"])


class WeatherResponse(BaseModel):
    """Weather API response model."""

    current: dict
    hourly: list[dict]
    daily: list[dict]
    fetched_at: str
    cached: bool = False


class WeatherError(BaseModel):
    """Weather API error response."""

    error: str
    detail: str


@router.get(
    "",
    response_model=WeatherResponse,
    responses={
        503: {"model": WeatherError, "description": "Weather service unavailable"},
    },
)
async def get_weather_data(
    refresh: Annotated[
        bool,
        Query(description="Force refresh from AccuWeather API (bypasses cache)"),
    ] = False,
) -> WeatherResponse:
    """Get current weather, hourly forecast (12h), and daily forecast (5-day).

    Weather data is cached for 15 minutes by default to stay within
    AccuWeather API limits (15,000 calls/month on starter plan).

    The response includes:
    - current: Temperature, icon, and condition phrase
    - hourly: 12-hour forecast with rain probability for each hour
    - daily: 5-day forecast with rain probability for each day

    Rain probability values are real data from AccuWeather's
    PrecipitationProbability field.
    """
    try:
        data = await get_weather(force_refresh=refresh)

        return WeatherResponse(
            current=dict(data["current"]),
            hourly=[dict(h) for h in data["hourly"]],
            daily=[dict(d) for d in data["daily"]],
            fetched_at=data["fetched_at"],
            cached=not refresh,
        )
    except ValueError as e:
        # Configuration error
        logger.error(f"Weather config error: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Weather service not configured",
                "detail": str(e),
            },
        )
    except Exception as e:
        # API error
        logger.error(f"Weather API error: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Weather service unavailable",
                "detail": str(e),
            },
        )


@router.post("/refresh")
async def refresh_weather() -> WeatherResponse:
    """Force refresh weather data from AccuWeather API.

    Clears the cache and fetches fresh data.
    Use sparingly to stay within API limits.
    """
    clear_weather_cache()
    return await get_weather_data(refresh=True)


@router.get("/status")
async def weather_status() -> dict:
    """Check weather service configuration status."""
    from backend.config import get_settings

    settings = get_settings()

    return {
        "configured": bool(
            settings.accuweather_api_key and settings.accuweather_location_key
        ),
        "has_api_key": bool(settings.accuweather_api_key),
        "has_location_key": bool(settings.accuweather_location_key),
        "cache_minutes": settings.accuweather_cache_minutes,
    }


__all__ = ["router"]
