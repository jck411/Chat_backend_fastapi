"""Slideshow router — proxies Immich API for landscape photos with people/pets."""

from __future__ import annotations

import hashlib
import logging
import os
import random
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slideshow", tags=["slideshow"])

IMMICH_URL = os.getenv("IMMICH_URL", "http://192.168.1.113:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")

# How many photos to serve per day
DAILY_PHOTO_COUNT = 100

# Search queries to pull diverse photos from Immich
_SEARCH_QUERIES = [
    "people faces family",
    "pets animals dogs cats",
    "outdoors nature landscape",
    "kids children playing",
    "friends group celebration",
]

# In-memory cache: pool refreshed once per day
_cache: dict = {"date": None, "pool": [], "daily": []}


def _immich_headers() -> dict[str, str]:
    return {"x-api-key": IMMICH_API_KEY, "Accept": "application/json"}


async def _fetch_landscape_pool() -> list[str]:
    """Build a large pool of landscape photo IDs from Immich via multiple queries."""
    seen: set[str] = set()
    landscape_ids: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in _SEARCH_QUERIES:
            for page in range(1, 4):  # up to 3 pages per query
                body = {
                    "query": query,
                    "type": "IMAGE",
                    "size": 200,
                    "page": page,
                    "withExif": True,
                }
                try:
                    resp = await client.post(
                        f"{IMMICH_URL}/api/search/smart",
                        json=body,
                        headers=_immich_headers(),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    logger.warning("Immich query=%r page=%d failed", query, page)
                    break

                items = data.get("assets", {}).get("items", [])
                if not items:
                    break  # no more results for this query

                for a in items:
                    aid = a["id"]
                    if aid in seen:
                        continue
                    seen.add(aid)
                    w = a.get("exifInfo", {}).get("exifImageWidth") or a.get("width", 0)
                    h = a.get("exifInfo", {}).get("exifImageHeight") or a.get(
                        "height", 0
                    )
                    if w > h:  # landscape only
                        landscape_ids.append(aid)

    logger.info(
        "Immich pool built: %d landscape photos from %d queries",
        len(landscape_ids),
        len(_SEARCH_QUERIES),
    )
    return landscape_ids


def _select_daily(pool: list[str], today: date) -> list[str]:
    """Pick DAILY_PHOTO_COUNT photos from the pool using a deterministic daily seed.

    The seed changes each day so different photos are shown.
    If the pool is smaller than DAILY_PHOTO_COUNT, all photos are used.
    """
    if not pool:
        return []
    seed = int(hashlib.md5(str(today).encode()).hexdigest(), 16)
    rng = random.Random(seed)
    shuffled = list(pool)
    rng.shuffle(shuffled)
    return shuffled[:DAILY_PHOTO_COUNT]


async def _get_daily_photos() -> list[str]:
    """Return today's photo list, refreshing the pool once per day."""
    today = date.today()
    if _cache["date"] != today or not _cache["daily"]:
        try:
            pool = await _fetch_landscape_pool()
            daily = _select_daily(pool, today)
            _cache["pool"] = pool
            _cache["daily"] = daily
            _cache["date"] = today
            logger.info("Slideshow: pool=%d, today=%d photos", len(pool), len(daily))
        except Exception:
            logger.exception("Failed to fetch photos from Immich")
            if not _cache["daily"]:
                raise
    return _cache["daily"]


@router.get("/photos")
async def list_photos(daily_seed: bool = False) -> dict:
    """List today's landscape photo asset IDs."""
    ids = await _get_daily_photos()

    if daily_seed and ids:
        seed = int(hashlib.md5(str(date.today()).encode()).hexdigest(), 16)
        rng = random.Random(seed)
        ids = list(ids)
        rng.shuffle(ids)

    return {"photos": ids, "count": len(ids)}


@router.get("/status")
async def slideshow_status() -> dict:
    """Get slideshow status."""
    ids = await _get_daily_photos()
    return {
        "photo_count": len(ids),
        "pool_size": len(_cache["pool"]),
        "source": IMMICH_URL,
    }


@router.get("/photo/{asset_id}")
async def get_photo(asset_id: str) -> Response:
    """Proxy an Immich asset thumbnail (preview size)."""
    # Validate asset_id is a UUID-like string
    if not asset_id or "/" in asset_id or "\\" in asset_id or ".." in asset_id:
        raise HTTPException(status_code=400, detail="Invalid asset ID")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail",
            params={"size": "preview"},
            headers=_immich_headers(),
        )
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Photo not found")
        resp.raise_for_status()

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
        headers={
            "Cache-Control": "public, max-age=86400",
            "ETag": resp.headers.get("etag", ""),
        },
    )
