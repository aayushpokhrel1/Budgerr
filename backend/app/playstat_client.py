from datetime import date

import httpx

from app.config import settings


def get_box_scores(game_date: date) -> list[dict]:
    response = httpx.get(
        f"{settings.playstat_base_url}/box-scores",
        params={"date": game_date.isoformat()},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()
