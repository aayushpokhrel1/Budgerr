from datetime import date

import httpx

from app.config import settings


def _headers() -> dict[str, str]:
    # playstat rejects requests without X-API-Key when its AUTH_ENABLED is on.
    if settings.playstat_api_key:
        return {"X-API-Key": settings.playstat_api_key}
    return {}


def get_box_scores(game_date: date) -> list[dict]:
    response = httpx.get(
        f"{settings.playstat_base_url}/box-scores",
        params={"date": game_date.isoformat()},
        headers=_headers(),
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def get_parlay_recommendations(limit: int = 10) -> list[dict]:
    response = httpx.get(
        f"{settings.playstat_base_url}/parlay-recommendations",
        params={"limit": limit},
        headers=_headers(),
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def get_edges() -> list[dict]:
    response = httpx.get(
        f"{settings.playstat_base_url}/edges",
        headers=_headers(),
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()
