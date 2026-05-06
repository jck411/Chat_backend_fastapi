"""AccuWeather API service for kiosk weather display.

Fetches hourly (12h) and daily (5-day) forecasts from AccuWeather.
Implements caching to stay within API call limits (15,000/month on starter plan).

API Docs:
- https://developer.accuweather.com/core-weather/location-key-daily
- https://developer.accuweather.com/apis/v1/forecasts/v1/hourly/12hour
"""

import logging
from datetime import datetime, timedelta
from typing import TypedDict

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

ACCUWEATHER_BASE_URL = "https://dataservice.accuweather.com"


class HourlyForecast(TypedDict):
    """Single hour forecast data."""

    hour: str  # e.g., "3PM"
    temp: int
    icon: int
    icon_phrase: str
    rain_chance: int  # PrecipitationProbability from API


class DailyForecast(TypedDict):
    """Single day forecast data."""

    day: str  # e.g., "Fri"
    date: str  # ISO date
    high: int
    low: int
    icon: int
    icon_phrase: str
    rain_chance: int  # Day.PrecipitationProbability from API


class CurrentConditions(TypedDict):
    """Current weather conditions."""

    temp: int
    icon: int
    phrase: str


class WeatherData(TypedDict):
    """Complete weather data bundle."""

    current: CurrentConditions
    hourly: list[HourlyForecast]
    daily: list[DailyForecast]
    fetched_at: str  # ISO timestamp
    location_key: str


class WeatherCache:
    """Simple in-memory cache for weather data."""

    def __init__(self) -> None:
        self._data: WeatherData | None = None
        self._expires_at: datetime | None = None

    def get(self) -> WeatherData | None:
        """Get cached data if not expired."""
        if self._data is None or self._expires_at is None:
            return None
        if datetime.now() > self._expires_at:
            return None
        return self._data

    def set(self, data: WeatherData, ttl_minutes: int) -> None:
        """Cache data with TTL."""
        self._data = data
        self._expires_at = datetime.now() + timedelta(minutes=ttl_minutes)

    def clear(self) -> None:
        """Clear the cache."""
        self._data = None
        self._expires_at = None


# Global cache instance
_weather_cache = WeatherCache()


def _format_hour(dt: datetime) -> str:
    """Format datetime to hour string like '3PM' or '12AM'."""
    hour = dt.strftime("%I").lstrip("0")  # Remove leading zero
    ampm = dt.strftime("%p")
    return f"{hour}{ampm}"


def _format_day(dt: datetime) -> str:
    """Format datetime to short day name like 'Fri'."""
    return dt.strftime("%a")


async def fetch_hourly_forecast(
    api_key: str, location_key: str
) -> list[HourlyForecast]:
    """Fetch 12-hour forecast from AccuWeather.

    API: GET /forecasts/v1/hourly/12hour/{locationKey}
    Returns PrecipitationProbability for each hour.
    """
    url = f"{ACCUWEATHER_BASE_URL}/forecasts/v1/hourly/12hour/{location_key}"
    params = {"apikey": api_key, "details": "true"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    hourly: list[HourlyForecast] = []
    for item in data:
        dt = datetime.fromisoformat(item["DateTime"].replace("Z", "+00:00"))
        hourly.append(
            HourlyForecast(
                hour=_format_hour(dt),
                temp=round(item["Temperature"]["Value"]),
                icon=item["WeatherIcon"],
                icon_phrase=item["IconPhrase"],
                rain_chance=item.get("PrecipitationProbability", 0),
            )
        )

    return hourly


async def fetch_daily_forecast(api_key: str, location_key: str) -> list[DailyForecast]:
    """Fetch 5-day forecast from AccuWeather.

    API: GET /forecasts/v1/daily/5day/{locationKey}
    Returns Day.PrecipitationProbability for each day.
    """
    url = f"{ACCUWEATHER_BASE_URL}/forecasts/v1/daily/5day/{location_key}"
    params = {"apikey": api_key, "details": "true"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    daily: list[DailyForecast] = []
    for item in data.get("DailyForecasts", []):
        dt = datetime.fromisoformat(item["Date"].replace("Z", "+00:00"))
        day_data = item.get("Day", {})

        daily.append(
            DailyForecast(
                day=_format_day(dt),
                date=item["Date"],
                high=round(item["Temperature"]["Maximum"]["Value"]),
                low=round(item["Temperature"]["Minimum"]["Value"]),
                icon=day_data.get("Icon", 1),
                icon_phrase=day_data.get("IconPhrase", ""),
                rain_chance=day_data.get("PrecipitationProbability", 0),
            )
        )

    return daily


async def fetch_current_conditions(
    api_key: str, location_key: str
) -> CurrentConditions:
    """Fetch current conditions from AccuWeather.

    API: GET /currentconditions/v1/{locationKey}
    """
    url = f"{ACCUWEATHER_BASE_URL}/currentconditions/v1/{location_key}"
    params = {"apikey": api_key}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    if not data:
        raise ValueError("No current conditions data returned")

    item = data[0]
    return CurrentConditions(
        temp=round(item["Temperature"]["Imperial"]["Value"]),
        icon=item["WeatherIcon"],
        phrase=item["WeatherText"],
    )


async def get_weather(force_refresh: bool = False) -> WeatherData:
    """Get weather data, using cache if available.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data.

    Returns:
        WeatherData with current, hourly, and daily forecasts.

    Raises:
        ValueError: If AccuWeather is not configured.
        httpx.HTTPError: If API request fails.
    """
    settings = get_settings()

    if not settings.accuweather_api_key:
        raise ValueError("ACCUWEATHER_API_KEY not configured")
    if not settings.accuweather_location_key:
        raise ValueError("ACCUWEATHER_LOCATION_KEY not configured")

    # Check cache first
    if not force_refresh:
        cached = _weather_cache.get()
        if cached is not None:
            logger.debug("Returning cached weather data")
            return cached

    # Fetch fresh data
    api_key = settings.accuweather_api_key.get_secret_value()
    location_key = settings.accuweather_location_key

    logger.info(f"Fetching weather data for location {location_key}")

    # Fetch all data (3 API calls)
    current = await fetch_current_conditions(api_key, location_key)
    hourly = await fetch_hourly_forecast(api_key, location_key)
    daily = await fetch_daily_forecast(api_key, location_key)

    weather_data = WeatherData(
        current=current,
        hourly=hourly,
        daily=daily,
        fetched_at=datetime.now().isoformat(),
        location_key=location_key,
    )

    # Cache the result
    _weather_cache.set(weather_data, settings.accuweather_cache_minutes)
    logger.info(
        f"Weather data cached for {settings.accuweather_cache_minutes} minutes"
    )

    return weather_data


def clear_weather_cache() -> None:
    """Clear the weather cache (for testing or manual refresh)."""
    _weather_cache.clear()
    logger.info("Weather cache cleared")


__all__ = [
    "WeatherData",
    "HourlyForecast",
    "DailyForecast",
    "CurrentConditions",
    "get_weather",
    "clear_weather_cache",
]
